# Google ADK Agents on TensorLake

Run [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/agents/) agents as serverless applications on TensorLake. Each example demonstrates a different ADK agent type, rewritten so that every LLM agent and tool runs as its own TensorLake function in a separate container.

> **Note:** TensorLake functions are currently synchronous only (async support is landing in a few weeks). Since Google ADK's `InMemoryRunner` exposes an async API, each example wraps the async calls in `asyncio.run()` inside a sync TensorLake function. Once async functions are supported, the `asyncio.run()` wrapper can be removed and functions can be declared `async def` directly.

## Examples

| File | ADK Agent Type | What it demonstrates |
|------|---------------|---------------------|
| `basic_agent.py` | LLM Agent | Minimal agent with weather and time tools. Quickstart reference. |
| `llm_agent_example.py` | LLM Agent | Agent with multiple tools (capital cities, populations). Tools and agent each run in separate containers. |
| `sequential_agent_example.py` | Sequential Agent | Three-stage code pipeline (write &rarr; review &rarr; refactor). Each stage is a separate container, outputs flow forward. |
| `parallel_agent_example.py` | Parallel Agent | Three researcher agents run in truly parallel containers via `Future.wait()`, then a synthesis agent merges results. |

### How TensorLake maps to ADK agent types

| ADK Concept | TensorLake Equivalent |
|---|---|
| `Agent` (LlmAgent) | `@function(image=..., secrets=[...])` wrapping an ADK agent + `InMemoryRunner` |
| Agent tools | `@function()` decorated Python functions (each gets its own container) |
| `SequentialAgent` | Orchestrator function calling agent functions in order, passing outputs as inputs |
| `ParallelAgent` | `func.awaitable(...).run()` to launch futures + `Future.wait()` to collect results |
| `LoopAgent` | Python `for` loop in the orchestrator calling agent functions iteratively |
| `BaseAgent` (Custom) | Orchestrator function with arbitrary Python control flow (conditionals, loops, parallel stages) |

## Prerequisites

- Python 3.11+
- A [Google AI API key](https://aistudio.google.com/apikey) (for Gemini models)
- A [TensorLake account](https://cloud.tensorlake.ai) and API key

## Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install tensorlake google-adk

# Set your Google AI API key (required for local testing and as a TensorLake secret)
export GOOGLE_API_KEY=your_google_api_key

# Set your TensorLake API key (required for deploy and remote invocation)
export TENSORLAKE_API_KEY=your_tensorlake_api_key
```

## Test Locally

Each example can be run directly with TensorLake's local runner:

```bash
python basic_agent.py
python llm_agent_example.py
python sequential_agent_example.py
python parallel_agent_example.py
```

## Deploy to TensorLake

```bash
# Set the Google API key as a secret in your TensorLake project
tensorlake secrets set GOOGLE_API_KEY=your_google_api_key

# Deploy any example
tensorlake deploy basic_agent.py
tensorlake deploy llm_agent_example.py
tensorlake deploy sequential_agent_example.py
tensorlake deploy parallel_agent_example.py
```

## Test Deployed Applications

Once deployed, call applications via the TensorLake API. Each application takes a single string argument, sent as a raw JSON string:

```bash
# Basic agent
curl https://api.tensorlake.ai/applications/google_adk_basic_agent \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  --json '"What is the weather in New York?"'

# LLM agent
curl https://api.tensorlake.ai/applications/capital_agent \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  --json '"What is the capital of France and its population?"'

# Sequential pipeline
curl https://api.tensorlake.ai/applications/code_pipeline \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  --json '"Write a Python function that returns the top K most frequent elements from a list."'

# Parallel research
curl https://api.tensorlake.ai/applications/parallel_research \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  --json '"Latest sustainability technology trends"'
```

The API is async -- the response contains a `request_id`. Poll for the result:

```bash
# Check status
curl https://api.tensorlake.ai/applications/capital_agent/requests/$REQUEST_ID \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY"

# Get output (once status is "success")
curl https://api.tensorlake.ai/applications/capital_agent/requests/$REQUEST_ID/output \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY"
```

Or from Python (blocks until complete):

```python
from tensorlake.applications import run_remote_application

request = run_remote_application("capital_agent", query="What is the capital of Japan?")
print(request.output())
```

## How It Works

Every example follows the same architecture:

```
@application() entry point (orchestrator container)
    |
    |-- calls --> @function() LLM agent A (container A)
    |                 |-- calls --> @function() tool 1 (container 1)
    |                 |-- calls --> @function() tool 2 (container 2)
    |
    |-- calls --> @function() LLM agent B (container B)
    |                 |-- calls --> @function() tool 3 (container 3)
    ...
```

1. **Tools** are `@function()` decorated Python functions. Each runs in its own container.
2. **LLM agents** are `@function(image=..., secrets=[...])` functions that create an ADK `Agent` with an `InMemoryRunner` inside.
3. **The orchestrator** is the `@application()` entry point that chains agent functions together using sequential calls, `Future.wait()` for parallelism, or loops for iteration.

This gives you container-level isolation, independent scaling, and true distributed parallelism -- compared to ADK's built-in workflow agents which run everything in a single process.
