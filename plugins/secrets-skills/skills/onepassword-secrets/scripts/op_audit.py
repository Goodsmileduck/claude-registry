#!/usr/bin/env python3
"""Audit 1Password `op` CLI access for Claude Code.

A PreToolUse hook that logs every `op` invocation and denies high-risk wrapped
child commands, plus a `verify` integrity check over the log. Stdlib only: no
subprocess, no network, no eval/exec.

The hook is INERT unless opted in: set OP_AUDIT_ENABLED=1. This keeps the shipped
plugin passive on install -- the user explicitly arms the audit.
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

OP_REF_RE = re.compile(r"op://[^\s'\"]+")
# Leaf commands that, handed a resolved secret via `op run -- <child>` (or an
# `op inject` pipeline), can exfiltrate it in a single approved call.
RISKY_CHILD_LEADERS = {
    "sh", "bash", "zsh", "dash", "ksh",
    "curl", "wget", "nc", "ncat", "netcat", "socat", "telnet",
    "ssh", "scp", "sftp",
    "python", "python3", "perl", "ruby", "node", "deno", "bun", "php",
}
DEFAULT_LOG = Path(os.path.expanduser("~/.claude/logs/op-access.jsonl"))
SCHEMA_KEYS = {"ts", "session_id", "cwd", "op_subcommand", "refs", "child", "decision"}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_assignment(token):
    """True for a leading `VAR=value` env-assignment token (not a flag)."""
    return "=" in token and not token.startswith("-")


def parse_op_command(command):
    """Return {op_subcommand, refs, child} if `command` invokes op, else None.

    Recognizes the literal `op` as the leading token (optionally behind `env`).
    Deliberate obfuscation (`$OP`, `sh -c 'op ...'`) is out of scope here and is
    covered by the separate always-ask permission rule, documented in the skill.
    """
    tokens = command.strip().split()
    if not tokens:
        return None
    idx = 0
    if tokens[idx] == "env":
        idx += 1
        # skip `VAR=value` assignments that follow `env` (e.g. `env FOO=bar op ...`)
        while idx < len(tokens) and _is_assignment(tokens[idx]):
            idx += 1
    if idx >= len(tokens):
        return None
    if os.path.basename(tokens[idx]) != "op":
        return None
    sub = tokens[idx + 1] if idx + 1 < len(tokens) else ""
    refs = OP_REF_RE.findall(command)
    child = None
    if " -- " in command:
        child = command.split(" -- ", 1)[1].strip() or None
    return {"op_subcommand": sub, "refs": refs, "child": child}


def child_leader(child):
    """First real command word of a wrapped child, skipping FOO=bar prefixes."""
    if not child:
        return None
    for tok in child.split():
        if _is_assignment(tok):
            continue
        return os.path.basename(tok)
    return None


def assess_risk(parsed):
    """Return (decision, reason). 'deny' for a risky wrapped child, else 'allow'."""
    if parsed["op_subcommand"] == "run":
        leader = child_leader(parsed["child"])
        if leader in RISKY_CHILD_LEADERS:
            return ("deny",
                    "op run wraps '%s', which can exfiltrate the resolved secret in "
                    "one approved call. Review the full command line; rerun without "
                    "the shell/network child if unintended."
                    % leader)
    return ("allow", "")


def make_entry(parsed, now, session_id, cwd, decision):
    return {
        "ts": now,
        "session_id": session_id,
        "cwd": cwd,
        "op_subcommand": parsed["op_subcommand"],
        "refs": parsed["refs"],
        "child": parsed["child"],
        "decision": decision,
    }


def append_log(path, entry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def run_hook(stdin_text, now, log_path):
    """Process one PreToolUse event. Return a decision dict to print, or None."""
    if os.environ.get("OP_AUDIT_ENABLED") != "1":
        return None  # inert until armed
    try:
        event = json.loads(stdin_text)
    except (ValueError, TypeError):
        return None
    if event.get("tool_name") != "Bash":
        return None
    command = (event.get("tool_input") or {}).get("command", "")
    parsed = parse_op_command(command)
    if parsed is None:
        return None  # not an op call; stay silent -> normal permission flow
    decision, reason = assess_risk(parsed)
    append_log(log_path, make_entry(
        parsed, now, event.get("session_id", ""), event.get("cwd", ""), decision))
    if decision == "deny":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}
    return None  # allow -> silent; the always-ask rule still prompts interactively


def verify_lines(lines):
    """Return a list of finding strings over audit-log lines."""
    findings = []
    prev_ts = None
    for i, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            findings.append("line %d: not valid JSON" % i)
            continue
        extra = entry.keys() - SCHEMA_KEYS
        missing = SCHEMA_KEYS - entry.keys()
        if extra:
            findings.append("line %d: unexpected field(s) %s (log must hold only "
                            "refs + metadata)" % (i, sorted(extra)))
        if missing:
            findings.append("line %d: missing field(s) %s" % (i, sorted(missing)))
        refs = entry.get("refs", [])
        if not isinstance(refs, list) or any(not str(r).startswith("op://") for r in refs):
            findings.append("line %d: 'refs' must be a list of op:// references" % i)
        ts = entry.get("ts")
        if prev_ts is not None and ts is not None and ts < prev_ts:
            findings.append("line %d: timestamp '%s' precedes previous '%s' "
                            "(possible tampering)" % (i, ts, prev_ts))
        if ts is not None:
            prev_ts = ts
    return findings


def cmd_hook(args):
    out = run_hook(sys.stdin.read(), utc_now_iso(), args.log or DEFAULT_LOG)
    if out is not None:
        print(json.dumps(out))
    return 0


def cmd_verify(args):
    path = Path(args.log or DEFAULT_LOG)
    if not path.exists():
        if args.format == "json":
            print(json.dumps({"ok": True, "findings": [], "note": "no log yet"}))
        else:
            print("No log at %s; nothing to verify." % path)
        return 0
    findings = verify_lines(path.read_text(encoding="utf-8").splitlines())
    if args.format == "json":
        print(json.dumps({"ok": not findings, "findings": findings}))
    else:
        if findings:
            print("%d finding(s) in %s:" % (len(findings), path))
            for f in findings:
                print("  - " + f)
        else:
            print("OK: %s conforms to the audit schema." % path)
    return 1 if findings else 0


def build_parser():
    p = argparse.ArgumentParser(description="1Password op access audit for Claude Code.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log", help="Audit log path (default %s)." % DEFAULT_LOG)
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook", parents=[common],
                       help="PreToolUse hook: read event JSON on stdin, log, maybe deny.")
    h.set_defaults(func=cmd_hook)
    v = sub.add_parser("verify", parents=[common],
                       help="Check the audit log's schema/integrity.")
    v.add_argument("--format", choices=["text", "json"], default="text")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv=None):
    # Fast path: this runs as a PreToolUse hook on *every* Bash call, so an
    # unarmed `hook` invocation must exit before argparse and the stdin read.
    cli = sys.argv[1:] if argv is None else list(argv)
    if cli[:1] == ["hook"] and os.environ.get("OP_AUDIT_ENABLED") != "1":
        return 0
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
