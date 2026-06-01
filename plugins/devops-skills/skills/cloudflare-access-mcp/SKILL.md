---
name: cloudflare-access-mcp
description: Adds OAuth/SSO to a remote MCP server using Cloudflare. Three paths — AI Controls MCP Portal (REST, fastest), self-hosted Access app with Managed OAuth (REST), and the same as Terraform (when IaC already exists) — with a decision matrix, REST recipes per path, Terraform templates for the IaC path, and a stdlib validator that lints a `terraform show -json` plan. Use when the user asks to put an MCP server behind Cloudflare, add OAuth/SSO to a remote MCP server, expose a private MCP server via Cloudflare Tunnel, register MCP servers with the AI Controls portal, enable Managed OAuth or DCR on an Access app, or wire Claude Desktop / claude.ai web / Claude Code to an internal MCP server.
---

# Cloudflare → MCP server OAuth/SSO

Three real paths, pick by use case.

## Decision matrix

| Path | When | Effort | Limit |
| --- | --- | --- | --- |
| **A — AI Controls MCP Portal** (REST) | You have one or more MCP URLs that already exist (public Workers MCPs, vendor MCPs). You want one portal URL fronting them with OAuth, plus per-tool/per-prompt toggles and Access logging. | 1 IdP step + 1 POST per server + 1 dashboard step | `auth_type` is `oauth` / `bearer` / `unauthenticated` only — **no Cloudflare Access service-token (dual-header) auth to upstream**. Portal composition is dashboard-driven; only server registration is REST today. |
| **B — Self-hosted Access app + Managed OAuth** (REST) | Per-hostname OAuth on your own origin. No portal hop. Origin can be private (behind cloudflared tunnel) or any public URL. Native MCP-spec OAuth (DCR, /authorize, /token, .well-known/*) at your hostname. | ~6 API calls per server (+ tunnel if private) | More moving parts. Five known footguns — see Hard rules. |
| **C — Same as B, in Terraform/OpenTofu** | You already have IaC for Cloudflare. Multi-environment parity matters. PR-reviewed access changes. | Heaviest setup, smallest per-server delta | Wrong for one-offs. If a project has no Cloudflare IaC yet, pick A or B. |

The `cf` CLI also exposes these endpoints — preview only; the REST recipes here are the stable contract. See the `cloudflare-cf-cli` skill for `cf` posture.

## When to invoke

- "Put my MCP server behind Cloudflare", "add OAuth to my MCP server", "expose private MCP server", "MCP portal", "AI Controls MCP", "MCP Managed OAuth", "MCP DCR".
- Adding another MCP server to an existing setup (Path A: another POST; Path B/C: another API call / tfvars entry).
- Choosing between paths — present the matrix above.

## Inputs to collect first

| Input | Why | Example |
| --- | --- | --- |
| Cloudflare account ID + zone ID | Account- and zone-scoped resources | from dash sidebar |
| MCP URL(s) | Path A: the upstream `hostname`. Path B/C: the public FQDN you'll publish | `https://mcp.github.com/mcp` (A) or `notion-mcp.example.com` (B/C) |
| Path B/C: origin reachability from cloudflared | Tunnel ingress `service` (only if origin is private) | `http://notion-mcp:3100` |
| IdP + client id + secret location | Cloudflare needs an Identity Provider before any path works | Google; AWS SM `cf/google-oauth` |
| Zero Trust team domain | `<team>.cloudflareaccess.com`; permanent; dashboard | `mycorp` |
| Allowlist | Who can sign in | `["example.com"]` |
| Session duration | Edge auth cache TTL | `"24h"` |

API token scopes per path: `Account: Access: Apps and Policies: Edit` (all); `Account: Access: AI Controls: Edit` (A); `Account: Cloudflare Tunnel: Edit` + `Zone: DNS: Edit` (B/C with tunnel); `Zone: Zone: Read` (all).

## Setup that's not Terraformable / scriptable

Walk the user through these once per account before any path. Detailed steps in `references/recipes/idp-setup.md`:

1. Pick a Zero Trust team domain in the dashboard. Permanent.
2. Create the IdP OAuth client (e.g. Google Cloud Console) with the exact Access callback URI.
3. Store the IdP client secret in your secret store.
4. Create the IdP resource in Cloudflare via the API.

## Path recipes

- **Path A** — `references/recipes/ai-controls-portal.md`. POST per server to `/accounts/{aid}/access/ai-controls/mcp/servers` with `auth_type`, finish OAuth admin handshake in dashboard, attach Access policy, assemble portal in dashboard.
- **Path B** — `references/recipes/access-app-rest.md`. Optional tunnel, ingress + DNS, Access policy, then `POST /accounts/{aid}/access/apps` with the Managed OAuth block (below).
- **Path C** — `references/templates/{google_idp,tunnel,main,variables}.tf`. Same six resources as Path B; uses `for_each` over a `mcp_apps` list so adding a server is one tfvars entry. Run the validator before `apply`.

### The Managed OAuth block (B and C)

```json
"oauth_configuration": {
  "enabled": true,
  "dynamic_client_registration": {
    "enabled": true,
    "allow_any_on_localhost": true,
    "allow_any_on_loopback": true,
    "allowed_uris": []
  }
}
```

Without `enabled`, MCP clients can't discover OAuth (they fall back to `mcp-remote` or refuse). Without DCR enabled + the two loopback flags, Claude Desktop's `http://127.0.0.1:<random>/callback` is rejected.

## Hard rules

`scripts/validate_access_mcp.py` flags rules 1–5 on Path C plans; for Paths A and B, the same logic applies by hand.

1. **Policy `session_duration` overrides the app's.** *(A, B, C — A's portal policy too.)* Omit it on the policy → effective session is the 24h policy default regardless of the app value. Silent; no plan warning. Always set both.
2. **`require { login_method }` mandatory on any policy that `include`s by email/email_domain.** *(A, B, C.)* Otherwise Cloudflare's one-time-PIN identity satisfies the allowlist and bypasses your IdP. `allowed_idps` + `auto_redirect_to_identity` affect UX, not policy evaluation.
3. **Catch-all `{ service: "http_status:404" }` must be the LAST tunnel ingress entry.** *(B, C only.)*
4. **One shared tunnel for all MCP origins.** *(B, C only.)* Don't provision one per server.
5. **One owner per DNS record.** *(B, C only.)* Cloudflare provider v5 upserts by `(name, type)`; two stacks managing the same hostname fight on every apply. See the `cloudflare-dns-zones` skill for record CRUD semantics.

Plus, **Path A only**: `oauth` upstreams need a one-time admin OAuth handshake in the dashboard after registration; per-tool/per-prompt toggles live on the portal, not the server (two portals fronting the same server can expose different subsets).

## Validator (Path C)

```bash
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan | python3 scripts/validate_access_mcp.py
# --format json for CI; non-zero exit on findings.
```

Scope: Terraform plans only. Paths A/B (REST) have no plan to lint — verify by hitting the live endpoints (see each recipe's verify section).

## IdP swap

The IdP resource is the only IdP-specific piece; everything downstream is identical. See the `Other IdPs` table in `references/recipes/idp-setup.md`.

## Proactive triggers

- User has 3+ MCP URLs to expose and is reaching for per-server Access apps → suggest Path A (portal) instead.
- User wants service-token (dual-header) auth on the upstream AND an AI Controls portal in front → incompatible; pick one.
- User starts writing Terraform for one MCP server in a project with no other Cloudflare IaC → suggest Path B REST instead.
- `oauth_configuration` missing on a Path B/C Access app intended for MCP → clients won't discover OAuth.
- User wires `mcp-remote` in client config for a Path B/C upstream with Managed OAuth enabled → unnecessary.
- User wants one cloudflared tunnel per MCP server → recommend shared tunnel + ingress `for_each`.

## Verification (per path)

**Path A:**
- [ ] `GET /accounts/$AID/access/ai-controls/mcp/servers` lists each server.
- [ ] Portal URL `https://<sub>.<domain>/mcp` redirects through the IdP and lists registered servers' tools to a connected MCP client.
- [ ] Removing a user from the portal's Access policy revokes access within `session_duration`.

**Paths B and C:**
- [ ] `curl -s https://<mcp-host>/.well-known/oauth-authorization-server | jq .` returns issuer + authorization_endpoint + token_endpoint + registration_endpoint.
- [ ] Fresh-incognito browse to `https://<mcp-host>/mcp` redirects through the IdP and lands at the Access success page.
- [ ] Claude Desktop "Add custom connector" finishes OAuth and shows tools.
- [ ] `curl https://<mcp-host>/health` without auth returns Access's HTML login page, **not** 200 from the origin.
- [ ] Path C only: `python3 scripts/validate_access_mcp.py` against the plan returns 0 findings.

## Old patterns

- `mcp-remote` as a client-side OAuth proxy — only needed when the upstream lacks OAuth metadata. With Path A portal OAuth or Path B/C Managed OAuth, drop it.
- One cloudflared tunnel per MCP server (B/C) — shared tunnel + ingress `for_each` is simpler.
- Cloudflare Access service tokens for human MCP flows — service tokens are machine-to-machine. For humans, use OAuth + IdP SSO.
- Starting with Path C "for future flexibility" without existing Cloudflare IaC — start REST; migrate to Terraform when there's a second environment to keep parity with.
