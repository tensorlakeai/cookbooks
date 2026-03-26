# Competitive Website Analyst

A TensorLake cookbook that researches a market category, captures homepage screenshots in isolated browser sandboxes, scores each site with Claude's vision, and generates a ranked competitive analysis report.

## How It Works

```
domain + count
      |
      v
Research Agent        -- discovers N companies via web search
      |
      v
Browser Agent (x N)   -- parallel sandboxed Playwright sessions, one per site
      |
      v
Filter                -- drops failed browser runs
      |
      v
Analysis Agent (x N)  -- Claude vision scores each homepage screenshot
      |
      v
Report Agent          -- produces ranked markdown report + CSV summary
```

Each browser agent runs in its own TensorLake Sandbox with Playwright. The Claude Agent SDK handles all LLM reasoning (research, browser interaction, analysis, report generation).

## Prerequisites

- Python 3.11+
- A [TensorLake](https://tensorlake.ai) account with `TENSORLAKE_API_KEY`
- An [Anthropic](https://console.anthropic.com) API key (`ANTHROPIC_API_KEY`)
- A TensorLake sandbox snapshot pre-built with Playwright + Chromium (`BROWSER_SANDBOX_SNAPSHOT_ID`)

## Setup

```bash
# Install the package with agent dependencies
pip install -e '.[agents]'

# Set required environment variables
export TENSORLAKE_API_KEY=your-tensorlake-key
export ANTHROPIC_API_KEY=your-anthropic-key
export BROWSER_SANDBOX_SNAPSHOT_ID=your-playwright-snapshot
```

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
