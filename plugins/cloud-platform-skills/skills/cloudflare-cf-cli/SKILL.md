---
name: cloudflare-cf-cli
description: Operates Cloudflare's new unified `cf` CLI (technical preview, April 2026) — install path, flag conventions, the local-vs-remote default trap, coexistence with Wrangler and `wrangler.jsonc`, and agent-mode usage via the Local Explorer OpenAPI. Use when the user mentions `cf`, `npx cf`, "the new Cloudflare CLI", or is choosing between `cf` / `wrangler` / REST / Terraform.
---

# Cloudflare `cf` CLI

`cf` is Cloudflare's new unified CLI, announced April 2026, intended to eventually become the next major version of Wrangler. It exposes ~3,000 API operations across 100+ products through one consistent command surface. This skill is about using it correctly while it is still a technical preview.

## When to invoke

- The user mentions `cf`, `npx cf`, or "the new Cloudflare CLI" / "CLI for all of Cloudflare".
- A script or workflow is about to replace `wrangler ...` with `cf ...`.
- An agent is generating `cf` commands and may not know the flag conventions.
- A `cf` invocation behaved as if it targeted production when the user expected local.
- The user is choosing between `cf`, `wrangler`, direct REST, the official `cloudflare` SDK, or Terraform.

## Cross-cutting rules

1. **`cf` is technical preview.** Only a small subset of products is wired up. Conventions can shift between releases. **`wrangler` stays authoritative for CI/CD until `cf` reaches GA.** Use `cf` for local exploration, ad-hoc agent work, and feedback. If `cf` must be used in a script, pin the exact version — never unpinned `npx cf` in CI.
2. **`cf` defaults to the REMOTE resource.** Without `--local`, every mutation hits production. See [the local-vs-remote trap](#the-local-vs-remote-trap).
3. **Token scoping rules are identical to the `cloudflare-dns-zones` skill** — never the Global API Key, scope to minimum permissions on specific zones/accounts. For `cf` additionally: prefer a separate non-prod token for local work, so an accidental remote write fails 403 instead of corrupting production.
4. **Don't guess flag names by analogy.** `cf` enforces consistent flags across the surface (`--force`, `--json`, `--local`). Verify with `cf <command> --help` rather than reaching for the synonym another CLI uses.

Verify current preview status before recommending: <https://blog.cloudflare.com/cf-cli-local-explorer/>. Training data ages fast on preview tools.

## Don't confuse `cf` with neighbors

| Tool | What it is | Use for |
|---|---|---|
| `cf` (this skill) | Cloudflare's new official unified CLI (preview). npm package: `cf`. | Future Wrangler replacement. |
| `wrangler` | Current official CLI for Workers / KV / R2 / D1 / Pages. | Production CI/CD today. |
| `c3` / `create-cloudflare` | `npm create cloudflare@latest` — project scaffolder. | Bootstrap a new Workers / Pages project. |
| `cfcli` (`cloudflare-cli`) | Unofficial third-party CLI. Config at `~/.cfcli.yml`, different conventions. | Not recommended — out of scope. |
| `cloudflare` (TS SDK) | Official TypeScript SDK. Env: `CLOUDFLARE_API_TOKEN`. | Building tools against the API. |

If the user says "I installed `cf` and it does X" but X matches `cfcli`'s shape (`~/.cfcli.yml`, `CF_API_KEY` env), they probably installed the wrong package. Have them check `which cf` and the npm package name.

## Install and auth

```bash
npx cf <command>                    # one-off
npm install -g cf                   # global
npm install -g cf@<version>         # pinned — use this in scripts
cf --version                        # verify binary
```

Auth: `cf` is built on the same API as Wrangler and the official SDK. Treat `CLOUDFLARE_API_TOKEN` as canonical until `cf --help` on your installed version documents otherwise.

## Flag conventions

| Intent | `cf` flag | Common wrong guess |
|---|---|---|
| Read a resource | `get` (subcommand) | `info`, `describe`, `show` |
| Suppress confirmation prompts | `--force` | `--yes`, `--no-prompt`, `--skip-confirmations` |
| Machine-readable output | `--json` | `--format=json`, `--output=json`, `-o json` |
| Operate on local simulated resource | `--local` | `--preview`, `--env=local`, `--dev` |

`--json` is intended to be supported on every command but the preview may not cover all of them yet. If a command lacks `--json`, fall back to REST + jq for that one operation rather than parsing human output.

## The local-vs-remote trap

`cf` mutations target production unless `--local` is passed. Example:

```bash
# Developer running `wrangler dev` in another terminal, expects this to seed local KV:
cf kv put session:abc '{"user":"test"}'      # ❌ writes to PRODUCTION

# Correct — seeds the simulated namespace Local Explorer / wrangler dev see:
cf kv put session:abc '{"user":"test"}' --local
```

Guardrails:

1. **Non-prod token for local work** (see cross-cutting rule 3). A 403 on accidental remote writes beats silent corruption.
2. **Prefer Local Explorer for local seeding.** Press `e` in the `wrangler dev` terminal, or point the agent at `/cdn-cgi/explorer/api` (OpenAPI spec advertised at that URL). Local Explorer is local-only by construction — no `--local` to forget.
3. **In review:** any `cf` mutation (`put`, `delete`, `create`, `update`) without `--local` is production-targeting. Confirm intent.

There is no global config knob in the preview to flip the default. `--local` is per-invocation.

## Coexistence with Wrangler

`cf` shares `wrangler.jsonc` — the same config describes bindings, routes, and compatibility flags for both tools.

In practice during preview:

- A repo can use `wrangler` for `deploy`/`dev` and `cf` for ad-hoc reads of resources Wrangler has no command for (because `cf` covers more API surface).
- `cf` reading a KV namespace defined as a Wrangler binding works because both tools resolve `wrangler.jsonc` the same way.
- Don't mix them inside one CI job. Pick one per workflow step; mixing makes failures harder to debug when convention drift hits.

## Agent-mode usage

`cf` is designed to be agent-usable. Two patterns:

1. **Invoke `cf` directly.** `--json` is the machine contract. Don't scrape human output.
2. **Point the agent at the Local Explorer OpenAPI.** When `wrangler dev` is running, `<dev-host>:<port>/cdn-cgi/explorer/api` advertises an OpenAPI spec covering the simulated KV / R2 / D1 / Durable Objects / Workflows. An agent that reads OpenAPI can manage local resources without `cf` installed at all — useful for sandboxed agents.

## When to use what

| Task | Best tool today |
|---|---|
| Deploy a Worker in CI | `wrangler deploy` |
| Local dev / hot reload | `wrangler dev` or Cloudflare Vite plugin |
| Seed/inspect local KV/R2/D1 during dev | Local Explorer (`e` key, or `/cdn-cgi/explorer/api`) |
| Ad-hoc inspection of remote KV/R2/D1 from terminal | `cf get` / `cf list` (preview), or REST + jq |
| DNS record CRUD in CI | REST API + jq — see `cloudflare-dns-zones` skill |
| Multi-resource infra-as-code | Terraform (`cloudflare/cloudflare` provider) |
| Building tools against the API | Official `cloudflare` TypeScript SDK |
| Anything not yet in `cf` | REST API directly |

Don't reach for `cf` in CI just because it's new. Reach for it when it shortens a script you'd otherwise write with curl + jq — and pin the version when you do.

## Anti-patterns

- ❌ Unpinned `npx cf` in CI. Pin the version or use `wrangler`.
- ❌ Treating `cf` and `cfcli` as the same tool. Different package, different conventions (`~/.cfcli.yml`, `CF_API_KEY`), different auth model.
- ❌ `cf` mutation (`put`, `delete`, `create`, `update`) without `--local` in a dev context. Default is remote.
- ❌ Parsing `cf` human output. Use `--json` or fall back to REST.
- ❌ Mixing `cf` and `wrangler` in the same CI job during preview.
- ❌ Using flag analogues from other CLIs (`--yes`, `--format=json`, `--preview`). The documented conventions are `--force`, `--json`, `--local` — verify with `cf <cmd> --help`.
- ❌ Recommending `cf` for DNS record CRUD when REST + the `cloudflare-dns-zones` skill is the proven path.

## Cross-skill notes

- For DNS-specific work via REST, see the `cloudflare-dns-zones` skill — GA, unaffected by `cf`'s preview status.
- For R2 endpoint detection and credentials versus generic S3, see the `cloud-storage-identification` skill.
- For Terraform-managed Cloudflare resources, the `terraform-workflows` skill covers plan review and provider-upgrade discipline.
- A `cloudflare-local-explorer` companion skill is planned; until then, [agent-mode usage](#agent-mode-usage) is the brief.
