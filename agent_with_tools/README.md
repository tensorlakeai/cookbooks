# Claude Agentic Loop with Tool Calling on Tensorlake

An agentic loop where Claude autonomously chains tool calls to answer a user query. Each tool runs as an isolated Tensorlake function (container), and the orchestration loop runs as a Tensorlake application.

The agent answers "What are the current weather alerts for my location?" by:
1. Calling `get_ip_address` to discover the server's public IP
2. Calling `get_location_info` to resolve the IP to a geographic location
3. Calling `get_weather_alerts` to fetch NWS alerts for that location
4. Returning a natural-language summary

## Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

```bash
pip install tensorlake anthropic httpx pydantic
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Run Locally

```bash
python app.py
```

## Deploy to Tensorlake

```bash
tensorlake deploy app.py
```

Once deployed, invoke via the Tensorlake HTTP API:

```bash
curl -X POST https://<your-endpoint>/agent_loop \
  -H "Content-Type: application/json" \
  -d '{"user_query": "What are the current weather alerts for my location?"}'
```
