---
name: cloudflare-dns-zones
description: Operates Cloudflare DNS zones and records via the REST API (curl + jq) — token scoping, zone discovery, record CRUD, batch operations, BIND import/export, proxied vs DNS-only decisions, CNAME flattening at apex, DNSSEC, and DNS-01 ACME challenge wiring with cert-manager. Use when working with Cloudflare DNS, `api.cloudflare.com`, `CLOUDFLARE_API_TOKEN` (or legacy `CF_API_TOKEN`), zone records, DNS-01 challenges, mail records (MX/SPF/DKIM/DMARC), or "orange cloud / grey cloud" proxy decisions.
---

# Cloudflare DNS Zones

Operational skill for managing Cloudflare DNS through the REST API. Not for Terraform — see the Cloudflare provider docs if IaC is wanted. This skill focuses on the API directly (curl + jq), which is the source of truth every wrapper is built on.

## When to invoke

**Symptoms:**

- Need to add, update, or audit DNS records via script/CI rather than the dashboard.
- A record was switched to proxied (orange cloud) and a non-HTTP service stopped working.
- DNS-01 ACME challenges fail despite a token that "should have permission."
- Bulk record migration into or out of Cloudflare (BIND zone file in hand).
- DKIM, SPF, DMARC TXT records being authored and the long-string semantics matter.
- DNSSEC handoff to the parent registrar.
- The token in use is the deprecated Global API Key.

## Cross-cutting rules

1. **Never use the Global API Key.** It's account-wide and can't be scoped. Use API Tokens (Profile → API Tokens). Every example below uses `Authorization: Bearer $CLOUDFLARE_API_TOKEN` — the same variable wrangler, the `cf` CLI, and the official SDK read, so one export serves every Cloudflare tool.
2. **Scope tokens to the minimum.** A token for DNS work needs `Zone:Read` + `Zone:DNS:Edit` on the specific zones it operates. Not "All Zones" unless the workload genuinely touches all zones.
3. **Discover zone IDs at runtime.** Hard-coding zone IDs in scripts is brittle — they change when zones are recreated. Look them up by name on each run.
4. **Idempotent operations require list-then-act.** There is no "upsert by name+type" endpoint. Always `GET` filtered by `name` and `type` first, then `POST` (create) or `PATCH` (update by ID).
5. **Verify proxiability before setting `proxied: true`.** Only HTTP-serving hostnames should be proxied. See [proxied vs DNS-only](#proxied-vs-dns-only-the-orange-cloud-trap).

## API basics

**Base URL:** `https://api.cloudflare.com/client/v4`

**Auth header:** `Authorization: Bearer $CLOUDFLARE_API_TOKEN`

**Response envelope:** every endpoint returns:

```json
{
  "success": true|false,
  "errors": [...],
  "messages": [...],
  "result": {...} | [...]
}
```

Always check `.success` and extract from `.result`. Don't assume the top-level is the data.

**Useful curl flags:** `-sf` (silent + fail on HTTP 4xx/5xx, so the shell sees a non-zero exit). For body-on-error, replace with `-s` and check `.success` in jq.

```bash
# Verify token works and see what it can do
curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  https://api.cloudflare.com/client/v4/user/tokens/verify | jq .result.status
# Expected: "active"
```

## Token scoping

API tokens are created at the user level (Profile → API Tokens → Create Token). Three knobs:

| Knob | Effect |
|---|---|
| **Permissions** | Per-product, per-action. For DNS work: `Zone:Read` + `Zone:DNS:Edit`. (Dashboard navigates as Zone → DNS → Edit.) |
| **Resources** | Which accounts and zones. Prefer "Include → Specific zone" over "All zones from an account." |
| **Client IP / TTL** | Optional. CI tokens may not have a stable IP; leave Client IP unrestricted unless you control the runner IP. |

**Minimum-scope cookbook:**

- Read-only DNS audit: `Zone:Read` on the target zones. Nothing else.
- Record CRUD: `Zone:Read` + `Zone:DNS:Edit` on target zones.
- DNS-01 ACME challenges (cert-manager): `Zone:Read` (all zones — needed for zone discovery by name) + `Zone:DNS:Edit` on the specific zones holding `_acme-challenge` records. See [DNS-01 with cert-manager](#dns-01-with-cert-manager).
- Zone provisioning (rare): `Account:Cloudflare DNS:Edit` at account level.

## Zone discovery

```bash
# By exact name
ZONE_ID=$(curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=example.com" \
  | jq -r '.result[0].id')

# All zones (paginated; default 20 per page, max 50)
curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?per_page=50&page=1" \
  | jq -r '.result[] | "\(.id)\t\(.name)\t\(.status)"'
```

**Trap:** `?name=` is exact match. To search by suffix you must list and filter client-side. The `name=` filter on `/zones` does NOT support wildcards.

## Record CRUD

All record operations are under `/zones/{zone_id}/dns_records`. The full method set:

| Method | Endpoint | Use |
|---|---|---|
| `GET` | `/zones/{zid}/dns_records` | List (filter via `?name=`, `?type=`, `?content=`) |
| `GET` | `/zones/{zid}/dns_records/{rid}` | Read one |
| `POST` | `/zones/{zid}/dns_records` | Create |
| `PATCH` | `/zones/{zid}/dns_records/{rid}` | Partial update |
| `PUT` | `/zones/{zid}/dns_records/{rid}` | Full replace (must send all fields) |
| `DELETE` | `/zones/{zid}/dns_records/{rid}` | Delete |
| `POST` | `/zones/{zid}/dns_records/batch` | Atomic mixed-op batch (posts/patches/puts/deletes arrays) |
| `GET` | `/zones/{zid}/dns_records/export` | Export full zone as BIND file |
| `POST` | `/zones/{zid}/dns_records/import` | Import BIND file (multipart) |

### Create a record

```bash
curl -sf -X POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
  -d '{
    "type": "A",
    "name": "app.example.com",
    "content": "198.51.100.4",
    "ttl": 1,
    "proxied": true,
    "comment": "Created by deploy script"
  }'
```

Field notes:

- `name` — the FQDN, not just the subdomain. `app.example.com`, not `app`.
- `content` — for A: IPv4; AAAA: IPv6; CNAME: target FQDN (no trailing dot); TXT: the raw string (no surrounding quotes; the API handles wrapping); MX: requires also `priority`.
- `ttl` — seconds (60–86400; Enterprise: 30 minimum). `1` means "auto" (300s when DNS-only; ignored when `proxied`).
- `proxied` — `true` only for proxiable types (A, AAAA, CNAME) on HTTP-serving hostnames. Cloudflare ignores `proxied: true` on record types that can't be proxied (MX, TXT, SRV, NS, CAA, PTR).

### Idempotent upsert pattern

There is no "upsert by name+type." Implement it as list → decide → write:

```bash
NAME="app.example.com"
TYPE="A"
CONTENT="198.51.100.4"

EXISTING_ID=$(curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=$TYPE&name=$NAME" \
  | jq -r '.result[0].id // empty')

if [ -n "$EXISTING_ID" ]; then
  curl -sf -X PATCH \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$EXISTING_ID" \
    -d "{\"content\":\"$CONTENT\"}"
else
  curl -sf -X POST \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
    -d "{\"type\":\"$TYPE\",\"name\":\"$NAME\",\"content\":\"$CONTENT\",\"ttl\":1}"
fi
```

For >1 record, do NOT wrap this snippet in a `for` loop. List once, then build a single batch payload — see [Batch operations](#batch-operations). Per-record loops waste 2N requests and hit the rate limit fast.

### Batch operations

For >5 mixed changes, use the batch endpoint — single atomic call, all-or-nothing:

```bash
curl -sf -X POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/batch" \
  -d '{
    "posts": [
      {"type":"TXT","name":"s1._domainkey.example.com","content":"v=DKIM1; k=rsa; p=..."}
    ],
    "patches": [
      {"id":"<existing_record_id>","content":"v=DKIM1; k=rsa; p=NEW..."}
    ],
    "deletes": [
      {"id":"<old_record_id>"}
    ]
  }'
```

Notes:

- Atomic: if any single op fails validation, the whole batch is rejected and no changes apply.
- Order: `deletes` → `patches` → `puts` → `posts`. Plan accordingly when replacing a record with a different type.
- Limit: 1000 ops per batch.

### Rate limit

**1200 requests per 5 minutes per token** (default). Symptoms of hitting it: HTTP 429 with `Retry-After`. Mitigations:

- Use the batch endpoint instead of per-record loops.
- Add `sleep 0.5` between calls in shell loops, or use `xargs -P 4` for controlled parallelism — not unlimited.
- Honor `Retry-After` on 429.

## Proxied vs DNS-only (the orange cloud trap)

Cloudflare's "proxy" sits in front of A/AAAA/CNAME records as an HTTP/HTTPS reverse proxy. **It does not pass through other protocols.** A proxied hostname that handles non-HTTP traffic is silently broken at the edge.

| Use | Proxy setting |
|---|---|
| Web server (HTTP/HTTPS) | `proxied: true` — gets TLS, WAF, caching, DDoS protection |
| Mail server (SMTP/IMAP/POP3) | `proxied: false` — proxy drops these protocols at the edge |
| SSH/Git over SSH | `proxied: false` — same reason |
| Custom TCP/UDP service on non-HTTP port | `proxied: false` (Spectrum is the paid exception) |
| MX record target | `proxied: false` (always — MX targets must accept SMTP) |
| FTP, gaming, IoT | `proxied: false` |

When in doubt: if the service speaks HTTP(S), proxy it. Otherwise don't.

Cloudflare does not warn you when proxying breaks a service. Set MX target hostnames and any non-HTTP A records to DNS-only and document why with the `comment` field.

## CNAME flattening at apex

Cloudflare allows CNAME records at the zone apex (e.g., `example.com CNAME app.cdn.com`) — DNS standards disallow this, but Cloudflare "flattens" by resolving the CNAME at query time and returning A/AAAA records to the client.

Implications:

- `example.com` with a CNAME to a third-party CDN works without special config.
- The `proxied` flag still applies to the CNAME; flattening is transparent.
- The flattened target must itself resolve to A/AAAA; if the target is also a CNAME chain, flattening follows the chain.
- Some legacy resolvers may behave oddly; in practice this is rare.

When NOT to rely on apex CNAME flattening: when you have other infrastructure (Argo, Magic DNS, etc.) that expects real A records at apex. Use A/AAAA records to the same IPs the CNAME would flatten to.

## Mail records: SPF, DKIM, DMARC, MX

All four are TXT records (SPF is **not** a separate `SPF` type — the RFC deprecated that). Format reminders:

```bash
# SPF — only ONE SPF TXT per hostname. If you have multiple senders, merge into one string.
# Record: example.com TXT
"v=spf1 include:_spf.google.com include:mailgun.org ~all"

# DKIM — one per selector. Long key values are auto-split by Cloudflare.
# Record: selector1._domainkey.example.com TXT
"v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3..."

# DMARC — at _dmarc.<domain>
# Record: _dmarc.example.com TXT
"v=DMARC1; p=reject; rua=mailto:dmarc@example.com; ruf=mailto:dmarc@example.com; pct=100"

# MX — separate record type, requires priority
# Record: example.com MX (priority 10) → mail.example.com
```

Traps:

- **Multiple SPF records on the same hostname** — invalid per RFC 7208. Mail providers may treat the domain as unauthenticated. Combine into one.
- **Trailing dot in MX/CNAME content** — Cloudflare accepts both `mail.example.com` and `mail.example.com.`; both work, but be consistent in audits.
- **Proxied MX target** — see [proxied vs DNS-only](#proxied-vs-dns-only-the-orange-cloud-trap).

## Bulk import/export (BIND)

For zone migrations:

```bash
# Export: returns BIND zone file as plain text
curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/export" \
  -o example.com.zone

# Import: multipart upload
curl -sf -X POST -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -F "file=@example.com.zone" \
  -F "proxied=false" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/import"
```

What does NOT round-trip:

- The `proxied` flag — import sets all proxiable records according to the `proxied` form field. Adjust per-record after import if needed.
- Custom TTLs on records that get auto-proxied — proxied records ignore TTL.
- Comments and tags — these are Cloudflare extensions, not standard BIND.

Use export for backup before risky changes; use import only for fresh-zone migrations.

## DNSSEC

```bash
# Enable — response body contains the DS record fields
curl -sf -X PATCH \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dnssec" \
  -d '{"status":"active"}' \
  | jq '.result | {ds, key_tag, algorithm, digest_type, digest, status}'
```

To re-fetch later: `GET /zones/$ZONE_ID/dnssec` returns the same shape.

The handoff:

1. PATCH to `active` — Cloudflare publishes DNSKEY records in the zone.
2. Read `.result.ds` (full DS record string), `.key_tag`, `.algorithm`, `.digest_type`, `.digest`.
3. Enter the DS record at the **registrar** (where the domain is registered, often a different vendor than Cloudflare).
4. After registrar publishes, status changes from `pending` → `active`. Can take hours.

Trap: enabling DNSSEC in Cloudflare without entering the DS record at the registrar does nothing useful. The chain of trust is broken; clients fall back as if DNSSEC wasn't there.

## DNS-01 with cert-manager

cert-manager `Issuer` with Cloudflare DNS-01:

```yaml
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@example.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - dns01:
          cloudflare:
            apiTokenSecretRef:
              name: cloudflare-api-token
              key: api-token
        selector:
          dnsZones:
            - example.com
```

**Token scope:** `Zone:Read` (across all zones — needed for zone discovery by name) + `Zone:DNS:Edit` on the specific zones holding `_acme-challenge` records.

**The delegated-subdomain trap:**

If you request a cert for `*.prod.example.com` and `prod.example.com` is a separate Cloudflare zone (not a record in the `example.com` zone), the token must have `DNS:Edit` on the `prod.example.com` zone — not just `example.com`. Symptom: `presenting challenge: NS lookup found no answer for _acme-challenge.prod.example.com`.

Diagnostic:

```bash
# Which zone is authoritative for the challenge name?
dig +trace _acme-challenge.prod.example.com NS @1.1.1.1

# Did the record actually land somewhere?
dig +trace _acme-challenge.prod.example.com TXT @1.1.1.1
```

**The CNAME delegation pattern** — useful when you can't broaden the token, or want all `_acme-challenge` records consolidated in one zone:

```text
# In example.com zone (token has access):
_acme-challenge.prod.example.com  CNAME  prod.delegated.example.com

# cert-manager writes to:
_acme-challenge.prod.delegated.example.com  TXT  "<challenge>"
```

cert-manager follows the CNAME automatically when `cnameStrategy: Follow` is set on the solver. The token then only needs `DNS:Edit` on the `delegated.example.com` subzone.

## Anti-patterns checklist

- ❌ Using the Global API Key. Always use scoped tokens.
- ❌ Hard-coding zone IDs in scripts. Look up by name at runtime.
- ❌ "All Zones" scope on a token that touches one zone.
- ❌ `proxied: true` on mail, SSH, or any non-HTTP hostname.
- ❌ Multiple SPF TXT records on the same hostname. Merge.
- ❌ Setting up DNSSEC in Cloudflare and forgetting the registrar DS handoff.
- ❌ Per-record loops for bulk operations. Use the `/dns_records/batch` endpoint.
- ❌ Editing exported BIND files and re-importing as a "diff" mechanism. Use the API endpoints for surgical changes.
- ❌ Trusting `?name=` zone lookups to support wildcards. Exact match only.

## Cross-skill notes

- For object storage on R2 (Cloudflare's S3-compatible service), see the `cloud-storage-identification` skill — endpoint detection and credential model differ from generic AWS S3 patterns.
- For deploy-time DNS automation in GitHub Actions, see the `github-actions-pipelines` skill for OIDC-to-cloud and the secret-management section.
- For Workers, Pages, KV, D1, WAF: not yet covered. Build separate skills when friction emerges.
