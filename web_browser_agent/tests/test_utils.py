import pytest

from competitive_website_analyst.browser_failures import classify_browser_failure_stage
from competitive_website_analyst.utils import normalize_homepage_url, validate_companies


def test_normalize_homepage_url_strips_query_and_fragment():
    assert normalize_homepage_url("https://Example.com/path/?a=1#frag") == "https://example.com/path"


def test_validate_companies_filters_invalid_entries_and_dedupes():
    companies = validate_companies(
        [
            {"name": "A", "url": "https://a.com", "short_description": "x"},
            {"name": "A Copy", "url": "https://a.com/pricing", "short_description": "y"},
            {"name": "", "url": "https://b.com", "short_description": "z"},
            {"name": "B", "url": "https://b.com", "short_description": "z"},
        ],
        count=10,
    )
    assert [company.id for company in companies] == ["a", "b"]


def test_validate_companies_requires_one_valid_result():
    with pytest.raises(ValueError):
        validate_companies([{"name": "", "url": "", "short_description": ""}], count=3)


def test_classify_browser_failure_stage():
    assert classify_browser_failure_stage("Sandbox q did not start within 60s") == "sandbox_startup"
    assert classify_browser_failure_stage("failed to install browser runtime") == "runtime_bootstrap"
    assert classify_browser_failure_stage("browser server failed to start") == "browser_server_startup"
