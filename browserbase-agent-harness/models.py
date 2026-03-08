"""Pydantic input models for the Browserbase agent harness."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field


class AgenticQueryInput(BaseModel):
    query: str = Field(description="Question the agent should answer")
    website: str = Field(description="Seed website to explore")
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        description="OpenAI model used for the harness",
    )
    max_iterations: int = Field(default=8, ge=1, le=20)
    max_pages: int = Field(default=6, ge=1, le=20)
    agent_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Hard timeout for Agents SDK orchestration stage",
    )
    auto_search_phase: bool = Field(
        default=True,
        description="Use search-bar exploration with reframed queries before main loop",
    )
    search_variations: int = Field(
        default=5,
        ge=1,
        le=12,
        description="Number of reframed search queries to run during auto search phase",
    )
    search_results_per_variation: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Max search results captured per variation",
    )
    prefetch_from_search_results: int = Field(
        default=4,
        ge=0,
        le=20,
        description="How many discovered URLs to prefetch as evidence",
    )
    enable_elasticsearch: bool = Field(
        default=False,
        description="Whether to index intermediate and final findings into Elasticsearch",
    )
    enable_tracing: bool = Field(
        default=True,
        description="Whether to collect detailed step-by-step trace events",
    )
    max_trace_events: int = Field(
        default=300,
        ge=20,
        le=2000,
        description="Maximum trace events retained in output",
    )
    browserbase_project_id: str = Field(
        default_factory=lambda: os.getenv("BROWSERBASE_PROJECT_ID", ""),
        description="Browserbase project ID",
    )
    browserbase_api_key: str = Field(
        default_factory=lambda: os.getenv("BROWSERBASE_API_KEY", ""),
        description="Browserbase API key",
    )
    elasticsearch_url: str = Field(
        default_factory=lambda: os.getenv("ELASTICSEARCH_URL", ""),
        description="Elasticsearch endpoint URL",
    )
    elasticsearch_api_key: str = Field(
        default_factory=lambda: os.getenv("ELASTIC_API_KEY", ""),
        description="Elasticsearch API key",
    )
    elasticsearch_index: str = Field(default="browserbase_agent_runs")


class BrowserFetchInput(BaseModel):
    url: str
    allowed_domain: str | None = None
    max_links: int = Field(default=25, ge=1, le=100)
    max_chars: int = Field(default=9000, ge=1000, le=25000)
    timeout_ms: int = Field(default=45000, ge=5000, le=120000)
    wait_after_load_ms: int = Field(default=1000, ge=0, le=10000)
    browserbase_project_id: str = ""
    browserbase_api_key: str = ""


class BrowserSnippetInput(BaseModel):
    url: str
    query: str
    allowed_domain: str | None = None
    max_snippets: int = Field(default=5, ge=1, le=20)
    snippet_chars: int = Field(default=260, ge=80, le=1000)
    browserbase_project_id: str = ""
    browserbase_api_key: str = ""


class BrowserSearchInput(BaseModel):
    start_url: str
    search_query: str
    allowed_domain: str | None = None
    max_results: int = Field(default=8, ge=1, le=30)
    timeout_ms: int = Field(default=45000, ge=5000, le=120000)
    wait_after_load_ms: int = Field(default=1000, ge=0, le=10000)
    wait_after_submit_ms: int = Field(default=1200, ge=0, le=10000)
    browserbase_project_id: str = ""
    browserbase_api_key: str = ""


class DownloadFileInput(BaseModel):
    url: str
    allowed_domain: str | None = None
    max_bytes: int = Field(default=8_000_000, ge=10_000, le=40_000_000)
    timeout_seconds: int = Field(default=60, ge=5, le=240)


class ExtractArchiveInput(BaseModel):
    file_b64: str
    filename: str
    max_files: int = Field(default=50, ge=1, le=500)
    max_bytes_per_file: int = Field(default=1_000_000, ge=10_000, le=10_000_000)


class DocumentToMarkdownInput(BaseModel):
    file_b64: str
    filename: str
    query: str | None = None
    max_chars: int = Field(default=25_000, ge=1_000, le=120_000)
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    )


class ElasticsearchIndexNoteInput(BaseModel):
    index: str
    run_id: str
    query: str
    website: str
    note: str
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""


class ElasticsearchSearchInput(BaseModel):
    index: str
    query: str
    run_id: str | None = None
    size: int = Field(default=5, ge=1, le=20)
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""


class ElasticsearchBulkIngestInput(BaseModel):
    index: str
    run_id: str
    query: str
    website: str
    pages: list[dict[str, Any]]
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""
