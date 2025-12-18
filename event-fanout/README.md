# Event Fan-Out System

Broadcast events to multiple notification channels with independent retry policies.

## What This Demonstrates

This example showcases Tensorlake's **parallel execution with per-channel reliability**:

| Feature | What it does |
|---------|--------------|
| `Future.wait()` with multiple futures | Fan-out to multiple channels simultaneously |
| Per-function `Retries` | Independent retry policies per channel |
| Per-function `timeout` | Different timeouts based on channel characteristics |
| Failure isolation | One channel failing doesn't affect others |

## Why This Would Be Hard Elsewhere

Building this traditionally would require:
- **Message broker** (RabbitMQ, Kafka, SQS) with multiple consumer groups
- **Worker pools** per notification channel
- **Circuit breakers** to handle channel failures
- **Dead letter queues** for retry management
- **Aggregation service** to track delivery status across channels
- **Different retry policies** implemented per consumer

With Tensorlake, each channel is a function with its own `retries` and `timeout` decorator.

## Architecture

```
                         ┌───────────────────┐
                         │    log_event()    │
                    ┌───▶│  retries=2, 10s   │───┐
                    │    │   (container 1)   │   │
                    │    └───────────────────┘   │
                    │    ┌───────────────────┐   │
                    │    │  send_webhook()   │   │
┌─────────────┐     ├───▶│  retries=3, 30s   │───┤     ┌─────────────┐
│broadcast_   │     │    │   (container 2)   │   │     │   Results   │
│   event     │─────┤    └───────────────────┘   ├────▶│ (combined)  │
│  (entry)    │     │    ┌───────────────────┐   │     │   status    │
└─────────────┘     │    │   store_audit()   │   │     └─────────────┘
                    ├───▶│  retries=2, 15s   │───┤
                    │    │   (container 3)   │   │
                    │    └───────────────────┘   │
                    │    ┌───────────────────┐   │
                    └───▶│ record_metrics()  │───┘
                         │  retries=1, 10s   │
                         │   (container 4)   │
                         └───────────────────┘

          All 4 channels run CONCURRENTLY with INDEPENDENT retry policies
```

## Channel Policies

| Channel | Retries | Timeout | Why |
|---------|---------|---------|-----|
| **log_event** | 2 | 10s | Logging should be fast; moderate retries |
| **send_webhook** | 3 | 30s | External services may be slow; more retries |
| **store_audit** | 2 | 15s | Compliance critical; moderate retries |
| **record_metrics** | 1 | 10s | Metrics are less critical; fail fast |

## Deploy

```bash
tensorlake deploy main.py
```

## Invoke

```bash
curl -X POST \
  "https://api.tensorlake.ai/v1/namespaces/<namespace>/applications/broadcast_event/invoke" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "user.signup",
    "message": "New user registered",
    "severity": "info",
    "data": {"user_id": "usr_123", "plan": "pro"},
    "tags": ["user", "signup"]
  }'
```

## Example Output

```json
{
  "event_id": "a1b2c3d4e5f6g7h8",
  "event_type": "user.signup",
  "broadcast_at": "2024-01-15T10:30:00.000000",
  "overall_status": "success",
  "channels": {
    "log": {
      "status": "success",
      "channel": "log",
      "timestamp": "2024-01-15T10:30:00.123456",
      "log_id": "log_a1b2c3d4e5f6g7h8"
    },
    "webhook": {
      "status": "success",
      "channel": "webhook",
      "timestamp": "2024-01-15T10:30:00.234567",
      "payload_bytes": 156,
      "delivery_id": "wh_a1b2c3d4e5f6g7h8"
    },
    "audit": {
      "status": "success",
      "channel": "audit",
      "timestamp": "2024-01-15T10:30:00.345678",
      "audit_id": "aud_a1b2c3d4e5f6g7h8"
    },
    "metrics": {
      "status": "success",
      "channel": "metrics",
      "timestamp": "2024-01-15T10:30:00.456789",
      "metrics_recorded": 3
    }
  },
  "summary": {
    "total_channels": 4,
    "successful": 4,
    "failed": 0
  }
}
```

## Key Code Patterns

### Per-Channel Retry Policies

```python
@function(retries=Retries(max_retries=3), timeout=30)  # External service
def send_webhook(event: Event, event_id: str) -> dict: ...

@function(retries=Retries(max_retries=1), timeout=10)  # Non-critical
def record_metrics(event: Event, event_id: str) -> dict: ...
```

### Parallel Fan-Out

```python
# All channels start immediately, run in parallel containers
log_future = log_event.awaitable(event, event_id).run()
webhook_future = send_webhook.awaitable(event, event_id).run()
audit_future = store_audit.awaitable(event, event_id).run()
metrics_future = record_metrics.awaitable(event, event_id).run()
```

### Safe Result Collection

```python
# Wait for all channels (including failed ones)
Future.wait(
    [log_future, webhook_future, audit_future, metrics_future],
    return_when=RETURN_WHEN.ALL_COMPLETED
)

# Collect results with error handling
results = {
    "log": safe_get_result(log_future),
    "webhook": safe_get_result(webhook_future),
    ...
}
```

## Real-World Extensions

In production, you might:
- **log_event**: Send to CloudWatch, Datadog, or Elasticsearch
- **send_webhook**: POST to Slack, Discord, PagerDuty
- **store_audit**: Write to PostgreSQL, S3, or compliance database
- **record_metrics**: Push to Prometheus, StatsD, or CloudWatch Metrics

Add secrets for external services:

```python
@function(
    retries=Retries(max_retries=3),
    timeout=30,
    secrets=["SLACK_WEBHOOK_URL"]
)
def send_slack(event: Event, event_id: str) -> dict:
    import os
    import requests
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    # ... send to Slack
```

## Local Testing

```bash
python main.py
```
