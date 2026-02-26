"""
Parallel Agent Example on TensorLake
=====================================
Demonstrates true distributed parallelism by wrapping each LLM agent
as its own TensorLake function (separate container). Instead of ADK's
ParallelAgent (which runs sub-agents in the same process), this uses
TensorLake's Future.wait() to run agents on separate containers
concurrently, then a synthesis agent combines results.

Pipeline:
  1. Three researcher agents run in parallel (each in its own container)
  2. A synthesis agent combines all findings into a structured report

Based on: https://google.github.io/adk-docs/agents/workflow-agents/parallel-agents/
"""

import asyncio
from tensorlake.applications import (
    application,
    function,
    run_local_application,
    Future,
    RETURN_WHEN,
    Request,
    Image,
)


# --- Research tools as TensorLake Functions (each runs in its own container) ---

@function()
def search_renewable_energy(query: str) -> str:
    """Searches for information about renewable energy sources.

    Args:
        query: The search query about renewable energy.

    Returns:
        str: search results summary.
    """
    return (
        "Recent advances in perovskite solar cells have pushed efficiency "
        "beyond 33%. Offshore wind capacity grew 35% globally in 2024. "
        "Green hydrogen production costs dropped 40% due to improved electrolyzer technology."
    )


@function()
def search_ev_technology(query: str) -> str:
    """Searches for information about electric vehicle technology.

    Args:
        query: The search query about EV technology.

    Returns:
        str: search results summary.
    """
    return (
        "Solid-state batteries are entering mass production with 500+ mile range. "
        "Vehicle-to-grid (V2G) technology is being deployed in major cities. "
        "EV charging infrastructure expanded 60% with ultra-fast 350kW stations."
    )


@function()
def search_carbon_capture(query: str) -> str:
    """Searches for information about carbon capture methods.

    Args:
        query: The search query about carbon capture.

    Returns:
        str: search results summary.
    """
    return (
        "Direct Air Capture (DAC) plants now operate in 12 countries. "
        "Ocean-based carbon removal using electrochemical methods shows promise. "
        "Enhanced rock weathering deployed across 500,000 acres of farmland."
    )


# --- Agent Image (includes google-adk for LLM agent execution) ---

AGENT_IMAGE = Image().run("pip install google-adk")


# --- Each LLM researcher agent is its own TensorLake function (separate container) ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def research_renewable_energy(topic: str) -> str:
    """LLM researcher agent for renewable energy - runs in its own container."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="RenewableEnergyResearcher",
            instruction=(
                "You are an AI Research Assistant specializing in energy. "
                "Research the latest advancements in 'renewable energy sources'. "
                "Use the search_renewable_energy tool provided. "
                "Summarize your key findings concisely (1-2 sentences). "
                "Output *only* the summary."
            ),
            description="Researches renewable energy sources.",
            tools=[search_renewable_energy],
        )

        runner = InMemoryRunner(agent=agent, app_name="researcher_1")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="researcher_1"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=topic)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def research_ev_technology(topic: str) -> str:
    """LLM researcher agent for EV technology - runs in its own container."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="EVResearcher",
            instruction=(
                "You are an AI Research Assistant specializing in transportation. "
                "Research the latest developments in 'electric vehicle technology'. "
                "Use the search_ev_technology tool provided. "
                "Summarize your key findings concisely (1-2 sentences). "
                "Output *only* the summary."
            ),
            description="Researches electric vehicle technology.",
            tools=[search_ev_technology],
        )

        runner = InMemoryRunner(agent=agent, app_name="researcher_2")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="researcher_2"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=topic)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def research_carbon_capture(topic: str) -> str:
    """LLM researcher agent for carbon capture - runs in its own container."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="CarbonCaptureResearcher",
            instruction=(
                "You are an AI Research Assistant specializing in climate solutions. "
                "Research the current state of 'carbon capture methods'. "
                "Use the search_carbon_capture tool provided. "
                "Summarize your key findings concisely (1-2 sentences). "
                "Output *only* the summary."
            ),
            description="Researches carbon capture methods.",
            tools=[search_carbon_capture],
        )

        runner = InMemoryRunner(agent=agent, app_name="researcher_3")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="researcher_3"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=topic)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


# --- Synthesis agent: combines parallel results ---

@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def synthesize_research(
    renewable_energy_result: str,
    ev_technology_result: str,
    carbon_capture_result: str,
) -> str:
    """LLM synthesis agent that combines research findings into a report."""
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    async def _run():
        agent = Agent(
            model="gemini-2.0-flash",
            name="SynthesisAgent",
            instruction=(
                "You are an AI Assistant responsible for combining research findings "
                "into a structured report.\n\n"
                "Synthesize the following research summaries, clearly attributing "
                "findings to their source areas. Your response MUST be grounded "
                "exclusively on the provided input summaries.\n\n"
                "Format as a structured report with sections for each topic."
            ),
            description="Combines research findings into a structured report.",
        )

        combined_input = (
            f"Renewable Energy Findings:\n{renewable_energy_result}\n\n"
            f"EV Technology Findings:\n{ev_technology_result}\n\n"
            f"Carbon Capture Findings:\n{carbon_capture_result}"
        )

        runner = InMemoryRunner(agent=agent, app_name="synthesizer")
        user_id = "local_user"
        session = await runner.session_service.create_session(
            user_id=user_id, app_name="synthesizer"
        )

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(parts=[Part(text=combined_input)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        return response_text

    return asyncio.run(_run())


# --- Orchestrator: launches parallel agents, then synthesizes ---

@application()
@function(image=AGENT_IMAGE, secrets=["GOOGLE_API_KEY"])
def parallel_research(topic: str) -> str:
    """Orchestrates truly parallel research across separate containers.

    Each researcher LLM agent runs in its own TensorLake container.
    Futures are launched concurrently, waited on together, then results
    are passed to a synthesis agent.
    """

    # Launch all three researcher agents in parallel (non-blocking).
    # Each .future() call creates a Future; .run() starts running it by
    # dispatching it to a separate container.
    future_renewable = research_renewable_energy.future(topic=topic).run()
    future_ev = research_ev_technology.future(topic=topic).run()
    future_carbon = research_carbon_capture.future(topic=topic).run()

    # Wait for all three to complete - true distributed parallelism.
    done, _ = Future.wait(
        [future_renewable, future_ev, future_carbon],
        return_when=RETURN_WHEN.ALL_COMPLETED,
    )

    # Collect results from each future.
    renewable_result = future_renewable.result()
    ev_result = future_ev.result()
    carbon_result = future_carbon.result()

    # Pass all results to the synthesis agent (also its own container).
    report = synthesize_research(
        renewable_energy_result=renewable_result,
        ev_technology_result=ev_result,
        carbon_capture_result=carbon_result,
    )

    return report


if __name__ == "__main__":
    request: Request = run_local_application(
        parallel_research, topic="Latest sustainability technology trends"
    )
    print(request.output())
