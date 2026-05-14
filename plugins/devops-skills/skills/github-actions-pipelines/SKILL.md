---
name: github-actions-pipelines
description: Debugs and authors GitHub Actions workflows — OIDC federation to AWS/GCP/Azure, GITHUB_TOKEN permissions hardening, reusable workflows vs composite actions, deploy concurrency, caching, the path-filter/required-check trap, and pull_request_target security. Use when working with GitHub Actions, `.github/workflows/`, OIDC to cloud providers, `pull_request_target`, branch protection required checks, reusable workflows, or CI/CD pipelines that deploy to AWS/GCP/DigitalOcean.
---

# GitHub Actions Pipelines

## When to invoke

**Symptoms:**

- `Not authorized to perform: sts:AssumeRoleWithWebIdentity` from a GitHub Actions job that's "supposed to use OIDC."
- `Error: google-github-actions/auth failed with: failed to generate Google Cloud federated token`.
- A required status check is stuck "Expected — Waiting for status to be reported" on PRs that touched unrelated paths.
- Secrets are `null` / empty in a workflow triggered by a fork PR.
- A reusable workflow can't see the caller's secrets.
- Two deploys to the same environment race each other and the older one wins.
- `actions/cache` reports a hit but the build still re-installs everything.
- A workflow runs untrusted PR code with `pull_request_target` and has secrets — security audit needs a verdict.

**The trap this prevents:** treating GitHub Actions as "just YAML." The privilege model, trigger semantics, and branch-protection interactions have non-obvious failure modes that look like "the action is broken" but are actually misconfiguration.

## Cross-cutting rules

These apply to every section below.

1. **Pin third-party actions to a commit SHA, not a floating tag.** See [supply chain](#supply-chain) for the format. First-party `actions/*` / `aws-actions/*` / `google-github-actions/*` can use major-version tags; everything else pins by SHA.
2. **Default `permissions:` to least-privilege.** Add `permissions: contents: read` at the workflow root and elevate per-job only what's needed. A repo's "default workflow permissions" setting can be `read` or `read-and-write` org-wide — don't rely on it; be explicit.
3. **Never check out and execute fork code from `pull_request_target`.** See the [pull_request_target rule](#pull_request-vs-pull_request_target).
4. **Verify current action versions before recommending YAML.** First-party actions (`aws-actions/*`, `google-github-actions/*`, `actions/*`) ship breaking major versions on their own cadence and training data lags. Before writing YAML, query the action's README via Context7 (`/aws-actions/configure-aws-credentials`, `/actions/cache`, etc.) to confirm the current major and any protocol changes.
5. **Skipped jobs are not passing jobs.** A required check that's skipped (via `paths:`, `if:`, or matrix-exclude) reports nothing to branch protection. See [path-filter trap](#the-path-filter--required-check-trap).

## OIDC federation to cloud

Use OIDC instead of long-lived access keys whenever possible. Three pieces must agree, or the assume-role call fails:

1. The workflow has `permissions: id-token: write` on the job (or workflow). Without it, no OIDC token is minted.
2. The cloud trust policy's audience (`aud`) matches what the action sends (`sts.amazonaws.com` for AWS).
3. The cloud trust policy's subject (`sub`) condition matches the actual workflow context.

### AWS — `aws-actions/configure-aws-credentials`

Verify the current major via Context7 (`/aws-actions/configure-aws-credentials`) before pasting — the action follows semver and major bumps have changed the role-assumption protocol in the past. The shape below is stable across recent majors:

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gh-deploy
          aws-region: us-east-1
      - run: aws sts get-caller-identity
```

Trust policy (the IAM role's `AssumeRolePolicyDocument`):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:my-org/my-repo:ref:refs/heads/main"
      }
    }
  }]
}
```

**`sub` claim formats** (mix these up and you get AssumeRoleWithWebIdentity denied):

| Workflow context | `sub` value |
|---|---|
| Push to `main` | `repo:ORG/REPO:ref:refs/heads/main` |
| Tag push | `repo:ORG/REPO:ref:refs/tags/v1.2.3` |
| Pull request | `repo:ORG/REPO:pull_request` |
| Environment `prod` | `repo:ORG/REPO:environment:prod` |
| Any context (wildcard) | `repo:ORG/REPO:*` — use `StringLike`, not `StringEquals` |

Prefer environment-scoped subjects for deploys — they pair with [environment protection rules](#environments--protection-rules) for human approval.

**AWS-side prerequisites:**

- `arn:aws:iam::<acct>:oidc-provider/token.actions.githubusercontent.com` must exist in the account (one-time setup; v4+ of the action no longer requires a thumbprint list — AWS handles it).
- The role must have a permissions policy attached, not just the trust policy. Trust policy ≠ permissions.

### GCP — `google-github-actions/auth`

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: projects/123/locations/global/workloadIdentityPools/gh/providers/gh-provider
          service_account: deployer@my-proj.iam.gserviceaccount.com
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud auth list
```

The Workload Identity Pool provider must have an attribute condition that matches the GitHub OIDC claim, and the service account must grant `roles/iam.workloadIdentityUser` to the pool's principalSet. Details belong in the `gcp-iam` skill; this skill only validates the GitHub side.

### Azure — `azure/login`

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

Federated credentials on the Entra app registration must include the subject (`repo:ORG/REPO:ref:refs/heads/main` or env form) and audience `api://AzureADTokenExchange`.

## GITHUB_TOKEN permissions model

Every workflow run gets a scoped `GITHUB_TOKEN`. Its default permissions are controlled at three levels (most-specific wins):

1. Org / repo setting "Default workflow permissions" — either `read` or `read-and-write`.
2. Workflow-level `permissions:` block.
3. Job-level `permissions:` block.

**Recommended pattern:**

```yaml
permissions: {}   # explicit empty — nothing by default

jobs:
  build:
    permissions:
      contents: read
  release:
    permissions:
      contents: write     # to create tags / releases
      id-token: write     # to mint OIDC for signing
```

Available scopes (most common): `actions`, `attestations`, `checks`, `contents`, `deployments`, `id-token`, `issues`, `packages`, `pages`, `pull-requests`, `security-events`, `statuses`. Each is `read | write | none`.

**Watch for:** `permissions: read-all` is convenient but grants read on every scope including `pull-requests` (PR contents can be sensitive) and `security-events` (vuln data). Prefer explicit per-scope.

**Fork PR caveat:** for workflows triggered by `pull_request` from a fork, `GITHUB_TOKEN` is read-only regardless of the `permissions:` block, and repo secrets are not exposed.

## pull_request vs pull_request_target

| Trigger | Runs in context of | Secrets available | Default checkout |
|---|---|---|---|
| `pull_request` | PR head (fork or branch) | No (on fork PRs) | PR head SHA |
| `pull_request_target` | Base repo at base ref | **Yes** | Base ref (NOT PR head) |

`pull_request_target` exists for safe automation on PR metadata — labeling, commenting, triaging — using base-repo code with secrets. The killer rule:

> **Do not check out PR head and execute its code under `pull_request_target`.**

The attacker controls everything in the PR head: `package.json` postinstall scripts, test files, Makefiles, anything that runs. If your workflow has `actions/checkout` with `ref: ${{ github.event.pull_request.head.sha }}` followed by `npm ci` or `make test`, secrets are exfiltrable.

**Safe patterns for "run tests with secrets on fork PRs":**

- Manual approval gate: GitHub Settings → Actions → "Require approval for all outside contributors." Maintainer approves the run after eyeballing the diff.
- Two-workflow split: a `pull_request` workflow runs untrusted code with no secrets; a separate `workflow_run` triggered on its completion can promote to a `pull_request_target` job that consumes its outputs but doesn't execute fork code.
- Deployment environment with required reviewers (works for any trigger).

## Reusable workflows vs composite actions vs matrix

Pick by what's being reused:

| Need | Use |
|---|---|
| Same job graph across many repos (build → scan → deploy) | **Reusable workflow** (`workflows/foo.yml` with `on: workflow_call`) |
| Same sequence of steps inside one job | **Composite action** (`action.yml` with `runs.using: composite`) |
| Same job across N variants (Node 18/20/22, ubuntu/macos) | **Matrix** |

**Reusable workflow secrets:**

```yaml
# caller
jobs:
  deploy:
    uses: my-org/shared/.github/workflows/deploy.yml@v1
    with:
      env: prod
    secrets: inherit          # passes all caller secrets
    # OR explicit:
    # secrets:
    #   AWS_ROLE: ${{ secrets.AWS_ROLE }}
```

`secrets: inherit` works only across same-org / same-enterprise repos. Cross-org callers must list secrets explicitly.

**Composite actions can't have their own secrets or permissions** — they inherit the calling job's. If you need to grant `id-token: write` for the composite's OIDC step, declare it on the caller job.

## Concurrency for deploys

Two deploys to the same environment must not run in parallel. The pattern:

```yaml
concurrency:
  group: deploy-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false
```

- `cancel-in-progress: false` for deploys — never cancel a half-finished deploy mid-flight. Queue instead.
- `cancel-in-progress: true` for PR CI — when a new commit lands, kill the older run to save minutes.
- Group on `github.ref` (per-branch queue) for branch-aware concurrency, or on a fixed string (`deploy-prod`) for global per-environment queueing.

GitHub enforces at most one running + one queued per group; further pushes overwrite the queued slot. If you need a longer queue, drive it from outside (a job queue, manual approval steps).

## Caching

`actions/cache` — verify current major via Context7 (`/actions/cache`); cache scope semantics changed in v4. Key strategy:

```yaml
- uses: actions/cache@v4
  with:
    path: |
      ~/.npm
      node_modules
    key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}
    restore-keys: |
      ${{ runner.os }}-node-
```

**Rules:**

- `key` must change whenever the cached contents would differ. Always hash the lockfile, not just `package.json`.
- `restore-keys` provides partial-hit fallback. Without it, a lockfile change forces a clean cache install.
- A cache hit is reported in the action's logs as `Cache hit for: <key>`. If the hit happens but install still runs, the cache path is wrong (e.g., `node_modules` not actually populated) — verify the `path:` matches where the tool writes.
- **Cache scope:** since v4, caches are scoped to a branch + its base. PR branches read main's cache but writes don't pollute it. This is a feature, not a bug — earlier intuition that "we should see PR caches across the repo" is wrong.
- **No secrets in cache keys.** Cache contents and keys are listed in the repo's cache UI.

For language ecosystems, prefer the pre-built `setup-node` / `setup-python` / `setup-go` caching options over a raw `actions/cache` — they get the path right.

## The path-filter / required-check trap

```yaml
on:
  pull_request:
    paths:
      - 'src/**'
```

If `lint` is a required status check in branch protection and the PR touches only `docs/`, the lint job never runs, so no status is reported, and the PR is stuck "Expected — Waiting for status."

**Fixes (pick one):**

1. **Always-run shim job.** Drop the `paths:` filter from the trigger. Inside the job, gate the real work with `if:` based on a path-filter step (e.g., `dorny/paths-filter`). On no-match, exit 0. The check posts success either way.
2. **Two jobs, same name.** One job runs only when paths match and does real work. A second job runs on the inverse and is a no-op `exit 0`. Both report under the required-check name. (Brittle — GitHub's matching by exact name.)
3. **Switch to rulesets.** GitHub repository rulesets can require a check only when it actually runs, unlike classic branch protection. This is the modern fix and avoids workflow contortions.

**Anti-fixes:**

- `continue-on-error: true` — reports failure-but-non-blocking, not success. Won't satisfy the required check.
- Removing the path filter and running lint on every PR — defeats the purpose.

## Environments + protection rules

A `jobs.<id>.environment: prod` line activates environment-scoped controls:

- Required reviewers (1–6 people) — pauses the job until approved.
- Wait timer (1–43200 minutes).
- Deployment branch rules — only listed branches can deploy.
- Environment-scoped secrets and variables (override repo-level).

For production deploys, the canonical shape is:

```yaml
jobs:
  deploy:
    environment:
      name: production
      url: https://app.example.com
    permissions:
      id-token: write
      contents: read
    # ...
```

Pair with an OIDC trust policy that uses the environment-scoped `sub` claim (`repo:ORG/REPO:environment:production`) — this limits role assumption to runs that have actually cleared the environment gate.

## Supply chain

- **Pin third-party actions to commit SHA**, not tags. A tag is a movable pointer; a SHA is immutable. Format: `uses: someone/foo@a1b2c3d4...`. Add a comment with the tag for human readability: `uses: someone/foo@a1b2c3d4  # v2.1.0`.
- **Enable Dependabot for actions** (`.github/dependabot.yml` with `package-ecosystem: github-actions`). It opens PRs to bump SHAs while keeping pinning intact.
- **Restrict which actions can run.** Org / repo Actions settings → "Allow actions and reusable workflows" → "Allow select actions" → list the trusted publishers. Stops typosquats and abandoned forks.
- **Never run untrusted action inputs through shell interpolation.** `run: echo "${{ github.event.issue.title }}"` is a command injection. Use env vars instead:
  ```yaml
  env:
    TITLE: ${{ github.event.issue.title }}
  run: echo "$TITLE"
  ```

## Self-hosted runners

The default-deny rule: **never use self-hosted runners on public repos with `pull_request` from forks.** Fork PRs can run arbitrary code on the runner, which then has network access to your internal infra.

For private repos, self-hosted is fine, but:

- Use ephemeral runners (single-job lifetime) so PR runs don't leave state.
- Run each repo's runners in an isolated environment — don't share a runner pool across repos with different trust levels.
- For Kubernetes-hosted runners, prefer Actions Runner Controller (ARC) over hand-rolled.

## Quick checklist before merging a workflow

- [ ] Top-level `permissions:` set explicitly, not relying on org default.
- [ ] `id-token: write` only where OIDC is used.
- [ ] Third-party actions pinned to SHA; first-party pinned to `@vN`.
- [ ] No `pull_request_target` checking out and running PR head code.
- [ ] Deploy jobs have a `concurrency:` group with `cancel-in-progress: false`.
- [ ] Required-check jobs always report — no silent skips via `paths:` unless using rulesets.
- [ ] Untrusted strings (`github.event.*` from issues/PRs) routed through `env:`, not interpolated into `run:`.
- [ ] Secrets only on jobs that need them; `pull_request` fork triggers do not get secrets and the workflow accounts for that.
