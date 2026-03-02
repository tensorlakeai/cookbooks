# Security Autopatch — Claude Agent SDK + Tensorlake

This cookbook implements a Ramp-style vulnerability pipeline on Tensorlake Applications, using the **Claude Agent SDK** for intelligent sub-agents and **Tensorlake Futures** for parallel execution.

Inspired by:
- [Ramp Builders: "We proactively fixed ~100 security issues in 6 days with 0 humans"](https://builders.ramp.com/post/100-vulnerabilities-patched-with-0-humans)

## Architecture

```text
security_autopatch (Tensorlake @application — Coordinator Agent with custom MCP tools)
├── build_code_corpus   (Tensorlake @function  — pure Python, no LLM)
├── run_detector        (Tensorlake @function  — Claude Agent SDK sub-agent)
├── run_manager_review  (Tensorlake @function  — Claude Agent SDK sub-agent)
├── run_validator       (Tensorlake @function  — Claude Agent SDK sub-agent)
└── run_fixer           (Tensorlake @function  — Claude Agent SDK sub-agent)
```

### How it works

1. **Coordinator Agent** — A Claude Agent SDK agent (`ClaudeSDKClient`) with custom MCP tools. It decides what to do and calls tools to dispatch work.
2. **Sub-agents** — Each sub-agent (detector, manager, validator, fixer) runs as a Tensorlake function that internally uses `claude_agent_sdk.query()` with a specialized "skill" prompt.
3. **Parallel execution** — The coordinator's custom tools dispatch sub-agents in parallel using Tensorlake `Future` objects.
4. **Python objects** — Artifacts (code snippets, findings, reviews, patches) flow between stages as Pydantic models.

### Pipeline stages

| Stage | Agent | Skill | Parallelism |
|-------|-------|-------|-------------|
| 1. Corpus | `build_code_corpus` | N/A (pure Python) | Single |
| 2. Detection | `run_detector` | Per-class vulnerability skill with examples | Fan-out by vulnerability class |
| 3. Manager Review | `run_manager_review` | Adversarial review skill | Fan-out per finding |
| 4. Validation | `run_validator` | Test-driven validation skill | Fan-out per approved finding |
| 5. Fix Generation | `run_fixer` | Minimal-patch skill | Fan-out per confirmed finding |

### Blog-style skills

Instead of simple prompts, each agent uses a detailed **skill** (defined in `skills.py`) that includes:
- Vulnerability class definition
- Step-by-step analysis methodology a human analyst would follow
- Real codebase examples of the vulnerability (vulnerable + fixed code)
- Common false-positive patterns to avoid

## Tensorlake Features Used

- `@application` + `@function` runtime model
- `Future` + `RETURN_WHEN.ALL_COMPLETED` for fan-out/fan-in parallelism
- `RequestContext.progress.update()` for streaming progress
- `Retries(max_retries=...)` for durable stage retries
- `max_containers` / `warm_containers` for stage-level scale-out

## Claude Agent SDK Features Used

- `query()` for one-shot sub-agent invocations
- `ClaudeSDKClient` for multi-turn coordinator with custom tools
- `@tool` decorator for defining MCP tools that dispatch Tensorlake Futures
- `create_sdk_mcp_server()` for in-process MCP server
- `AgentDefinition`-style system prompts for specialized agent behaviour

## Prerequisites

- Python 3.10+
- Anthropic API key
- Tensorlake API key (for deploy/invoke)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
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

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(required)_ | Anthropic API key for Claude Agent SDK |
| `SCAN_REPO_URL` | _(empty)_ | Git URL to clone. If set, `SCAN_REPO_PATH` is ignored. |
| `SCAN_REPO_BRANCH` | _(empty)_ | Branch or tag to clone. |
| `SCAN_REPO_PATH` | `.` | Local path to scan (used when `SCAN_REPO_URL` is not set). |
| `SCAN_INCLUDE_GLOBS` | `**/*.py` | Comma-separated glob patterns of files to include. |
| `SCAN_EXCLUDE_GLOBS` | `**/.venv/**,**/venv/**,**/node_modules/**` | Comma-separated glob patterns to exclude. |
| `SCAN_VULN_CLASSES` | `idor,sql_injection,ssrf,command_injection` | Comma-separated vulnerability classes. |
| `SCAN_REPORT_PATH` | _(empty)_ | If set, the markdown report is also written to this file. |
| `IS_SANDBOX` | `1` | Signals to Claude Code that the environment is sandboxed, allowing `bypassPermissions` mode when running as root (e.g. in Docker). Set automatically by the app. See [claude-code#9184](https://github.com/anthropics/claude-code/issues/9184). |

## Deploy

```bash
tensorlake secrets set ANTHROPIC_API_KEY "sk-ant-..."
tensorlake deploy app.py
```

## Invoke (Remote)

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
    "test_command": "pytest -q",
    "run_validation": true,
    "generate_fixes": true
  }'
```

## Files

| File | Purpose |
|------|---------|
| `app.py` | Tensorlake application — coordinator agent + sub-agent functions |
| `skills.py` | Blog-style skill prompts for each agent (detector, manager, validator, fixer, coordinator) |
| `models.py` | Pydantic request/response contracts |
| `requirements.txt` | Dependencies |

## Notes

- This example generates test drafts and patch proposals; it does not auto-apply diffs or auto-open PRs.
- Keep human review in the loop before merging security fixes.
- The coordinator agent uses `bypassPermissions` mode since it only calls custom MCP tools (no filesystem access).
