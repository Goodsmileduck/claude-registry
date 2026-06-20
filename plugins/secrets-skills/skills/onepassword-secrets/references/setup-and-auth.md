> Parent: ../SKILL.md

# Setup and Authentication — 1Password CLI

## Contents

- [Installing the op CLI](#installing-the-op-cli)
- [Auth model 1: interactive desktop-biometric](#auth-model-1-interactive-desktop-biometric)
- [Auth model 2: headless service-account](#auth-model-2-headless-service-account)
- [Always-ask permission rule and bypass surfaces](#always-ask-permission-rule-and-bypass-surfaces)
- [Headless resolution without a human present](#headless-resolution-without-a-human-present)
- [Shell-history hygiene](#shell-history-hygiene)

---

## Installing the op CLI

### Debian and Ubuntu (apt)

The official 1Password APT repository is the recommended install path on Debian-based systems.

```bash
# 1. Add the signing key
curl -s "https://downloads.1password.com/linux/keys/1password.gpg" \
  | sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg

# 2. Add the repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] \
  https://downloads.1password.com/linux/debian/ stable main" \
  | sudo tee /etc/apt/sources.list.d/1password.list

# 3. Install
sudo apt update
sudo apt install 1password-cli
```

On pure Debian (no Ubuntu layer), the `debsig-verify` policy may also be required — consult the upstream install guide for the extra two lines.

Verify the install:

```bash
op --version
```

### macOS (Homebrew)

```bash
brew install --cask 1password-cli
```

Homebrew manages upgrades via `brew upgrade --cask 1password-cli`.

### Windows and WSL2

On native Windows, the recommended package manager path is winget:

```powershell
winget install 1Password.1PasswordCLI
```

**WSL2 caution — desktop integration is fiddly.** The 1Password desktop app's CLI integration (`op signin` using biometric unlock) communicates with the Windows host over a named pipe. That channel is only available in WSL2 shells that were launched *after* the desktop app was already running and the integration enabled under Settings > Developer. A WSL2 terminal opened before the desktop app started will time out on biometric prompts. Practical mitigations:

- Start 1Password for Windows before opening the WSL2 shell, or
- Use `OP_SERVICE_ACCOUNT_TOKEN` (see below) to avoid desktop-app dependency entirely in WSL2.

---

## Auth model 1: interactive desktop-biometric

This is the default and preferred model for interactive development sessions.

### How it works

`op` delegates authentication to the 1Password desktop application. On macOS and Windows, the desktop app handles Touch ID, Face ID, or Windows Hello. No long-lived token is stored in the shell environment; the session is time-bounded and renewed on demand through the desktop app.

### Sign-in

```bash
op signin
```

On first run, `op signin` may prompt for the account shorthand or sign-in address:

```bash
op signin --account <account-shorthand-or-email>
```

Subsequent invocations re-use the desktop session; biometric re-authentication is triggered by the app, not by the CLI.

### Why prefer this model

- No long-lived credential exists in the environment or on disk.
- Session expiry is enforced by the desktop app's lock policy.
- Biometric confirmation provides a human presence check on every unlock cycle.

### Limitation

Requires the 1Password desktop app to be running. Unusable in pure headless environments (servers, CI, containers). Use the service-account model in those contexts.

---

## Auth model 2: headless service-account

Service accounts are designed for non-interactive environments: CI pipelines, containers, servers, and automated agents.

### How it works

A service account token is provisioned in the 1Password admin console and exported as `OP_SERVICE_ACCOUNT_TOKEN`. The `op` CLI detects this variable and bypasses the interactive sign-in flow. No desktop app is required.

```bash
export OP_SERVICE_ACCOUNT_TOKEN='ops_...'
op user get --me   # confirms the service account is active
```

### Scoping (mandatory)

Every service account token must be scoped to the minimum set of vaults needed. Granting access to all vaults is equivalent to giving the token permission to read every secret in the organization.

- Create the service account in the 1Password admin console under Integrations > Service Accounts.
- Grant access only to the specific vault(s) the automated task requires.
- Use `op run --vault <Vault>` and `op inject --vault <Vault>` to enforce this scoping at call time — `../SKILL.md` requires `--vault` on every invocation.

### Storing the token securely

The token is itself a secret ("secret-zero" problem: any system that stores the token must already be trusted). Acceptable storage locations, in order of preference:

1. **CI/CD platform secret store** (GitHub Actions `secrets.*`, GitLab CI variables, Vault dynamic secret) — the platform injects the token at runtime and it is never written to disk.
2. **OS keychain** (macOS Keychain, Windows Credential Manager, Linux `secret-tool` backed by GNOME Keyring or KWallet) — the token is encrypted at rest by the OS and unlocked only for the authorized process.
3. **`op` itself** — store the service account token as a field in 1Password and inject it at startup with `op run` using an already-authenticated session. This avoids the secret-zero problem for workstations where interactive auth is available.

**Never store `OP_SERVICE_ACCOUNT_TOKEN` in:**
- `.env` files (committed or not — these land on disk unencrypted and are often accidentally committed),
- shell profile files (`.bashrc`, `.zshrc`, `.profile`) — they persist across restarts and are readable by any process running as the same user,
- CI logs or build output.

### Rotation

Rotate service account tokens regularly. The 1Password admin console revokes old tokens without affecting the vault contents. After generating a new token, update it in every secret store that holds a copy before revoking the old one.

### Keeping the token out of the agent's inherited environment

When Claude Code starts, it inherits the shell environment. If `OP_SERVICE_ACCOUNT_TOKEN` is present in the environment, every `op` call during the session is pre-authenticated without the interactive-desktop biometric gate. Where practical, export the token only in the shell that runs the specific automated task, not in the general developer shell that launches Claude Code.

One pattern: use a wrapper script that sets the token and immediately runs the scoped task, rather than exporting it into the ambient environment.

```bash
# arm.sh — scoped to one op run; token not in developer's general shell
OP_SERVICE_ACCOUNT_TOKEN="$(op read op://Ops/DeployToken/credential)" \
  op run --env-file=.env.tpl --vault Production -- ./deploy.sh
```

---

## Always-ask permission rule and bypass surfaces

`../SKILL.md` (cross-cutting rules) states: every `op` invocation requires explicit approval and users must never select "always allow" for `op`. The mechanism that enforces this in Claude Code is the `ask` permission rule:

```json
{"permissions": {"ask": ["Bash(op:*)"]}}
```

Place this in `.claude/settings.json` at the project or user level. Claude Code presents an approval prompt for every Bash command whose first token is `op`.

### Bypass surfaces to understand

The always-ask rule matches on the literal `op` token as the command leader. It does **not** cover:

- **`sh -c 'op ...'`** — the outer command is `sh`, not `op`; the pattern does not match.
- **`$OP` variable substitution** — if `OP=/usr/local/bin/op`, then `$OP read ...` bypasses the literal match.
- **Shell wrappers and aliases** — a user-defined `function run_op() { op "$@"; }` bypasses the literal match.
- **Command-rewriting hooks** — a Claude Code PreToolUse hook that rewrites a non-`op` command into an `op` invocation after the permission check has already run bypasses the rule.

Because of these surfaces, **never advise selecting "always allow" for `op`**, and ensure that any command-rewriting hook does not introduce op calls that escape the always-ask gate.

---

## Headless resolution without a human present

In a fully automated context (no human watching Claude Code), the interactive approval prompt cannot be answered. This is by design: `../SKILL.md` positions the ask-prompt as the primary control.

In headless deployments, the opt-in audit hook (see `references/audit-logging.md`) acts as the gate. With `OP_AUDIT_ENABLED=1`, the hook:

1. Logs every `op` invocation to the local JSONL audit log.
2. Automatically denies `op run` and `op inject` calls whose wrapped child is a shell, network tool, or interpreter.

The hook is not a replacement for the always-ask rule — it is a second layer. The architectural principle is:

- **Interactive sessions**: always-ask prompt is the gate; hook adds logging.
- **Headless sessions**: hook deny is the gate for high-risk patterns; the service-account token scope is the gate for everything the hook does not cover.

Service accounts should be scoped as narrowly as possible (single vault, read-only fields) so that even if an `op` call runs without human approval, the blast radius is bounded.

---

## Shell-history hygiene

Shell history is a common vector for credential leakage. Specific patterns to avoid, drawn from `../SKILL.md`'s Old Patterns section:

- `export VARNAME=$(op read ...)` — the command appears in shell history verbatim, and the resolved value is visible to any process reading `/proc/<pid>/environ`. Use `op run --env-file` instead.
- `source <(op ...)` — sources op output into the current shell; the resolved values land in the shell environment and the command appears in history.
- Passing secrets as command-line arguments to any program — arguments are visible in `ps aux` output and in `/proc/<pid>/cmdline`.

If `op read` must be used (last resort, as noted in `../SKILL.md`), prefix the call with a space in bash/zsh to suppress history recording (requires `HISTCONTROL=ignorespace`), and treat the resolved value as exposed the moment it appears in output.
