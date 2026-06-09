# Bindings cheat sheet

> Parent: `../../SKILL.md`. One section per binding type with the wrangler block, the `env.<NAME>` shape, and the dev-mode gotcha.

## Table of contents

1. [KV namespaces](#kv-namespaces)
2. [R2 buckets](#r2-buckets)
3. [D1 databases](#d1-databases)
4. [Queues](#queues)
5. [Durable Objects](#durable-objects)
6. [Service bindings + WorkerEntrypoint RPC](#service-bindings--workerentrypoint-rpc)
7. [Workers AI](#workers-ai)
8. [Vectorize](#vectorize)
9. [Hyperdrive](#hyperdrive)
10. [Analytics Engine](#analytics-engine)
11. [`vars` and secrets](#vars-and-secrets)
12. [`remote` flag for local dev](#remote-flag-for-local-dev)

## KV namespaces

```jsonc
"kv_namespaces": [
  { "binding": "CACHE", "id": "abc123def..." }
]
```

Access: `await env.CACHE.get("key")`, `env.CACHE.put("key", "value", { expirationTtl: 3600 })`.

**Gotcha:** KV reads are eventually consistent globally (up to ~60s propagation). Don't poll KV for "config refresh" expecting fresh writes from another Worker — use a Durable Object for synchronized state, or redeploy with the new value baked in.

**Dev:** `wrangler dev` uses local KV in `.wrangler/state/`. For real KV reads/writes use `--remote` or `"remote": true` per binding.

## R2 buckets

```jsonc
"r2_buckets": [
  { "binding": "ASSETS_BUCKET", "bucket_name": "my-bucket" }
]
```

Access: `await env.ASSETS_BUCKET.get("path/key")`, `.put()`, `.list({ prefix })`.

**Gotcha:** Public access has two paths — `r2.dev` (per-bucket subdomain, no custom CORS, dev-only really) vs Custom Domain (full CORS, Cache headers, production-grade). They're separate toggles. CORS config doesn't apply to `r2.dev`.

**Gotcha:** Bucket-scoped API tokens exist; prefer them over account tokens for CI.

## D1 databases

```jsonc
"d1_databases": [
  { "binding": "DB", "database_name": "app-prod", "database_id": "..." }
]
```

Access: `await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(id).first()`.

**Gotcha:** Schema migrations are separate. `wrangler d1 migrations create <name> <descr>` → write SQL in `migrations/` → `wrangler d1 migrations apply <name> --env production`. `wrangler deploy` does NOT apply migrations.

**Gotcha:** `database_id` must match per env. Common to have `app-dev`, `app-staging`, `app-prod` as separate databases.

## Queues

```jsonc
"queues": {
  "producers": [
    { "binding": "JOBS", "queue": "jobs-q" }
  ],
  "consumers": [
    { "queue": "jobs-q", "max_batch_size": 10, "max_batch_timeout": 30, "max_retries": 3, "dead_letter_queue": "jobs-dlq" }
  ]
}
```

Producer access: `await env.JOBS.send({ payload })`.
Consumer: implement `async queue(batch, env, ctx)` handler.

**Gotcha:** Producers and consumers are separate. A single Worker can be both. Consumer config (batch size, timeout, DLQ) lives on the consuming Worker's wrangler config, not on the queue itself.

## Durable Objects

```jsonc
"durable_objects": {
  "bindings": [
    { "name": "ROOMS", "class_name": "ChatRoom" }
  ]
},
"migrations": [
  { "tag": "v1", "new_sqlite_classes": ["ChatRoom"] }
]
```

Access: `const id = env.ROOMS.idFromName("room-42"); const stub = env.ROOMS.get(id); await stub.fetch(req);` or RPC: `await stub.broadcast(msg)`.

**See `durable-objects.md` for the full migrations story.**

## Service bindings + WorkerEntrypoint RPC

```jsonc
"services": [
  { "binding": "BILLING", "service": "billing-worker", "entrypoint": "BillingAPI" }
]
```

Worker B exposes a named entrypoint:

```typescript
import { WorkerEntrypoint } from "cloudflare:workers";

export class BillingAPI extends WorkerEntrypoint {
  async chargeCustomer(customerId: string, cents: number) {
    // ...
    return { ok: true, chargeId: "..." };
  }
}

export default { async fetch() { /* main handler */ } };
```

Worker A calls it:

```typescript
const result = await env.BILLING.chargeCustomer("cus_1", 100);
```

**Gotcha:** To target a specific env of Worker B, use the deployed name: `"service": "billing-worker-production"` (matches Worker B's `name-<env>` suffix). Service bindings respect envs but only via name.

**Gotcha:** `WorkerEntrypoint` RPC supports structured-clone-able args + return values. Returning a `Response` works; returning a class with methods works (proxy). Returning a function does NOT.

**Gotcha:** Worker B does NOT need to be publicly routed. It can have `"workers_dev": false` and no routes and still receive service-binding traffic.

## Workers AI

```jsonc
"ai": { "binding": "AI" }
```

Access: `await env.AI.run("@cf/meta/llama-3-8b-instruct", { messages: [...] })`. The block intentionally has no `name`/`id` — just `binding`.

## Vectorize

```jsonc
"vectorize": [
  { "binding": "VECTORS", "index_name": "embeddings" }
]
```

Index created out-of-band: `wrangler vectorize create embeddings --dimensions=1536 --metric=cosine`. The binding then attaches to that index.

## Hyperdrive

```jsonc
"hyperdrive": [
  { "binding": "POSTGRES", "id": "..." }
]
```

Hyperdrive is a Cloudflare-managed connection pooler + query cache for external Postgres. Connection string is configured at the Hyperdrive resource level (out of band); the Worker sees a normal Postgres connection.

**Gotcha:** Hyperdrive caches based on the literal SQL. Parameterized queries cache well; string-concatenated queries don't.

## Analytics Engine

```jsonc
"analytics_engine_datasets": [
  { "binding": "ANALYTICS", "dataset": "app_events" }
]
```

Access: `env.ANALYTICS.writeDataPoint({ blobs: [...], doubles: [...], indexes: [...] })`. Pull data via SQL API.

## `vars` and secrets

```jsonc
"vars": {
  "PUBLIC_URL": "https://example.com",
  "FEATURE_X_ENABLED": "true"
}
```

> SKILL.md Hard rule 3 applies. `vars` is plaintext config; secrets go through `wrangler secret put`.

Both surface in the Worker via `env.NAME`. For local dev, drop secret-shaped values into a `.dev.vars` file (gitignored) — `wrangler dev` reads them.

## `remote` flag for local dev

Newer Wrangler versions let you pin individual bindings to use the real Cloudflare resource during `wrangler dev`:

```jsonc
"kv_namespaces": [
  { "binding": "CACHE", "id": "prod-kv-id", "remote": true }
]
```

Without it, `wrangler dev` uses local emulation per binding. Use `--remote` on the command line to force all bindings remote. Mixing — some remote, some local — is supported and useful for fast iteration on Worker logic against real production KV/R2.
