from __future__ import annotations

# Stages that are worth retrying — transient infrastructure or timing issues
RETRYABLE_STAGES = frozenset({
    "sandbox_startup",
    "runtime_bootstrap",
    "browser_server_startup",
    "browser_execution",
})

# Stages that are not worth retrying — the site itself is the problem
NON_RETRYABLE_STAGES = frozenset({
    "blocked_or_captcha",
    "dns_error",
})


def classify_browser_failure_stage(reason: str) -> str:
    text = reason.lower()
    if "did not start within" in text:
        return "sandbox_startup"
    if "failed to install browser runtime" in text or "playwright" in text:
        return "runtime_bootstrap"
    if "browser server failed to start" in text:
        return "browser_server_startup"
    if "captcha" in text or "blocked" in text or "403" in text:
        return "blocked_or_captcha"
    if "dns" in text or "name resolution" in text or "getaddrinfo" in text:
        return "dns_error"
    if "no metadata" in text:
        return "metadata_extraction"
    return "browser_execution"


def is_retryable(stage: str) -> bool:
    return stage not in NON_RETRYABLE_STAGES
