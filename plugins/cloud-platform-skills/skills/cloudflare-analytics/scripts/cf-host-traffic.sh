#!/usr/bin/env bash
# Per-hostname traffic for a zone over a 24h window.
#
# When each app or service lives on its own subdomain, ONE zone query grouped by
# clientRequestHTTPHost breaks out all of them at once — far cheaper than
# per-project queries, and it also surfaces hosts you forgot were deployed.
#
# Usage: cf-host-traffic.sh <zone-name> [end-date]
#   end-date: YYYY-MM-DD, reports that UTC calendar day.
#             Default: the most recent 24h.
#
# Window is fixed at 24h because httpRequestsAdaptiveGroups (the only dataset
# with a hostname dimension) commonly caps maxDuration at 1 day on
# non-Enterprise plans — verify with cf-limits.sh. For a multi-day per-host
# trend, loop this over dates within the dataset's notOlderThan retention;
# beyond that, per-host data does not exist and cannot be reconstructed.

set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"
ZONE_NAME="${1:?usage: cf-host-traffic.sh <zone-name> [YYYY-MM-DD]}"
API="https://api.cloudflare.com/client/v4"
auth=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")

if [ -n "${2:-}" ]; then
  until=$(date -u -d "${2} +1 day" +%Y-%m-%dT00:00:00Z)
  since=$(date -u -d "${2}" +%Y-%m-%dT00:00:00Z)
else
  until=$(date -u +%Y-%m-%dT%H:00:00Z)
  since=$(date -u -d '1 day ago' +%Y-%m-%dT%H:00:00Z)
fi

zone_id=$(curl -s "${auth[@]}" "${API}/zones?name=${ZONE_NAME}" | jq -r '.result[0].id // empty')
[ -n "$zone_id" ] || { echo "Cannot resolve zone ${ZONE_NAME} (needs Zone:Read)." >&2; exit 1; }

read -r -d '' Q <<'GQL' || true
query HostTraffic($zoneTag: string!, $since: Time!, $until: Time!) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      httpRequestsAdaptiveGroups(
        limit: 500
        filter: { datetime_geq: $since, datetime_leq: $until }
        orderBy: [count_DESC]
      ) {
        count
        avg { sampleInterval }
        sum { edgeResponseBytes visits }
        dimensions { clientRequestHTTPHost }
      }
    }
  }
}
GQL

resp=$(jq -n --arg q "$Q" --arg z "$zone_id" --arg s "$since" --arg u "$until" \
  '{query:$q, variables:{zoneTag:$z, since:$s, until:$u}}' \
  | curl -s "${auth[@]}" --data @- "${API}/graphql")

if [ "$(jq -r 'if (.errors // empty | length) > 0 then "yes" else "no" end' <<<"$resp")" = "yes" ]; then
  echo "GraphQL errors:" >&2; jq -r '.errors[].message' <<<"$resp" >&2; exit 1
fi

echo "Zone ${ZONE_NAME} — ${since} → ${until}"
# Hosts carrying an explicit :port or a trailing dot are scanner/bot noise
# probing cPanel-style ports (2082/2095/8080/...). Excluded from the table;
# counted in the footer.
jq -r '
  .data.viewer.zones[0].httpRequestsAdaptiveGroups
  | map(select(.dimensions.clientRequestHTTPHost | test(":[0-9]+$|\\.$") | not))
  | (["HOST","REQUESTS","VISITS","GB","SAMPLE_IVL"] | @tsv),
    (.[] | [
      .dimensions.clientRequestHTTPHost,
      .count,
      .sum.visits,
      (.sum.edgeResponseBytes / 1073741824 * 100 | round / 100),
      ((.avg.sampleInterval // 1) * 100 | round / 100)
    ] | @tsv)
' <<<"$resp" | column -t

noise=$(jq -r '
  [.data.viewer.zones[0].httpRequestsAdaptiveGroups[]
   | select(.dimensions.clientRequestHTTPHost | test(":[0-9]+$|\\.$")) | .count] | add // 0
' <<<"$resp")
echo
echo "(${noise} requests to :port / trailing-dot hostnames excluded as scanner noise)"
echo "SAMPLE_IVL > 1 means the row is extrapolated from sampled data, not an exact count."
echo "VISITS=0 on API-serving hosts is expected — visits approximate page loads."
