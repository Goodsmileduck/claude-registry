# Cloudflare Zero Trust Access apps fronting MCP servers.
# Managed OAuth on the Access app makes Cloudflare serve the MCP-spec
# OAuth endpoints (DCR, /authorize, /token, .well-known/oauth-...) at
# the app's hostname, so MCP clients can OAuth natively against the
# upstream without an mcp-remote shim.

resource "cloudflare_zero_trust_access_application" "mcp" {
  for_each = local.mcp_apps_by_id

  zone_id                   = var.cloudflare_zone_id
  name                      = "${var.namespace}-${var.stage}-${each.value.id}"
  domain                    = each.value.hostname
  type                      = "self_hosted"
  session_duration          = var.session_duration
  auto_redirect_to_identity = true
  allowed_idps              = [cloudflare_zero_trust_access_identity_provider.google.id]

  # Set managed_oauth = null on a tfvars entry if that server should be
  # browser-only (no MCP-spec OAuth surface).
  oauth_configuration = each.value.managed_oauth == null ? null : {
    enabled = each.value.managed_oauth.enabled
    dynamic_client_registration = {
      enabled                = each.value.managed_oauth.dcr_enabled
      allow_any_on_localhost = each.value.managed_oauth.allow_any_on_localhost
      allow_any_on_loopback  = each.value.managed_oauth.allow_any_on_loopback
      allowed_uris           = each.value.managed_oauth.allowed_redirect_uris
    }
  }

  policies = [
    {
      id         = cloudflare_zero_trust_access_policy.mcp_users[each.key].id
      precedence = 1
    },
  ]
}

resource "cloudflare_zero_trust_access_policy" "mcp_users" {
  for_each = local.mcp_apps_by_id

  account_id = var.cloudflare_account_id
  name       = "${var.namespace}-${var.stage}-${each.value.id}-users"
  decision   = "allow"

  # Policy-level session_duration OVERRIDES the app's. If they differ,
  # the effective session is the policy's (or the 24h policy default
  # if omitted). Always set both to the same value.
  session_duration = var.session_duration

  include = concat(
    [
      for domain in coalesce(each.value.allowlist.email_domains, []) : {
        email_domain = { domain = domain }
      }
    ],
    [
      for email in coalesce(each.value.allowlist.emails, []) : {
        email = { email = email }
      }
    ],
  )

  # Without this require block, Cloudflare's one-time-PIN identity
  # matching an allowlisted email satisfies the policy and the user
  # never hits the IdP. Force it.
  require = [
    {
      login_method = {
        id = cloudflare_zero_trust_access_identity_provider.google.id
      }
    },
  ]
}
