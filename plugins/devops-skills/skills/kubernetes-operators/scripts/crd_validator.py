#!/usr/bin/env python3
"""Validate Kubernetes CRD YAMLs against operator best practices.

Scans one CRD file or a tree of YAMLs. For each CustomResourceDefinition
document, reports failures (blockers) and warnings. Does not require PyYAML —
uses regex over the raw document text. Documents are split on '---' separators.

Checks (high-level):
  * status subresource is enabled
  * exactly one version has storage:true; at least one served:true
  * openAPIV3Schema is declared; no top-level preserve-unknown-fields
  * conditions array is in the schema (for metav1.Conditions)
  * additionalPrinterColumns include an Age column
  * scope and names.singular / names.listKind are declared

Exit 1 if any FAIL, else 0.

Usage:
    crd_validator.py --crd path/to/file-or-dir
    crd_validator.py --crd path --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

FAIL, WARN = "FAIL", "WARN"


@dataclass
class Issue:
    level: str
    code: str
    message: str


@dataclass
class CrdReport:
    file: str
    name: str
    issues: list[Issue]


# ----------- doc utilities -----------

_DOC_SPLIT = re.compile(r"(?m)^---\s*$")


def _iter_docs(text: str):
    for chunk in _DOC_SPLIT.split(text):
        s = chunk.strip()
        if s:
            yield chunk  # preserve original indentation


def _is_crd(doc: str) -> bool:
    return re.search(r"(?m)^kind:\s*CustomResourceDefinition\s*$", doc) is not None


def _name_of(doc: str, fallback: str) -> str:
    # Find metadata.name: scan lines after a 'metadata:' line at column 0
    in_metadata = False
    for line in doc.splitlines():
        if not in_metadata:
            if re.match(r"^metadata:\s*$", line):
                in_metadata = True
            continue
        if line and not line.startswith((" ", "\t")):
            break  # left the metadata block
        m = re.match(r"^\s+name:\s*([\w.\-]+)\s*$", line)
        if m:
            return m.group(1)
    return fallback


# ----------- individual checks -----------


def check_status_subresource(doc: str) -> list[Issue]:
    # subresources block followed within the next few lines by a status key
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\bsubresources:\s*$", line):
            for nxt in lines[i + 1 : i + 6]:
                if re.match(r"^\s+status:\s*\{?\s*\}?\s*$", nxt):
                    return []
    return [Issue(FAIL, "status_subresource",
                  "spec.versions[*].subresources.status is not enabled — status writes will re-trigger reconcile")]


def check_storage_version(doc: str) -> list[Issue]:
    n = len(re.findall(r"(?m)^\s*storage:\s*true\b", doc))
    if n == 1:
        return []
    return [Issue(FAIL, "storage_version", f"expected exactly one 'storage: true' version, found {n}")]


def check_served_version(doc: str) -> list[Issue]:
    if re.search(r"(?m)^\s*served:\s*true\b", doc):
        return []
    return [Issue(FAIL, "served_version", "no version declares 'served: true'")]


def check_schema_present(doc: str) -> list[Issue]:
    if "openAPIV3Schema" in doc:
        return []
    return [Issue(FAIL, "schema_present", "no spec.versions[*].schema.openAPIV3Schema")]


def check_schema_typed(doc: str) -> list[Issue]:
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if "openAPIV3Schema:" in line:
            for nxt in lines[i + 1 : i + 4]:
                if re.match(r"^\s+x-kubernetes-preserve-unknown-fields:\s*true\b", nxt):
                    return [Issue(WARN, "schema_typed",
                                  "x-kubernetes-preserve-unknown-fields: true at schema root defeats validation")]
    return []


def check_conditions_array(doc: str) -> list[Issue]:
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s+conditions:\s*$", line):
            for nxt in lines[i + 1 : i + 4]:
                if re.match(r"^\s+type:\s*array\b", nxt):
                    return []
    return [Issue(WARN, "conditions_array",
                  "no typed conditions array in schema (recommended: metav1.Conditions shape)")]


def check_printer_columns(doc: str) -> list[Issue]:
    if "additionalPrinterColumns" not in doc:
        return [Issue(WARN, "printer_columns",
                      "no additionalPrinterColumns — 'kubectl get' shows only NAME and AGE")]
    # Look for any line of the form "name: Age" inside the doc (any indent).
    for line in doc.splitlines():
        if re.match(r"^\s*-?\s*name:\s*Age\s*$", line):
            return []
    return [Issue(WARN, "printer_columns_age",
                  "additionalPrinterColumns is present but no 'Age' column")]


def check_scope(doc: str) -> list[Issue]:
    m = re.search(r"(?m)^\s*scope:\s*(\w+)", doc)
    if not m:
        return [Issue(WARN, "scope", "spec.scope is not declared")]
    if m.group(1) == "Cluster":
        return [Issue(WARN, "scope", "spec.scope is Cluster — make sure that's intentional, prefer Namespaced")]
    return []


def check_names(doc: str) -> list[Issue]:
    out = []
    if not re.search(r"(?m)^\s*singular:\s*\S", doc):
        out.append(Issue(WARN, "names_singular", "names.singular is not declared"))
    if not re.search(r"(?m)^\s*listKind:\s*\S", doc):
        out.append(Issue(WARN, "names_listKind", "names.listKind is not declared"))
    return out


CHECKS = [
    check_status_subresource,
    check_storage_version,
    check_served_version,
    check_schema_present,
    check_schema_typed,
    check_conditions_array,
    check_printer_columns,
    check_scope,
    check_names,
]


def evaluate_doc(doc: str) -> list[Issue]:
    issues: list[Issue] = []
    for fn in CHECKS:
        issues.extend(fn(doc))
    return issues


_SKIP_DIRS = {".git", "vendor", "node_modules", "dist", "build", "bin", "__pycache__"}


def _yaml_files(target: Path):
    if target.is_file():
        yield target
        return
    for p in target.rglob("*"):
        if p.suffix not in {".yaml", ".yml"}:
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def audit(target: Path) -> list[CrdReport]:
    reports: list[CrdReport] = []
    for f in _yaml_files(target):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for doc in _iter_docs(text):
            if not _is_crd(doc):
                continue
            name = _name_of(doc, fallback=f.name)
            reports.append(CrdReport(file=str(f), name=name, issues=evaluate_doc(doc)))
    return reports


def render_text(reports: list[CrdReport]) -> int:
    if not reports:
        print("no CustomResourceDefinition documents found")
        return 0
    n_fail = sum(1 for r in reports for i in r.issues if i.level == FAIL)
    n_warn = sum(1 for r in reports for i in r.issues if i.level == WARN)
    print(f"scanned {len(reports)} CRD(s): {n_fail} FAIL, {n_warn} WARN\n")
    for r in reports:
        print(f"# {r.name}  ({r.file})")
        if not r.issues:
            print("  ok\n")
            continue
        for i in r.issues:
            print(f"  {i.level:4}  {i.code}: {i.message}")
        print()
    return 1 if n_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Kubernetes CRDs")
    ap.add_argument("--crd", required=True, help="Path to a CRD YAML file or directory")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    target = Path(args.crd)
    if not target.exists():
        print(f"error: not found: {args.crd}", file=sys.stderr)
        return 2

    reports = audit(target)
    if args.format == "json":
        payload = [{"file": r.file, "name": r.name, "issues": [asdict(i) for i in r.issues]} for r in reports]
        print(json.dumps(payload, indent=2))
        return 1 if any(i.level == FAIL for r in reports for i in r.issues) else 0
    return render_text(reports)


if __name__ == "__main__":
    sys.exit(main())
