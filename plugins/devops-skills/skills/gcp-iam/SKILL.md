---
name: gcp-iam
description: Debugs GCP permission-denied errors, designs IAM bindings, traces org → folder → project inheritance, and untangles service-account impersonation chains. Covers Workload Identity. Use when working with GCP IAM, gcloud, "permission denied" on GCP resources, Workload Identity, or SA impersonation.
---

> For the GitHub Actions caller side of Workload Identity Federation (`permissions: id-token: write`, `google-github-actions/auth` setup, the OIDC token shape), see the `github-actions-pipelines` skill. This skill covers the GCP side: pool provider attribute conditions, service account `iam.workloadIdentityUser` bindings, impersonation chains.


# GCP IAM Inheritance

## When to invoke

**Symptoms:**
- `permission denied: <permission> on resource <X>` despite the user being "in the right group at the org level."
- `iam.serviceAccounts.getAccessToken` denied during Workload Identity, ESO sync, or CI/CD.
- A service account can `get` a resource but not `list` siblings (or vice versa).
- `gcloud projects get-iam-policy <project>` shows no binding for the user, but the user *can* log into the project console.
- Cross-project SA impersonation works for human users via `gcloud auth login` but fails from a workload.

**The trap this prevents:** assuming GCP IAM is additive across the hierarchy in all directions. It is — but with caveats around conditional bindings, deny policies, audit-log filtering, and the fact that some permissions only apply at specific resource levels.

## The inheritance model (the part that *does* work)

```
Organization
  └── Folder(s)
        └── Project(s)
              └── Resource(s)   (Bucket, BQ Dataset, GCE Instance, etc.)
```

**Rule:** A role granted at level N applies to N and everything below it. Permissions are *added* — never subtracted (except by Deny policies, see below).

So `roles/storage.admin` granted at the *folder* level grants storage.admin on every project under that folder. This is the "obvious" inheritance and it does work as expected.

## Why "group at org" sometimes fails to grant project access

Common reasons users report "I'm in the group but it doesn't work":

1. **The group has no IAM binding at any level.** Group membership alone grants nothing. The group must appear as a `principal` in an IAM binding somewhere in the hierarchy.
   ```bash
   # Find every binding that includes a group, project or higher
   gcloud projects get-iam-policy <project> \
     --flatten="bindings[].members" \
     --filter="bindings.members:group:<group-email>" \
     --format="value(bindings.role)"

   gcloud organizations get-iam-policy <org-id> \
     --flatten="bindings[].members" \
     --filter="bindings.members:group:<group-email>" \
     --format="value(bindings.role)"
   ```

2. **The binding exists but uses a *resource-level* role.** Some roles (e.g. `roles/storage.objectViewer`) make sense bound to a bucket, not an org. Granting them at the org grants viewer on every bucket in every project — but a *deny policy* somewhere downstream can block it.

3. **A Deny policy at a lower level overrides the allow.** Deny policies are evaluated *before* allows and always win. Check:
   ```bash
   gcloud iam policies list --attachment-point="cloudresourcemanager.googleapis.com/projects/<project-num>" --kind=denypolicies
   ```

4. **Conditional bindings restrict scope.** A binding with a `condition` block (CEL expression) only grants the role when the condition is true — e.g. only for resources tagged `env=dev`, or only between 9am–5pm.
   ```bash
   gcloud projects get-iam-policy <project> --format=json | jq '.bindings[] | select(.condition)'
   ```

5. **Group nesting depth or propagation delay.** Workspace group changes propagate to GCP within minutes but not instantly. Nested groups are supported but each level adds latency. After a permission change, wait 1–5 minutes before re-testing.

6. **The user is in the group but using a different identity.** `gcloud auth list` may show a service account or a different user. Confirm:
   ```bash
   gcloud config list account
   gcloud auth list
   ```

## Service account impersonation — three roles, three meanings

These look similar and are routinely confused:

| Role | What it lets you do |
|---|---|
| `roles/iam.serviceAccountUser` | "Run as" the SA when *attaching* it to a GCE instance, Cloud Function, or Cloud Run service. Does NOT grant the right to mint tokens for it. |
| `roles/iam.serviceAccountTokenCreator` | Generate short-lived access tokens or sign JWTs *as* the SA. This is the one Workload Identity, ESO, and `gcloud --impersonate-service-account` need. |
| `roles/iam.workloadIdentityUser` | Specifically for binding a K8s ServiceAccount to a GSA in GKE/Anthos Workload Identity. Required on the *target* GSA, granted *to* the K8s SA's principal identifier. |

Diagnostic for `iam.serviceAccounts.getAccessToken` denied:
```bash
# Who is trying to impersonate
gcloud config list account                              # user/caller
# Is caller a tokenCreator on the target SA?
gcloud iam service-accounts get-iam-policy <target-sa> \
  --format=json | jq '.bindings[] | select(.role=="roles/iam.serviceAccountTokenCreator")'
```

If the caller is a K8s SA, the principal format depends on which GKE identity mode you use:

| Mode | Principal in IAM binding | Role on target SA (if impersonating one) |
|---|---|---|
| **Workload Identity for GKE** (older, namespace-pool model) | `serviceAccount:<project>.svc.id.goog[<namespace>/<k8s-sa>]` | `roles/iam.workloadIdentityUser` |
| **Workload Identity Federation for GKE** (newer; uses a Workload Identity Pool) | `principal://iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/<pool>/subject/...` or `principalSet://...` for a set | Bind roles directly to the principal; impersonation no longer required for many flows |

The newer Federation form lets K8s SAs hold GCP roles directly (no GSA intermediary). The older form is still supported but is the GKE-specific path; new clusters default to the Federation model.

## Project-level vs org-level diagnostic flow

When debugging "permission denied":

1. **What permission, on what resource?** Read the error message carefully — the resource name (`projects/<p>/buckets/<b>/...`) tells you the level.

2. **At what level should the role be bound?** Some permissions only apply at certain levels. Compute access? Project-level. BQ dataset access? Dataset-level. Org-policy management? Org-level.

3. **List bindings from the bottom up:**
   ```bash
   # Most specific first — resource-level (bucket, dataset, etc.) — then up the hierarchy
   gsutil iam get gs://<bucket>                       # bucket-level
   gcloud projects get-iam-policy <project>           # project-level
   gcloud resource-manager folders get-iam-policy <folder-id>  # folder
   gcloud organizations get-iam-policy <org-id>       # org
   ```

4. **Check for deny policies attached at any level.** They override allows.

5. **Check conditional bindings.** A condition that evaluates false yields silent denial.

6. **If using impersonation, walk the chain.** Caller → tokenCreator on intermediate SA → tokenCreator on target SA. Each link must be explicit.

## Quick reference — common command shapes

```bash
# Who am I, what project, what auth mode?
gcloud config list
gcloud auth list

# Resolve "what roles does <principal> have on <project>"
gcloud projects get-iam-policy <project> \
  --flatten="bindings[].members" \
  --filter="bindings.members:<user-or-sa-email>" \
  --format="value(bindings.role)"

# Same for the org (catches inherited grants)
gcloud organizations get-iam-policy <org-id> \
  --flatten="bindings[].members" \
  --filter="bindings.members:<user-or-sa-email>" \
  --format="value(bindings.role)"

# What permissions does role R actually contain?
gcloud iam roles describe <role-name>  # e.g. roles/storage.objectViewer

# Simulate: does principal P have permission X on resource R?
gcloud asset analyze-iam-policy \
  --organization=<org-id> \
  --identity="user:<email>" \
  --permissions="<permission>" \
  --resource="//<full-resource-name>"
```

The `analyze-iam-policy` command is the closest GCP has to a definitive "do I have access" answer — it walks the full inheritance, conditions, and deny policies in one call. Use it when manual inspection of multiple `get-iam-policy` calls is getting confusing.
