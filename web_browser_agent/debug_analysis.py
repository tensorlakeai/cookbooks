#!/usr/bin/env python3
"""Run the analysis agent in isolation against a local screenshot.
Usage:
    python debug_analysis.py <screenshot_path>
    python debug_analysis.py /tmp/artifacts/cursor-20260327-ee22f34f/screenshot.png
"""
import sys
from pathlib import Path

from competitive_website_analyst.agent_backend import get_agent_backend
from competitive_website_analyst.models import BrowserArtifact, BrowserMetadata, Company
from competitive_website_analyst.utils import parse_json
from competitive_website_analyst.scoring import compute_overall_score
from competitive_website_analyst.models import Scorecard

screenshot_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/artifacts/cursor-20260327-ee22f34f/screenshot.png"

if not Path(screenshot_path).exists():
    print(f"ERROR: screenshot not found: {screenshot_path}")
    sys.exit(1)

print(f"Screenshot: {screenshot_path} ({Path(screenshot_path).stat().st_size / 1024:.0f} KB)")

# Infer company name from the artifact dir name
run_id = Path(screenshot_path).parent.name
company_name = run_id.rsplit("-", 2)[0].replace("-", " ").title()
company_url = f"https://{run_id.rsplit('-', 2)[0]}.com"

artifact = BrowserArtifact(
    company=Company(id=run_id.rsplit("-", 2)[0], name=company_name, url=company_url, short_description=company_name),
    run_id=run_id,
    status="success",
    screenshot_path=screenshot_path,
    metadata_path="",
    metadata=BrowserMetadata(
        title=company_name,
        h1_hero_text="",
        meta_description="",
        nav_items=[],
        visible_cta_labels=[],
        og_image_url="",
        page_load_time_ms=0,
    ),
)

print(f"Company:    {company_name}")
print(f"Running analysis agent (timeout=120s)...\n")

backend = get_agent_backend()
try:
    raw = backend.analyze(artifact)
    print(f"\nRaw output:\n{raw}")
    scorecard = Scorecard.model_validate(parse_json(raw))
    print(f"\nOverall score: {compute_overall_score(scorecard.scores):.2f}/10")
except Exception as e:
    print(f"\nERROR: {e}")
    raise
