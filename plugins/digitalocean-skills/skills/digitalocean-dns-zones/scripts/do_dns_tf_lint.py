#!/usr/bin/env python3
"""Heuristic linter for digitalocean_record Terraform blocks.

This is NOT a full HCL parser — the Python stdlib has none. It scans .tf
text for `resource "digitalocean_record" "<label>" { ... }` blocks via
brace-depth matching and flags two DigitalOcean-specific DNS mistakes:

  - apex-cname (error):  type = "CNAME" with name = "@". DigitalOcean has no
    apex CNAME and no CNAME flattening; such a record breaks the zone.
  - cname-relative-value (warning): a CNAME/MX value without a trailing dot.
    The DigitalOcean provider treats a dotless value as relative and appends
    the domain, producing a doubled FQDN (api.example.com.example.com).

Deliberately NOT checked: a TTL below 30. The provider documents ttl >= 0
(default 1800), so a low TTL is legal and flagging it would be a false
positive. Heuristic limits: interpolated values (value = local.x) and
`dynamic` blocks are skipped, not guessed.

Exit codes: 0 = clean, 1 = findings, 2 = usage/IO error.
"""
import argparse
import json
import re
import sys

RESOURCE_RE = re.compile(
    r'resource\s+"digitalocean_record"\s+"(?P<label>[^"]+)"\s*\{'
)
# Matches `key = "value"` (quoted string args only — interpolations are skipped).
ATTR_RE = re.compile(r'(?P<key>\w+)\s*=\s*"(?P<val>[^"]*)"')


def _iter_blocks(text):
    """Yield (label, body_text) for each digitalocean_record block."""
    for m in RESOURCE_RE.finditer(text):
        start = m.end()  # position just after the opening brace
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        # On a well-formed block i points just past the closing brace, so drop it;
        # on an unclosed block (depth>0, truncated file) keep everything to EOF.
        body = text[start:i - 1] if depth == 0 else text[start:i]
        yield m.group("label"), body


def _attrs(body):
    return {m.group("key"): m.group("val") for m in ATTR_RE.finditer(body)}


def lint_text(text):
    """Return a list of finding dicts for the given Terraform text."""
    findings = []
    for label, body in _iter_blocks(text):
        attrs = _attrs(body)
        rtype = attrs.get("type")
        name = attrs.get("name")
        value = attrs.get("value")

        if rtype == "CNAME" and name == "@":
            findings.append({
                "severity": "error",
                "rule": "apex-cname",
                "label": label,
                "message": (
                    "CNAME at the zone apex (name = \"@\"). DigitalOcean has no "
                    "apex CNAME and no flattening; use an A/AAAA record instead."
                ),
            })

        if rtype in ("CNAME", "MX") and value is not None and not value.endswith("."):
            findings.append({
                "severity": "warning",
                "rule": "relative-fqdn-value",
                "label": label,
                "message": (
                    f'{rtype} value "{value}" has no trailing dot; the provider '
                    "treats it as relative and appends the domain. Use a full "
                    f'FQDN: value = "{value}."'
                ),
            })
    return findings


def _format_text(findings):
    lines = []
    for f in findings:
        lines.append(
            f'[{f["severity"].upper()}] {f["rule"]} '
            f'(digitalocean_record.{f["label"]}): {f["message"]}'
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint digitalocean_record Terraform blocks for DO DNS traps."
    )
    parser.add_argument("paths", nargs="+", help="Terraform .tf files to scan")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args(argv)

    all_findings = []
    for path in args.paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        for f in lint_text(text):
            f = dict(f, file=path)
            all_findings.append(f)

    if args.format == "json":
        print(json.dumps(all_findings, indent=2))
    elif all_findings:
        print(_format_text(all_findings))

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
