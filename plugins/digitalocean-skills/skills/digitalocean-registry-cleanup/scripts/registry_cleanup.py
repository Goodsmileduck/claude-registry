#!/usr/bin/env python3
"""DigitalOcean Container Registry cleanup tool.

Analyzes and cleans old/unused Docker images from a DO registry.
Requires: doctl CLI authenticated (doctl auth init).

Usage:
    python3 registry_cleanup.py analyze                    # Show all repos with tag counts and storage
    python3 registry_cleanup.py clean <repo> --keep 5      # Delete old tags, keep newest N
    python3 registry_cleanup.py clean <repo> --keep 5 --dry-run  # Preview what would be deleted
    python3 registry_cleanup.py stale --months 6           # Find repos not updated in N months
    python3 registry_cleanup.py stale --months 6 --delete  # Delete stale repos
    python3 registry_cleanup.py gc                         # Trigger garbage collection
"""

import subprocess
import sys
import json
from datetime import datetime, timezone


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error running {' '.join(cmd)}: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return r.stdout.strip()


def list_repos() -> list[dict]:
    out = run(["doctl", "registry", "repository", "list", "--output", "json"])
    return json.loads(out) if out else []


def list_tags(repo: str) -> list[dict]:
    out = run(["doctl", "registry", "repository", "list-tags", repo, "--output", "json"])
    return json.loads(out) if out else []


def delete_tag(repo: str, tag: str) -> bool:
    r = subprocess.run(
        ["doctl", "registry", "repository", "delete-tag", repo, tag, "--force"],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def delete_repo(repo: str) -> bool:
    r = subprocess.run(
        ["doctl", "registry", "repository", "delete-manifest", repo, "--force"],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def parse_date(s: str) -> datetime:
    """Parse DO API date string."""
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S %z %Z"]:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    # Fallback: extract date portion
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def fmt_size(b: int) -> str:
    if b == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def cmd_analyze():
    repos = list_repos()
    if not repos:
        print("No repositories found.")
        return

    print(f"{'Repository':<30} {'Tags':>6} {'Last Updated':<22} {'Latest Tag':<15}")
    print("-" * 80)
    for r in repos:
        name = r.get("repository", r.get("name", "?"))
        tag_count = r.get("tag_count", 0)
        latest = r.get("latest_tag", {})
        latest_tag = latest.get("tag", "?") if isinstance(latest, dict) else str(latest)
        # updated_at is inside latest_tag, not top-level
        if isinstance(latest, dict):
            updated = latest.get("updated_at", "?")[:19].replace("T", " ")
        else:
            updated = "?"
        print(f"{name:<30} {tag_count:>6} {updated:<22} {latest_tag:<15}")

    print(f"\nTotal: {len(repos)} repositories, {sum(r.get('tag_count', 0) for r in repos)} tags")


def cmd_clean(repo: str, keep: int, dry_run: bool):
    tags_data = list_tags(repo)
    if not tags_data:
        print(f"No tags found in {repo}.")
        return

    # Sort by updated_at descending
    tags_data.sort(key=lambda t: parse_date(t.get("updated_at", "")), reverse=True)

    keep_tags = tags_data[:keep]
    delete_tags = tags_data[keep:]

    print(f"\n{repo}: {len(tags_data)} total tags")
    print(f"\nKEEPING ({len(keep_tags)}):")
    for t in keep_tags:
        tag = t.get("tag", "?")
        date = t.get("updated_at", "?")[:19].replace("T", " ")
        size = fmt_size(t.get("size_bytes", t.get("compressed_size_bytes", 0)))
        print(f"  {tag:<20} {date:<22} {size}")

    if not delete_tags:
        print(f"\nNothing to delete (only {len(tags_data)} tags, keeping {keep}).")
        return

    print(f"\nDELETING ({len(delete_tags)}):")
    for t in delete_tags:
        tag = t.get("tag", "?")
        date = t.get("updated_at", "?")[:19].replace("T", " ")
        size = fmt_size(t.get("size_bytes", t.get("compressed_size_bytes", 0)))
        print(f"  {tag:<20} {date:<22} {size}")

    if dry_run:
        print(f"\n[DRY RUN] Would delete {len(delete_tags)} tags from {repo}.")
        return

    print(f"\nDeleting {len(delete_tags)} tags...")
    ok, fail = 0, 0
    for t in delete_tags:
        tag = t.get("tag", "?")
        if delete_tag(repo, tag):
            ok += 1
        else:
            fail += 1
            print(f"  Failed to delete: {tag}")

    print(f"Done: {ok} deleted, {fail} failed.")


def cmd_stale(months: int, do_delete: bool):
    repos = list_repos()
    if not repos:
        print("No repositories found.")
        return

    now = datetime.now(timezone.utc)
    stale = []
    for r in repos:
        latest = r.get("latest_tag", {})
        updated_str = latest.get("updated_at", "") if isinstance(latest, dict) else ""
        updated = parse_date(updated_str)
        age_days = (now - updated).days
        if age_days > months * 30:
            stale.append((r, age_days, updated_str))

    if not stale:
        print(f"No repositories older than {months} months.")
        return

    print(f"Stale repositories (not updated in {months}+ months):\n")
    print(f"{'Repository':<30} {'Tags':>6} {'Last Updated':<22} {'Age':>10}")
    print("-" * 75)
    for r, age, updated_str in stale:
        name = r.get("repository", r.get("name", "?"))
        tag_count = r.get("tag_count", 0)
        updated = updated_str[:19].replace("T", " ") if updated_str else "?"
        print(f"{name:<30} {tag_count:>6} {updated:<22} {age:>7}d")

    if not do_delete:
        print(f"\n{len(stale)} stale repos found. Use --delete to remove them.")
        return

    for r, _, _ in stale:
        name = r.get("repository", r.get("name", "?"))
        tags = list_tags(name)
        print(f"\nDeleting all {len(tags)} tags from {name}...")
        for t in tags:
            tag = t.get("tag", "?")
            delete_tag(name, tag)
        print(f"  Done.")

    print(f"\nDeleted tags from {len(stale)} stale repos. Run 'gc' to reclaim storage.")


def cmd_gc():
    print("Starting garbage collection...")
    out = run([
        "doctl", "registry", "garbage-collection", "start",
        "--include-untagged-manifests", "--force",
    ])
    print(out)
    print("\nGarbage collection started. DO will reclaim storage in the background.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "analyze":
        cmd_analyze()

    elif cmd == "clean":
        if len(sys.argv) < 3:
            print("Usage: registry_cleanup.py clean <repo> [--keep N] [--dry-run]")
            sys.exit(1)
        repo = sys.argv[2]
        keep = 5
        dry_run = "--dry-run" in sys.argv
        for i, arg in enumerate(sys.argv):
            if arg == "--keep" and i + 1 < len(sys.argv):
                keep = int(sys.argv[i + 1])
        cmd_clean(repo, keep, dry_run)

    elif cmd == "stale":
        months = 6
        do_delete = "--delete" in sys.argv
        for i, arg in enumerate(sys.argv):
            if arg == "--months" and i + 1 < len(sys.argv):
                months = int(sys.argv[i + 1])
        cmd_stale(months, do_delete)

    elif cmd == "gc":
        cmd_gc()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
