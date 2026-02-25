"""Security Autopatch — Coordinator + multi-turn Claude Agent SDK sub-agents.

Architecture (mirrors Ramp's "100 vulnerabilities patched with 0 humans"):
  Coordinator (Tensorlake) orchestrates 5 stages, fanning out in parallel:
    1. build_code_corpus   — clone/scan repo, return file snippets
    2. run_detector        — Claude agent iterates with Read/Grep/Glob per vuln class
    3. run_manager_review  — Claude agent adversarially reviews each finding
    4. run_validator       — Claude agent writes integration tests per finding
    5. run_fixer           — Claude agent generates minimal patches

Each stage function is a synchronous Tensorlake @function that calls asyncio.run()
on an async Claude Agent SDK session.  The agents use native filesystem tools
(Read, Grep, Glob) to explore the code and custom in-process MCP tools to emit
structured results back to the coordinator.
"""

import asyncio
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
    CandidateFinding,
    DetectorResult,
    FileSnippet,
    FindingLifecycle,
    FixProposal,
    ManagerReview,
    SecuritySweepReport,
    SecuritySweepRequest,
    ValidationResult,
)
from prompts import (
    FIXER_SKILL,
    KEYWORDS_BY_CLASS,
    MANAGER_SKILL,
    ROUTE_HINTS,
    VALIDATOR_SKILL,
    build_detector_skill,
)


# ---------------------------------------------------------------------------
# Docker image
# The Claude Agent SDK (Python) spawns the Claude Code CLI as a subprocess,
# so Node.js and @anthropic-ai/claude-code must be installed alongside it.
# ---------------------------------------------------------------------------

security_image = (
    Image(name="security-autopatch-claude-sdk")
    .run("apt-get update && apt-get install -y git curl")
    .run(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - "
        "&& apt-get install -y nodejs"
    )
    .run("npm install -g @anthropic-ai/claude-code")
    .run("pip install claude-agent-sdk pydantic")
)


# ---------------------------------------------------------------------------
# Snippet utilities
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


def _score_snippet(snippet: FileSnippet, vulnerability_class: str) -> int:
    keywords = KEYWORDS_BY_CLASS.get(vulnerability_class, [])
    content = snippet.content.lower()
    path = snippet.path.lower()
    score = sum(content.count(kw.lower()) * 2 for kw in keywords)
    score += sum(2 for hint in ROUTE_HINTS if hint in content)
    if any(token in path for token in ("route", "api", "handler", "controller", "endpoint")):
        score += 2
    return score


def _select_snippets_for_detector(
    snippets: list[FileSnippet], vulnerability_class: str, limit: int
) -> list[FileSnippet]:
    ranked = sorted(
        snippets,
        key=lambda s: (_score_snippet(s, vulnerability_class), s.path),
        reverse=True,
    )
    selected = ranked[:limit]
    if not selected:
        return []
    if _score_snippet(selected[0], vulnerability_class) == 0:
        return sorted(snippets, key=lambda s: s.path)[:limit]
    return selected


def _snippets_for_finding(
    finding: CandidateFinding, snippets: list[FileSnippet], limit: int = 6
) -> list[FileSnippet]:
    exact = [s for s in snippets if s.path == finding.file_path]
    if exact:
        return exact[:limit]
    return _select_snippets_for_detector(snippets, finding.vulnerability_class, limit)


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(severity.lower(), 4)


def _write_snippets_to_tmpdir(snippets: list[FileSnippet]) -> str:
    """Write file snippets to a temp directory so agents can use Read/Grep/Glob."""
    tmpdir = tempfile.mkdtemp()
    for snippet in snippets:
        file_path = Path(tmpdir) / snippet.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(snippet.content, encoding="utf-8")
    return tmpdir


async def _make_prompt_stream(content: str):
    """Async generator wrapping a string prompt.

    Custom in-process MCP tools require streaming (async generator) input format —
    a plain string prompt does not work when mcp_servers contains SDK servers.
    """
    yield {"type": "user", "message": {"role": "user", "content": content}}


# ---------------------------------------------------------------------------
# Async agent runners
# Each function runs a full multi-turn Claude Agent SDK session:
#   - Writes relevant snippets to a tmpdir (native filesystem tools work)
#   - Gives the agent Read / Grep / Glob for iterative code exploration
#   - Registers a custom MCP tool to capture structured output
#   - Runs until the agent calls the output tool or hits max_turns
# ---------------------------------------------------------------------------

async def _run_agent(
    agent_name: str,
    prompt: str,
    options: "ClaudeAgentOptions",
) -> dict:
    """Run a single agent turn and return ResultMessage.structured_output.

    Uses output_format on ClaudeAgentOptions so the model is forced to emit
    structured JSON as its final response — no MCP tool round-trip needed.
    Returns the parsed dict or {} if the agent failed / produced no output.
    """
    from claude_agent_sdk import (
        query,
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolUseBlock,
    )

    def _on_stderr(line: str) -> None:
        print(f"[{agent_name}][stderr] {line}", flush=True)

    options.stderr = _on_stderr

    structured: dict = {}
    turn = 0
    async for message in query(prompt=_make_prompt_stream(prompt), options=options):
        if isinstance(message, AssistantMessage):
            turn += 1
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[{agent_name}] turn={turn} tool={block.name}", flush=True)
                elif isinstance(block, TextBlock):
                    preview = block.text[:200].replace("\n", " ")
                    print(f"[{agent_name}] turn={turn} text={preview!r}", flush=True)
        elif isinstance(message, ResultMessage):
            print(
                f"[{agent_name}] result subtype={message.subtype!r} "
                f"is_error={message.is_error} turns={message.num_turns}",
                flush=True,
            )
            if message.structured_output:
                structured = message.structured_output
            if message.is_error:
                raise RuntimeError(
                    f"[{agent_name}] agent error: subtype={message.subtype} "
                    f"result={message.result}"
                )
        elif isinstance(message, SystemMessage):
            print(f"[{agent_name}] system subtype={message.subtype!r}", flush=True)

    print(
        f"[{agent_name}] done. turns={turn} structured_output={bool(structured)}",
        flush=True,
    )
    return structured


async def _detector_agent(
    vulnerability_class: str,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> DetectorResult:
    from claude_agent_sdk import ClaudeAgentOptions

    selected = _select_snippets_for_detector(
        snippets, vulnerability_class, request.max_files_per_detector
    )
    tmpdir = _write_snippets_to_tmpdir(selected)

    options = ClaudeAgentOptions(
        system_prompt=build_detector_skill(vulnerability_class),
        allowed_tools=["Read", "Grep", "Glob"],
        permission_mode="bypassPermissions",
        cwd=tmpdir,
        max_turns=30,
        model=request.model,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "required": ["notes", "findings"],
                "properties": {
                    "notes": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "vulnerability_class", "severity", "endpoint",
                                "file_path", "line_start", "line_end", "summary",
                                "evidence", "exploit_scenario", "confidence",
                                "recommended_fix",
                            ],
                            "properties": {
                                "vulnerability_class": {"type": "string"},
                                "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                                "endpoint": {"type": "string"},
                                "file_path": {"type": "string"},
                                "line_start": {"type": "integer"},
                                "line_end": {"type": "integer"},
                                "summary": {"type": "string"},
                                "evidence": {"type": "string"},
                                "exploit_scenario": {"type": "string"},
                                "confidence": {"type": "number"},
                                "recommended_fix": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    )

    prompt = (
        f"Analyze this Python codebase for **{vulnerability_class}** vulnerabilities.\n\n"
        f"Workflow:\n"
        f"1. Use Glob(\"**/*.py\") to discover all files\n"
        f"2. Use Grep to search for vulnerability-specific patterns\n"
        f"3. Use Read to examine suspicious files and trace data flows\n\n"
        f"Report up to {request.max_findings_per_detector} confirmed, exploitable findings. "
        f"Return an empty findings array if nothing is found."
    )

    raw = await _run_agent(f"detector:{vulnerability_class}", prompt, options)

    findings: list[CandidateFinding] = []
    for idx, f in enumerate(
        raw.get("findings", [])[: request.max_findings_per_detector], start=1
    ):
        findings.append(
            CandidateFinding.model_validate(
                {**f, "finding_id": f"{vulnerability_class}-{idx}", "vulnerability_class": vulnerability_class}
            )
        )

    return DetectorResult(
        vulnerability_class=vulnerability_class,
        notes=raw.get("notes", "Agent produced no structured output"),
        findings=findings,
    )


async def _manager_agent(
    finding: CandidateFinding,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> ManagerReview:
    from claude_agent_sdk import ClaudeAgentOptions

    relevant = _snippets_for_finding(finding, snippets)
    tmpdir = _write_snippets_to_tmpdir(relevant)

    options = ClaudeAgentOptions(
        system_prompt=MANAGER_SKILL,
        allowed_tools=["Read", "Grep"],
        permission_mode="bypassPermissions",
        cwd=tmpdir,
        max_turns=15,
        model=request.model,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "required": ["finding_id", "decision", "rationale", "requested_followups"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["approved", "rejected", "needs_human"]},
                    "rationale": {"type": "string"},
                    "requested_followups": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    )

    prompt = (
        f"Review this vulnerability finding:\n\n"
        f"```json\n{json.dumps(finding.model_dump(), indent=2)}\n```\n\n"
        f"The relevant source files are in your working directory. "
        f"Use Read to examine the vulnerable code and Grep to search for "
        f"any mitigations (middleware, decorators, authorization checks) "
        f"that might have been missed by the detector.\n\n"
        f"Output your review decision as JSON."
    )

    raw = await _run_agent(f"manager:{finding.finding_id}", prompt, options)

    if not raw:
        return ManagerReview(
            finding_id=finding.finding_id,
            decision="needs_human",
            rationale="Manager agent produced no structured output",
            requested_followups=["Manual analyst review required"],
        )

    raw.setdefault("finding_id", finding.finding_id)
    review = ManagerReview.model_validate(raw)
    if review.finding_id != finding.finding_id:
        review = review.model_copy(update={"finding_id": finding.finding_id})
    return review


async def _validator_agent(
    finding: CandidateFinding,
    manager_review: ManagerReview,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> ValidationResult:
    from claude_agent_sdk import ClaudeAgentOptions

    relevant = _snippets_for_finding(finding, snippets)
    tmpdir = _write_snippets_to_tmpdir(relevant)

    options = ClaudeAgentOptions(
        system_prompt=VALIDATOR_SKILL,
        allowed_tools=["Read", "Grep", "Glob"],
        permission_mode="bypassPermissions",
        cwd=tmpdir,
        max_turns=20,
        model=request.model,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "required": ["finding_id", "status", "rationale", "test_file_path", "test_code", "run_command"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["confirmed", "false_positive", "needs_human"]},
                    "rationale": {"type": "string"},
                    "test_file_path": {"type": "string"},
                    "test_code": {"type": "string"},
                    "run_command": {"type": "string"},
                },
            },
        },
    )

    prompt = (
        f"Write a validation test for this approved finding:\n\n"
        f"**Finding:**\n```json\n{json.dumps(finding.model_dump(), indent=2)}\n```\n\n"
        f"**Manager review:**\n```json\n{json.dumps(manager_review.model_dump(), indent=2)}\n```\n\n"
        f"The relevant source files are in your working directory. "
        f"Use Read to understand the code structure and Glob to discover existing test files.\n\n"
        f"Write an integration test that FAILS without the fix and PASSES after.\n"
        f"Default test command: `{request.test_command}`\n\n"
        f"Output your validation result as JSON."
    )

    raw = await _run_agent(f"validator:{finding.finding_id}", prompt, options)

    if not raw:
        return ValidationResult(
            finding_id=finding.finding_id,
            status="needs_human",
            rationale="Validator agent produced no structured output",
            test_file_path=f"tests/security/test_{finding.finding_id}.py",
            test_code="",
            run_command=request.test_command,
        )

    raw.setdefault("finding_id", finding.finding_id)
    raw.setdefault("run_command", request.test_command)
    validation = ValidationResult.model_validate(raw)
    if validation.finding_id != finding.finding_id:
        validation = validation.model_copy(update={"finding_id": finding.finding_id})
    return validation


async def _fixer_agent(
    finding: CandidateFinding,
    validation: ValidationResult,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> FixProposal:
    from claude_agent_sdk import ClaudeAgentOptions

    relevant = _snippets_for_finding(finding, snippets)
    tmpdir = _write_snippets_to_tmpdir(relevant)

    options = ClaudeAgentOptions(
        system_prompt=FIXER_SKILL,
        allowed_tools=["Read", "Grep"],
        permission_mode="bypassPermissions",
        cwd=tmpdir,
        max_turns=20,
        model=request.model,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "required": ["finding_id", "status", "patch_diff", "files_touched", "pr_title", "pr_body", "notes"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["generated", "skipped", "failed"]},
                    "patch_diff": {"type": "string"},
                    "files_touched": {"type": "array", "items": {"type": "string"}},
                    "pr_title": {"type": "string"},
                    "pr_body": {"type": "string"},
                    "notes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    )

    prompt = (
        f"Generate a minimal patch for this confirmed vulnerability:\n\n"
        f"**Finding:**\n```json\n{json.dumps(finding.model_dump(), indent=2)}\n```\n\n"
        f"**Validation:**\n```json\n{json.dumps(validation.model_dump(), indent=2)}\n```\n\n"
        f"The vulnerable source files are in your working directory. "
        f"Use Read to examine the exact code at {finding.file_path} lines "
        f"{finding.line_start}–{finding.line_end} and any surrounding context.\n\n"
        f"Keep the diff minimal — only change what is necessary.\n\n"
        f"Output your patch proposal as JSON."
    )

    raw = await _run_agent(f"fixer:{finding.finding_id}", prompt, options)

    if not raw:
        return FixProposal(
            finding_id=finding.finding_id,
            status="failed",
            notes=["Fixer agent produced no structured output"],
        )

    raw.setdefault("finding_id", finding.finding_id)
    proposal = FixProposal.model_validate(raw)
    if proposal.finding_id != finding.finding_id:
        proposal = proposal.model_copy(update={"finding_id": finding.finding_id})
    return proposal


# ---------------------------------------------------------------------------
# Tensorlake @functions — synchronous wrappers around the async agents
# ---------------------------------------------------------------------------

@function(image=security_image, timeout=300)
def build_code_corpus(request: SecuritySweepRequest) -> list[FileSnippet]:
    """Clone/scan repo and return all matching file snippets."""
    if request.repo_url:
        tmp = tempfile.mkdtemp()
        clone_cmd = ["git", "clone", "--depth", "1"]
        if request.repo_branch:
            clone_cmd += ["--branch", request.repo_branch]
        clone_cmd += [request.repo_url, tmp]

        safe_url = request.repo_url.split("@")[-1] if "@" in request.repo_url else request.repo_url
        print(
            f"[build_code_corpus] Cloning {safe_url}"
            + (f" (branch: {request.repo_branch})" if request.repo_branch else " (default branch)")
        )

        try:
            result = subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
            if result.stderr:
                print(f"[build_code_corpus] git clone output: {result.stderr.strip()}")
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
        snippets.append(
            FileSnippet(
                path=relative,
                content=stripped[: request.max_chars_per_file],
                line_count=content.count("\n") + 1,
            )
        )

    snippets.sort(key=lambda s: s.path)
    return snippets


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
    snippets: list[FileSnippet],
) -> DetectorResult:
    """Detector agent: iteratively explores the codebase with Read/Grep/Glob.

    The agent uses its specialized vulnerability skill prompt to guide its
    investigation, then calls submit_findings to return structured results.
    Parallelized: one container per vulnerability class.
    """
    try:
        return asyncio.run(
            _detector_agent(vulnerability_class, request, snippets)
        )
    except Exception as exc:
        return DetectorResult(
            vulnerability_class=vulnerability_class,
            notes=f"detector_error: {exc}",
            findings=[],
        )


@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=600,
    max_containers=12,
)
def run_manager_review(
    finding: CandidateFinding,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> ManagerReview:
    """Manager agent: adversarially reviews each finding, rejecting ~40% as false positives.

    Uses Read/Grep to look for mitigations the detector may have missed (middleware,
    decorators, authorization checks up the call stack).
    Parallelized: one container per finding.
    """
    try:
        return asyncio.run(
            _manager_agent(finding, request, snippets)
        )
    except Exception as exc:
        return ManagerReview(
            finding_id=finding.finding_id,
            decision="needs_human",
            rationale=f"manager_error: {exc}",
            requested_followups=["Manual analyst review required"],
        )


@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=600,
    max_containers=8,
)
def run_validator(
    finding: CandidateFinding,
    manager_review: ManagerReview,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> ValidationResult:
    """Validator agent: writes an integration test that fails before fix, passes after.

    Uses Glob to discover existing test patterns, then Read to understand the
    vulnerable code path, then writes a targeted test.
    Parallelized: one container per approved finding.
    """
    try:
        return asyncio.run(
            _validator_agent(finding, manager_review, request, snippets)
        )
    except Exception as exc:
        return ValidationResult(
            finding_id=finding.finding_id,
            status="needs_human",
            rationale=f"validator_error: {exc}",
            test_file_path=f"tests/security/test_{finding.finding_id}.py",
            test_code="",
            run_command=request.test_command,
        )


@function(
    image=security_image,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=900,
    max_containers=4,
)
def run_fixer(
    finding: CandidateFinding,
    validation: ValidationResult,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> FixProposal:
    """Fixer agent: generates a minimal patch using test-driven development.

    Reads the vulnerable code, writes a diff that makes the validator test pass,
    and produces a PR title + body for human review.
    Parallelized: one container per confirmed finding.
    """
    if validation.status != "confirmed":
        return FixProposal(
            finding_id=finding.finding_id,
            status="skipped",
            notes=["Fix generation skipped because validator did not confirm the finding."],
        )
    try:
        return asyncio.run(
            _fixer_agent(finding, validation, request, snippets)
        )
    except Exception as exc:
        return FixProposal(
            finding_id=finding.finding_id,
            status="failed",
            notes=[f"fixer_error: {exc}"],
        )


# ---------------------------------------------------------------------------
# Coordinator — orchestrates all stages with Tensorlake parallel futures
# ---------------------------------------------------------------------------

def _build_summary_markdown(
    request: SecuritySweepRequest,
    detector_results: list[DetectorResult],
    lifecycles: list[FindingLifecycle],
) -> str:
    approved = sum(
        1 for item in lifecycles
        if item.manager_review and item.manager_review.decision == "approved"
    )
    confirmed = sum(
        1 for item in lifecycles
        if item.validation and item.validation.status == "confirmed"
    )
    fixes = sum(
        1 for item in lifecycles
        if item.fix and item.fix.status == "generated"
    )

    lines = [
        "# Security Autopatch Sweep",
        "",
        f"- Repository: `{request.repo_url or request.repo_path}`",
        f"- Branch: `{request.repo_branch or 'default'}`",
        f"- Detectors run: `{len(detector_results)}`",
        f"- Findings detected: `{len(lifecycles)}`",
        f"- Findings approved by manager: `{approved}`",
        f"- Findings confirmed by validator: `{confirmed}`",
        f"- Fix proposals generated: `{fixes}`",
        "",
        "## Detector Notes",
    ]

    for dr in detector_results:
        lines.append(f"- `{dr.vulnerability_class}`: {len(dr.findings)} findings. {dr.notes}")

    lines += ["", "## Finding Details"]

    if not lifecycles:
        lines.append("No findings were detected.")
        return "\n".join(lines)

    ordered = sorted(
        lifecycles,
        key=lambda item: (
            _severity_rank(item.candidate.severity),
            item.candidate.vulnerability_class,
            item.candidate.finding_id,
        ),
    )

    for item in ordered:
        c = item.candidate
        lines.extend([
            "", "---", "",
            f"### {c.finding_id} — [{c.severity.upper()}] `{c.vulnerability_class}`",
            "",
            f"**Location:** `{c.file_path}:{c.line_start}` &nbsp;|&nbsp; "
            f"**Endpoint:** `{c.endpoint}` &nbsp;|&nbsp; "
            f"**Confidence:** `{c.confidence:.0%}`",
            "", f"**Summary:** {c.summary}", "",
            "**Evidence:**", "```", c.evidence.strip(), "```", "",
            f"**Exploit scenario:** {c.exploit_scenario}", "",
            f"**Recommended fix:** {c.recommended_fix}",
        ])

        if item.manager_review:
            lines += [
                "",
                f"**Manager review:** `{item.manager_review.decision}` — {item.manager_review.rationale}",
            ]
        if item.validation:
            lines += [
                "",
                f"**Validation:** `{item.validation.status}` — {item.validation.rationale}",
            ]
            if item.validation.test_file_path:
                lines.append(f"  - Suggested test file: `{item.validation.test_file_path}`")
        if item.fix:
            lines += ["", f"**Fix proposal:** `{item.fix.status}`"]
            if item.fix.pr_title:
                lines += ["", f"**PR title:** {item.fix.pr_title}"]
            if item.fix.files_touched:
                lines += ["", f"**Files touched:** {', '.join(f'`{f}`' for f in item.fix.files_touched)}"]
            if item.fix.pr_body:
                lines += ["", "**PR description:**", "", item.fix.pr_body]
            if item.fix.patch_diff:
                lines += ["", "**Patch diff:**", "```diff", item.fix.patch_diff.strip(), "```"]
            if item.fix.notes:
                lines += ["", "**Fixer notes:**"]
                for note in item.fix.notes:
                    lines.append(f"- {note}")

    return "\n".join(lines)


@application(
    tags={
        "pattern": "coordinator-detector-manager-validator-fixer",
        "domain": "security",
        "inspired_by": "ramp-100-vulns-blog",
        "agents": "claude-agent-sdk-multi-turn",
    },
    retries=Retries(max_retries=1),
)
@function(image=security_image, secrets=["ANTHROPIC_API_KEY"], timeout=3600)
def security_autopatch(request: SecuritySweepRequest) -> SecuritySweepReport:
    """Coordinator: orchestrates the full 5-stage security sweep pipeline.

    Uses Tensorlake futures for parallel fan-out at every stage.
    Each sub-agent is a multi-turn Claude Agent SDK session that can
    iteratively explore the codebase before reporting results.
    """
    ctx = RequestContext.get()

    # ── Stage 1: Corpus ────────────────────────────────────────────────────
    ctx.progress.update(1, 6, "Collecting code corpus", {"repo_path": request.repo_path})
    snippets = build_code_corpus(request)

    if not snippets:
        return SecuritySweepReport(
            repo_path=request.repo_url or request.repo_path,
            repo_branch=request.repo_branch,
            files_scanned=0,
            detectors_run=len(request.vulnerability_classes),
            findings_detected=0,
            findings_approved=0,
            findings_confirmed=0,
            fixes_generated=0,
            detector_results=[],
            findings=[],
            summary_markdown="# Security Autopatch Sweep\n\nNo matching files were found for scanning.",
        )

    # ── Stage 2: Detector agents (parallel) ───────────────────────────────
    ctx.progress.update(
        2, 6,
        f"Running {len(request.vulnerability_classes)} detector agents in parallel",
        {"files_scanned": str(len(snippets))},
    )

    detector_futures: list[Future] = [
        run_detector.awaitable(vuln_class, request, snippets).run()
        for vuln_class in request.vulnerability_classes
    ]
    Future.wait(detector_futures, return_when=RETURN_WHEN.ALL_COMPLETED)

    detector_results: list[DetectorResult] = []
    for idx, future in enumerate(detector_futures):
        vuln_class = request.vulnerability_classes[idx]
        try:
            detector_results.append(future.result())
        except Exception as exc:
            detector_results.append(
                DetectorResult(
                    vulnerability_class=vuln_class,
                    notes=f"detector_future_error: {exc}",
                    findings=[],
                )
            )

    candidates = [f for result in detector_results for f in result.findings]
    lifecycle_by_id: dict[str, FindingLifecycle] = {
        f.finding_id: FindingLifecycle(candidate=f) for f in candidates
    }
    approved_findings: list[CandidateFinding] = []

    # ── Stage 3: Manager agents (parallel per finding) ─────────────────────
    if candidates:
        ctx.progress.update(3, 6, f"Manager triage: {len(candidates)} findings", {})

        manager_futures: dict[str, Future] = {
            f.finding_id: run_manager_review.awaitable(f, request, snippets).run()
            for f in candidates
        }
        Future.wait(manager_futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        for finding_id, future in manager_futures.items():
            try:
                review = future.result()
            except Exception as exc:
                review = ManagerReview(
                    finding_id=finding_id,
                    decision="needs_human",
                    rationale=f"manager_future_error: {exc}",
                    requested_followups=["Manual analyst review required"],
                )
            lifecycle = lifecycle_by_id[finding_id]
            lifecycle.manager_review = review
            if review.decision == "approved":
                approved_findings.append(lifecycle.candidate)

    confirmed_findings: list[CandidateFinding] = []

    # ── Stage 4: Validator agents (parallel per approved finding) ──────────
    if request.run_validation and approved_findings:
        ctx.progress.update(
            4, 6, f"Validator stage: {len(approved_findings)} approved findings", {}
        )

        validator_futures: dict[str, Future] = {}
        for finding in approved_findings:
            mgr = lifecycle_by_id[finding.finding_id].manager_review
            if mgr is None:
                continue
            validator_futures[finding.finding_id] = run_validator.awaitable(
                finding, mgr, request, snippets
            ).run()

        Future.wait(validator_futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        for finding_id, future in validator_futures.items():
            try:
                validation = future.result()
            except Exception as exc:
                validation = ValidationResult(
                    finding_id=finding_id,
                    status="needs_human",
                    rationale=f"validator_future_error: {exc}",
                    test_file_path=f"tests/security/test_{finding_id}.py",
                    test_code="",
                    run_command=request.test_command,
                )
            lifecycle = lifecycle_by_id[finding_id]
            lifecycle.validation = validation
            if validation.status == "confirmed":
                confirmed_findings.append(lifecycle.candidate)

    elif approved_findings:
        for finding in approved_findings:
            lifecycle_by_id[finding.finding_id].validation = ValidationResult(
                finding_id=finding.finding_id,
                status="needs_human",
                rationale="Validation stage disabled by request configuration",
                run_command=request.test_command,
            )

    fixes_generated = 0

    # ── Stage 5: Fixer agents (parallel per confirmed finding) ─────────────
    if request.generate_fixes and confirmed_findings:
        ctx.progress.update(
            5, 6, f"Fixer stage: {len(confirmed_findings)} confirmed findings", {}
        )

        fixer_futures: dict[str, Future] = {}
        for finding in confirmed_findings:
            val = lifecycle_by_id[finding.finding_id].validation
            if val is None:
                continue
            fixer_futures[finding.finding_id] = run_fixer.awaitable(
                finding, val, request, snippets
            ).run()

        Future.wait(fixer_futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

        for finding_id, future in fixer_futures.items():
            try:
                proposal = future.result()
            except Exception as exc:
                proposal = FixProposal(
                    finding_id=finding_id,
                    status="failed",
                    notes=[f"fixer_future_error: {exc}"],
                )
            lifecycle_by_id[finding_id].fix = proposal
            if proposal.status == "generated":
                fixes_generated += 1

    elif confirmed_findings:
        for finding in confirmed_findings:
            lifecycle_by_id[finding.finding_id].fix = FixProposal(
                finding_id=finding.finding_id,
                status="skipped",
                notes=["Fix generation disabled by request configuration"],
            )

    # ── Stage 6: Compile report ────────────────────────────────────────────
    lifecycle_items = list(lifecycle_by_id.values())
    summary = _build_summary_markdown(request, detector_results, lifecycle_items)

    approved_count = sum(
        1 for item in lifecycle_items
        if item.manager_review and item.manager_review.decision == "approved"
    )
    confirmed_count = sum(
        1 for item in lifecycle_items
        if item.validation and item.validation.status == "confirmed"
    )

    ctx.progress.update(
        6, 6,
        "Security sweep complete",
        {
            "detected": str(len(candidates)),
            "approved": str(approved_count),
            "confirmed": str(confirmed_count),
            "fixes_generated": str(fixes_generated),
        },
    )

    return SecuritySweepReport(
        repo_path=request.repo_url or request.repo_path,
        repo_branch=request.repo_branch,
        files_scanned=len(snippets),
        detectors_run=len(request.vulnerability_classes),
        findings_detected=len(candidates),
        findings_approved=approved_count,
        findings_confirmed=confirmed_count,
        fixes_generated=fixes_generated,
        detector_results=detector_results,
        findings=lifecycle_items,
        summary_markdown=summary,
    )


if __name__ == "__main__":
    def _parse_list_env(name: str, default: list[str]) -> list[str]:
        raw = os.getenv(name, "")
        return [item.strip() for item in raw.split(",") if item.strip()] if raw.strip() else default

    sample_request = SecuritySweepRequest(
        repo_url=os.getenv("SCAN_REPO_URL", ""),
        repo_branch=os.getenv("SCAN_REPO_BRANCH", ""),
        repo_path=os.getenv("SCAN_REPO_PATH", "."),
        include_globs=_parse_list_env("SCAN_INCLUDE_GLOBS", ["**/*.py"]),
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
