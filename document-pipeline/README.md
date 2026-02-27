# Fund Analysis Pipeline

A Tensorlake application that extracts structured financial data from multi-fund annual reports using the Claude Agent SDK. Inspired by the [LlamaExtract Fidelity fund analysis example](https://github.com/run-llama/llama_cloud_services/blob/main/examples/extract/asset_manager_fund_analysis.ipynb).

## Architecture

```
fund_analysis  (Tensorlake @application — orchestrator)
├── parse_document       (@function — Tensorlake DocumentAI)
├── find_fund_splits     (@function — Claude structured output)
├── extract_fund_data    (@function — Claude Agent SDK, ×N in parallel)
└── analyze_funds        (@function — Claude Agent SDK, optional)
```

### Pipeline Steps

1. **Parse** — Tensorlake DocumentAI converts the PDF into page-level markdown
2. **Split** — Claude identifies fund sections from the table of contents, then tags each page to its fund (same two-phase approach as the original)
3. **Extract** — A Claude agent extracts 17+ structured fields per fund (FundData schema) — all funds run in parallel via Tensorlake Futures
4. **Analyze** (optional) — A Claude agent with Python execution answers natural language queries over the extracted data

## Setup

```bash
pip install -r requirements.txt
```

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export TENSORLAKE_API_KEY="tl-..."
```

## Usage

### Local Testing

```bash
# Basic extraction
python app.py data/fidelity_fund.pdf

# With analysis query
ANALYSIS_QUERY="Which funds have the highest allocation drift from their target?" \
  python app.py data/fidelity_fund.pdf

# Custom split detection
SPLIT_DESCRIPTION="Find and split by the main funds" \
SPLIT_KEY="fidelity_asset_manager" \
  python app.py data/fidelity_fund.pdf
```

### Deploy to Tensorlake

```bash
tensorlake deploy app.py
```

### API Usage

```bash
curl https://api.tensorlake.ai/applications/fund_analysis \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -F "file=@report.pdf"
```

## FundData Schema

Each fund section yields these fields:

| Field | Type | Description |
|-------|------|-------------|
| `fund_name` | str | Full fund name as it appears |
| `target_equity_pct` | int | Target equity % from fund name |
| `report_date` | str | Report date (YYYY-MM-DD) |
| `equity_pct` | float | Actual equity allocation % |
| `fixed_income_pct` | float | Fixed income allocation % |
| `money_market_pct` | float | Money market allocation % |
| `nav` | float | Net Asset Value per share |
| `net_assets_usd` | float | Total net assets (USD) |
| `expense_ratio` | float | Expense ratio % |
| `one_year_return` | float | 1-year total return % |
| `portfolio_turnover` | float | Turnover rate % |
| `equity_futures_notional` | float | Equity futures notional (USD) |
| `bond_futures_notional` | float | Bond futures notional (USD) |
| `net_investment_income` | float | Net investment income (USD) |
| `total_distributions` | float | Distributions to shareholders (USD) |
| `net_asset_change` | float | Net change in assets (USD) |

## Stack Comparison

| Component | Original (LlamaCloud) | This Pipeline |
|-----------|----------------------|---------------|
| PDF parsing | LlamaParse | Tensorlake DocumentAI |
| LLM (splitting) | OpenAI gpt-4.1 | Claude (structured output) |
| Extraction | LlamaExtract | Claude Agent SDK |
| Orchestration | LlamaIndex Workflow | Tensorlake @application |
| Parallelism | `run_jobs()` | Tensorlake Futures |
| Analysis | PandasQueryEngine | Claude agent + Python execution |
| Deployment | N/A | `tensorlake deploy` |
