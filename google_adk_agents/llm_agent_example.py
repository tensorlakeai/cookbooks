"""
LLM Agent Example on TensorLake
================================
Demonstrates the core LlmAgent - the "thinking" part of an ADK application.
Uses an LLM for reasoning, understanding language, making decisions, and
interacting with tools. Behavior is non-deterministic and flexible.

Architecture:
  - Tools run as separate TensorLake functions (own containers)
  - The LLM agent runs in its own TensorLake function (own container)
  - The application entry point orchestrates the call

Based on: https://google.github.io/adk-docs/agents/llm-agents/
"""

import asyncio
from tensorlake.applications import application, function, run_local_application, Request, Image


# --- Tools as TensorLake Functions (each runs in its own container) ---

@function()
def get_capital_city(country: str) -> dict:
    """Retrieves the capital city for a given country.

    Args:
        country: The name of the country.

    Returns:
        dict: status and result or error msg.
    """
    capitals = {
        "united states": "Washington, D.C.",
        "france": "Paris",
        "japan": "Tokyo",
        "brazil": "Brasília",
        "india": "New Delhi",
        "germany": "Berlin",
        "australia": "Canberra",
    }
    country_lower = country.lower()
    if country_lower in capitals:
        return {
            "status": "success",
            "capital": capitals[country_lower],
        }
    else:
        return {
            "status": "error",
            "error_message": f"Capital information for '{country}' is not available.",
        }


@function()
def get_country_population(country: str) -> dict:
    """Retrieves the approximate population of a given country.

    Args:
        country: The name of the country.

    Returns:
        dict: status and result or error msg.
    """
    populations = {
        "united states": "331 million",
        "france": "67 million",
        "japan": "125 million",
        "brazil": "214 million",
        "india": "1.4 billion",
        "germany": "84 million",
        "australia": "26 million",
    }
    country_lower = country.lower()
    if country_lower in populations:
        return {
            "status": "success",
            "population": populations[country_lower],
        }
    else:
        return {
            "status": "error",
            "error_message": f"Population information for '{country}' is not available.",
        }


# --- Agent Image ---

AGENT_IMAGE = Image().run("pip install google-adk")


# --- LLM Agent as its own TensorLake function (separate container) ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def run_capital_agent(query: str) -> str:
    """LLM Agent that answers questions about countries using tools.

    Runs in its own container. The LLM reasons about which tool to call
    based on the user's natural language query.
    """
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run_async():
        agent = Agent(
            model="gemini-2.0-flash",
            name="capital_agent",
            description="Agent to answer questions about countries, capitals, and populations.",
            instruction=(
                "You are a helpful agent who can answer user questions about countries. "
                "You can look up capital cities and population information. "
                "Always use the available tools to get accurate data before responding."
            ),
            tools=[get_capital_city, get_country_population],
        )

        runner = InMemoryRunner(agent=agent, app_name="capital_app")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="capital_app"
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

    return asyncio.run(_run_async())


# --- Application entry point ---

@application()
@function(image=AGENT_IMAGE)
def capital_agent(query: str) -> str:
    """Entry point that dispatches to the LLM agent function."""
    return run_capital_agent(query=query)


if __name__ == "__main__":
    request: Request = run_local_application(
        capital_agent, query="What is the capital of France and its population?"
    )
    print(request.output())
