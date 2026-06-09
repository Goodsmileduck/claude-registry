# Google as the Cloudflare Zero Trust IdP. The OAuth client secret is
# pulled from AWS Secrets Manager at plan time. The secret value lands
# in Terraform state (same as any sensitive input) — encrypt your state.
#
# Swap this data source for your store of choice (GCP Secret Manager,
# Vault, 1Password, etc.). Swap `type` and `config` keys for a different
# IdP — see the SKILL.md "IdP swap" table.

data "aws_secretsmanager_secret_version" "google_oauth_client_secret" {
  secret_id = var.google_oauth_client_secret_name
}

resource "cloudflare_zero_trust_access_identity_provider" "google" {
  account_id = var.cloudflare_account_id
  name       = "${var.namespace}-${var.stage}-google"
  type       = "google"

  config = {
    client_id     = var.google_oauth_client_id
    client_secret = data.aws_secretsmanager_secret_version.google_oauth_client_secret.secret_string
  }
}
