"""
Event Fan-Out System - Demonstrates Parallel Notifications in Tensorlake

This example shows how to broadcast events to multiple channels concurrently,
with independent retry policies per channel. Each notification channel runs
in its own container, ensuring isolation and independent failure handling.

What this replaces if built traditionally:
- Message broker (RabbitMQ, Kafka) with multiple consumers
- Fan-out infrastructure with per-channel workers
- Circuit breakers and retry logic per notification service
- Dead letter queues for failed notifications
- Aggregation service to track delivery status
"""

from typing import Optional, List
from datetime import datetime
import json
import hashlib

from pydantic import BaseModel
from tensorlake.applications import application, function, Future, Retries, RETURN_WHEN


class Event(BaseModel):
    """Event to be broadcast to all channels."""
    type: str
    message: str
    severity: str = "info"  # info, warning, error, critical
    data: Optional[dict] = None
    tags: Optional[List[str]] = None


@application()
@function()
def broadcast_event(event: Event) -> dict:
    """
    Broadcast an event to multiple notification channels in parallel.

    Each channel runs in its own container with independent:
    - Retry policies
    - Timeouts
    - Resource allocation
    - Failure isolation

    If one channel fails, others still succeed.
    """
    timestamp = datetime.utcnow().isoformat()
    event_id = hashlib.sha256(f"{event.type}:{event.message}:{timestamp}".encode()).hexdigest()[:16]

    # Fan out to multiple notification channels - each in its own container
    # All channels start immediately and run in parallel
    log_future = log_event.awaitable(event, event_id).run()
    webhook_future = send_webhook.awaitable(event, event_id).run()
    audit_future = store_audit.awaitable(event, event_id).run()
    metrics_future = record_metrics.awaitable(event, event_id).run()

    # Wait for all channels to complete (or fail)
    # Using ALL_COMPLETED ensures we get status from every channel
    Future.wait(
        [log_future, webhook_future, audit_future, metrics_future],
        return_when=RETURN_WHEN.ALL_COMPLETED
    )

    # Collect results from each channel
    results = {
        "log": safe_get_result(log_future),
        "webhook": safe_get_result(webhook_future),
        "audit": safe_get_result(audit_future),
        "metrics": safe_get_result(metrics_future),
    }

    # Compute overall status
    statuses = [r.get("status") for r in results.values()]
    all_success = all(s == "success" for s in statuses)
    any_success = any(s == "success" for s in statuses)

    return {
        "event_id": event_id,
        "event_type": event.type,
        "broadcast_at": timestamp,
        "overall_status": "success" if all_success else ("partial" if any_success else "failed"),
        "channels": results,
        "summary": {
            "total_channels": len(results),
            "successful": sum(1 for s in statuses if s == "success"),
            "failed": sum(1 for s in statuses if s != "success"),
        }
    }


def safe_get_result(future: Future) -> dict:
    """Safely get result from a future, handling exceptions."""
    try:
        return future.result()
    except Exception as e:
        return {"status": "error", "error": str(e)}


@function(retries=Retries(max_retries=2), timeout=10)
def log_event(event: Event, event_id: str) -> dict:
    """
    Log event to structured logging system.

    In production, this might send to:
    - CloudWatch Logs
    - Datadog
    - Elasticsearch
    """
    timestamp = datetime.utcnow().isoformat()

    # Format log entry
    log_entry = {
        "timestamp": timestamp,
        "event_id": event_id,
        "level": event.severity.upper(),
        "type": event.type,
        "message": event.message,
        "data": event.data,
        "tags": event.tags,
    }

    # In production: send to logging service
    # For demo: print to stdout (which Tensorlake captures)
    print(f"[{log_entry['level']}] {json.dumps(log_entry)}")

    return {
        "status": "success",
        "channel": "log",
        "timestamp": timestamp,
        "log_id": f"log_{event_id}",
    }


@function(retries=Retries(max_retries=3), timeout=30)
def send_webhook(event: Event, event_id: str) -> dict:
    """
    Send event to external webhook endpoint.

    Has more retries (3) and longer timeout (30s) because
    external services may be slow or temporarily unavailable.

    In production, this would POST to configured webhook URLs.
    """
    timestamp = datetime.utcnow().isoformat()

    # Prepare webhook payload
    payload = {
        "event_id": event_id,
        "type": event.type,
        "severity": event.severity,
        "message": event.message,
        "data": event.data,
        "timestamp": timestamp,
    }

    # In production: actually call the webhook
    # webhook_url = os.getenv("WEBHOOK_URL")
    # response = requests.post(webhook_url, json=payload, timeout=25)

    # For demo: simulate webhook call
    payload_size = len(json.dumps(payload))

    return {
        "status": "success",
        "channel": "webhook",
        "timestamp": timestamp,
        "payload_bytes": payload_size,
        "delivery_id": f"wh_{event_id}",
    }


@function(retries=Retries(max_retries=2), timeout=15)
def store_audit(event: Event, event_id: str) -> dict:
    """
    Store event in audit trail for compliance.

    In production, this might write to:
    - PostgreSQL audit table
    - S3 with immutable retention
    - Blockchain-based audit log
    """
    timestamp = datetime.utcnow().isoformat()

    # Create audit record
    audit_record = {
        "audit_id": f"aud_{event_id}",
        "event_id": event_id,
        "event_type": event.type,
        "severity": event.severity,
        "message": event.message,
        "data_hash": hashlib.sha256(
            json.dumps(event.data or {}, sort_keys=True).encode()
        ).hexdigest()[:32],
        "recorded_at": timestamp,
        "tags": event.tags,
    }

    # In production: write to audit storage
    # For demo: simulate storage

    return {
        "status": "success",
        "channel": "audit",
        "timestamp": timestamp,
        "audit_id": audit_record["audit_id"],
        "data_hash": audit_record["data_hash"],
    }


@function(retries=Retries(max_retries=1), timeout=10)
def record_metrics(event: Event, event_id: str) -> dict:
    """
    Record event metrics for monitoring dashboards.

    Fewer retries (1) because metrics are less critical -
    better to fail fast than block the pipeline.

    In production, this might send to:
    - Prometheus
    - Datadog metrics
    - CloudWatch metrics
    """
    timestamp = datetime.utcnow().isoformat()

    # Define metrics to record
    metrics = [
        {
            "name": "events_total",
            "type": "counter",
            "value": 1,
            "labels": {
                "event_type": event.type,
                "severity": event.severity,
            }
        },
        {
            "name": "event_data_size_bytes",
            "type": "gauge",
            "value": len(json.dumps(event.data or {})),
            "labels": {
                "event_type": event.type,
            }
        }
    ]

    # Add tag-based metrics
    if event.tags:
        for tag in event.tags:
            metrics.append({
                "name": "events_by_tag",
                "type": "counter",
                "value": 1,
                "labels": {"tag": tag}
            })

    # In production: send to metrics backend
    # For demo: simulate metrics recording

    return {
        "status": "success",
        "channel": "metrics",
        "timestamp": timestamp,
        "metrics_recorded": len(metrics),
        "metric_names": [m["name"] for m in metrics],
    }


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    test_event = Event(
        type="user.signup",
        message="New user registered",
        severity="info",
        data={
            "user_id": "usr_123",
            "email": "test@example.com",
            "plan": "pro",
        },
        tags=["user", "signup", "pro-plan"]
    )

    request = run_local_application(broadcast_event, test_event)
    print(json.dumps(request.output(), indent=2))
