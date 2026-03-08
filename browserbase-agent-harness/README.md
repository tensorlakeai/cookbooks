# Browserbase Agent Harness on Tensorlake

A Tensorlake cookbook that combines:
- Agentic querying (tool-calling loop)
- Browserbase browser automation for web exploration
- OpenAI Agents SDK tool orchestration
- Elasticsearch ingestion for run artifacts

Given a `query` and a `website`, the app:
1. Runs multi-variation site search via the website search bar
2. Uses Browserbase tools to fetch and inspect pages
3. Downloads documents/files, unzips archives, and parses docs to markdown with foundation models
4. Synthesizes a grounded answer
5. Optionally writes visited pages + final answer to Elasticsearch

## Architecture

`browserbase_agentic_query` is the Tensorlake application entrypoint. It orchestrates tools implemented as separate Tensorlake functions:

- `browser_fetch_page`: open a URL in Browserbase and extract title/text/links
- `browser_find_relevant_snippets`: extract query-specific snippets from a page
- `browser_search_site`: use the site's search UI for query discovery
  - On CMS, it targets the hero search bar (`#hero-search-input`) and submit button, then ranks true result rows by query relevance.
- `download_file`: download files for analysis
- `extract_archive`: unzip/tar extraction for downloaded archives
- `document_to_markdown`: foundation-model markdown conversion of extracted docs
- `elasticsearch_index_note`: store agent findings
- `elasticsearch_search_notes`: query indexed data
- `elasticsearch_bulk_ingest_pages`: bulk index visited pages (called automatically at end)

## Files

- `app.py`: Tensorlake app, agent harness, browser tools, Elasticsearch tools
- `requirements.txt`: Local development dependencies

## Prerequisites

- Tensorlake account/API key
- OpenAI API key
- Browserbase API key + project ID
- Elasticsearch URL + API key

## Environment Variables

```bash
export TENSORLAKE_API_KEY="tl_..."
export OPENAI_API_KEY="sk-..."
export BROWSERBASE_API_KEY="bb_..."
export BROWSERBASE_PROJECT_ID="proj_..."
export ELASTICSEARCH_URL="https://<cluster>.es.<region>.aws.elastic-cloud.com:443"
export ELASTIC_API_KEY="<base64-api-key>"
```

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy to Tensorlake

```bash
tensorlake deploy app.py
```

## Invoke with curl

```bash
curl -X POST https://api.tensorlake.ai/applications/browserbase_agentic_query \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does Browserbase session management work?",
    "website": "https://docs.browserbase.com/introduction",
    "max_iterations": 8,
    "max_pages": 6,
    "enable_elasticsearch": false,
    "enable_tracing": true,
    "max_trace_events": 300,
    "elasticsearch_index": "browserbase_agent_runs"
  }'
```

## Response Shape

The app returns:
- `answer`: final synthesized answer
- `evidence_urls`: pages explored by the agent
- `citations`: URL/title citation list
- `search_evidence`: structured evidence from search/snippet tools (queries, top hits, raw search observations)
- `cached_files`: file IDs produced by download/unzip tools
- `elasticsearch`: ingestion status (enabled/disabled + results)
- `traces`: detailed run events with redacted secrets

## Progress Streaming

The app emits frequent progress updates through Tensorlake progress events:
- run initialization + query-variation planning
- each search variation and prefetch step
- each agent tool completion (search/fetch/snippet/download/unzip/markdown)
- Elasticsearch ingestion stage (or skip)
- completion summary

Use Tensorlake's progress streaming API for live updates:
- docs: https://docs.tensorlake.ai/applications/guides/streaming-progress
