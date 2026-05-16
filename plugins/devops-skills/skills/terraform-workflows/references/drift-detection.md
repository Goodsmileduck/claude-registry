# Drift detection

> Parent: [`../SKILL.md`](../SKILL.md). Provider identity verification, plan-then-apply, and the one-owner-per-resource rule from the parent govern this whole reference — re-read them if "fixing drift" is on the table.

Drift = the live cloud resource doesn't match Terraform's recorded state. Detection is the moment to establish ownership — running `apply` to "fix" drift without that step is how you start a controller war.

## First question: who owns this resource?

Apply the one-owner rule from `../SKILL.md` §4. The reference for the loser-side cleanup is `state-operations.md`.

| Situation | Action |
|---|---|
| Another controller manages it (ArgoCD, Flux, an Operator, kubectl, console) and Terraform also has it | Hand off — see `state-operations.md`. |
| Terraform is the legitimate owner | Decide *before* applying: is the live drift the correct state (update HCL) or wrong (revert via apply)? |
| Resource is in the cloud but not in any state file | `terraform import` only if Terraform should own it. Otherwise document and leave alone. |

The anti-pattern is mutating the live resource to silence the next plan. The drift itself is signal; suppressing it without diagnosis loses information. See the `argocd-operations` skill for the GitOps posture this references.

## Surface the drift cleanly

```bash
terraform init                              # if backend or providers changed
terraform plan -refresh-only -out=drift.out
terraform show -json drift.out > drift.json
```

`-refresh-only` reconciles state against reality without proposing config-side changes — the cleanest signal isolation Terraform offers.

Count and list:

```bash
jq -r '.resource_drift | length' drift.json
jq -r '.resource_drift[] | "\(.address): \(.change.actions | join(","))"' drift.json
```

## Categorize before reporting

| Severity | Examples |
|---|---|
| **CRITICAL** | Security groups, IAM, encryption, KMS policy, public-access flags |
| **HIGH** | Instance class, networking, environment variables, autoscaling thresholds |
| **LOW** | Tags |
| **INFO** | Provider-managed fields that drift naturally (launch template versions, instance fingerprints, RDS minor version increments) |

## Probable-cause checklist, top-down

Run these against each drifted address until something fits:

1. **Another controller** — labels/annotations on K8s, `managed-by` tags on cloud resources, the git log for the commit that introduced a parallel controller.
2. **Console click-ops** — cloud audit log around the drift's apparent start: CloudTrail, GCP Audit Logs, Azure Activity Log.
3. **Pipeline race** — two CI/CD jobs applying to the same state from different branches.
4. **Provider self-mutation** — managed services that edit themselves (RDS minor bumps, GKE node pool image refreshes).

## Resolution menu — never auto-pick

Present these to the user; let them choose explicitly.

| Choice | Mechanism | When |
|---|---|---|
| Accept drift | `terraform apply -refresh-only` | The manual change is correct and intentional |
| Reject drift | `terraform apply` (normal) | The manual change was wrong; revert |
| Hand off ownership | `state rm` + delete the HCL block (or `ignore_changes`) | A different controller should own this |
| Investigate | (no change yet) | Cause is unclear; pull in a human |

## Common sources mapped to the usual resolution

| Source | Usual choice |
|---|---|
| Autoscaling adjusting `desired_capacity` | Accept or `ignore_changes` |
| Managed-service self-upgrade (RDS minor, GKE node image) | Accept and add `ignore_changes` on the affected fields |
| Emergency hot-fix to live infra | Accept *and* commit the equivalent IaC change in the same PR |
| Console click-ops mistake | Reject |
| Two controllers fighting | Don't accept or reject — fix the ownership root cause |
| First-time integration that added new fields | `ignore_changes` with a one-line comment explaining why |

## Reporting shape

Keep the report tight:

- Drifted-resource count + severity breakdown.
- One-line cause hypothesis per address.
- Suggested resolution per address.

The user decides; this reference equips them to decide quickly. A long narrative drift report buries the choice they actually need to make.
