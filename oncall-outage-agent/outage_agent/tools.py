"""
Infrastructure and external service tools for the outage agent.
Includes log/metrics retrieval, deployment actions, web search, and dashboard capture.
"""

import os
from typing import Dict, Any

# Initialize external services (optional dependencies)
EXA_API_KEY = os.environ.get("EXA_API_KEY")
BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY")

# Try importing optional dependencies
exa = None
try:
    from exa_py import Exa
    if EXA_API_KEY:
        exa = Exa(EXA_API_KEY)
except ImportError:
    pass  # Exa not installed, web search will be unavailable

bb = None
try:
    from browserbase import Browserbase
    if BROWSERBASE_API_KEY:
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
except ImportError:
    pass  # Browserbase not installed, screenshot capture will be unavailable


# ----- Infrastructure tools (stubbed for demo) -----

def get_logs(service: str, minutes: int = 15) -> str:
    """
    Fetch recent logs for a service.
    
    TODO: Integrate with your log store (Datadog, Loki, CloudWatch, etc.)
    For now, returns simulated log data.
    
    Args:
        service: Service name to fetch logs for
        minutes: Number of minutes of logs to retrieve
    
    Returns:
        Log data as a string
    """
    # Simulated log data for demo
    sample_logs = f"""
[2025-12-16 22:45:12] ERROR {service} - KafkaTimeoutException: Failed to commit offset
[2025-12-16 22:46:03] ERROR {service} - Connection pool exhausted, waiting for available connection
[2025-12-16 22:47:21] ERROR {service} - Request timeout after 5000ms
[2025-12-16 22:48:45] WARN {service} - High memory usage: 87%
[2025-12-16 22:50:12] ERROR {service} - Database connection failed, retrying...
    """.strip()
    
    return f"[LOGS for {service} - last {minutes} minutes]\n{sample_logs}"


def get_metrics(service: str, minutes: int = 15) -> Dict[str, Any]:
    """
    Fetch recent metrics for a service.
    
    TODO: Integrate with your metrics system (Prometheus, Grafana, Datadog, etc.)
    For now, returns simulated metrics.
    
    Args:
        service: Service name to fetch metrics for
        minutes: Time window in minutes
    
    Returns:
        Dictionary of metric values
    """
    # Simulated metrics for demo
    return {
        "service": service,
        "time_window_minutes": minutes,
        "error_rate": 0.23,
        "p50_latency_ms": 450,
        "p95_latency_ms": 820,
        "p99_latency_ms": 1450,
        "request_count": 12400,
        "success_rate": 0.77,
        "cpu_usage_percent": 78,
        "memory_usage_percent": 87,
        "active_connections": 245,
    }


def rollback_deploy(service: str, target_version: str) -> str:
    """
    Rollback a service deployment to a previous version.
    
    TODO: Integrate with your deployment system (Argo CD, GitHub Actions, Jenkins, etc.)
    For now, simulates the rollback action.
    
    Args:
        service: Service name to rollback
        target_version: Version to rollback to
    
    Returns:
        Status message
    """
    return f"✅ Successfully rolled back {service} from current version to {target_version}"


def restart_service(service: str) -> str:
    """
    Restart a service (e.g., restart pods in Kubernetes).
    
    TODO: Integrate with your container orchestration (Kubernetes, Docker Swarm, ECS, etc.)
    For now, simulates the restart action.
    
    Args:
        service: Service name to restart
    
    Returns:
        Status message
    """
    return f"✅ Successfully restarted {service} - all pods are now running with fresh state"


def send_slack_message(channel: str, text: str) -> None:
    """
    Send a message to a Slack channel.
    
    TODO: Integrate with Slack API using slack-sdk
    For now, prints to console.
    
    Args:
        channel: Slack channel name
        text: Message content
    """
    print(f"\n{'='*80}")
    print(f"[SLACK MESSAGE to {channel}]")
    print(f"{'-'*80}")
    print(text)
    print(f"{'='*80}\n")


# ----- Exa search for known issues -----

def search_web_for_error(error_text: str) -> str:
    """
    Search the web for known issues related to an error message.
    Uses Exa to find relevant documentation, Stack Overflow posts, GitHub issues, etc.
    
    Args:
        error_text: Error message or description to search for
    
    Returns:
        Formatted string with search results
    """
    if not exa or not EXA_API_KEY:
        return "[Exa web search unavailable - EXA_API_KEY not configured]"
    
    try:
        res = exa.search_and_contents(
            error_text,
            type="auto",
            text=True,
            num_results=3,
        )
        
        if not res.results:
            return f"No web results found for: {error_text}"
        
        snippets = []
        for r in res.results:
            snippet = f"**{r.title}**\nURL: {r.url}\n{r.text[:500]}..."
            snippets.append(snippet)
        
        return "\n\n---\n\n".join(snippets)
    
    except Exception as e:
        return f"[Error during web search: {str(e)}]"


# ----- Browserbase for dashboard screenshots -----

def capture_dashboard_screenshot(url: str) -> str:
    """
    Capture a screenshot of a monitoring dashboard (e.g., Grafana).
    Uses Browserbase + Playwright/Stagehand.
    
    TODO: Implement full Browserbase integration with Playwright
    For now, returns a placeholder.
    
    Args:
        url: URL of the dashboard to capture
    
    Returns:
        URL or path to the screenshot
    """
    if not bb or not BROWSERBASE_API_KEY:
        return f"[Dashboard screenshot unavailable - BROWSERBASE_API_KEY not configured]\nTarget URL: {url}"
    
    # In a real implementation:
    # 1. Create a Browserbase session
    # 2. Use Playwright to navigate to the URL
    # 3. Wait for dashboard to load
    # 4. Take screenshot
    # 5. Upload to cloud storage
    # 6. Return public URL
    
    return f"[Screenshot captured for: {url}]"
