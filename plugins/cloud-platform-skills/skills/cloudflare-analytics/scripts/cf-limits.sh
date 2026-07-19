#!/usr/bin/env bash
# Probe the runtime limits for zone-scoped analytics datasets. ALWAYS run this
# before writing a query with a time range — plan entitlements decide what is
# possible, and they are not discoverable from the docs.
#
# Usage: cf-limits.sh <zone-name>
#
# maxDuration  = widest single query window
# notOlderThan = retention; how far back you may query at all
# These are INDEPENDENT: a dataset can retain 8 days but refuse a window
# wider than 1 day, forcing you to issue one query per day and stitch.

set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"
ZONE_NAME="${1:?usage: cf-limits.sh <zone-name>}"
API="https://api.cloudflare.com/client/v4"
auth=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")

zone_id=$(curl -s "${auth[@]}" "${API}/zones?name=${ZONE_NAME}" | jq -r '.result[0].id // empty')
[ -n "$zone_id" ] || { echo "Cannot resolve zone ${ZONE_NAME} (needs Zone:Read)." >&2; exit 1; }

read -r -d '' Q <<'GQL' || true
query Limits($zoneTag: string) {
  viewer { zones(filter: { zoneTag: $zoneTag }) { settings {
    httpRequestsAdaptiveGroups { enabled maxDuration maxPageSize maxNumberOfFields notOlderThan }
    httpRequests1hGroups       { enabled maxDuration maxPageSize maxNumberOfFields notOlderThan }
    httpRequests1dGroups       { enabled maxDuration maxPageSize maxNumberOfFields notOlderThan }
    firewallEventsAdaptive     { enabled maxDuration maxPageSize maxNumberOfFields notOlderThan }
  } } }
}
GQL

resp=$(jq -n --arg q "$Q" --arg z "$zone_id" '{query:$q, variables:{zoneTag:$z}}' \
  | curl -s "${auth[@]}" --data @- "${API}/graphql")

if [ "$(jq -r 'if (.errors // empty | length) > 0 then "yes" else "no" end' <<<"$resp")" = "yes" ]; then
  jq -r '.errors[].message' <<<"$resp" >&2; exit 1
fi

echo "Zone ${ZONE_NAME} (${zone_id}) — dataset limits"
jq -r '
  def dur: if . == null then "-"
           elif . % 86400 == 0 then "\(./86400)d"
           elif . % 3600  == 0 then "\(./3600)h"
           else "\(.)s" end;
  .data.viewer.zones[0].settings
  | (["DATASET","ENABLED","MAX_WINDOW","RETENTION","MAX_ROWS","MAX_FIELDS"] | @tsv),
    (to_entries[] | [
      .key, (.value.enabled|tostring),
      (.value.maxDuration|dur), (.value.notOlderThan|dur),
      (.value.maxPageSize|tostring), (.value.maxNumberOfFields|tostring)
    ] | @tsv)
' <<<"$resp" | column -t
