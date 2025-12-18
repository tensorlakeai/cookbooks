# Parallel Web Scraper

Scrape multiple websites in parallel with automatic retries and result aggregation.

## What This Demonstrates

This example showcases Tensorlake's **map-reduce** capabilities:

| Feature | What it does |
|---------|--------------|
| `function.map()` | Processes each URL in a separate container, in parallel |
| `function.reduce()` | Aggregates results incrementally as they arrive |
| `retries=Retries(max_retries=2)` | Automatic retry on individual failures |
| `timeout=30` | Per-function timeout control |

## Why This Would Be Hard Elsewhere

Building this traditionally would require:
- A **task queue** (Celery, SQS, RabbitMQ) to distribute URLs to workers
- A **worker pool** to process URLs in parallel
- **Retry logic** and dead letter queue handling
- A **result aggregation service** to combine outputs
- **Failure isolation** so one bad URL doesn't crash everything
- **Infrastructure** to scale workers based on load

With Tensorlake, it's just Python functions with decorators.

## Architecture

```
                                    ┌─────────────────┐
                                ┌──▶│  count_words()  │──┐
                                │   │  (container 1)  │  │
                                │   └─────────────────┘  │
┌─────────────┐   map()         │   ┌─────────────────┐  │   reduce()    ┌─────────────┐
│ scrape_sites│─────────────────┼──▶│  count_words()  │──┼──────────────▶│merge_counts │
│   (entry)   │                 │   │  (container 2)  │  │               │ (aggregate) │
└─────────────┘                 │   └─────────────────┘  │               └─────────────┘
                                │   ┌─────────────────┐  │
                                └──▶│  count_words()  │──┘
                                    │  (container N)  │
                                    └─────────────────┘
```

## Deploy

```bash
tensorlake deploy main.py
```

## Invoke

```bash
curl -X POST \
  "https://api.tensorlake.ai/v1/namespaces/<namespace>/applications/scrape_sites/invoke" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '["https://example.com", "https://httpbin.org/html", "https://jsonplaceholder.typicode.com"]'
```

## Example Output

```json
{
  "total_words": 1523,
  "sites_processed": 3,
  "successful": 3,
  "failed": 0,
  "results": [
    {
      "url": "https://example.com",
      "word_count": 256,
      "unique_words": 89,
      "top_words": [{"word": "example", "count": 5}, ...],
      "success": true
    },
    ...
  ]
}
```

## Key Code Patterns

### Parallel Processing with Map

```python
# This single line processes N URLs in N parallel containers
word_counts = count_words.map(urls)
```

### Incremental Aggregation with Reduce

```python
# Results are aggregated as they arrive, not waiting for all to complete
return merge_counts.reduce(word_counts, initial_state)
```

### Per-Function Reliability

```python
@function(timeout=30, retries=Retries(max_retries=2))
def count_words(url: str) -> dict:
    # Each URL gets independent timeout and retries
    # Failures don't affect other URLs
```

## Local Testing

```bash
python main.py
```
