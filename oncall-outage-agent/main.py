from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import Dict, Optional
from datetime import datetime

from outage_agent.indexify_app import handle_outage

app = FastAPI(
    title="On-Call Outage Agent",
    description="Automatically diagnose and fix production incidents powered by Tensorlake",
    version="0.1.0",
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


class Alert(BaseModel):
    """Alert payload model"""
    service: str = Field(..., description="Service name experiencing the issue")
    severity: str = Field(..., description="Alert severity: critical, warning, info")
    summary: str = Field(..., description="Human-readable description of the issue")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO 8601 timestamp when alert fired"
    )
    labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional metadata (env, region, team, etc.)"
    )


@app.get("/")
def root():
    """Redirect to dashboard"""
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
def health():
    """Detailed health check"""
    import os
    
    return {
        "status": "healthy",
        "groq_configured": bool(os.environ.get("GROQ_API_KEY")),
        "exa_configured": bool(os.environ.get("EXA_API_KEY")),
        "browserbase_configured": bool(os.environ.get("BROWSERBASE_API_KEY")),
    }


@app.post("/alert")
def receive_alert(alert: Alert):
    """
    Main webhook endpoint for receiving production alerts.
    
    Accepts alerts from:
    - Prometheus / Alertmanager
    - Grafana
    - PagerDuty
    - Custom monitoring systems
    
    Returns the incident response with diagnosis and actions taken.
    """
    try:
        # Convert Pydantic model to dict
        alert_dict = alert.model_dump()
        
        # Process the alert through Tensorlake workflow
        result = handle_outage(alert_dict)
        
        return {
            "status": "processed",
            "incident": result,
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process alert: {str(e)}"
        )


@app.get("/incidents")
def list_incidents():
    """
    List all processed incidents (for debugging/analytics).
    Returns incidents from in-memory store.
    """
    from outage_agent.memory import get_all_incidents
    
    incidents = get_all_incidents()
    return {
        "count": len(incidents),
        "incidents": incidents,
    }


@app.delete("/incidents")
def clear_incidents():
    """
    Clear all stored incidents (for testing/demo resets).
    """
    from outage_agent.memory import clear_incidents
    
    clear_incidents()
    return {"status": "cleared", "message": "All incidents cleared from memory"}


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*80)
    print("🚀 Starting On-Call Outage Agent")
    print("="*80)
    print("Webhook endpoint: http://localhost:8000/alert")
    print("Health check: http://localhost:8000/health")
    print("Incidents list: http://localhost:8000/incidents")
    print("="*80 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
