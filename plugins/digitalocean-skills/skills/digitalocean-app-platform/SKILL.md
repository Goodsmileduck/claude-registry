---
name: digitalocean-app-platform
description: Lints DigitalOcean App Platform app specs (app.yaml / doctl apps spec JSON / digitalocean_app Terraform) for security, reliability, correctness, and sizing anti-patterns — plaintext secrets, missing health checks, single-instance services, dev databases in production, port mismatches, overlapping ingress routes, conflicting git/image sources, deprecated routes, unknown instance sizes, and app/database region mismatch. Use when working with DigitalOcean App Platform, app.yaml, .do/app.yaml, doctl apps, the digitalocean_app Terraform resource, or reviewing an App Platform deployment for problems.
---

# DigitalOcean App Platform

Reviews App Platform app specs for the mistakes that cause downtime, leaked
secrets, and broken routing. Ships a stdlib-only validator, `do_app_spec_lint.py`,
that ingests the spec as JSON (recommended), the block-YAML DO emits, or the
`digitalocean_app` Terraform resource, and reports findings with a rule id,
severity, and a one-line fix.

## When to invoke

- Reviewing or authoring an `app.yaml` / `.do/app.yaml` / `digitalocean_app`.
- A service has downtime on deploy or flaps with no warning (health check / HA).
- DigitalOcean warns that `routes` is deprecated.
- A credential may be sitting in an env `value` in plaintext.
- Ingress routing behaves unexpectedly (overlapping prefixes).

## Cross-cutting rules

1. **Prefer JSON input.** `doctl apps spec get <app-id> --format json` is the
   most reliable input; the YAML path is a subset parser and rejects anchors,
   flow collections, and folded/literal scalars.
2. **Never put a literal secret in an env `value`.** Use `type: SECRET` and a
   `${VAR}` substitution. Values containing `${...}` (GitHub secrets, `${db.X}`
   bindable refs, `${APP_URL}` app-wide vars) are references, not literals.
3. **The app spec is the source of truth.** App Platform reconciles to the spec
   on every deploy; fix the spec, not the running app.

## Running the validator

```bash
# JSON (recommended)
doctl apps spec get <app-id> --format json > spec.json
python3 scripts/do_app_spec_lint.py spec.json

# YAML subset, or Terraform — format auto-detected by extension/content
python3 scripts/do_app_spec_lint.py .do/app.yaml
python3 scripts/do_app_spec_lint.py main.tf

# machine-readable
python3 scripts/do_app_spec_lint.py spec.json --format json
```

Exit 0 = clean or warnings only; 1 = at least one error-severity finding;
2 = unreadable/unparseable input.

## Checks

- **Secrets** — `secret-not-encrypted` (literal secret with type != SECRET),
  `secret-build-scope` (SECRET scoped RUN_AND_BUILD_TIME leaks into the build).
- **Reliability** — `no-health-check`, `single-instance` (one instance, no
  autoscaling), `dev-db-as-prod` (database with production: false).
- **Correctness** — `port-mismatch`, `route-overlap`, `source-conflict` (both
  git and image), `deprecated-routes`.
- **Sizing** — `unknown-instance-slug`, `db-region-mismatch`.

## Proactive triggers

- env `value` is a literal API key/token/password (type != SECRET) → flag
  `secret-not-encrypted`; move to `type: SECRET` + `${VAR}`.
- a `service` has `instance_count: 1` and no `autoscaling` → warn single point
  of failure.
- a `service` has no `health_check.http_path` → warn deploys can't detect
  unhealthy instances.
- both a git source and an `image` on one component → flag `source-conflict`.
- component-level `routes` present → recommend `spec.ingress.rules`.
- `production: false` on a database backing real traffic → warn dev database.
