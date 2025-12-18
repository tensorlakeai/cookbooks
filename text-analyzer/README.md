# Multi-Model Text Analyzer

Run multiple text analyses concurrently using parallel Futures.

## What This Demonstrates

This example showcases Tensorlake's **Futures** for parallel execution:

| Feature | What it does |
|---------|--------------|
| `function.awaitable().run()` | Starts a function in a separate container without blocking |
| `Future.wait()` | Waits for multiple concurrent operations to complete |
| Per-function resources | Each analysis gets independent CPU/memory allocation |

## Why This Would Be Hard Elsewhere

Building this traditionally would require:
- **Multiple microservices** for each analysis type (sentiment, keywords, etc.)
- **An orchestration layer** to fan-out requests and collect responses
- **asyncio/threading** code to manage concurrent HTTP calls
- **Service discovery** and load balancing
- **Timeout and error handling** per service
- **Result aggregation** logic

With Tensorlake, each analysis is just a decorated function, and parallelism is one line.

## Architecture

```
                    ┌───────────────────────┐
                    │  analyze_sentiment()  │───┐
                    │    (container 1)      │   │
                    └───────────────────────┘   │
                    ┌───────────────────────┐   │
┌─────────────┐     │ compute_statistics()  │   │     ┌─────────────┐
│analyze_text │────▶│    (container 2)      │───┼────▶│   Results   │
│   (entry)   │     └───────────────────────┘   │     │ (combined)  │
└─────────────┘     ┌───────────────────────┐   │     └─────────────┘
      │             │  extract_keywords()   │   │           ▲
      │             │    (container 3)      │───┤           │
      │             └───────────────────────┘   │      Future.wait()
      │             ┌───────────────────────┐   │
      └────────────▶│ compute_readability() │───┘
                    │    (container 4)      │
                    └───────────────────────┘

        All 4 analyses run CONCURRENTLY in separate containers
```

## Deploy

```bash
tensorlake deploy main.py
```

## Invoke

```bash
curl -X POST \
  "https://api.tensorlake.ai/v1/namespaces/<namespace>/applications/analyze_text/invoke" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '"Tensorlake is a fantastic platform for building distributed applications. It provides excellent abstractions that make complex workflows simple."'
```

## Example Output

```json
{
  "sentiment": {
    "label": "positive",
    "score": 0.857,
    "positive_count": 6,
    "negative_count": 1,
    "positive_words_found": ["fantastic", "excellent", "simple"]
  },
  "statistics": {
    "characters": 142,
    "words": 19,
    "unique_words": 18,
    "sentences": 2,
    "avg_word_length": 6.32,
    "avg_sentence_length": 9.5
  },
  "keywords": [
    {"word": "tensorlake", "count": 1, "tf_score": 0.0526},
    {"word": "distributed", "count": 1, "tf_score": 0.0526},
    {"word": "applications", "count": 1, "tf_score": 0.0526}
  ],
  "readability": {
    "flesch_reading_ease": 42.3,
    "flesch_kincaid_grade": 11.2,
    "interpretation": "fairly difficult (10th-12th grade)"
  }
}
```

## Key Code Patterns

### Starting Parallel Operations

```python
# Each .run() starts a function in its own container immediately
sentiment_future = analyze_sentiment.awaitable(text).run()
stats_future = compute_statistics.awaitable(text).run()
keywords_future = extract_keywords.awaitable(text).run()
```

### Waiting for Results

```python
# Wait for all containers to finish
Future.wait([sentiment_future, stats_future, keywords_future])

# Collect results
return {
    "sentiment": sentiment_future.result(),
    "statistics": stats_future.result(),
    "keywords": keywords_future.result(),
}
```

### Independent Resource Allocation

```python
# Each function can have different resource requirements
@function(cpu=1, memory=1)
def analyze_sentiment(text: str) -> dict: ...

@function(cpu=2, memory=4)  # More resources for heavier computation
def run_ml_model(text: str) -> dict: ...
```

## Real-World Extensions

In production, you might:
- Use actual ML models (BERT, GPT) for sentiment analysis
- Add GPU support: `@function(gpu="T4")`
- Use different container images per function with specific dependencies
- Add more analysis types (entity extraction, language detection, etc.)

## Local Testing

```bash
python main.py
```
