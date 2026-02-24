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

### Security Autopatch Sweep

- Repository: `https://github.com/crewAIInc/crewAI`
- Branch: `main`
- Detectors run: `4`
- Findings detected: `1`
- Findings approved by manager: `1`
- Findings confirmed by validator: `1`
- Fix proposals generated: `1`

#### Detector Notes

- `idor`: 0 findings. No direct IDOR vulnerability found — organization IDs are validated against the user's org list before use.
- `sql_injection`: 1 findings. Detected SQL injection risk in NL2SQLTool where user input is directly interpolated into SQL query strings without parameter binding.
- `ssrf`: 0 findings. All HTTP requests use fixed or mocked URLs without direct attacker-controlled URL input.
- `command_injection`: 0 findings. All subprocess.run calls use argument lists without shell=True.

#### Finding Details

---

##### nl2sql_tool_001 — [HIGH] `sql_injection`

**Location:** `lib/crewai-tools/src/crewai_tools/tools/nl2sql/nl2sql_tool.py:56` &nbsp;|&nbsp; **Endpoint:** `_run(sql_query: str)` &nbsp;|&nbsp; **Confidence:** `90%`

**Summary:** Direct string interpolation of user input into SQL query without parameter binding in execute_sql method.

**Evidence:**
```python
def _run(self, sql_query: str):
    try:
        data = self.execute_sql(sql_query)
    ...

def execute_sql(self, sql_query: str) -> list | str:
    ...
    result = session.execute(text(sql_query))
```

**Exploit scenario:** An attacker can craft malicious SQL queries as input to `_run`, which are executed directly without parameterization, allowing arbitrary SQL execution, data leakage, or data manipulation.

**Recommended fix:** Use parameterized queries with bound parameters instead of direct string interpolation.

**Manager review:** `approved` — User input is directly passed as a raw SQL string to execute_sql without sanitization.

**Validation:** `confirmed` — A test with malicious input will fail before the fix and pass after parameterization is applied.
- Suggested test file: `tests/test_nl2sql_tool_injection.py`

**Fix proposal:** `generated`

**PR title:** Mitigate SQL Injection by Blocking Multiple Statements in NL2SQLTool._run

**Files touched:** `lib/crewai-tools/src/crewai_tools/tools/nl2sql/nl2sql_tool.py`

**Patch diff:**
```diff
@@ -56,7 +56,12 @@ class NL2SQLTool(BaseTool):
-    def _run(self, sql_query: str):
+    def _run(self, sql_query: str):
+        # Prevent execution of multiple statements to mitigate SQL injection
+        if ";" in sql_query.strip().rstrip(';'):
+            raise ValueError("Multiple SQL statements are not allowed.")
+
         try:
             data = self.execute_sql(sql_query)
```

**Fixer notes:**
- Added check in `_run` to reject multiple SQL statements separated by semicolons.
- Prevents execution of malicious multi-statement SQL injection payloads.
- Maintains existing behavior for single-statement queries.

---