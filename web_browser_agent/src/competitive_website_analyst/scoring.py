from __future__ import annotations

import csv
import io

from competitive_website_analyst.models import ReportBundle, ScoreBreakdown, Scorecard


WEIGHTS = {
    "positioning_clarity": 0.20,
    "target_audience_clarity": 0.15,
    "cta_strength": 0.15,
    "visual_polish": 0.15,
    "trust_credibility_signals": 0.10,
    "product_specificity": 0.15,
    "technical_depth": 0.10,
}


def compute_overall_score(scores: ScoreBreakdown) -> float:
    total = sum(getattr(scores, key) * weight for key, weight in WEIGHTS.items())
    return round(total, 2)


def sort_scorecards(scorecards: list[Scorecard]) -> list[Scorecard]:
    return sorted(scorecards, key=lambda card: card.overall_score, reverse=True)


def build_summary_csv(scorecards: list[Scorecard]) -> str:
    fieldnames = [
        "company",
        "url",
        "overall_score",
        "positioning_clarity",
        "target_audience_clarity",
        "cta_strength",
        "visual_polish",
        "trust_credibility_signals",
        "product_specificity",
        "technical_depth",
        "primary_cta",
        "target_audience_guess",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for card in scorecards:
        writer.writerow(
            {
                "company": card.company,
                "url": str(card.url),
                "overall_score": card.overall_score,
                "positioning_clarity": card.scores.positioning_clarity,
                "target_audience_clarity": card.scores.target_audience_clarity,
                "cta_strength": card.scores.cta_strength,
                "visual_polish": card.scores.visual_polish,
                "trust_credibility_signals": card.scores.trust_credibility_signals,
                "product_specificity": card.scores.product_specificity,
                "technical_depth": card.scores.technical_depth,
                "primary_cta": card.primary_cta,
                "target_audience_guess": card.target_audience_guess,
            }
        )
    return buffer.getvalue()


def build_empty_report_bundle(domain: str, requested_count: int, discovered_count: int, failures: list[dict]) -> ReportBundle:
    return ReportBundle(
        domain=domain,
        requested_count=requested_count,
        discovered_count=discovered_count,
        successful_count=0,
        failed_count=len(failures),
        failures=failures,
        scorecards=[],
        markdown_report="# Competitive Website Analysis\n\nNo sites were successfully analyzed.",
        summary_csv=build_summary_csv([]),
    )
