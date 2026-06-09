---
name: digitalocean-registry-cleanup
description: Analyze and clean DigitalOcean Container Registry images. Lists repos with tag counts, deletes old tags (keep last N), finds stale repos, triggers garbage collection. Supports dry-run mode. Use when user says "clean registry", "delete old images", "DO registry", "registry cleanup", "docker images cleanup", "container registry", or "clean up old tags".
---

# DigitalOcean Registry Cleanup

Prerequisite: `doctl` CLI must be installed and authenticated (`doctl auth init`).

## Script

Run `scripts/registry_cleanup.py` via Bash tool:

```bash
SCRIPT="<skill-path>/scripts/registry_cleanup.py"

# Analyze: show all repos with tag counts and dates
python3 $SCRIPT analyze

# Clean: delete old tags from a repo, keep newest N
python3 $SCRIPT clean <repo> --keep 5 --dry-run   # preview first
python3 $SCRIPT clean <repo> --keep 5              # execute

# Stale: find repos not updated in N months
python3 $SCRIPT stale --months 6                   # list only
python3 $SCRIPT stale --months 6 --delete          # delete all tags

# GC: trigger garbage collection to reclaim storage
python3 $SCRIPT gc
```

## Workflow

1. Run `analyze` to show registry overview
2. Present the table to the user, ask which repos to clean and how many tags to keep
3. Run `clean <repo> --keep N --dry-run` to preview
4. On user confirmation, run `clean <repo> --keep N` to delete
5. Run `stale` if user wants to find abandoned repos
6. Run `gc` after all deletions to reclaim storage
7. Run `analyze` again to show the result

Always use `--dry-run` first before destructive operations.
