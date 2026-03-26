from competitive_website_analyst.models import FailureRecord, ReportBundle, ScoreBreakdown, Scorecard
from competitive_website_analyst.scoring import build_summary_csv, compute_overall_score, sort_scorecards


def make_scorecard(company: str, score: int) -> Scorecard:
    breakdown = ScoreBreakdown(
        positioning_clarity=score,
        target_audience_clarity=score,
        cta_strength=score,
        visual_polish=score,
        trust_credibility_signals=score,
        product_specificity=score,
        technical_depth=score,
    )
    return Scorecard(
        company=company,
        url=f"https://{company.lower()}.com",
        run_id=f"{company.lower()}-run",
        scores=breakdown,
        overall_score=compute_overall_score(breakdown),
    )


def test_compute_overall_score():
    card = make_scorecard("alpha", 8)
    assert card.overall_score == 8.0


def test_sort_scorecards_descending():
    cards = [make_scorecard("alpha", 5), make_scorecard("beta", 9)]
    assert [card.company for card in sort_scorecards(cards)] == ["beta", "alpha"]


def test_build_summary_csv_contains_expected_columns():
    csv_text = build_summary_csv([make_scorecard("alpha", 7)])
    assert "company,url,overall_score" in csv_text
    assert "alpha,https://alpha.com,7.0" in csv_text
