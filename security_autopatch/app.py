"""Security Autopatch — Tensorlake Application

Replicates the Ramp "100 Vulnerabilities Patched with 0 Humans" architecture
using the Claude Agent SDK for sub-agents and Tensorlake for orchestration.

Architecture
────────────
security_autopatch  (Tensorlake @application — coordinator agent with custom tools)
├── build_code_corpus   (Tensorlake @function  — pure Python, no LLM)
├── run_detector        (Tensorlake @function  — Claude Agent SDK sub-agent)
├── run_manager_review  (Tensorlake @function  — Claude Agent SDK sub-agent)
├── run_validator       (Tensorlake @function  — Claude Agent SDK sub-agent)
└── run_fixer           (Tensorlake @function  — Claude Agent SDK sub-agent)

Each sub-agent receives the full repository via Tensorlake's RequestContext
(backed by S3).  On each container the repo is reconstituted on the local
filesystem so the Claude agent can explore it with Read, Glob, and Grep tools.
All inter-agent communication uses plain text.
"""

import asyncio
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tensorlake.applications import (
    Future,
    Image,
    RETURN_WHEN,
    RequestContext,
    Retries,
    application,
    function,
    run_local_application,
)

from models import (
    SecuritySweepReport,
    SecuritySweepRequest,
    FileSnippet,
)
from skills import (
    COORDINATOR_SKILL,
    FIXER_SKILL,
    MANAGER_SKILL,
    VALIDATOR_SKILL,
    build_detector_skill,
)


# ---------------------------------------------------------------------------
# Sandbox flag — Tensorlake containers are sandboxed, so signal this to the
# Claude Agent SDK / Claude Code runtime.  Without this, running as root
# (common in Docker) with bypassPermissions mode will be rejected.
# See: https://github.com/anthropics/claude-code/issues/9184
# ---------------------------------------------------------------------------

os.environ.setdefault("IS_SANDBOX", "1")


# ---------------------------------------------------------------------------
# Container image
# ---------------------------------------------------------------------------

security_image = (
    Image(name="security-autopatch")
    .run("apt-get update && apt-get install -y git")
    .run("pip install claude-agent-sdk pydantic")
    .run("echo 'IS_SANDBOX=1' >> /etc/environment")
)


# ---------------------------------------------------------------------------
# Pure-Python helpers
# ---------------------------------------------------------------------------

def _resolve_repo_path(repo_path: str) -> Path:
    resolved = Path(repo_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {resolved}")
    return resolved


def _match_glob(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(path, pattern[3:])
    return False


def _matches_globs(path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    include_ok = True if not include_globs else any(
        _match_glob(path, p) for p in include_globs
    )
    if not include_ok:
        return False
    return not any(_match_glob(path, p) for p in exclude_globs)


def _reconstitute_repo(snippets: list[FileSnippet]) -> str:
    """Write FileSnippets to a temp directory, recreating the repo structure.

    Returns the absolute path to the temporary directory.
    """
    repo_dir = tempfile.mkdtemp(prefix="repo-")
    for snippet in snippets:
        file_path = Path(repo_dir) / snippet.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(snippet.content, encoding="utf-8")
    return repo_dir


def _file_listing(snippets: list[FileSnippet]) -> str:
    """Generate a compact file listing for agent prompts."""
    return "\n".join(f"  {s.path} ({s.line_count} lines)" for s in snippets)


# ---------------------------------------------------------------------------
# Claude Agent SDK helper — agents communicate in plain text
# ---------------------------------------------------------------------------

def _run_claude_agent(
    system_prompt: str,
    user_prompt: str,
    max_turns: int = 3,
    progress_label: str | None = None,
    progress_step: int | None = None,
    progress_total: int = 6,
) -> str:
    """Run a one-shot Claude Agent SDK agent and return its text result.

    When *progress_label* and *progress_step* are supplied the function
    emits fine-grained Tensorlake progress updates by inspecting the
    streamed messages for tool_use blocks (no SDK hooks required).
    """
    from claude_agent_sdk import ClaudeAgentOptions, query as claude_query

    result_text = ""
    collected_blocks: list[str] = []

    # Grab ctx once on the Tensorlake worker thread (before asyncio.run).
    ctx = RequestContext.get() if progress_label else None

    def _emit_progress(tool_name: str, tool_input: dict) -> None:
        if ctx is None or progress_step is None:
            return
        try:
            detail = ""
            if tool_name == "Read" and tool_input.get("file_path"):
                detail = f" → {tool_input['file_path'].split('/')[-1]}"
            elif tool_name == "Grep" and tool_input.get("pattern"):
                detail = f" → /{tool_input['pattern']}/"
            elif tool_name == "Glob" and tool_input.get("pattern"):
                detail = f" → {tool_input['pattern']}"
            ctx.progress.update(
                progress_step, progress_total,
                f"{progress_label}: {tool_name}{detail}",
            )
        except Exception as exc:
            print(f"[progress] {progress_label}: {type(exc).__name__}: {exc}")

    async def _run():
        nonlocal result_text
        async for msg in claude_query(
            prompt=user_prompt,
            options=ClaudeAgentOptions(
                system_prompt=system_prompt,
                max_turns=max_turns,
                permission_mode="bypassPermissions",
            ),
        ):
            if hasattr(msg, "content") and msg.content:
                for block in msg.content:
                    if hasattr(block, "text"):
                        collected_blocks.append(block.text)
                    # Detect tool_use blocks → emit progress
                    if getattr(block, "type", None) == "tool_use":
                        _emit_progress(
                            getattr(block, "name", ""),
                            getattr(block, "input", {}),
                        )
            if hasattr(msg, "result"):
                result_text = msg.result

    asyncio.run(_run())
    return result_text or "\n".join(collected_blocks)


def _get_repo_snippets() -> list[FileSnippet]:
    """Read the repo snippets from Tensorlake's request context."""
    ctx = RequestContext.get()
    return ctx.state.get("repo_snippets")


# ---------------------------------------------------------------------------
# Tensorlake function: build_code_corpus (no LLM — pure Python)
# ---------------------------------------------------------------------------

@function(image=security_image, timeout=300)
def build_code_corpus(request: SecuritySweepRequest) -> int:
    """Read all matching files and write them to request context.

    Returns the number of files found.  The full list of FileSnippet objects
    is stored in context under ``repo_snippets`` so every downstream function
    can access it via ``ctx.request_state.get("repo_snippets")``.
    """
    if request.repo_url:
        tmp = tempfile.mkdtemp()
        clone_cmd = ["git", "clone", "--depth", "1"]
        if request.repo_branch:
            clone_cmd += ["--branch", request.repo_branch]
        clone_cmd += [request.repo_url, tmp]

        safe_url = request.repo_url.split("@")[-1] if "@" in request.repo_url else request.repo_url
        print(f"[build_code_corpus] Cloning {safe_url}")

        try:
            subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "(no output)"
            raise RuntimeError(f"Failed to clone repository '{safe_url}': {stderr}") from exc

        repo = Path(tmp)
    else:
        repo = _resolve_repo_path(request.repo_path)

    extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in request.file_extensions
    }

    snippets: list[FileSnippet] = []
    for candidate in repo.rglob("*"):
        if not candidate.is_file():
            continue
        if extensions and candidate.suffix.lower() not in extensions:
            continue
        relative = candidate.relative_to(repo).as_posix()
        if not _matches_globs(relative, request.include_globs, request.exclude_globs):
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        stripped = content.strip()
        if not stripped:
            continue
        snippets.append(FileSnippet(
            path=relative,
            content=stripped[: request.max_chars_per_file],
            line_count=content.count("\n") + 1,
        ))

    snippets.sort(key=lambda s: s.path)

    # Write the whole repo into request context (backed by S3)
    ctx = RequestContext.get()
    ctx.state.set("repo_snippets", snippets)

    return len(snippets)


# ---------------------------------------------------------------------------
# Tensorlake function: DETECTOR sub-agent
# ---------------------------------------------------------------------------

@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=2),
    timeout=900,
    max_containers=8,
    warm_containers=1,
)
def run_detector(
    vulnerability_class: str,
    request: SecuritySweepRequest,
) -> str:
    """Detector sub-agent — explores the repo filesystem and reports findings."""
    snippets = _get_repo_snippets()
    repo_dir = _reconstitute_repo(snippets)
    listing = _file_listing(snippets)

    skill = build_detector_skill(vulnerability_class)

    prompt = (
        f"Analyze the repository for **{vulnerability_class}** vulnerabilities.\n\n"
        f"The repository has been checked out at: `{repo_dir}`\n"
        f"It contains {len(snippets)} files:\n{listing}\n\n"
        "Use your file reading tools to explore the codebase. Start with the "
        "files most likely to contain this vulnerability class, then follow "
        "imports, check middleware, configuration, and related files for full "
        "context.\n\n"
        f"Report at most {request.max_findings_per_detector} findings.\n\n"
        "For each finding include:\n"
        "- A clear title\n"
        "- Severity (low / medium / high / critical)\n"
        "- The file path and line numbers\n"
        "- The vulnerable code evidence\n"
        "- An exploit scenario\n"
        "- A recommended fix\n"
        "- Your confidence (0-100%)\n\n"
        "If you find no vulnerabilities, say so and explain why."
    )

    try:
        return _run_claude_agent(
            skill, prompt, max_turns=8,
            progress_label=f"Detector[{vulnerability_class}]", progress_step=2,
        )
    except Exception as exc:
        return f"[detector_error for {vulnerability_class}]: {exc}"


# ---------------------------------------------------------------------------
# Tensorlake function: MANAGER sub-agent
# ---------------------------------------------------------------------------

@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=600,
    max_containers=12,
)
def run_manager_review(
    vulnerability_class: str,
    detector_output: str,
    request: SecuritySweepRequest,
) -> str:
    """Manager sub-agent — adversarial review with full repo access."""
    snippets = _get_repo_snippets()
    repo_dir = _reconstitute_repo(snippets)
    listing = _file_listing(snippets)

    prompt = (
        "Review the following detector output with an adversarial lens.\n\n"
        f"## Detector Output\n{detector_output}\n\n"
        f"## Repository\n"
        f"The full repository is available at: `{repo_dir}`\n"
        f"Files ({len(snippets)}):\n{listing}\n\n"
        "Use your file reading tools to independently verify each finding. "
        "Read the cited files, check for upstream guards, middleware, "
        "decorators, and framework protections the detector may have missed.\n\n"
        "For each finding, state whether you:\n"
        "- **APPROVE** — the vulnerability is credible and exploitable\n"
        "- **REJECT** — it is a false positive, with your reasoning\n"
        "- **NEEDS HUMAN** — you cannot determine; a human analyst should review\n\n"
        "Give a clear rationale for each decision."
    )

    try:
        return _run_claude_agent(
            MANAGER_SKILL, prompt, max_turns=8,
            progress_label=f"Manager[{vulnerability_class}]", progress_step=3,
        )
    except Exception as exc:
        return f"[manager_error for {vulnerability_class}]: {exc}"


# ---------------------------------------------------------------------------
# Tensorlake function: VALIDATOR sub-agent
# ---------------------------------------------------------------------------

@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=600,
    max_containers=8,
)
def run_validator(
    vulnerability_class: str,
    detector_output: str,
    manager_output: str,
    request: SecuritySweepRequest,
) -> str:
    """Validator sub-agent — writes tests with full repo access."""
    snippets = _get_repo_snippets()
    repo_dir = _reconstitute_repo(snippets)
    listing = _file_listing(snippets)

    prompt = (
        "Given the following approved vulnerability findings, draft "
        "integration tests that prove each vulnerability is real.\n\n"
        f"## Detector Findings\n{detector_output}\n\n"
        f"## Manager Review\n{manager_output}\n\n"
        f"## Repository\n"
        f"The full repository is available at: `{repo_dir}`\n"
        f"Files ({len(snippets)}):\n{listing}\n\n"
        "Use your file reading tools to read the vulnerable code and its "
        "dependencies so you can write accurate, runnable tests.\n\n"
        f"Default test command: `{request.test_command}`\n\n"
        "For each approved finding:\n"
        "1. Write a test that FAILS before the fix and PASSES after.\n"
        "2. State whether the finding is CONFIRMED, FALSE POSITIVE, or NEEDS HUMAN.\n"
        "3. Include the full test code and the command to run it.\n"
    )

    try:
        return _run_claude_agent(
            VALIDATOR_SKILL, prompt, max_turns=8,
            progress_label=f"Validator[{vulnerability_class}]", progress_step=4,
        )
    except Exception as exc:
        return f"[validator_error for {vulnerability_class}]: {exc}"


# ---------------------------------------------------------------------------
# Tensorlake function: FIXER sub-agent
# ---------------------------------------------------------------------------

@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=900,
    max_containers=4,
)
def run_fixer(
    vulnerability_class: str,
    detector_output: str,
    manager_output: str,
    validator_output: str,
    request: SecuritySweepRequest,
) -> str:
    """Fixer sub-agent — generates patches with full repo access."""
    snippets = _get_repo_snippets()
    repo_dir = _reconstitute_repo(snippets)
    listing = _file_listing(snippets)

    prompt = (
        "Given the following confirmed vulnerabilities, their validation "
        "tests, and full repository access, generate minimal patches.\n\n"
        f"## Detector Findings\n{detector_output}\n\n"
        f"## Manager Review\n{manager_output}\n\n"
        f"## Validator Results\n{validator_output}\n\n"
        f"## Repository\n"
        f"The full repository is available at: `{repo_dir}`\n"
        f"Files ({len(snippets)}):\n{listing}\n\n"
        "Use your file reading tools to read the vulnerable files and their "
        "context so your patches are accurate and style-consistent.\n\n"
        "For each confirmed finding:\n"
        "1. Generate a unified diff patch.\n"
        "2. Write a PR title and description.\n"
        "3. List all files touched.\n"
        "If a finding was not confirmed, skip it and explain why.\n"
    )

    try:
        return _run_claude_agent(
            FIXER_SKILL, prompt, max_turns=8,
            progress_label=f"Fixer[{vulnerability_class}]", progress_step=5,
        )
    except Exception as exc:
        return f"[fixer_error for {vulnerability_class}]: {exc}"


# ---------------------------------------------------------------------------
# Build final markdown report — let Claude assemble it from the raw outputs
# ---------------------------------------------------------------------------

def _build_report_markdown(
    request: SecuritySweepRequest,
    files_scanned: int,
    stage_outputs: dict[str, dict[str, str]],
) -> str:
    """Use Claude to compile a polished markdown report from stage outputs."""
    raw_sections = []
    for vc in request.vulnerability_classes:
        outputs = stage_outputs.get(vc, {})
        section = f"## Vulnerability Class: {vc}\n\n"
        for stage_name in ("detector", "manager", "validator", "fixer"):
            if outputs.get(stage_name):
                section += f"### {stage_name.title()} Output\n\n{outputs[stage_name]}\n\n"
        raw_sections.append(section)

    all_raw = "\n---\n\n".join(raw_sections)

    prompt = (
        "Below are the raw outputs from a multi-stage security vulnerability "
        "scan.  Compile them into a single, clean Markdown report.\n\n"
        f"**Repository:** {request.repo_url or request.repo_path}\n"
        f"**Branch:** {request.repo_branch or 'default'}\n"
        f"**Files scanned:** {files_scanned}\n\n"
        "Include:\n"
        "- An executive summary at the top with counts of findings at each stage\n"
        "- A section per vulnerability class with detection, review, validation, "
        "and fix details\n"
        "- Any patch diffs in fenced code blocks\n"
        "- A clear call-to-action for human reviewers\n\n"
        f"Raw stage outputs:\n\n{all_raw}"
    )

    system = (
        "You are a technical writer compiling a security report. "
        "Output well-structured Markdown only.  No preamble or commentary."
    )

    try:
        return _run_claude_agent(
            system, prompt,
            progress_label="Report", progress_step=6,
        )
    except Exception:
        # Fallback: concatenate raw outputs directly
        return f"# Security Autopatch Sweep\n\n{all_raw}"


# ---------------------------------------------------------------------------
# Tensorlake application: COORDINATOR AGENT
# ---------------------------------------------------------------------------

@application(
    tags={
        "pattern": "detector-manager-validator-fixer",
        "domain": "security",
        "inspired_by": "ramp-100-vulns-blog",
    },
    retries=Retries(max_retries=1),
)
@function(image=security_image, secrets=["ANTHROPIC_API_KEY"], timeout=3600)
def security_autopatch(request: SecuritySweepRequest) -> SecuritySweepReport:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        create_sdk_mcp_server,
        tool,
    )

    ctx = RequestContext.get()

    # ── Step 1: build code corpus (writes repo into context) ──────────
    ctx.progress.update(1, 6, "Collecting code corpus", {"repo": request.repo_url or request.repo_path})
    files_scanned = build_code_corpus(request)

    if not files_scanned:
        return SecuritySweepReport(
            repo_path=request.repo_url or request.repo_path,
            repo_branch=request.repo_branch,
            files_scanned=0,
            vulnerability_classes=request.vulnerability_classes,
            summary_markdown="# Security Autopatch Sweep\n\nNo matching files were found for scanning.",
            stage_outputs={},
        )

    # ── Coordinator mutable state (shared with tool closures) ──────────
    # stage_outputs[vuln_class] = {detector: str, manager: str, ...}
    stage_outputs: dict[str, dict[str, str]] = {
        vc: {} for vc in request.vulnerability_classes
    }

    # ── Custom MCP tools for the coordinator agent ─────────────────────

    @tool(
        "run_detectors",
        "Run vulnerability detectors in parallel across the codebase. "
        "Returns a summary of all detected findings as text.",
        {
            "type": "object",
            "properties": {
                "vulnerability_classes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of vulnerability classes to scan for",
                },
            },
            "required": ["vulnerability_classes"],
        },
    )
    async def run_detectors_tool(args: dict[str, Any]) -> dict[str, Any]:
        vuln_classes = args.get("vulnerability_classes", request.vulnerability_classes)
        ctx.progress.update(2, 6, f"Running {len(vuln_classes)} detectors")

        futures: dict[str, Future] = {
            vc: run_detector.awaitable(vc, request).run()
            for vc in vuln_classes
        }
        Future.wait(futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        results_text = []
        for vc, fut in futures.items():
            try:
                output = fut.result()
            except Exception as exc:
                output = f"[detector_future_error for {vc}]: {exc}"
            stage_outputs[vc]["detector"] = output
            results_text.append(f"## {vc}\n\n{output}")

        combined = "\n\n---\n\n".join(results_text)
        return {"content": [{"type": "text", "text": f"Detection complete.\n\n{combined}"}]}

    @tool(
        "run_managers",
        "Run adversarial manager reviews on detected findings in parallel.",
        {"type": "object", "properties": {}},
    )
    async def run_managers_tool(args: dict[str, Any]) -> dict[str, Any]:
        vuln_classes = [vc for vc in request.vulnerability_classes if stage_outputs[vc].get("detector")]
        if not vuln_classes:
            return {"content": [{"type": "text", "text": "No detector outputs to review."}]}

        ctx.progress.update(3, 6, f"Manager review for {len(vuln_classes)} classes")

        futures: dict[str, Future] = {
            vc: run_manager_review.awaitable(
                vc, stage_outputs[vc]["detector"], request,
            ).run()
            for vc in vuln_classes
        }
        Future.wait(futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        results_text = []
        for vc, fut in futures.items():
            try:
                output = fut.result()
            except Exception as exc:
                output = f"[manager_future_error for {vc}]: {exc}"
            stage_outputs[vc]["manager"] = output
            results_text.append(f"## {vc}\n\n{output}")

        combined = "\n\n---\n\n".join(results_text)
        return {"content": [{"type": "text", "text": f"Manager review complete.\n\n{combined}"}]}

    @tool(
        "run_validators",
        "Run validation tests on approved findings in parallel.",
        {"type": "object", "properties": {}},
    )
    async def run_validators_tool(args: dict[str, Any]) -> dict[str, Any]:
        vuln_classes = [
            vc for vc in request.vulnerability_classes
            if stage_outputs[vc].get("manager")
        ]
        if not vuln_classes:
            return {"content": [{"type": "text", "text": "No manager outputs to validate."}]}

        ctx.progress.update(4, 6, f"Validating {len(vuln_classes)} classes")

        futures: dict[str, Future] = {
            vc: run_validator.awaitable(
                vc,
                stage_outputs[vc]["detector"],
                stage_outputs[vc]["manager"],
                request,
            ).run()
            for vc in vuln_classes
        }
        Future.wait(futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        results_text = []
        for vc, fut in futures.items():
            try:
                output = fut.result()
            except Exception as exc:
                output = f"[validator_future_error for {vc}]: {exc}"
            stage_outputs[vc]["validator"] = output
            results_text.append(f"## {vc}\n\n{output}")

        combined = "\n\n---\n\n".join(results_text)
        return {"content": [{"type": "text", "text": f"Validation complete.\n\n{combined}"}]}

    @tool(
        "run_fixers",
        "Generate fix patches for confirmed findings in parallel.",
        {"type": "object", "properties": {}},
    )
    async def run_fixers_tool(args: dict[str, Any]) -> dict[str, Any]:
        vuln_classes = [
            vc for vc in request.vulnerability_classes
            if stage_outputs[vc].get("validator")
        ]
        if not vuln_classes:
            return {"content": [{"type": "text", "text": "No validated findings to fix."}]}

        ctx.progress.update(5, 6, f"Generating fixes for {len(vuln_classes)} classes")

        futures: dict[str, Future] = {
            vc: run_fixer.awaitable(
                vc,
                stage_outputs[vc]["detector"],
                stage_outputs[vc]["manager"],
                stage_outputs[vc]["validator"],
                request,
            ).run()
            for vc in vuln_classes
        }
        Future.wait(futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        results_text = []
        for vc, fut in futures.items():
            try:
                output = fut.result()
            except Exception as exc:
                output = f"[fixer_future_error for {vc}]: {exc}"
            stage_outputs[vc]["fixer"] = output
            results_text.append(f"## {vc}\n\n{output}")

        combined = "\n\n---\n\n".join(results_text)
        return {"content": [{"type": "text", "text": f"Fix generation complete.\n\n{combined}"}]}

    # ── Create MCP server with coordinator tools ───────────────────────
    server = create_sdk_mcp_server(
        "security-coordinator",
        version="1.0.0",
        tools=[run_detectors_tool, run_managers_tool, run_validators_tool, run_fixers_tool],
    )

    # ── Run the coordinator agent ──────────────────────────────────────
    coordinator_prompt = (
        "You are coordinating a security vulnerability sweep.\n\n"
        f"Repository: {request.repo_url or request.repo_path}\n"
        f"Branch: {request.repo_branch or 'default'}\n"
        f"Files scanned: {files_scanned}\n"
        f"Vulnerability classes: {', '.join(request.vulnerability_classes)}\n"
        f"Validation enabled: {request.run_validation}\n"
        f"Fix generation enabled: {request.generate_fixes}\n\n"
        "Execute the full security sweep now.  Call each tool in order:\n"
        f"1. run_detectors with vulnerability_classes={json.dumps(request.vulnerability_classes)}\n"
        "2. run_managers\n"
        + ("3. run_validators\n" if request.run_validation else "")
        + ("4. run_fixers\n" if request.generate_fixes else "")
        + "\nAfter all stages complete, provide a concise summary of results."
    )

    async def _coordinate():
        async with ClaudeSDKClient(
            options=ClaudeAgentOptions(
                system_prompt=COORDINATOR_SKILL,
                mcp_servers={"tools": server},
                allowed_tools=[
                    "mcp__tools__run_detectors",
                    "mcp__tools__run_managers",
                    "mcp__tools__run_validators",
                    "mcp__tools__run_fixers",
                ],
                permission_mode="bypassPermissions",
                max_turns=10,
            ),
        ) as client:
            await client.query(coordinator_prompt)
            async for _msg in client.receive_response():
                # Emit progress when coordinator invokes MCP tools
                if hasattr(_msg, "content") and _msg.content:
                    for block in _msg.content:
                        if getattr(block, "type", None) == "tool_use":
                            try:
                                ctx.progress.update(
                                    1, 6,
                                    f"Coordinator: {getattr(block, 'name', '')}",
                                )
                            except Exception:
                                pass

    asyncio.run(_coordinate())

    # ── Build the final report from accumulated text artifacts ──────────
    summary = _build_report_markdown(request, files_scanned, stage_outputs)

    ctx.progress.update(6, 6, "Security sweep complete")

    return SecuritySweepReport(
        repo_path=request.repo_url or request.repo_path,
        repo_branch=request.repo_branch,
        files_scanned=files_scanned,
        vulnerability_classes=request.vulnerability_classes,
        summary_markdown=summary,
        stage_outputs=stage_outputs,
    )


# ---------------------------------------------------------------------------
# Local runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    def _parse_list_env(name: str, default: list[str]) -> list[str]:
        raw = os.getenv(name, "")
        return [item.strip() for item in raw.split(",") if item.strip()] if raw.strip() else default

    sample_request = SecuritySweepRequest(
        repo_url=os.getenv("SCAN_REPO_URL", ""),
        repo_branch=os.getenv("SCAN_REPO_BRANCH", ""),
        repo_path=os.getenv("SCAN_REPO_PATH", "."),
        include_globs=_parse_list_env("SCAN_INCLUDE_GLOBS", ["**/*.py", "**/*.js", "**/*.ts", "**/*.rs", "**/*.go"]),
        exclude_globs=_parse_list_env(
            "SCAN_EXCLUDE_GLOBS",
            ["**/.venv/**", "**/venv/**", "**/node_modules/**"],
        ),
        vulnerability_classes=_parse_list_env(
            "SCAN_VULN_CLASSES",
            ["idor", "sql_injection", "ssrf", "command_injection"],
        ),
    )

    local_request = run_local_application(security_autopatch, sample_request)
    report: SecuritySweepReport = local_request.output()
    print(report.summary_markdown)

    report_path = os.getenv("SCAN_REPORT_PATH", "")
    if report_path:
        Path(report_path).write_text(report.summary_markdown, encoding="utf-8")
        print(f"Report written to {report_path}", file=sys.stderr)
