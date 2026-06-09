---
name: terraform-workflows
description: Reviews Terraform/OpenTofu plans, detects drift, performs state surgery (mv/rm/import), upgrades providers, and traces Terragrunt cache errors. Multi-cloud. Use when working with Terraform, OpenTofu, Terragrunt, terraform plan, drift, or provider upgrades.
---

# Terraform / OpenTofu / Terragrunt

This skill is the entry point for every Terraform-touching task. Cross-cutting rules below apply to all sub-procedures; the router at the bottom points to the right `references/` file for the specific job.

## Cross-cutting rules (apply to every Terraform operation)

These supersede any habit, default, or shortcut. If a sub-procedure in `references/` ever appears to contradict one of these, the rule here wins.

### 1. Verify provider identity before any plan or mutation

Run this before generating any plan or executing any apply/destroy/import. A plan or apply against the wrong account is worse than no plan — it produces misleading diffs and risks destructive cross-environment writes.

```bash
# Identify providers in scope
terraform providers | grep -E 'aws|google|digitalocean|azurerm|cloudflare'
# Terragrunt: prepend `terragrunt`

# Verify identity for each provider detected
aws sts get-caller-identity              # AWS — Account, Arn
gcloud config list account project       # GCP — account + active project
doctl account get                        # DigitalOcean — email + team
az account show                          # Azure — subscription + tenant

# Cloudflare uses a scoped token, not caller identity:
curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  https://api.cloudflare.com/client/v4/user/tokens/verify | jq .result.status
```

If any provider in scope fails its identity check, or the result doesn't match the expected environment (account/project/team), **STOP** and surface the mismatch. Do not generate a plan; the cached state will produce a misleading diff.

### 2. Plan-then-apply only — never `-auto-approve` on shared state

Hard rules:
- Never invoke `terraform apply` directly. Always `terraform plan -out=<file>` → review → `terraform apply <file>`.
- Never combine `-auto-approve` with `apply` on shared dev, stg, or prod state. The only acceptable use is ephemeral throwaway state (your own laptop sandbox).
- Print the blast-radius summary BEFORE any analysis: `Plan summary: N to add, M to change, K to destroy.`
- If `K > 0` (any destroys) outside ephemeral dev, surface the count first and require explicit "go ahead with K destroys" before continuing.

### 3. Never bump pins in passing

The following are load-bearing pins. If one needs to change, that change is the whole task — analyze, announce, get explicit approval, then apply.

| Pin | Where |
|---|---|
| `required_version = "..."` | `terraform { }` block |
| `version = "~> X.Y"` | `required_providers { }` |
| `?ref=` / `?version=` | `module { source = "..." }` |
| Helm `chart` / `version` | `helm_release` resource or values |
| `.terraform.lock.hcl` hashes | Generated, but committed |

Symptoms of an accidental bump:
- A `versions.tf` line changed in a diff that "just adds a variable"
- `.terraform.lock.hcl` has new hashes you didn't ask for
- Plan suddenly shows attribute renames

If you catch yourself wanting to bump in passing, **stop** — flag the pin as needing a separate decision and continue with the existing pin.

### 4. One owner per resource

Never have two controllers writing the same field. When introducing or switching controllers (Terraform → Argo, console → Terraform, etc.), close the loop on the losing side:

1. Remove the resource from the losing controller's source-of-truth (delete the HCL block, remove the Kubernetes manifest from Argo's source path), or
2. Add `lifecycle { ignore_changes = [...] }` on the losing side for the fields the winning side now owns.

Document the new owner in a comment on the resource. Two controllers fighting over a resource is much harder to debug than the original handoff — always close ownership before considering the work complete.

### 5. Always `fmt` + `validate` after edits

```bash
terraform fmt -recursive
terraform validate
```

Before claiming any `.tf` edit is "done." Hallucinated attributes, mis-cased keys, and structural typos surface here cheaply — don't make them surface in the plan.

### 6. Bootstrap circularity gets an explicit manual-step note

Anything that bootstraps itself (TF state bucket created by the same root that uses it; IAM that grants access to that state; ESO that fetches its own credentials) gets a `# bootstrap: manual` comment in the IaC. Don't silently assume bootstrap order.

## Router — which `references/` file to read

| Task / symptom | Read |
|---|---|
| About to run `terraform apply` on non-trivial state; need to review the plan first; plan output has unfamiliar destroys | `references/plan-review.md` |
| Suspecting out-of-band changes; `plan` shows unexpected diffs against an unchanged module | `references/drift-detection.md` |
| `terraform state mv`, `state rm`, `import`, `force-unlock`, or `taint`; resource was created by another tool and now needs TF ownership (or vice versa) | `references/state-operations.md` |
| Bumping a provider version intentionally; hitting `Unsupported attribute` or `Unsupported block type` errors after a `required_version` change | `references/provider-upgrades.md` |
| Need to understand why a resource is configured the way it is; investigating a recurring issue with this module | `references/historical-patterns.md` |
| Error path references `.terragrunt-cache/<hash>/<hash>/...`; dependency `mock_outputs` may be masking unapplied state; dependency DAG inspection | `references/terragrunt.md` |
| Terragrunt CLI redesign (`run`, `run --all`, `--terragrunt-*` flag rename, `TG_*` env vars), `include`/`inputs` merge semantics, `generate` blocks, `dependency` block wiring, hooks, `terragrunt.stack.hcl` | use the `terragrunt-workflows` skill |

If the task involves a Kubernetes resource Terraform is managing, also consult the `argocd-operations` skill for ownership posture if Argo is in the picture, or the `kubernetes-operations` skill for cluster-level debugging.

## Anti-patterns checklist (never do these)

- `terraform apply -auto-approve` on shared state
- Bumping `required_version`, provider `version =`, or module `?ref=` as a side-effect of other work
- Editing files inside `.terragrunt-cache/<hash>/<hash>/`
- Running state operations without a backup (`terraform state pull > backup.tfstate`)
- Importing a resource without removing it from the previous controller's source
- Inventing provider attribute names; if unsure, `terraform providers schema -json` or check the official registry docs
