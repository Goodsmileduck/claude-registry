#!/usr/bin/env python3
"""Lint a wrangler config (toml or jsonc) against cloudflare-workers SKILL.md Hard rules 1–5.

Usage:
  python3 validate_wrangler.py --config wrangler.jsonc
  python3 validate_wrangler.py --config wrangler.toml --format json

Exit codes: 0 = clean, 1 = ERROR/WARN findings, 2 = bad input.
Stdlib only. Python 3.11+ for .toml (tomllib); .jsonc works on any 3.x.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from typing import Any

try:
    import tomllib  # py 3.11+
except ImportError:
    tomllib = None


# ---------- constants -------------------------------------------------------

# Non-inheritable keys per Cloudflare docs: declaring any of these at root and
# also populating env.<name> means env.<name> must repeat them or they vanish.
NON_INHERITABLE: set[str] = {
    "vars", "kv_namespaces", "r2_buckets", "d1_databases",
    "queues", "durable_objects", "services",
    "analytics_engine_datasets", "vectorize", "hyperdrive",
    "mtls_certificates", "dispatch_namespaces", "send_email",
    "browser", "ai", "routes", "route", "workers_dev",
    "placement", "triggers",
}

# Heuristic: a `vars` key ending in any of these is probably a secret.
SECRET_SUFFIX_RE = re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD|_PASSPHRASE)$", re.IGNORECASE)


# ---------- config loading --------------------------------------------------

def strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments from JSONC text, leaving string literals intact."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_config(path: str) -> tuple[dict[str, Any], str]:
    """Load wrangler config; returns (cfg, 'toml'|'jsonc')."""
    if path.endswith(".toml"):
        if tomllib is None:
            raise SystemExit("Python 3.11+ required to parse .toml (no tomllib).")
        with open(path, "rb") as f:
            return tomllib.load(f), "toml"
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(strip_jsonc(raw)), "jsonc"
    except json.JSONDecodeError as e:
        raise SystemExit(f"failed to parse {path}: {e}")


# ---------- checks ----------------------------------------------------------

# Finding shape: {check, address, severity, message}
# severity: ERROR (deploys break or silently mis-configure), WARN (likely
# misuse), INFO (hygiene heads-up).


EMPTY = (None, [], {}, "")


def check_env_override(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule 1: every non-inheritable root key must appear under each env block."""
    envs = cfg.get("env") or {}
    if not envs:
        return []
    root_keys = sorted(k for k in NON_INHERITABLE if k in cfg and cfg[k] not in EMPTY)
    if not root_keys:
        return []
    findings: list[dict[str, Any]] = []
    for env_name, env_cfg in envs.items():
        if not isinstance(env_cfg, dict):
            continue
        missing = [k for k in root_keys if k not in env_cfg or env_cfg[k] in EMPTY]
        if missing:
            findings.append({
                "check": "env_override_missing_bindings",
                "address": f"env.{env_name}",
                "severity": "ERROR",
                "message": (
                    f"non-inheritable keys present at root but missing under env.{env_name}: "
                    f"{', '.join(missing)}. These do NOT inherit; the env deploys without them. "
                    "Repeat each per env, or move root bindings entirely into env blocks."
                ),
            })
    return findings


def _do_class_names(do_block: Any) -> list[str]:
    if not isinstance(do_block, dict):
        return []
    return [
        b["class_name"]
        for b in (do_block.get("bindings") or [])
        if isinstance(b, dict) and "class_name" in b
    ]


def check_do_migrations(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule 2: every DO-bound class must be declared in the migrations history."""
    bound: dict[str, str] = {}
    for cn in _do_class_names(cfg.get("durable_objects")):
        bound[cn] = "durable_objects.bindings"
    for env_name, env_cfg in (cfg.get("env") or {}).items():
        if isinstance(env_cfg, dict):
            for cn in _do_class_names(env_cfg.get("durable_objects")):
                bound.setdefault(cn, f"env.{env_name}.durable_objects.bindings")

    if not bound:
        return []

    declared: set[str] = set()
    deleted: set[str] = set()
    for m in cfg.get("migrations") or []:
        if not isinstance(m, dict):
            continue
        declared.update(m.get("new_classes") or [])
        declared.update(m.get("new_sqlite_classes") or [])
        for r in m.get("renamed_classes") or []:
            if isinstance(r, dict) and "to" in r:
                declared.add(r["to"])
                if "from" in r:
                    declared.discard(r["from"])
        for t in m.get("transferred_classes") or []:
            if isinstance(t, dict) and "to" in t:
                declared.add(t["to"])
        deleted.update(m.get("deleted_classes") or [])
    declared -= deleted

    findings: list[dict[str, Any]] = []
    for cn, addr in sorted(bound.items()):
        if cn not in declared:
            findings.append({
                "check": "do_class_no_migration",
                "address": addr,
                "severity": "ERROR",
                "message": (
                    f"Durable Object class '{cn}' is bound but not declared in the migrations array. "
                    "Every bound class must appear as new_classes / new_sqlite_classes, or as the `to` "
                    "side of renamed_classes / transferred_classes."
                ),
            })
    return findings


def check_secret_shape_vars(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule 3: vars keys with secret-shaped suffixes are probably misplaced."""
    findings: list[dict[str, Any]] = []

    def scan(vars_block: Any, addr: str) -> None:
        if not isinstance(vars_block, dict):
            return
        for k in vars_block:
            if SECRET_SUFFIX_RE.search(k):
                findings.append({
                    "check": "secret_shape_in_vars",
                    "address": f"{addr}.{k}",
                    "severity": "WARN",
                    "message": (
                        f"var name '{k}' looks like a secret. `vars` ships plaintext in the deploy "
                        f"bundle and is visible in the dashboard. Move to a secret: "
                        f"`wrangler secret put {k}` (per env)."
                    ),
                })

    scan(cfg.get("vars"), "vars")
    for env_name, env_cfg in (cfg.get("env") or {}).items():
        if isinstance(env_cfg, dict):
            scan(env_cfg.get("vars"), f"env.{env_name}.vars")
    return findings


def check_compat_date(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule 4: compatibility_date must be a past or today ISO date."""
    cd = cfg.get("compatibility_date")
    if cd is None:
        return [{
            "check": "compat_date_missing",
            "address": "compatibility_date",
            "severity": "WARN",
            "message": "compatibility_date is missing. Pin one (YYYY-MM-DD); the default is implicit and shifts over time.",
        }]
    if not isinstance(cd, str):
        return [{
            "check": "compat_date_bad_type",
            "address": "compatibility_date",
            "severity": "ERROR",
            "message": f"compatibility_date is {type(cd).__name__}, expected an ISO date string.",
        }]
    try:
        parsed = date.fromisoformat(cd)
    except ValueError:
        return [{
            "check": "compat_date_bad_format",
            "address": "compatibility_date",
            "severity": "ERROR",
            "message": f"compatibility_date '{cd}' is not YYYY-MM-DD.",
        }]
    if parsed > date.today():
        return [{
            "check": "compat_date_future",
            "address": "compatibility_date",
            "severity": "ERROR",
            "message": f"compatibility_date '{cd}' is in the future; deploys reject future dates.",
        }]
    return []


def _route_entries(scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize routes/route into [{pattern, custom_domain}]."""
    out: list[dict[str, Any]] = []
    for r in scope.get("routes") or []:
        if isinstance(r, str):
            out.append({"pattern": r, "custom_domain": False})
        elif isinstance(r, dict):
            out.append({
                "pattern": r.get("pattern", ""),
                "custom_domain": bool(r.get("custom_domain")),
            })
    route = scope.get("route")
    if isinstance(route, str):
        out.append({"pattern": route, "custom_domain": False})
    elif isinstance(route, dict):
        out.append({
            "pattern": route.get("pattern", ""),
            "custom_domain": bool(route.get("custom_domain")),
        })
    return out


def _host_of(pattern: str) -> str:
    """Hostname from a route pattern; strip scheme and path."""
    p = pattern
    if "://" in p:
        p = p.split("://", 1)[1]
    return p.split("/", 1)[0].lower()


def check_route_custom_domain_overlap(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule 5: the same hostname can't be a Custom Domain AND a route pattern."""
    findings: list[dict[str, Any]] = []

    def scan(scope: dict[str, Any], addr: str) -> None:
        if not isinstance(scope, dict):
            return
        by_host: dict[str, list[dict[str, Any]]] = {}
        for e in _route_entries(scope):
            h = _host_of(e["pattern"])
            if h:
                by_host.setdefault(h, []).append(e)
        for host, ents in by_host.items():
            has_cd = any(e["custom_domain"] for e in ents)
            has_route = any(not e["custom_domain"] for e in ents)
            if has_cd and has_route:
                findings.append({
                    "check": "route_and_custom_domain_overlap",
                    "address": f"{addr} (host {host})",
                    "severity": "ERROR",
                    "message": (
                        f"hostname '{host}' has both a Custom Domain entry and a route-pattern entry. "
                        "Precedence is undefined; pick one."
                    ),
                })

    scan(cfg, "root")
    for env_name, env_cfg in (cfg.get("env") or {}).items():
        scan(env_cfg, f"env.{env_name}")
    return findings


def check_schema_pointer(cfg: dict[str, Any], fmt: str) -> list[dict[str, Any]]:
    """jsonc projects benefit from $schema for IDE autocomplete."""
    if fmt != "jsonc" or cfg.get("$schema"):
        return []
    return [{
        "check": "jsonc_no_schema",
        "address": "$schema",
        "severity": "INFO",
        "message": (
            'no $schema key set. Add "$schema": "node_modules/wrangler/config-schema.json" '
            "for IDE autocomplete and key validation."
        ),
    }]


# ---------- driver ----------------------------------------------------------

def validate(cfg: dict[str, Any], fmt: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(check_env_override(cfg))
    findings.extend(check_do_migrations(cfg))
    findings.extend(check_secret_shape_vars(cfg))
    findings.extend(check_compat_date(cfg))
    findings.extend(check_route_custom_domain_overlap(cfg))
    findings.extend(check_schema_pointer(cfg, fmt))
    return findings


def format_text(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "OK — no findings.\n"
    sev_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    lines: list[str] = []
    for f in sorted(findings, key=lambda x: (sev_order.get(x["severity"], 9), x["check"])):
        lines.append(f"[{f['severity']}] {f['check']} @ {f['address']}")
        lines.append(f"    {f['message']}")
    lines.append("")
    lines.append(f"{len(findings)} finding(s).")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Lint a wrangler config for cloudflare-workers Hard rules.",
    )
    p.add_argument("--config", required=True,
                   help="Path to wrangler.toml or wrangler.jsonc.")
    p.add_argument("--format", choices=("text", "json"), default="text",
                   help="Output format (default: text).")
    args = p.parse_args(argv)

    try:
        cfg, fmt = load_config(args.config)
    except OSError as e:
        print(f"failed to read {args.config}: {e}", file=sys.stderr)
        return 2

    findings = validate(cfg, fmt)
    if args.format == "json":
        json.dump({"findings": findings, "count": len(findings)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_text(findings))
    return 1 if any(f["severity"] in ("ERROR", "WARN") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
