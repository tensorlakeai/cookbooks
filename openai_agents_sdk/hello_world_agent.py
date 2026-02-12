import math

from tensorlake.applications import (
    Image,
    Request,
    application,
    function,
    run_local_application,
)

# Container image with OpenAI Agents SDK installed
agent_image = Image(name="python:3.11-slim").run("pip install openai-agents requests")

# Image for the weather tool (needs requests for HTTP calls)
weather_image = Image(name="python:3.11-slim").run("pip install requests")


# --- Tensorlake Functions (equivalent to Temporal Activities) ---


@function(image=weather_image)
def get_weather(city: str) -> str:
    """Get the current weather for a given city using the Open-Meteo API (free, no API key needed)."""
    import requests

    # Step 1: Geocode the city name to lat/lon
    geo_resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1},
    )
    geo_data = geo_resp.json()
    if not geo_data.get("results"):
        return f"Could not find location: {city}"

    location = geo_data["results"][0]
    lat, lon = location["latitude"], location["longitude"]
    name = location.get("name", city)
    country = location.get("country", "")

    # Step 2: Fetch current weather
    weather_resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
        },
    )
    weather_data = weather_resp.json()
    current = weather_data["current"]

    return (
        f"Weather in {name}, {country}: "
        f"{current['temperature_2m']}°C, "
        f"Humidity: {current['relative_humidity_2m']}%, "
        f"Wind: {current['wind_speed_10m']} km/h, "
        f"WMO code: {current['weather_code']}"
    )


@function()
def calculate_circle_area(radius: float) -> float:
    """Calculate the area of a circle given its radius."""
    return math.pi * radius**2


# --- Application entry point (equivalent to Temporal Workflow) ---


@application()
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def openai_sdk_weather_agent(prompt: str) -> str:
    """An agent that determines what tool to use based on the user's question."""
    from agents import Agent, Runner, function_tool

    @function_tool
    def weather(city: str) -> str:
        """Get the weather for a given city."""
        return get_weather(city)

    @function_tool
    def circle_area(radius: float) -> str:
        """Calculate the area of a circle given its radius."""
        return str(calculate_circle_area(radius))

    agent = Agent(
        name="Hello World Agent",
        instructions="You are a helpful assistant that determines what tool to use based on the user's question.",
        tools=[weather, circle_area],
    )

    result = Runner.run_sync(agent, prompt)
    return result.final_output


# --- Local testing ---

if __name__ == "__main__":
    request: Request = run_local_application(
        hello_world_agent, "What is the weather in London?"
    )
    output = request.output()
    print(f"Result: {output}")
