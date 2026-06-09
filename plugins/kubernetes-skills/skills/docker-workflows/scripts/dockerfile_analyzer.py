#!/usr/bin/env python3
"""Static analyzer for Dockerfiles.

Walks instructions and reports anti-patterns grouped by severity. Stdlib only.
Exit code 1 if any blocking finding, else 0.

Usage:
    dockerfile_analyzer.py <path>             # path to Dockerfile or directory
    dockerfile_analyzer.py <path> --format json
    dockerfile_analyzer.py <path> --only-security
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"


@dataclass
class Instruction:
    op: str           # FROM, RUN, COPY, etc.
    args: str
    line_no: int      # 1-based source line of the first physical line


@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    snippet: str
    detail: str
    remedy: str


def tokenize(text: str) -> list[Instruction]:
    """Collapse line-continuations, drop comments/blanks, emit Instructions."""
    out: list[Instruction] = []
    buf: list[str] = []
    first_line = 0
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        bare = line.strip()
        if not bare or bare.startswith("#"):
            continue
        if not buf:
            first_line = n
        if bare.endswith("\\"):
            buf.append(bare[:-1].rstrip())
            continue
        buf.append(bare)
        merged = " ".join(buf).strip()
        buf = []
        m = re.match(r"([A-Za-z]+)\s+(.*)$", merged)
        if not m:
            continue
        out.append(Instruction(op=m.group(1).upper(), args=m.group(2), line_no=first_line))
    return out


# ------- rules -------
# Each rule is a function: (instructions, source) -> Iterable[Finding]


def _floating_tag(inst: Instruction) -> str | None:
    """Return the offending image reference if FROM uses :latest or has no tag."""
    if inst.op != "FROM":
        return None
    # FROM <image>[:tag][@digest] [AS name]
    first = inst.args.split()[0] if inst.args else ""
    if not first:
        return None
    image = first.split("@", 1)[0]  # drop digest
    if ":" not in image.split("/")[-1]:
        return image
    if image.endswith(":latest"):
        return image
    return None


def rule_floating_tag(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        img = _floating_tag(i)
        if img:
            yield Finding("DK-floating-tag", WARN, i.line_no, i.args[:80],
                          f"Image '{img}' uses :latest or no tag",
                          "Pin a specific tag, or pin by digest (image@sha256:...) for CI")


def rule_secrets_in_env(insts: list[Instruction]) -> Iterable[Finding]:
    pat = re.compile(r"\b([A-Z_]*(?:PASSWORD|SECRET|TOKEN|API[_-]?KEY|PRIVATE_KEY))\s*=", re.IGNORECASE)
    for i in insts:
        if i.op in {"ENV", "ARG"}:
            for m in pat.finditer(i.args):
                yield Finding("DK-env-secret", BLOCK, i.line_no, i.args[:80],
                              f"{m.group(1)} appears to be a secret in {i.op}",
                              "Use BuildKit secret mounts: RUN --mount=type=secret,id=... cat /run/secrets/...")


def rule_apt_no_clean(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "RUN":
            continue
        if "apt-get install" in i.args or "apt install" in i.args:
            if "rm -rf /var/lib/apt/lists" not in i.args:
                yield Finding("DK-apt-cache", WARN, i.line_no, i.args[:80],
                              "apt cache retained in layer",
                              "Append: && rm -rf /var/lib/apt/lists/* in the same RUN")


def rule_apk_no_cache(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "RUN":
            continue
        if re.search(r"\bapk\s+add\b", i.args) and "--no-cache" not in i.args:
            yield Finding("DK-apk-cache", WARN, i.line_no, i.args[:80],
                          "apk add without --no-cache leaves the package index in the layer",
                          "Use: apk add --no-cache <pkgs>")


def rule_pip_no_cache(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "RUN":
            continue
        if re.search(r"\bpip(?:3)?\s+install\b", i.args) and "--no-cache-dir" not in i.args:
            yield Finding("DK-pip-cache", INFO, i.line_no, i.args[:80],
                          "pip install without --no-cache-dir caches wheels in the layer",
                          "Add --no-cache-dir, or use a BuildKit cache mount")


def rule_npm_with_dev(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "RUN":
            continue
        if re.search(r"\bnpm\s+install\b", i.args) and "--omit=dev" not in i.args and "--production" not in i.args:
            yield Finding("DK-npm-dev", WARN, i.line_no, i.args[:80],
                          "npm install pulls devDependencies into the runtime image",
                          "Use 'npm ci --omit=dev' (or run in a build stage that does not propagate)")


def rule_add_for_local(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "ADD":
            continue
        first = i.args.split()[0] if i.args else ""
        if first and not first.startswith(("http://", "https://")):
            yield Finding("DK-add-local", INFO, i.line_no, i.args[:80],
                          "ADD is used for a local path",
                          "Prefer COPY for local files; ADD's tar auto-extract is rarely intended")


def rule_copy_dot_dot(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "COPY":
            continue
        if re.match(r"^\.\s+\./?$", i.args.strip()) or i.args.strip() == ". /":
            # Detect if any RUN-with-pkg-install came BEFORE this COPY in the same stage
            pre_install = False
            for prev in insts:
                if prev.line_no >= i.line_no:
                    break
                if prev.op == "FROM":
                    pre_install = False  # new stage resets
                if prev.op == "RUN" and re.search(r"(?:npm ci|npm install|pip install|go mod download|bundle install|cargo fetch)", prev.args):
                    pre_install = True
            if not pre_install:
                yield Finding("DK-copy-before-deps", WARN, i.line_no, i.args[:80],
                              "COPY . . occurs before any dependency-install step",
                              "Copy lockfiles + install deps first, THEN copy source — preserves layer cache on source changes")


def rule_no_user(insts: list[Instruction]) -> Iterable[Finding]:
    if not insts:
        return
    # Per stage: track whether any USER appears between two FROMs
    stage_starts = [n for n, i in enumerate(insts) if i.op == "FROM"]
    if not stage_starts:
        return
    # Only consider the final stage — earlier stages aren't shipped
    last_stage_start = stage_starts[-1]
    final = insts[last_stage_start:]
    if not any(x.op == "USER" and x.args.strip() not in {"", "0", "root", "0:0"} for x in final):
        anchor = final[0]
        yield Finding("DK-runs-as-root", BLOCK, anchor.line_no, anchor.args[:80],
                      "Final stage has no non-root USER instruction",
                      "Add: USER 1001 (or a named non-root user created via adduser/useradd)")


def rule_no_healthcheck(insts: list[Instruction]) -> Iterable[Finding]:
    if not insts:
        return
    if any(i.op == "HEALTHCHECK" for i in insts):
        return
    # Only flag if there's a CMD/ENTRYPOINT that suggests a long-running process
    if not any(i.op in {"CMD", "ENTRYPOINT"} for i in insts):
        return
    yield Finding("DK-no-healthcheck", INFO, insts[-1].line_no, "",
                  "No HEALTHCHECK; orchestrators cannot distinguish 'starting' from 'broken'",
                  "Add: HEALTHCHECK --interval=30s --timeout=3s CMD <probe>")


def rule_multiple_cmd(insts: list[Instruction]) -> Iterable[Finding]:
    cmds = [i for i in insts if i.op == "CMD"]
    if len(cmds) > 1:
        yield Finding("DK-multi-cmd", WARN, cmds[-1].line_no, cmds[-1].args[:80],
                      f"{len(cmds)} CMD instructions present — only the last takes effect",
                      "Keep exactly one CMD")


def rule_shell_form(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op not in {"CMD", "ENTRYPOINT"}:
            continue
        s = i.args.strip()
        if not s.startswith("["):
            yield Finding("DK-shell-form", INFO, i.line_no, s[:80],
                          f"{i.op} uses shell form; exec form delivers signals correctly",
                          'Use exec form: ' + i.op + ' ["/path/bin", "arg"]')


def rule_chmod_777(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "RUN":
            continue
        if re.search(r"\bchmod\s+(?:-R\s+)?777\b", i.args):
            yield Finding("DK-chmod-777", WARN, i.line_no, i.args[:80],
                          "chmod 777 is almost always wrong",
                          "Use a specific mode; combine with chown to grant only to the target user")


def rule_add_https(insts: list[Instruction]) -> Iterable[Finding]:
    for i in insts:
        if i.op != "ADD":
            continue
        first = i.args.split()[0] if i.args else ""
        if first.startswith(("http://", "https://")):
            yield Finding("DK-add-url", WARN, i.line_no, i.args[:80],
                          "ADD <url> does not verify checksums",
                          "RUN curl -fsSL <url> -o file && echo '<sha256> file' | sha256sum -c -")


RULES: list[Callable[[list[Instruction]], Iterable[Finding]]] = [
    rule_floating_tag,
    rule_secrets_in_env,
    rule_apt_no_clean,
    rule_apk_no_cache,
    rule_pip_no_cache,
    rule_npm_with_dev,
    rule_add_for_local,
    rule_add_https,
    rule_copy_dot_dot,
    rule_no_user,
    rule_no_healthcheck,
    rule_multiple_cmd,
    rule_shell_form,
    rule_chmod_777,
]


SECURITY_RULES = {"DK-env-secret", "DK-runs-as-root", "DK-add-url", "DK-chmod-777"}


def analyze(path: Path) -> list[Finding]:
    insts = tokenize(path.read_text(encoding="utf-8", errors="replace"))
    findings: list[Finding] = []
    for r in RULES:
        findings.extend(r(insts))
    order = {BLOCK: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: (order[f.severity], f.line))
    return findings


def discover(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    out: list[Path] = []
    for p in target.rglob("Dockerfile*"):
        if p.is_file():
            out.append(p)
    return out


def render_text(path: Path, findings: list[Finding]) -> None:
    counts = {BLOCK: 0, WARN: 0, INFO: 0}
    for f in findings:
        counts[f.severity] += 1
    print(f"-- {path}")
    print(f"   block={counts[BLOCK]}  warn={counts[WARN]}  info={counts[INFO]}")
    for f in findings:
        print(f"   [{f.severity.upper():5}] line {f.line:4}  {f.rule}: {f.detail}")
        if f.snippet:
            print(f"           > {f.snippet}")
        print(f"           fix: {f.remedy}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Static analyzer for Dockerfiles")
    ap.add_argument("path", help="Dockerfile path or directory to scan")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--only-security", action="store_true",
                    help="Restrict to security-relevant rules")
    args = ap.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"error: path not found: {args.path}", file=sys.stderr)
        return 2

    files = discover(target)
    if not files:
        print(f"error: no Dockerfile found under {args.path}", file=sys.stderr)
        return 2

    all_blocked = False
    payload = []
    for fp in files:
        findings = analyze(fp)
        if args.only_security:
            findings = [f for f in findings if f.rule in SECURITY_RULES]
        if any(f.severity == BLOCK for f in findings):
            all_blocked = True
        if args.format == "json":
            payload.append({"path": str(fp), "findings": [asdict(f) for f in findings]})
        else:
            render_text(fp, findings)

    if args.format == "json":
        print(json.dumps(payload, indent=2))

    return 1 if all_blocked else 0


if __name__ == "__main__":
    sys.exit(main())
