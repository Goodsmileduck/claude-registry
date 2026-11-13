---
name: claude-md-optimizer
description: "Analyzes and optimizes CLAUDE.md files following Anthropic's official best practices. Use when reviewing existing CLAUDE.md for improvements, or when user mentions CLAUDE.md is too long or ineffective."
---

# Optimize CLAUDE.md

Analyze, audit, and optimize CLAUDE.md files based on Anthropic's official best practices for effective Claude Code configuration.

## Degrees of Freedom

This is a **medium-freedom** task. Follow the structured workflow but adapt recommendations based on the specific project's needs, tech stack, and team conventions.

## Workflow

Copy this checklist and track your progress:

```
Optimization Progress:
- [ ] Step 1: Analyze current CLAUDE.md state
- [ ] Step 2: Audit against best practices checklist
- [ ] Step 3: Identify and prioritize issues
- [ ] Step 4: Generate recommendations
- [ ] Step 5: Apply or present changes
```

### 1. Analyze Current State

Read the existing CLAUDE.md (if present) and assess:
- Current structure and organization
- Content categories present
- Length and verbosity
- Presence of anti-patterns

### 2. Audit Against Best Practices

Compare against the checklist in [references/best-practices.md](references/best-practices.md).

Score each category:
- **Present & Good** - Follows best practices
- **Present but Needs Work** - Exists but could be improved
- **Missing** - Should be added
- **Anti-pattern** - Should be removed/fixed

### 3. Generate Recommendations

Prioritize changes by impact:

**High Priority:**
- Remove sensitive information (API keys, credentials)
- Fix anti-patterns (generic advice, excessive length)
- Add missing critical sections (project structure, commands)

**Medium Priority:**
- Improve organization and scannability
- Add missing helpful sections
- Condense verbose explanations

**Low Priority:**
- Formatting improvements
- Minor restructuring
- Optional enhancements

### 4. Apply Changes

Either:
- **Suggest mode**: Present recommendations for user approval
- **Apply mode**: Directly edit CLAUDE.md with improvements

## Optimization Principles

### Keep It Concise

CLAUDE.md and its @imports load into every session, so their size is a cost on every turn.

```markdown
# BAD: Verbose explanation
## Testing Guidelines
When running tests in this project, you should use the npm test command.
This will execute all test files and provide output. Make sure tests pass.

# GOOD: Concise and actionable
## Testing
npm test                    # Run all tests
npm run test:coverage       # With coverage report
```

### Be Specific, Not Generic

Include YOUR project's actual patterns, not theoretical best practices.

```markdown
# BAD: Generic advice
Follow clean code principles and write maintainable code.

# GOOD: Project-specific guidance
Use `logger` from @/utils, never console.log (tree-shaken in production).
Query keys must use queryKeys factory from @/lib/queryKeys.
```

### Use Progressive Disclosure

For large documentation, split into separate files and reference them:

```markdown
# In CLAUDE.md
See @.claude/rules/testing.md for testing standards.
See @docs/ARCHITECTURE.md for system design.

# NOT: 500 lines of testing docs inline
```

### Include Actual Commands

Show the commands your team uses:

```markdown
## Development
npm run dev     # Start dev server (localhost:3000)
npm test        # Run tests in watch mode
npm run build   # Production build
```

### State Must-Follow Rules Plainly, With the Reason

Current models follow CLAUDE.md closely; capitalised IMPORTANT / YOU MUST / ALWAYS markers make them over-apply a rule.
Write the rule and its reason:

```markdown
Use the logger utility, not console.log: console output is stripped in production.
```

Reserve emphasis for one rule you have actually seen ignored. Flag emphasis clusters without a reason as an issue.

## Output Format

When optimizing, provide:

1. **Summary** - Brief assessment (1-2 sentences)
2. **Critical Issues** - Must-fix problems
3. **Recommendations** - Prioritized improvements
4. **Optimized CLAUDE.md** - The improved file (if applying changes)

## Resources

### references/
- [best-practices.md](references/best-practices.md) - Complete checklist for CLAUDE.md optimization
