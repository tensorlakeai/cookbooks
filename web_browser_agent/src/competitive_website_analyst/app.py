from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tensorlake.applications import Image, Retries, application, function, run_local_application
from tensorlake.sandbox import OutputMode, SandboxClient

from competitive_website_analyst.agent_backend import get_agent_backend
from competitive_website_analyst.browser_failures import classify_browser_failure_stage
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


@application(retries=Retries(max_retries=1))
@function()
def competitive_analyst(domain: str, count: int) -> dict:
    companies = research_agent.future(domain, count)
    raw_artifacts = browser_agent.map(companies)
    successful_artifacts = filter_successful(raw_artifacts)
    scorecards = analysis_agent.map(successful_artifacts)
    return report_agent(domain, count, companies, raw_artifacts, scorecards)


@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def research_agent(domain: str, count: int) -> list[dict]:
    backend = get_agent_backend()
    payload = parse_json(backend.research(domain, count))
    return [company.model_dump(mode="json") for company in validate_companies(payload, count)]


@function(
    image=browser_image,
    timeout=180,
    secrets=["ANTHROPIC_API_KEY", "TENSORLAKE_API_KEY"],
    retries=Retries(max_retries=2),
)
def browser_agent(company: dict) -> dict:
    backend = get_agent_backend()
    validated_company = Company.model_validate(company)
    company_id = validated_company.id
    run_id = make_run_id(company_id, suffix=os.urandom(4).hex())
    artifact_dir = Path("/tmp/artifacts") / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifact_dir / "screenshot.png"
    metadata_path = artifact_dir / "metadata.json"

    client = SandboxClient.for_cloud()
    try:
        with client.create_and_connect(
            snapshot_id=os.getenv("BROWSER_SANDBOX_SNAPSHOT_ID"),
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
                    args=["/app/browser_server.py", "--url", str(validated_company.url)],
                    stdout_mode=OutputMode.CAPTURE,
                    stderr_mode=OutputMode.CAPTURE,
                )
                _wait_for_browser_server(tools)
                artifact = backend.drive_browser(company=validated_company, tools=tools)
                screenshot_bytes = sandbox.read_file("/app/screenshot.png")
                if artifact.metadata is None:
                    raise ValueError("browser agent returned no metadata")
                metadata_bytes = json.dumps(artifact.metadata.model_dump(mode="json")).encode("utf-8")
                screenshot_path.write_bytes(screenshot_bytes)
                metadata_path.write_bytes(metadata_bytes)
                return BrowserArtifact(
                    company=validated_company,
                    run_id=run_id,
                    status="success",
                    screenshot_path=str(screenshot_path),
                    metadata_path=str(metadata_path),
                    metadata=artifact.metadata,
                ).model_dump(mode="json")
            except Exception as exc:
                return BrowserArtifact(
                    company=validated_company,
                    run_id=run_id,
                    status="failed",
                    failure_reason=str(exc),
                    failure_stage=classify_browser_failure_stage(str(exc)),
                    diagnostics=_collect_browser_diagnostics(
                        sandbox=sandbox,
                        proc=proc,
                        artifact_dir=artifact_dir,
                    ),
                ).model_dump(mode="json")
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
            company=validated_company,
            run_id=run_id,
            status="failed",
            failure_reason=str(exc),
            failure_stage="sandbox_startup",
            diagnostics={"artifact_dir": str(artifact_dir)},
        ).model_dump(mode="json")


@function()
def filter_successful(artifacts: list[dict]) -> list[dict]:
    return [artifact for artifact in artifacts if artifact.get("status") == "success"]


@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def analysis_agent(artifact: dict) -> dict:
    backend = get_agent_backend()
    validated_artifact = BrowserArtifact.model_validate(artifact)
    if not validated_artifact.screenshot_path or not validated_artifact.metadata:
        raise ValueError("analysis requires screenshot_path and metadata")
    payload = parse_json(backend.analyze(validated_artifact))
    scorecard = Scorecard.model_validate(payload)
    adjusted = scorecard.model_copy(
        update={
            "run_id": validated_artifact.run_id,
            "overall_score": compute_overall_score(scorecard.scores),
        }
    )
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
        return build_empty_report_bundle(
            domain=domain,
            requested_count=requested_count,
            discovered_count=len(companies),
            failures=[failure.model_dump(mode="json") for failure in failures],
        ).model_dump(mode="json")

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
    request = run_local_application(competitive_analyst, "AI coding assistants", 3)
    print(json.dumps(request.output(), indent=2))
