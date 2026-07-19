#!/usr/bin/env bash
# Run an arbitrary Cloudflare GraphQL Analytics query.
#
# Usage:
#   cf-query.sh <query-file> [variables-json]
#   echo '<query>' | cf-query.sh - '{"zoneTag":"..."}'
#
# Requires CLOUDFLARE_API_TOKEN in the environment.
# Prints the raw JSON body. Exits 1 if the response carries GraphQL errors,
# which can happen on an HTTP 200 — always check, never trust the status code.

set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"
QUERY_FILE="${1:?usage: cf-query.sh <query-file|-> [variables-json]}"
VARS="${2:-{\}}"

query=$(cat -- "$QUERY_FILE")

resp=$(jq -n --arg q "$query" --argjson v "$VARS" '{query:$q, variables:$v}' \
  | curl -s https://api.cloudflare.com/client/v4/graphql \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data @-)

if [ "$(jq -r 'if (.errors // empty | length) > 0 then "yes" else "no" end' <<<"$resp")" = "yes" ]; then
  echo "GraphQL errors:" >&2
  jq -r '.errors[] | "  \(.message)\(if .path then "  (path: \(.path|join(".")))" else "" end)"' <<<"$resp" >&2
  exit 1
fi

jq . <<<"$resp"
