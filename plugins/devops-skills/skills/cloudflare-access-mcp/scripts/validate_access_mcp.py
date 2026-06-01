#!/usr/bin/env python3
"""Lint a Terraform plan for the cloudflare-access-mcp pattern (SKILL.md Hard rules 1–5).

Usage:
  terraform show -json plan.tfplan | python3 validate_access_mcp.py
  python3 validate_access_mcp.py --plan plan.json [--format json]

Exit codes: 0 = no findings, 1 = findings, 2 = input is not a Terraform JSON plan.
Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Iterator


# ---------- plan walking -----------------------------------------------------

def iter_resources(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every resource in planned_values, recursing into child_modules."""
    root = plan.get("planned_values", {}).get("root_module") or {}

    def walk(module: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for r in module.get("resources") or []:
            yield r
        for child in module.get("child_modules") or []:
            yield from walk(child)

    yield from walk(root)


# ---------- individual checks -----------------------------------------------

# Findings shape: {check, address, severity, message}
# severity: ERROR (will break), WARN (silent footgun), INFO (heads-up)


def check_oauth_block(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 1: Managed OAuth block correctness on access_application."""
    if r.get("type") != "cloudflare_zero_trust_access_application":
        return []
    vals = r.get("values") or {}
    oauth = vals.get("oauth_configuration")
    addr = r.get("address", "<unknown>")

    if oauth is None:
        return [{
            "check": "managed_oauth_absent",
            "address": addr,
            "severity": "INFO",
            "message": (
                "no oauth_configuration block — MCP clients will not discover OAuth "
                "via /.well-known/oauth-authorization-server at this hostname. If this "
                "app is intended for MCP, enable Managed OAuth (see SKILL.md)."
            ),
        }]

    findings: list[dict[str, Any]] = []
    if oauth.get("enabled") is not True:
        findings.append({
            "check": "managed_oauth_enabled_false",
            "address": addr,
            "severity": "ERROR",
            "message": "oauth_configuration.enabled is not true; MCP-spec OAuth endpoints will not be published.",
        })

    dcr = oauth.get("dynamic_client_registration") or {}
    if dcr.get("enabled") is not True:
        findings.append({
            "check": "dcr_disabled",
            "address": addr,
            "severity": "ERROR",
            "message": (
                "dynamic_client_registration.enabled is not true; Claude clients "
                "register dynamically and have no static-client UI, so they will "
                "fail to register."
            ),
        })
    if dcr.get("allow_any_on_localhost") is not True:
        findings.append({
            "check": "dcr_localhost_disallowed",
            "address": addr,
            "severity": "ERROR",
            "message": "allow_any_on_localhost is not true; Claude Desktop's loopback callback (http://127.0.0.1:<random>/callback) will be rejected.",
        })
    if dcr.get("allow_any_on_loopback") is not True:
        findings.append({
            "check": "dcr_loopback_disallowed",
            "address": addr,
            "severity": "ERROR",
            "message": "allow_any_on_loopback is not true; Claude Desktop's loopback callback will be rejected.",
        })
    return findings


def check_policy_require_login_method(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 2: OTP bypass — email/email_domain include with no login_method require."""
    if r.get("type") != "cloudflare_zero_trust_access_policy":
        return []
    vals = r.get("values") or {}
    include_matches_email = any(
        isinstance(e, dict) and (e.get("email") or e.get("email_domain"))
        for e in vals.get("include") or []
    )
    if not include_matches_email:
        return []
    has_login_method = any(
        isinstance(e, dict) and e.get("login_method")
        for e in vals.get("require") or []
    )
    if has_login_method:
        return []
    return [{
        "check": "otp_bypass_risk",
        "address": r.get("address", "<unknown>"),
        "severity": "ERROR",
        "message": (
            "policy allowlists by email/email_domain but has no `require { login_method }` block. "
            "Cloudflare's built-in one-time-PIN identity will satisfy the allowlist and bypass "
            "your IdP. Add a require block forcing the IdP id."
        ),
    }]


def check_policy_session_duration(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 3: missing session_duration on policy → silent 24h override."""
    if r.get("type") != "cloudflare_zero_trust_access_policy":
        return []
    vals = r.get("values") or {}
    if vals.get("session_duration"):
        return []
    return [{
        "check": "policy_session_duration_missing",
        "address": r.get("address", "<unknown>"),
        "severity": "WARN",
        "message": (
            "policy has no session_duration. The policy value OVERRIDES the app's; "
            "with it omitted the effective session caps at the 24h policy default, "
            "ignoring whatever you set on the access_application."
        ),
    }]


def check_ingress_catch_all_last(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 4: tunnel ingress catch-all must be the LAST entry."""
    if r.get("type") != "cloudflare_zero_trust_tunnel_cloudflared_config":
        return []
    vals = r.get("values") or {}
    config = vals.get("config") or {}
    # The provider sometimes nests as a single-element list of objects;
    # accept either shape.
    if isinstance(config, list):
        config = config[0] if config else {}
    ingress = config.get("ingress") or []
    if not ingress:
        return []
    addr = r.get("address", "<unknown>")

    def is_catch_all(entry: dict[str, Any]) -> bool:
        return entry.get("service") == "http_status:404" and not entry.get("hostname") and not entry.get("path")

    catch_indices = [i for i, e in enumerate(ingress) if isinstance(e, dict) and is_catch_all(e)]
    findings: list[dict[str, Any]] = []
    if not catch_indices:
        findings.append({
            "check": "ingress_catchall_missing",
            "address": addr,
            "severity": "ERROR",
            "message": "tunnel ingress has no catch-all `{ service = \"http_status:404\" }` entry; cloudflared will reject this config.",
        })
        return findings
    last = len(ingress) - 1
    if catch_indices != [last]:
        findings.append({
            "check": "ingress_catchall_misplaced",
            "address": addr,
            "severity": "ERROR",
            "message": (
                f"catch-all ingress entry found at indices {catch_indices}; "
                f"must be the SINGLE last entry (index {last}). cloudflared rejects out-of-order configs."
            ),
        })
    return findings


def check_dns_duplicates(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check 5: multiple DNS records for the same (zone_id, type, fqdn)."""
    groups: defaultdict[tuple[str, str, str], list[str]] = defaultdict(list)
    for r in resources:
        if r.get("type") != "cloudflare_dns_record":
            continue
        vals = r.get("values") or {}
        key = (
            str(vals.get("zone_id") or ""),
            str(vals.get("type") or ""),
            str(vals.get("name") or ""),
        )
        groups[key].append(r.get("address", "<unknown>"))
    findings: list[dict[str, Any]] = []
    for (zone_id, rtype, name), addrs in groups.items():
        if len(addrs) > 1:
            findings.append({
                "check": "dns_record_duplicate",
                "address": ", ".join(addrs),
                "severity": "ERROR",
                "message": (
                    f"{len(addrs)} cloudflare_dns_record resources target the same "
                    f"(zone_id={zone_id or '?'}, type={rtype}, name={name}). Cf provider v5 "
                    "upserts by (name, type) so both resources will fight on every apply."
                ),
            })
    return findings


# ---------- driver -----------------------------------------------------------

def validate(plan: dict[str, Any]) -> list[dict[str, Any]]:
    resources = list(iter_resources(plan))
    findings: list[dict[str, Any]] = []
    for r in resources:
        findings.extend(check_oauth_block(r))
        findings.extend(check_policy_require_login_method(r))
        findings.extend(check_policy_session_duration(r))
        findings.extend(check_ingress_catch_all_last(r))
    findings.extend(check_dns_duplicates(resources))
    return findings


def format_text(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "OK — no findings.\n"
    lines = []
    sev_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    for f in sorted(findings, key=lambda x: (sev_order.get(x["severity"], 9), x["check"])):
        lines.append(f"[{f['severity']}] {f['check']} @ {f['address']}")
        lines.append(f"    {f['message']}")
    lines.append("")
    lines.append(f"{len(findings)} finding(s).")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plan", help="Path to `terraform show -json` output. Default: stdin.")
    p.add_argument("--format", choices=("text", "json"), default="text",
                   help="Output format (default: text).")
    args = p.parse_args(argv)

    if args.plan:
        with open(args.plan, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"input is not valid JSON: {e}", file=sys.stderr)
        return 2

    # Smoke-test it actually looks like a Terraform plan.
    if not isinstance(plan, dict) or "planned_values" not in plan:
        print(
            "input does not look like `terraform show -json` output "
            "(missing top-level `planned_values` key).",
            file=sys.stderr,
        )
        return 2

    findings = validate(plan)
    if args.format == "json":
        json.dump({"findings": findings, "count": len(findings)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_text(findings))

    return 1 if any(f["severity"] in ("ERROR", "WARN") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
