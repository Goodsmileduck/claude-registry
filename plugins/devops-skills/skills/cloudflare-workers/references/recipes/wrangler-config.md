# Wrangler configuration deep-dive

> Parent: `../../SKILL.md`. The env-inheritance trap and the full set of non-inheritable keys.

## Table of contents

1. [toml vs jsonc](#toml-vs-jsonc)
2. [The `$schema` pointer](#the-schema-pointer)
3. [Non-inheritable keys (the override trap)](#non-inheritable-keys-the-override-trap)
4. [Two structural options when using envs](#two-structural-options-when-using-envs)
5. [Routes vs Custom Domains vs `workers_dev`](#routes-vs-custom-domains-vs-workers_dev)
6. [`compatibility_date` and flags](#compatibility_date-and-flags)
7. [Observability block](#observability-block)
8. [Cron triggers](#cron-triggers)
9. [Smart Placement](#smart-placement)

## toml vs jsonc

Same keys, same env semantics, both support comments. The only practical difference: jsonc supports `$schema` for IDE autocomplete/validation and is the new-project default since 2024.

Pick jsonc for new projects. Don't migrate working toml projects without a reason. **Format choice does NOT save you from the env-override trap.**

## The `$schema` pointer

```jsonc
{
  "$schema": "node_modules/wrangler/config-schema.json",
  // ...
}
```

Cloudflare ships a JSON Schema in the wrangler package. The IDE will underline unknown keys and provide autocomplete. Without it, typos in binding names live until deploy.

## Non-inheritable keys (the override trap)

> SKILL.md Hard rule 1 applies. The full list of non-inheritable keys is below; the rule's *why* lives in SKILL.md.

Full list (current as of wrangler v3+):

- `vars`
- `kv_namespaces`
- `r2_buckets`
- `d1_databases`
- `queues` (both `producers` and `consumers`)
- `durable_objects.bindings`
- `services`
- `analytics_engine_datasets`
- `vectorize`
- `hyperdrive`
- `mtls_certificates`
- `dispatch_namespaces`
- `send_email`
- `browser`
- `ai`
- `routes` / `route`
- `workers_dev`
- `placement`
- `triggers` (cron)

Inheritable (these DO carry into envs without restating):

- `name` (envs auto-suffix: `name-<env>`)
- `main`
- `compatibility_date`
- `compatibility_flags`
- `account_id`
- `usage_model`
- `limits`
- `observability`

## Two structural options when using envs

**Option A — root + per-env override (default in docs):**

```jsonc
{
  "name": "my-api",
  "main": "src/index.ts",
  "compatibility_date": "2025-05-01",
  // Root bindings = development defaults; `wrangler dev` uses these.
  "kv_namespaces": [{ "binding": "CACHE", "id": "dev-kv-id" }],
  "env": {
    "staging": {
      "kv_namespaces": [{ "binding": "CACHE", "id": "staging-kv-id" }],
      "routes": [{ "pattern": "api-staging.example.com/*", "zone_name": "example.com" }]
    },
    "production": {
      "kv_namespaces": [{ "binding": "CACHE", "id": "prod-kv-id" }],
      "routes": [{ "pattern": "api.example.com/*", "zone_name": "example.com" }]
    }
  }
}
```

Pro: root config doubles as the dev environment. Con: easy to forget repeating a binding when you add it; production loses it silently.

**Option B — empty root, everything under envs (safer):**

```jsonc
{
  "name": "my-api",
  "main": "src/index.ts",
  "compatibility_date": "2025-05-01",
  "env": {
    "dev": { "kv_namespaces": [{ "binding": "CACHE", "id": "dev-kv-id" }] },
    "staging": { /* ... */ },
    "production": { /* ... */ }
  }
}
```

Pro: impossible to forget — there's no root to forget from. `wrangler dev --env dev` for local. Con: slightly more verbose; `wrangler dev` without `--env` deploys an unconfigured Worker (which fails loudly — actually fine).

If a project has had a non-inheritable bug in production, switch to Option B.

## Routes vs Custom Domains vs `workers_dev`

Three independent ways a Worker can be reached:

1. **`workers.dev` subdomain** — `<name>.<account-subdomain>.workers.dev`. Toggle with `"workers_dev": true|false`. Free, always available; turn it off for production Workers that should only be on a custom hostname.
2. **Route patterns** — `"routes": [{ "pattern": "api.example.com/*", "zone_name": "example.com" }]`. Pattern can include path. Many routes can target one Worker. Requires the zone to be on Cloudflare.
3. **Custom Domain** — `"routes": [{ "pattern": "api.example.com", "custom_domain": true }]` or attached via dashboard. Cloudflare manages DNS + SSL for that exact hostname. The Worker becomes the origin; no route-pattern matching.

> SKILL.md Hard rule 5 applies — pick one per hostname. If both are configured, delete one before debugging anything else.

## `compatibility_date` and flags

> SKILL.md Hard rule 4 applies. Concrete examples of what bumping changes: `fetch()` redirect handling, `Request.signal` AbortController semantics, `nodejs_compat` vs `nodejs_compat_v2` defaults, streams behavior, error response shapes.

`compatibility_flags` opts in/out of specific behavior independently of the date:

```jsonc
{
  "compatibility_date": "2025-05-01",
  "compatibility_flags": ["nodejs_compat"]
}
```

Common flags:

- `nodejs_compat` / `nodejs_compat_v2` — Node built-ins polyfills (path, buffer, crypto, etc.).
- `streams_enable_constructors` — older flag, usually default now.
- `global_navigator` — set `navigator.userAgent`.

## Observability block

```jsonc
{
  "observability": { "enabled": true, "head_sampling_rate": 1 }
}
```

`enabled: true` ships logs to the Workers Logs dashboard. `head_sampling_rate` is between 0 and 1 — drop sampling in high-volume Workers to control cost. Inheritable across envs.

## Cron triggers

```jsonc
{
  "triggers": { "crons": ["*/5 * * * *", "0 0 * * *"] }
}
```

Cron strings are UTC. Implement the `scheduled(controller, env, ctx)` handler in the Worker. `triggers` IS non-inheritable — repeat per env if envs are used.

## Smart Placement

```jsonc
{
  "placement": { "mode": "smart" }
}
```

Cloudflare auto-places the Worker close to its main backend (origin, database) instead of close to the user. Good for backend-heavy Workers. Interacts with service bindings — bound Workers should share placement to avoid round-trip cost. `placement` is non-inheritable.
