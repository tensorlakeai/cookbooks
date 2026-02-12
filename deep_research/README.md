# Deep Research Agent on Tensorlake

A multi-agent deep research pipeline powered by the OpenAI Agents SDK and Tensorlake. Given a research question, a planner agent generates search queries, a search agent executes them in parallel via Tensorlake Futures, and a writer agent synthesizes a comprehensive report.

This mirrors the [Temporal deep research example](https://github.com/temporalio/sdk-python), replacing Temporal's workflow orchestration with Tensorlake's `@application`/`@function` and Futures for parallel execution.

## How It Works

The pipeline runs 3 agent phases, each as an isolated Tensorlake function:

1. **PlannerAgent** (`gpt-4o`) — Generates 2-3 targeted web search queries with rationale
2. **SearchAgent** (`gpt-4o` + `WebSearchTool`) — Executes each search and produces a concise summary (runs in parallel via Futures)
3. **WriterAgent** (`o3-mini`) — Synthesizes all search summaries into a detailed markdown report

```
                            ┌──── search_web(q1) ────┐
query → plan_research ──────┼──── search_web(q2) ────┼──── write_report → report
                            └──── search_web(q3) ────┘
```

## Prerequisites

- Python 3.11+
- An [OpenAI API key](https://platform.openai.com/api-keys)

```bash
pip install tensorlake openai-agents pydantic
export OPENAI_API_KEY="sk-..."
```

## Run Locally

```bash
python app.py
```

This runs the full pipeline locally via Tensorlake's `run_local_application()`.

## Deploy to Tensorlake

```bash
tensorlake deploy app.py
```

Once deployed, invoke via the Tensorlake HTTP API:

```bash
curl -X POST https://api.tensorlake.ai/applications/deep_research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '"What are the economic impacts of AI on the job market?"'
```

## Project Structure

| File | Description |
|------|-------------|
| `app.py` | Main application — Tensorlake functions and orchestrator |
| `models.py` | Pydantic data models (`WebSearchPlan`, `ReportData`) |
| `prompts.py` | System prompts for each OpenAI agent phase |
