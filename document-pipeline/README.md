# Document Processing Pipeline

A multi-stage document processing pipeline with different resources at each stage.

## What This Demonstrates

This example showcases Tensorlake's **multi-stage workflow** capabilities:

| Feature | What it does |
|---------|--------------|
| Sequential `map()` calls | Chain multiple processing stages |
| Per-function resources | Different CPU/memory at each stage |
| Durability | Resume from last successful stage on failure |
| Stage isolation | Each stage runs in its own container type |

## Why This Would Be Hard Elsewhere

Building this traditionally would require:
- **Workflow orchestration** (Airflow, Step Functions)
- **Multiple Kubernetes deployments** with different resource specifications
- **Message queues** for inter-stage communication
- **State management** to track pipeline progress
- **Checkpointing logic** to resume from failures
- **Separate scaling policies** per stage

With Tensorlake, it's just a sequence of `map()` calls with resource decorators.

## Pipeline Architecture

```
                Stage 1              Stage 2              Stage 3              Stage 4
               (1 CPU, 1GB)        (2 CPU, 2GB)         (1 CPU, 1GB)        (2 CPU, 2GB)
              ┌───────────┐       ┌─────────────┐       ┌──────────┐       ┌───────────┐
    URLs ────▶│ download  │──────▶│parse_content│──────▶│  enrich  │──────▶│ summarize │────▶ Results
              │   .map()  │       │   .map()    │       │  .map()  │       │   .map()  │
              └───────────┘       └─────────────┘       └──────────┘       └───────────┘
                   │                    │                    │                   │
                   ▼                    ▼                    ▼                   ▼
              N containers         N containers         N containers        N containers
               (parallel)          (parallel)           (parallel)          (parallel)

    Each stage processes N documents in parallel, then passes to next stage.
    Different resource allocations optimize cost and performance per stage.
```

## Pipeline Stages

| Stage | Purpose | Resources | Why |
|-------|---------|-----------|-----|
| **download** | Fetch URLs | 1 CPU, 1GB | I/O bound, lightweight |
| **parse_content** | Extract structure | 2 CPU, 2GB | CPU-intensive parsing |
| **enrich** | Add metadata | 1 CPU, 1GB | Simple computations |
| **summarize** | Generate summary | 2 CPU, 2GB | Could use ML models |

## Deploy

```bash
tensorlake deploy main.py
```

## Invoke

```bash
curl -X POST \
  "https://api.tensorlake.ai/v1/namespaces/<namespace>/applications/process_documents/invoke" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '["https://example.com", "https://httpbin.org/html"]'
```

## Example Output

```json
[
  {
    "url": "https://example.com",
    "success": true,
    "title": "Example Domain",
    "summary": "This domain is for use in illustrative examples in documents...",
    "metadata": {
      "word_count": 83,
      "sentence_count": 5,
      "estimated_reading_time_mins": 0.4,
      "detected_language": "en"
    },
    "structure": {
      "sections": ["Example Domain", "More information"],
      "links_count": 1
    }
  }
]
```

## Key Code Patterns

### Chained Map Operations

```python
def process_documents(urls: List[str]) -> List[dict]:
    # Each map() creates N parallel containers
    # Stages execute sequentially, items within stages execute in parallel
    documents = download.map(urls)      # Stage 1: N parallel downloads
    parsed = parse_content.map(documents)  # Stage 2: N parallel parses
    enriched = enrich.map(parsed)       # Stage 3: N parallel enrichments
    summaries = summarize.map(enriched)  # Stage 4: N parallel summaries
    return summaries
```

### Resource Allocation Per Stage

```python
@function(cpu=1, memory=1, timeout=60)  # Light container for I/O
def download(url: str) -> dict: ...

@function(cpu=2, memory=2, timeout=120)  # Heavy container for parsing
def parse_content(doc: dict) -> dict: ...
```

### Durability

If `parse_content` fails for one document:
- Other documents continue processing
- Failed document can be retried without re-downloading
- Progress is preserved (Tensorlake handles checkpointing)

## Real-World Extensions

In production, you might:
- **Download stage**: Add authentication, handle different protocols (S3, GCS)
- **Parse stage**: Use proper parsers (PDF, DOCX, HTML) with heavy dependencies
- **Enrich stage**: Call external APIs (entity extraction, classification)
- **Summarize stage**: Integrate with LLMs (OpenAI, Anthropic)

Each stage can have its own container image with specific dependencies:

```python
parse_image = Image().run("pip install beautifulsoup4 pdfplumber")
llm_image = Image().run("pip install openai")

@function(image=parse_image, cpu=2, memory=4)
def parse_content(doc: dict) -> dict: ...

@function(image=llm_image, secrets=["OPENAI_API_KEY"])
def summarize(doc: dict) -> dict: ...
```

## Local Testing

```bash
python main.py
```
