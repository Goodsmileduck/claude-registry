# Claude Registry - Community Plugin Marketplace

[![GitHub stars](https://img.shields.io/github/stars/Goodsmileduck/claude-registry?style=social)](https://github.com/Goodsmileduck/claude-registry)
[![GitHub forks](https://img.shields.io/github/forks/Goodsmileduck/claude-registry?style=social)](https://github.com/Goodsmileduck/claude-registry/fork)
[![GitHub issues](https://img.shields.io/github/issues/Goodsmileduck/claude-registry)](https://github.com/Goodsmileduck/claude-registry/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/Goodsmileduck/claude-registry)](https://github.com/Goodsmileduck/claude-registry/commits/main)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Compatible-blueviolet)](https://claude.ai/code)

> **A community marketplace of Claude Code plugins** for productivity, code quality, and developer workflows.

Discover, install, and share plugins that enhance your Claude Code experience. From CLAUDE.md optimization to custom workflows - find the tools you need in one place.

---

## Table of Contents

- [Why Use Claude Registry?](#why-use-claude-registry)
- [Quick Start](#quick-start)
- [Available Plugins](#available-plugins)
  - [claude-md-optimizer](#claude-md-optimizer)
  - [DevOps skill plugins](#devops-skill-plugins) (iac, kubernetes, cicd, cloud-platform, diagramming)
  - [digitalocean-skills](#digitalocean-skills)
  - [secrets-skills](#secrets-skills)
  - [setup-project-skills](#setup-project-skills)
  - [mindfulness-mentor](#mindfulness-mentor)
  - [cloudflare](#cloudflare)
  - [vercel-react-skills](#vercel-react-skills)
- [Installation](#installation)
- [Repository Structure](#repository-structure)
- [Contributing](#contributing)
  - [Plugin Requirements](#plugin-requirements)
- [License](#license)

---

## Why Use Claude Registry?

- **Curated Collection**: Quality plugins reviewed and organized for easy discovery
- **One-Command Install**: Add the marketplace once, install any plugin instantly
- **Community Driven**: Open-source plugins from the Claude Code community
- **Best Practices**: All plugins follow Claude Code plugin standards

## Quick Start

Add this marketplace to Claude Code and start installing plugins:

```bash
# Add the marketplace
/plugin marketplace add Goodsmileduck/claude-registry

# Install any plugin
/plugin install claude-md-optimizer@claude-registry
```

---

## Available Plugins

### claude-md-optimizer

Analyzes and optimizes CLAUDE.md files following Anthropic's official best practices.

| Feature | Description |
|---------|-------------|
| **Audit** | Check existing CLAUDE.md against best practices checklist |
| **Identify** | Find anti-patterns and security issues |
| **Recommend** | Generate optimization recommendations |
| **Score** | Rate your configuration (1-10 scale) |
| **Apply** | Automatically apply improvements or suggest changes |

**Install:**
```bash
/plugin install claude-md-optimizer@claude-registry
```

**Usage:**
```bash
/claude-md-optimizer
```

---

### DevOps skill plugins

The DevOps skills are split into five focused plugins. Many ship stdlib Python validators (no dependencies). Install only what a project needs.

**`iac-skills`** — Infrastructure-as-code.

| Skill | Covers |
|-------|--------|
| `terraform-workflows` | Plan review, drift, state surgery (mv/rm/import), provider upgrades |
| `terragrunt-workflows` | CLI-redesign migration, config composition, `run --all`, stacks |
| `cloud-storage-identification` | Identify S3-compatible object-storage providers |

**`kubernetes-skills`** — Kubernetes operations & GitOps.

| Skill | Covers |
|-------|--------|
| `kubernetes-operations` | Pod/workload debugging |
| `kubernetes-operators` | CRD design, reconcile-loop linting, OperatorHub audit |
| `argocd-operations` | ApplicationSets, GitOps posture |
| `docker-workflows` | Dockerfile static analysis, Compose validation |

**`cicd-skills`** — CI/CD pipelines.

| Skill | Covers |
|-------|--------|
| `github-actions-pipelines` | OIDC, permissions hardening, reusable workflows, `pull_request_target` security |
| `aws-codepipeline-codebuild` | Pipeline v1/v2, CodeStar Connections, buildspec, IAM roles, ECR/VPC builds |

**`cloud-platform-skills`** — Cloud provider operations.

| Skill | Covers |
|-------|--------|
| `gcp-iam` | Inheritance, impersonation |
| `aws-cost-investigation` | Cost Explorer + CUR, anomaly detection, Savings Plans vs RIs |
| `cloudflare-dns-zones` | DNS records via REST |
| `cloudflare-workers` | Workers/wrangler configuration, bindings |
| `cloudflare-cf-cli` | The `cf` unified CLI (preview) |
| `cloudflare-access-mcp` | Zero Trust Access for remote MCP servers |

**`diagramming-skills`** — `drawio-diagramming`: native `.drawio` generation, draw.io MCP servers, and an architecture/HLA method for infra & Kubernetes topology.

**Install (example):**
```bash
/plugin install kubernetes-skills@claude-registry
```

**Triggers:** "terraform plan", "terragrunt", "kubectl", "CRD / operator", "Dockerfile", "ArgoCD", "GCP IAM", "GitHub Actions OIDC", "CodePipeline", "AWS cost", "Cloudflare DNS / Workers", "draw.io diagram"

---

### digitalocean-skills

DigitalOcean operations skills. Three skills: `digitalocean-dns-zones` (DNS management), `digitalocean-app-platform` (App Platform spec linter), and `digitalocean-registry-cleanup` (Container Registry hygiene).

**`digitalocean-dns-zones`** — managing DNS via `doctl`, the DigitalOcean API v2, and the `digitalocean` Terraform provider.

| Feature | Description |
|---------|-------------|
| **Record CRUD** | doctl + Terraform record management; `@` for apex, list-then-act idempotency |
| **Apex CNAME trap** | Warns DO has no apex CNAME or flattening — the #1 Cloudflare-migration mistake |
| **TF linter** | Stdlib `do_dns_tf_lint.py` flags apex CNAMEs and dotless CNAME/MX values |
| **Wildcard certs** | DNS-01 ACME via cert-manager's DO solver or lego/acme.sh |
| **Delegation** | Nameserver setup and record-by-record zone migration |

**`digitalocean-app-platform`** — lints App Platform app specs (`app.yaml`, `doctl apps spec` JSON, `digitalocean_app` Terraform).

| Feature | Description |
|---------|-------------|
| **Secret checks** | Flags plaintext secrets in env values; warns on build-scoped secrets |
| **Reliability checks** | Detects single-instance services, missing health checks, dev databases in production |
| **Correctness checks** | Port mismatches, overlapping ingress routes, conflicting git/image sources, deprecated routes |
| **Sizing checks** | Unknown instance size slugs, app/database region mismatch |

**`digitalocean-registry-cleanup`** — analyze and clean DigitalOcean Container Registry images (requires `doctl`).

| Feature | Description |
|---------|-------------|
| **Analyze** | List all repos with tag counts, dates, and latest tags |
| **Clean** | Delete old tags, keep newest N per repo |
| **Dry Run** | Preview what would be deleted before executing |
| **Stale** | Find repos not updated in N months |
| **GC** | Trigger garbage collection to reclaim storage |

**Install:**
```bash
/plugin install digitalocean-skills@claude-registry
```

**Triggers:** "DigitalOcean DNS", "doctl domain records", "digitalocean_record", "apex CNAME on DO", "wildcard cert DigitalOcean", "DigitalOcean App Platform", "app.yaml", "doctl apps", "digitalocean_app", "app spec lint", "clean registry", "delete old images", "registry cleanup"

---

### secrets-skills

Injects and audits 1Password secrets via the `op` CLI. One skill: `onepassword-secrets`.

| Feature | Description |
|---------|-------------|
| **Injection** | `op run` / `op inject` for `op://` references in env vars and config files |
| **Permission gate** | Always-ask gate before any secret-touching command |
| **Audit hook** | Opt-in `PreToolUse` hook (`hooks/hooks.json`) that can deny commands touching secrets |
| **Service account** | `OP_SERVICE_ACCOUNT_TOKEN` usage for CI/non-interactive flows |

**Install:**
```bash
/plugin install secrets-skills@claude-registry
```

**Triggers:** "1Password", "op://", "op run", "op inject", "OP_SERVICE_ACCOUNT_TOKEN", "API keys", "credentials", ".env secrets"

---

### setup-project-skills

Installs skills from a user-curated manifest (`~/.claude/skill-manifest.json`) into a project's `.claude/skills/` — symlinks local skills, runs `npx skills add` for third-party ones, and advises `/plugin install` for native Claude plugins. Can scan the project for trigger files (Dockerfile, `wrangler.jsonc`, `*.tf`, …) and pre-select recommended matches. Ships a starter `skill-manifest.example.json`.

**Install:**
```bash
/plugin install setup-project-skills@claude-registry
```

**Triggers:** "set up skills in this project", "add a skill I curated", "bootstrap a freshly cloned repo with my toolbox"

---

### mindfulness-mentor

Guides developers through mindfulness exercises and stress-reduction techniques.

**Install:**
```bash
/plugin install mindfulness-mentor@claude-registry
```

**Triggers:** "taking a break", "feeling overwhelmed", "debugging frustration", "need to refocus"

---

### cloudflare

Curated upstream — Cloudflare's official platform skills: Workers, Pages, KV/D1/R2, Workers AI, Vectorize, Agents SDK, Tunnel, Spectrum, WAF, DDoS, Terraform, and Pulumi. Includes the `wrangler`, `agents-sdk`, `durable-objects`, `sandbox-sdk`, `web-perf`, `building-mcp-server-on-cloudflare`, `building-ai-agent-on-cloudflare`, and `cloudflare-email-service` skills.

**Install:**
```bash
/plugin install cloudflare@claude-registry
```

**Triggers:** "Cloudflare Workers", "wrangler", "Durable Objects", "KV / D1 / R2", "Workers AI", "Cloudflare email"

---

### vercel-react-skills

Curated upstream — Vercel-Labs React/Next.js skills: performance best practices, composition patterns (compound components, render props), and React Native / Expo conventions for mobile.

**Install:**
```bash
/plugin install vercel-react-skills@claude-registry
```

**Triggers:** "React performance", "Next.js best practices", "compound components", "React Native / Expo"

---

## Installation

### Option 1: Marketplace Install (Recommended)

Add the marketplace to access all plugins:

```bash
/plugin marketplace add Goodsmileduck/claude-registry
```

Then install any plugin from the registry:

```bash
/plugin install <plugin-name>@claude-registry
```

### Option 2: Local Path

For development or offline use:

```bash
# Clone the repository
git clone https://github.com/Goodsmileduck/claude-registry.git

# Add marketplace from local path
/plugin marketplace add /path/to/claude-registry
```

---

## Repository Structure

```
claude-registry/
├── .claude-plugin/
│   ├── plugin.json            # Root plugin manifest
│   └── marketplace.json       # Marketplace definition (authoritative plugin list)
├── .github/workflows/
│   └── skills-ci.yml          # CI: best-practices lint + script security audit on every PR
├── scripts/
│   ├── lint_skills.py         # Stdlib best-practices linter (frontmatter, naming, manifests)
│   └── audit_skill_scripts.py # Stdlib AST audit for code-execution risks in skill scripts
└── plugins/
    ├── claude-md-optimizer/     # CLAUDE.md auditing & optimization
    ├── iac-skills/              # Terraform, Terragrunt, storage identification
    ├── kubernetes-skills/       # k8s ops, operators, ArgoCD, Docker
    ├── cicd-skills/             # GitHub Actions, AWS CodePipeline/CodeBuild
    ├── cloud-platform-skills/   # GCP IAM, AWS cost, Cloudflare platform
    ├── diagramming-skills/      # draw.io architecture diagrams
    ├── digitalocean-skills/     # DigitalOcean DNS, App Platform linter, registry cleanup
    ├── secrets-skills/          # 1Password secret injection and audit hook
    ├── setup-project-skills/    # Install curated skills into a project
    ├── mindfulness-mentor/      # Developer mindfulness exercises
    ├── cloudflare/              # Curated upstream: Cloudflare platform skills
    └── vercel-react-skills/     # Curated upstream: Vercel React/Next.js skills

# Each plugins/<name>/ contains:
#   .claude-plugin/plugin.json   — plugin manifest
#   skills/<skill>/SKILL.md      — one or more skills (+ optional references/, scripts/, evals/)
```

---

## Contributing

Contributions are welcome! If you have a plugin idea or want to share your workflows:

1. Fork the repository
2. Create your plugin in `plugins/your-plugin-name/`
3. Add proper `.claude-plugin/plugin.json` manifest
4. Add entry to `.claude-plugin/marketplace.json`
5. Submit a Pull Request

Every PR runs `skills-ci`, which lints skills against the [authoring rules](CLAUDE.md) (frontmatter, naming, manifest consistency) and audits skill scripts for code-execution risks. Run the checks locally first:

```bash
python3 scripts/lint_skills.py          # best-practices lint
python3 scripts/audit_skill_scripts.py  # script security audit
```

### Plugin Requirements

| Requirement | Description |
|-------------|-------------|
| **Manifest** | Must have `.claude-plugin/plugin.json` with name, description, and version |
| **Naming** | Follow kebab-case naming convention |
| **Description** | Include useful description and keywords |
| **Testing** | Test your plugin locally before submitting |

---

## License

MIT License

---

**Found this useful?** Star the repo and help others discover Claude Code plugins!

[![Star this repo](https://img.shields.io/github/stars/Goodsmileduck/claude-registry?style=social)](https://github.com/Goodsmileduck/claude-registry)
