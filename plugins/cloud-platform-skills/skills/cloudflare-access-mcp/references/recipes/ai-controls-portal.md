# Path A — AI Controls MCP Portal (REST)

> Parent: `../../SKILL.md`. Fastest path. Best when you have N existing MCP URLs (Workers MCPs, vendor MCPs, your own public origins) and want one portal URL fronting them with OAuth + per-tool toggles.

## Table of contents

1. [What you're building](#what-youre-building)
2. [Prereqs](#prereqs)
3. [Step 1 — Register each MCP server](#step-1--register-each-mcp-server)
4. [Step 2 — Bootstrap OAuth admin credentials (per server)](#step-2--bootstrap-oauth-admin-credentials-per-server)
5. [Step 3 — Create the Access policy for the portal](#step-3--create-the-access-policy-for-the-portal)
6. [Step 4 — Assemble the portal in the dashboard](#step-4--assemble-the-portal-in-the-dashboard)
7. [Step 5 — Verify](#step-5--verify)
8. [Gotchas specific to Path A](#gotchas-specific-to-path-a)
9. [Useful endpoints](#useful-endpoints)

## What you're building

A single portal URL — `https://<subdomain>.<your-domain>/mcp` — that:

- Authenticates users via your IdP (Google / Okta / etc.).
- Fans out to N registered upstream MCP servers.
- Logs prompts/responses in Access.
- Lets you toggle which tools/prompts each server exposes through the portal.

## Prereqs

- IdP created (see `idp-setup.md`).
- Cloudflare API token with `Account: Access: Apps and Policies: Edit` and `Account: Access: AI Controls: Edit`.
- For each MCP server: its public URL ending in `/mcp` (or whatever path it speaks Streamable HTTP on).
- A zone in your Cloudflare account whose subdomain will host the portal URL.

## Step 1 — Register each MCP server

```bash
export AID=<account_id>
export CF_TOKEN=<api_token>

curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/access/ai-controls/mcp/servers" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "name": "GitHub MCP",
  "hostname": "https://mcp.github.com/mcp",
  "auth_type": "oauth"
}
JSON
```

`auth_type` values:

| Value | Use when |
| --- | --- |
| `oauth` | Upstream MCP server speaks OAuth (most public/vendor MCPs do). After creation, finish admin auth in the dashboard (step 2). |
| `bearer` | Upstream uses a static token. Add `"auth_credentials": "<token>"` in the body. |
| `unauthenticated` | Trust the network path; no upstream auth. Rare; only for fully internal servers reachable only through this portal. |

Repeat for every MCP server you want in the portal. Save each `result.id` — you'll attach them to the portal in step 4.

## Step 2 — Bootstrap OAuth admin credentials (per server)

For `auth_type:oauth` servers, Cloudflare needs to establish **admin credentials** to talk to the upstream OAuth-protected MCP server. This step is currently dashboard-only:

1. Zero Trust → Access controls → AI controls → MCP servers.
2. Click each `oauth` server, hit **Authenticate**, complete the OAuth flow as an admin user.
3. The server's status flips to "Authenticated".

Until you do this for an `oauth` server, the portal will surface it but client calls through it will fail.

## Step 3 — Create the Access policy for the portal

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/access/policies" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "mcp-portal-users",
  "decision": "allow",
  "session_duration": "24h",
  "include": [
    { "email_domain": { "domain": "example.com" } }
  ],
  "require": [
    { "login_method": { "id": "<your-idp-id>" } }
  ]
}
JSON
```

`require { login_method }` is non-negotiable — without it, Cloudflare's one-time-PIN identity satisfies the email allowlist and bypasses your IdP. (Same gotcha as Path B; portal policies are not exempt.)

Save `result.id` — you'll attach it to the portal in step 4.

## Step 4 — Assemble the portal in the dashboard

Portal composition isn't fully exposed in REST yet. Currently:

1. Zero Trust → Access controls → AI controls → **Add MCP server portal**.
2. Name the portal, pick a custom domain + subdomain (`<sub>.<your-zone>`).
3. Attach each MCP server you registered in step 1.
4. (Optional) Configure per-tool / per-prompt toggles.
5. (Optional) **Require user auth**:
   - **Enabled** (default): each user OAuths into each upstream MCP server with their own credentials.
   - **Disabled**: connected users hit the upstream via the admin credentials from step 2 (shared identity).
6. Attach the Access policy from step 3.

After saving, the portal URL is `https://<subdomain>.<domain>/mcp` — give that to MCP clients.

## Step 5 — Verify

```bash
# List your portals
curl -s "https://api.cloudflare.com/client/v4/accounts/$AID/access/ai-controls/mcp/portals" \
  -H "Authorization: Bearer $CF_TOKEN" | jq '.result[] | {id,name,hostname}'

# List registered servers
curl -s "https://api.cloudflare.com/client/v4/accounts/$AID/access/ai-controls/mcp/servers" \
  -H "Authorization: Bearer $CF_TOKEN" | jq '.result[] | {id,name,hostname,auth_type,status}'

# Browse the portal URL in incognito — should redirect through your IdP.
# Add to Claude Desktop (Settings → Connectors → Add custom) — should OAuth and list tools.
```

## Gotchas specific to Path A

See SKILL.md "Hard rules" for the cross-path footguns (rules 1, 2 apply to Path A). Path-A-specific:

- **No service-token upstream auth.** If the upstream MCP server sits behind another Access app expecting `CF-Access-Client-Id` + `CF-Access-Client-Secret` dual headers, Path A can't reach it. Either drop the upstream requirement, or skip the portal.
- **Portal composition is dashboard-only today.** If your team rejects click-ops for production, that's a reason to pick Path B/C.
- **Per-tool toggles live on the portal, not the server.** Two portals using the same registered server can expose different tool subsets — useful for staging vs prod.

## Useful endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/accounts/{aid}/access/ai-controls/mcp/servers` | `POST`, `GET`, `PUT`, `DELETE` | CRUD on registered MCP servers |
| `/accounts/{aid}/access/ai-controls/mcp/portals` | `GET` | List portals (composition currently dashboard) |
| `/accounts/{aid}/access/identity_providers` | `GET`, `POST` | IdP CRUD (shared with Path B/C) |
| `/accounts/{aid}/access/policies` | `POST`, `GET` | Reusable policies (attach to portals/apps) |

Tear-down: `DELETE` the portal in the dashboard, then `DELETE` each server, then optionally `DELETE` the policy and IdP if no other app uses them.
