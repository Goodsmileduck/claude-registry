# Provider Upgrades

> Parent: [`../SKILL.md`](../SKILL.md). The parent's "never bump pins in passing" rule is what makes this a dedicated workflow — pin changes are the whole task, not a side-effect.

Use when *intentionally* bumping a provider, the Terraform/OpenTofu `required_version`, or a module `?ref=`. Analyze breaking changes, identify code modifications, plan state migrations, then apply with explicit user approval.

## Contents

- Step 1 — Capture current state (`terraform version`, `providers`, `.terraform.lock.hcl`)
- Step 2 — Identify target version
- Step 3 — Research breaking changes (provider-specific changelog and guide locations)
- Step 4 — Categorize the breaking changes (removed/renamed/default/required)
- Step 5 — Scan the code for affected patterns
- Step 6 — Generate the upgrade report (template included)
- Step 7 — Execute the upgrade (backup, init -upgrade, code changes, state migrations, plan, apply)
- Risk by version delta (major / minor / patch)
- Common traps — lock-file platforms, deleted tags, transitive bumps, namespace moves
- After the upgrade

## Step 1 — Capture current state

```bash
terraform version                # binary version
terraform providers              # provider versions in use
cat .terraform.lock.hcl          # exact pinned versions + hashes
```

## Step 2 — Identify target version

Decide explicitly:
- Latest stable, specific version, next major, or next minor?
- One provider or several at once? (Prefer one at a time for major bumps.)

## Step 3 — Research breaking changes

Read the official upgrade guide and changelog *before* touching code:

| Provider | Changelog | Upgrade guide path |
|---|---|---|
| `hashicorp/aws` | github.com/hashicorp/terraform-provider-aws CHANGELOG.md | `/docs/guides/version-X-upgrade` |
| `hashicorp/google` | github.com/hashicorp/terraform-provider-google CHANGELOG.md | `/docs/guides/version_X_upgrade` |
| `hashicorp/azurerm` | github.com/hashicorp/terraform-provider-azurerm CHANGELOG.md | `/docs/guides/X.0-upgrade-guide` |
| `digitalocean/digitalocean` | github.com/digitalocean/terraform-provider-digitalocean CHANGELOG.md | release notes |
| `cloudflare/cloudflare` | github.com/cloudflare/terraform-provider-cloudflare CHANGELOG.md | `/docs/guides/version-X-upgrade` |

Prefer the official upgrade guide over the changelog — guides focus on the changes that affect users; changelogs include internal churn.

## Step 4 — Categorize the breaking changes

| Category | Impact | Action |
|---|---|---|
| **Removed resource** | HIGH | Find usages, plan replacement; may need `state mv` to a new resource type |
| **Removed argument** | HIGH | Find usages, decide new equivalent or removal |
| **Renamed resource** | HIGH | Plan `state mv` for every affected address |
| **Changed default** | MEDIUM | Decide: explicit set to old value, or accept new default |
| **New required arg** | MEDIUM | Add to all affected blocks |
| **Deprecation only** | LOW | Plan migration before the next major; not blocking now |

## Step 5 — Scan the code for affected patterns

```bash
# Removed/renamed resource type
grep -rn "aws_old_resource_type" --include="*.tf"

# Removed/renamed argument
grep -rn "old_argument_name" --include="*.tf"

# Modules pinned to a ref that may not exist after upgrade
grep -rn 'source.*?ref=' --include="*.tf"
```

Build a per-file list of touches. Plan all changes before applying any.

## Step 6 — Generate the upgrade report

```markdown
## Provider Upgrade — <provider> <current> → <target>

### Risk: [HIGH | MEDIUM | LOW]

### Breaking changes affecting this codebase
1. <change name>
   - Type: removed argument / renamed resource / changed default / new required arg
   - Affected: <resource_type>.<attribute>
   - Files:
     - modules/compute/main.tf:45
     - environments/prod/instances.tf:23
   - Action: <specific fix>

### State migrations required
- [ ] `aws_old.foo` → `aws_new.foo` (state mv)
- [ ] ...

### Deprecations to plan for (not blocking now)
- `aws_old_thing` → use `aws_new_thing` (removed in next major)

### Upgrade plan
1. Backup state: `terraform state pull > backup-$(date +%Y%m%d).tfstate`
2. Update `required_providers` version constraint
3. `terraform init -upgrade`
4. Apply code changes (per list above)
5. Run state migrations (per list above)
6. `terraform plan` — verify only the intended changes show
7. Review + apply
```

## Step 7 — Execute (with approval)

```bash
# Backup first
terraform state pull > "backup-$(date +%Y%m%d-%H%M%S).tfstate"

# Update version constraint in versions.tf (or required_providers block)
# Then:
terraform init -upgrade           # rewrites .terraform.lock.hcl

# Apply code changes per the report

# State migrations, if any
terraform state mv <old> <new>    # see state-operations.md

# Final plan + apply (per plan-review.md)
terraform plan -out=upgrade.plan
# Review carefully — esp. fields the provider auto-populates differently
terraform apply upgrade.plan
```

## Risk by version delta

| Delta | Typical risk | Approach |
|---|---|---|
| Major (4.x → 5.x) | HIGH — breaking changes expected | Full upgrade guide; test in non-prod; explicit approval |
| Minor (5.1 → 5.2) | LOW — usually backward compatible | Read changelog; watch for new deprecation warnings |
| Patch (5.1.0 → 5.1.1) | LOW — bug fixes only | Read changelog briefly; generally safe |

## Common traps

- **`.terraform.lock.hcl` not regenerated for all platforms** — if your team has mixed darwin/linux, run `terraform init -upgrade` once and commit the resulting `.terraform.lock.hcl` with `linux_amd64` + `darwin_arm64` + `darwin_amd64` hashes.
- **Module `?ref=` pointing to a deleted tag** — the upstream repo may have force-pushed; pin to a commit SHA, not a branch.
- **Hidden bumps via dependency chains** — bumping `aws` may transitively require bumping a Terraform module that wraps it. Surface the chain.
- **Provider name changes** — a provider may have moved namespaces (`hashicorp/foo` → `community/foo`). `terraform providers` will show this; update the `source =` line.

## After the upgrade

Surface to the user:
- New deprecation warnings now appearing in `terraform plan` output
- Any state migrations that landed (with the old → new address mapping)
- Suggested follow-up: bumping the version constraint to `~> NEW.0` instead of pinning exact, so patch updates auto-apply
