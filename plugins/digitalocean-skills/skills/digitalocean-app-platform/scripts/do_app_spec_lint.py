#!/usr/bin/env python3
"""Linter for DigitalOcean App Platform app specs.

Accepts three input formats, all normalized to one canonical dict before
checks run:

  - JSON (primary): output of `doctl apps spec get --format json` or the API
    app `spec` object. Parsed with the stdlib json module.
  - Block-YAML subset (fallback): the indent-based YAML DigitalOcean emits in
    `.do/app.yaml`. NOT a full YAML parser — anchors, flow collections, and
    folded/literal block scalars are rejected with a clear error directing the
    user to pass JSON.
  - Terraform: `resource "digitalocean_app"` -> `spec { ... }`, via a small
    HCL block parser.

Checks (rule ids): secret-not-encrypted, secret-build-scope, no-health-check,
single-instance, dev-db-as-prod, port-mismatch, route-overlap, source-conflict,
deprecated-routes, unknown-instance-slug, db-region-mismatch.

Exit codes: 0 = clean (or warnings only), 1 = at least one error-severity
finding, 2 = usage/IO/parse error.
"""
import argparse
import json
import sys

COMPONENT_KINDS = ("services", "workers", "jobs", "functions", "static_sites")
# Component kinds that serve HTTP traffic and so warrant health-check / scaling
# checks. Jobs and functions are excluded (no long-running listener).
HTTP_KINDS = ("service", "worker", "static_site")


def _as_list(val):
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def _norm_env(e):
    return {
        "key": e.get("key"),
        "value": e.get("value"),
        "type": e.get("type"),
        "scope": e.get("scope"),
    }


def _norm_component(raw, kind_plural):
    kind = kind_plural.rstrip("s") if kind_plural != "static_sites" else "static_site"
    hc = raw.get("health_check")
    health = None
    if isinstance(hc, dict):
        health = {"http_path": hc.get("http_path"), "port": hc.get("port")}
    return {
        "kind": kind,
        "name": raw.get("name"),
        "instance_count": raw.get("instance_count"),
        "instance_size_slug": raw.get("instance_size_slug"),
        "http_port": raw.get("http_port"),
        "autoscaling": raw.get("autoscaling") if isinstance(raw.get("autoscaling"), dict) else None,
        "health_check": health,
        "has_git": any(raw.get(k) for k in ("git", "github", "gitlab")),
        "has_image": bool(raw.get("image")),
        "routes": [{"path": r.get("path")} for r in _as_list(raw.get("routes")) if isinstance(r, dict)],
        "envs": [_norm_env(e) for e in _as_list(raw.get("envs")) if isinstance(e, dict)],
    }


def _norm_ingress(raw):
    rules = []
    ingress = raw.get("ingress")
    if isinstance(ingress, dict):
        for rule in _as_list(ingress.get("rules")):
            if not isinstance(rule, dict):
                continue
            match = rule.get("match") or {}
            path = match.get("path") or {}
            comp = rule.get("component") or {}
            rules.append({"prefix": path.get("prefix"), "component": comp.get("name")})
    return {"rules": rules}


def _norm_database(d):
    return {
        "name": d.get("name"),
        "production": d.get("production"),
        "region": d.get("region"),
    }


def _normalize(raw):
    """Turn a raw spec dict (JSON/YAML/HCL origin) into the canonical shape."""
    if not isinstance(raw, dict):
        raise ValueError("spec root must be a mapping")
    # A spec may be wrapped as {"spec": {...}} (API response) — unwrap it.
    if "spec" in raw and isinstance(raw["spec"], dict) and not any(
            k in raw for k in COMPONENT_KINDS):
        raw = raw["spec"]
    components = []
    for plural in COMPONENT_KINDS:
        for c in _as_list(raw.get(plural)):
            if isinstance(c, dict):
                components.append(_norm_component(c, plural))
    return {
        "name": raw.get("name"),
        "region": raw.get("region"),
        "envs": [_norm_env(e) for e in _as_list(raw.get("envs")) if isinstance(e, dict)],
        "ingress": _norm_ingress(raw),
        "databases": [_norm_database(d) for d in _as_list(raw.get("databases")) if isinstance(d, dict)],
        "components": components,
    }


# --- check registry -------------------------------------------------------
# Each check: (normalized_spec) -> list[Finding]. Registered in CHECKS.
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def lint_spec(norm):
    findings = []
    for fn in CHECKS:
        findings.extend(fn(norm))
    return findings


# Env-key substrings that strongly imply the value is a credential.
SECRET_KEY_HINTS = (
    "SECRET", "TOKEN", "PASSWORD", "PASSWD", "APIKEY", "API_KEY",
    "PRIVATE_KEY", "ACCESS_KEY", "CLIENT_SECRET",
)
# A value-only signal: long, whitespace-free strings that mix letters+digits
# look like raw credentials. 24 chars avoids flagging short slugs/enum values
# while catching typical API keys/tokens (AWS keys are 20-40, JWTs longer).
MIN_SECRET_VALUE_LEN = 24


def _is_substitution(value):
    # ${VAR}, ${db.X}, ${APP_URL} are GitHub-secret / bindable / app-wide refs,
    # never literal secrets. Any value containing ${ is treated as a ref.
    return isinstance(value, str) and "${" in value


def _looks_like_secret_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("-----BEGIN"):
        return True
    v = value.strip()
    if len(v) >= MIN_SECRET_VALUE_LEN and not any(c.isspace() for c in v):
        has_alpha = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        return has_alpha and has_digit
    return False


def _key_implies_secret(key):
    return isinstance(key, str) and any(h in key.upper() for h in SECRET_KEY_HINTS)


def _iter_envs(norm):
    """Yield (component_label, env) for app-level and component envs."""
    for e in norm["envs"]:
        yield "app", e
    for c in norm["components"]:
        for e in c["envs"]:
            yield c["name"] or c["kind"], e


@check
def check_secret_not_encrypted(norm):
    findings = []
    for label, e in _iter_envs(norm):
        value, etype = e["value"], e["type"]
        if _is_substitution(value):
            continue
        if etype == "SECRET":
            continue
        if _key_implies_secret(e["key"]) or _looks_like_secret_value(value):
            findings.append({
                "severity": "error", "rule": "secret-not-encrypted",
                "component": label,
                "message": f'env "{e["key"]}" holds a secret-looking value with type != SECRET.',
                "fix": 'set type: SECRET and supply the value via ${VAR} substitution, not inline.',
            })
    return findings


@check
def check_secret_build_scope(norm):
    findings = []
    for label, e in _iter_envs(norm):
        if e["type"] == "SECRET" and e["scope"] == "RUN_AND_BUILD_TIME":
            findings.append({
                "severity": "warning", "rule": "secret-build-scope",
                "component": label,
                "message": f'secret env "{e["key"]}" is scoped RUN_AND_BUILD_TIME and leaks into the build layer.',
                "fix": "use scope: RUN_TIME unless the build genuinely needs the secret.",
            })
    return findings


@check
def check_no_health_check(norm):
    findings = []
    for c in norm["components"]:
        if c["kind"] not in HTTP_KINDS:
            continue
        if c["kind"] == "static_site":  # static sites have no health_check concept
            continue
        hc = c["health_check"]
        if not hc or (not hc.get("http_path") and not hc.get("port")):
            findings.append({
                "severity": "warning", "rule": "no-health-check",
                "component": c["name"] or c["kind"],
                "message": "service has no health_check; App Platform falls back to a TCP check only.",
                "fix": "add health_check.http_path (e.g. /healthz) so unhealthy instances are recycled.",
            })
    return findings


@check
def check_single_instance(norm):
    findings = []
    for c in norm["components"]:
        if c["kind"] != "service":
            continue
        count = c["instance_count"]
        # None means unspecified, which App Platform defaults to 1 -> still a SPOF.
        if c["autoscaling"] is None and (count is None or count == 1):
            findings.append({
                "severity": "warning", "rule": "single-instance",
                "component": c["name"] or c["kind"],
                "message": "service runs a single instance with no autoscaling (single point of failure).",
                "fix": "set instance_count >= 2 or add an autoscaling block for HA.",
            })
    return findings


@check
def check_dev_db_as_prod(norm):
    findings = []
    for d in norm["databases"]:
        if d["production"] is False:
            findings.append({
                "severity": "warning", "rule": "dev-db-as-prod",
                "component": d["name"] or "database",
                "message": "database has production: false (a dev database — no backups, no standby).",
                "fix": "set production: true for any database backing a real workload.",
            })
    return findings


@check
def check_port_mismatch(norm):
    findings = []
    for c in norm["components"]:
        hc = c["health_check"]
        hc_port = hc.get("port") if hc else None
        if hc_port is not None and c["http_port"] is not None and hc_port != c["http_port"]:
            findings.append({
                "severity": "error", "rule": "port-mismatch",
                "component": c["name"] or c["kind"],
                "message": f'health_check.port ({hc_port}) != http_port ({c["http_port"]}); the check probes the wrong port.',
                "fix": "align health_check.port with http_port (or drop it to inherit http_port).",
            })
    return findings


@check
def check_route_overlap(norm):
    findings = []
    rules = [r for r in norm["ingress"]["rules"] if r["prefix"] is not None]
    seen = []
    for r in rules:
        for prev in seen:
            a, b = prev["prefix"], r["prefix"]
            # exact duplicate, or one prefix shadows the other, to different components
            shadows = a == b or b.startswith(a.rstrip("/") + "/") or a.startswith(b.rstrip("/") + "/") or a == "/" or b == "/"
            if shadows and prev["component"] != r["component"]:
                findings.append({
                    "severity": "error", "rule": "route-overlap",
                    "component": r["component"] or "ingress",
                    "message": f'ingress prefix "{b}" overlaps "{a}" (routes to a different component); matching is order-sensitive and ambiguous.',
                    "fix": "make prefixes mutually exclusive, or order most-specific-first and verify intent.",
                })
        seen.append(r)
    return findings


@check
def check_source_conflict(norm):
    findings = []
    for c in norm["components"]:
        if c["has_git"] and c["has_image"]:
            findings.append({
                "severity": "error", "rule": "source-conflict",
                "component": c["name"] or c["kind"],
                "message": "component sets both a git source and an image; App Platform needs exactly one.",
                "fix": "keep either the git/github/gitlab block or the image block, not both.",
            })
    return findings


@check
def check_deprecated_routes(norm):
    findings = []
    for c in norm["components"]:
        if c["routes"]:
            findings.append({
                "severity": "warning", "rule": "deprecated-routes",
                "component": c["name"] or c["kind"],
                "message": "component-level routes is deprecated in favour of top-level ingress.rules.",
                "fix": "move routing to spec.ingress.rules with match.path.prefix + component.name.",
            })
    return findings


# Known App Platform instance_size_slugs. Source: App Platform docs / digitalocean
# Terraform provider. VERIFY against live docs at implementation time and update.
KNOWN_INSTANCE_SLUGS = frozenset({
    # current "apps-" generation (shared = -s-, dedicated = -d-)
    "apps-s-1vcpu-0.5gb", "apps-s-1vcpu-1gb", "apps-s-1vcpu-2gb", "apps-s-2vcpu-4gb",
    "apps-d-1vcpu-0.5gb", "apps-d-1vcpu-1gb", "apps-d-1vcpu-2gb", "apps-d-2vcpu-4gb",
    "apps-d-2vcpu-8gb", "apps-d-4vcpu-8gb", "apps-d-4vcpu-16gb", "apps-d-8vcpu-32gb",
    # legacy generation still accepted by the API
    "basic-xxs", "basic-xs", "basic-s", "basic-m",
    "professional-xs", "professional-s", "professional-m",
    "professional-1l", "professional-l", "professional-xl",
})


def _region_match(a, b):
    if not a or not b:
        return True  # unspecified -> don't guess a mismatch
    # App spec region ("nyc") and DB region ("nyc1") share a datacenter prefix.
    return a == b or a.startswith(b) or b.startswith(a)


@check
def check_unknown_instance_slug(norm):
    findings = []
    for c in norm["components"]:
        slug = c["instance_size_slug"]
        if slug and slug not in KNOWN_INSTANCE_SLUGS:
            findings.append({
                "severity": "warning", "rule": "unknown-instance-slug",
                "component": c["name"] or c["kind"],
                "message": f'instance_size_slug "{slug}" is not a recognised App Platform size.',
                "fix": "use a current slug such as apps-s-1vcpu-1gb (run `doctl apps tier instance-size list`).",
            })
    return findings


@check
def check_db_region_mismatch(norm):
    findings = []
    for d in norm["databases"]:
        if not _region_match(norm["region"], d["region"]):
            findings.append({
                "severity": "warning", "rule": "db-region-mismatch",
                "component": d["name"] or "database",
                "message": f'database region "{d["region"]}" differs from app region "{norm["region"]}" (cross-region latency).',
                "fix": "co-locate the database with the app region unless cross-region is intentional.",
            })
    return findings


# --- input dispatch -------------------------------------------------------

def load_spec(text, path):
    """Parse raw text (format auto-detected) into a normalized spec."""
    stripped = text.lstrip()
    if path.endswith(".tf") or 'resource "digitalocean_app"' in text:
        raw = parse_hcl_app(text)            # Task 7
    elif stripped.startswith("{"):
        raw = json.loads(text)
    else:
        raw = parse_yaml_subset(text)        # Task 6
    return _normalize(raw)


# --- reporter -------------------------------------------------------------

def _format_text(findings):
    lines = []
    for f in findings:
        comp = f"({f['component']}) " if f.get("component") else ""
        lines.append(f"[{f['severity'].upper()}] {f['rule']} {comp}{f['message']}")
        if f.get("fix"):
            lines.append(f"    fix: {f['fix']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint DigitalOcean App Platform app specs (JSON, YAML subset, or Terraform).")
    parser.add_argument("paths", nargs="+", help="app spec files (.json/.yaml/.yml/.tf)")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format (default: text)")
    args = parser.parse_args(argv)

    all_findings = []
    for path in args.paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        try:
            norm = load_spec(text, path)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"error: cannot parse {path}: {exc}", file=sys.stderr)
            return 2
        for f in lint_spec(norm):
            all_findings.append(dict(f, file=path))

    if args.format == "json":
        print(json.dumps(all_findings, indent=2))
    elif all_findings:
        print(_format_text(all_findings))

    return 1 if any(f["severity"] == "error" for f in all_findings) else 0


def parse_yaml_subset(text):  # replaced in Task 6
    raise ValueError("YAML parsing not yet implemented; pass JSON")


def parse_hcl_app(text):  # replaced in Task 7
    raise ValueError("Terraform parsing not yet implemented; pass JSON")


if __name__ == "__main__":
    sys.exit(main())
