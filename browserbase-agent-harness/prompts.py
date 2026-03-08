"""Prompt templates for the Browserbase agent harness."""

SYSTEM_INSTRUCTIONS = (
    "You are an agentic web research assistant running on Tensorlake. "
    "You can search a site, fetch pages, download files, unzip archives, and convert "
    "documents to markdown. Always gather concrete evidence before concluding. "
    "Use multiple query phrasings, inspect files when relevant, and return citations. "
    "In your final answer include a short 'Search Evidence' section with concrete URLs and extracted facts."
)

PROCESS_GUIDANCE = (
    "Process guidance:\n"
    "1) Start with site search using multiple variation queries.\n"
    "2) Fetch/snippet the best pages.\n"
    "3) If you see document/archive links (pdf/docx/zip/tar/csv/json), download and inspect them.\n"
    "4) For archives, unzip then inspect relevant files.\n"
    "5) Provide a concise final answer with citations (URLs and filenames).\n"
    "6) Include 3-6 concrete evidence bullets from search/fetch output."
)

AGENT_PROMPT_TEMPLATE = (
    "Run ID: {run_id}\n"
    "User question: {query}\n"
    "Website: {website}\n"
    "Allowed domain: {allowed_domain}\n"
    "Max page budget: {max_pages}\n"
    "Suggested query variations: {query_variations_json}\n"
    "Auto-search phase findings: {presearch_summary_json}\n\n"
    "{process_guidance}"
)


def build_agent_prompt(
    *,
    run_id: str,
    query: str,
    website: str,
    allowed_domain: str,
    max_pages: int,
    query_variations_json: str,
    presearch_summary_json: str,
) -> str:
    """Build the agent input prompt from shared template strings."""
    return AGENT_PROMPT_TEMPLATE.format(
        run_id=run_id,
        query=query,
        website=website,
        allowed_domain=allowed_domain,
        max_pages=max_pages,
        query_variations_json=query_variations_json,
        presearch_summary_json=presearch_summary_json,
        process_guidance=PROCESS_GUIDANCE,
    )
