---
name: cloud-storage-identification
description: Identifies which object-storage provider an S3-compatible target actually hits, from endpoint URLs, env vars, or Terraform provider blocks. Prevents AWS-default assumptions on GCS/DO Spaces/R2/Hetzner/B2/MinIO. Use when working with boto3, `aws_s3_bucket`, rclone, s3cmd, or S3-compatible storage.
---

# Cloud Object Storage — provider identification

Prevents the AWS-default trap: assuming any `aws_s3_bucket` resource or boto3 call is hitting AWS S3 when it's actually GCS, DO Spaces, Hetzner, R2, B2, MinIO, or Azure Blob.

## When to invoke

**Symptoms:**
- A script uses `boto3` or the AWS SDK but you don't see `import boto3` paired with explicit AWS credentials.
- A Terraform module references an `aws_s3_bucket` but the backend or provider block points elsewhere.
- A `rclone` config has `provider = Other` or a custom endpoint.
- The user says "the bucket" or "S3" without context.
- An error message mentions an endpoint that doesn't end in `amazonaws.com`.

**Failure mode this prevents:** assuming AWS S3 and recommending AWS-specific tooling (IAM policies, S3 Lifecycle rules, CloudWatch metrics, presigned URL signing v4 quirks) when the actual target is a different provider with different auth, different features, and different bugs.

## Step 1 — Identify the endpoint

Endpoint URL is the most reliable identifier. Look in: `endpoint_url=` kwarg, `AWS_ENDPOINT_URL` env, Terraform `endpoints` block, rclone config, `s3cmd` config, or the explicit URL in error messages.

| Endpoint pattern | Provider | Notes |
|---|---|---|
| `s3.<region>.amazonaws.com` / `<bucket>.s3.<region>.amazonaws.com` | **AWS S3** | Real deal. Region required in v4 signing. |
| `s3-accelerate.amazonaws.com` | AWS S3 Transfer Acceleration | Different perf profile, same API. |
| `storage.googleapis.com` | **Google Cloud Storage** | XML API is S3-ish but not full compat; native JSON API is preferred. |
| `<region>.digitaloceanspaces.com` | **DigitalOcean Spaces** | S3-compat. Missing some headers (e.g. `x-amz-tagging` partial). |
| `<endpoint>.your-objectstorage.com` (e.g. `nbg1.your-objectstorage.com`) | **Hetzner Object Storage** | Path-style only; virtual-hosted style not supported. |
| `<account>.r2.cloudflarestorage.com` | **Cloudflare R2** | No egress fees, requires `auto` as region. |
| `s3.<region>.backblazeb2.com` | **Backblaze B2** (S3-compat API) | Native B2 API also exists and is different. |
| `<account>.blob.core.windows.net` | **Azure Blob** | Not S3-compat — different SDK entirely. |
| `<minio-host>:9000` or self-hosted IP/hostname | **MinIO** or other self-hosted | Behavior varies by version. |

If endpoint is `None` / unset in code using boto3, the SDK defaults to AWS — confirm by looking at credentials (`AWS_PROFILE`, `~/.aws/credentials`).

## Step 2 — Confirm via credentials / env

| Env var present | Strong signal of |
|---|---|
| `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (no endpoint override) | AWS S3 |
| `AWS_*` + `AWS_ENDPOINT_URL` set | S3-compatible, *not* AWS |
| `GOOGLE_APPLICATION_CREDENTIALS` pointing to a service account JSON | GCS (native client) — if seen alongside boto3, suspect HMAC interop |
| `SPACES_ACCESS_KEY_ID` / `SPACES_SECRET_ACCESS_KEY` | DigitalOcean Spaces (DO's preferred names; some configs still use `AWS_*`) |
| `HCLOUD_TOKEN` (separate from object storage creds, but signals Hetzner stack) | Hetzner ecosystem |
| `R2_*` or `CF_*` + `AWS_ENDPOINT_URL` containing `r2.cloudflarestorage.com` | Cloudflare R2 |
| `B2_APPLICATION_KEY_ID` | Backblaze B2 |

## Step 3 — Confirm via Terraform provider block

If the context is Terraform/Terragrunt:

```hcl
# AWS S3 — standard
provider "aws" {
  region = "us-east-1"
}
resource "aws_s3_bucket" "x" { ... }

# DO Spaces — uses aws provider with endpoint override
provider "aws" {
  region                      = "us-east-1"   # required but ignored
  endpoints { s3 = "https://nyc3.digitaloceanspaces.com" }
  skip_credentials_validation = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
}

# GCS — separate provider entirely
provider "google" { project = "..." }
resource "google_storage_bucket" "x" { ... }

# Cloudflare R2 — aws provider + R2 endpoint, region = "auto"
provider "aws" {
  region = "auto"
  endpoints { s3 = "https://<acct>.r2.cloudflarestorage.com" }
}
```

The `aws_s3_bucket` resource type is a poor signal — it's used for AWS, DO Spaces, R2, and MinIO. The provider's `endpoints` block is the truth.

## Step 4 — Confirm via Terraform state backend

State backend is separate from workload storage and may use a different provider. Check `backend "s3"` or `terragrunt.hcl` `remote_state` block:

```hcl
remote_state {
  backend = "s3"
  config = {
    endpoint = "https://nyc3.digitaloceanspaces.com"  # → Spaces, not AWS
    bucket   = "tfstate"
    key      = "..."
    region   = "us-east-1"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    force_path_style            = true
  }
}
```

`backend = "s3"` with `endpoint = "..."` and `force_path_style = true` → S3-compatible, not AWS.

## Common misroutes (the "AWS-assumption" trap)

When the actual target is NOT AWS but Claude assumes it is, these recommendations are wrong:

| Wrong AWS-default recommendation | What to do instead |
|---|---|
| "Add an S3 Lifecycle policy" | Check if the provider supports lifecycle rules; verify per current provider docs (DO Spaces and R2 do; Hetzner does not). |
| "Use CloudWatch metrics" | CloudWatch doesn't see non-AWS buckets — use provider-native monitoring. |
| "Generate IAM policy" | Non-AWS providers use access keys or their own RBAC; no IAM policy applicable. |
| "Use S3 Transfer Acceleration" | AWS-only. |
| "Use presigned URL with v4 signing region X" | Region semantics differ: R2 needs `auto`, Hetzner uses path-style only, Spaces uses regional endpoint. |
| "Enable Object Lock" | Spaces and Hetzner: not supported. R2: supported. GCS uses a different mechanism (retention policy). Verify per current provider docs. |

## A bucket name alone is not enough

If you only have a bucket name like `prod-data`, you cannot identify the provider. Required context (in order of reliability):
1. The endpoint URL in the calling code
2. The provider/backend block in surrounding Terraform
3. The credential profile name or env var prefix
4. The repository's existing `aws_s3_bucket` resources' endpoint pattern (look for a sibling Terraform file)

If none of these are available, **ask the user** rather than guess.
