"""
LangChain agent orchestration using Groq LLM.
Core reasoning and decision-making for incident response.
"""

import os
import json
from typing import Dict, Any
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from langchain.agents import create_tool_calling_agent, AgentExecutor

from .tools import (
    get_logs,
    get_metrics,
    rollback_deploy,
    restart_service,
    send_slack_message,
    search_web_for_error,
    capture_dashboard_screenshot,
)
from .memory import add_incident_record, find_similar_incidents

# Get API key from environment
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


# ----- Wrap tools for LangChain -----

@tool
def lc_get_logs(service: str, minutes: int = 15) -> str:
    """Fetch recent logs for a service to understand what's happening."""
    return get_logs(service, minutes)


@tool
def lc_get_metrics(service: str, minutes: int = 15) -> str:
    """Fetch recent metrics (error rate, latency, resource usage) for a service."""
    metrics = get_metrics(service, minutes)
    return json.dumps(metrics, indent=2)


@tool
def lc_rollback_deploy(service: str, target_version: str) -> str:
    """Rollback a service deployment to a previous stable version. Use this when a recent deployment caused issues."""
    return rollback_deploy(service, target_version)


@tool
def lc_restart_service(service: str) -> str:
    """Restart a service to clear transient issues. Use this for connection pool exhaustion, memory leaks, or stuck processes."""
    return restart_service(service)


@tool
def lc_search_web(error_text: str) -> str:
    """Search the web for known issues, solutions, and documentation related to an error message."""
    return search_web_for_error(error_text)


@tool
def lc_capture_dashboard(url: str) -> str:
    """Capture a screenshot of a monitoring dashboard (like Grafana) for the incident report."""
    return capture_dashboard_screenshot(url)


# All available tools
TOOLS = [
    lc_get_logs,
    lc_get_metrics,
    lc_rollback_deploy,
    lc_restart_service,
    lc_search_web,
    lc_capture_dashboard,
]


def build_llm():
    """
    Initialize the Groq LLM for agent reasoning.
    Using Mixtral-8x7b for fast, high-quality inference.
    """
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY not found in environment. "
            "Please set it in .env file or export it."
        )
    
    return ChatGroq(
        groq_api_key=GROQ_API_KEY,
        model_name="llama-3.3-70b-versatile",  # Fast, capable model
        temperature=0.1,  # Low temperature for more deterministic responses
    )


def run_outage_agent(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main orchestrator for incident response.
    
    Given an alert, this function:
    1. Retrieves similar past incidents from memory
    2. Initializes the LangChain agent with tools
    3. Prompts the agent to diagnose and potentially fix the issue
    4. Stores the incident record for future learning
    5. Sends notifications
    
    Args:
        alert: Alert payload containing service, severity, summary, etc.
    
    Returns:
        Incident record with diagnosis and actions taken
    """
    # Extract alert details
    service = alert.get("service", "unknown-service")
    summary = alert.get("summary", "")
    severity = alert.get("severity", "unknown")
    timestamp = alert.get("timestamp", "")
    labels = alert.get("labels", {})
    
    print(f"\n{'='*80}")
    print(f"🚨 INCIDENT ALERT RECEIVED")
    print(f"{'='*80}")
    print(f"Service: {service}")
    print(f"Severity: {severity}")
    print(f"Summary: {summary}")
    print(f"Timestamp: {timestamp}")
    print(f"Labels: {labels}")
    print(f"{'='*80}\n")
    
    # Fetch similar past incidents
    similar_incidents = find_similar_incidents(service, summary)
    
    if similar_incidents:
        print(f"[MEMORY] Found {len(similar_incidents)} similar past incident(s)")
    else:
        print("[MEMORY] No similar past incidents found - this is new")
    
    # Initialize LLM
    llm = build_llm()

    # Create Prompt Template
    prompt = ChatPromptTemplate.from_messages([
        ("system", """
You are an expert Site Reliability Engineer (SRE) and on-call incident responder.

Your job is to:
1. Diagnose production incidents quickly and accurately
2. Decide whether to take automated remediation actions
3. Execute fixes when safe and appropriate
4. Escalate to humans when uncertain or risky

You have access to tools to:
- Inspect logs and metrics
- Search the web for known issues
- Rollback deployments
- Restart services
- Capture dashboard screenshots

CRITICAL GUIDELINES:
- Always investigate before acting (check logs, metrics)
- Only take automated actions if you're highly confident
- For critical severity, prefer safe actions (rollback > restart > escalate)
- For unknown issues or low confidence, escalate to humans
- Document your reasoning clearly

Your final response must be valid JSON with this structure:
{{
  "root_cause": "Brief explanation of what went wrong",
  "confidence": 0.85,
  "actions_taken": ["rollback to v1.2.3", "restarted service"],
  "should_escalate": false,
  "short_summary": "One-line summary for humans",
  "recommendations": "What to do next or prevent this in future"
}}
"""),
        ("human", """
ALERT DETAILS:
Service: {service}
Severity: {severity}
Summary: {summary}
Timestamp: {timestamp}
Labels: {labels}

SIMILAR PAST INCIDENTS:
{similar_context}

TASK:
1. Use tools to investigate (logs, metrics, web search)
2. Determine the root cause
3. Decide if automated action is safe
4. If safe AND high confidence, execute the fix (rollback or restart)
5. Respond with JSON only (no extra text)
"""),
        ("placeholder", "{agent_scratchpad}"),
    ])
    
    # Construct the agent
    agent = create_tool_calling_agent(llm, TOOLS, prompt)
    agent_executor = AgentExecutor(
        agent=agent, 
        tools=TOOLS, 
        verbose=True, 
        max_iterations=10,
        handle_parsing_errors=True
    )
    
    # Construct the user prompt inputs
    similar_context = "None"
    if similar_incidents:
        similar_context = ""
        for idx, inc in enumerate(similar_incidents, 1):
            similar_context += f"\n{idx}. Service: {inc.get('service')}"
            similar_context += f"\n   Error: {inc.get('error_snippet', 'N/A')}"
            similar_context += f"\n   Actions: {inc.get('actions_taken', 'N/A')}"
            similar_context += f"\n   Outcome: {inc.get('outcome', 'N/A')}\n"
    
    # Run the agent
    print("\n[AGENT] Starting diagnosis and response...\n")
    
    try:
        # Invoke the agent
        response_dict = agent_executor.invoke({
            "service": service,
            "severity": severity,
            "summary": summary,
            "timestamp": timestamp,
            "labels": json.dumps(labels, indent=2),
            "similar_context": similar_context
        })
        
        response = response_dict.get("output", "")
        
        # Try to parse JSON from response
        try:
            # Extract JSON if it's wrapped in text
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            
            llm_output = json.loads(response)
        except json.JSONDecodeError:
            # If parsing fails, wrap the raw response
            llm_output = {
                "root_cause": "Unable to parse LLM response",
                "confidence": 0.5,
                "actions_taken": [],
                "should_escalate": True,
                "short_summary": response[:200],
                "raw_response": response,
            }
    
    except Exception as e:
        print(f"\n[ERROR] Agent execution failed: {str(e)}\n")
        llm_output = {
            "root_cause": f"Agent error: {str(e)}",
            "confidence": 0.0,
            "actions_taken": [],
            "should_escalate": True,
            "short_summary": "Agent failed to complete analysis",
        }
    
    # Build incident record
    incident_record = {
        "service": service,
        "severity": severity,
        "alert_summary": summary,
        "timestamp": timestamp,
        "labels": labels,
        "error_snippet": summary[:200],
        "root_cause": llm_output.get("root_cause", "Unknown"),
        "confidence": llm_output.get("confidence", 0.0),
        "actions_taken": llm_output.get("actions_taken", []),
        "should_escalate": llm_output.get("should_escalate", True),
        "llm_output": llm_output,
    }
    
    # Store in memory for future incidents
    add_incident_record(incident_record)
    
    # Send Slack notification
    slack_message = f"""
🚨 **INCIDENT AUTO-RESPONSE**

**Service:** {service}
**Severity:** {severity}
**Summary:** {summary}

**Root Cause:** {llm_output.get('root_cause', 'Unknown')}
**Confidence:** {llm_output.get('confidence', 0.0):.0%}

**Actions Taken:**
{chr(10).join(f'- {action}' for action in llm_output.get('actions_taken', ['None']))}

**Escalate to Human:** {'⚠️ YES' if llm_output.get('should_escalate') else '✅ NO - Handled automatically'}

**Recommendations:** {llm_output.get('recommendations', 'None')}
""".strip()
    
    send_slack_message(channel="#oncall", text=slack_message)
    
    print(f"\n{'='*80}")
    print("✅ INCIDENT PROCESSING COMPLETE")
    print(f"{'='*80}\n")
    
    return incident_record
