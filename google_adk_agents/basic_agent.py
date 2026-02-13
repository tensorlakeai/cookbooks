"""
Basic ADK Agent on TensorLake
==============================
Minimal example: a single LLM agent with weather and time tools.
Tools and agent each run as separate TensorLake functions.

Based on: https://google.github.io/adk-docs/agents/llm-agents/
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo
from tensorlake.applications import application, function, run_local_application, Request, Image

AGENT_IMAGE = Image().run("pip install google-adk")


@function()
def get_weather(city: str) -> dict:
    """Retrieves the current weather report for a specified city.

    Args:
        city (str): The name of the city for which to retrieve the weather report.

    Returns:
        dict: status and result or error msg.
    """
    if city.lower() == "new york":
        return {
            "status": "success",
            "report": (
                "The weather in New York is sunny with a temperature of 25 degrees"
                " Celsius (77 degrees Fahrenheit)."
            ),
        }
    else:
        return {
            "status": "error",
            "error_message": f"Weather information for '{city}' is not available.",
        }


@function()
def get_current_time(city: str) -> dict:
    """Returns the current time in a specified city.

    Args:
        city (str): The name of the city for which to retrieve the current time.

    Returns:
        dict: status and result or error msg.
    """
    if city.lower() == "new york":
        tz_identifier = "America/New_York"
    else:
        return {
            "status": "error",
            "error_message": (
                f"Sorry, I don't have timezone information for {city}."
            ),
        }

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    report = (
        f'The current time in {city} is {now.strftime("%Y-%m-%d %H:%M:%S %Z%z")}'
    )
    return {"status": "success", "report": report}


@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def run_weather_time_agent(query: str) -> str:
    """LLM agent that answers questions about weather and time."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            name="weather_time_agent",
            model="gemini-2.0-flash",
            description="Agent to answer questions about the time and weather in a city.",
            instruction="You are a helpful agent who can answer user questions about the time and weather in a city.",
            tools=[get_weather, get_current_time],
        )

        runner = InMemoryRunner(agent=agent, app_name="weather_app")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="weather_app"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=query)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


@application()
@function(image=AGENT_IMAGE)
def google_adk_basic_agent(query: str) -> str:
    """Entry point that dispatches to the weather/time agent."""
    return run_weather_time_agent(query=query)


if __name__ == "__main__":
    request: Request = run_local_application(
        google_adk_basic_agent, query="What is the weather in New York?"
    )
    print(request.output())
