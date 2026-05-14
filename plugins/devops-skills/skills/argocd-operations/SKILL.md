---
name: argocd-operations
description: Designs and debugs ArgoCD ApplicationSets, picks generators, templates per-tenant deploys, configures sync waves and hooks, and untangles syncPolicy.automated prune/selfHeal. Use when working with ArgoCD, ApplicationSet, sync wave, GitOps, or per-tenant Application deploys.
---

# ArgoCD

## GitOps posture (the rules behind every recommendation here)

Every recommendation in this skill assumes GitOps-mode: **Git is the source of truth, the cluster is a downstream replica.** That implies three hard rules:

1. **Edit the chart/values, not the live object.** `kubectl edit deploy/foo` on an Argo-managed resource is reverted in ~3 minutes by `selfHeal`. The fix is a commit to the source repo. Temporary hotfixes are allowed only when (a) explicitly requested, (b) labelled as a hotfix, (c) followed by a TODO to backport.
2. **One owner per resource.** If ArgoCD manages a resource, Terraform must not also write it. If you're moving ownership from Terraform → Argo (or vice versa), close the loop: either remove the resource from the losing side's source or add `ignore_changes` / Argo `Ignore` annotations. See the `state-operations.md` reference in the `terraform-workflows` skill for the Terraform side.
3. **`--prune` is the moral equivalent of `terraform destroy`.** Any `argocd app sync --prune`, `app delete --cascade`, or `applicationsSync` change that could prune Applications requires per-invocation confirmation. List what would be pruned (`argocd app diff --refresh`) and pause before executing.

## When to invoke

**Scenarios:**
- You need one Application per X (cluster, tenant, directory, PR) and don't want to author them by hand.
- A multi-tenant deploy (per-clinic, per-customer, per-env) needs to scale without copy-pasting Application manifests.
- An ApplicationSet generated unexpected Applications, deleted ones you wanted to keep, or kept ones you wanted gone.
- Sync waves aren't ordering as expected; PreSync/PostSync hooks aren't firing.
- `prune: true` + `selfHeal: true` produced surprising behavior.

## Always-on defaults (set these at the top of every ApplicationSet)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: <name>
  namespace: argocd
spec:
  goTemplate: true                              # use Go templating, not fasttemplate
  goTemplateOptions: ["missingkey=error"]       # fail loudly on missing keys instead of ""
  syncPolicy:
    applicationsSync: create-update             # disallow auto-delete of generated Applications
    preserveResourcesOnDeletion: true           # if Application is deleted, leave the workloads
```

**Why these matter:**
- `goTemplate: true` — the legacy `fasttemplate` engine is being phased out; new ApplicationSets should use Go templates. Required for `range`, conditionals, and complex parameter shaping.
- `missingkey=error` — without it, a typo like `{{.naem}}` silently renders empty and you get an Application named `prod-` deploying to namespace `-prod`. This is the single most common ApplicationSet bug.
- `applicationsSync: create-update` — the controller will not delete Applications when a generator stops emitting them. Critical for production; without it a transient API error from the SCM Provider generator can prune all your apps.
- `preserveResourcesOnDeletion: true` — if a user accidentally deletes the ApplicationSet, the generated Applications and their workloads survive.

## Pick a generator by what changes

| What you have | Use generator |
|---|---|
| A fixed, small list of targets (envs, clusters) | **List** |
| Many clusters registered in ArgoCD, identified by labels on the cluster Secret | **Clusters** |
| One Application per directory in a Git repo (e.g. `apps/*`) | **Git directories** |
| One Application per file matching a glob in a Git repo (e.g. `tenants/*.yaml`) | **Git files** |
| Branches or PRs in a repo (preview environments) | **Pull Request** |
| Combine two of the above (e.g. each app × each cluster) | **Matrix** |
| Two sources whose results should be merged on a key (e.g. cluster name) | **Merge** |
| Repos discovered from a GitHub/GitLab org | **SCM Provider** |
| Cluster set decided by an external controller | **Cluster Decision Resource** |

## Cluster generator — the multi-tenant workhorse

For "one Application per cluster matching these labels":

```yaml
generators:
- clusters:
    selector:
      matchLabels:
        argocd.argoproj.io/secret-type: cluster   # exclude the in-cluster default
        type: workload
        env: prod
```

The matching cluster Secrets must be labelled appropriately:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: cluster-clinic-eu-1
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: cluster      # required for ArgoCD to treat as a cluster
    type: workload
    env: prod
    region: eu
    tenant: clinic-eu-1                          # custom labels become template vars
type: Opaque
data:
  name: <base64>
  server: <base64>
  config: <base64-of-rest-config-json>
```

In templates, label values are available as `{{.metadata.labels.tenant}}` (when `goTemplate: true`). Common parameters injected by the cluster generator:
- `{{.name}}` — the secret's `name` field
- `{{.server}}` — the cluster API URL
- `{{.metadata.labels.X}}` — any label on the cluster secret

## Matrix generator — one app per (X × Y)

Most common multi-tenant pattern: per-tenant directory in Git, fanned out across N clusters.

```yaml
generators:
- matrix:
    generators:
      - git:
          repoURL: https://github.com/org/tenants.git
          revision: HEAD
          directories:
            - path: tenants/*
      - clusters:
          selector:
            matchLabels:
              argocd.argoproj.io/secret-type: cluster
              env: prod
template:
  metadata:
    name: '{{.path.basename}}-{{.name}}'              # tenant-cluster
  spec:
    project: default
    source:
      repoURL: https://github.com/org/tenants.git
      targetRevision: HEAD
      path: '{{.path.path}}'                          # tenants/<tenant>
    destination:
      server: '{{.server}}'
      namespace: '{{.path.basename}}'
    syncPolicy:
      syncOptions: [CreateNamespace=true]
      automated:
        prune: true
        selfHeal: true
```

Result: cartesian product. 3 tenants × 5 clusters = 15 Applications. If you want a sparse mapping (tenant A only on cluster X), use the **Merge** generator on a key both sides emit.

## sync-wave gotchas

Annotate resources to order sync within a single Application:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "-1"     # negative waves run first
```

Rules that bite:
- Sync waves order **resources within one Application**, not Applications relative to each other. To order Applications, use a separate ApplicationSet with sync waves on the Applications themselves, or use App-of-Apps with waves.
- Default wave is 0. Resources with no annotation are wave 0 — they sync alongside other wave-0 resources, not after them.
- Waves apply per sync phase. PreSync hooks all run before any Sync-phase resource regardless of wave; PostSync after all Sync-phase resources.
- Finalizers can stall a wave indefinitely. If wave -1 includes a resource whose finalizer hangs, wave 0 never starts.

## Hooks — PreSync, Sync, PostSync, SyncFail, PostDelete

```yaml
metadata:
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded   # clean up after success
```

| Hook | Fires when | Common use |
|---|---|---|
| `PreSync` | Before sync starts | DB migrations, ConfigMap warming |
| `Sync` (default) | During sync, ordered by wave | Normal resources |
| `PostSync` | After all Sync resources are Healthy | Smoke tests, cache warmers |
| `SyncFail` | After a failed sync | Notifications, rollback triggers |
| `PostDelete` | After Application deletion | Cleanup external resources |

Delete policies for hooks (where the hook resource lives after running):
- `HookSucceeded` — delete on success (typical for Jobs)
- `HookFailed` — delete on failure (clean up failed Jobs)
- `BeforeHookCreation` — delete previous instance before creating new (default for Jobs; required if your Job name is static)

## `automated` sync policy — `prune` vs `selfHeal`

```yaml
syncPolicy:
  automated:
    prune: true       # delete cluster resources removed from Git
    selfHeal: true    # revert manual changes in cluster to match Git
```

What each one actually does:

| Setting | When it triggers | When it doesn't |
|---|---|---|
| `prune: true` | Resource removed from Git → ArgoCD deletes it from cluster | Resource added in cluster but not in Git — leaves it alone (those are "extra resources," handled separately) |
| `selfHeal: true` | Anyone runs `kubectl edit` on a managed resource — ArgoCD reverts within ~3 min | New manual resource (no Git source) — left alone (same as `prune`'s blind spot) |

**Trap:** `selfHeal` reverts based on the rendered manifest. If your manifest uses a Helm chart whose default values changed between syncs, selfHeal can flap — it'll try to revert to the just-synced value, but on next sync the value re-renders differently.

**Trap:** `prune: true` + a generator that filters to zero results = mass deletion. Use `applicationsSync: create-update` at the ApplicationSet level as a safety net (it prevents the *Application* from being pruned, even if `prune` is true on the inner syncPolicy).

## Debugging — when an ApplicationSet does the wrong thing

```bash
# Did the generator emit what you expected?
kubectl get applicationset <name> -n argocd -o yaml | yq '.status'

# What Applications exist with this owner reference?
kubectl get applications -n argocd -o json | \
  jq '.items[] | select(.metadata.ownerReferences[]?.name=="<appset-name>") | .metadata.name'

# Controller logs
kubectl logs -n argocd deploy/argocd-applicationset-controller --tail=200 | grep -i <appset-name>
```

Common diagnoses:

| Symptom | Likely cause |
|---|---|
| Generator runs but produces 0 Applications | Selector matches no cluster secrets; check labels — most commonly `argocd.argoproj.io/secret-type: cluster` is missing on the secret |
| Application names collide | Template doesn't include enough discriminators (e.g. just `{{.name}}` when matrix produces overlapping names) — add tenant/path/cluster to the name |
| Applications stuck `OutOfSync` | `goTemplate: false` (legacy fasttemplate) silently rendered an empty field — check for `name: prod-` or `namespace: -` |
| Pruned all Applications unexpectedly | The generator's data source went away (e.g. Git repo unreachable, SCM token expired) and `applicationsSync` wasn't set to `create-update` |
| One Application syncs, others don't | Per-Application `syncPolicy.automated` not set in template; only sync-policy on the AppSet itself doesn't propagate to generated Apps |
