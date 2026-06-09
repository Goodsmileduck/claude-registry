#!/usr/bin/env python3
"""Lint Claude skills against the registry's machine-checkable authoring rules.

Human-readable rules live in AGENTS.md / CLAUDE.md; this enforces only the
mechanical subset. Pure Python 3 stdlib. Exit 0 when clean, 1 when any finding.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Thresholds (from AGENTS.md):
MAX_NAME_LEN = 64          # name max 64 chars
MAX_DESC_LEN = 1024        # description max 1024 chars
MAX_BODY_LINES = 500       # SKILL.md hard line limit
REF_TOC_THRESHOLD = 100    # reference files > this need a TOC
REF_TOC_SCAN_LINES = 30    # how far down we look for a TOC marker (heuristic)

NAME_RE = re.compile(r"^[a-z0-9-]+$")
VAGUE_NAMES = {"helper", "utils", "tools", "documents", "data", "files"}
FORBIDDEN_NAME_WORDS = ("anthropic", "claude")
FIRST_PERSON_RE = re.compile(r"\b(I can|I'll|I will|you can|you should)\b", re.IGNORECASE)
WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
# A path segment, a backslash, then another segment — catches scripts\helper.py.
# Markdown escapes like \[ won't match (next char not in the class).
WINDOWS_PATH_RE = re.compile(r"[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+")
# Code spans/fences are stripped before content scans so that TOML array-of-tables
# (`[[routes]]`) and jsonpath escapes (`{.a\.b}`) inside code don't false-positive.
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


class Finding:
    def __init__(self, path, rule, message):
        self.path = str(path)
        self.rule = rule
        self.message = message

    def as_dict(self):
        return {"path": self.path, "rule": self.rule, "message": self.message}

    def as_line(self):
        return f"{self.path}: {self.rule}: {self.message}"


def parse_frontmatter(text):
    """Return (fields, ok). Minimal leading `--- ... ---` scanner, NOT a YAML parser.

    Extracts top-level `key: value` scalars. Indented continuation lines append to
    the previous value (folded descriptions). Surrounding quotes stripped. ok is
    False when there is no leading block or it is never closed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, False
    fields, last_key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields, True
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            last_key = m.group(1)
            fields[last_key] = m.group(2).strip().strip('"').strip("'")
        elif last_key and line.strip():
            fields[last_key] = (fields[last_key] + " " + line.strip()).strip()
    return fields, False  # no closing --- => malformed


def strip_code(text):
    """Remove fenced blocks and inline code spans so config/jsonpath syntax inside
    code is not mistaken for wikilinks or Windows paths."""
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


def content_findings(path, text):
    out = []
    prose = strip_code(text)
    if WIKILINK_RE.search(prose):
        out.append(Finding(path, "wikilinks", "contains [[wikilink]]; use plain-prose cross-refs"))
    if WINDOWS_PATH_RE.search(prose):
        out.append(Finding(path, "windows-path", "contains a backslash path; use forward slashes"))
    return out


def has_toc(lines):
    head = lines[:REF_TOC_SCAN_LINES]
    for line in head:
        low = line.lower()
        if low.startswith("## ") and "table of contents" in low:
            return True
        if low.startswith("## contents"):
            return True
    # two or more markdown link-list items near the top count as a TOC
    link_items = sum(1 for l in head if re.match(r"^\s*[-*] \[.+\]\(.+\)", l))
    return link_items >= 2


def lint_references(skill_dir):
    findings = []
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return findings
    # Note: directory organization under references/ (e.g. references/recipes/) is
    # allowed — the "one level deep" rule targets reference->reference *chaining*,
    # which is not reliably machine-checkable, so it is left to human review.
    for entry in sorted(refs_dir.rglob("*")):
        if entry.is_dir():
            continue
        if entry.suffix != ".md":
            continue
        text = entry.read_text(encoding="utf-8")
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            findings.append(Finding(entry, "refs-no-frontmatter",
                                    "reference file has YAML frontmatter; refs are not skills"))
        if len(lines) > REF_TOC_THRESHOLD and not has_toc(lines):
            findings.append(Finding(entry, "refs-toc",
                                    f"reference > {REF_TOC_THRESHOLD} lines without a table of contents"))
        findings.extend(content_findings(entry, text))
    return findings


def lint_skill(skill_dir):
    findings = []
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fields, ok = parse_frontmatter(text)
    if not ok or "name" not in fields or "description" not in fields:
        findings.append(Finding(skill_md, "frontmatter-present",
                                "missing or malformed --- frontmatter with name + description"))
    name = fields.get("name", "")
    if name:
        if not NAME_RE.match(name):
            findings.append(Finding(skill_md, "name-charset", f"name '{name}' must match [a-z0-9-]+"))
        if len(name) > MAX_NAME_LEN:
            findings.append(Finding(skill_md, "name-length", f"name exceeds {MAX_NAME_LEN} chars"))
        # 'claude-md' refers to the CLAUDE.md file, not the assistant — allow it;
        # 'claude'/'anthropic' anywhere else is still flagged (false-provenance branding).
        debranded = name.lower().replace("claude-md", "")
        if any(w in debranded for w in FORBIDDEN_NAME_WORDS):
            findings.append(Finding(skill_md, "name-forbidden-word", "name contains 'anthropic' or 'claude'"))
        if name in VAGUE_NAMES:
            findings.append(Finding(skill_md, "name-vague", f"name '{name}' is too vague"))
    if "description" in fields:
        desc = fields["description"]
        if not desc.strip():
            findings.append(Finding(skill_md, "desc-nonempty", "description is empty"))
        if len(desc) > MAX_DESC_LEN:
            findings.append(Finding(skill_md, "desc-length", f"description exceeds {MAX_DESC_LEN} chars"))
        if FIRST_PERSON_RE.search(desc):
            findings.append(Finding(skill_md, "desc-first-person",
                                    "description uses first/second person; write in third person"))
    if len(text.splitlines()) > MAX_BODY_LINES:
        findings.append(Finding(skill_md, "body-line-count", f"SKILL.md exceeds {MAX_BODY_LINES} lines"))
    findings.extend(content_findings(skill_md, text))
    findings.extend(lint_references(skill_dir))
    return findings


def _load_json(path):
    """Return (data, error_message)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, str(exc)


def lint_manifests(repo_root):
    findings = []
    plugins_dir = repo_root / "plugins"
    market_path = repo_root / ".claude-plugin" / "marketplace.json"

    market, err = _load_json(market_path)
    if err is not None:
        findings.append(Finding(market_path, "json-valid", f"cannot parse marketplace.json: {err}"))

    on_disk = sorted(
        p.name for p in plugins_dir.iterdir()
        if p.is_dir() and (p / ".claude-plugin" / "plugin.json").exists()
    ) if plugins_dir.is_dir() else []

    market_entries = {}
    if isinstance(market, dict):
        for entry in market.get("plugins", []):
            if isinstance(entry, dict) and "name" in entry:
                market_entries[entry["name"]] = entry

    # "home" source repos = those used by plugins that DO have a local dir. A
    # marketplace entry sourced from a *different* github repo is a curated-upstream
    # plugin (e.g. cloudflare/skills) and is not expected to exist locally.
    home_repos = set()
    for name in on_disk:
        src = market_entries.get(name, {}).get("source")
        if isinstance(src, dict) and src.get("repo"):
            home_repos.add(src["repo"])

    def is_external(entry):
        src = entry.get("source")
        if isinstance(src, dict) and src.get("source") == "github":
            return src.get("repo") not in home_repos
        return False

    # bidirectional registration (external/upstream entries exempt from the dir check)
    for name in on_disk:
        if name not in market_entries:
            findings.append(Finding(market_path, "plugin-registered",
                                    f"plugin '{name}' on disk is not in marketplace.json"))
    for name, entry in market_entries.items():
        if name not in on_disk and not is_external(entry):
            findings.append(Finding(market_path, "plugin-registered",
                                    f"marketplace plugin '{name}' has no plugins/{name}/ dir"))

    # per-plugin.json checks
    for name in on_disk:
        pj_path = plugins_dir / name / ".claude-plugin" / "plugin.json"
        pj, err = _load_json(pj_path)
        if err is not None:
            findings.append(Finding(pj_path, "json-valid", f"cannot parse plugin.json: {err}"))
            continue
        if pj.get("name") != name:
            findings.append(Finding(pj_path, "plugin-name-matches-dir",
                                    f"plugin.json name '{pj.get('name')}' != dir '{name}'"))
        if not str(pj.get("description", "")).strip():
            findings.append(Finding(pj_path, "plugin-desc-nonempty", "plugin.json description is empty"))

    # conditional skills[] checks
    for name, entry in market_entries.items():
        skills = entry.get("skills")
        if not isinstance(skills, list):
            continue
        listed = set()
        for s in skills:
            d = repo_root / str(s).lstrip("./")
            listed.add(d.name)
            if not (d / "SKILL.md").exists():
                findings.append(Finding(market_path, "marketplace-skill-stale",
                                        f"{name}: '{s}' has no SKILL.md"))
        skills_dir = plugins_dir / name / "skills"
        if skills_dir.is_dir():
            for sd in sorted(skills_dir.iterdir()):
                if (sd / "SKILL.md").exists() and sd.name not in listed:
                    findings.append(Finding(market_path, "marketplace-skill-complete",
                                            f"{name}: skill '{sd.name}' not in skills[]"))
    return findings


def find_skill_dirs(repo_root):
    return sorted(p.parent for p in (repo_root / "plugins").glob("*/skills/*/SKILL.md"))


def lint_repo(repo_root):
    findings = []
    for sd in find_skill_dirs(repo_root):
        findings.extend(lint_skill(sd))
    findings.extend(lint_manifests(repo_root))
    # normalize paths to be relative to repo_root for stable output
    for f in findings:
        try:
            f.path = str(Path(f.path).resolve().relative_to(repo_root.resolve()))
        except ValueError:
            pass
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lint Claude skills against registry best practices.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)
    findings = lint_repo(Path(args.root))
    if args.format == "json":
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for f in findings:
            print(f.as_line())
        print(f"\n{len(findings)} finding(s).", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
