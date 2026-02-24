import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from openai import OpenAI

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
    FIXER_PROMPT,
    KEYWORDS_BY_CLASS,
    MANAGER_PROMPT,
    ROUTE_HINTS,
    VALIDATOR_PROMPT,
    build_detector_prompt,
)


security_image = (
    Image(name="security-autopatch")
    .run("apt-get update && apt-get install -y git")
    .run("pip install openai pydantic")
)


def _resolve_repo_path(repo_path: str) -> Path:
    resolved = Path(repo_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {resolved}")
    return resolved


def _match_glob(path: str, pattern: str) -> bool:
    """Match path against a glob pattern.

    Both fnmatch and Path.match require at least one directory separator for
    patterns like ``**/*.py``, so root-level files are never matched by those
    helpers alone.  When the pattern starts with ``**/`` we therefore also
    test the path against the tail of the pattern (the part after ``**/``).
    """
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(path, pattern[3:])
    return False


def _matches_globs(path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    include_ok = True if not include_globs else any(
        _match_glob(path, pattern) for pattern in include_globs
    )
    if not include_ok:
        return False
    return not any(_match_glob(path, pattern) for pattern in exclude_globs)


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        chunks = text.split("```")
        if len(chunks) >= 3:
            text = chunks[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        raise ValueError("LLM response did not include a JSON object")
    return json.loads(text[start : end + 1])


def _call_llm_json(system_prompt: str, payload: dict, model: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it as a Tensorlake secret for this application."
        )

    client = OpenAI(api_key=api_key, max_retries=0)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    return _parse_json_object(content)


def _score_snippet(snippet: FileSnippet, vulnerability_class: str) -> int:
    keywords = KEYWORDS_BY_CLASS.get(vulnerability_class, [])
    content = snippet.content.lower()
    path = snippet.path.lower()

    score = 0
    for keyword in keywords:
        score += content.count(keyword.lower()) * 2

    for hint in ROUTE_HINTS:
        if hint in content:
            score += 2

    if any(token in path for token in ("route", "api", "handler", "controller", "endpoint")):
        score += 2

    return score


def _select_snippets_for_detector(
    snippets: list[FileSnippet], vulnerability_class: str, limit: int
) -> list[FileSnippet]:
    ranked = sorted(
        snippets,
        key=lambda snippet: (_score_snippet(snippet, vulnerability_class), snippet.path),
        reverse=True,
    )

    selected = ranked[:limit]
    if not selected:
        return []

    # If everything scored zero, still scan deterministically to avoid empty context.
    if _score_snippet(selected[0], vulnerability_class) == 0:
        return sorted(snippets, key=lambda snippet: snippet.path)[:limit]

    return selected


def _snippets_for_finding(
    finding: CandidateFinding, snippets: list[FileSnippet], limit: int = 6
) -> list[FileSnippet]:
    exact = [snippet for snippet in snippets if snippet.path == finding.file_path]
    if exact:
        return exact[:limit]

    # Fallback: use vulnerability-specific ranking if exact path was not included in corpus.
    return _select_snippets_for_detector(snippets, finding.vulnerability_class, limit)


def _severity_rank(severity: str) -> int:
    ranks = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return ranks.get(severity.lower(), 4)


def _build_summary_markdown(
    request: SecuritySweepRequest,
    detector_results: list[DetectorResult],
    lifecycles: list[FindingLifecycle],
) -> str:
    approved = sum(
        1
        for item in lifecycles
        if item.manager_review and item.manager_review.decision == "approved"
    )
    confirmed = sum(
        1
        for item in lifecycles
        if item.validation and item.validation.status == "confirmed"
    )
    fixes = sum(
        1
        for item in lifecycles
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

    for detector in detector_results:
        lines.append(
            f"- `{detector.vulnerability_class}`: {len(detector.findings)} findings. {detector.notes}"
        )

    lines.append("")
    lines.append("## Finding Details")

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
        candidate = item.candidate
        lines.extend(
            [
                "",
                f"### {candidate.finding_id} [{candidate.severity.upper()}] {candidate.vulnerability_class}",
                f"- Endpoint: `{candidate.endpoint}`",
                f"- File: `{candidate.file_path}:{candidate.line_start}`",
                f"- Confidence: `{candidate.confidence:.2f}`",
                f"- Summary: {candidate.summary}",
                f"- Evidence: {candidate.evidence}",
                f"- Exploit scenario: {candidate.exploit_scenario}",
                f"- Recommended fix: {candidate.recommended_fix}",
            ]
        )

        if item.manager_review:
            lines.append(
                f"- Manager decision: `{item.manager_review.decision}` ({item.manager_review.rationale})"
            )

        if item.validation:
            lines.append(
                f"- Validation status: `{item.validation.status}` ({item.validation.rationale})"
            )
            if item.validation.test_file_path:
                lines.append(f"- Suggested test file: `{item.validation.test_file_path}`")

        if item.fix:
            lines.append(f"- Fix proposal status: `{item.fix.status}`")
            if item.fix.pr_title:
                lines.append(f"- PR title: {item.fix.pr_title}")

    return "\n".join(lines)


@function(image=security_image, timeout=300)
def build_code_corpus(request: SecuritySweepRequest) -> list[FileSnippet]:
    if request.repo_url:
        tmp = tempfile.mkdtemp()
        clone_cmd = ["git", "clone", "--depth", "1"]
        if request.repo_branch:
            clone_cmd += ["--branch", request.repo_branch]
        clone_cmd += [request.repo_url, tmp]

        safe_url = request.repo_url.split("@")[-1] if "@" in request.repo_url else request.repo_url
        print(f"[build_code_corpus] Cloning {safe_url}" + (f" (branch: {request.repo_branch})" if request.repo_branch else " (default branch)"))

        try:
            result = subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
            if result.stderr:
                print(f"[build_code_corpus] git clone output: {result.stderr.strip()}")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "(no output)"
            print(f"[build_code_corpus] ERROR: git clone failed for {safe_url}")
            print(f"[build_code_corpus] git stderr: {stderr}")
            raise RuntimeError(
                f"Failed to clone repository '{safe_url}': {stderr}"
            ) from exc

        repo = Path(tmp)
    else:
        repo = _resolve_repo_path(request.repo_path)
    extensions = {
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        for extension in request.file_extensions
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

    snippets.sort(key=lambda snippet: snippet.path)
    return snippets


@function(
    image=security_image,
    secrets=["OPENAI_API_KEY"],
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
    selected = _select_snippets_for_detector(
        snippets=snippets,
        vulnerability_class=vulnerability_class,
        limit=request.max_files_per_detector,
    )

    payload = {
        "vulnerability_class": vulnerability_class,
        "max_findings": request.max_findings_per_detector,
        "snippets": [snippet.model_dump() for snippet in selected],
    }

    try:
        raw = _call_llm_json(
            system_prompt=build_detector_prompt(vulnerability_class),
            payload=payload,
            model=request.model,
        )
        parsed = DetectorResult.model_validate(raw)
    except Exception as exc:
        return DetectorResult(
            vulnerability_class=vulnerability_class,
            notes=f"detector_error: {exc}",
            findings=[],
        )

    findings: list[CandidateFinding] = []
    for idx, finding in enumerate(parsed.findings[: request.max_findings_per_detector], start=1):
        finding_id = finding.finding_id or f"{vulnerability_class}-{idx}"
        findings.append(
            finding.model_copy(
                update={
                    "finding_id": finding_id,
                    "vulnerability_class": vulnerability_class,
                }
            )
        )

    return DetectorResult(
        vulnerability_class=vulnerability_class,
        notes=parsed.notes,
        findings=findings,
    )


@function(
    image=security_image,
    secrets=["OPENAI_API_KEY"],
    retries=Retries(max_retries=1),
    timeout=600,
    max_containers=12,
)
def run_manager_review(
    finding: CandidateFinding,
    request: SecuritySweepRequest,
    snippets: list[FileSnippet],
) -> ManagerReview:
    payload = {
        "finding": finding.model_dump(),
        "relevant_snippets": [
            snippet.model_dump()
            for snippet in _snippets_for_finding(finding=finding, snippets=snippets)
        ],
    }

    try:
        raw = _call_llm_json(
            system_prompt=MANAGER_PROMPT,
            payload=payload,
            model=request.model,
        )
        review = ManagerReview.model_validate(raw)
        if review.finding_id != finding.finding_id:
            review = review.model_copy(update={"finding_id": finding.finding_id})
        return review
    except Exception as exc:
        return ManagerReview(
            finding_id=finding.finding_id,
            decision="needs_human",
            rationale=f"manager_error: {exc}",
            requested_followups=["Manual analyst review required"],
        )


@function(
    image=security_image,
    secrets=["OPENAI_API_KEY"],
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
    payload = {
        "finding": finding.model_dump(),
        "manager_review": manager_review.model_dump(),
        "relevant_snippets": [
            snippet.model_dump()
            for snippet in _snippets_for_finding(finding=finding, snippets=snippets)
        ],
        "default_test_command": request.test_command,
    }

    try:
        raw = _call_llm_json(
            system_prompt=VALIDATOR_PROMPT,
            payload=payload,
            model=request.model,
        )
        validation = ValidationResult.model_validate(raw)
        if validation.finding_id != finding.finding_id:
            validation = validation.model_copy(update={"finding_id": finding.finding_id})
        return validation
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
    secrets=["OPENAI_API_KEY"],
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
    if validation.status != "confirmed":
        return FixProposal(
            finding_id=finding.finding_id,
            status="skipped",
            notes=["Fix generation skipped because validator did not confirm the finding."],
        )

    payload = {
        "finding": finding.model_dump(),
        "validation": validation.model_dump(),
        "relevant_snippets": [
            snippet.model_dump()
            for snippet in _snippets_for_finding(finding=finding, snippets=snippets)
        ],
    }

    try:
        raw = _call_llm_json(
            system_prompt=FIXER_PROMPT,
            payload=payload,
            model=request.model,
        )
        proposal = FixProposal.model_validate(raw)
        if proposal.finding_id != finding.finding_id:
            proposal = proposal.model_copy(update={"finding_id": finding.finding_id})
        return proposal
    except Exception as exc:
        return FixProposal(
            finding_id=finding.finding_id,
            status="failed",
            notes=[f"fixer_error: {exc}"],
        )


@application(
    tags={
        "pattern": "detector-manager-validator-fixer",
        "domain": "security",
        "inspired_by": "ramp-100-vulns-blog",
    },
    retries=Retries(max_retries=1),
)
@function(image=security_image, secrets=["OPENAI_API_KEY"], timeout=3600)
def security_autopatch(request: SecuritySweepRequest) -> SecuritySweepReport:
    ctx = RequestContext.get()

    ctx.progress.update(1, 6, "Collecting code corpus", {"repo_path": request.repo_path})
    snippets = build_code_corpus(request)

    if not snippets:
        empty_report = SecuritySweepReport(
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
        return empty_report

    ctx.progress.update(
        2,
        6,
        f"Running detector agents ({len(request.vulnerability_classes)})",
        {"files_scanned": str(len(snippets))},
    )

    detector_futures: list[Future] = [
        run_detector.awaitable(vulnerability_class, request, snippets).run()
        for vulnerability_class in request.vulnerability_classes
    ]
    Future.wait(detector_futures, return_when=RETURN_WHEN.ALL_COMPLETED)

    detector_results: list[DetectorResult] = []
    for idx, future in enumerate(detector_futures):
        vulnerability_class = request.vulnerability_classes[idx]
        try:
            detector_results.append(future.result())
        except Exception as exc:
            detector_results.append(
                DetectorResult(
                    vulnerability_class=vulnerability_class,
                    notes=f"detector_future_error: {exc}",
                    findings=[],
                )
            )

    candidates = [finding for result in detector_results for finding in result.findings]
    lifecycle_by_id: dict[str, FindingLifecycle] = {
        finding.finding_id: FindingLifecycle(candidate=finding) for finding in candidates
    }

    approved_findings: list[CandidateFinding] = []

    if candidates:
        ctx.progress.update(3, 6, f"Manager triage for {len(candidates)} findings", {})
        manager_futures: dict[str, Future] = {
            finding.finding_id: run_manager_review.awaitable(finding, request, snippets).run()
            for finding in candidates
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

    if request.run_validation and approved_findings:
        ctx.progress.update(
            4,
            6,
            f"Validator stage for {len(approved_findings)} approved findings",
            {},
        )

        validator_futures: dict[str, Future] = {}
        for finding in approved_findings:
            manager_review = lifecycle_by_id[finding.finding_id].manager_review
            if manager_review is None:
                continue
            validator_futures[finding.finding_id] = run_validator.awaitable(
                finding,
                manager_review,
                request,
                snippets,
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

    if request.generate_fixes and confirmed_findings:
        ctx.progress.update(
            5,
            6,
            f"Fixer stage for {len(confirmed_findings)} confirmed findings",
            {},
        )

        fixer_futures: dict[str, Future] = {}
        for finding in confirmed_findings:
            validation = lifecycle_by_id[finding.finding_id].validation
            if validation is None:
                continue
            fixer_futures[finding.finding_id] = run_fixer.awaitable(
                finding,
                validation,
                request,
                snippets,
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

    lifecycle_items = list(lifecycle_by_id.values())
    summary = _build_summary_markdown(request, detector_results, lifecycle_items)

    approved_count = sum(
        1
        for item in lifecycle_items
        if item.manager_review and item.manager_review.decision == "approved"
    )
    confirmed_count = sum(
        1
        for item in lifecycle_items
        if item.validation and item.validation.status == "confirmed"
    )

    ctx.progress.update(
        6,
        6,
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
