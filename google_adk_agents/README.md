# Google ADK Agents on TensorLake

Run [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/agents/) agents as serverless applications on TensorLake. Each example demonstrates a different ADK agent type, rewritten so that every LLM agent and tool runs as its own TensorLake function in a separate container.

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
- A [TensorLake account](https://cloud.tensorlake.ai)

## Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install tensorlake google-adk
```

## Test Locally

Each example can be run directly with TensorLake's local runner:

```bash
# Basic agent
python basic_agent.py

# LLM agent with tools
python llm_agent_example.py

# Sequential pipeline
python sequential_agent_example.py

# Parallel research agents
python parallel_agent_example.py
```

For local execution, set your Google AI API key as an environment variable:

```bash
export GOOGLE_API_KEY=your_api_key_here
```

## Deploy to TensorLake

```bash
# Set your TensorLake API key
export TENSORLAKE_API_KEY=your_tensorlake_api_key

# Set the Google API key as a secret in your TensorLake project
tensorlake secrets set GOOGLE_API_KEY=your_google_api_key

# Deploy any example
tensorlake deploy basic_agent.py
tensorlake deploy llm_agent_example.py
tensorlake deploy sequential_agent_example.py
tensorlake deploy parallel_agent_example.py
```

## Test Deployed Applications

Once deployed, call applications via the TensorLake API:

```bash
# Basic agent
curl -X POST https://api.tensorlake.ai/applications/google_adk_basic_agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '{"query": "What is the weather in New York?", "user_id": "user1", "session_id": "sess1"}'

# LLM agent
curl -X POST https://api.tensorlake.ai/applications/capital_agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '{"query": "What is the capital of France and its population?"}'

# Sequential pipeline
curl -X POST https://api.tensorlake.ai/applications/code_pipeline \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '{"specification": "Write a Python function that returns the top K most frequent elements from a list."}'

# Parallel research
curl -X POST https://api.tensorlake.ai/applications/parallel_research \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '{"topic": "Latest sustainability technology trends"}'
```

Or from Python:

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
