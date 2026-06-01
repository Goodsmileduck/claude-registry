# Design: `digitalocean-skills` plugin → `digitalocean-dns-zones`

Date: 2026-06-01
Status: Approved for planning

## Goal

Create a new `digitalocean-skills` plugin in the claude-registry and ship its
first skill, `digitalocean-dns-zones`. The skill must encode the **non-obvious
DigitalOcean DNS traps** — not generic `doctl` CRUD that Claude already knows.
The bar (per repo CLAUDE.md): "Only add context Claude doesn't already have."

Most of the value targets users migrating from or running alongside Cloudflare
(the user's actual stack), so the skill is structured to mirror the existing
`cloudflare-dns-zones` skill for consistency.

## Scope

In scope:

- New plugin directory `plugins/digitalocean-skills/` with
  `.claude-plugin/plugin.json`.
- One skill: `digitalocean-dns-zones`.
- A stdlib Python validator for the machine-checkable subset.
- At least three eval scenarios, written before the full SKILL body.

Out of scope (for this iteration):

- DigitalOcean Spaces skill (explicitly deferred — "DNS only first").
- Moving or modifying the existing standalone `do-registry-cleanup` plugin.
- DOKS, Droplets, managed Databases, App Platform.

## Plugin metadata

`plugins/digitalocean-skills/.claude-plugin/plugin.json` per the registry
template (third-person description, full author/homepage/repository/license/
keywords). The `description` and `keywords` must cover every skill the plugin
ships — currently just DNS, but written so future DO skills extend cleanly. Per
CLAUDE.md, re-read and update both fields whenever a skill is later added.

## Skill: `digitalocean-dns-zones`

### Frontmatter

- `name: digitalocean-dns-zones` — `<topic>-<role>` shape, consistent with
  `cloudflare-dns-zones`. Lowercase-hyphens, no `claude`/`anthropic`.
- `description`: function-first, then triggers. Includes real keywords a user
  would type: `doctl`, `DIGITALOCEAN_ACCESS_TOKEN`, `api.digitalocean.com`,
  `digitalocean_record`, apex CNAME, DNS-01, wildcard cert, nameserver
  delegation, Cloudflare→DO migration.

### Cross-cutting rules (lead the body)

1. **Token is account-wide.** `DIGITALOCEAN_ACCESS_TOKEN` cannot be scoped to a
   single zone the way a Cloudflare API Token can. Treat it as a high-value
   secret; do not paste into shared shells/CI logs. (This is the inverse of the
   CF skill's "scope tokens to the minimum" rule — call out the contrast.)
2. **Domain before records.** A domain must exist (`doctl compute domain
   create`) before any record can be added. The apex record uses name `@`.
3. **No apex CNAME, no flattening.** DigitalOcean has no ALIAS/ANAME and no
   CNAME flattening. A bare apex must be `A`/`AAAA`. This is the #1 trap when
   migrating a zone from Cloudflare (which flattens apex CNAMEs transparently).
4. **List-then-act for idempotency.** Like CF, there is no upsert-by-name.
   Look the record up by name+type, then create or update by ID.
5. **CNAME/MX values are FQDNs with a trailing dot.** `value = "mail.example.com."`
   — without the trailing dot the DO provider treats the value as relative and
   appends the domain. (Verified against the provider docs, which show every
   CNAME/MX value dotted. Note: `ttl` is *not* floored at 30 — the provider
   documents `ttl >= 0`, default 1800; do not assert a 30s minimum.)

### Routed content

- **Auth & access:** `doctl auth init` vs raw `api.digitalocean.com/v2/domains`;
  when to use each. doctl rate-limit behavior.
- **Record CRUD:** `doctl compute domain records list/create/update/delete`;
  relative vs FQDN names; CNAME values need a trailing dot.
- **Mail records:** MX/SPF/DKIM/DMARC TXT long-string semantics (cite CF skill's
  treatment rather than duplicating; note DO-specific differences only).
- **DNS-01 ACME / wildcard certs:** cert-manager DigitalOcean webhook and lego
  DO provider; `DIGITALOCEAN_TOKEN` env; parallels the CF DNS-01 section.
- **Nameserver delegation & migration:** set `ns1/ns2/ns3.digitalocean.com` at
  the registrar; DO has no BIND zone-file export endpoint — migration to/from
  Cloudflare is record-by-record via API (real friction to flag).
- **Terraform:** `digitalocean_domain` + `digitalocean_record`; apex `name="@"`,
  trailing-dot on CNAME `value`, explicit `ttl >= 30`. Shown alongside doctl,
  not as a separate skill.

### Validator: `scripts/do_dns_tf_lint.py`

Python 3 stdlib only. `--help`, exit 0 on pass / non-zero on findings,
`--format json`. Parses `digitalocean_record` blocks from Terraform `.tf` input
(regex/line scan — no HCL lib available in stdlib; documented as a heuristic
linter, not a full HCL parser) and flags:

- CNAME at apex (error): a block with `type = "CNAME"` and `name = "@"` — DO has
  no apex CNAME/flattening; the zone will break.
- CNAME/MX `value` missing a trailing dot (warning): relative-vs-FQDN ambiguity
  in the DO provider.

Dropped the earlier `ttl < 30` check: the provider documents `ttl >= 0`
(default 1800), so a sub-30 TTL is not a provider-level error and flagging it
would be a false positive. Every remaining rule is justified by an inline
comment citing the provider docs. No voodoo constants.

### Evals (written first)

At least three `evals/*.json` in `{query, files, expected_behavior}` shape:

1. **Apex migration from Cloudflare** — query about pointing a bare domain that
   was a flattened CNAME on Cloudflare → expected: skill warns DO has no apex
   CNAME/flattening, recommends A/AAAA.
2. **Wildcard cert via DO DNS-01** — query about issuing `*.example.com` →
   expected: skill routes to the DNS-01 / cert-manager DO webhook path with
   `DIGITALOCEAN_TOKEN`.
3. **TF CNAME without trailing dot** — a `digitalocean_record` CNAME whose value
   lacks the trailing dot → expected: validator flags it.

## Authoring constraints

- Verify current `doctl`, DigitalOcean API v2, and `digitalocean` Terraform
  provider syntax via Context7 before writing CLI/API specifics (CLAUDE.md).
- SKILL.md body under 500 lines; split to `references/` only if it approaches
  the limit. Single-topic skill — likely no references needed.
- No wikilinks, no Windows paths, no time-sensitive instructions.
- Cross-skill mention of `cloudflare-dns-zones` in plain prose, not wikilink.

## Self-check before commit

The registry CLAUDE.md self-check list applies: frontmatter shape, body length,
references depth/TOC, no wikilinks/Windows paths, evals present, validator
constraints. Run `python3 -c "import json; json.load(...)"` on the new
`plugin.json` to confirm it parses.
