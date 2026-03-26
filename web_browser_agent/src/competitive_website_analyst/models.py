from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal
from urllib.parse import urlsplit


BrowserStatus = Literal["success", "failed"]


def _require_https_url(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError("url must use https")
    parts = urlsplit(value)
    if not parts.netloc:
        raise ValueError("url must include a host")
    return value


class ModelMixin:
    @classmethod
    def model_validate(cls, value: Any):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"{cls.__name__} must be created from a dict")
        return cls(**value)

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return asdict(self)

    def model_dump_json(self) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"))

    def model_copy(self, update: dict[str, Any] | None = None):
        return replace(self, **(update or {}))


@dataclass(slots=True)
class Company(ModelMixin):
    id: str
    name: str
    url: str
    short_description: str

    def __post_init__(self) -> None:
        self.url = _require_https_url(self.url)
        self.id = self.id.strip()
        self.name = self.name.strip()
        self.short_description = self.short_description.strip()
        if not self.id or not self.name or not self.short_description:
            raise ValueError("company fields are required")


@dataclass(slots=True)
class BrowserMetadata(ModelMixin):
    title: str = ""
    meta_description: str = ""
    h1_hero_text: str = ""
    visible_cta_labels: list[str] = field(default_factory=list)
    nav_items: list[str] = field(default_factory=list)
    og_image_url: str = ""
    page_load_time_ms: int = 0


@dataclass(slots=True)
class BrowserArtifact(ModelMixin):
    company: Company | dict[str, Any]
    run_id: str
    status: BrowserStatus
    failure_reason: str | None = None
    failure_stage: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    metadata_path: str | None = None
    metadata: BrowserMetadata | dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.company = Company.model_validate(self.company)
        if self.metadata is not None:
            self.metadata = BrowserMetadata.model_validate(self.metadata)
        if self.status not in {"success", "failed"}:
            raise ValueError("invalid browser artifact status")
        if self.status == "success":
            missing = [
                name
                for name, value in {
                    "screenshot_path": self.screenshot_path,
                    "metadata_path": self.metadata_path,
                    "metadata": self.metadata,
                }.items()
                if value is None
            ]
            if missing:
                raise ValueError(f"successful artifact missing fields: {', '.join(missing)}")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed artifact requires failure_reason")


@dataclass(slots=True)
class ScoreBreakdown(ModelMixin):
    positioning_clarity: int
    target_audience_clarity: int
    cta_strength: int
    visual_polish: int
    trust_credibility_signals: int
    product_specificity: int
    technical_depth: int

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            value = int(value)
            if not 1 <= value <= 10:
                raise ValueError(f"{name} must be between 1 and 10")
            setattr(self, name, value)


@dataclass(slots=True)
class Scorecard(ModelMixin):
    company: str
    url: str
    run_id: str
    scores: ScoreBreakdown | dict[str, Any]
    overall_score: float
    target_audience_guess: str = ""
    primary_cta: str = ""
    hero_message: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    one_sentence_summary: str = ""

    def __post_init__(self) -> None:
        self.url = _require_https_url(self.url)
        self.scores = ScoreBreakdown.model_validate(self.scores)
        self.strengths = [item.strip() for item in self.strengths[:5] if item.strip()]
        self.weaknesses = [item.strip() for item in self.weaknesses[:5] if item.strip()]
        self.overall_score = float(self.overall_score)


@dataclass(slots=True)
class FailureRecord(ModelMixin):
    company: str
    reason: str


@dataclass(slots=True)
class ReportBundle(ModelMixin):
    domain: str
    requested_count: int
    discovered_count: int
    successful_count: int
    failed_count: int
    failures: list[FailureRecord | dict[str, Any]] = field(default_factory=list)
    scorecards: list[Scorecard | dict[str, Any]] = field(default_factory=list)
    markdown_report: str = ""
    summary_csv: str = ""

    def __post_init__(self) -> None:
        self.failures = [FailureRecord.model_validate(item) for item in self.failures]
        self.scorecards = [Scorecard.model_validate(item) for item in self.scorecards]
