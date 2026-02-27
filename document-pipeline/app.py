"""Fund Analysis — Tensorlake Application

Replicates the LlamaExtract "Fidelity Multi-Fund Annual Report" example
using Tensorlake for orchestration and Claude for intelligent extraction.

Architecture
────────────
fund_analysis  (Tensorlake @application — orchestrator)
├── parse_document       (@function — Tensorlake DocumentAI)
├── find_fund_splits     (@function — Claude structured output)
├── extract_fund_data    (@function — Claude Agent SDK, ×N in parallel)
└── analyze_funds        (@function — Claude Agent SDK, optional)

The original notebook uses LlamaParse + OpenAI + LlamaExtract + LlamaIndex.
This version uses Tensorlake DocumentAI + Claude + Claude Agent SDK + Tensorlake.

All agent functions are async — Tensorlake natively supports async, so no
asyncio.run() wrappers are needed.

Usage:
    # Local testing
    python app.py data/fidelity_fund.pdf

    # With analysis query
    ANALYSIS_QUERY="Which funds have the highest allocation drift?" \\
        python app.py data/fidelity_fund.pdf

    # Deploy
    tensorlake deploy app.py
"""

import json
import os
import tempfile
from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from tensorlake.applications import (
    File,
    Future,
    Image,
    RETURN_WHEN,
    RequestContext,
    application,
    function,
    run_local_application,
)

from models import (
    FundAnalysisReport,
    FundAnalysisRequest,
    FundComparisonData,
    FundData,
    PageSplit,
    PageSplits,
    SplitCategories,
)
from prompts import (
    ANALYSIS_AGENT_PROMPT,
    EXTRACTION_AGENT_PROMPT,
    SPLIT_CATEGORY_PROMPT,
    SPLIT_TAGGING_PROMPT,
)


# ---------------------------------------------------------------------------
# Sandbox flag
# ---------------------------------------------------------------------------

os.environ.setdefault("IS_SANDBOX", "1")


# ---------------------------------------------------------------------------
# Container images
# ---------------------------------------------------------------------------

parser_image = (
    Image(name="fund-parser")
    .run("pip install tensorlake[documentai]")
)

agent_image = (
    Image(name="fund-agent")
    .run("pip install claude-agent-sdk anthropic pydantic")
)


# ---------------------------------------------------------------------------
# Helper: Claude structured output via tool_use
# ---------------------------------------------------------------------------

def _claude_structured_output(
    prompt: str,
    output_model: type[BaseModel],
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> dict:
    """Call Claude with a tool matching the Pydantic model to get structured output.

    Uses Anthropic's tool_use as structured output — Claude is forced to call
    a tool whose input_schema matches the Pydantic model.
    """
    import anthropic

    client = anthropic.Anthropic()

    tool_name = output_model.__name__
    tool_schema = output_model.model_json_schema()
    # Remove unsupported keys from the schema
    tool_schema.pop("title", None)
    tool_schema.pop("$defs", None)

    messages = [{"role": "user", "content": prompt}]

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system if system else "You are a helpful document analysis assistant.",
        messages=messages,
        tools=[{
            "name": tool_name,
            "description": f"Output structured {tool_name} data.",
            "input_schema": tool_schema,
        }],
        tool_choice={"type": "tool", "name": tool_name},
    )

    for block in response.content:
        if block.type == "tool_use":
            return block.input

    raise RuntimeError("Claude did not return structured output")


# ---------------------------------------------------------------------------
# Step 1: Parse document with DocumentAI
# ---------------------------------------------------------------------------

@function(image=parser_image, secrets=["TENSORLAKE_API_KEY"], timeout=600)
async def parse_document(file: File) -> list[dict]:
    """Parse a PDF into page-level markdown nodes using Tensorlake DocumentAI.

    Returns a list of dicts: [{"page_number": int, "content": str}, ...]
    """
    import hashlib
    from tensorlake.documentai import (
        DocumentAI,
        ParsingOptions,
        ChunkingStrategy,
        TableOutputMode,
        ParseStatus,
    )

    ctx = RequestContext.get()
    ctx.progress.update(0, 100, "Preparing document for parsing...")

    api_key = os.getenv("TENSORLAKE_API_KEY")
    file_content = bytes(file.content)

    print(f"Content length: {len(file_content)} bytes")
    print(f"MD5 hash: {hashlib.md5(file_content).hexdigest()}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_content)
        tmp.flush()
        tmp_path = tmp.name

    try:
        ctx.progress.update(10, 100, "Uploading to DocumentAI...")
        doc_ai = DocumentAI(api_key=api_key)
        file_id = doc_ai.upload(tmp_path)
        ctx.progress.update(30, 100, f"Uploaded ({file_id[:8]}...)")

        parsing_options = ParsingOptions(
            chunking_strategy=ChunkingStrategy.PAGE,
            table_output_mode=TableOutputMode.MARKDOWN,
        )

        ctx.progress.update(40, 100, "Parsing document...")
        parse_id = doc_ai.read(file_id=file_id, parsing_options=parsing_options)

        ctx.progress.update(50, 100, "Waiting for parse completion...")
        result = doc_ai.wait_for_completion(parse_id)

        if result.status != ParseStatus.SUCCESSFUL:
            raise RuntimeError(f"Document parsing failed: {result.status}")

        pages = []
        for chunk in result.chunks:
            pages.append({
                "page_number": chunk.page_number,
                "content": chunk.content,
            })

        ctx.progress.update(100, 100, f"Parsed {len(pages)} pages")
        return pages

    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Step 2: Find fund splits (which pages belong to which fund)
# ---------------------------------------------------------------------------

@function(
    image=agent_image,
    secrets=["ANTHROPIC_API_KEY"],
    timeout=600,
)
async def find_fund_splits(
    pages_json: str,
    split_description: str,
    split_rules: str,
    split_key: str,
) -> str:
    """Identify fund sections in the document and map pages to funds.

    Two-phase approach (same as the original notebook):
    1. Find split categories from the first few pages (table of contents)
    2. Tag each page with its fund section

    Returns JSON string of {split_name: [page_numbers]}.
    """
    ctx = RequestContext.get()
    pages = json.loads(pages_json)

    # ── Phase 1: Find fund categories from first pages ────────────────
    ctx.progress.update(1, 4, "Finding fund categories...")

    head_pages = pages[:5]
    head_text = "\n-----\n".join(
        f"[Page {p['page_number']}]\n{p['content']}" for p in head_pages
    )

    category_prompt = SPLIT_CATEGORY_PROMPT.format(
        split_description=split_description,
        document_text=head_text,
    )

    categories_result = _claude_structured_output(
        category_prompt, SplitCategories
    )
    categories = categories_result.get("split_categories", [])
    print(f"Found categories: {categories}")

    ctx.progress.update(2, 4, f"Found {len(categories)} funds")

    # ── Phase 2: Tag each page with its fund ──────────────────────────
    ctx.progress.update(3, 4, "Tagging pages to funds...")

    full_split_rules = (
        f"Please split by these categories: {categories}\n\n{split_rules}"
    )

    all_splits: list[PageSplit] = []

    for page in pages:
        tag_prompt = SPLIT_TAGGING_PROMPT.format(
            split_key=split_key,
            split_rules=full_split_rules,
            chunk_text=f"[Page {page['page_number']}]\n{page['content']}",
        )

        try:
            result = _claude_structured_output(
                tag_prompt, PageSplits, model="claude-haiku-4-5-20251001"
            )
            for s in result.get("splits", []):
                all_splits.append(PageSplit(
                    split_name=s["split_name"],
                    split_description=s.get("split_description", ""),
                    page_number=s.get("page_number", page["page_number"]),
                ))
        except Exception as exc:
            print(f"[split_error] Page {page['page_number']}: {exc}")

    # ── Build page→fund mapping ───────────────────────────────────────
    split_name_to_pages: dict[str, list[int]] = defaultdict(list)

    split_idx = 0
    for page in pages:
        cur_page = page["page_number"]

        # Advance to the latest split that starts at or before this page
        while (
            split_idx + 1 < len(all_splits)
            and all_splits[split_idx + 1].page_number <= cur_page
        ):
            split_idx += 1

        if all_splits and all_splits[split_idx].page_number <= cur_page:
            name = all_splits[split_idx].split_name
            split_name_to_pages[name].append(cur_page)

    ctx.progress.update(4, 4, f"Mapped {len(split_name_to_pages)} fund sections")
    print(f"Split map: { {k: len(v) for k, v in split_name_to_pages.items()} }")

    return json.dumps(dict(split_name_to_pages))


# ---------------------------------------------------------------------------
# Step 3: Extract structured fund data per section (Claude Agent SDK)
# ---------------------------------------------------------------------------

@function(
    image=agent_image,
    secrets=["ANTHROPIC_API_KEY"],
    timeout=600,
    max_containers=8,
)
async def extract_fund_data(
    split_name: str,
    section_text: str,
) -> str:
    """Extract FundData from a single fund section using Claude Agent SDK.

    Returns JSON string of the extracted FundData.
    """
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        tool,
        create_sdk_mcp_server,
        AssistantMessage,
        TextBlock,
    )

    ctx = RequestContext.get()
    ctx.progress.update(0, 100, f"Extracting {split_name}...")

    os.environ["IS_SANDBOX"] = "1"

    extracted: dict = {}

    # ── Agent tools ───────────────────────────────────────────────────

    @tool("read_section", "Read the fund section content.", {})
    async def read_section_tool(args: dict[str, Any]) -> dict[str, Any]:
        ctx.progress.update(20, 100, "Reading section content...")
        return {
            "content": [{
                "type": "text",
                "text": f"## Fund Section: {split_name}\n\n{section_text}",
            }]
        }

    # Build the save_extraction tool schema from FundData
    fund_schema = FundData.model_json_schema()
    save_properties = {}
    for field_name, field_info in fund_schema.get("properties", {}).items():
        prop: dict[str, Any] = {"description": field_info.get("description", "")}
        # Map JSON schema types
        json_type = field_info.get("type")
        any_of = field_info.get("anyOf", [])
        if json_type:
            prop["type"] = json_type
        elif any_of:
            # Optional fields have anyOf with type + null
            for variant in any_of:
                if variant.get("type") != "null":
                    prop["type"] = variant.get("type", "string")
                    break
        else:
            prop["type"] = "string"
        save_properties[field_name] = prop

    @tool(
        "save_extraction",
        "Save the extracted fund data. All financial fields are required if present in the document.",
        save_properties,
    )
    async def save_extraction_tool(args: dict[str, Any]) -> dict[str, Any]:
        ctx.progress.update(80, 100, f"Saving {args.get('fund_name', split_name)}...")
        extracted.update(args)
        return {
            "content": [{
                "type": "text",
                "text": f"Saved extraction for {args.get('fund_name', split_name)}.",
            }]
        }

    # ── Run extraction agent ──────────────────────────────────────────

    server = create_sdk_mcp_server(
        name="extraction",
        version="1.0.0",
        tools=[read_section_tool, save_extraction_tool],
    )

    options = ClaudeAgentOptions(
        system_prompt=EXTRACTION_AGENT_PROMPT,
        mcp_servers={"extraction": server},
        allowed_tools=[
            "mcp__extraction__read_section",
            "mcp__extraction__save_extraction",
        ],
        permission_mode="bypassPermissions",
    )

    ctx.progress.update(10, 100, f"Connecting agent for {split_name}...")
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Extract structured fund data from the section: {split_name}. "
            "First read the section content, then save the extraction."
        )

        progress_step = 30
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        preview = block.text[:150]
                        ctx.progress.update(progress_step, 100, preview)
                        print(f"[Extract:{split_name}] {preview}", flush=True)
                progress_step = min(progress_step + 15, 90)

    ctx.progress.update(100, 100, f"Done: {extracted.get('fund_name', split_name)}")

    # Validate and return
    fund = FundData(**extracted)
    return fund.model_dump_json()


# ---------------------------------------------------------------------------
# Step 4 (optional): Analyze extracted fund data
# ---------------------------------------------------------------------------

@function(
    image=agent_image,
    secrets=["ANTHROPIC_API_KEY"],
    timeout=600,
)
async def analyze_funds(funds_json: str, query: str) -> str:
    """Run a natural language analysis query over extracted fund data.

    Uses Claude Agent SDK with a code execution tool for pandas analysis.
    """
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        tool,
        create_sdk_mcp_server,
        AssistantMessage,
        TextBlock,
    )

    ctx = RequestContext.get()
    os.environ["IS_SANDBOX"] = "1"

    funds = json.loads(funds_json)
    analysis_result = ""

    @tool("get_fund_data", "Get all extracted fund data as JSON.", {})
    async def get_fund_data_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(funds, indent=2),
            }]
        }

    @tool(
        "run_python",
        "Execute Python code for analysis. pandas and json are available. "
        "Print results to stdout.",
        {
            "code": {"type": "string", "description": "Python code to execute"},
        },
    )
    async def run_python_tool(args: dict[str, Any]) -> dict[str, Any]:
        import subprocess
        import tempfile as tf

        code = (
            "import pandas as pd\nimport json\n"
            f"fund_data = json.loads('''{json.dumps(funds)}''')\n"
            "df = pd.DataFrame(fund_data)\n"
            f"{args['code']}"
        )

        with tf.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            try:
                result = subprocess.run(
                    ["python3", f.name],
                    capture_output=True, text=True, timeout=30,
                )
                output = result.stdout
                if result.stderr:
                    output += f"\nSTDERR: {result.stderr}"
                return {"content": [{"type": "text", "text": output}]}
            finally:
                os.unlink(f.name)

    server = create_sdk_mcp_server(
        name="analysis",
        version="1.0.0",
        tools=[get_fund_data_tool, run_python_tool],
    )

    options = ClaudeAgentOptions(
        system_prompt=ANALYSIS_AGENT_PROMPT,
        mcp_servers={"analysis": server},
        allowed_tools=[
            "mcp__analysis__get_fund_data",
            "mcp__analysis__run_python",
        ],
        permission_mode="bypassPermissions",
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(query)

        response_parts: list[str] = []
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_parts.append(block.text)
                        print(f"[Analysis] {block.text[:200]}", flush=True)

        analysis_result = "\n".join(response_parts)

    return analysis_result


# ---------------------------------------------------------------------------
# Tensorlake application: fund_analysis
# ---------------------------------------------------------------------------

@application(
    tags={
        "pattern": "split-extract-analyze",
        "domain": "finance",
        "inspired_by": "llamaextract-fidelity-fund-analysis",
    },
)
@function(image=agent_image, secrets=["ANTHROPIC_API_KEY", "TENSORLAKE_API_KEY"], timeout=3600)
async def fund_analysis(file: File) -> FundAnalysisReport:
    """Analyze a multi-fund financial report.

    Pipeline:
    1. Parse PDF → page-level markdown (Tensorlake DocumentAI)
    2. Split document by fund sections (Claude)
    3. Extract structured FundData per fund (Claude Agent SDK, parallel)
    4. Optionally analyze results (Claude Agent SDK)
    """
    ctx = RequestContext.get()

    # Read config from request context.
    # ctx.state.get() raises on missing keys, so wrap each in try/except.
    defaults = FundAnalysisRequest()

    def _state_get(key: str, default: str) -> str:
        try:
            return ctx.state.get(key)
        except Exception:
            return default

    request = FundAnalysisRequest(
        split_description=_state_get("split_description", defaults.split_description),
        split_rules=_state_get("split_rules", defaults.split_rules),
        split_key=_state_get("split_key", defaults.split_key),
        analysis_query=_state_get("analysis_query", defaults.analysis_query),
    )

    # ── Step 1: Parse document ────────────────────────────────────────
    ctx.progress.update(1, 5, "Parsing document...")
    pages = await parse_document(file)
    total_pages = len(pages)
    print(f"Parsed {total_pages} pages")

    # ── Step 2: Find fund splits ──────────────────────────────────────
    ctx.progress.update(2, 5, "Finding fund sections...")
    split_map_json = await find_fund_splits(
        json.dumps(pages),
        request.split_description,
        request.split_rules,
        request.split_key,
    )
    split_map: dict[str, list[int]] = json.loads(split_map_json)
    print(f"Found {len(split_map)} fund sections")

    if not split_map:
        return FundAnalysisReport(
            fund_data=FundComparisonData(funds=[]),
            total_pages=total_pages,
            summary="No fund sections found in the document.",
        )

    # ── Step 3: Extract fund data in parallel ─────────────────────────
    ctx.progress.update(3, 5, f"Extracting data from {len(split_map)} funds...")

    # Build text for each fund section by combining its pages
    pages_by_number = {p["page_number"]: p["content"] for p in pages}

    extract_futures: dict[str, Future] = {}
    for split_name, page_numbers in split_map.items():
        section_text = "\n\n-------\n\n".join(
            f"[Page {pn}]\n{pages_by_number[pn]}"
            for pn in page_numbers
            if pn in pages_by_number
        )
        future = extract_fund_data.future(split_name, section_text)
        extract_futures[split_name] = future

    Future.wait(extract_futures.values(), return_when=RETURN_WHEN.ALL_COMPLETED)

    funds: list[FundData] = []
    for split_name, fut in extract_futures.items():
        try:
            fund_json = fut.result()
            fund = FundData.model_validate_json(fund_json)
            funds.append(fund)
            print(f"Extracted: {fund.fund_name}")
        except Exception as exc:
            print(f"[extract_error] {split_name}: {exc}")

    fund_data = FundComparisonData(funds=funds)
    ctx.progress.update(4, 5, f"Extracted {len(funds)} funds")

    # ── Step 4 (optional): Analyze results ────────────────────────────
    analysis_result = ""
    if request.analysis_query and funds:
        ctx.progress.update(4, 5, "Running analysis...")
        csv_rows = fund_data.to_csv_rows()
        analysis_result = await analyze_funds(
            json.dumps(csv_rows), request.analysis_query
        )

    # ── Build summary ─────────────────────────────────────────────────
    summary_parts = [
        f"Analyzed {total_pages}-page document, found {len(funds)} funds.",
    ]
    for fund in funds:
        parts = [fund.fund_name]
        if fund.one_year_return is not None:
            parts.append(f"return={fund.one_year_return}%")
        if fund.equity_pct is not None:
            parts.append(f"equity={fund.equity_pct}%")
        if fund.expense_ratio is not None:
            parts.append(f"expense={fund.expense_ratio}%")
        summary_parts.append(f"  - {', '.join(parts)}")

    ctx.progress.update(5, 5, "Analysis complete")

    return FundAnalysisReport(
        fund_data=fund_data,
        split_map=split_map,
        total_pages=total_pages,
        analysis_result=analysis_result,
        summary="\n".join(summary_parts),
    )


# ---------------------------------------------------------------------------
# Local runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python app.py <report.pdf>")
        print("  Set ANALYSIS_QUERY env var for natural language analysis.")
        print("  Set SPLIT_DESCRIPTION to customize fund detection.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    analysis_query = os.getenv("ANALYSIS_QUERY", "")

    class MockFile:
        def __init__(self, path: str):
            with open(path, "rb") as f:
                self.content = f.read()
            self.name = os.path.basename(path)
            self.content_type = "application/pdf"

    mock_file = MockFile(pdf_path)

    print(f"Processing {pdf_path}...")
    state = {}
    if analysis_query:
        state["analysis_query"] = analysis_query
        print(f"Analysis query: {analysis_query}")
    if os.getenv("SPLIT_DESCRIPTION"):
        state["split_description"] = os.getenv("SPLIT_DESCRIPTION")
    if os.getenv("SPLIT_KEY"):
        state["split_key"] = os.getenv("SPLIT_KEY")

    request = run_local_application(
        fund_analysis, mock_file,
        state=state if state else None,
    )
    report: FundAnalysisReport = request.output()

    print(f"\n{'='*60}")
    print("FUND ANALYSIS REPORT")
    print(f"{'='*60}")
    print(report.summary)

    if report.fund_data.funds:
        print(f"\n{'─'*60}")
        print("EXTRACTED FUND DATA")
        print(f"{'─'*60}")

        for fund in report.fund_data.funds:
            print(f"\n  {fund.fund_name}")
            if fund.target_equity_pct is not None:
                print(f"    Target equity:    {fund.target_equity_pct}%")
            if fund.equity_pct is not None:
                print(f"    Actual equity:    {fund.equity_pct}%")
                if fund.target_equity_pct is not None:
                    drift = fund.equity_pct - fund.target_equity_pct
                    print(f"    Allocation drift: {drift:+.1f}%")
            if fund.one_year_return is not None:
                print(f"    1-year return:    {fund.one_year_return}%")
            if fund.expense_ratio is not None:
                print(f"    Expense ratio:    {fund.expense_ratio}%")
            if fund.nav is not None:
                print(f"    NAV:              ${fund.nav:.2f}")
            if fund.net_assets_usd is not None:
                print(f"    Net assets:       ${fund.net_assets_usd:,.0f}")

    if report.analysis_result:
        print(f"\n{'─'*60}")
        print("ANALYSIS")
        print(f"{'─'*60}")
        print(report.analysis_result)
