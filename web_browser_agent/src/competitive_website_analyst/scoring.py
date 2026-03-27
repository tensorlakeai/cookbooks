from __future__ import annotations

import base64
import csv
import io
from dataclasses import asdict

from competitive_website_analyst.models import FailureRecord, ReportBundle, ScoreBreakdown, Scorecard


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


def build_html_report(
    domain: str,
    scorecards: list[Scorecard],
    failures: list[FailureRecord],
    screenshots: dict[str, str],
    markdown_report: str,
) -> str:
    """Build a self-contained HTML report with embedded screenshots.

    Args:
        screenshots: mapping of company name -> base64-encoded PNG data
    """
    score_dimensions = [
        ("positioning_clarity", "Positioning"),
        ("target_audience_clarity", "Audience"),
        ("cta_strength", "CTA"),
        ("visual_polish", "Visual"),
        ("trust_credibility_signals", "Trust"),
        ("product_specificity", "Specificity"),
        ("technical_depth", "Technical"),
    ]

    # Build ranked table rows
    table_rows = ""
    for rank, card in enumerate(scorecards, 1):
        scores_td = "".join(
            f'<td class="score">{getattr(card.scores, dim)}</td>'
            for dim, _ in score_dimensions
        )
        table_rows += f"""<tr>
            <td>{rank}</td>
            <td><strong>{card.company}</strong></td>
            <td class="score overall">{card.overall_score:.2f}</td>
            {scores_td}
        </tr>\n"""

    # Build per-company sections
    company_sections = ""
    for card in scorecards:
        img_b64 = screenshots.get(card.company, "")
        img_html = (
            f'<img src="data:image/png;base64,{img_b64}" alt="{card.company} homepage">'
            if img_b64 else '<p class="no-screenshot">Screenshot not available</p>'
        )
        strengths_html = "".join(f"<li>{s}</li>" for s in card.strengths)
        weaknesses_html = "".join(f"<li>{s}</li>" for s in card.weaknesses)

        company_sections += f"""
        <div class="company-card">
            <h3>{card.company} <span class="score-badge">{card.overall_score:.2f}/10</span></h3>
            <p class="url"><a href="{card.url}">{card.url}</a></p>
            <p class="summary">{card.one_sentence_summary}</p>
            <div class="card-body">
                <div class="screenshot">{img_html}</div>
                <div class="details">
                    <p><strong>Target audience:</strong> {card.target_audience_guess}</p>
                    <p><strong>Primary CTA:</strong> {card.primary_cta}</p>
                    <p><strong>Hero message:</strong> {card.hero_message}</p>
                    <div class="strengths-weaknesses">
                        <div><h4>Strengths</h4><ul>{strengths_html}</ul></div>
                        <div><h4>Weaknesses</h4><ul>{weaknesses_html}</ul></div>
                    </div>
                </div>
            </div>
        </div>
        """

    # Build failures section
    failures_html = ""
    if failures:
        failure_rows = "".join(
            f"<tr><td>{f.company}</td><td>{f.reason}</td></tr>" for f in failures
        )
        failures_html = f"""
        <h2>Failed Sites</h2>
        <table class="failures"><tr><th>Company</th><th>Reason</th></tr>
        {failure_rows}</table>
        """

    table_headers = "".join(f"<th>{label}</th>" for _, label in score_dimensions)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>UI Homepage Analysis: {domain}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 1200px; margin: 0 auto; padding: 2rem; background: #f8f9fa; color: #1a1a1a; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
    h2 {{ font-size: 1.4rem; margin: 2rem 0 1rem; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.5rem; }}
    h3 {{ font-size: 1.2rem; margin-bottom: 0.25rem; }}
    h4 {{ font-size: 0.9rem; margin-bottom: 0.5rem; color: #555; }}
    .subtitle {{ color: #666; margin-bottom: 2rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; background: white;
             border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    th, td {{ padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #f0f0f0; font-weight: 600; font-size: 0.85rem; }}
    .score {{ text-align: center; font-variant-numeric: tabular-nums; }}
    .overall {{ font-weight: 700; font-size: 1.05rem; color: #2563eb; }}
    .score-badge {{ background: #2563eb; color: white; padding: 2px 8px; border-radius: 12px;
                    font-size: 0.85rem; font-weight: 600; margin-left: 0.5rem; }}
    .company-card {{ background: white; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem;
                     box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .url {{ color: #666; font-size: 0.9rem; margin-bottom: 0.5rem; }}
    .url a {{ color: #2563eb; text-decoration: none; }}
    .summary {{ color: #333; margin-bottom: 1rem; font-style: italic; }}
    .card-body {{ display: flex; gap: 1.5rem; }}
    .screenshot {{ flex: 0 0 45%; }}
    .screenshot img {{ width: 100%; border: 1px solid #ddd; border-radius: 4px; }}
    .details {{ flex: 1; font-size: 0.9rem; }}
    .details p {{ margin-bottom: 0.5rem; }}
    .strengths-weaknesses {{ display: flex; gap: 1rem; margin-top: 0.75rem; }}
    .strengths-weaknesses div {{ flex: 1; }}
    .strengths-weaknesses ul {{ padding-left: 1.2rem; font-size: 0.85rem; }}
    .strengths-weaknesses li {{ margin-bottom: 0.25rem; }}
    .no-screenshot {{ color: #999; font-style: italic; }}
    .failures td {{ font-size: 0.9rem; }}
    @media (max-width: 768px) {{ .card-body {{ flex-direction: column; }} .screenshot {{ flex: none; }} }}
</style>
</head>
<body>
    <h1>UI Homepage Analysis: {domain}</h1>
    <p class="subtitle">{len(scorecards)} companies analyzed</p>

    <h2>Rankings</h2>
    <table>
        <tr><th>#</th><th>Company</th><th>Overall</th>{table_headers}</tr>
        {table_rows}
    </table>

    <h2>Company Details</h2>
    {company_sections}

    {failures_html}
</body>
</html>"""


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
