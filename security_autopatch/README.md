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

```bash
python app.py
```

The local run scans `repo_path="."` by default and prints a markdown security report.

## Deploy

```bash
tensorlake secrets set OPENAI_API_KEY "sk-..."
tensorlake deploy app.py
```

## Invoke (Remote)

```bash
curl -X POST https://api.tensorlake.ai/applications/security_autopatch \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/workspace/repo",
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

## Files

- `app.py`: Tensorlake functions + orchestrator
- `models.py`: Request/response contracts
- `prompts.py`: Detector/manager/validator/fixer prompts
- `requirements.txt`: dependencies

## Notes

- This example generates test drafts and patch proposals; it does not auto-apply diffs or auto-open PRs.
- Keep human review in the loop before merging security fixes.
