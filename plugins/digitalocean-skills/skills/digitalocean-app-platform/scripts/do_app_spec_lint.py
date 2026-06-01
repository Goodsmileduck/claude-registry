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
import re
import sys

COMPONENT_KINDS = ("services", "workers", "jobs", "functions", "static_sites")
# Plural spec key -> singular component kind. Explicit, so adding a kind can't
# be silently broken by a strip-trailing-"s" heuristic (e.g. "static_sites").
_KIND_BY_PLURAL = {
    "services": "service", "workers": "worker", "jobs": "job",
    "functions": "function", "static_sites": "static_site",
}


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
    kind = _KIND_BY_PLURAL[kind_plural]
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


def _finding(severity, rule, component, message, fix):
    return {"severity": severity, "rule": rule, "component": component,
            "message": message, "fix": fix}


def _comp_label(c):
    """The name a finding shows for a component, falling back to its kind."""
    return c["name"] or c["kind"]


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
            yield _comp_label(c), e


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
            findings.append(_finding(
                "error", "secret-not-encrypted", label,
                f'env "{e["key"]}" holds a secret-looking value with type != SECRET.',
                'set type: SECRET and supply the value via ${VAR} substitution, not inline.'))
    return findings


@check
def check_secret_build_scope(norm):
    findings = []
    for label, e in _iter_envs(norm):
        if e["type"] == "SECRET" and e["scope"] == "RUN_AND_BUILD_TIME":
            findings.append(_finding(
                "warning", "secret-build-scope", label,
                f'secret env "{e["key"]}" is scoped RUN_AND_BUILD_TIME and leaks into the build layer.',
                "use scope: RUN_TIME unless the build genuinely needs the secret."))
    return findings


@check
def check_no_health_check(norm):
    findings = []
    for c in norm["components"]:
        # Scoped to services: workers/jobs/functions/static_sites do not require
        # an HTTP health check, so flagging them would be a false positive.
        if c["kind"] != "service":
            continue
        hc = c["health_check"]
        if not hc or (not hc.get("http_path") and not hc.get("port")):
            findings.append(_finding(
                "warning", "no-health-check", _comp_label(c),
                "service has no health_check; App Platform falls back to a TCP check only.",
                "add health_check.http_path (e.g. /healthz) so unhealthy instances are recycled."))
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
            findings.append(_finding(
                "warning", "single-instance", _comp_label(c),
                "service runs a single instance with no autoscaling (single point of failure).",
                "set instance_count >= 2 or add an autoscaling block for HA."))
    return findings


@check
def check_dev_db_as_prod(norm):
    findings = []
    for d in norm["databases"]:
        if d["production"] is False:
            findings.append(_finding(
                "warning", "dev-db-as-prod", d["name"] or "database",
                "database has production: false (a dev database — no backups, no standby).",
                "set production: true for any database backing a real workload."))
    return findings


@check
def check_port_mismatch(norm):
    findings = []
    for c in norm["components"]:
        hc = c["health_check"]
        hc_port = hc.get("port") if hc else None
        if hc_port is not None and c["http_port"] is not None and hc_port != c["http_port"]:
            findings.append(_finding(
                "error", "port-mismatch", _comp_label(c),
                f'health_check.port ({hc_port}) != http_port ({c["http_port"]}); the check probes the wrong port.',
                "align health_check.port with http_port (or drop it to inherit http_port)."))
    return findings


@check
def check_route_overlap(norm):
    findings = []
    rules = [r for r in norm["ingress"]["rules"] if r["prefix"] is not None]
    for i, r in enumerate(rules):
        for prev in rules[:i]:
            a, b = prev["prefix"], r["prefix"]
            # exact duplicate, or one prefix shadows the other, to different components
            shadows = a == b or b.startswith(a.rstrip("/") + "/") or a.startswith(b.rstrip("/") + "/") or a == "/" or b == "/"
            if shadows and prev["component"] != r["component"]:
                findings.append(_finding(
                    "error", "route-overlap", r["component"] or "ingress",
                    f'ingress prefix "{b}" overlaps "{a}" (routes to a different component); matching is order-sensitive and ambiguous.',
                    "make prefixes mutually exclusive, or order most-specific-first and verify intent."))
    return findings


@check
def check_source_conflict(norm):
    findings = []
    for c in norm["components"]:
        if c["has_git"] and c["has_image"]:
            findings.append(_finding(
                "error", "source-conflict", _comp_label(c),
                "component sets both a git source and an image; App Platform needs exactly one.",
                "keep either the git/github/gitlab block or the image block, not both."))
    return findings


@check
def check_deprecated_routes(norm):
    findings = []
    for c in norm["components"]:
        if c["routes"]:
            findings.append(_finding(
                "warning", "deprecated-routes", _comp_label(c),
                "component-level routes is deprecated in favour of top-level ingress.rules.",
                "move routing to spec.ingress.rules with match.path.prefix + component.name."))
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
            findings.append(_finding(
                "warning", "unknown-instance-slug", _comp_label(c),
                f'instance_size_slug "{slug}" is not a recognised App Platform size.',
                "use a current slug such as apps-s-1vcpu-1gb (run `doctl apps tier instance-size list`)."))
    return findings


@check
def check_db_region_mismatch(norm):
    findings = []
    for d in norm["databases"]:
        if not _region_match(norm["region"], d["region"]):
            findings.append(_finding(
                "warning", "db-region-mismatch", d["name"] or "database",
                f'database region "{d["region"]}" differs from app region "{norm["region"]}" (cross-region latency).',
                "co-locate the database with the app region unless cross-region is intentional."))
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


def _yaml_scalar(token):
    """Convert a YAML scalar token to a Python value."""
    t = token.strip()
    if t == "" or t == "~" or t == "null":
        return None
    if (t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'"):
        return t[1:-1]
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def parse_yaml_subset(text):
    """Parse the block-YAML subset DigitalOcean emits.

    Supports nested block mappings, block sequences (`- ` items), and plain or
    quoted scalars. Rejects anchors/aliases, flow collections, and folded/literal
    block scalars — directing the user to JSON. NOT a general YAML parser.
    """
    # Strip comments and blank lines, keep (indent, content) pairs.
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.strip() in ("---", "..."):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        for ch in ("&", "*"):
            if content.startswith(ch) or f": {ch}" in content:
                raise ValueError(f"unsupported YAML anchor/alias: {content!r}; pass JSON")
        if content.endswith((">", "|")):
            raise ValueError(f"unsupported YAML block scalar: {content!r}; pass JSON")
        if ": {" in content or ": [" in content or content in ("{", "["):
            raise ValueError(f"unsupported YAML flow collection: {content!r}; pass JSON")
        lines.append((indent, content))

    pos = [0]

    def parse_block(min_indent):
        # Decide mapping vs sequence by the first line at this level.
        if pos[0] >= len(lines):
            return None
        indent, content = lines[pos[0]]
        if content.startswith("- "):
            return parse_sequence(indent)
        return parse_mapping(indent)

    def parse_mapping(indent):
        node = {}
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise ValueError(f"unexpected indent at {content!r}")
            if content.startswith("- "):
                break
            if ":" not in content:
                raise ValueError(f"expected key: value, got {content!r}")
            key, _, rest = content.partition(":")
            key, rest = key.strip(), rest.strip()
            pos[0] += 1
            if rest == "":
                # A nested block follows when the next line is more-indented
                # (a child mapping/sequence) OR is a block sequence at the same
                # indent as this key — YAML allows `key:` then `- item` aligned
                # with the key. A same-indent non-sequence line is a sibling key,
                # so this key's value is null.
                nested = False
                if pos[0] < len(lines):
                    nxt_indent, nxt_content = lines[pos[0]]
                    if nxt_indent > indent:
                        nested = True
                    elif nxt_indent == indent and nxt_content.startswith("- "):
                        nested = True
                node[key] = parse_block(indent + 1) if nested else None
            else:
                node[key] = _yaml_scalar(rest)
        return node

    def parse_sequence(indent):
        items = []
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent < indent or not content.startswith("- "):
                break
            if cur_indent > indent:
                raise ValueError(f"unexpected indent at {content!r}")
            inner = content[2:].strip()
            # Rewrite "- key: val" as a mapping line at indent+2 and recurse.
            if ":" in inner:
                lines[pos[0]] = (indent + 2, inner)
                items.append(parse_mapping(indent + 2))
            else:
                pos[0] += 1
                items.append(_yaml_scalar(inner))
        return items

    result = parse_block(0)
    return result if isinstance(result, dict) else {}


# Block keywords that repeat inside a digitalocean_app spec -> normalized to lists
# under these plural keys. Everything else stays a singular object.
_HCL_REPEATED = {
    "service": "services", "worker": "workers", "job": "jobs",
    "function": "functions", "static_site": "static_sites",
    "database": "databases", "env": "envs", "rule": "rules", "route": "routes",
}
_HCL_ATTR = re.compile(r'(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<val>"(?:[^"\\]|\\.)*"|[^\s{}]+)')
_HCL_BLOCK_OPEN = re.compile(r'(?P<name>[A-Za-z0-9_]+)\s*(?:"[^"]*"\s*)*\{')
_HCL_APP_RESOURCE = re.compile(r'resource\s+"digitalocean_app"\s+"[^"]*"\s*\{')


def _hcl_scalar(tok):
    if tok and tok[0] == '"' and tok[-1] == '"':
        return tok[1:-1].encode().decode("unicode_escape")
    if tok == "true":
        return True
    if tok == "false":
        return False
    try:
        return int(tok)
    except ValueError:
        return tok


def _parse_hcl_body(text, i):
    """Parse a brace body starting at index i (just after '{').

    Returns (node_dict, index_after_closing_brace). Repeated child blocks are
    collected into lists keyed by their normalized plural name.
    """
    node = {}
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "}":
            return node, i + 1
        if ch in " \t\r\n,":
            i += 1
            continue
        if ch == "#" or text[i:i + 2] == "//":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        # Try a nested block first: NAME [labels] {
        # _HCL_BLOCK_OPEN only matches an identifier immediately before '{', so a
        # match is always a real block (never a string), no extra guard needed.
        bm = _HCL_BLOCK_OPEN.match(text, i)
        if bm:
            name = bm.group("name")
            child, i = _parse_hcl_body(text, bm.end())
            if name in _HCL_REPEATED:
                node.setdefault(_HCL_REPEATED[name], []).append(child)
            else:
                node[name] = child
            continue
        # Otherwise an attribute: key = value
        am = _HCL_ATTR.match(text, i)
        if am:
            node[am.group("key")] = _hcl_scalar(am.group("val"))
            i = am.end()
            continue
        i += 1
    return node, i


def parse_hcl_app(text):
    """Extract the spec of the first resource "digitalocean_app" as a raw dict."""
    m = _HCL_APP_RESOURCE.search(text)
    if not m:
        return {}
    body, _ = _parse_hcl_body(text, m.end())
    spec = body.get("spec", {})
    return spec if isinstance(spec, dict) else {}


if __name__ == "__main__":
    sys.exit(main())
