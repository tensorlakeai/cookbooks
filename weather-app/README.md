# Weather Agent

A witty, conversational weather agent powered by Claude Opus 4.5. Ask about weather, plans, or why the sky does weird things.
Once deployed, the agent will be available as an HTTP API that you can integrate into any application.

## Local Developement

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Test Locally

```bash
# Run with Tensorlake's local runner
python tensorlake_app.py
```

Or test the agent directly:

```bash
python -c "
from agent import run_agent
print(run_agent('Will I need an umbrella in Seattle today?'))
"
```

## Deploy to Tensorlake

```bash
# Login to Tensorlake
tensorlake login
# or set a API Key of your Tensorlake Project
export TENSORLAKE_API_KEY=tl_xxx

# Set your Anthropic API key as a secret
tensorlake secrets set ANTHROPIC_API_KEY="sk-ant-..."

# Deploy
tensorlake deploy tensorlake_app.py
```
## Test with curl

Once deployed, test via the Tensorlake API:

```bash
curl -X POST https://api.tensorlake.ai/applications/handle_weather_query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '"Can I wear white sneakers tonight in San Francisco?"'
```

## How It Works

The agent uses Claude's built-in `web_search` and `web_fetch` tools to get real-time weather data, then reasons about what that weather means for your plans. No external weather API needed.
