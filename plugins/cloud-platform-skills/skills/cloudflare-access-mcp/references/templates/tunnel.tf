# Shared MCP tunnel. One cloudflared connector token covers every entry
# in var.mcp_apps; add another entry and it gets an ingress rule + DNS
# record without provisioning a second tunnel.

resource "random_password" "mcp_tunnel_secret" {
  length  = 32
  special = false
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "mcp" {
  account_id    = var.cloudflare_account_id
  name          = "${var.namespace}-${var.stage}-mcp"
  config_src    = "cloudflare"
  tunnel_secret = base64encode(random_password.mcp_tunnel_secret.result)
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "mcp" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.mcp.id

  config = {
    # Catch-all (no hostname/path) MUST be last — cloudflared rejects
    # the config otherwise.
    ingress = concat(
      [
        for app in var.mcp_apps : {
          hostname = app.hostname
          service  = app.local_service
        }
      ],
      [{ service = "http_status:404" }],
    )
  }
}

# If another Terraform stack already owns a CNAME for any of these
# hostnames, destroy it there first. Cloudflare provider v5 upserts
# records by (name, type) — two owners means each apply overwrites
# the other and you'll chase phantom drift for a day.
resource "cloudflare_dns_record" "mcp_tunnel" {
  for_each = local.mcp_apps_by_id

  zone_id = var.cloudflare_zone_id
  name    = trimsuffix(replace(each.value.hostname, ".${var.zone_name}", ""), ".")
  type    = "CNAME"
  proxied = true
  content = "${cloudflare_zero_trust_tunnel_cloudflared.mcp.id}.cfargotunnel.com"
  ttl     = 1
}

locals {
  mcp_apps_by_id = { for app in var.mcp_apps : app.id => app }
}

output "mcp_tunnel_token" {
  value       = cloudflare_zero_trust_tunnel_cloudflared.mcp.tunnel_token
  description = "Pass to `cloudflared tunnel run --token <value>` on the origin host."
  sensitive   = true
}
