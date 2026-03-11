"""Pydantic data models for the deep research pipeline.

Matches the Temporal + OpenAI agents example.
"""

from pydantic import BaseModel


class ResearchQuery(BaseModel):
    query: str
    """The research question to answer."""


class WebSearchItem(BaseModel):
    reason: str
    """Your reasoning for why this search is important to the query."""

    query: str
    """The search term to use for the web search."""


class WebSearchPlan(BaseModel):
    searches: list[WebSearchItem]
    """A list of web searches to perform to best answer the query."""


class ReportData(BaseModel):
    short_summary: str
    """A short 2-3 sentence summary of the findings."""

    markdown_report: str
    """The final report"""

    follow_up_questions: list[str]
    """Suggested topics to research further"""
