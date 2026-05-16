# Plan review

> Parent: [`../SKILL.md`](../SKILL.md). Provider identity verification, plan-then-apply discipline, and the "never bump pins in passing" rule live there and apply to everything below.

How to review a `terraform plan` before approving an apply. Use whenever the plan is non-trivial — anything with destroys, IAM mutations, or production state.

## Generate a plan file the apply will reuse

```bash
terraform init                          # if backend or providers changed
terraform plan -out=plan.out
terraform show -json plan.out > plan.json
```

`-out=plan.out` is required by `../SKILL.md` — it pins the apply to the exact change you reviewed.

## Lead with the destroy count

Before any analysis, surface a one-liner the user can interrupt on:

```bash
jq -r '
  [.resource_changes[] | .change.actions[]] |
  group_by(.) | map({key: .[0], value: length}) | from_entries
' plan.json
```

Then print exactly: `Plan summary: N to add, M to change, K to destroy. Env: <env>.`

Rule: if `K > 0` outside an ephemeral dev environment, stop here and require explicit "go ahead with K destroys" before continuing. Destroys buried at the end of a long report get rubber-stamped.

Cross-check `prevent_destroy`:

```bash
jq -r '.resource_changes[] | select(.change.actions[] | contains("delete")) | .address' plan.json
# Then grep the source for `prevent_destroy = true` on each address.
```

A plan that deletes a `prevent_destroy` resource will fail at apply — but the **intent** still demands a separate acknowledgement.

## Analyze in parallel for large plans

When the plan is big or touches risk-bearing surface, fan out three analyses in a single batch:

| Lane | What it produces |
|---|---|
| **Risk** | Cascade effects, destroy ordering, modification blast radius. Output: severity tier + ranked findings. |
| **Security** | IAM principal scope, network exposure, encryption posture, cross-account additions. |
| **Historical** | Prior changes to the same addresses, past incidents, rollback patterns. See `historical-patterns.md`. |

Merge the three into one aggregated report. Three separate analyses presented sequentially is worse than one synthesis — the user can't see the cross-cuts.

## Approval gate

Present the aggregated findings with the destroy count restated, then wait for an explicit affirmative on **this specific apply**. A "yes" earlier in the conversation about something else does not carry over (per the destroy-gate rule in `../SKILL.md`).

```bash
# Only after explicit approval:
terraform apply plan.out
```

## Severity tiering

| Tier | Triggers |
|---|---|
| **CRITICAL** | Destroys on stateful/data resources; IAM principal widening; KMS or encryption changes; new cross-account trust |
| **HIGH** | Reachability changes (SG / firewall / NACL); load balancer mutations; production DNS |
| **MEDIUM** | Instance class or size changes; non-trivial spec updates; cost-allocation tag changes |
| **LOW** | Pure adds with no dependents; documentation-only; benign tag-only diffs |

## Patterns worth explicit calling out

- **Cascade deletes** — one destroy pulling others through `depends_on` or implicit refs.
- **State drift surfacing inside the change plan** — fields moving that the diff didn't introduce. Cross over to `drift-detection.md`.
- **Permission relaxation** — CIDR widening, principal `*`, public ACL, removed deny statements.
- **Cost-shape changes** — counts or sizes scaled by an order of magnitude.
- **First touches** — a module or resource type that has no history in this repo carries silent risk; flag it as such instead of treating "no findings" as "safe".
