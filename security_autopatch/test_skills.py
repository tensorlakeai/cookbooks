"""Skill smoke-test for all 4 agent types.

Tests:
  1. Detector  — 4 classes, each against a file with one clear vulnerability
  2. Manager   — adversarial review using a real finding (from detector run)
  3. Validator — integration-test writer using an approved finding
  4. Fixer     — patch generator using a confirmed finding

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python test_skills.py

    # To test only specific agents:
    python test_skills.py --only detector manager
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Intentionally vulnerable sample files — one clear bug per class
# ---------------------------------------------------------------------------

VULNERABLE_FILES: dict[str, dict[str, str]] = {
    "idor": {
        "api/documents.py": """\
from flask import Flask, request, jsonify, g

app = Flask(__name__)

@app.route("/api/documents/<int:doc_id>", methods=["GET"])
def get_document(doc_id):
    # BUG: no ownership check — any authenticated user can read any document
    doc = db.query("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        return jsonify({"error": "Not found"}), 404
    return jsonify(doc)

@app.route("/api/users/<int:user_id>/profile", methods=["GET"])
def get_profile(user_id):
    # BUG: user_id from URL, no check that it matches current session user
    profile = db.get_user(user_id)
    return jsonify(profile)

@app.route("/api/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id):
    current_user = g.user
    # BUG: deletes the order without checking if it belongs to current_user
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    return jsonify({"deleted": order_id})
""",
    },
    "sql_injection": {
        "db/queries.py": """\
import sqlite3

def search_users(username):
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    # BUG: f-string interpolation directly into SQL
    cursor.execute(f"SELECT * FROM users WHERE username = '{username}'")
    return cursor.fetchall()

def get_order(order_id):
    conn = sqlite3.connect("app.db")
    # BUG: string concatenation
    query = "SELECT * FROM orders WHERE id = " + str(order_id) + " AND status = 'active'"
    conn.execute(query)

def filter_products(category, sort_by):
    conn = sqlite3.connect("app.db")
    # BUG: sort_by from user input, no allowlist
    sql = "SELECT * FROM products WHERE category = '%s' ORDER BY %s" % (category, sort_by)
    conn.execute(sql)
""",
    },
    "ssrf": {
        "integrations/webhooks.py": """\
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/api/preview", methods=["POST"])
def preview_url():
    # BUG: fetches user-supplied URL with no validation
    url = request.json.get("url")
    resp = requests.get(url, timeout=5)
    return jsonify({"content": resp.text[:500]})

@app.route("/api/webhook/test", methods=["POST"])
def test_webhook():
    # BUG: callback_url comes from user input, no allowlist
    callback_url = request.json.get("callback_url")
    payload = {"event": "test", "timestamp": "2024-01-01"}
    requests.post(callback_url, json=payload)
    return jsonify({"sent": True})

@app.route("/api/fetch-logo", methods=["GET"])
def fetch_logo():
    domain = request.args.get("domain")
    # BUG: domain from query param, user controls the host
    logo_url = f"http://{domain}/favicon.ico"
    import urllib.request
    data = urllib.request.urlopen(logo_url).read()
    return data
""",
    },
    "command_injection": {
        "utils/image_processor.py": """\
import subprocess
import os
from flask import Flask, request

app = Flask(__name__)

@app.route("/api/convert", methods=["POST"])
def convert_image():
    filename = request.form.get("filename")
    # BUG: filename from user, shell=True with string interpolation
    result = subprocess.run(
        f"convert uploads/{filename} output/{filename}.png",
        shell=True, capture_output=True, text=True
    )
    return result.stdout

@app.route("/api/ping", methods=["GET"])
def ping_host():
    host = request.args.get("host")
    # BUG: host from query param interpolated into shell command
    output = os.popen(f"ping -c 1 {host}").read()
    return output

@app.route("/api/git-log", methods=["GET"])
def git_log():
    repo = request.args.get("repo_path")
    # BUG: repo path injected into shell command
    result = subprocess.check_output(f"git -C {repo} log --oneline -5", shell=True)
    return result.decode()
""",
    },
}

# ---------------------------------------------------------------------------
# A realistic pre-baked finding for testing manager/validator/fixer
# in isolation (without waiting for detector to run first).
# Uses the sql_injection file above.
# ---------------------------------------------------------------------------

BAKED_FINDING_DATA = {
    "finding_id": "sql_injection-1",
    "vulnerability_class": "sql_injection",
    "severity": "high",
    "endpoint": "search_users(username)",
    "file_path": "db/queries.py",
    "line_start": 7,
    "line_end": 7,
    "summary": "SQL injection via f-string interpolation in search_users()",
    "evidence": "cursor.execute(f\"SELECT * FROM users WHERE username = '{username}'\")",
    "exploit_scenario": (
        "An attacker passes username=\"' OR '1'='1\" to retrieve all users, "
        "or \"'; DROP TABLE users; --\" to destroy data."
    ),
    "confidence": 0.95,
    "recommended_fix": (
        "Use parameterized query: cursor.execute("
        "\"SELECT * FROM users WHERE username = ?\", (username,))"
    ),
}

BAKED_MANAGER_REVIEW_DATA = {
    "finding_id": "sql_injection-1",
    "decision": "approved",
    "rationale": (
        "The f-string interpolation directly embeds user input into SQL with no escaping. "
        "The function is reachable from HTTP endpoints. Exploitability is high."
    ),
    "requested_followups": [],
}

BAKED_VALIDATION_DATA = {
    "finding_id": "sql_injection-1",
    "status": "confirmed",
    "rationale": "Test exercises the vulnerable code path directly.",
    "test_file_path": "tests/security/test_sql_injection_search_users.py",
    "test_code": """\
import pytest
from db.queries import search_users

def test_sql_injection_in_search_users():
    # Without fix: this should NOT return all users
    results = search_users("' OR '1'='1")
    assert len(results) == 0, "SQL injection allowed — returned all users"
""",
    "run_command": "pytest tests/security/test_sql_injection_search_users.py -v",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(model: str):
    sys.path.insert(0, str(Path(__file__).parent))
    from models import SecuritySweepRequest
    return SecuritySweepRequest(
        repo_path=".",
        vulnerability_classes=list(VULNERABLE_FILES.keys()),
        max_files_per_detector=10,
        max_findings_per_detector=5,
        max_chars_per_file=8000,
        model=model,
        run_validation=False,
        generate_fixes=False,
    )


def _snippets_from(files: dict[str, str]):
    sys.path.insert(0, str(Path(__file__).parent))
    from models import FileSnippet
    return [
        FileSnippet(path=p, content=c, line_count=c.count("\n") + 1)
        for p, c in files.items()
    ]


def _header(title: str):
    print(f"\n{'='*62}")
    print(f"  {title}")
    print(f"{'='*62}")


def _result_line(label: str, ok: bool, detail: str = ""):
    icon = "✓" if ok else "✗"
    status = "PASS" if ok else "FAIL"
    print(f"  [{icon} {status}] {label}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Detector tests (all 4 classes in parallel)
# ---------------------------------------------------------------------------

async def test_detectors(request) -> dict[str, bool]:
    from app import _detector_agent

    _header("DETECTOR AGENTS — 4 vulnerability classes in parallel")

    tasks = {
        vuln_class: asyncio.create_task(
            _detector_agent(vuln_class, request, _snippets_from(files))
        )
        for vuln_class, files in VULNERABLE_FILES.items()
    }

    results: dict[str, bool] = {}

    for vuln_class, task in tasks.items():
        try:
            result = await task
            found = len(result.findings) > 0
            results[vuln_class] = found
            detail = f"{len(result.findings)} finding(s)"
            if result.findings:
                f = result.findings[0]
                detail += f" | [{f.severity.upper()}] {f.file_path}:{f.line_start} — {f.summary[:60]}"
            _result_line(vuln_class, found, detail)
        except Exception as exc:
            results[vuln_class] = False
            _result_line(vuln_class, False, f"ERROR: {exc}")

    return results


# ---------------------------------------------------------------------------
# 2. Manager test — reviews the baked finding (should approve it)
# ---------------------------------------------------------------------------

async def test_manager(request, finding=None, snippets=None) -> dict:
    from app import _manager_agent
    from models import CandidateFinding

    _header("MANAGER AGENT — adversarial review of a real SQLi finding")

    if finding is None:
        finding = CandidateFinding.model_validate(BAKED_FINDING_DATA)

    if snippets is None:
        # Use the files that match the finding's vulnerability class; fall back to sql_injection
        snippets = _snippets_from(
            VULNERABLE_FILES.get(finding.vulnerability_class, VULNERABLE_FILES["sql_injection"])
        )

    try:
        review = await _manager_agent(finding, request, snippets)
        approved = review.decision == "approved"
        _result_line(
            f"decision={review.decision}",
            approved,
            review.rationale[:100],
        )
        if review.requested_followups:
            for fu in review.requested_followups:
                print(f"    followup: {fu}")
        return {"passed": approved, "review": review}
    except Exception as exc:
        _result_line("manager_agent", False, f"ERROR: {exc}")
        return {"passed": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. Validator test — writes a test for the approved finding
# ---------------------------------------------------------------------------

async def test_validator(request, finding=None, manager_review=None, snippets=None) -> dict:
    from app import _validator_agent
    from models import CandidateFinding, ManagerReview

    _header("VALIDATOR AGENT — writes integration test for confirmed SQLi")

    if finding is None:
        finding = CandidateFinding.model_validate(BAKED_FINDING_DATA)
    if manager_review is None:
        manager_review = ManagerReview.model_validate(BAKED_MANAGER_REVIEW_DATA)

    if snippets is None:
        snippets = _snippets_from(
            VULNERABLE_FILES.get(finding.vulnerability_class, VULNERABLE_FILES["sql_injection"])
        )

    try:
        validation = await _validator_agent(finding, manager_review, request, snippets)
        confirmed = validation.status == "confirmed"
        has_test_code = bool(validation.test_code.strip())
        passed = confirmed and has_test_code

        _result_line(f"status={validation.status}", passed, validation.rationale[:100])
        if validation.test_file_path:
            print(f"    test_file: {validation.test_file_path}")
        if validation.test_code:
            preview = validation.test_code.strip()[:200].replace("\n", "\n    ")
            print(f"    test_code preview:\n    {preview}")
        return {"passed": passed, "validation": validation}
    except Exception as exc:
        _result_line("validator_agent", False, f"ERROR: {exc}")
        return {"passed": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 4. Fixer test — generates a patch for the confirmed finding
# ---------------------------------------------------------------------------

async def test_fixer(request, finding=None, validation=None, snippets=None) -> dict:
    from app import _fixer_agent
    from models import CandidateFinding, ValidationResult

    _header("FIXER AGENT — generates minimal patch for confirmed SQLi")

    if finding is None:
        finding = CandidateFinding.model_validate(BAKED_FINDING_DATA)
    if validation is None:
        validation = ValidationResult.model_validate(BAKED_VALIDATION_DATA)

    if snippets is None:
        snippets = _snippets_from(
            VULNERABLE_FILES.get(finding.vulnerability_class, VULNERABLE_FILES["sql_injection"])
        )

    try:
        proposal = await _fixer_agent(finding, validation, request, snippets)
        generated = proposal.status == "generated"
        has_diff = bool(proposal.patch_diff.strip())
        passed = generated and has_diff

        _result_line(f"status={proposal.status}", passed)
        if proposal.pr_title:
            print(f"    PR title: {proposal.pr_title}")
        if proposal.files_touched:
            print(f"    files_touched: {proposal.files_touched}")
        if proposal.patch_diff:
            preview = proposal.patch_diff.strip()[:300].replace("\n", "\n    ")
            print(f"    patch_diff preview:\n    {preview}")
        if proposal.notes:
            for note in proposal.notes:
                print(f"    note: {note}")
        return {"passed": passed, "proposal": proposal}
    except Exception as exc:
        _result_line("fixer_agent", False, f"ERROR: {exc}")
        return {"passed": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(only: list[str] | None = None):
    # Load API key from .env if not set
    if not os.getenv("ANTHROPIC_API_KEY"):
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    break

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-...  &&  python test_skills.py")
        sys.exit(1)

    model = os.getenv("TEST_MODEL", "claude-sonnet-4-6")
    sys.path.insert(0, str(Path(__file__).parent))
    request = _make_request(model)

    run_all = not only
    scores: list[bool] = []

    # --- Detectors (parallel) ---
    if run_all or "detector" in only:
        detector_results = await test_detectors(request)
        scores.extend(detector_results.values())
        # Grab the first real finding to chain into manager/validator/fixer
        first_finding = None
        for vuln_class, found in detector_results.items():
            if found:
                # Re-run to get the actual finding object
                from app import _detector_agent
                result = await _detector_agent(
                    vuln_class, request, _snippets_from(VULNERABLE_FILES[vuln_class])
                )
                if result.findings:
                    first_finding = result.findings[0]
                    break
    else:
        first_finding = None

    # Snippets for the live finding (or fall back to baked sql_injection snippets)
    if first_finding is not None:
        live_snippets = _snippets_from(
            VULNERABLE_FILES.get(first_finding.vulnerability_class, VULNERABLE_FILES["sql_injection"])
        )
    else:
        live_snippets = None

    # --- Manager ---
    manager_result = None
    if run_all or "manager" in only:
        manager_result = await test_manager(request, finding=first_finding, snippets=live_snippets)
        scores.append(manager_result["passed"])
        approved_review = manager_result.get("review") if manager_result.get("passed") else None
    else:
        approved_review = None

    # --- Validator ---
    validator_result = None
    if run_all or "validator" in only:
        validator_result = await test_validator(
            request,
            finding=first_finding,
            manager_review=approved_review,
            snippets=live_snippets,
        )
        scores.append(validator_result["passed"])
        confirmed_validation = validator_result.get("validation") if validator_result.get("passed") else None
    else:
        confirmed_validation = None

    # --- Fixer ---
    if run_all or "fixer" in only:
        fixer_result = await test_fixer(
            request,
            finding=first_finding,
            validation=confirmed_validation,
            snippets=live_snippets,
        )
        scores.append(fixer_result["passed"])

    # --- Summary ---
    passed = sum(scores)
    total = len(scores)
    _header(f"SUMMARY — {passed}/{total} checks passed")
    if passed == total:
        print("  All skills working correctly.")
    else:
        print(f"  {total - passed} check(s) need attention.")
    print()

    return passed == total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test security agent skills")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["detector", "manager", "validator", "fixer"],
        help="Run only specific agents (default: all)",
    )
    args = parser.parse_args()
    success = asyncio.run(main(only=args.only))
    sys.exit(0 if success else 1)
