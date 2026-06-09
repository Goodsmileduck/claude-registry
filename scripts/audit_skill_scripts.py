#!/usr/bin/env python3
"""Audit skill scripts for code-execution risks Claude shouldn't run unprompted.

Skill scripts ship as runnable helpers; a malicious or careless one is a
remote-code-execution vector the moment Claude executes it. This flags the
classic sinks via AST (no false positives from the words appearing in comments
or strings) for Python, plus a regex for pipe-to-shell in .sh files.

Pure Python 3 stdlib. Exit 0 when clean, 1 when any finding.
"""
import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Python sinks. dotted-name -> human reason. Matched against the call's full
# dotted path (os.system, subprocess.call, ...) and against bare names (eval).
DANGEROUS_CALLS = {
    "eval": "eval() executes arbitrary expressions",
    "exec": "exec() executes arbitrary code",
    "__import__": "__import__() loads arbitrary modules by name",
    "os.system": "os.system() runs a shell command string",
    "os.popen": "os.popen() runs a shell command string",
    "os.execv": "os.exec*() replaces the process with an arbitrary program",
    "os.execve": "os.exec*() replaces the process with an arbitrary program",
    "os.execvp": "os.exec*() replaces the process with an arbitrary program",
    "pty.spawn": "pty.spawn() runs an arbitrary program with a tty",
}
# subprocess.* calls are allowed, but shell=True turns the argument into a
# shell string (injection surface) — that specific kwarg is flagged separately.
SUBPROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen"}
# `curl … | sh`, `wget … | bash` and friends — fetch-and-execute in shell.
PIPE_TO_SHELL_RE = re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b")


@dataclass
class Finding:
    path: str
    rule: str
    message: str

    def __post_init__(self):
        self.path = str(self.path)  # callers pass Path objects

    def as_dict(self):
        return asdict(self)

    def as_line(self):
        return f"{self.path}: {self.rule}: {self.message}"


def _dotted_name(node):
    """Return the dotted path of a call target (os.system, a.b.c), or None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def audit_python(path, text):
    findings = []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        # A script that won't parse can't be reasoned about — surface it; the
        # compileall gate also catches this, so it's a belt-and-braces signal.
        return [Finding(path, "py-syntax", f"cannot parse: {exc.msg} (line {exc.lineno})")]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func)
        if dotted is None:
            continue
        bare = dotted.rsplit(".", 1)[-1]
        # exact dotted match (os.system) or bare-name match (eval/exec)
        reason = DANGEROUS_CALLS.get(dotted) or (DANGEROUS_CALLS.get(bare) if "." not in dotted else None)
        if reason:
            findings.append(Finding(path, "dangerous-call", f"line {node.lineno}: {dotted}() — {reason}"))
        if bare in SUBPROCESS_FUNCS:
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    findings.append(Finding(path, "shell-true",
                                            f"line {node.lineno}: {dotted}(shell=True) — shell injection surface"))
    return findings


def audit_shell(path, text):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        if PIPE_TO_SHELL_RE.search(line):
            findings.append(Finding(path, "pipe-to-shell",
                                    f"line {i}: fetch piped to a shell (curl|sh) — runs unverified remote code"))
    return findings


def find_script_files(repo_root):
    scripts = (repo_root / "plugins").glob("*/skills/*/scripts/**/*")
    return sorted(p for p in scripts if p.suffix in (".py", ".sh") and p.is_file())


def audit_repo(repo_root, script_files=None):
    if script_files is None:
        script_files = find_script_files(repo_root)
    findings = []
    for path in script_files:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            findings.extend(audit_python(path, text))
        else:
            findings.extend(audit_shell(path, text))
    root_resolved = repo_root.resolve()
    for f in findings:
        try:
            f.path = str(Path(f.path).resolve().relative_to(root_resolved))
        except ValueError:
            pass
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit skill scripts for code-execution risks.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)
    root = Path(args.root)
    script_files = find_script_files(root)
    findings = audit_repo(root, script_files)
    summary = f"Audited {len(script_files)} script(s); {len(findings)} finding(s)."
    print(summary, file=sys.stderr)
    if args.format == "json":
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for f in findings:
            print(f.as_line())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
