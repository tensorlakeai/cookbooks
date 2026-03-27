from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tensorlake.applications import Image, Retries, application, function, run_local_application
from tensorlake.sandbox import OutputMode, SandboxClient

from competitive_website_analyst.agent_backend import get_agent_backend
from competitive_website_analyst.browser_failures import classify_browser_failure_stage, is_retryable
from competitive_website_analyst.browser_runtime import SANDBOX_BROWSER_SERVER, SANDBOX_RPC_CLIENT, SandboxBrowserTools
from competitive_website_analyst.models import BrowserArtifact, Company, FailureRecord, ReportBundle, Scorecard
from competitive_website_analyst.scoring import build_empty_report_bundle, build_summary_csv, compute_overall_score, sort_scorecards
from competitive_website_analyst.utils import make_run_id, parse_json, validate_companies


browser_image = Image(base_image="python:3.11-slim").run(
    "pip install playwright && playwright install chromium --with-deps"
)

BROWSER_SERVER_BOOTSTRAP = """\
set -e
if ! python -c "import playwright" >/dev/null 2>&1; then
  python -m pip install --break-system-packages playwright >/tmp/pip-playwright.log 2>&1
  tail -100 /tmp/pip-playwright.log
fi
python -m playwright install --with-deps chromium >/tmp/playwright-install.log 2>&1 || {
  cat /tmp/playwright-install.log
  exit 1
}
tail -100 /tmp/playwright-install.log
"""


MAX_BACKFILL_ROUNDS = 3


@application()
@function()
def competitive_analyst(domain: str, count: int) -> dict:
    print(f"\n{'='*60}")
    print(f"  Competitive Website Analyst")
    print(f"  Domain: {domain!r}  |  Target: {count} companies")
    print(f"{'='*60}\n")

    print("[setup] Ensuring browser sandbox snapshot...")
    snapshot_id = ensure_browser_snapshot()

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
        print(f"\n[research] Searching for {needed + 5} candidates in '{domain}'...")
        candidates = research_agent(domain, needed + 5)
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
        tasks = prepare_browser_tasks(new_companies, snapshot_id)
        results = browser_agent.map(tasks)
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
    print(f"\n[report] Generating final competitive analysis report...")
    result = report_agent(domain, count, all_companies, all_artifacts, scorecards)
    print(f"[report] Done — {result.get('successful_count', 0)} companies in final report")
    print(f"\n{'='*60}")
    print(f"  Run complete")
    print(f"{'='*60}\n")
    return result


@function(timeout=600, secrets=["TENSORLAKE_API_KEY"])
def ensure_browser_snapshot() -> str:
    """Return a sandbox snapshot ID with Playwright pre-installed.

    If BROWSER_SANDBOX_SNAPSHOT_ID is already set, return it immediately.
    Otherwise, create a fresh sandbox, install Playwright + Chromium,
    snapshot it, and return the new snapshot ID.
    """
    existing = os.getenv("BROWSER_SANDBOX_SNAPSHOT_ID")
    if existing:
        print(f"Using existing browser snapshot: {existing}")
        return existing

    print("No browser snapshot found — creating one with Playwright + Chromium...")
    client = SandboxClient.for_cloud()
    with client.create_and_connect(
        image="python:3.11-slim",
        allow_internet_access=True,
        timeout_secs=600,
        startup_timeout=120,
    ) as sandbox:
        result = sandbox.run(
            "sh",
            args=["-lc", "pip install playwright && playwright install --with-deps chromium"],
            timeout=300,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to install browser runtime: {result.stderr or result.stdout}")
        snapshot = client.snapshot_and_wait(sandbox.id, timeout=300)
        print(f"Created browser snapshot: {snapshot.snapshot_id}")
        return snapshot.snapshot_id


@function()
def prepare_browser_tasks(companies: list[dict], snapshot_id: str) -> list[dict]:
    """Bundle each company with the shared snapshot ID for browser_agent.map()."""
    return [{"company": c, "snapshot_id": snapshot_id} for c in companies]


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
    image=browser_image,
    timeout=600,
    secrets=["ANTHROPIC_API_KEY", "TENSORLAKE_API_KEY"],
)
def browser_agent(task: dict) -> dict:
    """Browse a single company's homepage inside a sandboxed Playwright session.

    Retries internally up to MAX_BROWSER_ATTEMPTS for retryable failures
    (timeout, browser crash, sandbox issues). Non-retryable failures
    (DNS errors, CAPTCHA blocks) fail immediately.

    Args:
        task: {"company": <Company dict>, "snapshot_id": <str>}
    """
    backend = get_agent_backend()
    validated_company = Company.model_validate(task["company"])
    snapshot_id = task["snapshot_id"]
    company_id = validated_company.id

    print(f"[browser:{company_id}] Starting — {validated_company.name} ({validated_company.url})")
    last_failure: BrowserArtifact | None = None

    for attempt in range(MAX_BROWSER_ATTEMPTS):
        run_id = make_run_id(company_id, suffix=os.urandom(4).hex())
        artifact_dir = Path("/tmp/artifacts") / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = artifact_dir / "screenshot.png"
        metadata_path = artifact_dir / "metadata.json"

        if attempt > 0:
            print(f"[browser:{company_id}] Attempt {attempt + 1}/{MAX_BROWSER_ATTEMPTS}...")

        print(f"[browser:{company_id}] Creating sandbox and navigating to {validated_company.url}")
        result = _run_browser_session(
            backend=backend,
            company=validated_company,
            snapshot_id=snapshot_id,
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
            print(f"[browser:{company_id}] Attempt {attempt + 1} failed ({stage}), retrying...")

    print(f"[browser:{company_id}] All {MAX_BROWSER_ATTEMPTS} attempts exhausted")
    return last_failure.model_dump(mode="json")


def _run_browser_session(
    backend: object,
    company: Company,
    snapshot_id: str,
    run_id: str,
    artifact_dir: Path,
    screenshot_path: Path,
    metadata_path: Path,
) -> BrowserArtifact:
    """Single browser attempt. Returns a BrowserArtifact (success or failed), never raises."""
    client = SandboxClient.for_cloud()
    try:
        with client.create_and_connect(
            snapshot_id=snapshot_id,
            allow_internet_access=True,
            timeout_secs=180,
            startup_timeout=180,
        ) as sandbox:
            proc = None
            tools = SandboxBrowserTools(sandbox=sandbox)
            try:
                _ensure_browser_runtime(sandbox)
                sandbox.write_file("/app/browser_server.py", SANDBOX_BROWSER_SERVER.encode("utf-8"))
                sandbox.write_file("/app/browser_rpc.py", SANDBOX_RPC_CLIENT.encode("utf-8"))
                proc = sandbox.start_process(
                    "python",
                    args=["/app/browser_server.py", "--url", str(company.url)],
                    stdout_mode=OutputMode.CAPTURE,
                    stderr_mode=OutputMode.CAPTURE,
                )
                _wait_for_browser_server(tools)
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
    except Exception as exc:
        return BrowserArtifact(
            company=company,
            run_id=run_id,
            status="failed",
            failure_reason=str(exc),
            failure_stage="sandbox_startup",
            diagnostics={"artifact_dir": str(artifact_dir)},
        )



@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def analysis_agent(artifact: dict) -> dict:
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
    bundle = ReportBundle(
        domain=domain,
        requested_count=requested_count,
        discovered_count=len(companies),
        successful_count=len(validated_scorecards),
        failed_count=len(failures),
        failures=failures,
        scorecards=validated_scorecards,
        markdown_report=report_markdown,
        summary_csv=build_summary_csv(validated_scorecards),
    )
    print(f"[report] Report ready — {len(validated_scorecards)} scorecards, "
          f"{len(bundle.markdown_report)} chars markdown")
    return bundle.model_dump(mode="json")


def _wait_for_browser_server(tools: SandboxBrowserTools, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            tools.wait(0.1)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError("browser server failed to start") from last_error


def _ensure_browser_runtime(sandbox: object) -> None:
    check = sandbox.run("python", args=["-c", "import playwright"], timeout=15)
    if check.exit_code == 0:
        return
    install = sandbox.run(
        "sh",
        args=["-lc", BROWSER_SERVER_BOOTSTRAP],
        timeout=720,
    )
    if install.exit_code != 0:
        raise RuntimeError(install.stderr or install.stdout or "failed to install browser runtime")


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




if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Competitive Website Analyst")
    parser.add_argument("domain", help="Market category to research (e.g. 'AI coding assistants')")
    parser.add_argument("--count", type=int, default=5, help="Number of companies to discover (default: 5)")
    args = parser.parse_args()

    request = run_local_application(competitive_analyst, args.domain, args.count)
    print(json.dumps(request.output(), indent=2))
