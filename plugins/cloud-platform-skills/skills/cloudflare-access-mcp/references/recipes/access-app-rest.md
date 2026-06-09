# Path B — Self-hosted Access app + Managed OAuth (REST)

> Parent: `../../SKILL.md`. Per-hostname OAuth on your own origin. Same resources as Path C (Terraform), driven by curl. Pick this over Path C when you don't already have Cloudflare IaC.

## Table of contents

1. [What you're building](#what-youre-building)
2. [Prereqs](#prereqs)
3. [Step 1 — (Optional) Create the cloudflared tunnel](#step-1--optional-create-the-cloudflared-tunnel)
4. [Step 2 — Configure tunnel ingress + DNS](#step-2--configure-tunnel-ingress--dns)
5. [Step 3 — Create the Access policy](#step-3--create-the-access-policy)
6. [Step 4 — Create the Access self-hosted app with Managed OAuth](#step-4--create-the-access-self-hosted-app-with-managed-oauth)
7. [Step 5 — Run cloudflared on the origin host](#step-5--run-cloudflared-on-the-origin-host)
8. [Step 6 — Verify](#step-6--verify)
9. [Adding a second MCP server](#adding-a-second-mcp-server)
10. [Gotchas specific to Path B](#gotchas-specific-to-path-b)
11. [Useful endpoints](#useful-endpoints)

## What you're building

For each MCP server: a public hostname (`mcp.example.com`) that:

- Authenticates users via your IdP.
- Publishes MCP-spec OAuth metadata at `/.well-known/oauth-authorization-server` so MCP clients OAuth natively against the hostname (no `mcp-remote` shim).
- Reaches a private origin over a shared cloudflared tunnel.

Origin runs anywhere: ECS, Fly, Cloud Run, a VM, a k8s pod. It contains zero auth code.

## Prereqs

- IdP created (see `idp-setup.md`).
- Cloudflare API token: `Account: Cloudflare Tunnel: Edit`, `Account: Access: Apps and Policies: Edit`, `Zone: DNS: Edit`, `Zone: Zone: Read`.
- Each MCP server is reachable on the tunnel's network at a known address (`http://my-mcp:3100`).
- Public FQDN(s) chosen, in your Cloudflare-managed zone.

## Step 1 — (Optional) Create the cloudflared tunnel

Skip if your origin is already publicly reachable on its hostname. You still want the Access app step (4); you'd point its `domain` at your existing hostname and skip the tunnel.

```bash
export AID=<account_id>
export ZID=<zone_id>
export CF_TOKEN=<api_token>

# Generate a 32-byte tunnel secret (stable base64, no padding fiddling).
export TUNNEL_SECRET=$(openssl rand -base64 32)

curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/cfd_tunnel" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "mcp-shared",
  "config_src": "cloudflare",
  "tunnel_secret": "$TUNNEL_SECRET"
}
JSON
```

Save `result.id` as `TUNNEL_ID` and `result.token` (used by `cloudflared` in step 5).

## Step 2 — Configure tunnel ingress + DNS

For **all** MCP servers sharing this tunnel, send the full ingress list — Cloudflare replaces the config, it doesn't merge.

```bash
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$AID/cfd_tunnel/$TUNNEL_ID/configurations" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "config": {
    "ingress": [
      { "hostname": "notion-mcp.example.com", "service": "http://notion-mcp:3100" },
      { "hostname": "github-mcp.example.com", "service": "http://github-mcp:3100" },
      { "service": "http_status:404" }
    ]
  }
}
JSON
```

Catch-all `http_status:404` MUST be last (SKILL.md Hard rule 3).

Then a proxied CNAME per hostname — `name=<sub>`, `content=$TUNNEL_ID.cfargotunnel.com`, `type=CNAME`, `proxied=true`, `ttl=1`. POST to `/zones/$ZID/dns_records`; see the `cloudflare-dns-zones` skill for record CRUD semantics, batch creation, and idempotency. Per SKILL.md Hard rule 5: one owner per `(name, type)` — don't POST if another tool already manages the record.

## Step 3 — Create the Access policy

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/access/policies" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "notion-mcp-users",
  "decision": "allow",
  "session_duration": "24h",
  "include": [{ "email_domain": { "domain": "example.com" } }],
  "require": [{ "login_method": { "id": "<idp_id>" } }]
}
JSON
```

`session_duration` must match step 4's app value (SKILL.md Hard rule 1). `require { login_method }` is mandatory with email-based `include` (Hard rule 2). Save `result.id` as `POLICY_ID`.

## Step 4 — Create the Access self-hosted app with Managed OAuth

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/access/apps" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "notion-mcp",
  "type": "self_hosted",
  "domain": "notion-mcp.example.com",
  "session_duration": "24h",
  "auto_redirect_to_identity": true,
  "allowed_idps": ["<idp_id>"],
  "policies": ["$POLICY_ID"],
  "oauth_configuration": {
    "enabled": true,
    "dynamic_client_registration": {
      "enabled": true,
      "allow_any_on_localhost": true,
      "allow_any_on_loopback": true,
      "allowed_uris": []
    }
  }
}
JSON
```

Every field in `oauth_configuration` matters:

- `enabled: true` — publishes `/.well-known/oauth-authorization-server`, `/authorize`, `/token`. Without it, MCP clients don't discover OAuth.
- `dynamic_client_registration.enabled: true` — Claude clients register dynamically; no static UI exists.
- `allow_any_on_localhost` and `allow_any_on_loopback: true` — Claude Desktop's callback is `http://127.0.0.1:<random-port>/callback`. Can't enumerate.

## Step 5 — Run cloudflared on the origin host

Use the `token` from step 1. As a Docker container, an ECS sidecar, or a systemd service:

```bash
docker run -d --restart unless-stopped --network host \
  --name cloudflared cloudflare/cloudflared:latest \
  tunnel --no-autoupdate run --token "$TUNNEL_TOKEN"
```

cloudflared registers with Cloudflare and proxies the hostnames you declared in step 2 to the upstream `service` URLs.

## Step 6 — Verify

```bash
# OAuth metadata is published
curl -s "https://notion-mcp.example.com/.well-known/oauth-authorization-server" | jq .
# Expect: issuer, authorization_endpoint, token_endpoint, registration_endpoint.

# Unauthenticated GET on /health goes to Access login, NOT 200 from origin
curl -i "https://notion-mcp.example.com/health" | head -1
# Expect: HTTP/2 302 or HTTP/2 200 with Access HTML.

# Browse in incognito → IdP login → Access success
# Add to Claude Desktop → OAuth flow → tools listed
```

## Adding a second MCP server

For the same tunnel:

1. Re-`PUT` the tunnel configuration with the new hostname appended (catch-all still last).
2. Create a new DNS CNAME for the new hostname.
3. Create a new policy (or reuse an existing one).
4. Create a new Access self-hosted app with the new `domain` and the same `oauth_configuration` block.

That's it — no new tunnel, no new IdP.

## Gotchas specific to Path B

See SKILL.md "Hard rules" — rules 1–5 all apply to this flow. REST-only additions:

- **No drift detection.** If someone edits the Access app in the dashboard, your scripts won't notice. If drift matters, move to Path C (Terraform).
- **API token leakage.** The CF token has broad scope; treat it like a root-equivalent secret. Don't commit it; rotate periodically.

## Useful endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/accounts/{aid}/cfd_tunnel` | `POST`, `GET`, `DELETE` | Tunnel CRUD |
| `/accounts/{aid}/cfd_tunnel/{tid}/configurations` | `PUT`, `GET` | Ingress config (replace, not merge) |
| `/accounts/{aid}/access/apps` | `POST`, `GET`, `PUT`, `DELETE` | Access app CRUD |
| `/accounts/{aid}/access/policies` | `POST`, `GET`, `PUT`, `DELETE` | Reusable policies |
| `/zones/{zid}/dns_records` | `POST`, `GET`, `PUT`, `DELETE` | DNS records |

Tear-down: delete the app → delete the DNS record → re-PUT tunnel config without the hostname (or delete the tunnel) → delete policy if unused → stop `cloudflared`.
