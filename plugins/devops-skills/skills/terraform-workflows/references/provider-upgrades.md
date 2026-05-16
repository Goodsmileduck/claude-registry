# Provider upgrades

> Parent: [`../SKILL.md`](../SKILL.md). The "never bump pins in passing" rule from the parent is why this is a dedicated workflow — pin changes are the entire task, not a side effect of another change.

Use when **deliberately** raising a provider, the Terraform / OpenTofu `required_version`, or a module `?ref=`. The order is: research breaking changes → scan the codebase → write the plan → execute with approval.

## Contents

- Capture current state — `terraform version`, `providers`, `.terraform.lock.hcl`
- Pick the target — one provider at a time across majors
- Read the upstream upgrade guide — per-provider changelog/guide table
- Categorize the breaking changes — removed/renamed/default/required taxonomy
- Scan for affected patterns — grep recipes per category
- Write the upgrade report — markdown template
- Execute — backup → init -upgrade → code edits → state migrations → plan → apply
- Risk by version delta — major / minor / patch
- Common traps — lock file platforms, deleted refs, transitive bumps, namespace moves
- Aftercare — new warnings, migrations applied, constraint relaxation

## Capture current state

```bash
terraform version                # binary version (also: tofu version)
terraform providers              # which providers, which versions
cat .terraform.lock.hcl          # exact pins + per-platform hashes
```

## Pick the target

Decide and write it down before doing anything:

- Which version? Latest stable, a specific tag, or the next minor only?
- One provider or several at once? Bump providers one at a time across majors; combine only across patches.

## Read the upstream upgrade guide

Always the **upgrade guide** before the changelog — guides cover user-facing breakage; changelogs drown that signal in internal churn.

| Provider | Changelog | Upgrade guide path |
|---|---|---|
| `hashicorp/aws` | terraform-provider-aws/CHANGELOG.md | `/docs/guides/version-X-upgrade` |
| `hashicorp/google` | terraform-provider-google/CHANGELOG.md | `/docs/guides/version_X_upgrade` |
| `hashicorp/azurerm` | terraform-provider-azurerm/CHANGELOG.md | `/docs/guides/X.0-upgrade-guide` |
| `digitalocean/digitalocean` | terraform-provider-digitalocean/CHANGELOG.md | Release notes |
| `cloudflare/cloudflare` | terraform-provider-cloudflare/CHANGELOG.md | `/docs/guides/version-X-upgrade` |

For anything outside this list, use Context7 MCP (`resolve-library-id` → `query-docs`) to fetch the current guide — training data lags reality.

## Categorize the breaking changes

| Category | Impact | What to do |
|---|---|---|
| Removed resource | HIGH | Find usages; plan replacement (may require `state mv` to a new resource type) |
| Removed argument | HIGH | Find usages; pick the new equivalent or drop the argument |
| Renamed resource | HIGH | Plan a `state mv` per affected address |
| Changed default | MEDIUM | Decide: set explicitly to the old value, or accept the new default |
| New required argument | MEDIUM | Add it to all affected blocks |
| Deprecation only | LOW | Plan migration before the next major; not blocking now |

## Scan the codebase

```bash
# Removed / renamed resource type
grep -rn "aws_old_resource_type" --include="*.tf"

# Removed / renamed argument
grep -rn "old_argument_name" --include="*.tf"

# Modules pinned to a ref that may have moved
grep -rn 'source.*?ref=' --include="*.tf"
```

Build a per-file inventory. Plan every change before applying any of them — partial edits between init and apply are the failure mode here.

## Upgrade report template

```markdown
## Provider upgrade — <provider> <current> → <target>

### Risk: HIGH | MEDIUM | LOW

### Breaking changes that hit this codebase

1. <change name>
   - Kind: removed argument | renamed resource | changed default | new required arg
   - Affected: <resource_type>.<attribute>
   - Files:
     - modules/compute/main.tf:45
     - environments/prod/instances.tf:23
   - Action: <specific fix>

### State migrations

- [ ] `aws_old.foo` → `aws_new.foo` (state mv)
- [ ] ...

### Deprecations to plan for (non-blocking)

- `aws_old_thing` → `aws_new_thing` (slated for removal in next major)
```

## Execute — with approval

```bash
terraform state pull > "backup-$(date +%Y%m%d-%H%M%S).tfstate"

# Bump required_providers in versions.tf, then:
terraform init -upgrade           # rewrites .terraform.lock.hcl

# Apply code edits per the report.

terraform state mv <old> <new>    # per-resource migrations; see state-operations.md

terraform plan -out=upgrade.plan  # final review per plan-review.md
terraform apply upgrade.plan
```

## Risk by version delta

| Delta | Typical risk | Approach |
|---|---|---|
| Major (4.x → 5.x) | HIGH | Full upgrade guide; non-prod first; explicit approval at every step |
| Minor (5.1 → 5.2) | LOW | Skim the changelog; watch for new deprecation warnings |
| Patch (5.1.0 → 5.1.1) | LOW | Brief changelog scan; generally safe |

## Common traps

- **Lock file missing some platforms.** Run `terraform init -upgrade` once per OS in CI/dev, or use `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64 -platform=darwin_amd64` to materialize the hashes you need, then commit.
- **`?ref=` pointing to a deleted or force-pushed tag.** Pin to a commit SHA, not a branch.
- **Hidden transitive bumps.** Provider X often pulls a wrapper module that's pinned older. Surface the chain rather than chasing it during apply.
- **Provider namespace moves** (`hashicorp/foo` → `community/foo`). `terraform providers` will show the new namespace; update `source =` and re-init.

## Aftercare

Surface to the user:

- New deprecation warnings now appearing in `terraform plan`.
- Each state migration that ran, with the old → new address mapping.
- Recommended follow-up: relax the version constraint to `~> NEW.0` so patch updates auto-apply, but keep major pinned.
