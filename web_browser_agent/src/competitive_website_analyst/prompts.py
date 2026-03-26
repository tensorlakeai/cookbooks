RESEARCH_PROMPT = """Find {count} companies in the '{domain}' space.
Search the web to discover real companies with active websites.
Return a JSON array: [{{name, url, short_description}}].
Validate that URLs point to real homepages."""


BROWSER_PROMPT = """You are browsing {company_url}. Your goal is to get a clean, full-page
homepage screenshot and extract page metadata.

Instructions:
- First take a screenshot to inspect the page state
- If you see a cookie or consent popup, try to dismiss it
- If you see a loading spinner or skeleton state, wait and check again
- If you see an interstitial or signup wall, try to dismiss it
- Once the page looks clean, save a final full-page screenshot
- Extract page metadata
- If the site is unreachable or broken, report failure
- Do not click through to other pages or submit forms
- Stay brief and return structured output only"""


ANALYSIS_PROMPT = """Analyze this homepage.

Company: {company}
URL: {url}
Page metadata: {metadata}

Return structured JSON with:
- scores for positioning_clarity, target_audience_clarity, cta_strength,
  visual_polish, trust_credibility_signals, product_specificity, technical_depth
- target_audience_guess
- primary_cta
- hero_message
- strengths
- weaknesses
- one_sentence_summary"""


REPORT_PROMPT = """Generate a concise competitive analysis report from these scorecards:
{scorecards}

Include:
- ranked table by overall score
- per-company section with summary, strengths, weaknesses, and score highlights
- top 3 lists where enough data exists
- common messaging patterns
- gaps and opportunities"""
