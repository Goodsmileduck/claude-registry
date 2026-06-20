---
name: onepassword-secrets
description: Injects and audits 1Password secrets via the op CLI using op:// references, prefers op run/op inject over op read so values stay out of context, gates every op call behind an always-ask permission rule, and ships an opt-in deny-capable audit hook. Use when handling API keys, tokens, passwords, credentials, .env secrets, OP_SERVICE_ACCOUNT_TOKEN, or when the user mentions 1Password or the op CLI.
---

## Cross-cutting rules

These rules apply to every operation in this skill without exception.

- **Never print or restate a resolved secret value.** If a resolved value appears in output, treat it as compromised and rotate it immediately.
- **Reference secrets only as `op://Vault/Item/field`** (or `op://Vault/Item/Section/field`). Never embed raw values in code, templates, or responses.
- **Prefer `op run` over `op inject` over `op read`.** Each step down the hierarchy increases the risk that a resolved value reaches context or disk.
- **`op item get --reveal` and `op item get` with `--format json` are as dangerous as `op read`**: they print resolved values and must be treated with the same caution.
- **Always scope with `--vault`** to prevent accidental resolution against the wrong vault.
- **The wrapped child in `op run -- <cmd>` is the real risk surface**: the child inherits all resolved environment variables. Network tools, shells, and interpreters can exfiltrate those values in a single approved call.
- **Every `op` invocation requires explicit approval.** Never assume a prior approval covers a new call. Never run `op` speculatively. Never advise the user to select "always allow" for `op`.

## Setup & auth

The `op` CLI must be installed, signed in (`op signin`), and connected to a 1Password account before any secret resolution is possible. Service account authentication uses the `OP_SERVICE_ACCOUNT_TOKEN` environment variable instead of an interactive session. Biometric unlock requires the desktop app. For installation steps, sign-in flows, and service account configuration, see `references/setup-and-auth.md`.

## Inject secrets

### Environment injection (preferred)

Create a `.env.tpl` file containing only `op://` references — no raw values. The template file is safe to commit.

```text
# .env.tpl  (safe to commit — contains references, not values)
DB_URL=op://Production/Postgres/connection-string
OPENAI_API_KEY=op://Production/OpenAI/credential
STRIPE_SECRET=op://Production/Stripe/secret_key
```

Run the application with secrets injected at process start:

```shell
op run --env-file=.env.tpl --vault Production -- ./myapp start
```

Secrets never touch disk; they exist only in the child process environment for the duration of the call.

### Discover available items (no values)

```shell
op item list --vault Production
op vault list
```

These commands enumerate items and vaults without resolving field values; they are safe to run for discovery.

### File injection (`op inject`)

Use `op inject` only when a tool requires a physical config file and `op run` is not applicable. Disk hygiene is mandatory:

```shell
# Write to tmpfs (Linux) to keep the file off persistent storage
op inject -i config.yml.tpl -o /tmp/config.yml
chmod 600 /tmp/config.yml
# Clean up in a trap so the file is removed even on error
trap 'rm -f /tmp/config.yml' EXIT
```

Additional constraints:

- Prefer `/dev/shm` or a named pipe (`mkfifo`) over a regular tempfile.
- Add any output path to `.gitignore` before writing.
- Refuse to write the output file inside a git work tree unless the path is already listed in `.gitignore`.

## Read a single value (last resort)

`op read` resolves one field to stdout. Use only when the caller explicitly needs the raw value and `op run` cannot satisfy the requirement.

```shell
# Last resort: rotates responsibility for the value to the caller immediately
op read --vault Production op://Production/GitHub/credentials/personal_token
```

Warn before running: the resolved value will appear in terminal output and may enter shell history or logs. Treat it as exposed the moment it is printed and advise rotation if there is any doubt about where it landed.

## Audit logging

1Password's server-side audit log is the authoritative record of every secret access; it is tamper-evident, vault-scoped, and available in the 1Password admin console regardless of local state. The opt-in PreToolUse hook in this plugin provides a local deny-capable layer: arm it by setting `OP_AUDIT_ENABLED=1` before starting Claude Code. When armed, every `op` Bash invocation is logged to `~/.claude/logs/op-access.jsonl` and high-risk wrapped children (shells, network tools, interpreters) are denied automatically. The local log is non-authoritative and complements, not replaces, the 1Password server-side record. For log format, field schema, and retention guidance, see `references/audit-logging.md`.

## Verify the log

Check log schema conformance and integrity (timestamp ordering, field completeness, valid `op://` references in each entry):

```shell
python3 <plugin>/skills/onepassword-secrets/scripts/op_audit.py verify
python3 <plugin>/skills/onepassword-secrets/scripts/op_audit.py verify --format json
```

`verify` checks log schema conformance and structural integrity — it is not a cryptographic verification. It detects malformed entries, missing required fields, unexpected extra fields, and out-of-order timestamps that may indicate tampering or log rotation issues.

## Error recovery

| Symptom | Resolution |
|---|---|
| `op: command not found` | Install the 1Password CLI via the official package for the platform (see `references/setup-and-auth.md`). |
| `[ERROR] 401 Unauthorized` / `not signed in` | Run `op signin` (interactive) or set `OP_SERVICE_ACCOUNT_TOKEN` (headless). |
| `[ERROR] 403 Forbidden` on a vault | The authenticated account lacks access to that vault; check vault permissions in the 1Password admin console. |
| `[ERROR] item not found` | Verify vault name and item name with `op item list --vault <Vault>`; names are case-sensitive. |
| `[ERROR] field not found` | Confirm the full reference path with `op item get <Item> --vault <Vault> --format json` (inspect the `reference` key, not the `value` key). |

## Proactive triggers

- **A `.env` file containing raw credentials is about to be committed** → refuse, offer to convert each value to an `op://` reference and produce a `.env.tpl`.
- **`op read` is proposed where `op run` would work** → switch to `op run --env-file` pattern and explain why.
- **An `op` call is missing `--vault`** → add explicit `--vault` scope before running to prevent resolution against the wrong vault.
- **A service-account token (`OP_SERVICE_ACCOUNT_TOKEN`) is about to be printed or logged** → refuse and explain it grants vault-wide access; rotate if already exposed.
- **`op run -- <shell|curl|wget|python|node|…>`** is proposed → warn that the child process receives all resolved secrets and can exfiltrate them in the single approved call; ask the user to confirm the child command is the intended recipient.
- **`op item get` with `--reveal` or `--format json` is proposed for a lookup that only needs the reference** → redirect to `op item list --vault <V>` or `op item get <Item> --vault <V> --format json` with explicit awareness that field values are resolved in the output.
- **Credentials appear in a script as `export FOO=$(op read ...)`** → replace with `op run --env-file` to avoid shell history capture.

## Threat model & limits

### Enforced boundaries

- **Human ask-prompt on every `op` call**: the always-ask permission rule means no `op` invocation runs without explicit interactive approval. This is the primary control.
- **Armed hook deny**: when `OP_AUDIT_ENABLED=1`, the PreToolUse hook automatically denies `op run`/`op inject` wrapping a shell, network tool, or interpreter, providing a second layer before the approval prompt.
- **1Password server-side audit**: all secret accesses are recorded server-side regardless of local state. This is not controllable by this plugin and serves as the tamper-evident record.
- **Token scoping and rotation**: service account tokens should be scoped to the minimum required vaults and rotated regularly. This plugin enforces `--vault` scoping on all commands to support minimal-scope operation.

### Advisory hygiene (not enforced mechanically)

- **Never-print discipline**: the skill instructs avoidance of `op read`/`op item get --reveal`, but cannot prevent a user from running those commands outside Claude Code.
- **Log integrity**: `op_audit.py verify` checks schema conformance, not cryptographic authenticity. A user with filesystem access can alter the local log; rely on 1Password's server-side audit for authoritative records.
- **Wrapped-child discipline**: `op run` passes resolved secrets to the child process. The hook catches known-risky leaders but cannot enumerate every possible exfiltration path.
- **Compromised agent**: this plugin does not defend against a compromised or injected agent that already holds a valid `OP_SERVICE_ACCOUNT_TOKEN`. Token scoping and rotation are the mitigations for that threat.

## Old patterns

- **WSL2 with 1Password desktop integration**: the desktop app's CLI integration may fail in a new WSL2 shell that was opened before the app was running. Start a fresh login shell or export `OP_SERVICE_ACCOUNT_TOKEN` explicitly.
- **`source <(op ...)`**: avoid sourcing op output into the current shell; it evaluates arbitrary content and the resolved values land in the shell environment and command history.
- **`export VARNAME=$(op read ...)`**: never use command substitution to assign secrets to environment variables. The command appears in shell history, and the value is visible to any process that reads `/proc/<pid>/environ`. Use `op run --env-file` instead.
