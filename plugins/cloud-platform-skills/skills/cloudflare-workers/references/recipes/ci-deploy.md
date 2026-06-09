# Deploying Workers from CI

> Parent: `../../SKILL.md`. Workers-specific CI bits. For OIDC, GITHUB_TOKEN permissions, concurrency, and reusable-workflow posture see the `github-actions-pipelines` skill.

## Table of contents

1. [Token vs OIDC](#token-vs-oidc)
2. [API token scopes](#api-token-scopes)
3. [Minimal GitHub Actions deploy](#minimal-github-actions-deploy)
4. [Multi-env deploys](#multi-env-deploys)
5. [Setting secrets from CI](#setting-secrets-from-ci)
6. [D1 migrations in CI](#d1-migrations-in-ci)
7. [Smoke-testing after deploy](#smoke-testing-after-deploy)
8. [`wrangler deploy` flags worth knowing](#wrangler-deploy-flags-worth-knowing)

## Token vs OIDC

Cloudflare does not yet support OIDC federation for Workers deploys (as it does for AWS/GCP). CI must hold a Cloudflare API token. Treat the token like the deploy-grade secret it is: rotate periodically, scope tight, and store in the CI secret manager (`secrets.CLOUDFLARE_API_TOKEN`).

When Cloudflare ships OIDC for Workers, replace the token with federated credentials — the action interface is stable enough that the diff will be small.

## API token scopes

Minimum scopes for `wrangler deploy`:

- `Account: Workers Scripts: Edit`
- `Account: Workers Routes: Edit` (if attaching routes)
- `Zone: Workers Routes: Edit` (if zone-scoped routes)
- `Zone: Zone: Read` (any zone the Worker routes against)

Add as needed:

- `Account: Workers KV Storage: Edit` (for `wrangler kv namespace create` from CI; not for runtime reads)
- `Account: Workers R2 Storage: Edit` (for bucket create from CI)
- `Account: D1: Edit` (for migrations)
- `Account: Account Settings: Read`
- `User: User Details: Read`

Scope to the specific account ID in the token's "Account Resources" field. Don't grant `All accounts`.

## Minimal GitHub Actions deploy

```yaml
name: deploy
on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
      - run: npm ci
      - run: npm run build
      - uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          command: deploy
```

Verify the action's current major via Context7 (`/cloudflare/wrangler-action`) before pasting. The action also accepts `environment:` for `--env <name>` deploys, `secrets:` (newline-separated names sourced from env) for bulk secret upload, and `command:` for arbitrary `wrangler` invocations.

## Multi-env deploys

Two patterns:

**Pattern A — one workflow, branch-driven env:**

```yaml
- uses: cloudflare/wrangler-action@v3
  with:
    apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    command: deploy --env ${{ github.ref == 'refs/heads/main' && 'production' || 'staging' }}
```

**Pattern B — GitHub environment + protection rules:**

```yaml
jobs:
  deploy-production:
    environment: production
    runs-on: ubuntu-latest
    steps:
      - uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          environment: production
          command: deploy
```

Pattern B lets GitHub require approval for production deploys via `environment` protection rules. Each GitHub environment can hold its own `CLOUDFLARE_API_TOKEN` if you want per-env tokens (scope each token to that env's needs).

## Setting secrets from CI

`wrangler-action`'s `secrets:` input uploads named secrets to the Worker. Values come from the workflow's `env:`.

```yaml
- uses: cloudflare/wrangler-action@v3
  env:
    STRIPE_SECRET_KEY: ${{ secrets.STRIPE_SECRET_KEY }}
    DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
  with:
    apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    environment: production
    secrets: |
      STRIPE_SECRET_KEY
      DB_PASSWORD
    command: deploy --env production
```

The action effectively runs `wrangler secret put STRIPE_SECRET_KEY --env production <<< "$STRIPE_SECRET_KEY"` for each name. Avoids drift between repo-tracked deploys and dashboard-managed secrets.

## D1 migrations in CI

D1 migrations don't run from `wrangler deploy`. Add a step:

```yaml
- uses: cloudflare/wrangler-action@v3
  with:
    apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
    command: d1 migrations apply app-production --env production --remote
```

`--remote` is essential — without it, Wrangler tries to apply against a local SQLite file (which doesn't exist on the runner) and silently no-ops.

Order: run migrations BEFORE deploying the Worker that depends on the new schema. Roll back by deploying the previous Worker version if the migration is forward-compatible; otherwise prepare a down-migration before applying.

## Smoke-testing after deploy

```yaml
- name: smoke
  run: |
    set -euo pipefail
    code=$(curl -s -o /tmp/body -w "%{http_code}" https://api.example.com/health)
    [ "$code" = "200" ] || { cat /tmp/body; exit 1; }
```

Cheap; catches most "deploy succeeded but didn't actually deploy" failures (account ID mismatch, env mismatch, route not attached). Don't skip.

## `wrangler deploy` flags worth knowing

- `--dry-run --outdir=./dist-deploy` — bundles without publishing. Use in CI for PR builds to catch errors without deploying.
- `--keep-vars` — preserve `vars` already on the deployed Worker; do NOT overwrite from config. Rare; usually you want config to be the source of truth.
- `--no-bundle` — skip esbuild step; use when you've already bundled with your own toolchain.
- `--env <name>` — deploy under the env's name suffix (`<name>-<env>`). Reads `env.<env>` from config.
- `--minify` — minify the bundle. On by default in recent Wrangler.
- `--compatibility-date YYYY-MM-DD` — override the config value. Useful for emergency rollback to a known-good date without editing the config.
