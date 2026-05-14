# Plan Review

> Parent: [`../SKILL.md`](../SKILL.md). All cross-cutting rules (provider identity verification, plan-then-apply only, no in-passing pin bumps) are in the parent and apply here.

Workflow for analyzing a `terraform plan` before approving an apply. Use this when the plan is non-trivial (any destroys, IAM changes, or production state) or when you need a structured second-pair-of-eyes review.

## Step 1 — Generate the plan file

```bash
terraform init                          # only if backend or providers changed
terraform plan -out=plan.out
terraform show -json plan.out > plan.json
```

`-out=` is mandatory — it's the only way to guarantee the apply runs against the same plan you reviewed.

## Step 2 — Print the blast-radius summary first

Before any analysis, print one line the user can stop you at:

```bash
jq -r '
  [.resource_changes[] | .change.actions[]] |
  group_by(.) | map({key: .[0], value: length}) | from_entries
' plan.json
# → {"create": N, "update": M, "delete": K, ...}
```

Output exactly: `Plan summary: N to add, M to change, K to destroy. Env: <env>.`

If `K > 0` outside ephemeral dev, halt and require explicit "go ahead with K destroys" before any further analysis. Do not bury destroys inside a longer report.

List any `prevent_destroy` resources the plan touches:

```bash
jq -r '.resource_changes[] | select(.change.actions[] | contains("delete")) | .address' plan.json
# Grep the source for `prevent_destroy = true` on each address.
```

A planned delete of a `prevent_destroy` resource will fail at apply — but the *intent* still needs explicit acknowledgement.

## Step 3 — Parallel analysis (for non-trivial plans)

When the plan is large or risky, dispatch three analyses in a single message (parallel):

```
Task 1 — risk:
  Analyze plan.json for cascade effects, destroy operations, modification
  risks. Output: risk level (CRITICAL/HIGH/MEDIUM/LOW) + ranked findings.

Task 2 — security:
  Review plan.json for IAM, network, encryption, and compliance impact.
  Flag: security-weakening changes, over-broad principals, public exposure.

Task 3 — historical:
  See `historical-patterns.md`. Check git history for prior changes to the
  affected resources; surface past incidents or rollbacks.
```

Aggregate into a single report; do not present three separate analyses to the user.

## Step 4 — Approval gate

Present the aggregated findings with the explicit destroy count, then wait for `approve` (or equivalent explicit go). Do not proceed on a generic "yes" — the rule from the parent SKILL.md applies: a "yes" is scoped to the previous question, not the whole plan.

```bash
# Only after explicit approval:
terraform apply plan.out
```

## Risk taxonomy (for the aggregated report)

| Level | What qualifies |
|---|---|
| **CRITICAL** | Any destroy on stateful/data resources; IAM principal expansions; encryption key changes; cross-account access additions |
| **HIGH** | Network/SG/firewall changes that affect reachability; load balancer modifications; DNS changes on production zones |
| **MEDIUM** | Instance type changes; non-trivial config updates; tag changes that affect cost allocation |
| **LOW** | Pure adds with no dependencies; documentation-only; tag-only updates |

## Common patterns to flag explicitly

- **Cascade deletions** — one destroy triggers others via `depends_on` or implicit references
- **State drift in the plan** — fields changing that you didn't touch in code → see `drift-detection.md`
- **Security relaxation** — rules becoming more permissive (CIDR widening, principal `*`, public ACL)
- **Cost impact** — large count/size changes (e.g. `count = 3 → 30`)
- **First-time CRD / first-time module** — no historical signal; treat as higher risk
