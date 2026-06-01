# Workers Static Assets & Pages migration

> Parent: `../../SKILL.md`. The `[assets]` block, the `not_found_handling` modes, and how to port a Pages project.

## Table of contents

1. [What `[assets]` does](#what-assets-does)
2. [Minimal config](#minimal-config)
3. [`not_found_handling` modes](#not_found_handling-modes)
4. [`run_worker_first`](#run_worker_first)
5. [Static-only Worker (no fetch handler)](#static-only-worker-no-fetch-handler)
6. [Worker + assets (hybrid)](#worker--assets-hybrid)
7. [Porting a Pages project](#porting-a-pages-project)
8. [Custom domain cutover](#custom-domain-cutover)
9. [What's NOT covered](#whats-not-covered)

## What `[assets]` does

A Worker can attach a directory of static files (HTML, CSS, JS, images). Cloudflare's edge serves them directly — typically without invoking the Worker — and surfaces an `env.ASSETS.fetch(request)` binding for the Worker to delegate to when it wants.

It replaces the use case Cloudflare Pages was built for. For new projects, prefer this over Pages.

## Minimal config

```jsonc
{
  "name": "my-spa",
  "main": "src/index.ts",
  "compatibility_date": "2025-05-01",
  "assets": {
    "directory": "./dist",
    "binding": "ASSETS"
  }
}
```

- `directory` — built output, relative to the wrangler config file.
- `binding` — name inside `env.<NAME>`. Omit if the Worker never delegates to the asset server.

The asset server runs the standard request-routing logic: exact match first, then `.html` extension fallback, then `index.html` if `not_found_handling` is set.

## `not_found_handling` modes

```jsonc
"assets": {
  "directory": "./dist",
  "binding": "ASSETS",
  "not_found_handling": "single-page-application"
}
```

| Value | Behavior | When |
| --- | --- | --- |
| `none` (default) | 404 for unknown paths | MPA where each route maps to a real file |
| `404-page` | Serve `/404.html` with HTTP 404 status | Static site with a custom 404 page |
| `single-page-application` | Rewrite unknown paths to `/index.html` with HTTP 200 | SPAs using client-side routing (React Router, Vue Router) |

Vite/Next-static-export/CRA SPAs need `single-page-application`. Without it, deep-link refreshes 404.

## `run_worker_first`

By default the asset server tries to match a file first; the Worker only runs when no file matches. Reverse this with `run_worker_first`:

```jsonc
"assets": {
  "directory": "./dist",
  "binding": "ASSETS",
  "run_worker_first": ["/api/*", "/admin/*"]
}
```

The pattern array routes those paths through the Worker first; everything else hits the asset server first. Use when you need:

- Auth/cookie checks before serving anything (including `index.html`).
- A/B testing or feature-flag gating on static pages.
- Rewriting headers (CSP, security headers) before delivery.

Setting `run_worker_first: true` (boolean) routes ALL requests through the Worker first.

## Static-only Worker (no fetch handler)

If a Worker is purely a static site with no dynamic routes:

```jsonc
{
  "name": "marketing-site",
  "compatibility_date": "2025-05-01",
  "assets": { "directory": "./dist" }
}
```

No `main`, no `binding`, no Worker code at all. `wrangler deploy` ships just the assets. This is the closest Workers-equivalent to a static Pages project.

## Worker + assets (hybrid)

The typical SPA + API case:

```typescript
// src/index.ts
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      return handleAPI(request, env);
    }
    // Delegate to the asset server.
    return env.ASSETS.fetch(request);
  }
};
```

```jsonc
{
  "main": "src/index.ts",
  "assets": {
    "directory": "./dist",
    "binding": "ASSETS",
    "not_found_handling": "single-page-application"
  }
}
```

The Worker handles `/api/*`; everything else falls through to assets. SPA fallback handles client-side routes.

## Porting a Pages project

A Pages project with Functions in `functions/api/*` and a `_routes.json`:

**Pages config (before):**

```jsonc
{
  "name": "my-spa",
  "pages_build_output_dir": "./dist"
}
```

**Workers config (after):**

```jsonc
{
  "name": "my-spa",
  "main": "src/index.ts",
  "compatibility_date": "2025-05-01",
  "assets": {
    "directory": "./dist",
    "binding": "ASSETS",
    "not_found_handling": "single-page-application"
  }
}
```

Migration steps:

1. Delete `pages_build_output_dir`. Add `main` + `assets`.
2. Move Pages Function code from `functions/api/foo.ts` into the Worker's fetch handler. A router library (`hono`, `itty-router`) keeps this clean.
3. Replace `context.env`, `context.next()`, `context.waitUntil` (Pages Function ctx) with Worker `env`, `env.ASSETS.fetch`, `ctx.waitUntil` (Worker ctx).
4. `_routes.json` is no longer used — the Worker's fetch handler decides routing. `_headers` and `_redirects` are honored by the asset server but verify they still match what you need.
5. Switch deploy: `wrangler pages deploy ./dist` → `wrangler deploy`.
6. Per-environment Pages env vars become Worker env vars (and the env-override rule applies).

## Custom domain cutover

DNS is the trickiest part: Pages projects attach their custom domains in the Pages dashboard; Workers attach them via Workers Custom Domains or `routes`. You can't have BOTH the Pages project and the Workers Worker attached to the same hostname.

Cutover options:

- **Cut at the hostname level** — detach the domain from the Pages project, attach it to the Workers Worker. Brief gap; deploy + verify in advance on a staging hostname.
- **Cut at the DNS level** — temporarily point the apex/subdomain at a different Worker hostname (`*.workers.dev`), then attach the real one. More fiddly; usually unnecessary.

After cutover the Pages project can be deleted or left dormant.

## What's NOT covered

- **Image transformations** — separate product, configured at the zone level, not on the Worker.
- **Server-side rendering for frameworks** — Next.js / Remix / SvelteKit on Workers use framework-specific adapters; this skill covers the static-assets layer they sit on top of.
- **R2-backed asset hosting** — different pattern (Worker reads from R2 bucket). Use `[assets]` for files shipped with the Worker; use R2 for user uploads or large files outside the deploy bundle.
