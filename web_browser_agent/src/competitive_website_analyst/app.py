from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import time
from pathlib import Path

from tensorlake.applications import Retries, application, function, run_local_application
from tensorlake.sandbox import OutputMode, SandboxClient

from competitive_website_analyst.agent_backend import get_agent_backend
from competitive_website_analyst.browser_failures import classify_browser_failure_stage, is_retryable
from competitive_website_analyst.browser_runtime import SANDBOX_BROWSER_SERVER, SANDBOX_RPC_CLIENT, SandboxBrowserTools
from competitive_website_analyst.models import BrowserArtifact, Company, FailureRecord, ReportBundle, Scorecard
from competitive_website_analyst.scoring import build_empty_report_bundle, build_html_report, build_summary_csv, compute_overall_score, sort_scorecards
from competitive_website_analyst.utils import make_run_id, parse_json, validate_companies


MAX_BACKFILL_ROUNDS = 3


@application()
@function()
def competitive_analyst(domain: str, count: int) -> dict:
    print(f"\n{'='*60}")
    print(f"  Competitive Website Analyst")
    print(f"  Domain: {domain!r}  |  Target: {count} companies")
    print(f"{'='*60}\n")

    all_companies: list[dict] = []
    all_artifacts: list[dict] = []
    tried_urls: set[str] = set()

    for round_num in range(MAX_BACKFILL_ROUNDS):
        success_count = sum(1 for a in all_artifacts if a.get("status") == "success")
        needed = count - success_count
        if needed <= 0:
            print(f"\n[orchestrator] Target reached: {success_count}/{count} successful")
            break

        label = "round 1" if round_num == 0 else f"backfill round {round_num + 1}"
        print(f"\n{'─'*60}")
        print(f"[orchestrator] {label}: need {needed} more successful companies")
        print(f"{'─'*60}")

        # Research: discover new companies
        print(f"\n[research] Searching for {needed + 2} candidates in '{domain}'...")
        candidates = research_agent(domain, needed + 2)
        new_companies = [c for c in candidates if c["url"] not in tried_urls][:needed]
        if not new_companies:
            print(f"[research] No new companies found (all duplicates of already-tried URLs)")
            break

        tried_urls.update(c["url"] for c in new_companies)
        all_companies.extend(new_companies)
        print(f"[research] Discovered {len(new_companies)} new companies:")
        for c in new_companies:
            print(f"  - {c['name']} ({c['url']})")

        # Browse: run browser agents in parallel
        print(f"\n[browser] Launching {len(new_companies)} browser agents in parallel...")
        results = browser_agent.map(new_companies)
        all_artifacts.extend(results)

        batch_success = sum(1 for r in results if r.get("status") == "success")
        batch_failed = len(results) - batch_success
        print(f"\n[browser] Batch complete: {batch_success} succeeded, {batch_failed} failed")
        for r in results:
            name = r.get("company", {}).get("name", "?")
            if r.get("status") == "success":
                print(f"  ✓ {name}")
            else:
                print(f"  ✗ {name} — {r.get('failure_stage', '?')}: {r.get('failure_reason', '?')[:80]}")

    # Summary
    successful = [a for a in all_artifacts if a.get("status") == "success"]
    total_success = len(successful)
    total_failed = len(all_artifacts) - total_success
    print(f"\n{'─'*60}")
    print(f"[orchestrator] Browsing complete: {total_success}/{count} target, "
          f"{total_failed} failed, {len(tried_urls)} URLs tried")
    print(f"{'─'*60}")

    # Analysis
    print(f"\n[analysis] Scoring {len(successful)} homepages in parallel...")
    scorecards = analysis_agent.map(successful)
    print(f"[analysis] Scoring complete:")
    for card in sorted(scorecards, key=lambda c: c.get("overall_score", 0), reverse=True):
        print(f"  {card.get('overall_score', 0):5.2f}  {card.get('company', '?')}")

    # Report
    print(f"\n[report] Generating final Homepage analysis report...")
    result = report_agent(domain, count, all_companies, all_artifacts, scorecards)
    print(f"[report] Done — {result.get('successful_count', 0)} companies in final report")

    # Save HTML report locally and open in browser
    html_report = result.get("html_report", "")
    if html_report:
        report_dir = Path("output")
        report_dir.mkdir(parents=True, exist_ok=True)
        html_path = (report_dir / "report.html").resolve()
        html_path.write_text(html_report)
        print(f"\n{'='*60}")
        print(f"  Run complete")
        print(f"  Report: {html_path}")
        print(f"{'='*60}\n")
        _open_in_browser(str(html_path))
    else:
        print(f"\n{'='*60}")
        print(f"  Run complete (no HTML report generated)")
        print(f"{'='*60}\n")

    return result


@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def research_agent(domain: str, count: int) -> list[dict]:
    backend = get_agent_backend()
    print(f"[research] Querying LLM for {count} companies in '{domain}'...")
    payload = parse_json(backend.research(domain, count))
    companies = validate_companies(payload, count)
    print(f"[research] Validated {len(companies)} companies from LLM response")
    return [company.model_dump(mode="json") for company in companies]


MAX_BROWSER_ATTEMPTS = 3


@function(
    timeout=600,
    secrets=["ANTHROPIC_API_KEY", "TENSORLAKE_API_KEY"],
)
def browser_agent(company: dict) -> dict:
    """Browse a single company's homepage inside a sandboxed Playwright session.

    Creates a fresh sandbox, installs Playwright, and retries the browser
    server + agent loop up to MAX_BROWSER_ATTEMPTS times for retryable failures.
    """
    backend = get_agent_backend()
    validated_company = Company.model_validate(company)
    company_id = validated_company.id
    run_id = make_run_id(company_id, suffix=os.urandom(4).hex())
    artifact_dir = Path("/tmp/artifacts") / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifact_dir / "screenshot.png"
    metadata_path = artifact_dir / "metadata.json"

    print(f"[browser:{company_id}] Starting — {validated_company.name} ({validated_company.url})")

    client = SandboxClient.for_cloud()
    try:
        with client.create_and_connect(
            image="python:3.11-slim",
            allow_internet_access=True,
            timeout_secs=600,
            startup_timeout=120,
            memory_mb=4096,
            ephemeral_disk_mb=8192,
        ) as sandbox:
            print(f"[browser:{company_id}] Sandbox ready, installing Playwright...")
            r = sandbox.run(
                "python3",
                args=["-m", "pip", "install", "--break-system-packages", "-q", "playwright"],
                timeout=120,
            )
            if r.exit_code != 0:
                raise RuntimeError(f"pip install playwright failed: {r.stderr or r.stdout}")
            r = sandbox.run(
                "sh",
                args=["-c", "python3 -m playwright install --with-deps chromium > /tmp/pw.log 2>&1"],
                timeout=360,
            )
            if r.exit_code != 0:
                try:
                    log = sandbox.read_file("/tmp/pw.log").decode("utf-8", errors="replace")[-2000:]
                except Exception:
                    log = r.stderr or r.stdout or "no output"
                raise RuntimeError(f"playwright install chromium failed: {log}")
            print(f"[browser:{company_id}] Playwright installed, writing scripts...")
            sandbox.write_file("/app/browser_server.py", SANDBOX_BROWSER_SERVER.encode("utf-8"))
            sandbox.write_file("/app/browser_rpc.py", SANDBOX_RPC_CLIENT.encode("utf-8"))

            last_failure: BrowserArtifact | None = None

            for attempt in range(MAX_BROWSER_ATTEMPTS):
                if attempt > 0:
                    print(f"[browser:{company_id}] Retry {attempt + 1}/{MAX_BROWSER_ATTEMPTS} (reusing sandbox)...")

                result = _run_browser_attempt(
                    backend=backend,
                    sandbox=sandbox,
                    company=validated_company,
                    run_id=run_id,
                    artifact_dir=artifact_dir,
                    screenshot_path=screenshot_path,
                    metadata_path=metadata_path,
                )

                if result.status == "success":
                    title = result.metadata.title if result.metadata else "?"
                    print(f"[browser:{company_id}] Success — page title: {title!r}")
                    return result.model_dump(mode="json")

                last_failure = result
                stage = result.failure_stage or "browser_execution"

                if not is_retryable(stage):
                    print(f"[browser:{company_id}] Non-retryable failure ({stage}): {result.failure_reason}")
                    return result.model_dump(mode="json")

                if attempt < MAX_BROWSER_ATTEMPTS - 1:
                    print(f"[browser:{company_id}] Attempt {attempt + 1} failed ({stage}), will retry...")

            print(f"[browser:{company_id}] All {MAX_BROWSER_ATTEMPTS} attempts exhausted")
            return last_failure.model_dump(mode="json")

    except Exception as exc:
        return BrowserArtifact(
            company=validated_company,
            run_id=run_id,
            status="failed",
            failure_reason=str(exc),
            failure_stage="sandbox_startup",
            diagnostics={"artifact_dir": str(artifact_dir)},
        ).model_dump(mode="json")


def _run_browser_attempt(
    backend: object,
    sandbox: object,
    company: Company,
    run_id: str,
    artifact_dir: Path,
    screenshot_path: Path,
    metadata_path: Path,
) -> BrowserArtifact:
    """Single browser attempt inside an existing sandbox. Never raises."""
    proc = None
    tools = SandboxBrowserTools(sandbox=sandbox, save_dir=str(artifact_dir))
    try:
        print(f"[browser:{company.id}] Launching browser server for {company.url}")
        proc = sandbox.start_process(
            "python3",
            args=["/app/browser_server.py", "--url", str(company.url)],
            stdout_mode=OutputMode.CAPTURE,
            stderr_mode=OutputMode.CAPTURE,
        )
        try:
            _wait_for_browser_server(tools)
        except RuntimeError:
            # Log server output to help diagnose startup failures
            try:
                stdout = sandbox.get_stdout(proc.pid)
                stderr = sandbox.get_stderr(proc.pid)
                if stdout:
                    print(f"    [server stdout] {str(stdout)[:500]}")
                if stderr:
                    print(f"    [server stderr] {str(stderr)[:500]}")
            except Exception:
                pass
            raise
        print(f"[browser:{company.id}] Browser server ready, running agent...")
        artifact = backend.drive_browser(company=company, tools=tools)
        screenshot_bytes = sandbox.read_file("/app/screenshot.png")
        if artifact.metadata is None:
            raise ValueError("browser agent returned no metadata")
        metadata_bytes = json.dumps(artifact.metadata.model_dump(mode="json")).encode("utf-8")
        screenshot_path.write_bytes(screenshot_bytes)
        metadata_path.write_bytes(metadata_bytes)
        return BrowserArtifact(
            company=company,
            run_id=run_id,
            status="success",
            screenshot_path=str(screenshot_path),
            metadata_path=str(metadata_path),
            metadata=artifact.metadata,
        )
    except Exception as exc:
        print(f"[browser:{company.id}] Attempt failed: {exc}")
        return BrowserArtifact(
            company=company,
            run_id=run_id,
            status="failed",
            failure_reason=str(exc),
            failure_stage=classify_browser_failure_stage(str(exc)),
            diagnostics=_collect_browser_diagnostics(
                sandbox=sandbox,
                proc=proc,
                artifact_dir=artifact_dir,
            ),
        )
    finally:
        try:
            tools.shutdown()
        except Exception:
            pass
        if proc is not None:
            try:
                sandbox.kill_process(proc.pid)
            except Exception:
                pass



@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def analysis_agent(artifact: dict) -> dict:
    # Unset CLAUDECODE so claude-agent-sdk can spawn a subprocess when running
    # locally under a Claude Code session (which sets this env var).
    os.environ.pop("CLAUDECODE", None)
    backend = get_agent_backend()
    validated_artifact = BrowserArtifact.model_validate(artifact)
    company_name = validated_artifact.company.name
    print(f"[analysis:{company_name}] Scoring homepage with vision...")
    if not validated_artifact.screenshot_path or not validated_artifact.metadata:
        raise ValueError("analysis requires screenshot_path and metadata")
    payload = parse_json(backend.analyze(validated_artifact))
    scorecard = Scorecard.model_validate(payload)
    overall = compute_overall_score(scorecard.scores)
    adjusted = scorecard.model_copy(
        update={
            "run_id": validated_artifact.run_id,
            "overall_score": overall,
        }
    )
    print(f"[analysis:{company_name}] Score: {overall:.2f}/10")
    return adjusted.model_dump(mode="json")


@function(timeout=180, secrets=["ANTHROPIC_API_KEY"])
def report_agent(
    domain: str,
    requested_count: int,
    companies: list[dict],
    raw_artifacts: list[dict],
    scorecards: list[dict],
) -> dict:
    validated_scorecards = sort_scorecards([Scorecard.model_validate(card) for card in scorecards])
    failures = [
        FailureRecord(company=artifact["company"]["name"], reason=artifact.get("failure_reason", "unknown"))
        for artifact in raw_artifacts
        if artifact.get("status") != "success"
    ]
    if not validated_scorecards:
        print("[report] No successful scorecards — generating empty report")
        return build_empty_report_bundle(
            domain=domain,
            requested_count=requested_count,
            discovered_count=len(companies),
            failures=[failure.model_dump(mode="json") for failure in failures],
        ).model_dump(mode="json")

    print(f"[report] Generating report for {len(validated_scorecards)} companies "
          f"({len(failures)} failures)...")
    backend = get_agent_backend()
    report_markdown = backend.report(validated_scorecards)

    # Load screenshots for the HTML report
    screenshots: dict[str, str] = {}
    for artifact in raw_artifacts:
        if artifact.get("status") == "success" and artifact.get("screenshot_path"):
            company_name = artifact["company"]["name"]
            try:
                png_bytes = open(artifact["screenshot_path"], "rb").read()
                screenshots[company_name] = base64.b64encode(png_bytes).decode("ascii")
            except OSError:
                pass

    html_report = build_html_report(
        domain=domain,
        scorecards=validated_scorecards,
        failures=failures,
        screenshots=screenshots,
        markdown_report=report_markdown,
    )
    print(f"[report] HTML report generated ({len(html_report):,} chars)")

    bundle = ReportBundle(
        domain=domain,
        requested_count=requested_count,
        discovered_count=len(companies),
        successful_count=len(validated_scorecards),
        failed_count=len(failures),
        failures=failures,
        scorecards=validated_scorecards,
        markdown_report=report_markdown,
        html_report=html_report,
        summary_csv=build_summary_csv(validated_scorecards),
    )
    print(f"[report] Report ready — {len(validated_scorecards)} scorecards, "
          f"{len(bundle.markdown_report)} chars markdown")
    return bundle.model_dump(mode="json")


def _wait_for_browser_server(tools: SandboxBrowserTools, timeout_seconds: float = 90.0) -> None:
    """Wait for the browser server to be reachable AND the browser to be ready."""
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    server_up = False

    while time.time() < deadline:
        try:
            result = tools._rpc("ping")
            if not server_up:
                print(f"    [server] HTTP server is up, waiting for browser...")
                server_up = True
            if result.get("error"):
                raise RuntimeError(f"browser launch failed: {result['error']}")
            if result.get("ready"):
                return
            time.sleep(1.0)
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)

    if server_up:
        raise RuntimeError("browser server responded but browser never became ready")
    raise RuntimeError(
        f"browser server not reachable after {timeout_seconds}s: {last_error}"
    ) from last_error


def _collect_browser_diagnostics(sandbox: object, proc: object | None, artifact_dir: Path) -> dict:
    diagnostics: dict[str, object] = {"artifact_dir": str(artifact_dir)}
    if proc is not None:
        diagnostics["browser_server_pid"] = getattr(proc, "pid", None)
        try:
            diagnostics["browser_server_stdout"] = str(sandbox.get_stdout(proc.pid))
        except Exception:
            diagnostics["browser_server_stdout"] = None
        try:
            diagnostics["browser_server_stderr"] = str(sandbox.get_stderr(proc.pid))
        except Exception:
            diagnostics["browser_server_stderr"] = None
    for path_key, path in {
        "playwright_install_log": "/tmp/playwright-install.log",
        "pip_playwright_log": "/tmp/pip-playwright.log",
    }.items():
        try:
            diagnostics[path_key] = sandbox.read_file(path).decode("utf-8", errors="replace")[-4000:]
        except Exception:
            diagnostics[path_key] = None
    return diagnostics


def _open_in_browser(path: str) -> None:
    """Open a file in the default browser. Best-effort, never raises."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", path])
        elif system == "Windows":
            os.startfile(path)
        print(f"[report] Opened in browser: {path}")
    except Exception:
        print(f"[report] Could not auto-open. Open manually: {path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Competitive Website Analyst")
    parser.add_argument("domain", help="Market category to research (e.g. 'AI coding assistants')")
    parser.add_argument("--count", type=int, default=5, help="Number of companies to discover (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Print full agent traces without truncation")
    args = parser.parse_args()

    if args.verbose:
        os.environ["COMPETITIVE_ANALYST_VERBOSE"] = "1"

    request = run_local_application(competitive_analyst, args.domain, args.count)
    print(json.dumps(request.output(), indent=2))
