---
name: digitalocean-dns-zones
description: Operates DigitalOcean DNS zones and records via doctl, the DigitalOcean API v2, and the digitalocean Terraform provider — domain/record CRUD, the apex CNAME / no-flattening trap when migrating from Cloudflare, account-wide token handling, FQDN trailing-dot semantics, DNS-01 ACME wildcard certs, and nameserver delegation. Use when working with DigitalOcean DNS, doctl compute domain, DIGITALOCEAN_ACCESS_TOKEN, api.digitalocean.com domains, digitalocean_record/digitalocean_domain Terraform, apex CNAME questions, wildcard cert DNS-01, or moving a zone between Cloudflare and DigitalOcean.
---

# DigitalOcean DNS Zones

Operational skill for managing DigitalOcean DNS through doctl, the REST API, and the Terraform provider. Covers the traps that appear most often when migrating zones in or out of Cloudflare — especially apex CNAME handling, trailing-dot semantics, and the account-wide token model that differs from Cloudflare's scoped tokens.

## When to invoke

**Symptoms:**

- Apex hostname (`example.com`) refuses to take a CNAME — the provider errors or the record silently misbehaves.
- A Terraform CNAME resolves to a doubled FQDN like `api.example.com.example.com`.
- Need a wildcard cert (`*.example.com`) with a DNS-01 ACME challenge on a DO-hosted zone.
- Migrating a zone to or from Cloudflare and unsure which records need manual attention.
- Deciding whether to use doctl, the raw API, or the Terraform provider for a given task.
- Token handling for DO DNS in CI — needs guidance on secret management.

## Cross-cutting rules

These rules apply to every section below. Read them before acting.

1. **`DIGITALOCEAN_ACCESS_TOKEN` is account-wide.** Unlike Cloudflare, DigitalOcean has no zone-scoped token model. A token that can edit DNS can touch every domain in the account. Treat it as a high-value secret: store it in a secrets manager or CI secret, never echo it into shared shell history or CI logs. This is an explicit difference from the `cloudflare-dns-zones` skill's "scope tokens to the minimum" rule — on DigitalOcean you cannot scope below the account level.

2. **Domain before records.** A domain entry (`digitalocean_domain` resource or `doctl compute domain create`) must exist before any record can be added. There is no "lazy create." The apex record uses `name = "@"` in both doctl and Terraform.

3. **No apex CNAME, no flattening.** DigitalOcean has no ALIAS, ANAME, or CNAME flattening. A bare apex (`example.com`) must resolve to A or AAAA records. This is the most common trap when migrating from Cloudflare, which transparently flattens apex CNAMEs — that transparency disappears the moment you cut over nameservers to DO.

4. **CNAME and MX values are FQDNs ending in a trailing dot.** Set `value = "mail.example.com."` — with the trailing dot. A value without the trailing dot is treated as relative to the zone and the domain is appended automatically, producing a doubled FQDN such as `mail.example.com.example.com`. This is the second most common migration trap.

5. **List-then-act for idempotency.** There is no upsert-by-name endpoint. Look up a record by name and type, then create it (no ID) or update it by its record ID. Script idempotent tools this way.

6. **`ttl` accepts any non-negative integer; the default is 1800.** Do not assert a 30-second minimum — that was control-panel lore from an older version and does not reflect what the provider or API accept today.

## doctl record CRUD

doctl reads the token from `DIGITALOCEAN_ACCESS_TOKEN` or from `doctl auth init` (stored in `~/.config/doctl/config.yaml`). The raw API at `https://api.digitalocean.com/v2/domains` is the same surface if scripting without doctl.

Record names are relative to the zone — `www` for `www.example.com`. Use `@` to target the apex.

```bash
# Create the domain entry first
doctl compute domain create example.com

# List all records
doctl compute domain records list example.com

# Add an A record (apex)
doctl compute domain records create example.com \
  --record-type A --record-name @ --record-data 192.168.0.11

# Add an A record (subdomain)
doctl compute domain records create example.com \
  --record-type A --record-name www --record-data 192.168.0.11

# Update a record by ID
doctl compute domain records update example.com --record-id <id> --record-data <new-ip>

# Delete a record by ID
doctl compute domain records delete example.com <record-id>
```

To implement idempotent upsert, list and filter by name and type, then branch on whether an ID was found:

```bash
EXISTING_ID=$(doctl compute domain records list example.com \
  --format ID,Name,Type --no-header \
  | awk '$2 == "www" && $3 == "A" { print $1 }')

if [ -n "$EXISTING_ID" ]; then
  doctl compute domain records update example.com \
    --record-id "$EXISTING_ID" --record-data 192.168.0.11
else
  doctl compute domain records create example.com \
    --record-type A --record-name www --record-data 192.168.0.11
fi
```

The raw API endpoint for the same surface is `GET/POST/PUT/DELETE https://api.digitalocean.com/v2/domains/{domain_name}/records` with `Authorization: Bearer $DIGITALOCEAN_ACCESS_TOKEN`. Prefer doctl for interactive use; use the raw API when scripting inside environments where doctl is unavailable.

## Terraform

The provider is `digitalocean/digitalocean`. Always create the `digitalocean_domain` resource before any `digitalocean_record` resource that references it.

```hcl
resource "digitalocean_domain" "main" {
  name = "example.com"
}

# Apex MUST be A or AAAA — never CNAME (no flattening on DO)
resource "digitalocean_record" "apex" {
  domain = digitalocean_domain.main.id
  type   = "A"
  name   = "@"
  value  = "192.168.0.11"
}

# CNAME value requires a trailing dot
resource "digitalocean_record" "api" {
  domain = digitalocean_domain.main.id
  type   = "CNAME"
  name   = "api"
  value  = "www.example.com."
}

resource "digitalocean_record" "mx" {
  domain   = digitalocean_domain.main.id
  type     = "MX"
  name     = "@"
  priority = 10
  value    = "mail.example.com."
}
```

**Bundled validator** — `scripts/do_dns_tf_lint.py` is a stdlib-only Python 3 linter. Run it against any `.tf` file that defines DigitalOcean DNS records:

```bash
python3 scripts/do_dns_tf_lint.py path/to/dns.tf
python3 scripts/do_dns_tf_lint.py path/to/dns.tf --format json
```

It enforces two rules and deliberately omits a TTL check (the provider accepts `ttl >= 0`):

- `apex-cname` **(error)** — a `digitalocean_record` with `type = "CNAME"` and `name = "@"`. This would be rejected by the API at apply time and is always wrong.
- `relative-fqdn-value` **(warning)** — a CNAME or MX `value` that contains no trailing dot. The record will double the domain at query time.

The validator is a heuristic line/brace scanner, not a full HCL parser (stdlib has none). Interpolated values such as `value = local.endpoint` and `dynamic` blocks are skipped rather than guessed — the linter silently passes on them rather than emitting a false positive.

## DNS-01 ACME / wildcard certs

A wildcard cert (`*.example.com`) requires a DNS-01 challenge. With the zone on DigitalOcean DNS, the two common approaches are:

**cert-manager (Kubernetes).** Use cert-manager's DigitalOcean DNS solver. Create a Kubernetes `Secret` holding the DO token, then reference it in the `Issuer` or `ClusterIssuer`:

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
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
          digitalocean:
            tokenSecretRef:
              name: digitalocean-dns
              key: access-token
        selector:
          dnsZones:
            - example.com
```

**lego / acme.sh.** Set `DO_AUTH_TOKEN` (lego) or `DO_API_KEY` (acme.sh) in the environment. Both read the account-wide token.

Reiterate cross-cutting rule 1: the token is account-wide — store it as a Kubernetes Secret or CI secret vault entry, never inline it in manifests or scripts.

For the general mechanics of DNS-01 challenges (how the `_acme-challenge` TXT record works, propagation timing, CNAME delegation patterns), see the `cloudflare-dns-zones` skill's "DNS-01 with cert-manager" section — the protocol is identical; only the solver config changes.

## Nameserver delegation and migration

DigitalOcean's authoritative nameservers are:

```
ns1.digitalocean.com
ns2.digitalocean.com
ns3.digitalocean.com
```

These are set at the registrar — not inside the DigitalOcean control panel. DigitalOcean does not host the parent zone; it only serves records for zones you have created in your account.

**Migration friction — no zone file export.** DigitalOcean has no BIND zone-file export endpoint (unlike Cloudflare, which has `GET /zones/{id}/dns_records/export`). Migrating a zone to or from DigitalOcean is record-by-record. The practical export path:

```bash
# Dump all records for the zone in tabular form
doctl compute domain records list example.com \
  --format ID,Name,Type,Data,Priority,TTL --no-header
```

Pipe this through `awk` or `jq` (when using the raw API) to build import payloads for the destination. Flag this as real friction in any migration plan — expect 20-30 minutes of manual work per zone, plus a validation pass after nameserver cutover.

**Cutover order:**

1. Create the domain in DigitalOcean and import all records.
2. Verify records are correct with `dig @ns1.digitalocean.com example.com <type>` before changing the registrar.
3. Lower TTLs on current authoritative nameservers (if still controlled), wait one TTL period.
4. Update NS records at the registrar to point to DigitalOcean.
5. Monitor propagation with `dig +trace example.com NS`.

## Anti-patterns checklist

- Apex CNAME in Terraform or doctl — the API rejects it; use A/AAAA at apex instead.
- CNAME or MX `value` without a trailing dot — produces doubled FQDN at resolution time.
- Echoing `DIGITALOCEAN_ACCESS_TOKEN` in CI logs or shell scripts — account-wide blast radius.
- Adding records before `doctl compute domain create` — the API returns 404 for the parent.
- Hard-coding record IDs — IDs change when records are recreated; always look up by name+type.
- Assuming DO has BIND export — it doesn't; plan migration as record-by-record.
- Asserting `ttl >= 30` in validators — the provider permits any non-negative value today.

## Cross-skill notes

For Cloudflare DNS (CNAME flattening, proxied records, BIND import/export, batch operations), see the `cloudflare-dns-zones` skill. For identifying which S3-compatible provider a bucket belongs to — including DigitalOcean Spaces vs AWS S3 vs Cloudflare R2 — see the `cloud-storage-identification` skill.
