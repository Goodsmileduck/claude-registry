#!/usr/bin/env python3
"""Heuristic linter for Go controller Reconcile functions.

Detects common operator anti-patterns by scanning Go source files. Not an AST
parser; uses brace-matching to locate Reconcile function bodies and regex over
those bodies. Catches the recurring mistakes — blocking calls, spec mutation,
process-exiting calls, oversize reconcile bodies, finalizer add without remove.

Exit 1 on any FAIL, else 0.

Usage:
    reconcile_lint.py --controller path/to/controller.go
    reconcile_lint.py --controller controllers/ --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

FAIL, WARN = "FAIL", "WARN"

# Past ~80 lines, reconcile bodies consistently fold multiple concerns that
# belong in reconcileXxx helpers — opinionated, not magic.
MAX_RECONCILE_LINES = 80


@dataclass
class Finding:
    level: str
    code: str
    line: int
    message: str


@dataclass
class FileReport:
    file: str
    findings: list[Finding]


_RECONCILE_SIG = re.compile(
    r"func\s+\(\s*\w+\s+\*?\w+\s*\)\s+Reconcile\s*\([^)]*\)\s*\([^)]*\)\s*{",
)


def find_reconcile_bodies(src: str):
    """Yield (start_line, end_line, body_with_braces) per Reconcile function."""
    for m in _RECONCILE_SIG.finditer(src):
        open_brace = src.find("{", m.end() - 1)
        if open_brace < 0:
            continue
        depth = 0
        i = open_brace
        while i < len(src):
            c = src[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    body = src[open_brace : i + 1]
                    start_line = src.count("\n", 0, m.start()) + 1
                    end_line = src.count("\n", 0, i + 1) + 1
                    yield start_line, end_line, body
                    break
            i += 1


# ----------- per-body checks -----------


def _line_of(body: str, idx: int, body_start_line: int) -> int:
    return body_start_line + body.count("\n", 0, idx)


def check_blocking_sleep(body: str, body_start_line: int):
    for m in re.finditer(r"\btime\.Sleep\s*\(", body):
        yield Finding(FAIL, "time_sleep", _line_of(body, m.start(), body_start_line),
                      "time.Sleep blocks the workqueue; use ctrl.Result{RequeueAfter: ...} instead")


def check_spec_update(body: str, body_start_line: int):
    # r.Update(ctx, obj) or r.Client.Update(ctx, obj) where obj is not a child
    for m in re.finditer(r"\br\s*\.\s*(?:Client\s*\.\s*)?Update\s*\(\s*ctx\s*,\s*&?\w+\s*\)", body):
        yield Finding(WARN, "spec_update", _line_of(body, m.start(), body_start_line),
                      "r.Update on the reconciled object mutates spec; status changes belong on r.Status().Update")


def check_process_exit(body: str, body_start_line: int):
    for m in re.finditer(r"\bos\.Exit\s*\(", body):
        yield Finding(FAIL, "os_exit", _line_of(body, m.start(), body_start_line),
                      "os.Exit terminates the controller; return an error so it requeues")
    for m in re.finditer(r"\blog\.Fatal\w*\s*\(", body):
        yield Finding(FAIL, "log_fatal", _line_of(body, m.start(), body_start_line),
                      "log.Fatal terminates the process; return an error instead")
    for m in re.finditer(r"\bpanic\s*\(", body):
        yield Finding(WARN, "panic", _line_of(body, m.start(), body_start_line),
                      "panic crashes the reconciler; prefer returning an error")


def check_context_free_http(body: str, body_start_line: int):
    for m in re.finditer(r"\bhttp\.(?:Get|Post|Head|Do)\s*\(", body):
        yield Finding(WARN, "http_no_ctx", _line_of(body, m.start(), body_start_line),
                      "raw http call cannot be cancelled on shutdown; build a request with ctx and use client.Do(req)")


def check_reconcile_size(body: str, body_start_line: int):
    lines = body.count("\n")
    if lines > MAX_RECONCILE_LINES:
        yield Finding(WARN, "reconcile_size", body_start_line,
                      f"Reconcile body is {lines} lines (> {MAX_RECONCILE_LINES}); extract reconcileXxx helpers")


def check_returns_result(body: str, body_start_line: int):
    if "ctrl.Result" not in body and "reconcile.Result" not in body:
        yield Finding(WARN, "missing_result", body_start_line,
                      "no ctrl.Result returned anywhere in Reconcile; transient errors will not be requeued")


PER_BODY_CHECKS = [
    check_blocking_sleep,
    check_spec_update,
    check_process_exit,
    check_context_free_http,
    check_reconcile_size,
    check_returns_result,
]


# ----------- file-level check (finalizer balance) -----------


def check_finalizer_balance(src: str) -> list[Finding]:
    adds = list(re.finditer(r"\bcontrollerutil\.AddFinalizer\b", src))
    removes = re.search(r"\bcontrollerutil\.RemoveFinalizer\b", src)
    if adds and not removes:
        first = adds[0]
        line = src.count("\n", 0, first.start()) + 1
        return [Finding(WARN, "finalizer_unbalanced", line,
                        "AddFinalizer is used but RemoveFinalizer is not; external resources will orphan on delete")]
    return []


# ----------- top-level -----------


def lint_file(path: Path) -> FileReport:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileReport(file=str(path), findings=[])
    if "Reconcile" not in src:
        return FileReport(file=str(path), findings=[])

    findings: list[Finding] = []
    for start, end, body in find_reconcile_bodies(src):
        for fn in PER_BODY_CHECKS:
            findings.extend(fn(body, start))
    findings.extend(check_finalizer_balance(src))
    findings.sort(key=lambda f: (f.line, f.code))
    return FileReport(file=str(path), findings=findings)


_SKIP_DIRS = {".git", "vendor", "node_modules", "dist", "build", "bin", "__pycache__"}


def _go_files(target: Path):
    if target.is_file():
        yield target
        return
    for p in target.rglob("*.go"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def audit(target: Path) -> list[FileReport]:
    out: list[FileReport] = []
    for f in _go_files(target):
        rep = lint_file(f)
        if rep.findings:
            out.append(rep)
    return out


def render_text(reports: list[FileReport]) -> int:
    n_fail = sum(1 for r in reports for f in r.findings if f.level == FAIL)
    n_warn = sum(1 for r in reports for f in r.findings if f.level == WARN)
    if not reports:
        print("no findings")
        return 0
    print(f"{len(reports)} file(s) flagged: {n_fail} FAIL, {n_warn} WARN\n")
    for r in reports:
        print(f"# {r.file}")
        for f in r.findings:
            print(f"  {f.level:4}  L{f.line:<5} {f.code}: {f.message}")
        print()
    return 1 if n_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint Go Reconcile functions")
    ap.add_argument("--controller", required=True, help="Path to a Go file or directory")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    target = Path(args.controller)
    if not target.exists():
        print(f"error: not found: {args.controller}", file=sys.stderr)
        return 2

    reports = audit(target)
    if args.format == "json":
        payload = [{"file": r.file, "findings": [asdict(f) for f in r.findings]} for r in reports]
        print(json.dumps(payload, indent=2))
        return 1 if any(f.level == FAIL for r in reports for f in r.findings) else 0
    return render_text(reports)


if __name__ == "__main__":
    sys.exit(main())
