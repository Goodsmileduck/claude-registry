---
name: terragrunt-workflows
description: Terragrunt-specific orchestration patterns — CLI redesign migration (run/run --all, --terragrunt-* flag removal, TG_* env vars, strict controls), config composition (include, locals, inputs deep-merge, generate blocks), dependency wiring (mock_outputs semantics), run --all safety, hooks, and the new terragrunt.stack.hcl. Use when working with Terragrunt, `terragrunt.hcl`, `terragrunt.stack.hcl`, the deprecated `run-all`, `--terragrunt-*` flags, `TERRAGRUNT_*` env vars, `include` blocks, `dependency` blocks, or `terragrunt run --all`.
---

# Terragrunt Workflows

Terragrunt-specific orchestration. Pairs with the `terraform-workflows` skill — its cross-cutting rules apply on top of this skill's. See [Cross-skill notes](#cross-skill-notes) for the exact boundary.

## When to invoke

**Symptoms:**

- A command that worked last quarter now warns or fails: `--terragrunt-*` flag deprecated, `run-all` deprecated, "unknown command" for what used to be `terragrunt plan`.
- An `include` block isn't producing the merged config you expected.
- A child unit's `inputs` map seems to keep parent keys you tried to drop.
- A `dependency` block's `mock_outputs` produces values the actual upstream never emits.
- `terragrunt run --all apply` is on the table for a production rollout.
- A `terragrunt.stack.hcl` file is in the repo and you need to know how it generates units.
- Hooks (`before_hook`, `after_hook`, `error_hook`) fire at the wrong moment or silently swallow errors.

## Cross-cutting rules

These layer on top of `terraform-workflows` cross-cutting rules. Plan-then-apply, never bump pins in passing, never `-auto-approve` on shared state — all of those still apply.

1. **Treat the CLI redesign as the default.** Even if a repo still uses `run-all` and `--terragrunt-*` flags, write new code in the new form (`run --all`, `--<flag>`, `TG_*` env vars). Set `TG_STRICT_CONTROL=cli-redesign` locally so you can't accidentally regress.
2. **`run --all <mutation>` requires a plan-aggregation step.** It does NOT produce a unified plan. Without aggregation, blast radius is invisible until mid-pipeline. See [run --all safety](#run---all-orchestration).
3. **Never edit a generated file.** Files inside `.terragrunt-cache/` and `.terragrunt-stack/` are derived. Editing them produces drift that vanishes on the next run.
4. **Hooks run on every invocation.** A `before_hook` with `commands = ["plan", "apply"]` runs both during `plan` and `apply`. If a hook performs auth refresh or state pre-fetch, it costs that work twice on a plan→apply cycle. Scope hooks tightly.
5. **`mock_outputs` are placeholders for plan-time, not stand-ins for unapplied state.** If a plan contains a recognizable mock literal where you'd expect a real ID, the upstream dependency hasn't been applied. Apply it; do not adjust the mock to match reality.

## CLI redesign (the big one)

Terragrunt's CLI was redesigned in the v0.73 series. Training data and many internal scripts predate this — assume Claude's first guess is the deprecated form.

### The four renames

| Old (deprecated) | New |
|---|---|
| `terragrunt plan` (auto-forwards to OpenTofu) | `terragrunt run plan` |
| `terragrunt run-all <cmd>` | `terragrunt run --all <cmd>` |
| `--terragrunt-<flag>` | `--<flag>` (e.g. `--terragrunt-source-update` → `--source-update`) |
| `TERRAGRUNT_<VAR>` | `TG_<VAR>` (e.g. `TERRAGRUNT_NON_INTERACTIVE` → `TG_NON_INTERACTIVE`) |

Bare `terragrunt plan` still works for backward compatibility, but no longer auto-forwards unknown commands by default — under strict mode it errors.

### New top-level commands

- `terragrunt run <cmd> [--all]` — execute an OpenTofu/Terraform command in one or all units
- `terragrunt find` — discover units (replaces ad-hoc `find` for `terragrunt.hcl`); supports `--dag --json` for dependency order
- `terragrunt list` — list units with optional `--dag --tree`
- `terragrunt stack {generate,run,output,clean}` — manage `terragrunt.stack.hcl` files
- `terragrunt backend {bootstrap,migrate,delete}` — explicit backend management (previously implicit)

### Strict controls

`TG_STRICT_MODE=true` turns every deprecation into an error.

`TG_STRICT_CONTROL=cli-redesign,default-command` enables specific groups. Names of controls are stable across versions; check the current list with `terragrunt --help` or the docs.

Recommended in CI from day one — catches regressions before they accumulate.

### Migration heuristic

When you see deprecated syntax in a repo, do **not** mass-rewrite. Instead:

1. Set `TG_STRICT_CONTROL=cli-redesign` in CI.
2. Let the failures surface where the bumps would be load-bearing.
3. Rewrite per-call as you touch each file.

Mass rewrites churn diffs and risk missing a call site that depends on the old shape (e.g. a wrapper script forwarding `TERRAGRUNT_DEBUG`).

## Config composition

Terragrunt configs compose via `include` blocks. The merge semantics are the most common source of "but I told it to override that" surprises.

### Include blocks

```hcl
# envs/prod/db/terragrunt.hcl

include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true                # makes locals/blocks available as include.root.*
}

include "region" {
  path           = find_in_parent_folders("region.hcl")
  expose         = true
  merge_strategy = "no_merge"  # don't merge into the child; only expose
}
```

`merge_strategy` values:

- **(default — omit the field)** — block-aware merge: top-level fields (`inputs`, `locals`, `mock_outputs`) deep-merge; named blocks (`remote_state`, `generate`, `terraform`) shallow-replace.
- **`shallow`** — same as default for most cases; explicit form.
- **`deep`** — opt-in deep merge of everything including named blocks. Use sparingly; surprising.
- **`no_merge`** — don't merge at all. Useful when you want to `expose` parent values without inheriting parent config.

### Inputs and mock_outputs deep-merge — the trap

```hcl
# parent: _envcommon/db.hcl
inputs = {
  tags = { team = "platform", env = "prod" }
}

# child: envs/stg/db/terragrunt.hcl
inputs = {
  tags = { env = "stg" }    # ← child does NOT erase `team`
}

# effective:
# tags = { team = "platform", env = "stg" }
```

For scalars, child wins at the leaf. For nested maps and lists:

- Scalars in maps: child wins per key.
- Maps: recursively merged, **not** replaced.
- Lists: concatenated (parent first, then child).

**To force a child-only map**, three options ordered by preference:

1. Compute the map in `locals { }` and assign `tags = local.tags`. Sidesteps the merge entirely.
2. Restate every key in the child so the merged result equals what you want.
3. Set `merge_strategy = "no_merge"` on the include — but this drops *everything* inherited, not just one map.

Setting a key to `null` in the child **does not delete** the parent key. HCL deep-merge treats `null` as a value, not a delete sentinel.

### remote_state and generate are shallow

In contrast to `inputs`, named blocks are shallow:

```hcl
# parent
remote_state {
  backend = "s3"
  config = { bucket = "p", key = "x" }
}

# child
remote_state {
  backend = "local"   # child block replaces parent block entirely; config is lost
}
```

If you want to inherit most of the parent's `remote_state` and tweak one field, you usually need to define it once in the parent and not redeclare in children.

### Generate blocks

`generate` writes a file into each unit's working directory before OpenTofu/Terraform runs. Standard uses: backend block, provider block, version pins.

```hcl
generate "backend" {
  path      = "backend.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<EOF
terraform {
  backend "s3" {}
}
EOF
}
```

`if_exists` values:

- `overwrite_terragrunt` — overwrite only if Terragrunt itself created the file previously. **Default. Use this.**
- `overwrite` — clobber any existing file. Dangerous; can stomp committed `backend.tf`.
- `skip` — never overwrite. Use when generation is opt-in.
- `error` — fail if a file exists.

The silent-overwrite trap: a developer commits a `provider.tf` for local convenience, then later a parent adds `generate "provider"` with `if_exists = "overwrite"`. The committed file vanishes on the next `terragrunt run plan` and nobody notices until a feature it provided breaks.

## Dependency wiring

```hcl
dependency "vpc" {
  config_path = "../vpc"

  mock_outputs = {
    vpc_id     = "vpc-fake-12345"
    subnet_ids = ["subnet-fake-1", "subnet-fake-2"]
  }
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependencies {
  paths = ["../iam-role"]   # ordering only, no outputs
}
```

Key distinctions:

- `dependency` block: provides outputs via `dependency.vpc.outputs.<x>` AND establishes ordering.
- `dependencies` block: ordering only, no outputs read.

`mock_outputs_allowed_terraform_commands` — restrict when mocks are allowed. Default: only `plan`/`validate`/`output` need mocks; `apply` should fail loudly if the dependency hasn't been applied. Don't add `apply` to this list unless you've thought hard about it.

`mock_outputs_merge_strategy_with_state` — when state exists but is missing some output keys (e.g., upstream module added new outputs), what to do:

- `no_merge` (default) — if state exists, use state outputs exclusively; missing keys error out.
- `shallow` — fill in missing top-level keys from `mock_outputs`.
- `deep` — recursively fill in missing nested keys.

Use `shallow` when an upstream module adds new outputs faster than downstream units apply. Use `no_merge` (default) for stricter coupling.

For tracing dependency errors and cache misdirection (`Reference to undeclared output`, "Module not installed" after a ref bump, mock literals appearing in plan output) → see `terraform-workflows/references/terragrunt.md`.

## `run --all` orchestration

The mental model: `terragrunt run --all <cmd>` executes `<cmd>` in each unit in dependency order. It is NOT a unified plan/apply across units. Each unit's `<cmd>` is independent.

### Safe — read-only operations

```bash
terragrunt run --all plan -out=tfplan
terragrunt run --all output
terragrunt run --all validate
terragrunt run --all state list
```

These can be automated freely.

### Unsafe by default — mutations across many units

```bash
terragrunt run --all apply     # ← per-unit applies; mid-pipeline failure leaves stack inconsistent
terragrunt run --all destroy
```

The safer pattern:

1. `terragrunt run --all plan -out=tfplan` — writes a plan file in each unit's `.terragrunt-cache/.../tfplan`.
2. Aggregate the blast-radius summary across units (e.g. `terragrunt find --dag --json` to enumerate; parse each unit's plan).
3. Human review — destroys, recreates, IAM/policy changes.
4. Either `terragrunt run --all apply tfplan` (applies the saved plans, no replan) OR targeted per-unit applies for changed units only.

### Useful flags

- `--parallelism <N>` — limit concurrent unit operations. Default is high; lower it for rate-limited backends.
- `--queue-include-dir <pattern>` / `--queue-exclude-dir <pattern>` — scope which units run, useful for partial rollouts.
- `--queue-include-units-reading <file>` — include units that read a specific config file. Targets the blast radius of a config change.
- `--ignore-dependency-errors` — continue past failed units. Usually wrong for `apply`; sometimes right for `plan` when you want to see all failures at once.

## Hooks

```hcl
terraform {
  before_hook "auth_refresh" {
    commands = ["apply"]                     # scoped tight — plan reuses cached creds
    execute  = ["aws", "sso", "login", "--no-browser"]
  }

  after_hook "tag_state_bucket" {
    commands     = ["apply"]
    execute      = ["bash", "-c", "echo applied at $(date) >> /tmp/applies.log"]
    run_on_error = false
  }

  error_hook "alert" {
    commands      = ["apply"]
    execute       = ["bash", "-c", "curl -X POST $SLACK_WEBHOOK -d '{\"text\":\"apply failed\"}'"]
    on_errors     = [".*"]
  }
}
```

Key rules:

- `commands` is an inclusion list. A hook with `commands = ["apply"]` does not fire on `plan`.
- `run_on_error` defaults to `true` for `after_hook` — after-hooks fire even when the command failed. Set `false` if the hook should only run on success.
- `error_hook` fires only on error; pattern-match with `on_errors` (regex strings).
- Hook commands run in the unit's working directory by default. Set `working_dir` if you need otherwise.
- Hooks are NOT executed inside the OpenTofu/Terraform process — they're spawned by Terragrunt. Exit codes propagate (a failing `before_hook` aborts the command).

Anti-pattern: a `before_hook "auth"` that does `aws sso login` interactively. Fine on a laptop, blocks forever in CI. Gate with `if`:

```hcl
before_hook "auth" {
  commands = ["plan", "apply"]
  execute  = ["bash", "-c", "[ -z \"$CI\" ] && aws sso login || true"]
}
```

## Stacks (terragrunt.stack.hcl)

The newer feature for generating units from templates. A `terragrunt.stack.hcl` file declares units; `terragrunt stack generate` materializes them under `.terragrunt-stack/`.

```hcl
# envs/prod/svc/terragrunt.stack.hcl

unit "vpc" {
  source = "git::git@github.com:acme/infra-catalog.git//units/vpc?ref=v1.0.0"
  path   = "vpc"
  values = {
    vpc_name = "main"
    cidr     = "10.0.0.0/16"
  }
}

unit "database" {
  source = "git::git@github.com:acme/infra-catalog.git//units/database?ref=v1.0.0"
  path   = "database"
  values = {
    engine   = "postgres"
    vpc_path = "../vpc"
  }
}
```

Run:

```bash
terragrunt stack generate           # materialize units into .terragrunt-stack/
terragrunt stack run plan           # plan all units in the stack
terragrunt stack run apply tfplan   # apply saved plans
```

**Trap:** `.terragrunt-stack/` is generated. Do NOT edit files inside it. Edit the template unit modules (the `source = "..."` targets) or the `terragrunt.stack.hcl` values.

`no_dot_terragrunt_stack = true` on a unit prevents generation into the `.terragrunt-stack/` subdirectory — useful for preserving `path_relative_to_include()` behavior during migrations from hand-authored unit directories.

When to reach for a stack:
- Many near-identical units (e.g. one per region, one per tenant).
- Catalog pattern where unit definitions live in a separate repo and consumers compose stacks from them.

When NOT to use stacks:
- Each unit is meaningfully different. Per-unit `terragrunt.hcl` stays clearer.
- A small infra (under ~10 units). The generation step is overhead.

## Anti-patterns checklist

- ❌ Editing files inside `.terragrunt-cache/` or `.terragrunt-stack/`. Always wrong — regenerated on next run.
- ❌ `terragrunt run --all apply` from CI without per-unit plan aggregation.
- ❌ `--terragrunt-source-update`, `TERRAGRUNT_NON_INTERACTIVE`, or `run-all` in new code — use the redesigned CLI (`--source-update`, `TG_NON_INTERACTIVE`, `run --all`).
- ❌ Setting a parent input to `null` in a child config expecting it to delete. Use `locals { }` or restate the whole map.
- ❌ `generate` block with `if_exists = "overwrite"` over a committed `*.tf` file the developer doesn't realize is at risk.
- ❌ Adding `"apply"` to `mock_outputs_allowed_terraform_commands` to "make CI green." It hides unapplied dependencies.
- ❌ `before_hook` that auths interactively without a CI guard.
- ❌ Mass-rewriting deprecated CLI calls in one PR. Migrate per-touch under `TG_STRICT_CONTROL`.

## Cross-skill notes

- For Terraform-level concerns (plan review, drift, state surgery, provider upgrades, multi-cloud identity verification), see the `terraform-workflows` skill — its cross-cutting rules apply on top of this skill's.
- For `.terragrunt-cache/<hash>/<hash>/` error tracing and dependency-DAG inspection commands, see `terraform-workflows/references/terragrunt.md`.
- For Kubernetes resources managed via Terragrunt with potential Argo overlap, consult the `argocd-operations` skill on ownership posture.
