---
name: setup-project-skills
description: Installs skills from a user-curated manifest (`~/.claude/skill-manifest.json`) into the current project's `.claude/skills/` — symlinks local skills, runs `npx skills add` for third-party ones, and advises `/plugin install` for native Claude plugins. Optionally scans the project for trigger files (Dockerfile, wrangler.jsonc, *.tf, etc.) and pre-selects recommended matches. Use when the user wants to set up skills in a new project, add a skill they curated, see what skills fit the current project, or bootstrap a freshly cloned repo with their toolbox.
---

# setup-project-skills

Installs skills into the current project from a manifest the user maintains. Distinct from `claude-automation-recommender` (which advises across hooks/MCP/subagents/skills/plugins and does not install) and from `find-skills` (which discovers public skills). This skill is opinionated: it installs only what's on the user's whitelist.

## When to invoke

- "Set up skills for this project."
- "Bootstrap this repo with my skills."
- "What skills should I install here?" — the project-detection pass answers that from the manifest.
- "Link/install/add `<name>` here."
- Fresh `git clone` followed by a session in the new directory.

## When NOT to invoke

- User is actually doing terraform/k8s/docker/etc. work — the underlying skills handle that.
- User wants a recommendation across the full Claude Code surface (hooks, subagents, MCP, plugins) — that's `claude-automation-recommender`.
- User wants to discover what skills exist on the public registry — that's `find-skills`.
- User wants to author a new skill — that's `skill-creator`.

## Cross-cutting rules

1. **The manifest is the source of truth.** Always read `~/.claude/skill-manifest.json` (or `$SKILL_MANIFEST` if set). Never invent skills not listed there. If a skill the user names isn't in the manifest, offer to add it rather than installing ad-hoc.
2. **Project-scoped install only.** Always link or install into `./.claude/skills/<name>` of the current working directory. Never into `~/.claude/skills/` from this skill — that's the user's global decision.
3. **Idempotent.** If `./.claude/skills/<name>` already exists, check whether it matches the desired target. Skip if identical; refuse and surface the conflict if not.
4. **Restart required.** Skills installed mid-session don't activate until the next Claude Code session starts in this project. Always say so after a successful install.
5. **No slash commands.** Claude can't invoke `/plugin install`. For `claude-plugin` sources, print the exact command for the user to run themselves.

## Manifest schema

```json
{
  "version": 1,
  "skills": [
    {
      "name": "<short-id>",
      "source": "local-symlink | npx-skills | claude-plugin",
      "path": "<absolute or ~ path>",         // for local-symlink
      "repo": "<owner/repo[/subpath]>",       // for npx-skills
      "plugin": "<plugin@marketplace>",       // for claude-plugin
      "tags": ["..."],
      "detect": ["glob", "glob:substring", "..."],
      "note": "<optional user-facing hint>"
    }
  ]
}
```

`detect` entries are glob patterns. A trailing `:<substring>` requires that substring to appear inside any matching file (cheap grep). Examples:
- `**/*.tf` — any Terraform file
- `**/*.tf:google_` — Terraform file containing the substring `google_`
- `package.json:"react"` — package.json that mentions `"react"`

## Procedure

### Step 1 — Locate and validate the manifest

```bash
MANIFEST="${SKILL_MANIFEST:-$HOME/.claude/skill-manifest.json}"
test -f "$MANIFEST" || { echo "Manifest not found at $MANIFEST"; exit 1; }
python3 -c "import json,sys; json.load(open('$MANIFEST'))" || { echo "Manifest is not valid JSON"; exit 1; }
```

If missing or invalid, offer to create a minimal one and stop.

### Step 2 — Detect matches against the current project

For each skill, evaluate its `detect` patterns against the current working directory. Use `find` for plain globs and `grep -l` for `glob:substring` form. Mark every skill that matches at least one pattern as **recommended**.

If the project is empty (no entries in `ls -A`), skip detection — present the full catalog and ask which to install.

### Step 3 — Present and confirm

Show the user a table grouped by status:

```
RECOMMENDED (detected in this project)
  [x] cloudflare-cf-cli       — wrangler.jsonc found
  [x] docker-workflows        — Dockerfile found
  [x] github-actions-pipelines — .github/workflows/deploy.yml found

OPTIONAL (in your manifest, not detected)
  [ ] terraform-workflows
  [ ] kubernetes-operations
  ...
```

Pre-check the recommended ones. Ask the user to confirm or amend the selection before installing anything. Honor an explicit user request that contradicts detection (e.g. "actually also install terraform-workflows").

### Step 4 — Install each selected skill by source

Always create the target directory first: `mkdir -p .claude/skills`.

**`local-symlink`** — symlink the path into `.claude/skills/<name>`:

```bash
TARGET="$(python3 -c 'import os,sys; print(os.path.expanduser(sys.argv[1]))' "<path-from-manifest>")"
LINK=".claude/skills/<name>"
test -d "$TARGET" || { echo "Source missing: $TARGET"; exit 1; }
if [ -L "$LINK" ]; then
  current=$(readlink "$LINK")
  [ "$current" = "$TARGET" ] && { echo "$<name> already linked — skip"; }
  [ "$current" != "$TARGET" ] && { echo "$LINK points elsewhere — resolve manually"; exit 1; }
elif [ -e "$LINK" ]; then
  echo "$LINK exists and is not a symlink — refusing"; exit 1
else
  ln -s "$TARGET" "$LINK" && echo "Linked $<name>"
fi
```

**`npx-skills`** — fetch into the project's skills directory (NOT global):

```bash
# `npx skills add` defaults to project scope (./.claude/skills/) when run without -g
npx skills add "<repo-from-manifest>"
```

`npx skills add` copies files; updates require `npx skills update` later. Mention this when installing.

**`claude-plugin`** — Claude can't trigger slash commands. Tell the user to run it themselves:

```
Run this command yourself (Claude can't invoke slash commands):
  /plugin install <plugin>
```

Include any `note` field from the manifest.

### Step 5 — Post-install

After each successful install:

- Print a one-line confirmation.
- After all installs, remind the user to restart the session.
- If a `.gitignore` exists and doesn't already cover `.claude/skills/`, ask whether to add it. Symlinks to absolute user-machine paths shouldn't be committed; copies from `npx skills add` may or may not be wanted in the repo — let the user decide.

## Adding new skills to the manifest

When the user says "I found a new skill on skills.sh, add it to my manifest" or similar:

1. Confirm `source` type (most often `npx-skills` for skills.sh entries, or `claude-plugin` for Claude Code marketplace plugins).
2. Confirm the repo/plugin identifier.
3. Ask for `tags` and `detect` patterns — the user's project-detection signals.
4. Append to `~/.claude/skill-manifest.json` (preserve formatting and trailing newline).
5. Validate JSON before writing back.

This is the only modification this skill makes outside the project directory.

## Anti-patterns

- ❌ Installing a skill not in the manifest without first offering to add it.
- ❌ Linking into `~/.claude/skills/` from this skill — global is the user's explicit choice.
- ❌ Running `npx skills add -g` here — that's global, not project-scoped.
- ❌ Attempting `/plugin install` via Bash — Claude can't invoke slash commands; print the instruction.
- ❌ Overwriting an existing symlink or directory without confirming.
- ❌ Forgetting to remind the user to restart the session.
- ❌ Recommending skills based on Claude's general knowledge instead of the manifest — that's `claude-automation-recommender`'s job.
- ❌ Copying skill content into the project instead of symlinking when source is `local-symlink` — defeats the live-edit benefit.

## Cross-skill notes

- `claude-automation-recommender` (claude-code-setup plugin): broader codebase-aware advisor across hooks, subagents, MCP, plugins, and skills. Read-only. Useful BEFORE this skill — surfaces new ideas; you then add the good ones to the manifest.
- `find-skills`: discovers skills on the public registry. Use to find candidates worth adding to the manifest.
- `skill-creator`: for authoring new skills (which then get added to the manifest as `local-symlink`).
- `devops-skill-link`: deprecated — this skill subsumes its job. Safe to delete.
