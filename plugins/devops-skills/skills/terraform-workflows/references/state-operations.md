# State operations

> Parent: [`../SKILL.md`](../SKILL.md). The one-owner-per-resource rule from the parent is the reason most state surgery exists — re-read it if you haven't.

State operations rewrite Terraform's view of the world without touching live infrastructure. The failure modes are orphaned resources, silent recreates, and cross-controller fights. The Pre-flight section below is non-negotiable.

## Contents

- Why state ops exist (ownership change patterns)
- Pre-flight: Terragrunt wrapper, backup, document, approval
- Operations: `state mv`, `state rm`, `import`, `pull`/`push`, `force-unlock`
- Recovery: corrupted state, accidental removal, orphans
- Risk gradient — what to highlight when asking for approval

## Why state ops happen

Almost every state op is triggered by an ownership change. Identify the trigger before reaching for the command:

| Trigger | Operation |
|---|---|
| A resource was created by another tool (Helm, console, kubectl, a second TF root) and should become Terraform-managed | `import` |
| A module refactor moved a resource's address | `state mv` |
| Ownership is moving between TF and another controller (e.g. ArgoCD takes over) | `state rm` on the losing side; configure the winning side separately |
| State got corrupted, or the backend moved | `pull` / `push` |

Whatever the trigger, **close the ownership loop on the losing side** before considering the op complete: remove from the losing source-of-truth, or pin `ignore_changes` on the contested fields. See `../SKILL.md` §4.

## Pre-flight — no exceptions

### Terragrunt wrapper

If this is a Terragrunt unit, drive the op through Terragrunt — never into the cached copy:

```bash
terragrunt state pull > "$BACKUP_NAME"
terragrunt state mv <src> <dst>

# Wrong — operates on .terragrunt-cache/<hash>/<path>/ with a stale backend:
# cd .terragrunt-cache/<hash>/<path>/ && terraform state mv ...
```

Cache-path mapping is in `terragrunt.md`.

### Backup

```bash
BACKUP_NAME="state-backup-$(date +%Y%m%d-%H%M%S).tfstate"

# Local backend
cp terraform.tfstate "$BACKUP_NAME"

# Remote backend (S3, GCS, DO Spaces, Azure Blob, etc.)
terraform state pull > "$BACKUP_NAME"

# Terragrunt
terragrunt state pull > "$BACKUP_NAME"
```

### Document before executing

Write the plan somewhere the next person (or you, six months from now) can find:

```markdown
**Op:**            state mv | state rm | import
**Source addr:**   ...
**Dest addr / cloud ID:** ...
**Why:**           ...
**Backup file:**   $BACKUP_NAME
**Rollback:**      terraform state push $BACKUP_NAME
**Loser-side cleanup:** HCL removed | ignore_changes block added | manifest deleted
```

### Explicit approval

Print the exact resolved command. Wait. A "yes" earlier in the chat to something else does **not** authorize a state op; ownership-changing commands require approval on the specific command shown.

## Operations

### `state mv` — rename / reorganize

```bash
terraform state list
terraform state mv aws_instance.old_name aws_instance.new_name
terraform state mv aws_instance.web module.web.aws_instance.this
terraform state mv module.old.aws_instance.web module.new.aws_instance.web
```

Verification: `terraform plan` should report no changes.

### `state rm` — stop managing without destroying

The cloud resource keeps running; Terraform forgets it.

```bash
terraform state rm aws_instance.legacy
terraform state rm module.legacy
```

Trap: leaving the HCL block in place after `state rm`. The next plan sees code without state and proposes a fresh create. Either delete the HCL or pin `ignore_changes` on the contested fields.

### `import` — adopt an existing resource

Discipline: **code → import → plan**. Write the matching HCL block first, then run import, then plan-verify.

Two forms:

```hcl
# Plannable form (Terraform 1.5+, OpenTofu) — preferred for review-driven workflows.
import {
  to = aws_instance.web
  id = "i-1234567890abcdef0"
}
```

```bash
# Imperative CLI form — always available.
terraform import aws_instance.web i-1234567890abcdef0
terraform import module.web.aws_instance.this i-1234567890abcdef0
```

The `import {}` block surfaces in `terraform plan` like any other change, which makes it auditable in PRs. The CLI form is one-shot.

Importing without HCL leaves state-but-no-code; the next plan deletes the resource. The post-import plan should be empty (or only show diffs you'll then encode in HCL).

### `pull` / `push` — backup, migrate, restore

```bash
terraform state pull > state.json
# terraform state push state.json   # DANGEROUS — usually hook-blocked
```

### `force-unlock` — break a stuck lock

Only when you're certain no other apply is running. Lock ID comes from the error message:

```bash
terraform force-unlock <lock-id>
```

Wrong unlocks cause concurrent writes and state corruption. Confirm with the rest of the team (or your CI logs) before unlocking.

## Recovery

| Problem | Recovery |
|---|---|
| State corrupted | `cp $BACKUP_NAME terraform.tfstate` for local backends, or `terraform state push $BACKUP_NAME` for remote. Confirm no concurrent apply first. |
| `state rm` removed the wrong resource | `terraform state push $BACKUP_NAME` then `terraform init -reconfigure` |
| Cloud resource is orphaned (state says no, cloud says yes) | Either re-import or manually delete. Surface the choice — don't decide alone. |

## Risk gradient

For the approval ask, pair the op with what specifically warrants escalation:

| Op | Risk | What to highlight |
|---|---|---|
| `state mv` | LOW | Source and destination addresses; verify "no changes" after |
| `state rm` | MEDIUM | Resource keeps existing in cloud; TF stops managing it |
| `import` | MEDIUM | HCL must already match real config or the next plan diffs |
| `force-unlock` | MEDIUM | Confirm no concurrent op; wrong unlock corrupts state |
| `state push` | HIGH | Hook usually blocks; require "I understand the risk, push state.json" |
