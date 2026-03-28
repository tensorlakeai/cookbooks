#!/usr/bin/env python3
"""Standalone sandbox browser debug script.
Run directly: python debug_sandbox_browser.py
No TensorLake orchestration — just raw sandbox + browser server + RPC.
"""
import json
import os
import time
from tensorlake.sandbox import OutputMode, SandboxClient

from competitive_website_analyst.browser_runtime import SANDBOX_BROWSER_SERVER, SANDBOX_RPC_CLIENT, SandboxBrowserTools

TARGET_URL = "https://cursor.com"
DEBUG_DIR = "/tmp/debug_screenshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

def dump_server_output(sandbox, proc, label=""):
    """Print captured stdout/stderr from the browser server process."""
    tag = f"[server{' ' + label if label else ''}]"
    try:
        stdout = sandbox.get_stdout(proc.pid)
        if stdout:
            print(f"{tag} stdout:\n{stdout}")
    except Exception as e:
        print(f"{tag} could not read stdout: {e}")
    try:
        stderr = sandbox.get_stderr(proc.pid)
        if stderr:
            print(f"{tag} stderr:\n{stderr}")
    except Exception as e:
        print(f"{tag} could not read stderr: {e}")


print(f"\n{'='*60}")
print(f"  Debug: Sandbox Browser")
print(f"  Target: {TARGET_URL}")
print(f"  Output dir: {DEBUG_DIR}")
print(f"{'='*60}\n")

print("[sandbox] Creating sandbox...")
client = SandboxClient.for_cloud()
with client.create_and_connect(
    image="python:3.11-slim",
    allow_internet_access=True,
    timeout_secs=300,
    startup_timeout=120,
    memory_mb=4096,           # Chromium needs headroom
    ephemeral_disk_mb=8192,   # Chromium + deps ~2-3GB
) as sandbox:
    print(f"[sandbox] Connected")

    # Install Playwright
    print("\n[setup] Installing playwright pip package...")
    r = sandbox.run(
        "python3", args=["-m", "pip", "install", "--break-system-packages", "-q", "playwright"],
        timeout=120,
    )
    print(f"[setup] pip install exit={r.exit_code}")
    if r.stdout:
        print(f"[setup] pip stdout: {r.stdout.strip()}")
    if r.stderr:
        print(f"[setup] pip stderr: {r.stderr.strip()[:500]}")
    if r.exit_code != 0:
        raise RuntimeError("pip install playwright failed")

    print("\n[setup] Installing Chromium (slow, ~1-2 min)...")
    r = sandbox.run(
        "sh", args=["-c", "python3 -m playwright install --with-deps chromium > /tmp/pw.log 2>&1"],
        timeout=360,
    )
    print(f"[setup] playwright install exit={r.exit_code}")
    try:
        pw_log = sandbox.read_file("/tmp/pw.log").decode("utf-8", errors="replace")
        print(f"[setup] playwright install log (last 1000 chars):\n{pw_log[-1000:]}")
    except Exception as e:
        print(f"[setup] could not read pw.log: {e}")
    if r.exit_code != 0:
        raise RuntimeError("playwright install chromium failed")

    # Verify playwright works
    print("\n[setup] Verifying playwright import...")
    r = sandbox.run("python3", args=["-c", "from playwright.sync_api import sync_playwright; print('ok')"], timeout=15)
    print(f"[setup] verify exit={r.exit_code} stdout={r.stdout.strip()!r} stderr={r.stderr.strip()!r}")
    if r.exit_code != 0:
        raise RuntimeError("playwright verify failed")

    # Write scripts
    print("\n[setup] Writing browser_server.py and browser_rpc.py to sandbox...")
    sandbox.write_file("/app/browser_server.py", SANDBOX_BROWSER_SERVER.encode())
    sandbox.write_file("/app/browser_rpc.py", SANDBOX_RPC_CLIENT.encode())
    print("[setup] Scripts written")

    # Start browser server
    print(f"\n[server] Starting browser server for {TARGET_URL}...")
    proc = sandbox.start_process(
        "python3", args=["/app/browser_server.py", "--url", TARGET_URL],
        stdout_mode=OutputMode.CAPTURE,
        stderr_mode=OutputMode.CAPTURE,
    )
    print(f"[server] Process started (pid={proc.pid})")

    tools = SandboxBrowserTools(sandbox=sandbox, save_dir=DEBUG_DIR)

    # Wait for server to be ready
    print("\n[server] Waiting for browser server to become ready...")
    server_up = False
    for i in range(90):
        try:
            result = tools._rpc("ping")
            if not server_up:
                print(f"[server] HTTP server responded at t={i}s")
                server_up = True
            ready = result.get("ready", False)
            error = result.get("error")
            if error:
                dump_server_output(sandbox, proc, "on-error")
                raise RuntimeError(f"browser launch error: {error}")
            if ready:
                print(f"[server] Browser ready at t={i}s")
                break
            if i % 5 == 0:
                print(f"  [{i}s] not ready yet...")
                dump_server_output(sandbox, proc)
        except RuntimeError:
            raise
        except Exception as e:
            if i % 5 == 0:
                print(f"  [{i}s] ping failed: {e}")
        time.sleep(1)
    else:
        dump_server_output(sandbox, proc, "timeout")
        raise RuntimeError("browser server never became ready after 90s")

    dump_server_output(sandbox, proc, "after-ready")

    # --- Tool tests ---

    print("\n[test] screenshot (step 1)...")
    t0 = time.time()
    png = tools.screenshot()
    print(f"  -> {len(png):,} bytes in {time.time()-t0:.1f}s, saved to {DEBUG_DIR}/step_01.png")

    print("\n[test] extract_metadata...")
    t0 = time.time()
    meta = tools.extract_metadata()
    print(f"  -> took {time.time()-t0:.1f}s")
    print(f"  -> title:            {meta.title!r}")
    print(f"  -> h1_hero_text:     {meta.h1_hero_text!r}")
    print(f"  -> meta_description: {meta.meta_description!r}")
    print(f"  -> nav_items:        {meta.nav_items}")
    print(f"  -> visible_cta_labels: {meta.visible_cta_labels[:5]}")
    print(f"  -> og_image_url:     {meta.og_image_url!r}")
    print(f"  -> page_load_time_ms: {meta.page_load_time_ms}")

    print("\n[test] save_screenshot (full-page)...")
    t0 = time.time()
    tools.save_screenshot("/app/screenshot.png")
    final_bytes = sandbox.read_file("/app/screenshot.png")
    out_path = f"{DEBUG_DIR}/final.png"
    open(out_path, "wb").write(final_bytes)
    print(f"  -> {len(final_bytes):,} bytes in {time.time()-t0:.1f}s, saved to {out_path}")

    print("\n[server] Final server output:")
    dump_server_output(sandbox, proc, "final")

    tools.shutdown()
    try:
        sandbox.kill_process(proc.pid)
    except Exception:
        pass  # process already exited after shutdown

    print(f"\n{'='*60}")
    print(f"  All tools OK")
    print(f"  Screenshots in: {DEBUG_DIR}/")
    print(f"    step_01.png  — initial viewport")
    print(f"    final.png    — full-page save")
    print(f"{'='*60}\n")
