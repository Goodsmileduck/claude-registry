---
name: cloudflare-analytics
description: Queries Cloudflare traffic and usage analytics via the GraphQL Analytics API — per-hostname request/visitor/bandwidth breakdowns, status-code and cache analysis, Workers and Pages Functions invocation metrics, Web Analytics/RUM. Use when asked how much traffic a site, app, or subdomain gets, which services are actually used, or when writing or debugging a Cloudflare GraphQL query, or when such a query returns errors or empty results.
---

# Cloudflare Analytics

Cloudflare's MCP servers do **not** expose traffic analytics — the observability
server covers Workers Logs only (and only when a Worker sets `[observability]`
in its wrangler config), and Radar is public internet-wide data unrelated to any
one account. Per-hostname numbers come from the GraphQL Analytics API, which
these scripts wrap.

## Setup

All scripts need `CLOUDFLARE_API_TOKEN`. Create at dash.cloudflare.com → My
Profile → API Tokens → Custom token with `Zone > Analytics > Read`,
`Zone > Zone > Read`, and `Account > Account Analytics > Read`.

**Never let the user paste the token into the conversation** — it lands in the
transcript permanently. Ask them to run the command themselves (in Claude Code,
prefixed with `!`), which returns only the output. If a token does end up in the
transcript, say so plainly and tell them to rotate it.

## Workflow

**1. Probe limits first.** Plan entitlements decide what is answerable, and they
are not derivable from the docs — the published API capability is not the same
as a given zone's grant.

```bash
scripts/cf-limits.sh <zone-name>
```

Returns `maxDuration` (widest single window) and `notOlderThan` (retention) per
dataset. These are independent: 8-day retention with a 1-day max window means
eight separate queries, not one.

**2. Pick the dataset by whether you need a per-hostname breakdown.**
`clientRequestHTTPHost` exists only on `httpRequestsAdaptiveGroups`, which is
the most range-restricted dataset. That tension drives most decisions here —
see [references/datasets.md](references/datasets.md).

**3. Run.**

```bash
scripts/cf-host-traffic.sh <zone> [YYYY-MM-DD]  # per-hostname table, 24h window
scripts/cf-query.sh <query-file> '<vars-json>'  # arbitrary query
```

`cf-query.sh` accepts `-` to read the query from stdin, prints raw JSON, and
exits non-zero on GraphQL errors.

## Non-obvious behavior

**Errors arrive with HTTP 200.** A failed query returns status 200 with an
`errors` array. Always inspect `body.errors`; both scripts do.

**Numbers may be estimates.** `…Adaptive` datasets sample adaptively by volume,
so a quiet host returns exact counts while a busy one is extrapolated — in the
same table. Always select `avg { sampleInterval }` and report a value > 1 as an
estimate, not a measurement. Extrapolation mechanics: see
[references/datasets.md](references/datasets.md#sampling).

**Empty results are rarely a permissions problem.** Missing scopes return 403.
Empty usually means the beacon isn't installed (RUM), the Worker has no
`[observability]` block (Workers Logs), or the window fell outside retention.

**Per-hostname history beyond `notOlderThan` does not exist and cannot be
reconstructed.** With 8-day retention, "per-app traffic last month" is not
answerable — say so instead of silently substituting zone-wide totals from
`httpRequests1dGroups`, which measure something different.

## Interpreting output

- `VISITS = 0` on API-serving hosts is correct — visits approximate page loads,
  and those hosts serve API calls, not pages.
- Hostnames with an explicit `:port` or a trailing dot are bot/scanner noise;
  `cf-host-traffic.sh` excludes them from the table and totals them in a footer.
- Edge request counts and RUM page views will never match. Edge counts bots,
  assets, and API calls, and treats each unique IP as a visit.
- A zone often hosts more than the services under discussion — confirm which
  hostnames the user actually means before summarizing "app traffic".

## Proactive triggers

- User asks for per-hostname traffic older than the dataset's retention → state
  the limit and offer zone-wide `httpRequests1dGroups` totals as the alternative,
  naming the difference.
- A query fails with "time range wider than" → chunk into `maxDuration`-sized
  windows instead of retrying.
- `avg sampleInterval` > 2 on a reported number → label it an estimate in the
  summary, not a count.
- User pastes an API token into the chat → flag it immediately and advise
  rotation.
- Asked for "visitors" on a host that serves an API → explain visits ≈ page
  loads and suggest request counts instead.
