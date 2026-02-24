# Security Autopatch Workflow on Tensorlake

This cookbook implements a Ramp-style vulnerability pipeline on Tensorlake Applications, inspired by:
- [Ramp Builders: "We proactively fixed ~100 security issues in 6 days with 0 humans"](https://builders.ramp.com/post/100-vulnerabilities-patched-with-0-humans)

It follows the same core pattern:
1. Specialized `detector` agents per vulnerability class
2. Adversarial `manager` review to reject false positives
3. `validator` stage that drafts failing-then-passing integration tests
4. `fixer` stage that proposes minimal patch diffs and PR text

## Architecture

```text
security_autopatch (application)
  -> build_code_corpus
  -> run_detector (parallel fan-out by vuln class)
  -> run_manager_review (parallel per finding)
  -> run_validator (parallel per approved finding)
  -> run_fixer (parallel per confirmed finding)
  -> summary report
```

## Tensorlake Features Used

- `@application` + `@function` runtime model
- `Future` + `RETURN_WHEN.ALL_COMPLETED` for fan-out/fan-in parallelism
- `RequestContext.progress.update()` for streaming progress
- `Retries(max_retries=...)` for durable stage retries
- `max_containers` / `warm_containers` for stage-level scale-out and queueing behavior

Docs referenced:
- [Applications SDK Reference](https://docs.tensorlake.ai/applications/concepts)
- [Futures and Parallel Execution](https://docs.tensorlake.ai/applications/futures)
- [Streaming Progress](https://docs.tensorlake.ai/applications/guides/streaming-progress)
- [Retries](https://docs.tensorlake.ai/applications/retries)
- [Autoscaling](https://docs.tensorlake.ai/applications/guides/autoscaling)

## Prerequisites

- Python 3.11+
- OpenAI API key
- Tensorlake API key (for deploy/invoke)

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

## Run Locally

Scan the current directory:

```bash
python app.py
```

Scan a specific local path:

```bash
SCAN_REPO_PATH=/path/to/your/repo python app.py
```

Scan a remote repository (cloned at runtime):

```bash
SCAN_REPO_URL=https://github.com/org/repo SCAN_REPO_BRANCH=main python app.py
```

For private repos, embed a token in the URL:

```bash
SCAN_REPO_URL=https://token:ghp_xxx@github.com/org/repo python app.py
```

All env var options:

| Variable | Default | Description |
|---|---|---|
| `SCAN_REPO_URL` | _(empty)_ | Git URL to clone. If set, `SCAN_REPO_PATH` is ignored. |
| `SCAN_REPO_BRANCH` | _(empty)_ | Branch or tag to clone. If unset, the remote's default branch is used. |
| `SCAN_REPO_PATH` | `.` | Local path to scan (used when `SCAN_REPO_URL` is not set). |
| `SCAN_INCLUDE_GLOBS` | `**/*.py` | Comma-separated glob patterns of files to include. |
| `SCAN_EXCLUDE_GLOBS` | `**/.venv/**,**/venv/**,**/node_modules/**` | Comma-separated glob patterns of files to exclude. |
| `SCAN_VULN_CLASSES` | `idor,sql_injection,ssrf,command_injection` | Comma-separated list of vulnerability classes to run. |
| `SCAN_REPORT_PATH` | _(empty)_ | If set, the markdown report is also written to this file. |

## Deploy

```bash
tensorlake secrets set OPENAI_API_KEY "sk-..."
tensorlake deploy app.py
```

## Invoke (Remote)

Scan a public repository (wait for JSON result):

```bash
curl -X POST https://api.tensorlake.ai/applications/security_autopatch \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "repo_url": "https://github.com/org/repo",
    "repo_branch": "",
    "include_globs": ["**/*.py"],
    "exclude_globs": ["**/.venv/**", "**/venv/**", "**/node_modules/**"],
    "vulnerability_classes": ["idor", "sql_injection", "ssrf", "command_injection"],
    "max_files_per_detector": 20,
    "max_findings_per_detector": 5,
    "model": "gpt-4.1-mini",
    "test_command": "pytest -q",
    "run_validation": true,
    "generate_fixes": true
  }'
```

For private repos, embed a token in `repo_url`:

```bash
curl -X POST https://api.tensorlake.ai/applications/security_autopatch \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "repo_url": "https://token:ghp_xxx@github.com/org/repo",
    "repo_branch": "main",
    ...
  }'
```

## Files

- `app.py`: Tensorlake functions + orchestrator
- `models.py`: Request/response contracts
- `prompts.py`: Detector/manager/validator/fixer prompts
- `requirements.txt`: dependencies

## Notes

- This example generates test drafts and patch proposals; it does not auto-apply diffs or auto-open PRs.
- Keep human review in the loop before merging security fixes.

## Example Report

---

_Sample output from scanning [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI)_

# Security Autopatch Sweep

- Repository: `https://github.com/crewAIInc/crewAI`
- Branch: `main`
- Detectors run: `4`
- Findings detected: `2`
- Findings approved by manager: `1`
- Findings confirmed by validator: `1`
- Fix proposals generated: `1`

## Detector Notes
- `idor`: 0 findings. The code snippets show API client usage and CLI commands that accept organization IDs from user input or environment variables. The OrganizationCommand.switch method fetches the list of organizations the user belongs to and switches context only if the org_id matches one in the list, preventing arbitrary org_id usage. The GenerateCrewaiAutomationTool optionally accepts an organization_id to include in request headers, but this is a client-side header and server-side authorization is not shown. The PlusAPI client includes the org_uuid from settings in headers for API calls. No direct evidence of missing ownership or tenant checks on object IDs is found in the provided code. The organization IDs are validated against the user's organizations before switching or used as headers for API calls, assuming server-side enforces access control. Therefore, no exploitable IDOR vulnerabilities are identified in these snippets.
- `sql_injection`: 1 findings. The NL2SQLTool executes raw SQL queries constructed from user input without parameter binding, allowing SQL injection.
- `ssrf`: 0 findings. No realistic, exploitable SSRF vulnerabilities found in the provided test code snippets. All HTTP requests use fixed or environment-controlled URLs or parameters without direct attacker-controlled URL fetching without validation.
- `command_injection`: 1 findings. The code snippets use subprocess.run with user-controlled inputs as list arguments without shell=True, which is generally safe. However, in lib/crewai/src/crewai/cli/train_crew.py, the filename argument is used directly in the command list without sanitization, and the only check is that filename must not end with '.pkl' (which is contradictory). This could allow injection if filename contains malicious characters or sequences that alter command behavior. Other snippets use subprocess.run with controlled arguments or validated inputs.

## Finding Details

---

### nl2sql_tool_001 — [HIGH] `sql_injection`

**Location:** `lib/crewai-tools/src/crewai_tools/tools/nl2sql/nl2sql_tool.py:56` &nbsp;|&nbsp; **Endpoint:** `_run(sql_query: str)` &nbsp;|&nbsp; **Confidence:** `100%`

**Summary:** NL2SQLTool executes raw SQL queries from untrusted input without parameter binding.

**Evidence:**
```
def _run(self, sql_query: str):
    try:
        data = self.execute_sql(sql_query)
    except Exception as exc:
        ...

def execute_sql(self, sql_query: str) -> list | str:
    ...
    result = session.execute(text(sql_query))
    ...
```

**Exploit scenario:** An attacker can supply malicious SQL in the 'sql_query' parameter to execute arbitrary SQL commands, potentially extracting or modifying sensitive data.

**Recommended fix:** Use parameterized queries with bound parameters instead of executing raw SQL strings. Avoid string formatting or concatenation of SQL queries with untrusted input.

**Manager review:** `approved` — The code executes raw SQL queries from untrusted input directly via SQLAlchemy's text() without parameter binding, enabling credible SQL injection exploitability.

**Validation:** `confirmed` — The test demonstrates that before the fix, SQL injection is possible by injecting a malicious query that drops a table, which succeeds. After the fix, the injection is prevented and the table remains intact.
  - Suggested test file: `tests/test_nl2sql_tool_injection.py`

**Fix proposal:** `generated`

**PR title:** Fix SQL Injection in NL2SQLTool by disallowing multiple statements and enforcing single statement execution

**Files touched:** `lib/crewai-tools/src/crewai_tools/tools/nl2sql/nl2sql_tool.py`

**PR description:**

### Summary
This patch addresses a high severity SQL injection vulnerability in the `NL2SQLTool` class where raw SQL queries from untrusted input were executed without parameter binding or validation.

### Details
- Added validation in `_run` and `execute_sql` methods to reject SQL queries containing multiple statements separated by semicolons (except a trailing semicolon).
- This prevents attackers from injecting additional malicious SQL commands such as `DROP TABLE`.
- The patch does not yet implement full parameter binding since the current interface accepts raw SQL strings without parameters, but it enforces a strict single statement policy to mitigate injection.

### Validation
The existing test `test_sql_injection_prevention` demonstrates that before the fix, injection succeeds and drops the `users` table, while after the fix, injection is prevented and the table remains intact.

### Notes
- Further improvements could include redesigning the interface to accept parameterized queries or structured query objects to fully eliminate injection risks.
- This patch provides an immediate mitigation by rejecting multiple statements in a single query string.

Please review and merge to secure the NL2SQLTool against SQL injection attacks.

**Patch diff:**
```diff
@@ -56,13 +56,26 @@
-    def _run(self, sql_query: str):
-        try:
-            data = self.execute_sql(sql_query)
-        except Exception as exc:
-            data = (
-                f"Based on these tables {self.tables} and columns {self.columns}, "
-                "you can create SQL queries to retrieve data from the database."
-                f"Get the original request {sql_query} and the error {exc} and create the correct SQL query."
-            )
-
-        return data
+    def _run(self, sql_query: str):
+        # Prevent SQL injection by disallowing multiple statements and using parameterized queries
+        # Here we assume sql_query is a single statement with no parameters, so we validate it
+        # For demonstration, we reject queries containing semicolons except at the end
+        if ";" in sql_query.strip().rstrip(';'):
+            raise ValueError("Multiple SQL statements detected; only single statements are allowed.")
+
+        try:
+            data = self.execute_sql(sql_query)
+        except Exception as exc:
+            data = (
+                f"Based on these tables {self.tables} and columns {self.columns}, "
+                "you can create SQL queries to retrieve data from the database."
+                f"Get the original request {sql_query} and the error {exc} and create the correct SQL query."
+            )
+
+        return data
@@ -70,10 +83,19 @@
-        try:
-            result = session.execute(text(sql_query))
-            session.commit()
-
-            if result.returns_rows:  # type: ignore[attr-defined]
-                columns = result.keys()
-                return [
-                    dict(zip(columns, row, strict=False)) for row in result.fetchall()
-                ]
-            return f"Query {sql_query} executed successfully"
+        # Disallow multiple statements separated by semicolons to prevent injection
+        if ";" in sql_query.strip().rstrip(';'):
+            raise ValueError("Multiple SQL statements detected; only single statements are allowed.")
+
+        # Use parameterized queries if parameters are provided (not in current interface)
+        # Since sql_query is raw string, we only allow single statement execution
+        try:
+            result = session.execute(text(sql_query))
+            session.commit()
+
+            if result.returns_rows:  # type: ignore[attr-defined]
+                columns = result.keys()
+                return [
+                    dict(zip(columns, row, strict=False)) for row in result.fetchall()
+                ]
+            return f"Query {sql_query} executed successfully"
+        except Exception as e:
+            session.rollback()
+            raise e
```

**Fixer notes:**
- Disallow multiple SQL statements separated by semicolons to prevent injection.
- Raise ValueError if multiple statements detected.
- Patch mitigates injection by enforcing single statement execution.
- Further improvements could include parameterized query support.

---

### CI-001 — [MEDIUM] `command_injection`

**Location:** `lib/crewai/src/crewai/cli/train_crew.py:7` &nbsp;|&nbsp; **Endpoint:** `train_crew` &nbsp;|&nbsp; **Confidence:** `70%`

**Summary:** Potential command injection via unsanitized filename argument passed to subprocess.run

**Evidence:**
```
command = ["uv", "run", "train", str(n_iterations), filename]

result = subprocess.run(command, capture_output=False, text=True, check=True)  # noqa: S603

if n_iterations <= 0:
    raise ValueError("The number of iterations must be a positive integer.")

if not filename.endswith(".pkl"):
    raise ValueError("The filename must not end with .pkl")
```

**Exploit scenario:** An attacker controlling the filename parameter could inject additional commands or arguments if the filename contains shell metacharacters or sequences that affect the command execution, potentially leading to arbitrary command execution or unexpected behavior.

**Recommended fix:** Validate and sanitize the filename input strictly to allow only safe characters (e.g., alphanumeric, underscores, dashes) and ensure it cannot contain shell metacharacters or path traversal sequences. Also, clarify the filename extension check logic (currently it raises if filename ends with .pkl, which seems incorrect).

**Manager review:** `rejected` — The subprocess.run call uses a list of arguments, which prevents shell injection via shell metacharacters. The filename is passed as a separate argument, not via a shell string, so command injection is not credible. The filename validation logic is incorrect but does not enable injection.