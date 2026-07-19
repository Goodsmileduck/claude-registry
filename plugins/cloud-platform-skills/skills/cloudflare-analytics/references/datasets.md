# Cloudflare GraphQL Analytics — dataset reference

> Parent: [../SKILL.md](../SKILL.md) — workflow and cross-cutting rules apply, especially "probe limits first" and "errors arrive with HTTP 200".

- [Query shape](#query-shape)
- [Choosing a dataset](#choosing-a-dataset)
- [Example limits from a real free-plan zone](#example-limits-from-a-real-free-plan-zone)
- [Time filter keys](#time-filter-keys)
- [Sampling](#sampling)
- [Errors](#errors)
- [Web Analytics (RUM) vs edge data](#web-analytics-rum-vs-edge-data)

## Query shape

Endpoint `https://api.cloudflare.com/client/v4/graphql`, POST only. Four layers:
`viewer` → scope → dataset node → fieldset.

```graphql
{ viewer { zones(filter: { zoneTag: $zoneTag })       { <dataset>(...) { ... } } } }
{ viewer { accounts(filter: { accountTag: $acctTag }) { <dataset>(...) { ... } } } }
```

Scope rules: max 10 zones per query (`zoneTag_in`), exactly 1 account. The scope
filter is mandatory and cannot be combined with `AND`/`OR`.

Suffix conventions: `…Groups` = pre-aggregated, returns
`{ count, sum{}, avg{}, quantiles{}, dimensions{} }`. No `Groups` = raw event rows
as flat fields. `…Adaptive…` = sampling may apply.

**Which scope holds what**

| Zone-scoped | Account-scoped |
|---|---|
| `httpRequestsAdaptiveGroups`, `httpRequests1dGroups`, `httpRequests1hGroups`, `firewallEventsAdaptive`, `emailSendingAdaptiveGroups`, `emailRoutingAdaptiveGroups` | `workersInvocationsAdaptive`, `pagesFunctionsInvocationsAdaptiveGroups`, `r2OperationsAdaptiveGroups`, `d1AnalyticsAdaptiveGroups`, `workflowsAdaptive`, `rumPageloadEventsAdaptiveGroups` |

Token scopes are listed in SKILL.md Setup. A token missing the right scope
returns **403**, not empty data — empty data means something else.

## Choosing a dataset

The decisive question is **do you need a per-hostname breakdown?**

- **Yes** → `httpRequestsAdaptiveGroups`. It is the only one with
  `clientRequestHTTPHost`. Costs you range: short max windows and days-scale
  retention on non-Enterprise plans. For a multi-day trend, issue one query per
  window and stitch.
- **No, and you want long history** → `httpRequests1dGroups`. Typically a full
  year, but zone-wide totals only. Gives
  `sum { requests bytes cachedRequests pageViews }` and `uniq { uniques }`.
- **Recent hourly shape, zone-wide** → `httpRequests1hGroups`. Days-scale.
- **Pages Functions / Workers** → account-scoped
  `pagesFunctionsInvocationsAdaptiveGroups` / `workersInvocationsAdaptive`.
  ~3 months back, 1-week max increments.

## Example limits from a real free-plan zone

Read via `scripts/cf-limits.sh` in July 2026. **Illustrative only — probe your
zone**; limits vary by plan and change over time.

| Dataset | Max window | Retention | Max rows |
|---|---|---|---|
| `httpRequestsAdaptiveGroups` | 1d | 8d | 10000 |
| `httpRequests1hGroups` | 3d | 73h | 10000 |
| `httpRequests1dGroups` | ~1y | ~1y | 10000 |
| `firewallEventsAdaptive` | 1d | 15d | 10000 |

`maxDuration` and `notOlderThan` are independent — a dataset can retain 8 days
while refusing any window wider than 1 day. Chunk on `maxDuration`, clamp the
start to `now - notOlderThan`.

## Time filter keys

The filter key must be a dimension the dataset actually groups on; mismatching
is a hard error.

| Key | Type | Use with |
|---|---|---|
| `datetime_geq` / `_leq` | Time (`2026-07-19T00:00:00Z`) | raw `…Adaptive` nodes and adaptive `…Groups` grouped on datetime — incl. `httpRequestsAdaptiveGroups` |
| `date_geq` / `_leq` | Date (`2026-07-19`) | daily rollups — `httpRequests1dGroups` |
| `datetimeHour_geq` / `_leq` | Time | hourly — `httpRequests1hGroups` |

Operators are name suffixes: `_gt _lt _geq _leq _neq _in` (scalar), `_like` with
`%` (string), `_has _hasall _hasany` (array). `AND` is implicit between keys of
one object; `OR` must be explicit. Subqueries are not supported.

Filtering uses the **event start** timestamp. `requestSource: eyeball` restricts
to end-user traffic, excluding Worker subrequests.

## Sampling

Any `…Adaptive…` node may be sampled, at a rate that adapts to volume — so a
low-traffic host comes back exact while a busy one does not, within the same
result set. `sampleInterval` is the inverse rate.

- On `…Groups` nodes, `count` and `sum{}` are **already extrapolated**. Select
  `avg { sampleInterval }` and report it; > 1 means the number is an estimate.
- On raw `…Adaptive` nodes, rows are **not** extrapolated — each row represents
  `sampleInterval` real events, so estimate the total as `Σ sampleInterval`.

## Errors

**HTTP 200 responses can still carry an `errors` array.** Always check
`body.errors`; never branch on status code alone. `scripts/cf-query.sh` does this.

| Status / message | Meaning |
|---|---|
| 400 "cannot request a time range wider than X" | window exceeds `maxDuration` — chunk it |
| 400 "scalar fields must have no selections" | selection set on a scalar, or a `…Groups` node queried like a raw node |
| 401 | missing/invalid token |
| 403 | token lacks Analytics:Read for that zone/account |
| 429 "rate limiter budget depleted" | 300 queries / 5 min budget — back off a full 5 minutes |
| 503 | transient; retry with backoff |

Rate limit is **300 queries per rolling 5 minutes**, and cost multiplies:
a 10-zone × 3-node query costs 30.

## Web Analytics (RUM) vs edge data

`rumPageloadEventsAdaptiveGroups` measures real browser page loads (and Core Web
Vitals); `httpRequestsAdaptiveGroups` measures edge requests. They will never
agree, and neither is wrong.

Edge data counts every request — bots, crawlers, images, API calls — and treats
each unique IP as a visit. RUM counts only real browsers running the beacon.

RUM returns **nothing** unless: a Web Analytics site exists in the account
(giving a `siteTag`), the beacon `static.cloudflareinsights.com/beacon.min.js`
actually loads, and the page's Content-Security-Policy permits it (`script-src`
must allow `static.cloudflareinsights.com`; `connect-src` must allow the beacon
endpoint). Ad-blockers suppress it, and it cannot see Worker subrequests. Before
promising RUM numbers, verify a site is configured and the beacon fires.
