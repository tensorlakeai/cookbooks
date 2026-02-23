import uuid
from typing import Literal

from pydantic import BaseModel, Field

ManagerDecision = Literal["approved", "rejected", "needs_human"]
ValidationStatus = Literal["confirmed", "false_positive", "needs_human"]
FixStatus = Literal["generated", "skipped", "failed"]


class FileSnippet(BaseModel):
    path: str
    content: str
    line_count: int = 0


class CandidateFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    vulnerability_class: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    endpoint: str
    file_path: str
    line_start: int = Field(default=1, ge=1)
    line_end: int = Field(default=1, ge=1)
    summary: str
    evidence: str
    exploit_scenario: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_fix: str


class DetectorResult(BaseModel):
    vulnerability_class: str
    notes: str = ""
    findings: list[CandidateFinding] = Field(default_factory=list)


class ManagerReview(BaseModel):
    finding_id: str
    decision: ManagerDecision
    rationale: str
    requested_followups: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    finding_id: str
    status: ValidationStatus
    rationale: str
    test_file_path: str = ""
    test_code: str = ""
    run_command: str = ""


class FixProposal(BaseModel):
    finding_id: str
    status: FixStatus
    patch_diff: str = ""
    files_touched: list[str] = Field(default_factory=list)
    pr_title: str = ""
    pr_body: str = ""
    notes: list[str] = Field(default_factory=list)


class FindingLifecycle(BaseModel):
    candidate: CandidateFinding
    manager_review: ManagerReview | None = None
    validation: ValidationResult | None = None
    fix: FixProposal | None = None


class SecuritySweepRequest(BaseModel):
    repo_path: str = "."
    repo_url: str = ""
    repo_branch: str = ""  # Empty = use the remote's default branch.
    include_globs: list[str] = Field(default_factory=lambda: ["**/*.py"])
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.venv/**",
            "**/venv/**",
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
            "**/migrations/**",
            "**/__pycache__/**",
        ]
    )
    file_extensions: list[str] = Field(default_factory=lambda: [".py"])
    vulnerability_classes: list[str] = Field(
        default_factory=lambda: ["idor", "sql_injection", "ssrf", "command_injection"]
    )
    max_files_per_detector: int = Field(default=20, ge=1, le=200)
    max_chars_per_file: int = Field(default=8000, ge=200, le=200000)
    max_findings_per_detector: int = Field(default=5, ge=1, le=20)
    model: str = "gpt-4.1-mini"
    test_command: str = "pytest -q"
    run_validation: bool = True
    generate_fixes: bool = True


class SecuritySweepReport(BaseModel):
    repo_path: str
    files_scanned: int
    detectors_run: int
    findings_detected: int
    findings_approved: int
    findings_confirmed: int
    fixes_generated: int
    detector_results: list[DetectorResult] = Field(default_factory=list)
    findings: list[FindingLifecycle] = Field(default_factory=list)
    summary_markdown: str
