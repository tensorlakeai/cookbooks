# Competitive Website Analyst

A TensorLake cookbook that researches a market category, captures homepage screenshots in isolated browser sandboxes, scores each site with Claude's vision, and generates a ranked competitive analysis report.

## How It Works

```
domain + count
      |
      v
Research Agent      -- finds N candidate companies (Claude Agent SDK + web search)
      |
      v
Browser Agent (x N) -- parallel sandboxed Playwright sessions (snapshot or fresh install)
      |
      v
  Filter             -- drops failed browser runs
      |
      v
Analysis Agent (x N) -- Claude vision scores each screenshot (direct Anthropic API)
      |
      v
  Report Agent        -- ranked markdown report + CSV summary (direct Anthropic API)
```

Each browser agent runs in its own TensorLake Sandbox with Playwright. Research and browser interaction use the Claude Agent SDK (for web search and MCP browser tools). Analysis and report generation call the Anthropic API directly for lower latency.

## Agent Architecture

4 agents + 1 infrastructure function (all using Claude Agent SDK or Anthropic API directly):

| # | Agent | Tool Mode | SDK Features |
|---|-------|-----------|-------------|
| 1 | `research_agent` | Built-in only | Claude Agent SDK: `WebSearch`, `WebFetch` |
| 2 | `browser_agent` | Custom MCP only | `ClaudeSDKClient` + 6 custom browser tools via TensorLake Sandbox |
| 3 | `analysis_agent` | Vision (API direct) | Anthropic API `messages.create` with base64 screenshots |
| 4 | `report_agent` | API direct | Anthropic API `messages.create` |
| — | `create_browser_snapshot` | Infrastructure | TensorLake Sandbox snapshot (one-time setup) |

**5-phase orchestration in `competitive_analyst()`:**

1. Web research (`research_agent` — WebSearch/WebFetch, validates + dedupes companies)
2. Parallel browsing (`browser_agent.map()` — Playwright in sandbox, up to 3 backfill rounds)
3. Filtering (drop failed artifacts, track tried URLs)
4. Parallel analysis (`analysis_agent.map()` — vision scoring across 7 dimensions)
5. Report generation (`report_agent` — markdown + HTML + CSV output)

**Supporting infrastructure:**

- 8 data models (`Company`, `BrowserArtifact`, `Scorecard`, `ReportBundle`, etc.) using a custom `ModelMixin` for Pydantic-like interface
- `create_sandbox_mcp_server()` with 6 browser tools (`screenshot`, `click_text`, `click_coords`, `wait`, `extract_metadata`, `save_screenshot`)
- `MockAgentBackend` + `ClaudeAgentSDKBackend` (Protocol-based abstraction for local/cloud)
- `classify_browser_failure_stage()` with retryable vs non-retryable failure classification
- Scoring: weighted average across 7 dimensions → HTML report with embedded base64 screenshots

## Prerequisites

- Python 3.11+
- A [TensorLake](https://tensorlake.ai) account with `TENSORLAKE_API_KEY`
- An [Anthropic](https://console.anthropic.com) API key (`ANTHROPIC_API_KEY`)

## Setup

```bash
# Install the package with agent dependencies
pip install -e '.[agents]'

# Set required environment variables
export TENSORLAKE_API_KEY=your-tensorlake-key
export ANTHROPIC_API_KEY=your-anthropic-key
```

### (Recommended) Pre-build a sandbox snapshot

Each browser agent installs Playwright + Chromium at startup, which adds several minutes per run. Build a reusable snapshot once to skip that step on every subsequent run:

```bash
python -c "
from competitive_website_analyst.app import create_browser_snapshot
from tensorlake.applications import run_local_application
import json
result = run_local_application(create_browser_snapshot)
print(json.dumps(result.output(), indent=2))
"
```

Then pin the printed snapshot ID:

```bash
export BROWSER_SANDBOX_SNAPSHOT_ID=<snapshot-id-from-above>
```

With this set, browser agents restore from the snapshot instead of reinstalling Playwright, saving several minutes per run.

## Run Locally

Pass a market category as the domain and optionally the number of companies to analyze:

```bash
# Analyze 5 companies (default) in a category
python -m competitive_website_analyst.app "AI coding assistants"

# Analyze 10 companies
python -m competitive_website_analyst.app "AI coding assistants" --count 10

# Different domain examples
python -m competitive_website_analyst.app "developer tools" --count 8
python -m competitive_website_analyst.app "design collaboration software" --count 6
```

`run_local_application` validates the DAG in-process without containers. Browser isolation still depends on TensorLake Sandbox (requires `TENSORLAKE_API_KEY`).

## Deploy to TensorLake Cloud

Cloud deployment runs each `@function()` in its own container with auto-scaling, managed retries, and secret injection.

### 1. Upload secrets

```bash
tensorlake secrets set ANTHROPIC_API_KEY <your-anthropic-key>
tensorlake secrets set TENSORLAKE_API_KEY <your-tensorlake-key>
```

These are injected at runtime into any `@function(secrets=["ANTHROPIC_API_KEY", ...])`.

### 2. Deploy the application

```bash
tensorlake deploy app.py
```

This builds the container images (including the `browser_image` with Playwright + Chromium), uploads the code, and registers the application.

### 3. Run remotely

Use `run_remote_application` instead of `run_local_application`. Create a script or modify the entry point:

```python
from tensorlake.applications import run_remote_application
from competitive_website_analyst.app import competitive_analyst

request = run_remote_application(competitive_analyst, "AI coding assistants", 10)
print(request.output())  # blocks until the full DAG completes
```

Or invoke by registered application name:

```python
request = run_remote_application("competitive_analyst", "AI coding assistants", 10)
print(request.output())
```

### Output

The output is a JSON `ReportBundle`:

- `markdown_report` — the full competitive analysis in markdown
- `summary_csv` — a CSV table with scores per company
- `scorecards` — structured per-company scoring data
- `failures` — any sites that couldn't be captured

## Local Development (Mock Mode)

To run locally without the Claude Agent SDK or live sandboxes, enable mock mode:

```bash
export COMPETITIVE_ANALYST_USE_MOCKS=1
python -m competitive_website_analyst.app "AI coding assistants" --count 3
```

Mock mode uses deterministic fake data instead of real LLM calls, useful for testing the DAG wiring and data contracts.

## Run Tests

```bash
pip install -e '.[dev]'
pytest
```

## Project Structure

```
app.py                                  # TensorLake application entry point (re-export)
src/competitive_website_analyst/
  app.py                                # DAG: @application + @function definitions
  agent_backend.py                      # Claude Agent SDK integration (+ mock backend)
  browser_runtime.py                    # Sandbox browser server + RPC tools
  models.py                             # Data contracts (Company, BrowserArtifact, Scorecard, ReportBundle)
  scoring.py                            # Deterministic scoring, CSV generation
  prompts.py                            # Agent prompts
  utils.py                              # URL normalization, JSON parsing, validation
  browser_failures.py                   # Failure classification
tests/
  test_scoring.py                       # Scoring and CSV tests
  test_utils.py                         # Validation and URL normalization tests
```

