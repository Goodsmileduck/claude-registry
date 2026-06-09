# Terragrunt — cache tracing and dependency DAG

> Parent: [`../SKILL.md`](../SKILL.md). All parent rules apply (especially: never edit cache files, run state ops via `terragrunt` wrapper not raw TF — see `state-operations.md`).

## Contents

- When to use this reference — error-path symptoms, mock_outputs suspicion, stale cache
- How the cache works — the per-unit, `<provider-hash>/<source-hash>` layout
- Step 1 — Map a cache path back to the source unit
- Step 2 — Force a cache refresh after a source change
- Step 3 — Walk the dependency DAG with `find --dag` and `list --dag --tree`
- Step 4 — Common failure modes table
- Step 5 — Redirect cache for inspection (`TG_DOWNLOAD_DIR`, `TG_PROVIDER_CACHE_DIR`)
- Anti-pattern checklist

## When to use this reference

**Symptoms:**
- An error or stack trace references a file under `.terragrunt-cache/<hash>/<hash>/...` and you're tempted to edit that file.
- `terragrunt plan` works but `apply` fails with a backend or module-source error that didn't appear before.
- A dependency block's `mock_outputs` is in scope and you're not sure if the plan is real or mocked.
- A module source change isn't being picked up despite re-running.
- "Module not installed" / "Module source has changed" warnings.

**The trap this prevents:** treating `.terragrunt-cache/` as ground truth. It's a derived working copy. Fixing the cache fixes nothing — the source unit (or the `source = "..."` URL in `terragrunt.hcl`) is the real target.

## How the cache works (the model you need)

For each unit (a directory with `terragrunt.hcl` whose `terraform` block has a `source = "..."`), Terragrunt:

1. Downloads the module to `<unit>/.terragrunt-cache/<provider-hash>/<source-hash>/...`
2. Renders inputs/locals/dependencies into a working copy inside that subdirectory.
3. Runs OpenTofu/Terraform from inside the working copy.

The cache lives **per unit**, in that unit's own directory — not in a shared root location (unless you've set `TG_DOWNLOAD_DIR`). So a path like:

```
infra/envs/dev/db/.terragrunt-cache/AbCd.../XyZ.../main.tf
```

…tells you the source unit is `infra/envs/dev/db/` (the directory containing the `.terragrunt-cache`). The two hashes after that are the source URL hash and a working-copy hash — **don't edit anything inside.**

## Step 1 — Map a cache path back to the source unit

```bash
# Given an error mentioning /some/path/.terragrunt-cache/A/B/file.tf
# The source unit is the directory CONTAINING the .terragrunt-cache:
ERROR_PATH="/some/path/.terragrunt-cache/AbCd/XyZ/main.tf"
SOURCE_UNIT="${ERROR_PATH%%/.terragrunt-cache/*}"
echo "Source unit: $SOURCE_UNIT"
cat "$SOURCE_UNIT/terragrunt.hcl"
```

Now read the `source = "..."` value. That's the actual module being rendered. Three cases:

| `source` value | Action |
|---|---|
| `git::ssh://...` or `git::https://...` ref to a remote module | Edit the *upstream* module repo, or pin/unpin the ref. Cache will refresh on next run with `--source-update`. |
| `"../../../modules/foo"` (relative path to local module) | Edit the local module at that resolved path. |
| Inline `terraform { source = "tfr:///..." }` (Terraform Registry) | Pin to a different version. |

## Step 2 — Force a cache refresh after a source change

When you've changed the upstream module or bumped its ref, Terragrunt's cache may still hold the old copy:

```bash
# Modern Terragrunt CLI (preferred)
terragrunt run plan --source-update

# Legacy CLI (still works on older versions)
terragrunt plan --terragrunt-source-update
```

If `--source-update` doesn't help, nuke the unit's cache (safe — it's a working copy):

```bash
# From the source unit directory:
rm -rf .terragrunt-cache
terragrunt run init   # or `terragrunt init` on legacy CLI
```

To find and clean stale caches across a tree:

```bash
find . -type d -name ".terragrunt-cache"
# Inspect, then if you want to wipe all:
find . -type d -name ".terragrunt-cache" -prune -exec rm -rf {} +
```

## Step 3 — Walk the dependency DAG when dep wiring is suspect

When a unit fails with output-related errors (`Unsupported attribute`, `Reference to undeclared module output`, or just confusingly empty values), inspect the dependency graph:

```bash
# JSON dump of units in dependency order
terragrunt find --dag --json

# Visual tree
terragrunt list --dag --tree
```

For a single unit, see what it depends on:

```bash
# From the unit's directory:
terragrunt graph-dependencies
```

If a `dependency "X"` block uses `mock_outputs`, the unit can plan even when `X` hasn't been applied — but the plan is then **based on the mock**, not on real outputs. Verify whether you're seeing real or mocked values:

```hcl
dependency "vpc" {
  config_path = "../vpc"
  mock_outputs = {
    vpc_id = "vpc-fake-12345"   # ← if you see this in your plan, the mock is in play
  }
  mock_outputs_allowed_terraform_commands = ["plan"]   # mock only used during plan
}
```

**Diagnostic:** if the plan output contains a mock literal (recognizable string from the `mock_outputs` block) where you'd expect a real ID, the upstream dependency hasn't been applied yet. Apply it first; do not edit the mock to match reality.

## Step 4 — Common failure modes and the actual fix

| Symptom | Likely cause | Fix |
|---|---|---|
| Error path inside `.terragrunt-cache/<hash>/<hash>/...`, code looks wrong | Old cached copy of a module that's since been updated upstream | `terragrunt run plan --source-update` |
| `Reference to undeclared output: vpc_id` | Dependency unit hasn't been applied yet; no mock configured | Apply the dependency first, or add a `mock_outputs` for plan-time |
| Plan shows obviously fake values (`mock-subnet`, `vpc-fake-*`) | `mock_outputs` is masking unapplied state | Apply the real dependency, then re-plan |
| `Backend configuration changed` in the cache | `remote_state` block changed but cache holds the old backend | Wipe the unit's `.terragrunt-cache` and re-init |
| `Module not installed` after switching git refs | Cache pinned to old ref | `--source-update` or wipe cache |
| Different units appear to share state | Cache subdirectory hash collision or accidentally shared `remote_state.key` | Verify each unit's `terragrunt.hcl` resolves to a unique `key` in `remote_state.config` |

## Step 5 — Redirect cache for inspection (optional)

If you want all caches in one inspectable location (e.g. for sandboxing or to share a provider plugin cache across many units):

```bash
# Per-invocation
TG_DOWNLOAD_DIR=/tmp/tg-cache terragrunt run plan

# Provider plugin cache shared across all units (big perf win, no source-cache changes)
export TG_PROVIDER_CACHE_DIR=$HOME/.terragrunt-provider-cache
```

This doesn't change the model — caches are still per-unit — but it consolidates the location, which makes `find`/`du` and inspection easier.

## Anti-pattern checklist

- ❌ Editing files inside `.terragrunt-cache/<hash>/<hash>/`. Always wrong. They'll be overwritten.
- ❌ `cd .terragrunt-cache/<hash>/<hash>/ && terraform apply`. Bypasses Terragrunt's input rendering and dependency resolution — produces results that diverge from the next `terragrunt apply`.
- ❌ Updating `mock_outputs` to match observed reality so plans "pass." The mock is a placeholder; matching reality just hides the unapplied dependency.
- ❌ Treating `.terragrunt-cache/` as something to commit or back up. It's `.gitignore` material and always reconstructible.
