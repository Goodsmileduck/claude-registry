# IdP setup (shared by all paths)

> Parent: `../../SKILL.md`. Create the Cloudflare Zero Trust Identity Provider once per account; reuse across MCP servers and portals.

## Prereqs

- Zero Trust team domain set in the dashboard.
- IdP OAuth client created at the IdP (e.g. Google Cloud Console) with redirect URI **exactly** `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`.
- Client secret stored as a plain string in your secret store.
- Cloudflare API token with `Account: Access: Apps and Policies: Edit`.

## Google (example)

```bash
export AID=<account_id>
export CF_TOKEN=<api_token>
export GOOGLE_CLIENT_ID=<from-gcp>
export GOOGLE_CLIENT_SECRET=<from-secret-store>

curl -X POST "https://api.cloudflare.com/client/v4/accounts/$AID/access/identity_providers" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "google",
  "type": "google",
  "config": {
    "client_id": "$GOOGLE_CLIENT_ID",
    "client_secret": "$GOOGLE_CLIENT_SECRET"
  }
}
JSON
```

Response includes `result.id` — save it. Every Path A portal policy and every Path B/C Access app policy needs that id in its `require { login_method }` block.

## Other IdPs

Swap `type` and `config`:

| IdP | `type` | extra `config` |
| --- | --- | --- |
| GitHub | `github` | (none beyond client_id/secret) |
| Okta | `okta` | `okta_account` (your okta tenant URL) |
| Azure AD | `azureAD` | `directory_id` |
| Generic OIDC | `oidc` | `auth_url`, `token_url`, `certs_url`, `scopes` |

Everything downstream — portal policies, Access app policies — is identical regardless of IdP.

## List and verify

```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/$AID/access/identity_providers" \
  -H "Authorization: Bearer $CF_TOKEN" | jq '.result[] | {id,name,type}'
```

## Sanity check

In an incognito browser, go to `https://<team>.cloudflareaccess.com/cdn-cgi/access/login/<idp-name>`. You should hit the IdP login screen. If you get a Cloudflare error page, the redirect URI on the IdP side doesn't match — fix that first; nothing downstream will work otherwise.
