#!/bin/bash
# Usage: ./call_api.sh "your message here"
# Requires TENSORLAKE_API_KEY env var

USER_ID="${2:-user-123}"
MESSAGE="$1"

curl -s -X POST https://api.tensorlake.ai/applications/handle_message \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d "$(jq -n --arg uid "$USER_ID" --arg msg "$MESSAGE" '{user_id: $uid, message: $msg}')"
