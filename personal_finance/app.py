"""
Personal Finance Tensorlake Application

A Tensorlake application that uses Claude agents to:
1. Analyze bank and credit card statements (finance_analyzer)
2. Query transaction data using natural language (finance_query)

The application parses PDF statements, extracts transactions,
categorizes them, and stores them in PostgreSQL for analysis.

Usage:
    # Analyze a statement
    curl https://api.tensorlake.ai/applications/finance_analyzer \
      -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
      -F "file=@statement.pdf"

    # Query transactions
    curl https://api.tensorlake.ai/applications/finance_query \
      -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
      --json '{"question": "How much did I spend on groceries?"}'
"""

import os
import json
from typing import Any

from tensorlake.applications import application, function, File, RequestContext

from config import agent_image, parser_image, code_exec_image
from models import (
    FinanceResponse,
    QueryResponse,
    DocumentContentInput,
    DocumentContentResult,
    SaveTransactionsInput,
    SaveTransactionsResult,
    ExtractionSchemaResult,
    SQLQueryInput,
    SQLQueryResult,
    SchemaResult,
    CodeExecutionInput,
    CodeExecutionResult,
)
from prompts import (
    get_extraction_schema_text,
    get_query_schema_text,
    get_finance_agent_prompt,
    get_query_agent_prompt,
)
from utils import parse_date


# =============================================================================
# Document Parsing Functions
# =============================================================================

@function(cpu=1, memory=2, image=parser_image, secrets=["TENSORLAKE_API_KEY"])
def upload_and_parse_document(file: File) -> dict:
    """
    Upload a file to Tensorlake and parse it using DocumentAI.

    Returns:
        dict with 'file_id' and 'content' (parsed markdown)
    """
    import tempfile
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

    # Debug logging
    print(f"Content length: {len(file_content)} bytes")
    print(f"MD5 hash: {hashlib.md5(file_content).hexdigest()}")

    is_valid_pdf = file_content.startswith(b'%PDF')
    if not is_valid_pdf:
        print("WARNING: Content does not start with PDF magic bytes!")

    # Write to temp file for upload
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
        tmp_file.write(file_content)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        ctx.progress.update(10, 100, "Uploading document to Tensorlake...")
        doc_ai = DocumentAI(api_key=api_key)
        file_id = doc_ai.upload(tmp_path)
        print(f"Uploaded file_id: {file_id}")
        ctx.progress.update(30, 100, f"Document uploaded (file_id: {file_id[:8]}...)")

        parsing_options = ParsingOptions(
            chunking_strategy=ChunkingStrategy.PAGE,
            table_output_mode=TableOutputMode.MARKDOWN,
        )

        ctx.progress.update(40, 100, "Starting document parsing...")
        parse_id = doc_ai.read(file_id=file_id, parsing_options=parsing_options)

        ctx.progress.update(50, 100, "Waiting for parsing to complete...")
        result = doc_ai.wait_for_completion(parse_id)
        ctx.progress.update(80, 100, f"Parsing complete: {result.status}")

        if result.status != ParseStatus.SUCCESSFUL:
            raise RuntimeError(f"Document parsing failed: {result.status}")

        # Combine pages into markdown
        content_parts = []
        for chunk in result.chunks:
            content_parts.append(f"## Page {chunk.page_number}\n\n{chunk.content}")

        parsed_content = "\n\n".join(content_parts)
        ctx.progress.update(100, 100, f"Document parsed: {len(parsed_content)} chars")

        return {"file_id": file_id, "content": parsed_content}

    finally:
        os.unlink(tmp_path)


# =============================================================================
# Finance Agent Functions
# =============================================================================

@function(cpu=1, memory=1)
def get_extraction_schema() -> ExtractionSchemaResult:
    """Get the database schema for transaction extraction."""
    return ExtractionSchemaResult(schema_text=get_extraction_schema_text())


@function(cpu=1, memory=1)
def get_document_content(doc_input: DocumentContentInput) -> DocumentContentResult:
    """Return the parsed document content."""
    return DocumentContentResult(file_id=doc_input.file_id, content=doc_input.content)


@function(cpu=1, memory=2, image=agent_image, secrets=["DATABASE_URL"])
def save_transactions_to_db(save_input: SaveTransactionsInput) -> SaveTransactionsResult:
    """Save categorized transactions to PostgreSQL with duplicate detection."""
    import asyncio
    import asyncpg

    print(f"[FUNC] Saving transactions for {save_input.account_name}", flush=True)

    async def save_async():
        conn = await asyncpg.connect(save_input.database_url)
        try:
            # Create table if not exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    transaction_date DATE NOT NULL,
                    statement_date DATE,
                    description TEXT NOT NULL,
                    amount DECIMAL(12, 2) NOT NULL,
                    category VARCHAR(100),
                    subcategory VARCHAR(100),
                    account_type VARCHAR(50) NOT NULL,
                    account_name VARCHAR(100),
                    account_holder VARCHAR(200),
                    merchant VARCHAR(200),
                    is_debit BOOLEAN NOT NULL DEFAULT TRUE,
                    raw_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_transactions_statement_date
                    ON transactions(statement_date);
            """)

            stmt_date = parse_date(save_input.statement_date)
            saved_count = 0
            skipped_duplicates = 0
            total_spending = 0.0
            total_income = 0.0
            categories = {}

            transactions = save_input.transactions
            if isinstance(transactions, str):
                transactions = json.loads(transactions)

            for txn in transactions:
                if isinstance(txn, str):
                    txn = json.loads(txn)

                txn_date = parse_date(txn["transaction_date"])

                # Check for duplicate
                existing = await conn.fetchval("""
                    SELECT id FROM transactions
                    WHERE transaction_date = $1 AND description = $2
                    AND amount = $3 AND account_name = $4 LIMIT 1
                """, txn_date, txn["description"], txn["amount"], save_input.account_name)

                if existing:
                    skipped_duplicates += 1
                    continue

                await conn.execute("""
                    INSERT INTO transactions (
                        transaction_date, statement_date, description, amount,
                        category, subcategory, account_type, account_name,
                        account_holder, merchant, is_debit, raw_text
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    txn_date, stmt_date, txn["description"], txn["amount"],
                    txn.get("category"), txn.get("subcategory"), save_input.account_type,
                    save_input.account_name, save_input.account_holder,
                    txn.get("merchant"), txn.get("is_debit", True), json.dumps(txn)
                )
                saved_count += 1

                cat = txn.get("category", "other")
                if txn.get("is_debit", True):
                    total_spending += txn["amount"]
                    categories[cat] = categories.get(cat, 0) + txn["amount"]
                else:
                    total_income += txn["amount"]

            return SaveTransactionsResult(
                success=True,
                saved_count=saved_count,
                skipped_duplicates=skipped_duplicates,
                total_spending=total_spending,
                total_income=total_income,
                categories=categories,
            )
        finally:
            await conn.close()

    try:
        return asyncio.run(save_async())
    except Exception as e:
        return SaveTransactionsResult(success=False, error=str(e))


@function(cpu=2, memory=4, image=agent_image, secrets=["ANTHROPIC_API_KEY", "DATABASE_URL"])
def run_finance_agent(parsed_document: dict) -> FinanceResponse:
    """Run the Claude agent to analyze a statement and save transactions."""
    import asyncio
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        tool,
        create_sdk_mcp_server,
        AssistantMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        ThinkingBlock,
        ResultMessage,
    )

    ctx = RequestContext.get()
    ctx.progress.update(0, 100, "Starting finance agent...")

    os.environ["IS_SANDBOX"] = "1"

    file_id = parsed_document["file_id"]
    document_content = parsed_document["content"]
    database_url = os.getenv("DATABASE_URL")

    # State to track results
    result_state = {
        "account_type": None,
        "account_name": None,
        "account_holder": None,
        "statement_date": None,
        "transactions_saved": 0,
        "total_spending": 0.0,
        "total_income": 0.0,
        "categories": {},
    }

    # Define agent tools
    @tool("read_document", "Read the parsed statement document content.", {})
    async def read_document_tool(args: dict[str, Any]) -> dict[str, Any]:
        ctx.progress.update(15, 100, "Reading document content...")
        doc_result = get_document_content(
            DocumentContentInput(file_id=file_id, content=document_content)
        )
        return {
            "content": [{
                "type": "text",
                "text": f"## Statement Document\n\n{doc_result.content}",
            }]
        }

    @tool(
        "save_transactions",
        "Save categorized transactions to the database.",
        {
            "statement_date": {"type": "string", "description": "Statement date (YYYY-MM-DD)"},
            "account_type": {"type": "string", "description": "bank_account or credit_card"},
            "account_name": {"type": "string", "description": "Account product name"},
            "account_holder": {"type": "string", "description": "Name on the statement"},
            "transactions": {
                "type": "array",
                "description": "List of transactions",
                "items": {
                    "type": "object",
                    "properties": {
                        "transaction_date": {"type": "string"},
                        "description": {"type": "string"},
                        "amount": {"type": "number"},
                        "category": {"type": "string"},
                        "subcategory": {"type": "string"},
                        "merchant": {"type": "string"},
                        "is_debit": {"type": "boolean"},
                    },
                    "required": ["transaction_date", "description", "amount", "category", "is_debit"],
                },
            },
        },
    )
    async def save_transactions_tool(args: dict[str, Any]) -> dict[str, Any]:
        ctx.progress.update(50, 100, "Saving transactions...")
        try:
            transactions = args["transactions"]
            if isinstance(transactions, str):
                transactions = json.loads(transactions)

            result_state["statement_date"] = args["statement_date"]
            result_state["account_type"] = args["account_type"]
            result_state["account_name"] = args["account_name"]
            result_state["account_holder"] = args.get("account_holder")

            save_result = save_transactions_to_db(SaveTransactionsInput(
                statement_date=args["statement_date"],
                account_type=args["account_type"],
                account_name=args["account_name"],
                account_holder=args.get("account_holder"),
                transactions=transactions,
                database_url=database_url,
            ))

            if not save_result.success:
                return {"content": [{"type": "text", "text": f"Error: {save_result.error}"}], "is_error": True}

            result_state["transactions_saved"] = save_result.saved_count
            result_state["total_spending"] = save_result.total_spending
            result_state["total_income"] = save_result.total_income
            result_state["categories"] = save_result.categories

            ctx.progress.update(90, 100, f"Saved {save_result.saved_count} transactions")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Saved {save_result.saved_count} transactions ({save_result.skipped_duplicates} duplicates skipped)",
                }]
            }
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}

    @tool("get_database_schema", "Get the required schema for transaction extraction.", {})
    async def get_schema_tool(args: dict[str, Any]) -> dict[str, Any]:
        ctx.progress.update(8, 100, "Getting database schema...")
        schema_result = get_extraction_schema()
        return {"content": [{"type": "text", "text": schema_result.schema_text}]}

    async def run_agent():
        finance_server = create_sdk_mcp_server(
            name="finance",
            version="1.0.0",
            tools=[get_schema_tool, read_document_tool, save_transactions_tool],
        )

        options = ClaudeAgentOptions(
            system_prompt=get_finance_agent_prompt(file_id),
            mcp_servers={"finance": finance_server},
            allowed_tools=[
                "mcp__finance__get_database_schema",
                "mcp__finance__read_document",
                "mcp__finance__save_transactions",
            ],
            permission_mode="bypassPermissions",
        )

        ctx.progress.update(10, 100, "Connecting to Claude agent...")
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Analyze the statement, extract transactions, and save to database.")

            progress_step = 20
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            # Send model thinking as progress update
                            thinking_preview = block.thinking[:300] + "..." if len(block.thinking) > 300 else block.thinking
                            ctx.progress.update(progress_step, 100, f"Thinking: {thinking_preview}")
                            print(f"[Agent Thinking] {thinking_preview}", flush=True)
                        elif isinstance(block, TextBlock):
                            # Send text response as progress update
                            text_preview = block.text[:200] + "..." if len(block.text) > 200 else block.text
                            ctx.progress.update(progress_step, 100, f"Agent: {text_preview}")
                            print(f"[Agent] {text_preview}", flush=True)
                        elif isinstance(block, ToolUseBlock):
                            # Send tool invocation as progress update
                            tool_input_preview = json.dumps(block.input)[:100] if block.input else ""
                            ctx.progress.update(progress_step, 100, f"Calling tool: {block.name} - {tool_input_preview}")
                            print(f"[Agent] Calling: {block.name} with {tool_input_preview}", flush=True)
                        elif isinstance(block, ToolResultBlock):
                            # Send tool result as progress update
                            result_preview = str(block.content)[:150] if block.content else "completed"
                            status = "error" if block.is_error else "success"
                            ctx.progress.update(progress_step, 100, f"Tool result ({status}): {result_preview}")
                            print(f"[Agent] Tool result ({status}): {result_preview}", flush=True)
                    progress_step = min(progress_step + 10, 90)

    asyncio.run(run_agent())
    ctx.progress.update(100, 100, f"Completed: {result_state['transactions_saved']} transactions")

    return FinanceResponse(
        success=result_state["transactions_saved"] > 0,
        message=f"Processed {result_state['transactions_saved']} transactions",
        account_type=result_state["account_type"],
        account_name=result_state["account_name"],
        account_holder=result_state["account_holder"],
        statement_date=result_state["statement_date"],
        total_transactions=result_state["transactions_saved"],
        total_spending=result_state["total_spending"],
        total_income=result_state["total_income"],
        categories_summary=result_state["categories"],
    )


# =============================================================================
# Query Agent Functions
# =============================================================================

@function(cpu=1, memory=1, image=agent_image)
def get_database_schema() -> SchemaResult:
    """Get the database schema for querying."""
    return SchemaResult(schema_text=get_query_schema_text())


@function(cpu=1, memory=2, image=agent_image, secrets=["DATABASE_URL"])
def execute_sql_query(query_input: SQLQueryInput) -> SQLQueryResult:
    """Execute a SELECT query against the PostgreSQL database."""
    import asyncio
    import asyncpg
    from decimal import Decimal
    from datetime import date, datetime

    database_url = os.getenv("DATABASE_URL")

    # Safety: only SELECT queries
    if not query_input.sql.strip().upper().startswith("SELECT"):
        return SQLQueryResult(success=False, error="Only SELECT queries are allowed.")

    async def run_query():
        conn = await asyncpg.connect(database_url)
        try:
            rows = await conn.fetch(query_input.sql)
            results = []
            for row in rows:
                row_dict = {}
                for k, v in dict(row).items():
                    if isinstance(v, Decimal):
                        row_dict[k] = float(v)
                    elif isinstance(v, (date, datetime)):
                        row_dict[k] = v.isoformat()
                    else:
                        row_dict[k] = v
                results.append(row_dict)
            return results
        finally:
            await conn.close()

    try:
        results = asyncio.run(run_query())

        # Format output
        if not results:
            formatted = f"Query: {query_input.explanation}\n\nNo results found."
        else:
            formatted = f"Query: {query_input.explanation}\n\nFound {len(results)} rows:\n\n"
            columns = list(results[0].keys())
            formatted += " | ".join(str(c) for c in columns) + "\n"
            formatted += "-" * 50 + "\n"
            for row in results[:50]:
                values = [f"{v:.2f}" if isinstance(v, float) else str(v)[:50] for v in row.values()]
                formatted += " | ".join(values) + "\n"

        return SQLQueryResult(
            success=True, rows=results, row_count=len(results), formatted_output=formatted
        )
    except Exception as e:
        return SQLQueryResult(success=False, error=str(e))


@function(cpu=2, memory=4, image=code_exec_image)
def execute_code(code_input: CodeExecutionInput) -> CodeExecutionResult:
    """Execute Python or Node.js code for visualizations and reports."""
    import subprocess
    import tempfile
    import base64
    import glob as glob_module

    timeout = min(code_input.timeout, 120)

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if code_input.language.lower() == "python":
                code_file = os.path.join(tmpdir, "script.py")
                setup = f"import os\nos.chdir('{tmpdir}')\nimport matplotlib\nmatplotlib.use('Agg')\n"
                with open(code_file, "w") as f:
                    f.write(setup + code_input.code)
                result = subprocess.run(
                    ["python3", code_file], capture_output=True, text=True, timeout=timeout, cwd=tmpdir
                )
            elif code_input.language.lower() in ("nodejs", "javascript", "node", "js"):
                code_file = os.path.join(tmpdir, "script.js")
                setup = f"process.chdir('{tmpdir}');\n"
                with open(code_file, "w") as f:
                    f.write(setup + code_input.code)
                result = subprocess.run(
                    ["node", code_file], capture_output=True, text=True, timeout=timeout, cwd=tmpdir
                )
            else:
                return CodeExecutionResult(success=False, error=f"Unsupported language: {code_input.language}")

            # Collect created files
            files_created = []
            for file_path in glob_module.glob(os.path.join(tmpdir, "*")):
                if os.path.isfile(file_path) and not file_path.endswith(('.py', '.js')):
                    filename = os.path.basename(file_path)
                    with open(file_path, "rb") as f:
                        content = f.read()
                    try:
                        files_created.append({
                            "filename": filename, "content_type": "text", "content": content.decode('utf-8')[:10000]
                        })
                    except UnicodeDecodeError:
                        files_created.append({
                            "filename": filename, "content_type": "binary",
                            "content_base64": base64.b64encode(content).decode('ascii')
                        })

            output = result.stdout + ("\n--- STDERR ---\n" + result.stderr if result.stderr else "")
            return CodeExecutionResult(
                success=result.returncode == 0,
                output=output[:50000],
                error=result.stderr if result.returncode != 0 else None,
                files_created=files_created,
            )
        except subprocess.TimeoutExpired:
            return CodeExecutionResult(success=False, error=f"Timeout after {timeout}s")
        except Exception as e:
            return CodeExecutionResult(success=False, error=str(e))


@function(cpu=2, memory=4, image=agent_image, secrets=["ANTHROPIC_API_KEY"])
def run_query_agent(question: str) -> QueryResponse:
    """Run the Claude agent to answer questions about transaction data."""
    import asyncio
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        tool,
        create_sdk_mcp_server,
        AssistantMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        ThinkingBlock,
        ResultMessage,
    )

    ctx = RequestContext.get()
    ctx.progress.update(0, 100, "Starting query agent...")

    os.environ["IS_SANDBOX"] = "1"
    user_question = str(question)

    result_state = {"answer": "", "sql_query": None, "raw_results": None, "files_created": []}

    @tool(
        "query_database",
        "Execute a SQL query and return results.",
        {
            "sql": {"type": "string", "description": "SELECT query to execute"},
            "explanation": {"type": "string", "description": "What this query does"},
        },
    )
    async def query_database_tool(args: dict[str, Any]) -> dict[str, Any]:
        result_state["sql_query"] = args["sql"]
        query_result = execute_sql_query(SQLQueryInput(sql=args["sql"], explanation=args.get("explanation", "")))
        if not query_result.success:
            return {"content": [{"type": "text", "text": f"Error: {query_result.error}"}], "is_error": True}
        result_state["raw_results"] = query_result.rows
        return {"content": [{"type": "text", "text": query_result.formatted_output}]}

    @tool("get_schema", "Get the database schema.", {})
    async def get_schema_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": get_database_schema().schema_text}]}

    @tool(
        "run_code",
        "Execute Python code for charts and visualizations.",
        {
            "code": {"type": "string", "description": "Python code to execute"},
            "language": {"type": "string", "description": "'python' or 'nodejs'"},
        },
    )
    async def run_code_tool(args: dict[str, Any]) -> dict[str, Any]:
        code_result = execute_code(CodeExecutionInput(
            code=args["code"], language=args.get("language", "python"), timeout=60
        ))
        if not code_result.success:
            return {"content": [{"type": "text", "text": f"Error: {code_result.error}"}], "is_error": True}
        if code_result.files_created:
            result_state["files_created"].extend(code_result.files_created)
        response = code_result.output or "Code executed successfully."
        if code_result.files_created:
            response += f"\n\nFiles created: {[f['filename'] for f in code_result.files_created]}"
        return {"content": [{"type": "text", "text": response}]}

    async def run_agent():
        query_server = create_sdk_mcp_server(
            name="query", version="1.0.0", tools=[query_database_tool, get_schema_tool, run_code_tool]
        )

        options = ClaudeAgentOptions(
            system_prompt=get_query_agent_prompt(),
            mcp_servers={"query": query_server},
            allowed_tools=["mcp__query__query_database", "mcp__query__get_schema", "mcp__query__run_code"],
            permission_mode="bypassPermissions",
        )

        ctx.progress.update(10, 100, "Connecting to Claude agent...")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_question)

            response_text = []
            progress_step = 20
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            # Send model thinking as progress update
                            thinking_preview = block.thinking[:300] + "..." if len(block.thinking) > 300 else block.thinking
                            ctx.progress.update(progress_step, 100, f"Thinking: {thinking_preview}")
                            print(f"[Agent Thinking] {thinking_preview}", flush=True)
                        elif isinstance(block, TextBlock):
                            # Send text response as progress update
                            text_preview = block.text[:200] + "..." if len(block.text) > 200 else block.text
                            ctx.progress.update(progress_step, 100, f"Agent: {text_preview}")
                            print(f"[Agent] {text_preview}", flush=True)
                            response_text.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            # Send tool invocation as progress update
                            tool_input_preview = json.dumps(block.input)[:100] if block.input else ""
                            ctx.progress.update(progress_step, 100, f"Calling tool: {block.name} - {tool_input_preview}")
                            print(f"[Agent] Calling: {block.name} with {tool_input_preview}", flush=True)
                        elif isinstance(block, ToolResultBlock):
                            # Send tool result as progress update
                            result_preview = str(block.content)[:150] if block.content else "completed"
                            status = "error" if block.is_error else "success"
                            ctx.progress.update(progress_step, 100, f"Tool result ({status}): {result_preview}")
                            print(f"[Agent] Tool result ({status}): {result_preview}", flush=True)
                    progress_step = min(progress_step + 10, 90)

            result_state["answer"] = "\n".join(response_text)

    asyncio.run(run_agent())
    ctx.progress.update(100, 100, "Query completed")

    return QueryResponse(
        success=bool(result_state["answer"]),
        question=question,
        answer=result_state["answer"],
        sql_query=result_state["sql_query"],
        raw_results=result_state["raw_results"],
        files_created=result_state["files_created"] or None,
    )


# =============================================================================
# Application Entry Points
# =============================================================================

@application()
@function(cpu=1, memory=1)
def finance_analyzer(file: File) -> FinanceResponse:
    """
    Analyze a bank or credit card statement PDF.

    Parses the document, extracts transactions, categorizes them,
    and saves to PostgreSQL.

    Usage:
        curl https://api.tensorlake.ai/applications/finance_analyzer \
          -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
          -F "file=@statement.pdf"
    """
    try:
        parsed_doc = upload_and_parse_document(file)
        return run_finance_agent(parsed_doc)
    except Exception as e:
        return FinanceResponse(success=False, message=f"Error: {str(e)}")


@application()
@function(cpu=1, memory=1, image=agent_image)
def finance_query(question: str) -> QueryResponse:
    """
    Query transaction data using natural language.

    Uses a Claude agent to understand the question, generate SQL,
    and return human-readable answers with optional visualizations.

    Usage:
        curl https://api.tensorlake.ai/applications/finance_query \
          -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
          --json '{"question": "How much did I spend on groceries?"}'
    """
    try:
        if isinstance(question, dict):
            question = question.get("question", str(question))
        return run_query_agent(question)
    except Exception as e:
        return QueryResponse(success=False, question=str(question), answer=f"Error: {str(e)}")


# =============================================================================
# Local Testing
# =============================================================================

if __name__ == "__main__":
    import sys
    from tensorlake.applications import run_local_application, Request

    if len(sys.argv) < 2:
        print("Usage: python app.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    with open(file_path, "rb") as f:
        file_content = f.read()

    class MockFile:
        def __init__(self, content_bytes: bytes, content_type_str: str = "application/pdf"):
            self.content = content_bytes
            self.content_type = content_type_str

    mock_file = MockFile(file_content)

    print(f"Processing {file_path}...")
    result: Request = run_local_application(finance_analyzer, mock_file)
    output = result.output()

    print(f"\n{'='*50}")
    print("RESULT:")
    print(f"{'='*50}")
    print(f"Success: {output.success}")
    print(f"Message: {output.message}")
    if output.success:
        print(f"Account: {output.account_name} ({output.account_type})")
        print(f"Statement Date: {output.statement_date}")
        print(f"Total Transactions: {output.total_transactions}")
        print(f"Total Spending: ${output.total_spending:.2f}")
        print(f"Total Income: ${output.total_income:.2f}")
        print(f"\nSpending by Category:")
        for cat, amount in sorted(output.categories_summary.items(), key=lambda x: -x[1]):
            print(f"  {cat}: ${amount:.2f}")
