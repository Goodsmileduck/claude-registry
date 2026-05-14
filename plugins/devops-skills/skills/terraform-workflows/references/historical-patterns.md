# Historical Pattern Analysis

> Parent: [`../SKILL.md`](../SKILL.md). Use this reference to understand *why* a module is configured the way it is, before changing it — git history often holds the constraint that's not in the code.

Analyze git history (and project memory, where present) to learn from past infrastructure changes. Identify patterns, recurring issues, and apply lessons.

## Contents

- When to use
- Process — search scope, git archaeology, pattern extraction (steps 1–3)
- Step 4 — Identify lessons from past incidents
- Step 5 — Report template
- Integration with sibling references (plan-review, drift-detection, provider-upgrades)

## When to use

- Before making changes similar to past changes
- When investigating recurring issues (drift, plan flapping, repeated incidents)
- To understand why infrastructure is configured a certain way
- To identify change patterns and team practices

## Process

### Step 1: Define Search Scope

Determine what history to analyze:
- Specific resources being changed
- Time period (last month, quarter, year)
- Specific team members or patterns

### Step 2: Git Archaeology

#### Find Related Commits

```bash
# Commits touching specific files
git log --oneline -20 -- "path/to/module/*.tf"

# Commits mentioning resource types
git log --oneline -20 --grep="aws_security_group"

# Commits by pattern in message
git log --oneline -20 --grep="fix\|rollback\|revert"

# Commits in date range
git log --oneline --since="2024-01-01" --until="2024-06-01" -- "*.tf"
```

#### Analyze Commit Patterns

```bash
# Most frequently changed files
git log --pretty=format: --name-only -- "*.tf" | sort | uniq -c | sort -rn | head -20

# Authors and their focus areas
git shortlog -sn -- "environments/prod/"

# Change frequency by day/time
git log --format="%ad" --date=format:"%A %H:00" -- "*.tf" | sort | uniq -c
```

#### Find Reverts and Fixes

```bash
# Revert commits
git log --oneline --grep="revert\|Revert"

# Fix commits following changes
git log --oneline --grep="fix\|hotfix\|Fix"

# Commits with "URGENT" or "EMERGENCY"
git log --oneline --grep="urgent\|emergency" -i
```

### Step 3: Analyze Change Patterns

#### Coupling Analysis

Which files change together?
```bash
# For a specific file, what else changes with it?
git log --pretty=format:"%H" -- "modules/vpc/main.tf" | \
  xargs -I {} git show --name-only --pretty=format: {} | \
  sort | uniq -c | sort -rn | head -20
```

#### Change Sequences

Common sequences of changes:
1. VPC changes → followed by security group changes
2. IAM role changes → followed by policy attachments
3. RDS changes → followed by parameter group changes

#### Time Patterns

- Are prod changes clustered on certain days?
- Are there "risky" times based on past incidents?
- How long between staging and prod deployments?

### Step 4: Identify lessons from past incidents

For each past incident touching these resources, capture: trigger, how it was detected, the fix, and what could have prevented it. Check the user's distilled lessons docs at `/home/goodsmileduck/local/upwork/devops-agent-*.md` if present — those often hold per-incident context that's no longer obvious from the code.

### Step 5: Generate Report

```markdown
## Historical Pattern Analysis

### Search Scope
- Resources: [resources being analyzed]
- Time period: [date range]
- Related commits found: [count]

### Change Frequency

| Resource/File | Changes (90d) | Last Changed | Primary Authors |
|--------------|---------------|--------------|-----------------|
| modules/vpc/main.tf | 12 | 2024-01-10 | alice, bob |
| environments/prod/main.tf | 8 | 2024-01-08 | alice |

### Change Coupling

These resources typically change together:
1. `aws_security_group.web` ↔ `aws_instance.web` (85% correlation)
2. `aws_iam_role.app` ↔ `aws_iam_policy.app` (100% correlation)

### Past Incidents Related to These Resources

#### Incident: [Date] - [Title]
- **Trigger:** [What caused it]
- **Impact:** [What happened]
- **Resolution:** [How it was fixed]
- **Lesson:** [What we learned]
- **Relevance:** [How this applies to current change]

### Patterns Identified

#### Pattern: [Pattern Name]
- **Observation:** [What we see in history]
- **Frequency:** [How often]
- **Implication:** [What this means for current change]

### Risk Indicators

Based on historical data:
| Indicator | Current Change | Historical Issues |
|-----------|---------------|-------------------|
| Similar to past incident | [Yes/No] | [Details] |
| Frequently problematic resource | [Yes/No] | [Details] |
| Changed by unfamiliar author | [Yes/No] | [Details] |

### Recommendations

Based on historical patterns:
1. [Recommendation 1]
2. [Recommendation 2]

### Questions Raised

[Questions that history suggests we should answer]
```

## Integration with sibling references

This reference feeds into:
- **`plan-review.md`** — provides historical context for the risk assessment in Step 3 (parallel analysis)
- **`drift-detection.md`** — surfaces whether observed drift matches a past pattern
- **`provider-upgrades.md`** — past upgrade experiences inform the breaking-change scan
