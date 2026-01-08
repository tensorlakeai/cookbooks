# Personal Finance Manager

A Claude Agent-based personal finance manager that parses bank/credit card statements and answers questions about your spending.

## What it does

1. **Statement Analysis** (`finance_analyzer`): Upload a PDF statement, the agent extracts transactions, categorizes them, and saves to PostgreSQL
2. **Data Queries** (`finance_query`): Ask natural language questions, the agent writes SQL and can generate charts

## Prerequisites

- Python 3.11+
- Tensorlake account
- PostgreSQL database
- Anthropic API key

## Setup

```bash
# Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set your Tensorlake API key
export TENSORLAKE_API_KEY='your-key'

# Configure secrets
tensorlake secrets set "DATABASE_URL=postgresql://user:pass@host:5432/db"
tensorlake secrets set "ANTHROPIC_API_KEY=sk-ant-..."
```

## Deploy

```bash
tensorlake deploy app.py
```

## Test

**Upload a statement:**

```bash
curl https://api.tensorlake.ai/applications/finance_analyzer \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Accept: text/event-stream" \
  -F "file=@statement.pdf"
```

**Query your data:**

```bash
curl https://api.tensorlake.ai/applications/finance_query \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '"How much did I spend on groceries?"'
```

## Run Locally

```bash
export DATABASE_URL='postgresql://...'
export ANTHROPIC_API_KEY='sk-ant-...'
export TENSORLAKE_API_KEY='tl_apiKey_...'

python app.py statement.pdf
```

## Web App

A Next.js frontend is included in `web/`:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000 and enter your Tensorlake API key.

## Example Queries

- "What did I spend the most on last month?"
- "Show my top 5 merchants"
- "Draw a bar chart of spending by category"
- "List all transactions over $100"

## Files

- `app.py` - Tensorlake functions and Claude agents
- `config.py` - Container image configs
- `models.py` - Pydantic models
- `prompts.py` - Agent system prompts
- `web/` - Next.js dashboard
