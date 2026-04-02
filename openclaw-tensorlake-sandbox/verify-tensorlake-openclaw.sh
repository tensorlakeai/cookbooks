#!/usr/bin/env bash
# verify-tensorlake-openclaw.sh
# Smoke-tests the tensorlake-openclaw-shim integration end-to-end.
#
# Usage:
#   TENSORLAKE_API_KEY=<key> ./verify-tensorlake-openclaw.sh
#
# Optional:
#   SHIM_PATH=./tensorlake-openclaw-shim  # override the shim binary to test
#   OPENCLAW_AGENT_ID=verify              # use a dedicated sandbox for this run

set -uo pipefail

SHIM="${SHIM_PATH:-tensorlake-openclaw-shim}"
: "${OPENCLAW_AGENT_ID:=verify}"
export OPENCLAW_AGENT_ID

# ─── Pre-flight checks ────────────────────────────────────────────────────────
if [[ -z "${TENSORLAKE_API_KEY:-}" ]]; then
    printf 'ERROR: TENSORLAKE_API_KEY is not set\n' >&2
    exit 1
fi

if ! command -v "$SHIM" &>/dev/null && [[ ! -x "$SHIM" ]]; then
    printf 'ERROR: shim not found at "%s"\n' "$SHIM" >&2
    printf '       Install it:  install -m 0755 tensorlake-openclaw-shim ~/.local/bin/tensorlake-openclaw-shim\n' >&2
    printf '       Or set:      SHIM_PATH=./tensorlake-openclaw-shim\n' >&2
    exit 1
fi

# ─── Cleanup ─────────────────────────────────────────────────────────────────
STATE_FILE="/tmp/tensorlake-openclaw-${OPENCLAW_AGENT_ID}"

cleanup() {
    local sandbox_id=""
    [[ -f "$STATE_FILE" ]] && sandbox_id=$(cat "$STATE_FILE")
    if [[ -n "$sandbox_id" ]]; then
        printf '[tensorlake] terminating sandbox %s…\n' "$sandbox_id" >&2
        curl -s --max-time 30 -X DELETE \
            -H "Authorization: Bearer ${TENSORLAKE_API_KEY}" \
            "https://api.tensorlake.ai/sandboxes/${sandbox_id}" >/dev/null
        rm -f "$STATE_FILE"
    fi
}
trap cleanup EXIT

# ─── Helpers ─────────────────────────────────────────────────────────────────
PASS=0
FAIL=0

check() {
    local name="$1" result="$2" expected="$3"
    if [[ "$result" == *"$expected"* ]]; then
        printf '  PASS  %s\n' "$name"
        (( PASS++ )) || true
    else
        printf '  FAIL  %s\n' "$name"
        printf '        expected to contain: %s\n' "$expected"
        printf '        got:                 %s\n' "$result"
        (( FAIL++ )) || true
    fi
}

# ─── Test 1: Basic command execution ─────────────────────────────────────────
printf '\n=== Test 1: basic command execution ===\n'
output=$("$SHIM" 'echo "sandbox is alive"')
check "echo output" "$output" "sandbox is alive"

# ─── Test 2: Python file write + get round-trip ───────────────────────────────
printf '\n=== Test 2: Python file write → get round-trip ===\n'

# Use a timestamp-based unique value so reruns don't produce false positives
UNIQUE="tl-verify-$(date +%s)"
REMOTE_FILE="/tmp/openclaw-verify.txt"

# Write the file from inside the sandbox using a Python one-liner
"$SHIM" "python3 -c \"open('${REMOTE_FILE}', 'w').write('${UNIQUE}')\""

# Read the file back via the 'get' sub-command (no sandbox execution required)
retrieved=$("$SHIM" get "$REMOTE_FILE")
check "file round-trip" "$retrieved" "$UNIQUE"

# ─── Summary ─────────────────────────────────────────────────────────────────
printf '\n=== %d passed  %d failed ===\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
