# Historical pattern analysis

> Parent: [`../SKILL.md`](../SKILL.md). Use this reference to surface the constraints that aren't in the code — git history usually holds the "why".

Before changing something non-trivial, ask the repo what it remembers about it. The git log is the cheapest second opinion you have.

## When this is worth doing

- About to touch a module that has changed often (high churn → high coupling risk).
- A recurring issue: drift that keeps coming back, plan diffs that flap, repeated incidents.
- Onboarding into an unfamiliar area where current code looks arbitrary.
- A change that resembles one rolled back before — find that rollback before repeating it.

For trivial changes (tag updates, single-resource adds in a new file), skip this. The cost of analysis exceeds the value.

## Mining the log

### Touch history for the target paths

```bash
git log --oneline -20 -- "modules/<name>/*.tf"
git log --oneline -20 -- "environments/prod/"

# Time-bounded
git log --oneline --since="2024-01-01" --until="2024-06-01" -- "*.tf"

# Pattern in message
git log --oneline -20 --grep="aws_security_group"
git log --oneline -20 --grep="revert\|rollback\|hotfix" -iE
```

### Files most often changed (churn)

```bash
git log --pretty=format: --name-only -- "*.tf" \
  | sort | uniq -c | sort -rn | head -20
```

High churn isn't bad by itself — a module that everyone touches is the *most* likely place for a coordination bug to hide.

### Reverts and post-incident fixes

```bash
git log --oneline --grep="revert" -i
git log --oneline --grep="urgent\|emergency\|incident" -iE
git log --oneline --grep="fix" -i
```

A revert near the target paths is a strong signal: someone already tried what you're about to try.

### Authors and ownership

```bash
git shortlog -sn -- "environments/prod/"
```

Useful to identify who to ask, not to assign blame. If the change is in code only one person has ever touched, expect undocumented context.

## Coupling — what tends to change together

When the target file changes, what else changes in the same commit?

```bash
git log --pretty=format:"%H" -- "modules/vpc/main.tf" \
  | xargs -I {} git show --name-only --pretty=format: {} \
  | sort | uniq -c | sort -rn | head -20
```

Coupling above ~60% across many commits means the files are practically one unit; surface that to the user so the current change isn't reviewed in isolation.

## Translating findings into useful output

The point isn't a history report; it's an input to `plan-review.md`. The output should look like a few bullets, not a dashboard.

```markdown
### History signal for this change

- Affected paths: <list>
- Churn (90d): <high|medium|low>
- Coupled files (changed together >60%): <list>
- Past incidents found: <count or "none">
  - <YYYY-MM-DD> <one-line incident summary> — trigger / lesson / relevance
- Past reverts in this area: <count or "none">
  - <commit short-sha> <one-line summary>
- Risk increment from history: <none | one tier up | block-and-investigate>
```

## User-maintained lesson docs

Distilled per-session incident lessons (if any) often hold the *real* context that's no longer obvious from the code. Project memory may point to a path; check that before going deep into raw git mining.

## Feeds into

- **`plan-review.md`** — historical risk is one of the three parallel analyses there.
- **`drift-detection.md`** — if drift on this address has happened before, the cause hypothesis is often in the log already.
- **`provider-upgrades.md`** — past upgrade attempts (especially reverts) inform the breaking-change scan.
