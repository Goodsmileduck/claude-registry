variable "namespace" {
  type        = string
  description = "Short project/org slug used as a name prefix on Cloudflare resources."
}

variable "stage" {
  type        = string
  description = "Environment slug (staging, prod). Used in resource names so multiple stages can coexist in one Cloudflare account."
}

variable "cloudflare_zone_id" {
  type = string
}

variable "cloudflare_account_id" {
  type        = string
  description = "Account ID. Required for account-scoped resources (IdP, tunnel, policy)."
}

variable "zone_name" {
  type        = string
  description = "Apex domain for the zone (e.g. example.com). Used to compute relative DNS record names."
}

variable "session_duration" {
  type        = string
  default     = "24h"
  description = "How long a successful Access auth is cached at the Cloudflare edge. Must match the policy session_duration (the policy overrides the app's value)."
}

variable "cloudflare_team_domain" {
  type        = string
  description = "Cf Zero Trust team domain (the <team> in <team>.cloudflareaccess.com). Set once in the dashboard; permanent."
}

variable "google_oauth_client_id" {
  type        = string
  description = "Google OAuth client ID. Redirect URI: https://<team>.cloudflareaccess.com/cdn-cgi/access/callback."
}

variable "google_oauth_client_secret_name" {
  type        = string
  description = "AWS Secrets Manager secret name (not ARN) holding the Google OAuth client secret as a plain string. Swap the data source in google_idp.tf if you use another store."
}

variable "mcp_apps" {
  type = list(object({
    id            = string                    # short slug, used in resource names
    hostname      = string                    # public FQDN; must be in the zone
    local_service = string                    # how cloudflared reaches origin
    allowlist = object({
      email_domains = optional(list(string), [])
      emails        = optional(list(string), [])
    })
    managed_oauth = optional(object({
      enabled                = optional(bool, true)
      dcr_enabled            = optional(bool, true)
      allow_any_on_localhost = optional(bool, true)   # required for Claude Desktop
      allow_any_on_loopback  = optional(bool, true)   # required for Claude Desktop
      allowed_redirect_uris  = optional(list(string), [])
    }), null)
  }))
  default     = []
  description = "MCP servers exposed via the shared tunnel + Access. Each entry produces a tunnel ingress rule, Access app, policy, and DNS CNAME. When managed_oauth is set, the Access app exposes MCP-spec OAuth endpoints (DCR, .well-known/oauth-authorization-server, etc.) so MCP clients can natively OAuth without an mcp-remote shim."
}
