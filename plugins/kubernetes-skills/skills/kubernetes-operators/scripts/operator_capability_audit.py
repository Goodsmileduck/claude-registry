#!/usr/bin/env python3
"""Score a Kubernetes operator against the OperatorHub Capability Levels.

Walks the repo, collects evidence per level, and reports the highest level
where every required signal is present. Designed as a roadmap aid, not a
compliance check.

  L1 Basic Install      CRD + Deployment + Reconcile-bearing source
  L2 Seamless Upgrades  conversion webhook + leader election + PDB
  L3 Full Lifecycle     finalizers + status conditions + backup/restore evidence
  L4 Deep Insights      /metrics endpoint + Prometheus rules
  L5 Auto Pilot         autoscaler/autotune signals

Usage:
    operator_capability_audit.py --operator-dir path/to/repo
    operator_capability_audit.py --operator-dir path --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

SKIP_DIRS = {".git", "vendor", "node_modules", "dist", "build", "bin", "__pycache__"}
SCAN_EXTS = {".go", ".yaml", ".yml", ".md"}


# ----------- corpus -----------


@dataclass
class Corpus:
    paths: list[str]
    blob: str  # concatenated content; case-insensitive probes use re.IGNORECASE


def load(root: Path) -> Corpus:
    paths: list[str] = []
    chunks: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix not in SCAN_EXTS:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paths.append(str(p))
        # Tagging each chunk with its filename keeps file-aware checks cheap
        chunks.append(f"\n// FILE: {p}\n{text}")
    return Corpus(paths=paths, blob="".join(chunks))


# ----------- signal probes -----------
#
# A signal is `(name, probe(corpus) -> bool, hint_when_missing)`.


Probe = Callable[[Corpus], bool]


def has_text(needle: str) -> Probe:
    def _f(c: Corpus) -> bool:
        return needle in c.blob
    return _f


def has_regex(pattern: str, flags: int = 0) -> Probe:
    rx = re.compile(pattern, flags)
    def _f(c: Corpus) -> bool:
        return rx.search(c.blob) is not None
    return _f


@dataclass
class Signal:
    name: str
    probe: Probe
    hint: str


LEVELS: dict[str, tuple[str, list[Signal]]] = {
    "L1": ("Basic Install", [
        Signal("crd_manifest", has_regex(r"(?m)^kind:\s*CustomResourceDefinition"),
               "Add a CRD manifest under config/crd or chart/crds"),
        Signal("deployment_manifest", has_regex(r"(?m)^kind:\s*Deployment"),
               "Ship a Deployment for the controller itself"),
        Signal("reconcile_function", has_regex(r"\bfunc\s+\([^)]*\)\s+Reconcile\s*\("),
               "Implement a Reconcile method on the controller"),
    ]),
    "L2": ("Seamless Upgrades", [
        Signal("conversion_webhook", has_regex(r"\bconversion\s*:\s*\n\s+strategy\s*:\s*Webhook", re.MULTILINE),
               "Define spec.conversion.strategy: Webhook with a conversion webhook"),
        Signal("leader_election", has_regex(r"LeaderElection\b|leader[-_]elect"),
               "Enable leader election in main.go (mgr.Options.LeaderElection)"),
        Signal("pod_disruption_budget", has_regex(r"(?m)^kind:\s*PodDisruptionBudget"),
               "Add a PodDisruptionBudget for the controller"),
    ]),
    "L3": ("Full Lifecycle", [
        Signal("finalizer", has_regex(r"controllerutil\.(?:Add|Remove)Finalizer\b|\bfinalizers\s*:"),
               "Use finalizers to clean up external resources before deletion"),
        Signal("status_conditions", has_regex(r"metav1\.Condition\b|SetStatusCondition\b"),
               "Use metav1.Conditions + meta.SetStatusCondition for state"),
        Signal("backup_restore", has_regex(r"\b(backup|restore|snapshot)\b", re.IGNORECASE),
               "Document a backup/restore path; expose it via the CR (e.g. .spec.backup)"),
    ]),
    "L4": ("Deep Insights", [
        Signal("metrics_endpoint", has_regex(r"/metrics\b|metricsserver|MetricsBindAddress"),
               "Expose Prometheus /metrics from the controller manager"),
        Signal("prometheus_rules", has_regex(r"(?m)^kind:\s*PrometheusRule|^\s+alert\s*:"),
               "Ship PrometheusRules for the operator's SLOs and key error counters"),
    ]),
    "L5": ("Auto Pilot", [
        Signal("autoscaler_referenced", has_regex(r"HorizontalPodAutoscaler|VerticalPodAutoscaler|\bautoscal(er|ing)\b", re.IGNORECASE),
               "Drive HPA/VPA from the operator or document auto-scaling behavior"),
        Signal("autotune_or_anomaly", has_regex(r"\bautotune|self[-_]heal|anomaly|tuning\b", re.IGNORECASE),
               "Implement (and name) an autotune/anomaly response loop"),
    ]),
}

LEVEL_ORDER = ["L1", "L2", "L3", "L4", "L5"]


# ----------- evaluation -----------


@dataclass
class LevelResult:
    level: str
    name: str
    achieved: bool
    passing: list[str]
    missing: list[tuple[str, str]]  # (signal_name, hint)


@dataclass
class Report:
    current_level: str | None
    levels: list[LevelResult]


def evaluate(c: Corpus) -> Report:
    results: list[LevelResult] = []
    current: str | None = None
    progression_broken = False
    for lvl in LEVEL_ORDER:
        title, signals = LEVELS[lvl]
        passing: list[str] = []
        missing: list[tuple[str, str]] = []
        for s in signals:
            if s.probe(c):
                passing.append(s.name)
            else:
                missing.append((s.name, s.hint))
        achieved = not missing
        results.append(LevelResult(level=lvl, name=title, achieved=achieved,
                                   passing=passing, missing=missing))
        if achieved and not progression_broken:
            current = lvl
        else:
            progression_broken = True
    return Report(current_level=current, levels=results)


def render_text(report: Report, root: Path) -> None:
    if report.current_level is None:
        print(f"operator at {root}: no level achieved (L1 signals missing)")
    else:
        current_name = next(l.name for l in report.levels if l.level == report.current_level)
        print(f"operator at {root}: current level = {report.current_level} ({current_name})")
    print()
    for r in report.levels:
        mark = "✓" if r.achieved else "·"
        print(f"  {mark} {r.level} {r.name}  ({len(r.passing)}/{len(r.passing) + len(r.missing)} signals)")
        for name, hint in r.missing:
            print(f"      missing: {name} — {hint}")
    # surface "next level" actionables
    for r in report.levels:
        if r.level == report.current_level:
            continue
        if not r.achieved:
            print()
            print(f"next: advance to {r.level} {r.name} by addressing:")
            for name, hint in r.missing:
                print(f"  - {name}: {hint}")
            break


def main() -> int:
    ap = argparse.ArgumentParser(description="Score operator against OperatorHub Capability Levels")
    ap.add_argument("--operator-dir", required=True, help="Path to the operator repo root")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    root = Path(args.operator_dir)
    if not root.is_dir():
        print(f"error: not a directory: {args.operator_dir}", file=sys.stderr)
        return 2

    corpus = load(root)
    report = evaluate(corpus)

    if args.format == "json":
        payload = {
            "current_level": report.current_level,
            "levels": [
                {
                    "level": r.level,
                    "name": r.name,
                    "achieved": r.achieved,
                    "passing": r.passing,
                    "missing": [{"signal": n, "hint": h} for (n, h) in r.missing],
                }
                for r in report.levels
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        render_text(report, root)
    return 1 if report.current_level is None else 0


if __name__ == "__main__":
    sys.exit(main())
