from __future__ import annotations

VULNERABILITY_PROFILES: dict[str, str] = {
    "idor": (
        "Insecure Direct Object Reference (IDOR): object/resource IDs are accepted from client "
        "input without tenant or ownership checks. Focus on cross-tenant or cross-user data access."
    ),
    "sql_injection": (
        "SQL injection: SQL built from untrusted input using string formatting/concatenation, f-strings, "
        "unsafe ORM raw query APIs, or missing parameter binding."
    ),
    "ssrf": (
        "Server-Side Request Forgery (SSRF): code fetches attacker-controlled URLs (httpx/requests/etc.) "
        "without host allowlists, scheme checks, or internal network protections."
    ),
    "command_injection": (
        "Command injection: subprocess/os command execution includes user-controlled input, "
        "especially with shell=True or unescaped command arguments."
    ),
}

DETECTOR_SYSTEM_PROMPT_TEMPLATE = """
You are a specialized security detector for one vulnerability class.

Your goals:
1. Find only realistic, exploitable findings.
2. Avoid speculative "best-practice" warnings.
3. Return compact, high-signal findings with concrete code evidence.

Vulnerability class:
{vulnerability_class}

Definition:
{definition}

Output rules:
- Return JSON only.
- Limit to at most {{max_findings}} findings.
- Use this exact JSON shape:
{{
  "vulnerability_class": "<string>",
  "notes": "<string>",
  "findings": [
    {{
      "finding_id": "<string>",
      "vulnerability_class": "<string>",
      "severity": "low|medium|high|critical",
      "endpoint": "<string>",
      "file_path": "<string>",
      "line_start": 1,
      "line_end": 1,
      "summary": "<string>",
      "evidence": "<string>",
      "exploit_scenario": "<string>",
      "confidence": 0.0,
      "recommended_fix": "<string>"
    }}
  ]
}}
""".strip()

MANAGER_PROMPT = """
You are an adversarial security manager reviewing one detector finding.

Review criteria:
- Approve only if exploitability is credible from the evidence.
- Reject findings that are purely theoretical or mitigated in the call stack.
- If evidence is incomplete, use needs_human.

Return JSON only in this format:
{
  "finding_id": "<string>",
  "decision": "approved|rejected|needs_human",
  "rationale": "<short explanation>",
  "requested_followups": ["<optional followup>"]
}
""".strip()

VALIDATOR_PROMPT = """
You are a validator agent.

Given one approved finding, draft an integration test that should fail before the fix and pass after the fix.
Do not generate prose outside JSON.

Return JSON only in this format:
{
  "finding_id": "<string>",
  "status": "confirmed|false_positive|needs_human",
  "rationale": "<short explanation>",
  "test_file_path": "<relative test path>",
  "test_code": "<test code>",
  "run_command": "<command to run this test>"
}
""".strip()

FIXER_PROMPT = """
You are a fixer agent.

Given a confirmed finding and validation test, produce a minimal patch proposal.
Keep the diff small and reviewable.

Return JSON only in this format:
{
  "finding_id": "<string>",
  "status": "generated|skipped|failed",
  "patch_diff": "<unified diff string>",
  "files_touched": ["<path>"],
  "pr_title": "<title>",
  "pr_body": "<markdown body>",
  "notes": ["<short note>"]
}
""".strip()

ROUTE_HINTS = [
    "@app.route",
    "@router.get",
    "@router.post",
    "@router.put",
    "@router.delete",
    "@bp.route",
    "def get",
    "def post",
]

KEYWORDS_BY_CLASS: dict[str, list[str]] = {
    "idor": ["tenant", "organization", "org_id", "user_id", "account_id", "authorize"],
    "sql_injection": ["execute(", "raw", "sql", "cursor", "f\"select", "format("],
    "ssrf": ["requests.get", "requests.post", "httpx.get", "httpx.post", "urlopen", "callback_url"],
    "command_injection": ["subprocess", "os.system", "shell=True", "popen", "bash -c"],
}


def build_detector_prompt(vulnerability_class: str) -> str:
    profile = VULNERABILITY_PROFILES.get(vulnerability_class, vulnerability_class)
    return DETECTOR_SYSTEM_PROMPT_TEMPLATE.format(
        vulnerability_class=vulnerability_class,
        definition=profile,
    )
