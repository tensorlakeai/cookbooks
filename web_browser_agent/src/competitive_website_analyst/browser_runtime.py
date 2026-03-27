from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from competitive_website_analyst.models import BrowserMetadata


SANDBOX_BROWSER_SERVER = r'''
import argparse
import base64
import json
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def extract_metadata(page):
    return page.evaluate(
        """
        () => {
          const visibleText = (elements) =>
            elements
              .filter((el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
              .map((el) => (el.innerText || el.textContent || '').trim())
              .filter(Boolean);
          const title = document.title || '';
          const metaDescription = document.querySelector('meta[name="description"]')?.content || '';
          const ogImage = document.querySelector('meta[property="og:image"]')?.content || '';
          const h1 = visibleText(Array.from(document.querySelectorAll('h1')))[0] || '';
          const navItems = visibleText(Array.from(document.querySelectorAll('nav a'))).slice(0, 12);
          const ctas = visibleText(
            Array.from(document.querySelectorAll('button, a[role="button"], a'))
          ).slice(0, 20);
          return {
            title,
            meta_description: metaDescription,
            h1_hero_text: h1,
            visible_cta_labels: ctas,
            nav_items: navItems,
            og_image_url: ogImage,
          };
        }
        """
    )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            tool = payload["tool"]
            args = payload.get("args", {})
            response = self.server.handle_tool(tool, args)
        except Exception as e:
            import traceback
            traceback.print_exc(file=__import__("sys").stdout)
            response = {"ok": False, "error": str(e)}
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class BrowserServer(HTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.page = None
        self.started_at = time.time()
        self.ready = False
        self.launch_error = None
        # Queue for dispatching Playwright calls back to the browser thread.
        # Playwright sync API must be called from the thread that created it.
        self._op_queue = queue.Queue()

    def handle_tool(self, tool, args):
        if tool == "ping":
            return {"ok": True, "ready": self.ready, "error": self.launch_error}
        if not self.ready:
            return {"ok": False, "error": self.launch_error or "browser not ready yet"}
        # Dispatch to the browser thread via queue
        result_q = queue.Queue()
        self._op_queue.put((tool, args, result_q))
        result = result_q.get(timeout=60)
        if isinstance(result, Exception):
            raise result
        return result

    def do_browser_op(self, tool, args):
        """Execute a Playwright operation. Must be called from the browser thread."""
        if tool == "screenshot":
            png = self.page.screenshot(type="png")
            return {"image_b64": base64.b64encode(png).decode("ascii")}
        if tool == "click_text":
            label = args["text"]
            self.page.get_by_text(label, exact=False).first.click(timeout=3000)
            return {"ok": True}
        if tool == "click_coords":
            self.page.mouse.click(args["x"], args["y"])
            return {"ok": True}
        if tool == "wait":
            seconds = float(args.get("seconds", 1))
            time.sleep(seconds)
            return {"ok": True}
        if tool == "extract_metadata":
            data = extract_metadata(self.page)
            data["page_load_time_ms"] = int((time.time() - self.started_at) * 1000)
            return data
        if tool == "save_screenshot":
            self.page.screenshot(path=args["path"], type="png")
            return {"ok": True}
        if tool == "shutdown":
            return {"ok": True, "shutdown": True}
        raise ValueError(f"unknown tool: {tool}")


def main():
    import threading

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Start the HTTP server FIRST so health checks always get a response
    server = BrowserServer(("127.0.0.1", args.port), Handler)
    print(f"Browser server listening on port {args.port}", flush=True)

    # Run HTTP server in a background thread so Playwright stays on the main thread
    def serve_http():
        while not getattr(server, "_shutdown", False):
            server.handle_request()

    threading.Thread(target=serve_http, daemon=True).start()

    # Launch browser + navigate on the MAIN thread (Playwright sync API requirement)
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.goto(args.url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"Navigation warning (page may be partial): {e}", flush=True)
        server.page = page
        server.ready = True
        print(f"Browser ready for {args.url}", flush=True)

        # Process browser operations dispatched from the HTTP thread
        while True:
            try:
                tool, op_args, result_q = server._op_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                result = server.do_browser_op(tool, op_args)
            except Exception as e:
                result_q.put(e)
                continue
            result_q.put(result)
            if tool == "shutdown":
                break
    except Exception as e:
        server.launch_error = str(e)
        print(f"Browser launch failed: {e}", flush=True)
        # Keep HTTP server alive so ping can report the error
        import time as _time
        _time.sleep(30)
    finally:
        server._shutdown = True
        if server.page:
            try:
                server.page.context.browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
'''


SANDBOX_RPC_CLIENT = r"""
import json
import sys
import urllib.request

tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
payload = json.dumps({"tool": tool, "args": args}).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8765",
    data=payload,
    headers={"content-type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    print(response.read().decode("utf-8"))
"""


@dataclass(slots=True)
class SandboxBrowserTools:
    sandbox: object
    port: int = 8765
    save_dir: str | None = None
    _screenshot_count: int = 0

    def screenshot(self) -> bytes:
        payload = self._rpc("screenshot")
        png_bytes = base64.b64decode(payload["image_b64"])
        if self.save_dir:
            object.__setattr__(self, "_screenshot_count", self._screenshot_count + 1)
            step_path = f"{self.save_dir}/step_{self._screenshot_count:02d}.png"
            open(step_path, "wb").write(png_bytes)
            print(f"  [screenshot] Saved step {self._screenshot_count}: {step_path}")
        return png_bytes

    def click_text(self, text: str) -> dict:
        return self._rpc("click_text", {"text": text})

    def click_coords(self, x: int, y: int) -> dict:
        return self._rpc("click_coords", {"x": x, "y": y})

    def wait(self, seconds: float) -> dict:
        return self._rpc("wait", {"seconds": seconds})

    def extract_metadata(self) -> BrowserMetadata:
        return BrowserMetadata.model_validate(self._rpc("extract_metadata"))

    def save_screenshot(self, path: str) -> dict:
        result = self._rpc("save_screenshot", {"path": path})
        if self.save_dir:
            object.__setattr__(self, "_screenshot_count", self._screenshot_count + 1)
            step_path = f"{self.save_dir}/step_{self._screenshot_count:02d}_final.png"
            # Pull the full-page screenshot from the sandbox too
            try:
                full_page_bytes = self.sandbox.read_file(path)
                open(step_path, "wb").write(full_page_bytes)
                print(f"  [screenshot] Saved final full-page: {step_path}")
            except Exception:
                pass
        return result

    def shutdown(self) -> dict:
        return self._rpc("shutdown")

    def _rpc(self, tool: str, args: dict | None = None) -> dict:
        result = self.sandbox.run(
            "python3",
            args=["/app/browser_rpc.py", tool, json.dumps(args or {})],
            timeout=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout or f"browser rpc failed for {tool}")
        return json.loads(result.stdout)
