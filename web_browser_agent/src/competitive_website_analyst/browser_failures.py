from __future__ import annotations


def classify_browser_failure_stage(reason: str) -> str:
    text = reason.lower()
    if "did not start within" in text:
        return "sandbox_startup"
    if "failed to install browser runtime" in text or "playwright" in text:
        return "runtime_bootstrap"
    if "browser server failed to start" in text:
        return "browser_server_startup"
    if "no metadata" in text:
        return "metadata_extraction"
    return "browser_execution"
