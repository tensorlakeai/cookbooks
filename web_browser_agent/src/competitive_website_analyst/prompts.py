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
- Once the page looks clean, save a final screenshot to /app/screenshot.png
- Extract page metadata
- If the site is unreachable or broken, report failure
- Do not click through to other pages or submit forms
- Stay brief and return structured output only"""


ANALYSIS_PROMPT = """Analyze this company's website homepage using the attached screenshot and metadata.

Company: {company}
URL: {url}
Page metadata: {metadata}

Score each dimension from 1 to 10 using these rubrics:

- positioning_clarity: Can you tell what the product does within 5 seconds of looking at the page?
- target_audience_clarity: Is it obvious who this product is for?
- cta_strength: Is the next step clear, specific, and compelling?
- visual_polish: Does it look professional, modern, and intentional?
- trust_credibility_signals: Are there logos, testimonials, security badges, or team bios?
- product_specificity: Does it show the actual product vs vague promises?
- technical_depth: Does it speak to practitioners or only to buyers?

Return a single JSON object with exactly these fields:
{{
  "company": "{company}",
  "url": "{url}",
  "run_id": "",
  "scores": {{
    "positioning_clarity": <1-10>,
    "target_audience_clarity": <1-10>,
    "cta_strength": <1-10>,
    "visual_polish": <1-10>,
    "trust_credibility_signals": <1-10>,
    "product_specificity": <1-10>,
    "technical_depth": <1-10>
  }},
  "overall_score": 0.0,
  "target_audience_guess": "<who this is for>",
  "primary_cta": "<main call-to-action text>",
  "hero_message": "<hero heading text>",
  "strengths": ["<strength 1>", ...],
  "weaknesses": ["<weakness 1>", ...],
  "one_sentence_summary": "<one sentence positioning summary>"
}}

Return JSON only. Do not wrap in markdown fences. Max 5 strengths and 5 weaknesses."""


REPORT_PROMPT = """Generate a concise competitive UI homepage analysis report from these scorecards:
{scorecards}

This analysis is based on visual and content evaluation of each company's public homepage only —
it does not reflect product quality, pricing, or user reviews.

Include:
- ranked table by overall score
- per-company section with summary, strengths, weaknesses, and score highlights
- top 3 lists where enough data exists
- common homepage messaging patterns
- homepage gaps and opportunities"""
