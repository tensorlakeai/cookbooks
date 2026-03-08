"""
Browserbase Agent Harness - Tensorlake Application

Given a user query and a target website, this application runs an agentic
loop that:
1. Uses Browserbase-backed browser tools to explore the site
2. Synthesizes an answer grounded in retrieved content
3. Ingests discovered content and final findings into Elasticsearch
"""

import asyncio
import json
import os
import re
import tarfile
import threading
import uuid
import zipfile
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import quote_plus, urlparse

from models import (
    AgenticQueryInput,
    BrowserFetchInput,
    BrowserSearchInput,
    BrowserSnippetInput,
    DocumentToMarkdownInput,
    DownloadFileInput,
    ElasticsearchBulkIngestInput,
    ElasticsearchIndexNoteInput,
    ElasticsearchSearchInput,
    ExtractArchiveInput,
)
from pydantic import BaseModel
from prompts import SYSTEM_INSTRUCTIONS, build_agent_prompt
from tensorlake.applications import (
    Future,
    Image,
    RequestContext,
    RETURN_WHEN,
    application,
    function,
    run_local_application,
)
from tracing_utils import append_trace, register_agents_trace_sink

# ---------------------------------------------------------------------------
# Tensorlake images
# ---------------------------------------------------------------------------
agent_image = Image(name="browserbase-agent-image").run(
    "pip install openai openai-agents pydantic tensorlake"
)

browser_image = Image(name="browserbase-tools-image").run(
    "pip install browserbase playwright pydantic tensorlake"
)

document_image = Image(name="document-tools-image").run(
    "pip install openai pydantic requests pypdf python-docx tensorlake"
)

elastic_image = Image(name="elasticsearch-tools-image").run(
    "pip install elasticsearch pydantic tensorlake"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pydantic_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _resolve_required(value: str, env_name: str, label: str) -> str:
    resolved = value or os.getenv(env_name, "")
    if not resolved:
        raise ValueError(f"Missing required {label}. Set {env_name} or pass it in input.")
    return resolved


def _append_trace(
    traces: list[dict[str, Any]],
    enabled: bool,
    max_events: int,
    event: str,
    details: dict[str, Any],
) -> None:
    """Thin adapter to keep callsites compact; implementation lives in tracing_utils."""
    append_trace(
        traces=traces,
        enabled=enabled,
        max_events=max_events,
        event=event,
        details=details,
    )


def _extract_snippets(text: str, query: str, max_snippets: int, snippet_chars: int) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", query)]
    if not terms:
        terms = [query.lower()]

    scored: list[tuple[int, str]] = []
    for line in lines:
        lowered = line.lower()
        score = sum(lowered.count(term) for term in terms)
        if score > 0:
            scored.append((score, line))

    scored.sort(key=lambda x: x[0], reverse=True)

    snippets: list[str] = []
    seen: set[str] = set()
    for _, line in scored:
        snippet = line
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars].rsplit(" ", 1)[0] + "..."
        if snippet in seen:
            continue
        seen.add(snippet)
        snippets.append(snippet)
        if len(snippets) >= max_snippets:
            break

    return snippets


def _clean_query_for_reframing(query: str) -> str:
    cleaned = query.strip()
    cleaned = re.sub(r"^give me (a|an)?\s*summary (about|of)\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^what\s+are\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^what\s+is\s+", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" ?.")
    return cleaned or query.strip()


def _build_query_variations(query: str, max_variations: int) -> list[str]:
    base = _clean_query_for_reframing(query)
    candidates = [
        query.strip(),
        base,
        f"{base} official CMS guidance",
        f"{base} eligibility criteria",
        f"{base} age restrictions",
        f"{base} approved medications",
        f"{base} Medicare Part D coverage",
        f"{base} policy document PDF",
        f"{base} clinical guidance filetype:pdf",
        f"{base} formulary coverage",
        f"{base} prior authorization requirements",
    ]

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(re.sub(r"\s+", " ", candidate).strip())
        if len(deduped) >= max_variations:
            break

    return deduped


def _safe_decode_text(data: bytes, fallback_encoding: str = "utf-8") -> str:
    for encoding in [fallback_encoding, "utf-8", "latin-1"]:
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _is_archive_filename(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".zip") or lowered.endswith(".tar") or lowered.endswith(".tar.gz") or lowered.endswith(".tgz")


def _is_document_filename(name: str) -> bool:
    lowered = name.lower()
    return any(
        lowered.endswith(ext)
        for ext in [
            ".pdf",
            ".docx",
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".html",
            ".htm",
            ".xml",
            ".tsv",
        ]
    )


def _collect_page_with_browserbase(input: BrowserFetchInput) -> dict[str, Any]:
    from browserbase import Browserbase
    from playwright.sync_api import sync_playwright

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )
    project_id = _resolve_required(
        input.browserbase_project_id,
        "BROWSERBASE_PROJECT_ID",
        "Browserbase project ID",
    )

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    bb = Browserbase(api_key=api_key)
    session = bb.sessions.create(project_id=project_id)
    connect_url = getattr(session, "connect_url", None) or getattr(session, "connectUrl", None)
    session_id = getattr(session, "id", None)

    if not connect_url:
        raise RuntimeError("Browserbase session did not return a connect URL.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(input.url, wait_until="domcontentloaded", timeout=input.timeout_ms)
            if input.wait_after_load_ms > 0:
                page.wait_for_timeout(input.wait_after_load_ms)

            extracted = page.evaluate(
                """
                ({ maxLinks, maxChars }) => {
                  const toAbsolute = (href) => {
                    try {
                      return new URL(href, window.location.href).toString();
                    } catch {
                      return null;
                    }
                  };

                  const seen = new Set();
                  const links = [];
                  const anchors = Array.from(document.querySelectorAll('a[href]'));

                  for (const anchor of anchors) {
                    const absolute = toAbsolute(anchor.getAttribute('href'));
                    if (!absolute) continue;
                    if (!(absolute.startsWith('http://') || absolute.startsWith('https://'))) continue;
                    if (seen.has(absolute)) continue;
                    seen.add(absolute);
                    links.push(absolute);
                    if (links.length >= maxLinks) break;
                  }

                  const raw = document.body ? document.body.innerText : '';
                  const cleaned = raw
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+\\n/g, '\\n')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();

                  return {
                    title: document.title || '',
                    text: cleaned.slice(0, maxChars),
                    links,
                  };
                }
                """,
                {"maxLinks": input.max_links, "maxChars": input.max_chars},
            )

            final_url = page.url
            page.close()
        finally:
            browser.close()

    links = extracted.get("links", [])
    if allowed_domain:
        links = [link for link in links if _extract_domain(link) == allowed_domain]

    return {
        "success": True,
        "requested_url": input.url,
        "url": final_url,
        "title": extracted.get("title", ""),
        "text": extracted.get("text", ""),
        "links": links,
        "session_id": session_id,
        "fetched_at": _now_iso(),
    }


def _search_site_with_browserbase(input: BrowserSearchInput) -> dict[str, Any]:
    from browserbase import Browserbase
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )
    project_id = _resolve_required(
        input.browserbase_project_id,
        "BROWSERBASE_PROJECT_ID",
        "Browserbase project ID",
    )

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.start_url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    bb = Browserbase(api_key=api_key)
    session = bb.sessions.create(project_id=project_id)
    connect_url = getattr(session, "connect_url", None) or getattr(session, "connectUrl", None)
    session_id = getattr(session, "id", None)
    if not connect_url:
        raise RuntimeError("Browserbase session did not return a connect URL.")

    search_strategy = "none"
    search_selector = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            parsed = urlparse(input.start_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            encoded_query = quote_plus(input.search_query)

            # 1) Try visible search bar on the start page (hero search on CMS).
            visited_search_urls: list[str] = []
            input_found = False
            last_error = None

            page.goto(input.start_url, wait_until="domcontentloaded", timeout=input.timeout_ms)
            if input.wait_after_load_ms > 0:
                page.wait_for_timeout(input.wait_after_load_ms)

            selectors = [
                "#hero-search-input",
                ".hero-search-block input[name='keys']",
                "#hero-search-block-form input[name='keys']",
                "input[name='keys']",
                "input[id*='keys' i]",
                "input[type='search']",
                "form[role='search'] input",
                "[role='search'] input",
                "input[name*='search' i]",
                "input[id*='search' i]",
                "input[aria-label*='search' i]",
                "input[placeholder*='search' i]",
                "header input[type='text']",
            ]

            submit_selectors = [
                "#hero-search-block-form button[type='submit']",
                ".hero-search-block button[type='submit']",
                "button[id^='edit-submit'][type='submit']",
                "button:has-text('Search')",
                "input[type='submit'][value*='Search' i]",
            ]

            for selector in selectors:
                locator = page.locator(selector)
                try:
                    if locator.count() == 0:
                        continue
                except Exception:
                    continue

                candidate = locator.first
                try:
                    visible = False
                    try:
                        visible = candidate.is_visible(timeout=1200)
                    except Exception:
                        visible = False
                    if not visible:
                        continue

                    candidate.click(timeout=2500)
                    candidate.fill("")
                    candidate.fill(input.search_query, timeout=7000)

                    submitted = False
                    for submit_selector in submit_selectors:
                        submit_locator = page.locator(submit_selector)
                        try:
                            if submit_locator.count() == 0:
                                continue
                            submit_button = submit_locator.first
                            if submit_button.is_visible(timeout=800):
                                submit_button.click(timeout=2500)
                                submitted = True
                                search_selector = f"{selector} + {submit_selector}"
                                break
                        except Exception:
                            continue

                    if not submitted:
                        candidate.press("Enter")
                        search_selector = selector

                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(input.timeout_ms, 30000))
                    except PlaywrightTimeoutError:
                        pass
                    if input.wait_after_submit_ms > 0:
                        page.wait_for_timeout(input.wait_after_submit_ms)

                    quality = page.evaluate(
                        """
                        () => {
                          const bodyText = (document.body?.innerText || '').toLowerCase();
                          const hasPrompt = bodyText.includes('please enter some search terms');
                          const countResultRows = document.querySelectorAll(
                            '.search-item-list .result, .search-item-list .search-results, .gsc-webResult.gsc-result, .views-row'
                          ).length;
                          const countResultLinks = document.querySelectorAll(
                            '.search-item-list .result a[href], .search-item-list .search-results a[href], .gsc-webResult.gsc-result a[href], .views-row a[href]'
                          ).length;
                          return { hasPrompt, countResultRows, countResultLinks };
                        }
                        """
                    )
                    visited_search_urls.append(page.url)
                    if quality.get("countResultRows", 0) > 0 or quality.get("countResultLinks", 0) > 0:
                        search_strategy = "search_input"
                        input_found = True
                        break
                except Exception as exc:
                    last_error = exc
                    continue

            # 2) Deterministic search URL fallback (very reliable for CMS with ?keys=...).
            if not input_found:
                fallback_urls = [
                    f"{origin}/search/cms?keys={encoded_query}",
                    f"{origin}/search?keys={encoded_query}",
                    f"{origin}/search?q={encoded_query}",
                    f"{origin}/search?query={encoded_query}",
                    f"{origin}/site-search?search_api_fulltext={encoded_query}",
                ]

                for fallback_url in fallback_urls:
                    try:
                        page.goto(
                            fallback_url,
                            wait_until="domcontentloaded",
                            timeout=min(input.timeout_ms, 35000),
                        )
                        if input.wait_after_submit_ms > 0:
                            page.wait_for_timeout(input.wait_after_submit_ms)

                        quality = page.evaluate(
                            """
                            () => {
                              const bodyText = (document.body?.innerText || '').toLowerCase();
                              const hasPrompt = bodyText.includes('please enter some search terms');
                              const countResultRows = document.querySelectorAll(
                                '.search-item-list .result, .search-item-list .search-results, .gsc-webResult.gsc-result, .views-row'
                              ).length;
                              const countResultLinks = document.querySelectorAll(
                                '.search-item-list .result a[href], .search-item-list .search-results a[href], .gsc-webResult.gsc-result a[href], .views-row a[href]'
                              ).length;
                              return { hasPrompt, countResultRows, countResultLinks };
                            }
                            """
                        )
                        visited_search_urls.append(page.url)
                        if quality.get("countResultRows", 0) > 0 or quality.get("countResultLinks", 0) > 0 or not quality.get("hasPrompt", False):
                            search_strategy = "direct_search_url"
                            search_selector = fallback_url
                            input_found = True
                            break
                    except Exception as exc:
                        last_error = exc

            if not input_found:
                if last_error:
                    raise RuntimeError(f"Could not execute site search: {last_error}") from last_error
                raise RuntimeError("Could not locate a search input or working search URL.")

            extracted = page.evaluate(
                """
                ({ maxResults, allowedDomain, query }) => {
                  const normalize = (href) => {
                    try {
                      return new URL(href, window.location.href).toString();
                    } catch {
                      return null;
                    }
                  };
                  const domainOf = (href) => {
                    try {
                      return new URL(href).hostname.toLowerCase();
                    } catch {
                      return "";
                    }
                  };
                  const compact = (text) =>
                    (text || "")
                      .replace(/\\u00a0/g, " ")
                      .replace(/\\s+/g, " ")
                      .trim();

                  const queryTerms = (query || "")
                    .toLowerCase()
                    .split(/[^a-z0-9]+/)
                    .filter((t) => t.length >= 3);

                  const looksLikeNav = (url, title) => {
                    const u = (url || "").toLowerCase();
                    const t = (title || "").toLowerCase();
                    if (u.includes('#skip') || u.endsWith('#') || u.includes('javascript:')) return true;
                    if (['about cms', 'newsroom', 'data & research', 'search', 'contact us'].includes(t)) return true;
                    return false;
                  };

                  const scoreResult = (item) => {
                    const hay = `${item.title} ${item.snippet} ${item.url}`.toLowerCase();
                    let score = 0;
                    for (const term of queryTerms) {
                      const hits = hay.split(term).length - 1;
                      score += hits * 3;
                    }
                    if (hay.includes('diabetes')) score += 8;
                    if (hay.includes('coverage')) score += 6;
                    if (hay.includes('medicare-coverage-database')) score += 7;
                    if (hay.includes('policy article') || hay.includes('decision memo')) score += 5;
                    if (looksLikeNav(item.url, item.title)) score -= 12;
                    return score;
                  };

                  const collectFromRows = (rows) => {
                    const list = [];
                    for (const row of rows) {
                      const anchor = row.querySelector('a[href]');
                      if (!anchor) continue;
                      const absolute = normalize(anchor.getAttribute('href'));
                      if (!absolute) continue;
                      if (!(absolute.startsWith('http://') || absolute.startsWith('https://'))) continue;
                      if (allowedDomain && domainOf(absolute) !== allowedDomain) continue;

                      const title = compact(anchor.innerText || anchor.textContent).slice(0, 180);
                      let snippetRaw = compact(row.innerText || row.textContent);
                      if (snippetRaw === title) {
                        const maybe = row.querySelector('.snippet, .description, p, .search-snippet, .gs-snippet');
                        if (maybe) snippetRaw = compact(maybe.innerText || maybe.textContent);
                      }
                      const snippet = snippetRaw.length > 420 ? snippetRaw.slice(0, 420) + "..." : snippetRaw;
                      if (!title && !snippet) continue;
                      list.push({ url: absolute, title, snippet });
                    }
                    return list;
                  };

                  const seen = new Set();
                  const results = [];

                  // Prefer explicit search-result containers.
                  const rowSelectors = [
                    ".search-item-list .result",
                    ".search-item-list .search-results",
                    ".gsc-webResult.gsc-result",
                    ".gsc-result",
                    ".views-row",
                    "main article.search-result",
                    "main li.search-result",
                  ];

                  let candidates = [];
                  for (const sel of rowSelectors) {
                    const rows = Array.from(document.querySelectorAll(sel));
                    if (!rows.length) continue;
                    candidates = candidates.concat(collectFromRows(rows));
                  }

                  // Fallback to anchors in main content if no explicit rows.
                  if (!candidates.length) {
                    const anchors = Array.from(
                      document.querySelectorAll("main a[href], [role='main'] a[href]")
                    );
                    for (const anchor of anchors) {
                      const absolute = normalize(anchor.getAttribute("href"));
                      if (!absolute) continue;
                      if (!(absolute.startsWith("http://") || absolute.startsWith("https://"))) continue;
                      if (allowedDomain && domainOf(absolute) !== allowedDomain) continue;
                      const title = compact(anchor.innerText || anchor.textContent).slice(0, 180);
                      const container = anchor.closest(".result, article, li, section, div");
                      const snippetRaw = container ? compact(container.innerText || container.textContent) : "";
                      const snippet = snippetRaw.length > 360 ? snippetRaw.slice(0, 360) + "..." : snippetRaw;
                      if (!title && !snippet) continue;
                      candidates.push({ url: absolute, title, snippet });
                    }
                  }

                  // Deduplicate + score + sort.
                  const deduped = [];
                  for (const item of candidates) {
                    if (!item.url) continue;
                    if (seen.has(item.url)) continue;
                    seen.add(item.url);
                    deduped.push({ ...item, score: scoreResult(item) });
                  }

                  deduped.sort((a, b) => b.score - a.score);

                  for (const item of deduped) {
                    if (results.length >= maxResults) break;
                    if (looksLikeNav(item.url, item.title) && item.score < 2) continue;
                    results.push({
                      url: item.url,
                      title: item.title,
                      snippet: item.snippet,
                      score: item.score,
                    });
                  }

                  const bodyText = (document.body?.innerText || '').toLowerCase();

                  return {
                    search_url: window.location.href,
                    page_title: document.title || "",
                    extracted_candidates: deduped.length,
                    has_no_terms_prompt: bodyText.includes('please enter some search terms'),
                    results,
                  };
                }
                """,
                {
                    "maxResults": input.max_results,
                    "allowedDomain": allowed_domain or "",
                    "query": input.search_query,
                },
            )
            page.close()
        finally:
            browser.close()

    return {
        "success": True,
        "start_url": input.start_url,
        "search_query": input.search_query,
        "search_url": extracted.get("search_url", input.start_url),
        "page_title": extracted.get("page_title", ""),
        "extracted_candidates": extracted.get("extracted_candidates", 0),
        "has_no_terms_prompt": extracted.get("has_no_terms_prompt", False),
        "results": extracted.get("results", []),
        "search_strategy": search_strategy,
        "search_selector": search_selector,
        "visited_search_urls": visited_search_urls,
        "session_id": session_id,
        "fetched_at": _now_iso(),
    }


def _extract_text_from_document_bytes(file_bytes: bytes, filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(file_bytes))
        page_texts = []
        for page in reader.pages:
            page_texts.append(page.extract_text() or "")
        return "\n\n".join(page_texts).strip()

    if lowered.endswith(".docx"):
        from docx import Document

        document = Document(BytesIO(file_bytes))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
        return "\n".join(paragraphs).strip()

    if lowered.endswith((".html", ".htm", ".xml")):
        text = _safe_decode_text(file_bytes)
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    return _safe_decode_text(file_bytes).strip()


def _get_es_client(elasticsearch_url: str, elasticsearch_api_key: str):
    from elasticsearch import Elasticsearch

    url = _resolve_required(elasticsearch_url, "ELASTICSEARCH_URL", "Elasticsearch URL")
    api_key = _resolve_required(elasticsearch_api_key, "ELASTIC_API_KEY", "Elasticsearch API key")
    return Elasticsearch(url, api_key=api_key)


def _ensure_index(client, index: str) -> None:
    if client.indices.exists(index=index):
        return

    mappings = {
        "properties": {
            "run_id": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "query": {"type": "text"},
            "website": {"type": "keyword"},
            "url": {"type": "keyword"},
            "title": {"type": "text"},
            "content": {"type": "text"},
            "note": {"type": "text"},
            "agent_answer": {"type": "text"},
            "created_at": {"type": "date"},
            "metadata": {"type": "object", "enabled": True},
        }
    }

    client.indices.create(index=index, mappings=mappings)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------
@function(image=browser_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"])
def browser_fetch_page(input: BrowserFetchInput) -> dict[str, Any]:
    """Fetch a page with Browserbase and return title/text/links."""
    try:
        return _collect_page_with_browserbase(input)
    except Exception as exc:
        return {
            "success": False,
            "url": input.url,
            "error": str(exc),
        }


@function(image=browser_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"])
def browser_find_relevant_snippets(input: BrowserSnippetInput) -> dict[str, Any]:
    """Fetch a page with Browserbase and return query-relevant snippets."""
    fetch_input = BrowserFetchInput(
        url=input.url,
        allowed_domain=input.allowed_domain,
        max_links=20,
        max_chars=12000,
        browserbase_project_id=input.browserbase_project_id,
        browserbase_api_key=input.browserbase_api_key,
    )

    try:
        page_result = _collect_page_with_browserbase(fetch_input)
    except Exception as exc:
        return {
            "success": False,
            "url": input.url,
            "error": str(exc),
        }

    snippets = _extract_snippets(
        page_result.get("text", ""),
        input.query,
        input.max_snippets,
        input.snippet_chars,
    )

    return {
        "success": True,
        "url": page_result.get("url"),
        "title": page_result.get("title", ""),
        "snippets": snippets,
        "links": page_result.get("links", []),
    }


@function(image=browser_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"])
def browser_search_site(input: BrowserSearchInput) -> dict[str, Any]:
    """Use website search UI (or fallback search endpoint) to collect relevant result links."""
    try:
        return _search_site_with_browserbase(input)
    except Exception as exc:
        return {
            "success": False,
            "start_url": input.start_url,
            "search_query": input.search_query,
            "error": str(exc),
        }


@function(image=document_image)
def download_file(input: DownloadFileInput) -> dict[str, Any]:
    """Download a file and return base64 content for downstream tools."""
    import requests

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.url)
    if allowed_domain and requested_domain != allowed_domain:
        return {
            "success": False,
            "url": input.url,
            "error": (
                f"Requested URL domain '{requested_domain}' is outside allowed domain "
                f"'{allowed_domain}'."
            ),
        }

    try:
        response = requests.get(input.url, timeout=input.timeout_seconds)
        response.raise_for_status()
        content = response.content
        if len(content) > input.max_bytes:
            return {
                "success": False,
                "url": input.url,
                "error": f"File exceeds max_bytes ({len(content)} > {input.max_bytes}).",
                "size_bytes": len(content),
            }

        content_type = response.headers.get("content-type", "")
        filename = input.url.split("/")[-1] or "downloaded_file"
        return {
            "success": True,
            "url": input.url,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "file_b64": b64encode(content).decode("ascii"),
        }
    except Exception as exc:
        return {"success": False, "url": input.url, "error": str(exc)}


@function(image=document_image)
def extract_archive(input: ExtractArchiveInput) -> dict[str, Any]:
    """Extract ZIP/TAR archives and return file payloads (base64) for nested parsing."""
    try:
        archive_bytes = b64decode(input.file_b64)
    except Exception as exc:
        return {"success": False, "filename": input.filename, "error": f"Invalid base64: {exc}"}

    extracted_files: list[dict[str, Any]] = []

    try:
        lowered = input.filename.lower()
        if lowered.endswith(".zip"):
            with zipfile.ZipFile(BytesIO(archive_bytes), "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if len(extracted_files) >= input.max_files:
                        break
                    with zf.open(info, "r") as f:
                        data = f.read(input.max_bytes_per_file + 1)
                    if len(data) > input.max_bytes_per_file:
                        continue
                    extracted_files.append(
                        {
                            "name": info.filename,
                            "size_bytes": len(data),
                            "file_b64": b64encode(data).decode("ascii"),
                        }
                    )
        elif lowered.endswith(".tar") or lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
            mode = "r:gz" if lowered.endswith(".gz") or lowered.endswith(".tgz") else "r:"
            with tarfile.open(fileobj=BytesIO(archive_bytes), mode=mode) as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    if len(extracted_files) >= input.max_files:
                        break
                    file_obj = tf.extractfile(member)
                    if file_obj is None:
                        continue
                    data = file_obj.read(input.max_bytes_per_file + 1)
                    if len(data) > input.max_bytes_per_file:
                        continue
                    extracted_files.append(
                        {
                            "name": member.name,
                            "size_bytes": len(data),
                            "file_b64": b64encode(data).decode("ascii"),
                        }
                    )
        else:
            return {
                "success": False,
                "filename": input.filename,
                "error": "Unsupported archive format. Use zip/tar/tar.gz/tgz.",
            }
    except Exception as exc:
        return {"success": False, "filename": input.filename, "error": str(exc)}

    return {
        "success": True,
        "filename": input.filename,
        "files_count": len(extracted_files),
        "files": extracted_files,
    }


@function(image=document_image, secrets=["OPENAI_API_KEY"])
def document_to_markdown(input: DocumentToMarkdownInput) -> dict[str, Any]:
    """Convert document bytes into markdown using an OpenAI foundation model."""
    from openai import OpenAI
    try:
        file_bytes = b64decode(input.file_b64)
    except Exception as exc:
        return {"success": False, "filename": input.filename, "error": f"Invalid base64: {exc}"}

    try:
        raw_text = _extract_text_from_document_bytes(file_bytes, input.filename)
    except Exception as exc:
        return {
            "success": False,
            "filename": input.filename,
            "error": f"Could not parse file content: {exc}",
        }

    if not raw_text:
        return {
            "success": False,
            "filename": input.filename,
            "error": "No textual content could be extracted from this document.",
        }

    raw_text = raw_text[: input.max_chars]

    try:
        client = OpenAI(api_key=_resolve_required("", "OPENAI_API_KEY", "OpenAI API key"))
        focus = input.query.strip() if input.query else "the overall content"
        response = client.chat.completions.create(
            model=input.openai_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a document conversion assistant. Convert the provided raw "
                        "document text into concise, well-structured markdown. Preserve facts "
                        "and include headings, bullet points, and key details."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Focus on: {focus}\n"
                        f"Filename: {input.filename}\n\n"
                        "Raw extracted text:\n"
                        f"{raw_text}"
                    ),
                },
            ],
        )
        markdown = (response.choices[0].message.content or "").strip()
        if not markdown:
            markdown = raw_text
        return {
            "success": True,
            "filename": input.filename,
            "markdown": markdown,
            "source_chars": len(raw_text),
        }
    except Exception as exc:
        return {
            "success": False,
            "filename": input.filename,
            "error": f"Foundation-model markdown conversion failed: {exc}",
        }


@function(image=elastic_image, secrets=["ELASTICSEARCH_URL", "ELASTIC_API_KEY"])
def elasticsearch_index_note(input: ElasticsearchIndexNoteInput) -> dict[str, Any]:
    """Index an agent note/finding into Elasticsearch."""
    client = _get_es_client(input.elasticsearch_url, input.elasticsearch_api_key)
    try:
        _ensure_index(client, input.index)
        doc_type = str(input.metadata.get("doc_type", "agent_note"))
        doc = {
            "run_id": input.run_id,
            "doc_type": doc_type,
            "query": input.query,
            "website": input.website,
            "url": input.url,
            "note": input.note,
            "created_at": _now_iso(),
            "metadata": input.metadata,
        }
        resp = client.index(index=input.index, document=doc, refresh=True)
        return {
            "success": True,
            "index": input.index,
            "id": resp.get("_id"),
            "result": resp.get("result"),
        }
    finally:
        client.close()


@function(image=elastic_image, secrets=["ELASTICSEARCH_URL", "ELASTIC_API_KEY"])
def elasticsearch_search_notes(input: ElasticsearchSearchInput) -> dict[str, Any]:
    """Search previously indexed notes/pages in Elasticsearch."""
    client = _get_es_client(input.elasticsearch_url, input.elasticsearch_api_key)
    try:
        filters: list[dict[str, Any]] = []
        if input.run_id:
            filters.append({"term": {"run_id": input.run_id}})

        query = {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": input.query,
                            "fields": [
                                "note^3",
                                "agent_answer^3",
                                "title^2",
                                "content",
                                "query",
                            ],
                        }
                    }
                ],
                "filter": filters,
            }
        }

        resp = client.search(index=input.index, query=query, size=input.size)
        hits = resp.get("hits", {}).get("hits", [])

        return {
            "success": True,
            "count": len(hits),
            "hits": [
                {
                    "id": hit.get("_id"),
                    "score": hit.get("_score"),
                    "source": hit.get("_source", {}),
                }
                for hit in hits
            ],
        }
    finally:
        client.close()


@function(image=elastic_image, secrets=["ELASTICSEARCH_URL", "ELASTIC_API_KEY"])
def elasticsearch_bulk_ingest_pages(input: ElasticsearchBulkIngestInput) -> dict[str, Any]:
    """Bulk ingest visited pages into Elasticsearch."""
    from elasticsearch import helpers

    client = _get_es_client(input.elasticsearch_url, input.elasticsearch_api_key)
    try:
        _ensure_index(client, input.index)

        actions = []
        for page in input.pages:
            actions.append(
                {
                    "_op_type": "index",
                    "_index": input.index,
                    "_source": {
                        "run_id": input.run_id,
                        "doc_type": "page",
                        "query": input.query,
                        "website": input.website,
                        "url": page.get("url"),
                        "title": page.get("title", ""),
                        "content": page.get("text", ""),
                        "created_at": page.get("fetched_at", _now_iso()),
                        "metadata": {
                            "requested_url": page.get("requested_url"),
                            "link_count": len(page.get("links", [])),
                            "session_id": page.get("session_id"),
                        },
                    },
                }
            )

        if not actions:
            return {"success": True, "indexed_pages": 0, "errors": 0}

        indexed_count, errors = helpers.bulk(client, actions, raise_on_error=False)
        return {
            "success": True,
            "indexed_pages": indexed_count,
            "errors": len(errors),
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Tool schemas and dispatch for OpenAI tool calling
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_fetch_page",
            "description": (
                "Fetch a URL in Browserbase and return title, visible text, and in-domain links."
            ),
            "parameters": _pydantic_schema(BrowserFetchInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_find_relevant_snippets",
            "description": "Fetch a URL and return snippets relevant to the query.",
            "parameters": _pydantic_schema(BrowserSnippetInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search_site",
            "description": (
                "Use the site's search input (or fallback search URL) to find relevant "
                "pages for a query."
            ),
            "parameters": _pydantic_schema(BrowserSearchInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "Download a document or archive and return base64 bytes.",
            "parameters": _pydantic_schema(DownloadFileInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_archive",
            "description": "Extract zip/tar archive bytes into file entries.",
            "parameters": _pydantic_schema(ExtractArchiveInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "document_to_markdown",
            "description": (
                "Convert document bytes (PDF, DOCX, text, HTML, JSON, CSV) into markdown "
                "using a foundation model."
            ),
            "parameters": _pydantic_schema(DocumentToMarkdownInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "elasticsearch_index_note",
            "description": "Persist an intermediate insight or note into Elasticsearch.",
            "parameters": _pydantic_schema(ElasticsearchIndexNoteInput),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "elasticsearch_search_notes",
            "description": "Search previously indexed run documents in Elasticsearch.",
            "parameters": _pydantic_schema(ElasticsearchSearchInput),
        },
    },
]

BROWSER_TOOL_NAMES = {
    "browser_fetch_page",
    "browser_find_relevant_snippets",
    "browser_search_site",
    "download_file",
    "extract_archive",
    "document_to_markdown",
}
BROWSER_TOOLS = [tool for tool in TOOLS if tool["function"]["name"] in BROWSER_TOOL_NAMES]
ELASTIC_TOOLS = [tool for tool in TOOLS if tool["function"]["name"] not in BROWSER_TOOL_NAMES]

TOOL_DISPATCH: dict[str, dict[str, Any]] = {
    "browser_fetch_page": {"fn": browser_fetch_page, "input_model": BrowserFetchInput},
    "browser_find_relevant_snippets": {
        "fn": browser_find_relevant_snippets,
        "input_model": BrowserSnippetInput,
    },
    "browser_search_site": {"fn": browser_search_site, "input_model": BrowserSearchInput},
    "download_file": {"fn": download_file, "input_model": DownloadFileInput},
    "extract_archive": {"fn": extract_archive, "input_model": ExtractArchiveInput},
    "document_to_markdown": {"fn": document_to_markdown, "input_model": DocumentToMarkdownInput},
    "elasticsearch_index_note": {
        "fn": elasticsearch_index_note,
        "input_model": ElasticsearchIndexNoteInput,
    },
    "elasticsearch_search_notes": {
        "fn": elasticsearch_search_notes,
        "input_model": ElasticsearchSearchInput,
    },
}


def _execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    entry = TOOL_DISPATCH.get(name)
    if entry is None:
        return {"success": False, "error": f"Unknown tool: {name}"}

    model = entry["input_model"]
    fn = entry["fn"]
    validated = model(**args)
    return fn(validated)


# ---------------------------------------------------------------------------
# Agent harness application
# ---------------------------------------------------------------------------
@application()
@function(
    image=agent_image,
    secrets=[
        "OPENAI_API_KEY",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "ELASTICSEARCH_URL",
        "ELASTIC_API_KEY",
    ],
)
def agentic_search(input: AgenticQueryInput) -> dict[str, Any]:
    """Agentic query harness using OpenAI Agents SDK wrappers over Tensorlake tools."""
    from agents import Agent, RunConfig, Runner, function_tool
    from agents.tracing import gen_trace_id

    ctx = RequestContext.get()
    run_id = str(uuid.uuid4())
    allowed_domain = _extract_domain(input.website)

    _resolve_required("", "OPENAI_API_KEY", "OpenAI API key")
    _resolve_required(input.browserbase_api_key, "BROWSERBASE_API_KEY", "Browserbase API key")
    _resolve_required(input.browserbase_project_id, "BROWSERBASE_PROJECT_ID", "Browserbase project ID")
    if input.enable_elasticsearch:
        _resolve_required(input.elasticsearch_url, "ELASTICSEARCH_URL", "Elasticsearch URL")
        _resolve_required(input.elasticsearch_api_key, "ELASTIC_API_KEY", "Elasticsearch API key")

    traces: list[dict[str, Any]] = []
    visited_pages: dict[str, dict[str, Any]] = {}
    cached_files: dict[str, dict[str, Any]] = {}
    search_phase_reports: list[dict[str, Any]] = []
    search_observations: list[dict[str, Any]] = []
    snippet_observations: list[dict[str, Any]] = []
    candidate_urls: list[str] = []
    tool_calls_executed = 0
    final_answer = ""
    progress_current = 0.0
    agent_phase_started = False
    agents_trace_workflow_name = "Browserbase Agent Harness"
    agents_trace_id = gen_trace_id() if input.enable_tracing else None
    agents_trace_group_id = run_id
    agents_trace_metadata = {
        "harness_run_id": run_id,
        "query": input.query,
        "website": input.website,
        "allowed_domain": allowed_domain,
        "model": input.openai_model,
        "max_iterations": input.max_iterations,
        "max_pages": input.max_pages,
    }
    agents_run_config = RunConfig(
        tracing_disabled=not input.enable_tracing,
        trace_include_sensitive_data=True,
        workflow_name=agents_trace_workflow_name,
        trace_id=agents_trace_id,
        group_id=agents_trace_group_id,
        trace_metadata=agents_trace_metadata,
    )

    def _trim_text(value: str, max_chars: int) -> str:
        if not value:
            return ""
        return value[:max_chars] if len(value) <= max_chars else value[:max_chars] + "...<truncated>"

    def _emit_progress(current: float, message: str, attributes: dict[str, Any] | None = None) -> None:
        nonlocal progress_current
        progress_current = max(progress_current, min(float(current), 100.0))
        attr = {str(k): str(v) for k, v in (attributes or {}).items()}
        ctx.progress.update(progress_current, 100, message, attr)

    def _track_tool(tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        nonlocal tool_calls_executed
        tool_calls_executed += 1
        if agent_phase_started:
            _emit_progress(
                min(progress_current + 1.5, 90.0),
                f"Agent tool completed: {tool_name}",
                {
                    "tool_name": tool_name,
                    "success": bool(result.get("success", False)),
                    "tool_calls": tool_calls_executed,
                },
            )
        if tool_name == "browser_search_site":
            compact_results = []
            for item in result.get("results", []):
                if not item.get("url"):
                    continue
                compact_results.append(
                    {
                        "url": item.get("url", ""),
                        "title": _trim_text(str(item.get("title", "")), 180),
                        "snippet": _trim_text(str(item.get("snippet", "")), 320),
                    }
                )
            search_observations.append(
                {
                    "query": str(args.get("variation") or args.get("search_query") or ""),
                    "success": bool(result.get("success", False)),
                    "search_url": str(result.get("search_url", "")),
                    "error": str(result.get("error", "")),
                    "results": compact_results[: input.search_results_per_variation],
                }
            )
        elif tool_name == "browser_find_relevant_snippets" and result.get("success"):
            snippet_observations.append(
                {
                    "query": str(args.get("query", input.query)),
                    "url": str(result.get("url", args.get("url", ""))),
                    "title": _trim_text(str(result.get("title", "")), 180),
                    "snippets": [
                        _trim_text(str(snippet), 420)
                        for snippet in result.get("snippets", [])[:6]
                        if snippet
                    ],
                }
            )

    def _cache_file(filename: str, file_b64: str, source_url: str = "") -> str:
        file_id = f"file_{uuid.uuid4().hex[:10]}"
        cached_files[file_id] = {
            "filename": filename,
            "file_b64": file_b64,
            "source_url": source_url,
            "created_at": _now_iso(),
        }
        return file_id

    def _run_function_future(fn: Any, *args: Any, **kwargs: Any) -> Future:
        future_factory = getattr(fn, "future", None)
        if callable(future_factory):
            return future_factory(*args, **kwargs).run()
        awaitable_factory = getattr(fn, "awaitable", None)
        if callable(awaitable_factory):
            return awaitable_factory(*args, **kwargs).run()
        raise AttributeError(
            "Tensorlake Function object does not expose a supported future factory "
            "(`future` or `awaitable`)."
        )

    _append_trace(
        traces,
        input.enable_tracing,
        input.max_trace_events,
        "run_started",
        {
            "run_id": run_id,
            "query": input.query,
            "website": input.website,
            "allowed_domain": allowed_domain,
            "max_iterations": input.max_iterations,
            "max_pages": input.max_pages,
            "agent_timeout_seconds": input.agent_timeout_seconds,
            "auto_search_phase": input.auto_search_phase,
            "search_variations": input.search_variations,
            "search_results_per_variation": input.search_results_per_variation,
            "prefetch_from_search_results": input.prefetch_from_search_results,
            "enable_elasticsearch": input.enable_elasticsearch,
            "model": input.openai_model,
        },
    )
    _append_trace(
        traces,
        input.enable_tracing,
        input.max_trace_events,
        "agents_sdk_trace_configured",
        {
            "workflow_name": agents_trace_workflow_name,
            "trace_id": agents_trace_id,
            "group_id": agents_trace_group_id,
            "metadata": agents_trace_metadata,
        },
    )
    _emit_progress(
        2,
        "Run started",
        {
            "run_id": run_id,
            "website": input.website,
            "auto_search_phase": input.auto_search_phase,
        },
    )

    query_variations = _build_query_variations(input.query, input.search_variations)
    _append_trace(
        traces,
        input.enable_tracing,
        input.max_trace_events,
        "query_variations_built",
        {"variations": query_variations},
    )
    _emit_progress(
        5,
        "Built query variations",
        {"variations": len(query_variations)},
    )

    if input.auto_search_phase:
        search_total = max(len(query_variations), 1)
        _emit_progress(
            8,
            f"Launching {search_total} auto-search variations in parallel",
            {"variations": search_total},
        )

        search_futures: list[tuple[str, Future]] = []
        for variation in query_variations:
            search_futures.append(
                (
                    variation,
                    _run_function_future(
                        browser_search_site,
                        BrowserSearchInput(
                            start_url=input.website,
                            search_query=variation,
                            allowed_domain=allowed_domain,
                            max_results=input.search_results_per_variation,
                            browserbase_project_id=input.browserbase_project_id,
                            browserbase_api_key=input.browserbase_api_key,
                        )
                    ),
                )
            )

        if search_futures:
            Future.wait((future for _, future in search_futures), return_when=RETURN_WHEN.ALL_COMPLETED)

        for idx, (variation, future) in enumerate(search_futures):
            _emit_progress(
                8 + ((idx / search_total) * 35),
                f"Auto-search variation {idx + 1}/{search_total}",
                {"variation": variation},
            )

            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "success": False,
                    "error": f"Auto-search future failed: {exc}",
                    "search_url": "",
                    "results": [],
                }
            _track_tool("browser_search_site", {"variation": variation}, result)

            compact_results = []
            if result.get("success"):
                for item in result.get("results", []):
                    url = item.get("url")
                    if not url:
                        continue
                    compact_results.append(
                        {
                            "url": url,
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                        }
                    )
                    if url not in candidate_urls:
                        candidate_urls.append(url)

            search_phase_reports.append(
                {
                    "variation": variation,
                    "success": result.get("success", False),
                    "search_url": result.get("search_url", ""),
                    "results": compact_results[: input.search_results_per_variation],
                    "error": result.get("error", ""),
                }
            )
            _emit_progress(
                8 + (((idx + 1) / search_total) * 35),
                f"Finished search variation {idx + 1}/{search_total}",
                {
                    "variation": variation,
                    "success": bool(result.get("success", False)),
                    "results_count": len(compact_results),
                },
            )

        prefetch_budget = min(
            input.prefetch_from_search_results,
            max(0, input.max_pages - len(visited_pages)),
        )
        prefetch_targets: list[str] = []
        for url in candidate_urls:
            if len(prefetch_targets) >= prefetch_budget:
                break
            if url in visited_pages:
                continue
            prefetch_targets.append(url)

        prefetch_count = 0
        if prefetch_targets:
            _emit_progress(
                45,
                "Launching snippet prefetch calls in parallel",
                {"targets": len(prefetch_targets)},
            )

        snippet_futures: list[tuple[str, Future]] = [
            (
                url,
                _run_function_future(
                    browser_find_relevant_snippets,
                    BrowserSnippetInput(
                        url=url,
                        query=input.query,
                        allowed_domain=allowed_domain,
                        max_snippets=5,
                        browserbase_project_id=input.browserbase_project_id,
                        browserbase_api_key=input.browserbase_api_key,
                    )
                ),
            )
            for url in prefetch_targets
        ]
        if snippet_futures:
            Future.wait((future for _, future in snippet_futures), return_when=RETURN_WHEN.ALL_COMPLETED)

        for idx, (url, future) in enumerate(snippet_futures):
            _emit_progress(
                45 + (idx * 5),
                f"Prefetching snippets from candidate URL {idx + 1}",
                {"url": url},
            )
            try:
                snippet_result = future.result()
            except Exception as exc:
                snippet_result = {
                    "success": False,
                    "error": f"Snippet prefetch future failed: {exc}",
                    "url": url,
                }
            _track_tool("browser_find_relevant_snippets", {"url": url}, snippet_result)
            if snippet_result.get("success"):
                snippet_url = snippet_result.get("url")
                if snippet_url and snippet_url not in visited_pages:
                    visited_pages[snippet_url] = {
                        "requested_url": url,
                        "url": snippet_url,
                        "title": snippet_result.get("title", ""),
                        "text": "\n".join(snippet_result.get("snippets", [])),
                        "links": snippet_result.get("links", []),
                        "fetched_at": _now_iso(),
                    }
                    prefetch_count += 1
        _emit_progress(
            58,
            "Search discovery phase complete",
            {"candidate_urls": len(candidate_urls), "prefetched_pages": prefetch_count},
        )
    else:
        _emit_progress(58, "Auto-search phase skipped")

    @function_tool
    def search_site(search_query: str) -> str:
        """Search the target site using its search bar with a query variation."""
        result = browser_search_site(
            BrowserSearchInput(
                start_url=input.website,
                search_query=search_query,
                allowed_domain=allowed_domain,
                max_results=input.search_results_per_variation,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        _track_tool("browser_search_site", {"search_query": search_query}, result)
        if result.get("success"):
            for item in result.get("results", []):
                url = item.get("url")
                if url and url not in candidate_urls:
                    candidate_urls.append(url)
        return json.dumps(result)

    @function_tool
    def fetch_page(url: str, max_chars: int = 9000, max_links: int = 25) -> str:
        """Fetch page content and links from a URL."""
        if len(visited_pages) >= input.max_pages and url not in visited_pages:
            result = {"success": False, "error": f"Max page budget reached ({input.max_pages}).", "url": url}
            _track_tool("browser_fetch_page", {"url": url}, result)
            return json.dumps(result)

        result = browser_fetch_page(
            BrowserFetchInput(
                url=url,
                allowed_domain=allowed_domain,
                max_chars=max_chars,
                max_links=max_links,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        _track_tool("browser_fetch_page", {"url": url}, result)
        if result.get("success"):
            fetched_url = result.get("url")
            if fetched_url and fetched_url not in visited_pages and len(visited_pages) < input.max_pages:
                visited_pages[fetched_url] = result
        return json.dumps(result)

    @function_tool
    def find_snippets(url: str, query: str) -> str:
        """Find query-relevant snippets from a page."""
        if len(visited_pages) >= input.max_pages and url not in visited_pages:
            result = {"success": False, "error": f"Max page budget reached ({input.max_pages}).", "url": url}
            _track_tool("browser_find_relevant_snippets", {"url": url, "query": query}, result)
            return json.dumps(result)

        result = browser_find_relevant_snippets(
            BrowserSnippetInput(
                url=url,
                query=query,
                allowed_domain=allowed_domain,
                max_snippets=6,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        _track_tool("browser_find_relevant_snippets", {"url": url, "query": query}, result)
        if result.get("success"):
            snippet_url = result.get("url")
            if snippet_url and snippet_url not in visited_pages and len(visited_pages) < input.max_pages:
                visited_pages[snippet_url] = {
                    "requested_url": url,
                    "url": snippet_url,
                    "title": result.get("title", ""),
                    "text": "\n".join(result.get("snippets", [])),
                    "links": result.get("links", []),
                    "fetched_at": _now_iso(),
                }
        return json.dumps(result)

    @function_tool
    def download_document(url: str) -> str:
        """Download a file URL and cache it for archive/document tools."""
        result = download_file(
            DownloadFileInput(url=url, allowed_domain=allowed_domain, max_bytes=8_000_000)
        )
        _track_tool("download_file", {"url": url}, result)
        if not result.get("success"):
            return json.dumps(result)

        file_id = _cache_file(result.get("filename", "downloaded_file"), result["file_b64"], source_url=url)
        return json.dumps(
            {
                "success": True,
                "file_id": file_id,
                "url": url,
                "filename": result.get("filename"),
                "size_bytes": result.get("size_bytes"),
                "content_type": result.get("content_type", ""),
                "is_archive": _is_archive_filename(result.get("filename", "")),
                "is_document": _is_document_filename(result.get("filename", "")),
            }
        )

    @function_tool
    def unzip_file(file_id: str, max_files: int = 25) -> str:
        """Extract a cached archive and return child file IDs."""
        cached = cached_files.get(file_id)
        if not cached:
            return json.dumps({"success": False, "error": f"Unknown file_id: {file_id}"})

        result = extract_archive(
            ExtractArchiveInput(
                file_b64=cached["file_b64"],
                filename=cached["filename"],
                max_files=max_files,
            )
        )
        _track_tool("extract_archive", {"file_id": file_id, "max_files": max_files}, result)
        if not result.get("success"):
            return json.dumps(result)

        children = []
        for item in result.get("files", []):
            child_id = _cache_file(
                filename=item.get("name", "archive_file"),
                file_b64=item.get("file_b64", ""),
                source_url=cached.get("source_url", ""),
            )
            children.append(
                {
                    "file_id": child_id,
                    "name": item.get("name", ""),
                    "size_bytes": item.get("size_bytes", 0),
                    "is_archive": _is_archive_filename(item.get("name", "")),
                    "is_document": _is_document_filename(item.get("name", "")),
                }
            )

        return json.dumps(
            {
                "success": True,
                "source_file_id": file_id,
                "files_count": len(children),
                "files": children,
            }
        )

    @function_tool
    def read_document_as_markdown(file_id: str, focus_query: str = "") -> str:
        """Convert a cached document file into markdown via foundation model."""
        cached = cached_files.get(file_id)
        if not cached:
            return json.dumps({"success": False, "error": f"Unknown file_id: {file_id}"})

        result = document_to_markdown(
            DocumentToMarkdownInput(
                file_b64=cached["file_b64"],
                filename=cached["filename"],
                query=focus_query or input.query,
                openai_model=input.openai_model,
                max_chars=25_000,
            )
        )
        _track_tool("document_to_markdown", {"file_id": file_id, "focus_query": focus_query}, result)
        if result.get("success"):
            doc_url = cached.get("source_url") or f"file://{cached['filename']}"
            visit_key = doc_url if doc_url not in visited_pages else f"{doc_url}#{file_id}"
            if len(visited_pages) < input.max_pages:
                visited_pages[visit_key] = {
                    "requested_url": doc_url,
                    "url": doc_url,
                    "title": cached["filename"],
                    "text": result.get("markdown", "")[:9000],
                    "links": [],
                    "fetched_at": _now_iso(),
                }

        payload = dict(result)
        if payload.get("markdown") and len(payload["markdown"]) > 6000:
            payload["markdown"] = payload["markdown"][:6000] + "\n\n...[truncated]"
        return json.dumps(payload)

    openai_tools = [
        search_site,
        fetch_page,
        find_snippets,
        download_document,
        unzip_file,
        read_document_as_markdown,
    ]

    if input.enable_elasticsearch:
        @function_tool
        def index_note(note: str, url: str = "") -> str:
            """Index an intermediate note in Elasticsearch."""
            result = elasticsearch_index_note(
                ElasticsearchIndexNoteInput(
                    index=input.elasticsearch_index,
                    run_id=run_id,
                    query=input.query,
                    website=input.website,
                    note=note,
                    url=url or input.website,
                    metadata={},
                    elasticsearch_url=input.elasticsearch_url,
                    elasticsearch_api_key=input.elasticsearch_api_key,
                )
            )
            _track_tool("elasticsearch_index_note", {"url": url}, result)
            return json.dumps(result)

        @function_tool
        def search_notes(query: str, size: int = 5) -> str:
            """Search run artifacts in Elasticsearch."""
            result = elasticsearch_search_notes(
                ElasticsearchSearchInput(
                    index=input.elasticsearch_index,
                    query=query,
                    run_id=run_id,
                    size=size,
                    elasticsearch_url=input.elasticsearch_url,
                    elasticsearch_api_key=input.elasticsearch_api_key,
                )
            )
            _track_tool("elasticsearch_search_notes", {"query": query, "size": size}, result)
            return json.dumps(result)

        openai_tools.extend([index_note, search_notes])

    presearch_summary = [
        {
            "variation": item["variation"],
            "success": item["success"],
            "search_url": item["search_url"],
            "top_results": item["results"][:5],
        }
        for item in search_phase_reports
    ]

    prompt = build_agent_prompt(
        run_id=run_id,
        query=input.query,
        website=input.website,
        allowed_domain=allowed_domain,
        max_pages=input.max_pages,
        query_variations_json=json.dumps(query_variations),
        presearch_summary_json=json.dumps(presearch_summary),
    )

    _emit_progress(60, "Running OpenAI Agents SDK orchestration", {"run_id": run_id})
    agent = Agent(
        name="Browserbase File-Aware Research Agent",
        instructions=SYSTEM_INSTRUCTIONS,
        tools=openai_tools,
        model=input.openai_model,
    )
    try:
        agent_phase_started = True
        current_thread = threading.current_thread()
        _append_trace(
            traces,
            input.enable_tracing,
            input.max_trace_events,
            "agent_runner_started",
            {
                "max_turns": input.max_iterations,
                "timeout_seconds": input.agent_timeout_seconds,
                "thread_name": current_thread.name,
                "is_main_thread": current_thread is threading.main_thread(),
                "trace_id": agents_trace_id,
                "trace_group_id": agents_trace_group_id,
                "trace_workflow_name": agents_trace_workflow_name,
            },
        )

        async def _run_agent_with_timeout() -> Any:
            try:
                return await asyncio.wait_for(
                    Runner.run(
                        agent,
                        prompt,
                        max_turns=input.max_iterations,
                        run_config=agents_run_config,
                    ),
                    timeout=input.agent_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Agent orchestration timed out after {input.agent_timeout_seconds} seconds."
                ) from exc

        def _run_agent_with_safe_asyncio() -> Any:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_run_agent_with_timeout())

            thread_result: dict[str, Any] = {}
            thread_error: dict[str, Exception] = {}

            def _thread_target() -> None:
                try:
                    thread_result["value"] = asyncio.run(_run_agent_with_timeout())
                except Exception as exc:  # pragma: no cover - defensive runtime path
                    thread_error["error"] = exc

            helper = threading.Thread(
                target=_thread_target,
                name="agent-runner-asyncio",
                daemon=True,
            )
            helper.start()
            helper.join(input.agent_timeout_seconds + 5)
            if helper.is_alive():
                raise TimeoutError(
                    f"Agent orchestration timed out after {input.agent_timeout_seconds} seconds."
                )
            if "error" in thread_error:
                raise thread_error["error"]
            return thread_result.get("value")

        try:
            with register_agents_trace_sink(
                enabled=input.enable_tracing,
                trace_id=agents_trace_id,
                run_id=run_id,
                workflow_name=agents_trace_workflow_name,
                group_id=agents_trace_group_id,
                traces=traces,
                max_events=input.max_trace_events,
            ):
                run_result = _run_agent_with_safe_asyncio()
        finally:
            agent_phase_started = False

        final_answer = str(run_result.final_output or "").strip()
        _emit_progress(88, "Agent orchestration complete", {"tool_calls": tool_calls_executed})
    except TimeoutError as exc:
        agent_phase_started = False
        final_answer = f"Agent execution timed out: {exc}"
        _append_trace(
            traces,
            input.enable_tracing,
            input.max_trace_events,
            "agent_runner_timeout",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        _emit_progress(
            88,
            "Agent orchestration timed out",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
    except Exception as exc:
        agent_phase_started = False
        final_answer = f"Agent execution failed: {exc}"
        _append_trace(
            traces,
            input.enable_tracing,
            input.max_trace_events,
            "agent_runner_exception",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        _emit_progress(
            88,
            "Agent orchestration failed",
            {"error": str(exc), "error_type": type(exc).__name__},
        )

    if not final_answer:
        final_answer = "No final answer produced by the agent."
        _append_trace(
            traces,
            input.enable_tracing,
            input.max_trace_events,
            "final_answer_missing",
            {"reason": "empty_agent_output"},
        )

    if not visited_pages:
        bootstrap = browser_fetch_page(
            BrowserFetchInput(
                url=input.website,
                allowed_domain=allowed_domain,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        _track_tool("browser_fetch_page", {"url": input.website, "bootstrap": True}, bootstrap)
        if bootstrap.get("success"):
            visited_pages[bootstrap.get("url", input.website)] = bootstrap

    citations = [
        {"url": page.get("url"), "title": page.get("title", "")}
        for page in visited_pages.values()
        if page.get("url")
    ]

    ranked_hits: dict[str, dict[str, Any]] = {}
    for observation in search_observations:
        if not observation.get("success"):
            continue
        query_text = observation.get("query", "")
        for rank, hit in enumerate(observation.get("results", [])):
            url = hit.get("url", "")
            if not url:
                continue
            score = max(input.search_results_per_variation - rank, 1)
            existing = ranked_hits.setdefault(
                url,
                {
                    "url": url,
                    "title": hit.get("title", ""),
                    "sample_snippet": hit.get("snippet", ""),
                    "score": 0,
                    "times_seen": 0,
                    "queries": [],
                },
            )
            existing["score"] += score
            existing["times_seen"] += 1
            if query_text and query_text not in existing["queries"]:
                existing["queries"].append(query_text)
            if not existing.get("sample_snippet") and hit.get("snippet"):
                existing["sample_snippet"] = hit.get("snippet", "")

    top_search_hits = sorted(
        ranked_hits.values(),
        key=lambda item: (int(item.get("score", 0)), int(item.get("times_seen", 0))),
        reverse=True,
    )[:12]

    search_evidence = {
        "search_calls": len(search_observations),
        "successful_search_calls": sum(1 for item in search_observations if item.get("success")),
        "queries_attempted": [item.get("query", "") for item in search_observations if item.get("query")],
        "top_search_hits": top_search_hits,
        "snippet_observations": snippet_observations[:12],
        "raw_search_observations": search_observations[:20],
    }

    if input.enable_elasticsearch:
        _emit_progress(92, "Ingesting run artifacts into Elasticsearch", {"run_id": run_id})
        ingest_result = elasticsearch_bulk_ingest_pages(
            ElasticsearchBulkIngestInput(
                index=input.elasticsearch_index,
                run_id=run_id,
                query=input.query,
                website=input.website,
                pages=list(visited_pages.values()),
                elasticsearch_url=input.elasticsearch_url,
                elasticsearch_api_key=input.elasticsearch_api_key,
            )
        )
        _track_tool("elasticsearch_bulk_ingest_pages", {"pages": len(visited_pages)}, ingest_result)

        answer_note_result = elasticsearch_index_note(
            ElasticsearchIndexNoteInput(
                index=input.elasticsearch_index,
                run_id=run_id,
                query=input.query,
                website=input.website,
                note=final_answer,
                url=input.website,
                metadata={
                    "doc_type": "final_answer",
                    "evidence_urls": list(visited_pages.keys()),
                    "tool_calls": tool_calls_executed,
                },
                elasticsearch_url=input.elasticsearch_url,
                elasticsearch_api_key=input.elasticsearch_api_key,
            )
        )
        _track_tool("elasticsearch_index_note", {"final_answer": True}, answer_note_result)
        elasticsearch_result = {
            "enabled": True,
            "bulk_ingest": ingest_result,
            "final_answer_note": answer_note_result,
            "index": input.elasticsearch_index,
        }
        _emit_progress(
            98,
            "Elasticsearch ingestion complete",
            {
                "indexed_pages": ingest_result.get("indexed_pages", 0),
                "index": input.elasticsearch_index,
            },
        )
    else:
        elasticsearch_result = {
            "enabled": False,
            "message": "Elasticsearch disabled for this run; no documents were indexed.",
        }
        _append_trace(
            traces,
            input.enable_tracing,
            input.max_trace_events,
            "elasticsearch_skipped",
            {"reason": "disabled"},
        )
        _emit_progress(98, "Elasticsearch skipped")

    _append_trace(
        traces,
        input.enable_tracing,
        input.max_trace_events,
        "run_completed",
        {
            "pages_visited": len(visited_pages),
            "tool_calls": tool_calls_executed,
            "citations": len(citations),
            "cached_files": len(cached_files),
        },
    )
    _emit_progress(
        100,
        "Run complete",
        {
            "pages_visited": len(visited_pages),
            "citations": len(citations),
            "tool_calls": tool_calls_executed,
        },
    )

    return {
        "run_id": run_id,
        "query": input.query,
        "website": input.website,
        "answer": final_answer,
        "query_variations": query_variations,
        "auto_search": {
            "enabled": input.auto_search_phase,
            "variations_attempted": len(search_phase_reports),
            "candidate_urls_count": len(candidate_urls),
            "reports": search_phase_reports,
        },
        "search_evidence": search_evidence,
        "cached_files": {
            "count": len(cached_files),
            "files": [
                {
                    "file_id": file_id,
                    "filename": file_info.get("filename", ""),
                    "source_url": file_info.get("source_url", ""),
                }
                for file_id, file_info in cached_files.items()
            ],
        },
        "evidence_urls": list(visited_pages.keys()),
        "citations": citations,
        "pages_visited": len(visited_pages),
        "tool_calls": tool_calls_executed,
        "elasticsearch": elasticsearch_result,
        "agents_sdk_tracing": {
            "enabled": input.enable_tracing,
            "workflow_name": agents_trace_workflow_name,
            "trace_id": agents_trace_id,
            "group_id": agents_trace_group_id,
            "metadata": agents_trace_metadata,
        },
        "trace_count": len(traces),
        "traces": traces,
    }


# ---------------------------------------------------------------------------
# Local test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_query = os.getenv("TEST_QUERY", "What is this website about?")
    test_website = os.getenv("TEST_WEBSITE", "https://docs.browserbase.com/introduction")
    test_max_iterations = int(os.getenv("TEST_MAX_ITERATIONS", "6"))
    test_max_pages = int(os.getenv("TEST_MAX_PAGES", "4"))
    test_auto_search_phase = os.getenv("TEST_AUTO_SEARCH_PHASE", "true").strip().lower() == "true"
    test_search_variations = int(os.getenv("TEST_SEARCH_VARIATIONS", "5"))
    test_search_results_per_variation = int(os.getenv("TEST_SEARCH_RESULTS_PER_VARIATION", "8"))
    test_prefetch_from_search_results = int(os.getenv("TEST_PREFETCH_FROM_SEARCH_RESULTS", "4"))
    test_enable_elasticsearch = (
        os.getenv("TEST_ENABLE_ELASTICSEARCH", "false").strip().lower() == "true"
    )
    test_enable_tracing = (
        os.getenv("TEST_ENABLE_TRACING", "true").strip().lower() == "true"
    )
    test_max_trace_events = int(os.getenv("TEST_MAX_TRACE_EVENTS", "300"))

    test_input = AgenticQueryInput(
        query=test_query,
        website=test_website,
        max_iterations=test_max_iterations,
        max_pages=test_max_pages,
        auto_search_phase=test_auto_search_phase,
        search_variations=test_search_variations,
        search_results_per_variation=test_search_results_per_variation,
        prefetch_from_search_results=test_prefetch_from_search_results,
        enable_elasticsearch=test_enable_elasticsearch,
        enable_tracing=test_enable_tracing,
        max_trace_events=test_max_trace_events,
    )

    print("Running browserbase_agentic_query with Tensorlake local runner...")
    request = run_local_application(browserbase_agentic_query, test_input)
    result = request.output()

    print("\nRun complete")
    print(json.dumps(result, indent=2))
