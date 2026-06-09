#!/usr/bin/env python3
"""Validator for docker-compose.yml.

Parses a subset of YAML sufficient for compose files (no anchors, no flow style
outside simple inline scalars) and reports best-practice violations: missing
healthchecks, depends_on race conditions, inline secrets, host port conflicts,
floating tags, missing restart policy, bind-mounted Docker socket.

Stdlib only. Exit 1 on any block-level finding, else 0.

Usage:
    compose_validator.py docker-compose.yml
    compose_validator.py docker-compose.yml --format json
    compose_validator.py docker-compose.yml --strict
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"


@dataclass
class Finding:
    rule: str
    severity: str
    where: str   # "<service>" or "(root)"
    detail: str
    remedy: str


# ----------- minimal YAML reader -----------
#
# Two passes:
#   1) lex_lines  -> list of (indent, kind, key, value)
#                    kind ∈ {"map", "list", "scalar"} (scalar = empty line / not used)
#   2) build_tree -> walk the token list with an explicit stack, producing nested
#                    dict / list / str values.
#
# Limitations: no anchors/aliases, no flow-style maps spanning lines, no
# multiline scalars. Inline { ... } and [ ... ] are kept as raw strings.

_INLINE_COMMENT = re.compile(r"(?<![:'\"])\s+#.*$")


def _strip(line: str) -> str:
    line = line.rstrip("\r\n")
    line = _INLINE_COMMENT.sub("", line)
    return line


def _scalar(s: str):
    s = s.strip()
    if s == "" or s == "~" or s.lower() == "null":
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s
    return s


def parse_yaml(text: str) -> dict:
    lines = []
    for raw in text.splitlines():
        bare = _strip(raw)
        if not bare.strip() or bare.lstrip().startswith("#"):
            continue
        if bare.lstrip().startswith("---"):
            continue
        indent = len(bare) - len(bare.lstrip(" "))
        body = bare.strip()
        lines.append((indent, body))

    root: dict = {}
    # stack item: (indent_of_keys_inside, container, pending_list_key)
    stack: list[tuple[int, object, str | None]] = [(-1, root, None)]

    i = 0
    while i < len(lines):
        indent, body = lines[i]

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(-1, root, None)]

        parent_indent, parent, _ = stack[-1]

        if body.startswith("- "):
            item_body = body[2:].strip()
            if isinstance(parent, list):
                target = parent
            else:
                # promote previously-empty key under (parent_indent, parent) into a list
                # Find the key that was just set to None or {} at indent==parent_indent
                # We rely on having created the list eagerly below; this path means the
                # parent is a map and we should NOT be here in well-formed YAML.
                # Skip this line as malformed.
                i += 1
                continue

            if (
                ":" in item_body
                and not item_body.startswith(("\"", "'"))
                # only treat as map if the key part is a bare identifier
                and re.match(r"^[A-Za-z_][\w.-]*\s*:", item_body)
            ):
                k, _, v = item_body.partition(":")
                obj: dict = {}
                if v.strip():
                    obj[k.strip()] = _scalar(v)
                target.append(obj)
                stack.append((indent + 2, obj, None))
            else:
                target.append(_scalar(item_body))
            i += 1
            continue

        # mapping entry  "key:" or "key: value"
        if ":" in body:
            k, _, v = body.partition(":")
            key = k.strip()
            val_str = v.strip()
            if val_str == "":
                # peek ahead — if next non-blank is "- ..." at greater indent, it's a list;
                # otherwise an empty map.
                nxt_indent: int | None = None
                nxt_body: str | None = None
                if i + 1 < len(lines):
                    nxt_indent, nxt_body = lines[i + 1]
                if nxt_indent is not None and nxt_indent > indent and nxt_body.startswith("- "):
                    new_container: list = []
                else:
                    new_container = {}
                if isinstance(parent, dict):
                    parent[key] = new_container
                elif isinstance(parent, list) and parent and isinstance(parent[-1], dict):
                    parent[-1][key] = new_container
                stack.append((indent, new_container, None))
            else:
                value = _scalar(val_str)
                if isinstance(parent, dict):
                    parent[key] = value
                elif isinstance(parent, list) and parent and isinstance(parent[-1], dict):
                    parent[-1][key] = value
            i += 1
            continue

        # anything else: skip
        i += 1

    return root


# ----------- rules -----------


@dataclass
class Ctx:
    doc: dict
    strict: bool


def rule_deprecated_version(ctx: Ctx):
    if "version" in ctx.doc:
        yield Finding("CP-version-key", INFO, "(root)",
                      "Top-level 'version' is deprecated in Compose Spec",
                      "Remove the version key")


def rule_no_networks(ctx: Ctx):
    svcs = ctx.doc.get("services") or {}
    if len(svcs) > 1 and not ctx.doc.get("networks"):
        yield Finding("CP-no-networks", INFO, "(root)",
                      "No explicit networks; services share the default bridge",
                      "Declare networks; use 'internal: true' for backend-only")


def _iter_services(ctx: Ctx):
    svcs = ctx.doc.get("services") or {}
    for name, svc in svcs.items():
        if not isinstance(svc, dict):
            continue
        yield name, svc


def rule_image_tag(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        image = svc.get("image")
        if not isinstance(image, str):
            continue
        # strip registry prefix
        last = image.split("/")[-1]
        if "@sha256:" in image:
            continue
        if ":" not in last:
            yield Finding("CP-image-no-tag", WARN, name,
                          f"image '{image}' has no tag",
                          "Pin a specific version tag, or use @sha256: digest")
        elif last.endswith(":latest"):
            yield Finding("CP-image-latest", WARN, name,
                          f"image '{image}' uses :latest",
                          "Pin a specific version tag")


def rule_healthcheck(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        if "healthcheck" not in svc:
            sev = WARN if ctx.strict else INFO
            yield Finding("CP-no-healthcheck", sev, name,
                          "No healthcheck; depends_on conditions will not work",
                          "Add a healthcheck block (test/interval/timeout/retries)")


def rule_depends_race(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        d = svc.get("depends_on")
        if isinstance(d, list) and d:
            yield Finding("CP-depends-race", WARN, name,
                          "depends_on uses the short list form; dependent starts before the target is healthy",
                          "Use long form: depends_on: { <svc>: { condition: service_healthy } }")


def rule_inline_secret(ctx: Ctx):
    secret_key = re.compile(r"\b([A-Z_]*(?:PASSWORD|SECRET|TOKEN|API[_-]?KEY))\b", re.IGNORECASE)
    for name, svc in _iter_services(ctx):
        env = svc.get("environment")
        items: list[str] = []
        if isinstance(env, dict):
            for k, v in env.items():
                items.append(f"{k}={v}")
        elif isinstance(env, list):
            items.extend(str(x) for x in env)
        for entry in items:
            m = secret_key.search(entry)
            if not m:
                continue
            # Allow references like "${FOO}" or empty values (env_file driven)
            after_eq = entry.split("=", 1)[1] if "=" in entry else ""
            after_eq = after_eq.strip()
            if not after_eq or after_eq.startswith("${"):
                continue
            yield Finding("CP-inline-secret", BLOCK, name,
                          f"'{m.group(1)}' looks like a secret with a literal value in 'environment:'",
                          "Move secrets to env_file, Docker secrets, or a vault — never inline literals")


def rule_port_collisions(ctx: Ctx):
    seen: dict[str, str] = {}
    for name, svc in _iter_services(ctx):
        ports = svc.get("ports")
        if not isinstance(ports, list):
            continue
        for p in ports:
            s = str(p)
            m = re.match(r"(?:(?:\d+\.){3}\d+:)?(\d+):\d+", s)
            if not m:
                continue
            host = m.group(1)
            if host in seen and seen[host] != name:
                yield Finding("CP-port-collision", BLOCK, name,
                              f"host port {host} already claimed by service '{seen[host]}'",
                              "Change the host-side port or remove one mapping")
            else:
                seen[host] = name


def rule_docker_sock(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        vols = svc.get("volumes")
        if not isinstance(vols, list):
            continue
        for v in vols:
            if isinstance(v, str) and "/var/run/docker.sock" in v:
                yield Finding("CP-docker-sock", BLOCK, name,
                              "Docker socket is bind-mounted; the container effectively has host root",
                              "Avoid mounting docker.sock; use a rootless socket or remote API w/ TLS")


def rule_no_restart(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        if "build" in svc:
            continue  # dev-build pattern; restart is usually intentional
        if "restart" in svc:
            continue
        if "deploy" in svc and isinstance(svc["deploy"], dict) and "restart_policy" in svc["deploy"]:
            continue
        yield Finding("CP-no-restart", INFO, name,
                      "No restart policy; container will not restart on failure",
                      "Add restart: unless-stopped (or on-failure)")


def rule_resource_limits(ctx: Ctx):
    for name, svc in _iter_services(ctx):
        # v2-style
        if "mem_limit" in svc or "cpus" in svc:
            continue
        # v3 swarm-style
        if isinstance(svc.get("deploy"), dict) and isinstance(svc["deploy"].get("resources"), dict):
            r = svc["deploy"]["resources"]
            if isinstance(r.get("limits"), dict):
                continue
        sev = WARN if ctx.strict else INFO
        yield Finding("CP-no-limits", sev, name,
                      "No memory or CPU limit; one service can starve the host",
                      "Set mem_limit + cpus (Compose v2) or deploy.resources.limits (v3)")


RULES = [
    rule_deprecated_version,
    rule_no_networks,
    rule_image_tag,
    rule_healthcheck,
    rule_depends_race,
    rule_inline_secret,
    rule_port_collisions,
    rule_docker_sock,
    rule_no_restart,
    rule_resource_limits,
]


def validate(doc: dict, strict: bool) -> list[Finding]:
    ctx = Ctx(doc=doc, strict=strict)
    out: list[Finding] = []
    for r in RULES:
        out.extend(r(ctx))
    order = {BLOCK: 0, WARN: 1, INFO: 2}
    out.sort(key=lambda f: (order[f.severity], f.where, f.rule))
    return out


def render_text(path: Path, findings: list[Finding]) -> None:
    counts = {BLOCK: 0, WARN: 0, INFO: 0}
    for f in findings:
        counts[f.severity] += 1
    print(f"-- {path}")
    print(f"   block={counts[BLOCK]}  warn={counts[WARN]}  info={counts[INFO]}")
    if not findings:
        print("   (no findings)")
    for f in findings:
        print(f"   [{f.severity.upper():5}] {f.where:20} {f.rule}: {f.detail}")
        print(f"           fix: {f.remedy}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Validator for docker-compose.yml")
    ap.add_argument("path", help="Path to a compose file")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--strict", action="store_true",
                    help="Promote certain INFO findings to WARN")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.is_file():
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 2

    doc = parse_yaml(p.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(doc.get("services"), dict):
        print(f"error: no top-level 'services:' map in {args.path}", file=sys.stderr)
        return 2

    findings = validate(doc, args.strict)
    if args.format == "json":
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        render_text(p, findings)

    return 1 if any(f.severity == BLOCK for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
