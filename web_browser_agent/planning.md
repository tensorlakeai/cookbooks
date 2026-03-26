## Competitive Website Analyst

**Why this is a good Tensorlake story:**
Tensorlake can orchestrate sandboxed agents that interact with rendered websites, generate artifacts, extract structured signals, and synthesize decision-ready reports — all in parallel, with each browser session isolated in its own sandbox.

---

## V1 Scope

**In scope:** Desktop viewport (1280x800), homepage only, one screenshot per site, no login, no scrolling beyond full-page capture, no PDF output in v1.

**Out of scope for v1:** Mobile/responsive analysis, multi-page crawling, login-gated content, PDF report generation.

---

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| **DAG orchestration** | TensorLake Orchestrate (`@application`, `@function`, `.map()`, `.reduce()`) | Fan-out, parallelism, retries, structured result passing |
| **Isolated execution** | TensorLake Sandbox | Headless browser environments, one per company |
| **LLM reasoning** | Claude Agent SDK (`query()`, tools, vision) | Research, browser interaction, analysis, report generation |

These layers complement each other: Orchestrate handles *what runs when*, Sandbox handles *where it runs*, Agent SDK handles *thinking*.

---

## Workflow overview

```
User input (domain + count)
        |
        v
+----------------------------+
|  1. Research Agent         |  Claude Agent SDK + WebSearch tool
|     @function()            |  Discovers N companies in the domain
+------------+---------------+
             | list of {name, url}
             v
+----------------------------+
|  2. Orchestrator           |  TensorLake @application + @function
|     @application()         |  .future() and .map() coordinate the pipeline
+------------+---------------+
             | one task per company
             v
+----------------------------+
|  3. Browser Agent          |  Claude Agent SDK + Sandbox + Playwright tools
|     @function() x N        |  Tight loop: load, dismiss popups, screenshot
|     (parallel)             |  Returns status: success/failed
+------------+---------------+
             | screenshot path + metadata per site
             v
+----------------------------+
|  3.5 Filter                |  Pure function — no LLM
|     @function()            |  Drop failed browser results before analysis
+------------+---------------+
             | successful artifacts only
             v
+----------------------------+
|  4. Analysis Agent         |  Claude Agent SDK + vision
|     @function() x N        |  screenshot + metadata -> structured scorecard
|     (parallel)             |
+------------+---------------+
             | scored results
             v
+----------------------------+
|  5. Report Agent           |  Claude Agent SDK
|     @function()            |  All scorecards -> final markdown report
+----------------------------+
```

---

## Input

| Parameter | Type   | Example                  | Description                                |
|-----------|--------|--------------------------|--------------------------------------------|
| `domain`  | string | `"AI coding assistants"` | The market category to research            |
| `count`   | int    | `10`                     | Number of companies to discover and analyze |

That's it. The system figures out everything else.

---

## Build Readiness Assumptions

These assumptions make the plan implementable without changing the v1 product shape:

- TensorLake supports passing the output of one `@function()` as the input to downstream `.map()` / `.future()` calls within the same `@application()`.
- Each mapped `browser_agent` run executes in its own isolated sandbox and cannot share browser state with other runs.
- Agent outputs are validated in deterministic Python before being passed to the next stage.
- Artifacts are addressed by deterministic IDs, not only by company name, so retries and duplicate names do not collide.
- The final report is allowed to complete with partial coverage as long as at least one site succeeds.

---

## The DAG (TensorLake Orchestrate)

```python
from tensorlake.applications import application, function, run_local_application, Image

@application()
@function()
def competitive_analyst(domain: str, count: int) -> dict:
    # Step 1: Agent discovers companies via web search
    companies = research_agent.future(domain, count)

    # Step 3: Browser agents — parallel sandbox scraping (one per company)
    raw_artifacts = browser_agent.map(companies)

    # Step 3.5: Filter out failed browser runs before analysis
    successful_artifacts = filter_successful.future(raw_artifacts)

    # Step 4: Analysis agents — parallel scoring (only successful ones)
    scorecards = analysis_agent.map(successful_artifacts)

    # Step 5: Report agent — single call with all scorecards
    report = report_agent.future(scorecards)

    return report

if __name__ == "__main__":
    request = run_local_application(
        competitive_analyst,
        "AI coding assistants",
        10,
    )
    print(request.output())
```

Step 2 (Orchestrator) is implicit — TensorLake's `.map()` and `.future()` handle fan-out, dependency ordering, and failure isolation automatically. No separate orchestrator function needed.

**Execution semantics:**
- `research_agent` must resolve to a concrete `list[Company]` before `browser_agent.map(...)` begins.
- `browser_agent.map(companies)` creates one independent task per company.
- `filter_successful` runs once after all browser tasks complete and returns `list[BrowserArtifact]`.
- `analysis_agent.map(successful_artifacts)` creates one task per successful artifact only.
- `report_agent` runs once with the full list of validated scorecards.
- The application returns a single `ReportBundle` object, not raw agent text.

**Minimum success criteria for a run:**
- `research_agent` returns at least 1 valid company
- `browser_agent` succeeds for at least 1 company
- `analysis_agent` succeeds for every browser artifact passed into it
- `report_agent` always receives only validated scorecards

If zero browser runs succeed, the application returns a structured failure bundle instead of throwing away the whole run.

---

## Canonical Data Contracts

These are the deterministic contracts between steps. Agent prompts can be flexible, but handoff objects should match these shapes exactly.

### `Company`

```json
{
  "id": "cursor",
  "name": "Cursor",
  "url": "https://cursor.com",
  "short_description": "AI-first code editor"
}
```

Rules:
- `id` is a slug derived from normalized company name, deduplicated if needed
- `url` must be an absolute `https://` homepage URL
- `name`, `url`, and `short_description` are required

### `BrowserArtifact`

```json
{
  "company": {
    "id": "cursor",
    "name": "Cursor",
    "url": "https://cursor.com",
    "short_description": "AI-first code editor"
  },
  "run_id": "cursor-20260326-8f3c1d2a",
  "status": "success",
  "failure_reason": null,
  "screenshot_path": "/tmp/artifacts/cursor-20260326-8f3c1d2a/screenshot.png",
  "metadata_path": "/tmp/artifacts/cursor-20260326-8f3c1d2a/metadata.json",
  "metadata": {
    "title": "",
    "meta_description": "",
    "h1_hero_text": "",
    "visible_cta_labels": [],
    "nav_items": [],
    "og_image_url": "",
    "page_load_time_ms": 0
  }
}
```

Rules:
- `status` is `"success"` or `"failed"`
- `failure_reason` is required when `status == "failed"`
- `screenshot_path` and `metadata` are required when `status == "success"`
- `run_id` is unique per attempt and used for artifact directory naming

### `Scorecard`

```json
{
  "company": "Cursor",
  "url": "https://cursor.com",
  "run_id": "cursor-20260326-8f3c1d2a",
  "scores": {
    "positioning_clarity": 8,
    "target_audience_clarity": 8,
    "cta_strength": 7,
    "visual_polish": 9,
    "trust_credibility_signals": 6,
    "product_specificity": 8,
    "technical_depth": 7
  },
  "overall_score": 7.75,
  "target_audience_guess": "",
  "primary_cta": "",
  "hero_message": "",
  "strengths": [],
  "weaknesses": [],
  "one_sentence_summary": ""
}
```

Rules:
- All score fields are integers from 1 to 10
- `overall_score` is computed in Python, never accepted directly from the LLM
- `strengths` and `weaknesses` must contain short strings, max 5 each

### `ReportBundle`

```json
{
  "domain": "AI coding assistants",
  "requested_count": 10,
  "discovered_count": 10,
  "successful_count": 8,
  "failed_count": 2,
  "failures": [
    { "company": "Example", "reason": "timeout" }
  ],
  "scorecards": [],
  "markdown_report": "...",
  "summary_csv": "company,url,overall_score\n"
}
```

---

## Step 1: Research Agent

A Claude agent with web search capability discovers companies in the given domain.

**Runs as:** `@function()` → Claude Agent SDK with `WebSearch` + `WebFetch` tools

**Input:** `domain`, `count`

**Output:** list of companies:
```json
[
  { "name": "Cursor", "url": "https://cursor.com", "short_description": "AI-first code editor" },
  { "name": "Cody", "url": "https://sourcegraph.com/cody", "short_description": "AI coding assistant by Sourcegraph" }
]
```

**Implementation sketch:**
```python
@function(timeout=120, secrets=["ANTHROPIC_API_KEY"])
def research_agent(domain: str, count: int) -> list[dict]:
    result = run_agent(
        prompt=f"""Find {count} companies in the '{domain}' space.
        Search the web to discover real companies with active websites.
        Return a JSON array: [{{name, url, short_description}}]
        Validate that URLs point to real homepages.""",
        tools=["WebSearch", "WebFetch"],
        max_turns=15,
    )
    return parse_company_list(result)
```

**Deterministic validation after agent output:**
- Parse agent output as JSON only
- Drop entries missing `name` or `url`
- Normalize URLs to homepage form and require `https://`
- De-duplicate by normalized hostname
- If more than `count` valid companies are returned, keep the top `count`
- If fewer than `count` are valid, proceed with the smaller set rather than failing the entire run

**Failure contract:**
- Hard fail only if zero valid companies are returned after validation
- Treat malformed JSON, empty output, and non-homepage URLs as validation failures
- Retry once on malformed agent output before surfacing failure to the application

**TensorLake retry configuration:**
- If you want TensorLake-managed retries for this step, declare them explicitly on the function:

```python
from tensorlake.applications import Retries

@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def research_agent(domain: str, count: int) -> list[dict]:
    ...
```

**Why an agent:** Discovery requires multi-step reasoning — search, evaluate results, filter out dead links, backfill if needed. The agent's tool loop handles this naturally.

**Why separate from the orchestrator:** Can be retried independently if it returns bad URLs. The orchestrator only deals with fan-out.

---

## Step 3: Browser Agent (x N, parallel)

Each browser agent runs inside its own TensorLake Sandbox with Playwright. The agent uses **vision** to see the rendered page and decides how to interact with it.

**Runs as:** `@function()` → TensorLake Sandbox + Claude Agent SDK with MCP tools wrapping Playwright

**Why an agent (not a script):** Websites are unpredictable. Cookie popups, consent banners, interstitials, loading spinners, and CAPTCHAs all vary. A vision-capable agent can see what's on screen and reason about what to do — no brittle CSS selectors or hardcoded popup patterns.

**V1 contract (tight loop, not freeform browsing):**
1. Navigate to homepage URL
2. Wait for page to stabilize (network idle)
3. Take a screenshot to assess page state
4. If cookie/consent popup is visible, click accept/dismiss
5. Take final full-page screenshot (PNG)
6. Extract page metadata from DOM
7. Done — no further exploration

The agent should complete in 3-5 tool calls. If it hasn't finished in 10 turns, force-stop and return what it has.

**Function image setup:**
```python
browser_image = Image(
    base_image="python:3.11-slim"
).run(
    "pip install playwright && playwright install chromium --with-deps"
)
```

This image configures the `browser_agent` function environment itself. The TensorLake Sandbox launched from inside the function is a separate runtime and must also be provisioned with browser dependencies.

**Sandbox runtime setup:**
- Pre-build a sandbox image, snapshot, or pool that already contains Python, Playwright, and Chromium
- Pass that runtime explicitly to `SandboxClient.create_and_connect(...)` via `image=...`, `snapshot_id=...`, or `pool_id=...`
- Do not assume `@function(image=browser_image)` automatically configures the nested sandbox runtime

**MCP tools exposed to the agent (thin wrappers around Playwright in the sandbox):**

| Tool | Description |
|------|-------------|
| `screenshot` | Capture current viewport as PNG, returned to agent as image for vision |
| `click(selector_or_coords)` | Click an element by CSS selector or x,y coordinates |
| `scroll(direction, amount)` | Scroll the page |
| `wait(seconds)` | Wait for page to settle |
| `extract_metadata` | Pull title, meta description, h1, CTAs, nav items from DOM |
| `save_screenshot(path)` | Save the final clean screenshot as artifact |

**Browser control contract:**
- Each sandbox run creates exactly one Playwright browser context and one page
- Tool calls operate against that single page for the lifetime of the run
- The MCP bridge is stateful within the sandboxed function invocation only
- The browser process is started before the agent loop and torn down after artifacts are copied out
- Tools must return structured results, not freeform text, so the agent can reason over success/failure cleanly

**Agent prompt:**
```
You are browsing {company_url}. Your goal: get a clean, full-page screenshot
of the homepage and extract page metadata.

Instructions:
- First take a screenshot to see the current state of the page
- If you see cookie/consent popups, click "Accept" or "Accept All" to dismiss them
- If there's a loading spinner or skeleton UI, wait and check again
- If there's an interstitial or signup wall, try to dismiss it
- Once the page looks clean and fully loaded, save the final screenshot
- Extract page metadata (title, description, hero text, CTAs, nav items)
- If the site is unreachable or broken, report the failure
- Do NOT click through to other pages, fill in forms, or scroll beyond full-page capture
- Complete within 5 tool calls. Stop after 10 turns maximum.
```

**Implementation sketch:**
```python
@function(image=browser_image, timeout=180, secrets=["ANTHROPIC_API_KEY"])
def browser_agent(company: dict) -> dict:
    client = SandboxClient()
    with client.create_and_connect(
        snapshot_id="playwright-browser-snapshot",
        allow_internet_access=True,
        timeout_secs=180,
    ) as sandbox:
        run_id = make_run_id(company["id"])

        # Start browser and navigate
        sandbox.run(
            "python",
            args=["-c", f"...playwright launch + goto {company['url']}..."],
            timeout=30,
        )

        # MCP tools that talk to the running browser in the sandbox
        browser_tools = create_browser_mcp_server(sandbox)

        result = run_agent(
            prompt=BROWSER_AGENT_PROMPT.format(company_url=company["url"]),
            tools=browser_tools,  # screenshot, click, scroll, wait, extract_metadata
            max_turns=15,
        )

        # Pull artifacts from sandbox to local temp storage
        artifact_dir = f"/tmp/artifacts/{run_id}"
        os.makedirs(artifact_dir, exist_ok=True)
        screenshot_path = f"{artifact_dir}/screenshot.png"
        metadata_path = f"{artifact_dir}/metadata.json"

        screenshot_bytes = sandbox.read_file("/app/screenshot.png")
        metadata_bytes = sandbox.read_file("/app/metadata.json")
        open(screenshot_path, "wb").write(screenshot_bytes)
        open(metadata_path, "wb").write(metadata_bytes)
        metadata = json.loads(open(metadata_path).read())

        return {
            "company": company,
            "run_id": run_id,
            "screenshot_path": screenshot_path,
            "metadata_path": metadata_path,
            "metadata": metadata,
            "status": "success",
        }
```

**TensorLake retry configuration:**
- Function-level retries are not implicit. If this step should retry transient failures, configure them explicitly:

```python
from tensorlake.applications import Retries

@function(
    image=browser_image,
    timeout=180,
    secrets=["ANTHROPIC_API_KEY"],
    retries=Retries(max_retries=2),
)
def browser_agent(company: dict) -> dict:
    ...
```

**Artifact rules:**
- Use `artifact_dir = f"/tmp/artifacts/{run_id}"`, not company name alone
- `run_id` format: `{company_id}-{YYYYMMDD}-{random_suffix}`
- Save exactly two primary artifacts per successful run: `screenshot.png` and `metadata.json`
- If a retry occurs, it gets a new `run_id` and new artifact directory
- Artifact paths are returned in the function output so downstream steps never reconstruct them heuristically

**Browser metadata extraction rules:**
- `title`: document title
- `meta_description`: `<meta name="description">`
- `h1_hero_text`: first visible H1 or equivalent hero heading if present
- `visible_cta_labels`: visible button / link labels in the hero or primary CTA area
- `nav_items`: top-level visible navigation labels
- `og_image_url`: `<meta property="og:image">` if present
- `page_load_time_ms`: measured deterministically in Playwright, not guessed by the agent

**Failure categories:**
- `dns_error`
- `timeout`
- `blocked_or_captcha`
- `browser_crash`
- `empty_or_unrendered_page`
- `unexpected_agent_failure`

**Retry policy:**
- Retry browser runs up to 2 times for `timeout`, `browser_crash`, or transient navigation failures
- Do not retry `blocked_or_captcha` more than once
- Always return a structured failed artifact if all attempts are exhausted

**Extracted metadata:**
```json
{
  "title": "",
  "meta_description": "",
  "h1_hero_text": "",
  "visible_cta_labels": [],
  "nav_items": [],
  "og_image_url": "",
  "page_load_time_ms": 0
}
```

**Artifacts produced:**
| Artifact       | Format | Required |
|----------------|--------|----------|
| Screenshot     | PNG    | Yes      |
| Page metadata  | JSON   | Yes      |

**Failure handling:**
- If a site is unreachable after the agent's attempts, return `"status": "failed"` with a reason
- TensorLake retries only if `retries=Retries(...)` is configured on the function
- Failed sites are excluded from analysis — don't block the whole run

---

## Step 3.5: Filter Successful Results

Pure function — no LLM. Drops failed browser runs so analysis only processes valid artifacts.

```python
@function()
def filter_successful(artifacts: list[dict]) -> list[dict]:
    successful = [a for a in artifacts if a["status"] == "success"]
    failed = [a for a in artifacts if a["status"] != "success"]
    if failed:
        print(f"Skipping {len(failed)} failed sites: {[a['company']['name'] for a in failed]}")
    return successful
```

**Behavioral rule:**
- This step must not throw if some sites fail
- It should log skipped companies and preserve only validated successful artifacts
- If zero successful artifacts remain, downstream report generation receives a structured empty-result bundle instead of attempting analysis

---

## Step 4: Analysis Agent (x N, parallel)

Each analysis agent receives the screenshot + metadata for one site and produces a structured scorecard. Uses vision to evaluate the screenshot.

**Runs as:** `@function()` → Claude Agent SDK with vision

**Input:** screenshot PNG (as image) + metadata JSON from Step 3

**Implementation sketch:**
```python
@function(timeout=120, secrets=["ANTHROPIC_API_KEY"])
def analysis_agent(artifacts: dict) -> dict:
    result = run_agent(
        prompt=f"""Analyze this company's website homepage.

        Company: {artifacts['company']['name']}
        URL: {artifacts['company']['url']}
        Page metadata: {json.dumps(artifacts['metadata'])}

        [screenshot image attached]

        Score each dimension 1-10 and provide analysis.
        Return the scorecard as structured JSON.""",
        image=open(artifacts["screenshot_path"], "rb").read(),
        max_turns=3,
    )
    return parse_scorecard(result)
```

**Deterministic validation after agent output:**
- Parse JSON only
- Require every score dimension to be present
- Coerce score values to integers and reject values outside 1-10
- Truncate `strengths` and `weaknesses` to at most 5 items each
- Compute `overall_score` in Python from the validated component scores
- Attach `run_id` from the browser artifact so every scorecard remains traceable to a screenshot

**Overall score function:**

```python
def compute_overall_score(scores: dict[str, int]) -> float:
    weights = {
        "positioning_clarity": 0.20,
        "target_audience_clarity": 0.15,
        "cta_strength": 0.15,
        "visual_polish": 0.15,
        "trust_credibility_signals": 0.10,
        "product_specificity": 0.15,
        "technical_depth": 0.10,
    }
    total = sum(scores[k] * w for k, w in weights.items())
    return round(total, 2)
```

**Failure contract:**
- If screenshot bytes cannot be loaded or metadata is missing, do not call the agent; return a failed analysis result immediately
- If the agent returns malformed JSON, retry once
- If validation still fails, mark the artifact as analysis-failed and exclude it from ranking tables

**TensorLake retry configuration:**
- If desired, configure this step explicitly:

```python
from tensorlake.applications import Retries

@function(timeout=120, secrets=["ANTHROPIC_API_KEY"], retries=Retries(max_retries=1))
def analysis_agent(artifacts: dict) -> dict:
    ...
```

**Scoring schema (1-10 scale):**

```json
{
  "company": "",
  "url": "",
  "scores": {
    "positioning_clarity": 0,
    "target_audience_clarity": 0,
    "cta_strength": 0,
    "visual_polish": 0,
    "trust_credibility_signals": 0,
    "product_specificity": 0,
    "technical_depth": 0
  },
  "overall_score": 0.0,
  "target_audience_guess": "",
  "primary_cta": "",
  "hero_message": "",
  "strengths": [],
  "weaknesses": [],
  "one_sentence_summary": ""
}
```

**Scoring guidelines (included in the agent prompt):**
- **Positioning clarity:** Can you tell what the product does within 5 seconds?
- **Target audience clarity:** Is it obvious who this is for?
- **CTA strength:** Is the next step clear, specific, and compelling?
- **Visual polish:** Does it look professional, modern, and intentional?
- **Trust/credibility signals:** Logos, testimonials, security badges, team bios?
- **Product specificity:** Does it show the actual product vs vague promises?
- **Technical depth:** Does it speak to practitioners or only to buyers?

**Overall score formula (weighted average):**

| Dimension                    | Weight |
|------------------------------|--------|
| `positioning_clarity`        | 20%    |
| `target_audience_clarity`    | 15%    |
| `cta_strength`               | 15%    |
| `visual_polish`              | 15%    |
| `trust_credibility_signals`  | 10%    |
| `product_specificity`        | 15%    |
| `technical_depth`            | 10%    |

Positioning is weighted highest because it's the first thing a visitor evaluates. Trust and technical depth are lower because they're harder to assess from a homepage screenshot alone. The overall score is computed deterministically (not by the LLM) and included in the scorecard before passing to the report agent.

---

## Step 5: Report Agent

Receives all scorecards in a single call and produces the final report. No reduce needed — the agent needs all scorecards at once to do cross-company comparison and rankings.

**Runs as:** `@function()` → Claude Agent SDK

**Implementation sketch:**
```python
@function(timeout=180, secrets=["ANTHROPIC_API_KEY"])
def report_agent(scorecards: list[dict]) -> dict:
    sorted_scorecards = sorted(scorecards, key=lambda s: s["overall_score"], reverse=True)
    result = run_agent(
        prompt=f"""Generate a competitive analysis report from these scorecards:
        {json.dumps(sorted_scorecards)}

        Include:
        - Per-company section: name, URL, summary, scores, strengths, weaknesses
        - Ranked table by overall score
        - Top 3 lists: clearest positioning, strongest CTAs, most technical, most enterprise
        - Common messaging patterns across the category
        - Gaps and opportunities""",
        max_turns=5,
    )
    return {
        "scorecards": sorted_scorecards,
        "markdown_report": result,
        "summary_csv": build_summary_csv(sorted_scorecards),
    }
```

**TensorLake runtime note:**
- `run_local_application(...)` is useful for validating DAG logic and step interfaces.
- In this design, browser isolation comes from explicit `SandboxClient` usage inside `browser_agent`, not from local Orchestrate execution itself.
- Remote deployment is still the real validation path for cloud image, secret, and scaling behavior.

**Deterministic post-processing owned by this step:**
- Sort scorecards by `overall_score` descending before prompting the report agent
- Generate `summary_csv` in Python, not via the LLM
- Include failed-site counts and reasons in the final returned bundle
- If only one scorecard is available, still generate the report with comparison sections omitted or marked as insufficient sample size

**CSV schema:**
- `company`
- `url`
- `overall_score`
- `positioning_clarity`
- `target_audience_clarity`
- `cta_strength`
- `visual_polish`
- `trust_credibility_signals`
- `product_specificity`
- `technical_depth`
- `primary_cta`
- `target_audience_guess`

**Final application return shape:**
- `domain`
- `requested_count`
- `discovered_count`
- `successful_count`
- `failed_count`
- `failures`
- `scorecards`
- `markdown_report`
- `summary_csv`

**Per-company section:**
- Company name + URL
- Screenshot thumbnail
- One-sentence positioning summary
- Primary CTA
- Target audience
- Strengths / weaknesses
- Score breakdown (radar chart or table)

**Cross-company analysis:**
- Ranked table by overall score
- Top 3: clearest positioning
- Top 3: strongest CTAs
- Top 3: most technical
- Top 3: most enterprise-looking
- Common messaging patterns across the category
- Gaps and opportunities

**Output artifacts:**
| Artifact      | Format   |
|---------------|----------|
| Full report   | Markdown |
| Summary table | CSV      |

---

## Operational Rules For V1

- Viewport is fixed at `1280x800` for every browser run
- Homepage only means the initial canonical URL plus any redirects to the same site; no click-through exploration
- Full-page capture is allowed, but no exploratory scrolling beyond what Playwright needs to render the full-page screenshot
- No login, account creation, form submission, or chat widget interaction
- CAPTCHAs and hard anti-bot blocks are recorded as failures, not bypassed
- Partial completion is acceptable: the system should produce the best report possible from the successful sites

## Suggested Build Order

1. Implement deterministic schemas, validators, and `ReportBundle` assembly first
2. Implement `research_agent` with strict JSON parsing and URL normalization
3. Implement a minimal browser sandbox runner with a single persistent Playwright page plus MCP tool wrappers
4. Implement `analysis_agent` with score validation and deterministic overall scoring
5. Implement `report_agent` and Python CSV generation
6. Add retries, structured logging, and empty-result handling last
