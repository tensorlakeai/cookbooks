import json
import os

import anthropic
import httpx
from pydantic import BaseModel

from tensorlake.applications import (
    Image,
    RequestContext,
    application,
    function,
    run_local_application,
)

# ---------------------------------------------------------------------------
# Container image shared by all functions
# ---------------------------------------------------------------------------
agent_image = Image(name="claude-agent-tools").run(
    "pip install anthropic httpx pydantic"
)

# ---------------------------------------------------------------------------
# Pydantic models for tool inputs
# ---------------------------------------------------------------------------


class GetLocationInput(BaseModel):
    ip_address: str


class GetWeatherAlertsInput(BaseModel):
    latitude: float
    longitude: float
    state: str


# ---------------------------------------------------------------------------
# Helper: convert a Pydantic model → Claude input_schema
# ---------------------------------------------------------------------------


def pydantic_to_input_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


# ---------------------------------------------------------------------------
# Claude tool definitions
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_ip_address",
        "description": "Returns the current public IP address of the server.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_location_info",
        "description": (
            "Returns geographic location information (city, region, country, "
            "latitude, longitude) for a given IP address."
        ),
        "input_schema": pydantic_to_input_schema(GetLocationInput),
    },
    {
        "name": "get_weather_alerts",
        "description": (
            "Returns active weather alerts for a location specified by "
            "latitude, longitude, and US state code."
        ),
        "input_schema": pydantic_to_input_schema(GetWeatherAlertsInput),
    },
]

# ---------------------------------------------------------------------------
# Tensorlake tool functions (each runs in its own container)
# ---------------------------------------------------------------------------


@function(image=agent_image)
def get_ip_address() -> str:
    """Fetch the server's public IP address."""
    resp = httpx.get("https://api.ipify.org", timeout=10)
    resp.raise_for_status()
    return resp.text.strip()


@function(image=agent_image)
def get_location_info(input: GetLocationInput) -> str:
    """Look up geographic info for an IP address via ipinfo.io."""
    resp = httpx.get(f"https://ipinfo.io/{input.ip_address}/json", timeout=10)
    resp.raise_for_status()
    return json.dumps(resp.json())


@function(image=agent_image)
def get_weather_alerts(input: GetWeatherAlertsInput) -> str:
    """Fetch active NWS weather alerts for a location."""
    url = f"https://api.weather.gov/alerts/active?point={input.latitude},{input.longitude}"
    headers = {"User-Agent": "TensorlakeWeatherAgent/1.0", "Accept": "application/geo+json"}
    resp = httpx.get(url, headers=headers, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    features = data.get("features", [])
    if not features:
        return json.dumps({
            "state": input.state,
            "alerts": [],
            "message": "No active weather alerts for this location.",
        })

    alerts = []
    for feature in features[:5]:
        props = feature.get("properties", {})
        alerts.append({
            "event": props.get("event"),
            "headline": props.get("headline"),
            "severity": props.get("severity"),
            "description": props.get("description", "")[:500],
        })
    return json.dumps({"state": input.state, "alerts": alerts})


# ---------------------------------------------------------------------------
# Tool dispatch table
# ---------------------------------------------------------------------------
TOOL_DISPATCH: dict[str, dict] = {
    "get_ip_address": {"fn": get_ip_address, "input_model": None},
    "get_location_info": {"fn": get_location_info, "input_model": GetLocationInput},
    "get_weather_alerts": {"fn": get_weather_alerts, "input_model": GetWeatherAlertsInput},
}


def execute_tool(name: str, input_dict: dict) -> str:
    """Validate input via Pydantic (when applicable) and call the Tensorlake function."""
    entry = TOOL_DISPATCH.get(name)
    if entry is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    fn = entry["fn"]
    input_model = entry["input_model"]

    if input_model is not None:
        validated = input_model(**input_dict)
        return fn(validated)
    return fn()


# ---------------------------------------------------------------------------
# Agentic loop — the Tensorlake application entry-point
# ---------------------------------------------------------------------------
MAX_ITERATIONS = 10


@application()
@function(image=agent_image, secrets=["ANTHROPIC_API_KEY"])
def claude_weather_agent(user_query: str) -> str:
    """Run a Claude agentic loop that chains tool calls to answer a query."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    ctx = RequestContext.get()

    messages: list[dict] = [{"role": "user", "content": user_query}]

    for iteration in range(MAX_ITERATIONS):
        ctx.progress.update(
            iteration, MAX_ITERATIONS, f"Iteration {iteration + 1}", {}
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )

        # If Claude is done (no more tool calls), return the final text.
        if response.stop_reason != "tool_use":
            text_parts = [
                block.text for block in response.content if block.type == "text"
            ]
            return "\n".join(text_parts)

        # Otherwise, process every tool_use block in the response.
        assistant_content = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

                try:
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                except Exception as exc:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps({"error": str(exc)}),
                        "is_error": True,
                    })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    return "Reached maximum iterations without a final answer."


# ---------------------------------------------------------------------------
# Local testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    query = "What are the current weather alerts for my location?"
    print(f"Query: {query}\n")
    request = run_local_application(agent_loop, query)
    print(request.output())
