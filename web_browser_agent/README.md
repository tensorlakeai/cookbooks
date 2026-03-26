# Competitive Website Analyst

TensorLake application for researching a category, capturing homepage artifacts in isolated sandboxes, scoring each site, and generating a final markdown report.

## Setup

Set `TENSORLAKE_API_KEY` before running TensorLake code.

For the real Claude-backed agent path, install the agent extra and set `ANTHROPIC_API_KEY`:

```bash
pip install -e '.[agents]'
```

For local development without the real Claude Agent SDK integration, enable mocks:

```bash
export COMPETITIVE_ANALYST_USE_MOCKS=1
export BROWSER_SANDBOX_SNAPSHOT_ID=your-playwright-snapshot
```

## Run

```bash
python app.py
```

## Notes

- `run_local_application(...)` validates the DAG locally.
- Browser isolation still depends on TensorLake Sandbox.
- The real Claude Agent SDK calls are isolated in `src/competitive_website_analyst/agent_backend.py`.
