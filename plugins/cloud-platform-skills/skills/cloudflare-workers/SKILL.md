---
name: cloudflare-workers
description: Authors and reviews Cloudflare Workers projects — wrangler config (toml/jsonc), bindings (KV, R2, D1, Queues, Durable Objects, service bindings, Vectorize, Workers AI), env-scoped vs root config and the non-inheritable bindings trap, Durable Object migrations (renames, SQLite backend), compatibility_date semantics, static assets and Pages migration, secrets vs vars, cron triggers, observability, and deploy/CI patterns with `cloudflare/wrangler-action`. Use when working with Cloudflare Workers, wrangler.toml/wrangler.jsonc, Workers bindings, Durable Objects, Workers KV/R2/D1/Queues, Workers Static Assets, migrating from Pages to Workers, service bindings or WorkerEntrypoint RPC, or deploying Workers from CI.
---

# Cloudflare Workers

## When to invoke

**Symptoms:**

- A binding works in `wrangler dev` but is `undefined` in production.
- `wrangler deploy` fails with `Cannot apply new-class migration to class 'X' that is already depended on by existing Durable Objects` or `Class 'X' cannot be used as a Durable Object` after a code rename.
- A secret value is visible in the Cloudflare dashboard under "Variables" — or worse, committed in `wrangler.toml`.
- Same hostname has both a Custom Domain and a route pattern, and routing is non-deterministic.
- New Pages project — should it actually be a Worker?
- Two Workers in the same account need to talk; engineer is about to wire it through a public URL.
- `compatibility_date` was bumped and something started 500-ing.
- `wrangler tail` shows the deploy succeeded but the new code isn't running.

**The trap this prevents:** treating `wrangler.toml`/`wrangler.jsonc` as docstring-like config. Several keys are non-inheritable across envs, several have implicit ordering, and the Durable Object migrations array is append-only with strict semantics. Most "it broke in prod" Workers incidents trace to one of these.

## Inputs to collect first

| Input | Why | Example |
| --- | --- | --- |
| Account ID | All Workers are account-scoped | dashboard sidebar |
| Worker name | Becomes `<name>.<subdomain>.workers.dev` and route target | `checkout-api` |
| Routes vs Custom Domain | Decide before deploying — they conflict on the same hostname | `api.example.com/*` (route) or `api.example.com` (custom domain) |
| Bindings list | KV / R2 / D1 / Queues / DOs / services / AI / Vectorize | `[{type: kv, id: ...}, {type: do, class: Counter}]` |
| Environments | `staging`, `production`, ephemeral PR envs | dictates env block structure |
| `compatibility_date` | Behavior pin; not metadata | `2025-05-01`, recent but not future |
| Static assets | Yes/no; SPA vs MPA; needs Worker handler? | `./dist`, SPA, no |

API token scopes for deploy: see `references/recipes/ci-deploy.md` for the full per-resource list.

## Config format

Use `wrangler.jsonc` for new projects. `wrangler init` defaults to it, it supports `$schema` for IDE autocomplete, and Cloudflare's docs lead with it. `wrangler.toml` is fully supported — no urgency to migrate.

Add `"$schema": "node_modules/wrangler/config-schema.json"` at the top of any jsonc file; the IDE will underline unknown keys.

Deep dive: `references/recipes/wrangler-config.md`.

## Hard rules

`scripts/validate_wrangler.py` flags rules 1–5 statically.

1. **Bindings are non-inheritable across envs.** *(Every binding type.)* A populated `env.production` block fully overrides the root config for non-inheritable keys (`vars`, `kv_namespaces`, `r2_buckets`, `d1_databases`, `queues`, `durable_objects.bindings`, `services`, `routes`/`route`). It does NOT merge. If you set KV at root and only routes under `env.production`, production deploys with zero KV bindings — silently. Either repeat per env, or move everything into envs and leave root empty.

2. **`migrations` is append-only and class-name driven.** *(Durable Objects.)* Every class referenced by a DO binding must appear in the cumulative `migrations` history as `new_classes` / `new_sqlite_classes` / the `to` side of `renamed_classes`. Renames need a new entry with a new `tag` — never edit a past entry. Switching backends (KV → SQLite) is one-way; `new_sqlite_classes` cannot be downgraded.

3. **Secrets are not `vars`.** *(All projects.)* `vars` ships in plaintext with the deploy bundle and is visible in the dashboard. Anything ending in `_KEY` / `_TOKEN` / `_SECRET` / `_PASSWORD` / `_PASSPHRASE` belongs in `wrangler secret put NAME` (per env if envs are used). Secrets persist across `wrangler deploy`; vars are overwritten.

4. **`compatibility_date` is behavior, not metadata.** Bumping it can change `fetch` redirect handling, `nodejs_compat` semantics, error formatting, and more. Read the changelog before bumping; pin to a date in the past, not the future (deploys reject future dates).

5. **Custom Domain and route patterns can collide.** A Custom Domain attached to `api.example.com` AND a `[[routes]]` pattern matching `api.example.com/*` produce undefined precedence. Pick one per hostname.

6. **Module syntax only.** Service Worker syntax (`addEventListener('fetch', ...)`) is deprecated. New `compatibility_date` values can refuse it. Use `export default { fetch(req, env, ctx) { ... } }`.

7. **Local `wrangler dev` uses local bindings.** Local KV / R2 / D1 / DO state lives in `.wrangler/state/`. It diverges silently from production. For real-binding behavior use `wrangler dev --remote`, or set `"remote": true` per-binding in newer Wrangler versions.

## Bindings

Each binding type has its own block in the config and its own dev-mode behavior. Per-binding shape, gotchas, and the `remote` flag for local dev: `references/recipes/bindings.md`.

## Durable Objects

Migrations array gotchas and the SQLite-vs-KV backend choice get a dedicated page: `references/recipes/durable-objects.md`. The five migration verbs (`new_classes`, `new_sqlite_classes`, `renamed_classes`, `deleted_classes`, `transferred_classes`) and `tag` semantics are easy to get subtly wrong.

## Static assets and Pages migration

`[assets]` block in a Worker now covers what Pages used to. `references/recipes/static-assets.md` covers the conversion, `not_found_handling` modes (SPA vs 404 page vs none), `run_worker_first` for auth interceptors, and DNS coordination during the cutover.

Don't migrate a working Pages project just to migrate — wait for a meaningful change. New projects should pick Workers + Static Assets directly.

## CI deploy

`references/recipes/ci-deploy.md` covers the Workers-specific deploy: `cloudflare/wrangler-action`, API token scopes, multi-env deploys, environment-scoped secrets, and the `--env` flag. For OIDC, GITHUB_TOKEN permissions, concurrency, and reusable-workflow posture see the `github-actions-pipelines` skill — that surface is shared with AWS/GCP deploys and not Workers-specific.

## Validator

```bash
# Validates wrangler.toml or wrangler.jsonc against the hard rules.
python3 scripts/validate_wrangler.py --config wrangler.jsonc
# --format json for CI; non-zero exit on findings.
```

Checks: env-override (rule 1), DO migrations coverage (rule 2), secret-shape vars (rule 3), future or missing `compatibility_date` (rule 4), route/custom_domain hostname overlap (rule 5), and an INFO for missing `$schema` in jsonc.

## Proactive triggers

- User pastes a `wrangler.jsonc` with bindings at root AND a populated `env.production`/`env.staging` block → check whether bindings are repeated per env; flag if not.
- User renames a Durable Object class in code without touching `migrations` → flag rule 2 before they try to deploy.
- User adds `STRIPE_*` / `_TOKEN` / `_SECRET` etc. to `vars` → suggest `wrangler secret put`.
- User attaches both a Custom Domain and a `[[routes]]` pattern to the same hostname → pick one.
- User has Worker A about to `fetch('https://worker-b...')` for an internal call → suggest service binding + RPC.
- User starts a new project with `wrangler.toml` and the `pages_build_output_dir` key → suggest Workers Static Assets (`[assets]` block) instead.
- User uses `wrangler publish` in CI → it's deprecated in favor of `wrangler deploy`.
- User bumps `compatibility_date` to today and 500s appear → ask which flags they changed.

## Verification

- [ ] `wrangler deploy --dry-run --outdir=./dist-deploy` succeeds and the `worker.js` looks right.
- [ ] `python3 scripts/validate_wrangler.py --config wrangler.jsonc` returns 0 findings.
- [ ] For each env: `wrangler deploy --env <env> --dry-run` succeeds.
- [ ] `curl -i https://<worker-route>/<health-path>` returns the expected response after deploy.
- [ ] `wrangler tail --env <env>` shows request logs for the production hostname.
- [ ] If DOs are used: `wrangler deploy` reports the migration tag applied, not a no-op.
- [ ] Secrets set per env: `wrangler secret list --env production` lists what you expect.

## Old patterns

- **Service Worker syntax** (`addEventListener('fetch', ...)`) — use modules (`export default { fetch }`).
- **`wrangler publish`** — renamed to `wrangler deploy`. Old command still works but is being removed.
- **One Pages project per app** for new projects — prefer Workers + `[assets]`.
- **Worker → Worker via `fetch('https://other-worker.workers.dev')`** — use service bindings.
- **DO classes without `migrations`** — required since launch; if you see a Worker that "just works" without it, you're looking at one of the legacy bindings.
- **`workers_dev = true` for production** — fine for staging; in production prefer a Custom Domain or zone route.
