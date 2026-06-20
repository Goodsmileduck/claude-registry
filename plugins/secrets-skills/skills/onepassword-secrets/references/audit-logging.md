> Parent: ../SKILL.md

# Audit Logging — 1Password CLI

## Contents

- [Audit surfaces overview](#audit-surfaces-overview)
- [Surface 1: 1Password server-side audit (authoritative)](#surface-1-1password-server-side-audit-authoritative)
- [Surface 2: op_audit.py PreToolUse hook (enforced, opt-in)](#surface-2-op_auditpy-pretooluse-hook-enforced-opt-in)
- [Surface 3: local JSONL log (advisory)](#surface-3-local-jsonl-log-advisory)
- [Arming the hook](#arming-the-hook)
- [JSONL schema](#jsonl-schema)
- [Log location, permissions, and retention](#log-location-permissions-and-retention)
- [Verifying the log with op_audit.py verify](#verifying-the-log-with-op_auditpy-verify)
- [What each finding means](#what-each-finding-means)

---

## Audit surfaces overview

Three distinct surfaces record 1Password secret access. They differ in authority, enforcement strength, and scope:

| Surface | Enforced vs advisory | Who controls it | Tamper-evident |
|---|---|---|---|
| 1Password server-side audit | Authoritative (external enforcement) | 1Password service | Yes — cryptographically signed by 1Password |
| `op_audit.py` PreToolUse hook | **Enforced** (can deny calls) | Plugin + user opt-in | No — local filesystem |
| Local JSONL log (`op-access.jsonl`) | Advisory | Local filesystem | Optional (`chattr +a`) |

The relationship between the three: the server-side audit is the ground truth; the hook is the active gate during a Claude Code session; the local log is the hook's output and is useful for per-session review but is non-authoritative.

---

## Surface 1: 1Password server-side audit (authoritative)

Every `op` CLI call that resolves a secret value — including `op run`, `op inject`, `op read`, and `op item get --reveal` — is recorded server-side by 1Password regardless of local state. This record:

- Is scoped to the vault and account.
- Is available in the 1Password admin console under Reports > Activity Log (exact navigation depends on the 1Password plan).
- Records the actor (interactive account or service account), the item accessed, the field referenced, and a timestamp.
- Is not controllable by this plugin and cannot be suppressed or deleted from the client side.

To view the activity log:

1. Sign in to `<team>.1password.com` or open the 1Password desktop app.
2. Navigate to the admin console (requires team administrator or security auditor role).
3. Under Reports or Audit (plan-dependent), filter by vault, actor, or time range.

The server-side log is the record that satisfies compliance and incident-response requirements. The local hook log complements it for session-level deny decisions and rapid review — it does not replace it.

---

## Surface 2: op_audit.py PreToolUse hook (enforced, opt-in)

The `scripts/op_audit.py` hook runs as a Claude Code PreToolUse hook. It is **inert by default** and must be explicitly armed (see below). When armed it:

1. Intercepts every `Bash` tool call before execution.
2. Parses the command for an `op` invocation (recognizes `op` as the leading token, optionally behind `env`).
3. Logs the call to the local JSONL log.
4. Evaluates the risk of the wrapped child command (for `op run` only; `op inject` writes a file rather than wrapping a child, so its protection is the disk-hygiene guidance, not a child-command deny).
5. **Denies automatically** if the `op run` wrapped child is a shell, common network tool, or interpreter — see `scripts/op_audit.py` (`RISKY_CHILD_LEADERS`) for the exact set.
6. Returns a deny decision to Claude Code, which aborts the call without executing it.

For allowed calls (not matching the risky-child set), the hook logs the entry and returns silently — the always-ask permission rule then prompts interactively as normal. This means the hook provides a second deny layer, not a bypass of the always-ask rule described in `../SKILL.md`.

### Deliberate obfuscation is out of scope

The hook recognizes the literal `op` token as the command leader. Obfuscated invocations — `sh -c 'op ...'`, `$OP`, shell wrappers — are not intercepted by the hook. This is a known limitation; see the bypass-surfaces discussion in `references/setup-and-auth.md` and the cross-cutting rules in `../SKILL.md`.

---

## Surface 3: local JSONL log (advisory)

The hook writes one JSON line per intercepted `op` call to `~/.claude/logs/op-access.jsonl`. This log:

- Records only calls that the hook detected (i.e., literal `op` as command leader while `OP_AUDIT_ENABLED=1`).
- Is a local file subject to filesystem-level modification by any process running as the same user.
- Does **not** provide cryptographic proof of authenticity — see the Threat Model section of `../SKILL.md`.
- Is useful for per-session review and tooling (the `verify` command).

---

## Arming the hook

### Step 1 — Wire the hook into Claude Code settings

The hook is shipped in `hooks/hooks.json` at the plugin root. To activate it, the content of `hooks.json` must be merged into the Claude Code hooks configuration, which lives in `.claude/settings.json` (project-level) or `~/.claude/settings.json` (user-level):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/skills/onepassword-secrets/scripts/op_audit.py hook",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

`${CLAUDE_PLUGIN_ROOT}` resolves to the plugin's root directory at runtime. Adjust the path if the plugin is installed in a non-standard location.

### Step 2 — Set OP_AUDIT_ENABLED=1

The hook checks for `OP_AUDIT_ENABLED=1` on every invocation and returns immediately (no-op) if the variable is absent or any other value. This keeps the plugin passive on install.

For a development shell:

```bash
export OP_AUDIT_ENABLED=1
```

For a CI environment, set `OP_AUDIT_ENABLED=1` as an environment variable in the CI platform's job configuration alongside `OP_SERVICE_ACCOUNT_TOKEN`.

**Both steps are required.** Wiring the hook without setting `OP_AUDIT_ENABLED=1` has no effect. Setting `OP_AUDIT_ENABLED=1` without wiring the hook has no effect.

---

## JSONL schema

Each line in `~/.claude/logs/op-access.jsonl` is a JSON object with exactly these seven fields:

| Field | Type | Description |
|---|---|---|
| `ts` | string (ISO 8601 UTC) | Timestamp of the hook invocation |
| `session_id` | string | Claude Code session identifier from the PreToolUse event |
| `cwd` | string | Working directory at the time of the `op` call |
| `op_subcommand` | string | The `op` subcommand (e.g. `run`, `inject`, `read`, `item`) |
| `refs` | array of strings | All `op://Vault/Item/field` references found in the command |
| `child` | string or null | For `op run`, the wrapped child command string; otherwise null |
| `decision` | string | `"allow"` or `"deny"` |

Example entry:

```json
{"ts": "2025-06-20T10:14:32.001Z", "session_id": "abc123", "cwd": "/home/user/myapp", "op_subcommand": "run", "refs": ["op://Production/Postgres/connection-string"], "child": "python3", "decision": "deny"}
```

The schema is exact: `verify` flags any entry that contains additional fields beyond these seven or is missing any of them. No extra fields are permitted in the log — this prevents log injection from smuggling metadata that could be confused with hook-generated entries.

---

## Log location, permissions, and retention

### Default log path

```
~/.claude/logs/op-access.jsonl
```

The directory `~/.claude/logs/` is created on first write if it does not exist.

### Permissions

The log file and its parent directory must be readable only by the owning user:

```bash
chmod 0700 ~/.claude/logs/
chmod 0600 ~/.claude/logs/op-access.jsonl
```

The hook creates the file with these permissions on first write. Verify periodically that the permissions have not been widened (e.g., by a misconfigured umask).

### Optional: append-only flag

On Linux, `chattr +a` marks the file append-only at the filesystem level, preventing truncation or overwriting by any process (including the owning user without `CAP_LINUX_IMMUTABLE`):

```bash
sudo chattr +a ~/.claude/logs/op-access.jsonl
```

This is optional and advisory — it raises the bar for local log tampering but does not provide cryptographic guarantees. A user with sufficient privilege can remove the flag. Rely on the 1Password server-side audit for authoritative, tamper-evident records.

### Retention

The local log grows without bound; no automatic rotation is built into the hook. Options:

- `logrotate` with `copytruncate` (note: truncation removes the `chattr +a` protection; choose between rotation and append-only).
- Manual archival: copy the file to a timestamped archive and truncate it at the start of each session or audit period.
- Keep indefinitely if storage permits; the file is plain text and compresses well.

---

## Verifying the log with op_audit.py verify

The `verify` subcommand checks the local log for schema conformance and structural integrity.

```bash
python3 <plugin>/skills/onepassword-secrets/scripts/op_audit.py verify
```

JSON output (for tooling or CI):

```bash
python3 <plugin>/skills/onepassword-secrets/scripts/op_audit.py verify --format json
```

**Important:** `verify` checks schema conformance and structural integrity only. It is **not** a cryptographic verification. It cannot determine whether a log entry was generated by the legitimate hook or injected by an attacker with filesystem access. For tamper-evident audit, use the 1Password server-side activity log.

What `verify` detects:

- **Invalid JSON lines** — entries that cannot be parsed at all.
- **Missing required fields** — any of the seven schema fields is absent.
- **Unexpected extra fields** — any field beyond the seven schema fields is present.
- **Invalid `refs` values** — entries where `refs` is not a list, or any element does not start with `op://`.
- **Out-of-order timestamps** — an entry whose `ts` value is lexicographically earlier than the previous entry's `ts`, which may indicate log rotation artifacts or manual editing.

What `verify` does not detect:

- Whether the `decision` values are correct (it cannot re-run the risk assessment).
- Whether entries were deleted.
- Whether the hook was actually running when the corresponding `op` calls occurred.
- Cryptographic integrity — any of the above findings could be absent in a carefully crafted forged log.

---

## What each finding means

| Finding | Meaning | Recommended action |
|---|---|---|
| `line N: not valid JSON` | The line is malformed — either the log was truncated mid-write or manually edited | Inspect the raw line; if it is a partial write from a crash, it can be removed; investigate if tampering is suspected |
| `line N: missing field(s) [...]` | A required schema field is absent | The hook version may be mismatched with the verifier; check the plugin version; manual edits should be reverted |
| `line N: unexpected field(s) [...]` | Extra fields not in the schema are present | Possible log injection; cross-reference the 1Password server-side audit for the same time window |
| `line N: 'refs' must be a list of op:// references` | The `refs` field is absent, not a list, or contains non-`op://` strings | Investigate manual edits or a hook code change that altered the field format |
| `line N: timestamp '...' precedes previous '...'` | Timestamps are out of order | May indicate log rotation (safe if expected), manual editing, or an out-of-order write race; investigate if unexpected |
| `OK: <path> conforms to the audit schema.` | All lines pass all checks | No action required |
