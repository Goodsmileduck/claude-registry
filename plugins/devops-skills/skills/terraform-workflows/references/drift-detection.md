# Drift Detection

> Parent: [`../SKILL.md`](../SKILL.md). Provider identity verification, plan-then-apply, and one-owner-per-resource rules from the parent apply here.

Detect and categorize drift between Terraform state and actual cloud resources. Drift = out-of-band changes that will cause noise (or worse) on the next apply.

## Before "fixing" drift — establish ownership first

Drift detection is the *first* step, not the fix. Who fixes what depends on who owns the resource:

| Situation | Fix |
|---|---|
| Resource is managed by ArgoCD / Flux / another controller; Terraform also has it | The other controller owns it. Remove from Terraform (`state rm` + delete the HCL) or add `lifecycle { ignore_changes = [...] }` for the fields the other side mutates. **Do NOT `terraform apply` to "fix"** — that starts a controller war. See `state-operations.md`. |
| Terraform legitimately owns the resource | Decide *before* applying: is the manual change correct (update TF to match) or wrong (apply TF to revert)? |
| Resource exists in cloud but not in TF state at all | `terraform import` only if Terraform should own it; otherwise document and leave alone |

**Anti-pattern:** mutating the live resource to make reality match Terraform so the next plan is clean. That hides the real problem. See the `argocd-operations` skill for the full GitOps posture.

## Step 1 — Refresh state

```bash
terraform init                              # if backend/providers changed
terraform plan -refresh-only -out=drift.out
terraform show -json drift.out > drift.json
```

`-refresh-only` updates state from real infrastructure without proposing config changes — it's the cleanest way to surface only the drift.

## Step 2 — Categorize the drift

```bash
# Count drifted resources
jq -r '.resource_drift | length' drift.json
# List them with the changed attributes
jq -r '.resource_drift[] | "\(.address): \(.change.actions | join(","))"' drift.json
```

| Category | Severity | Examples |
|---|---|---|
| **Security drift** | CRITICAL | Security group rules, IAM policies, encryption settings, KMS key policies, public-access flags |
| **Configuration drift** | HIGH | Instance type, network settings, env vars, scaling thresholds |
| **Tag drift** | LOW | Tags added/removed outside Terraform |
| **Metadata drift** | INFO | Cloud-provider-managed fields that change naturally (e.g. AWS launch template versions, GCE instance fingerprints) |

## Step 3 — Identify probable cause for each drifted resource

For each drift entry, work top-down through these causes:

1. **Another controller manages this** — check labels/annotations on K8s resources, `managed-by` tags on cloud resources, prior commits that introduced a second controller
2. **Click-ops** — check the cloud provider's audit log (CloudTrail, GCP Audit Logs, Activity Log) around the drift's first detection
3. **CI/CD race** — multiple pipelines applying to the same state
4. **Provider auto-update** — managed services (RDS minor versions, GKE node pool images) where the cloud edits the resource

## Step 4 — Resolution options (present to user)

| Option | Command | When |
|---|---|---|
| **Accept drift** (state catches up to reality) | `terraform apply -refresh-only` | The manual change is correct and intended to stick |
| **Reject drift** (revert reality to match TF) | `terraform apply` (normal) | The manual change is wrong and should be undone |
| **Hand off ownership** | `state rm` + remove from HCL | Another controller should own this — see `state-operations.md` |
| **Investigate first** | (none) | Cause is unclear; involve a human before changing anything |

**Never auto-resolve drift.** Always present options and wait for an explicit choice. Drift is signal — silencing it without diagnosis loses information.

## Common drift sources

| Source | Typical resolution |
|---|---|
| Auto-scaling adjustments | Accept (or add `ignore_changes` on `desired_capacity`) |
| Manager service auto-updates (RDS minor versions, GKE node pool images) | Accept; add `ignore_changes` on the upgrade-related fields |
| Emergency manual fixes | Accept + commit the equivalent IaC change immediately |
| Console click-ops mistakes | Reject |
| Conflicting controllers | Fix ownership (root cause) — neither accept nor reject is right |
| First-time integration where a tool added fields | Add `ignore_changes`; document why |

## What to report

Keep the report tight — drifted-resource count, severity breakdown, cause hypothesis for each, and the recommended resolution. The user decides; this skill informs.
