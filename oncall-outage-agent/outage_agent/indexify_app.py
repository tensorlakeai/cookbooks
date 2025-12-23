"""
Tensorlake / Indexify application graph.
Defines the workflow for handling production outages.
"""

from typing import Dict, Any
# Uncomment when ready to deploy to Indexify
# from tensorlake.applications import application, function
# from indexify import RemoteGraph

from .agent import run_outage_agent


# Tensorlake application decorator
# Uncomment for production deployment to Indexify
# @application()
# @function()
def handle_outage(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Entry point for the Tensorlake / Indexify workflow.
    
    This function is invoked via HTTP when an alert fires from:
    - Prometheus / Alertmanager
    - Grafana
    - PagerDuty
    - Custom monitoring systems
    
    Args:
        alert: Alert payload containing:
            - service: Service name
            - severity: Alert severity (critical, warning, info)
            - summary: Human-readable alert description
            - timestamp: When the alert fired
            - labels: Additional metadata (env, region, team, etc.)
    
    Returns:
        Incident record with diagnosis and actions taken
    """
    # Delegate to the LangChain-based agent
    result = run_outage_agent(alert)
    return result


# For local testing
if __name__ == "__main__":
    print("Testing Tensorlake outage handler locally...\n")
    
    # Simulate a realistic production alert
    fake_alert = {
        "service": "payments-api",
        "severity": "critical",
        "summary": "High error rate on /charge endpoint - KafkaTimeoutException",
        "timestamp": "2025-12-16T22:45:00Z",
        "labels": {
            "env": "production",
            "region": "us-central1",
            "team": "payments",
            "cluster": "prod-k8s-1",
        },
    }
    
    print("=" * 80)
    print("SIMULATING PRODUCTION ALERT")
    print("=" * 80)
    print(f"Alert: {fake_alert}")
    print("=" * 80 + "\n")
    
    # Process the alert
    result = handle_outage(fake_alert)
    
    print("\n" + "=" * 80)
    print("RESULT")
    print("=" * 80)
    import json
    print(json.dumps(result, indent=2))
    print("=" * 80)
