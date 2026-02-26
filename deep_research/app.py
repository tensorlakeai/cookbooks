"""
Deep Research Agent — Tensorlake + OpenAI Agents SDK

A multi-agent research pipeline that uses OpenAI agents to plan searches,
search the web in parallel, and synthesize a comprehensive report.
Each agent phase runs as an isolated Tensorlake function.

Mirrors the Temporal deep research example, replacing Temporal's workflow
orchestration with Tensorlake's @application/@function and Futures.
"""

import asyncio
import json

from agents import Agent, Runner, WebSearchTool
from agents.model_settings import ModelSettings

from tensorlake.applications import (
    Future,
    Image,
    RETURN_WHEN,
    RequestContext,
    application,
    function,
    run_local_application,
)

from models import WebSearchItem, WebSearchPlan, ReportData
from prompts import PLANNER_PROMPT, SEARCH_PROMPT, WRITER_PROMPT

# ---------------------------------------------------------------------------
# Container image shared by all functions
# ---------------------------------------------------------------------------
agent_image = Image(name="deep-research-agent").run(
    "pip install openai-agents pydantic"
)


# ---------------------------------------------------------------------------
# Phase 1: Plan — decide what to search for
# ---------------------------------------------------------------------------
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def plan_research(query: str) -> str:
    """Use the planner agent to generate a set of web searches for the query."""
    planner = Agent(
        name="PlannerAgent",
        instructions=PLANNER_PROMPT,
        model="gpt-4o",
        output_type=WebSearchPlan,
    )
    result = asyncio.run(Runner.run(planner, f"Query: {query}"))
    plan: WebSearchPlan = result.final_output_as(WebSearchPlan)
    return plan.model_dump_json()


# ---------------------------------------------------------------------------
# Phase 2: Search — execute a single web search and summarise
# ---------------------------------------------------------------------------
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def search_web(search_item_json: str) -> str:
    """Use the search agent to search and summarise results for one query."""
    item = WebSearchItem.model_validate_json(search_item_json)
    search_agent = Agent(
        name="SearchAgent",
        instructions=SEARCH_PROMPT,
        tools=[WebSearchTool()],
        model_settings=ModelSettings(tool_choice="required"),
    )
    input_text = f"Search term: {item.query}\nReason for searching: {item.reason}"
    result = asyncio.run(Runner.run(search_agent, input_text))
    return str(result.final_output)


# ---------------------------------------------------------------------------
# Phase 3: Write — synthesise all search summaries into a report
# ---------------------------------------------------------------------------
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def write_report(context_json: str) -> str:
    """Use the writer agent to produce a detailed research report."""
    context = json.loads(context_json)
    writer = Agent(
        name="WriterAgent",
        instructions=WRITER_PROMPT,
        model="o3-mini",
        output_type=ReportData,
    )
    input_text = (
        f"Original query: {context['query']}\n"
        f"Summarized search results: {context['search_results']}"
    )
    result = asyncio.run(Runner.run(writer, input_text))
    report: ReportData = result.final_output_as(ReportData)
    return report.model_dump_json()


# ---------------------------------------------------------------------------
# Orchestrator: plan → parallel search → report
# ---------------------------------------------------------------------------
@application()
@function(image=agent_image, secrets=["OPENAI_API_KEY"])
def deep_research(query: str) -> str:
    """Orchestrate a full deep research pipeline for a given query."""
    ctx = RequestContext.get()

    # Phase 1: Plan searches
    ctx.progress.update(1, 4, "Planning research...", {})
    plan_json = plan_research(query)
    plan = WebSearchPlan.model_validate_json(plan_json)

    # Phase 2: Execute searches in parallel using Futures
    ctx.progress.update(2, 4, f"Searching the web ({len(plan.searches)} queries)...", {})
    search_futures: list[Future] = []
    for item in plan.searches:
        future = search_web.future(item.model_dump_json()).run()
        search_futures.append(future)

    Future.wait(search_futures, return_when=RETURN_WHEN.ALL_COMPLETED)

    search_results: list[str] = []
    for future in search_futures:
        try:
            search_results.append(future.result())
        except Exception:
            pass  # skip failed searches

    # Phase 3: Write report
    ctx.progress.update(3, 4, "Writing report...", {})
    context = json.dumps({"query": query, "search_results": search_results})
    report_json = write_report(context)
    report = ReportData.model_validate_json(report_json)

    # Format the final output
    ctx.progress.update(4, 4, "Done!", {})
    formatted = (
        f"# Research Report\n\n"
        f"**Summary:** {report.short_summary}\n\n"
        f"{report.markdown_report}\n\n"
        f"## Follow-up Questions\n\n"
    )
    for q in report.follow_up_questions:
        formatted += f"- {q}\n"

    return formatted


# ---------------------------------------------------------------------------
# Local testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    query = "What are the economic impacts of AI on the job market?"
    print(f"Query: {query}\n")
    print("=" * 60)
    request = run_local_application(deep_research, query)
    print(request.output())
