# State Operations

> Parent: [`../SKILL.md`](../SKILL.md). The one-owner-per-resource rule from the parent is what most state ops exist to enforce — read it first if you haven't.

State operations modify Terraform's understanding of infrastructure without changing actual resources. Mistakes orphan resources or trigger recreates of live infrastructure — always back up, document, and get explicit approval before executing.

## Contents

- Why state ops happen — the ownership-change trigger
- Pre-operation safety — Terragrunt note, backup, document, approval
- Operations reference — `state mv`, `state rm`, `import`, `pull`/`push`, `force-unlock`
- Recovery procedures — corrupted state, wrong removal, orphans
- Risk gradient — per-op risk level and what to highlight in the approval ask

## Why state ops happen

Almost every state op exists because of an ownership change:

| Trigger | Operation |
|---|---|
| Resource was created by another tool (Helm, console, kubectl, another TF root) and should now be TF-managed | `import` |
| Module was refactored; resource address changed | `state mv` |
| Resource is being handed off from TF to ArgoCD (or vice versa) | `state rm` on the losing side, configure the winning side separately |
| State got corrupted or backend moved | `pull` / `push` |

Whatever the trigger, **close the ownership loop on the losing side** before considering the op complete — either remove from the losing source-of-truth or add `lifecycle { ignore_changes = [...] }`. See parent SKILL.md §4.

## Pre-operation safety (always, no exceptions)

### Terragrunt note

If this is a Terragrunt unit, run state ops through the wrapper — not against the cached copy:

```bash
terragrunt state pull > "$BACKUP_NAME"
terragrunt state mv <src> <dst>

# Wrong — runs against .terragrunt-cache/<hash>/<path>/ with potentially stale backend:
# cd .terragrunt-cache/<hash>/<path>/ && terraform state mv ...
```

See `terragrunt.md` for cache path mapping.

### 1. Backup

```bash
BACKUP_NAME="state-backup-$(date +%Y%m%d-%H%M%S).tfstate"

# Local state
cp terraform.tfstate "$BACKUP_NAME"

# Remote state (any backend: S3, GCS, DO Spaces, Azure Blob, etc.)
terraform state pull > "$BACKUP_NAME"

# Terragrunt
terragrunt state pull > "$BACKUP_NAME"
```

### 2. Document

Before executing, write the plan somewhere durable:

```markdown
**Op:** [state mv | state rm | import]
**Source addr:** ...
**Dest addr / cloud ID:** ...
**Reason:** ...
**Backup:** $BACKUP_NAME
**Rollback:** restore $BACKUP_NAME via `terraform state push`
**Loser-side cleanup:** [HCL removal | ignore_changes block | manifest deletion]
```

### 3. Get explicit approval

Print the resolved command + scope, then wait. A generic "yes" earlier in the conversation does not authorize a state op. The user must say "go" to this specific op.

## Operations reference

### `terraform state mv` — rename / reorganize

```bash
terraform state list                                          # see what's there
terraform state mv aws_instance.old_name aws_instance.new_name
terraform state mv aws_instance.web module.web.aws_instance.this
terraform state mv module.old.aws_instance.web module.new.aws_instance.web
```

**Verify:** `terraform plan` → should show no changes.

### `terraform state rm` — remove from state without destroying

Used to hand off ownership or to stop managing a resource. The resource continues to exist in the cloud.

```bash
terraform state rm aws_instance.legacy
terraform state rm module.legacy
```

**WARNING:** If you don't also remove the resource from the HCL, the next plan will try to *recreate* it (TF sees code but no state → "needs to be created"). Either remove from code or add `ignore_changes` — close the loop.

### `terraform import` — adopt existing infrastructure

```bash
# 1. Write the resource block in HCL first (matching the actual config)
# 2. Import:
terraform import aws_instance.web i-1234567890abcdef0
# Module path:
terraform import module.web.aws_instance.this i-1234567890abcdef0
# 3. terraform plan → should show no changes (or only acceptable diffs you'll then encode in HCL)
```

Importing without writing the HCL first leaves you with state-but-no-code, and the next plan deletes the resource. Always: code first, import second, plan third.

### `terraform state pull` / `push` — backup, migrate, restore

```bash
terraform state pull > state.json

# state push is DANGEROUS — typically hook-blocked.
# terraform state push state.json
```

### `terraform force-unlock` — break a stuck lock

Only when you're certain no other operation is in progress (another `apply` running, another team member). Get the lock ID from the error message:

```bash
terraform force-unlock <lock-id>
```

Wrong unlocks cause concurrent writes and state corruption — confirm no one else is running TF in this layer before doing this.

## Recovery procedures

### State corrupted

```bash
cp "$BACKUP_NAME" terraform.tfstate
# For remote state, you may need to:
terraform state push "$BACKUP_NAME"
# (Be sure no one else is mid-apply.)
```

### Wrong resources removed (`state rm` mistake)

```bash
terraform state push "$BACKUP_NAME"   # restore from backup
terraform init -reconfigure           # sync with backend
```

### Resources orphaned in the cloud

Either re-import them (`terraform import …`) or delete them manually if no longer needed. Surface the choice to the user; don't decide unilaterally.

## Risk gradient (for the approval ask)

| Op | Risk | What to highlight |
|---|---|---|
| `state mv` | Low | Source and destination address; verify no changes after |
| `state rm` | Medium | The resource keeps existing in the cloud but TF stops managing — emphasize this |
| `import` | Medium | HCL must already match real config; otherwise next plan diffs |
| `force-unlock` | Medium | Confirm no concurrent operation; wrong unlock corrupts state |
| `state push` | High | Hook usually blocks; only with explicit "I understand the risk, push state.json" |
