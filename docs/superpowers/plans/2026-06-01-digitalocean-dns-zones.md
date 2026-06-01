# digitalocean-dns-zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `digitalocean-skills` plugin whose first skill, `digitalocean-dns-zones`, encodes DigitalOcean-specific DNS traps (no apex CNAME/flattening, account-wide token, FQDN trailing-dot) plus a stdlib Terraform linter.

**Architecture:** A new plugin directory under `plugins/`, structured like the existing `cloudflare-dns-zones` skill for consistency. Evals are written first (CLAUDE.md mandate), then a TDD'd stdlib Python validator, then the SKILL.md body. No `references/` — single-topic skill that fits under 500 lines.

**Tech Stack:** Markdown (SKILL.md), JSON (plugin.json, evals), Python 3 stdlib (validator + its tests). Syntax verified against Context7: `/digitalocean/terraform-provider-digitalocean`, `/digitalocean/doctl`.

---

## File Structure

- `plugins/digitalocean-skills/.claude-plugin/plugin.json` — plugin metadata (DNS now, extensible).
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/SKILL.md` — the skill body.
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/do_dns_tf_lint.py` — heuristic TF linter.
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/test_do_dns_tf_lint.py` — validator tests (stdlib `unittest`).
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/apex-migration.json`
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/wildcard-cert.json`
- `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/tf-cname-no-dot.json`

Branch: `skill/digitalocean-dns-zones` (already created; the spec is committed there).

---

### Task 1: Plugin scaffold + metadata

**Files:**
- Create: `plugins/digitalocean-skills/.claude-plugin/plugin.json`

- [ ] **Step 1: Write `plugin.json`**

```json
{
  "name": "digitalocean-skills",
  "version": "0.1.0",
  "description": "DigitalOcean operations skills. Currently: DNS zones via doctl, the DigitalOcean API v2, and the digitalocean Terraform provider — domain/record CRUD, the apex CNAME / no-flattening trap when migrating from Cloudflare, account-wide token handling, FQDN trailing-dot semantics, DNS-01 ACME wildcard certs, and nameserver delegation.",
  "author": {
    "name": "Stanislav Serebrennikov",
    "url": "https://github.com/Goodsmileduck"
  },
  "homepage": "https://github.com/Goodsmileduck/claude-registry",
  "repository": "https://github.com/Goodsmileduck/claude-registry",
  "license": "MIT",
  "keywords": [
    "digitalocean",
    "doctl",
    "dns",
    "digitalocean-dns",
    "domain-records",
    "terraform",
    "digitalocean_record",
    "dns-01",
    "acme",
    "wildcard-cert",
    "nameserver",
    "cloudflare-migration"
  ]
}
```

- [ ] **Step 2: Verify it parses as JSON**

Run: `python3 -c "import json; json.load(open('plugins/digitalocean-skills/.claude-plugin/plugin.json')); print('VALID')"`
Expected: `VALID`

- [ ] **Step 3: Commit**

```bash
git add plugins/digitalocean-skills/.claude-plugin/plugin.json
git commit -m "feat(do): scaffold digitalocean-skills plugin"
```

---

### Task 2: Eval scenarios (written before the SKILL body)

**Files:**
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/apex-migration.json`
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/wildcard-cert.json`
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/tf-cname-no-dot.json`

- [ ] **Step 1: Write `apex-migration.json`**

```json
{
  "query": "I'm moving example.com from Cloudflare to DigitalOcean. On Cloudflare the apex was a CNAME to my load balancer hostname. How do I recreate that on DO?",
  "files": [],
  "expected_behavior": "Warns that DigitalOcean has no apex CNAME and no CNAME flattening (unlike Cloudflare). Recommends resolving the LB hostname to an IP and using an A/AAAA record at name \"@\", or using a DO-native resource that exposes an IP. Does NOT suggest a CNAME at the apex."
}
```

- [ ] **Step 2: Write `wildcard-cert.json`**

```json
{
  "query": "I need a wildcard TLS cert for *.example.com and the zone is hosted on DigitalOcean DNS. How do I get it issued automatically?",
  "files": [],
  "expected_behavior": "Routes to a DNS-01 ACME challenge using DigitalOcean DNS. Mentions cert-manager's DigitalOcean DNS solver (tokenSecretRef) or lego/acme.sh with a DigitalOcean API token in the environment. Notes the token is account-wide and must be stored as a secret, not inlined."
}
```

- [ ] **Step 3: Write `tf-cname-no-dot.json`**

```json
{
  "query": "Why does my Terraform CNAME on DigitalOcean resolve to api.example.com.example.com?",
  "files": ["main.tf"],
  "expected_behavior": "Identifies the missing trailing dot on the digitalocean_record CNAME value: a relative value like \"www.example.com\" gets the domain appended, producing a doubled FQDN. Fix is value = \"www.example.com.\". The do_dns_tf_lint.py validator flags this case."
}
```

- [ ] **Step 4: Verify all three parse as JSON**

Run: `for f in plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/*.json; do python3 -c "import json,sys; json.load(open(sys.argv[1])); print('OK', sys.argv[1])" "$f"; done`
Expected: three `OK ...` lines, no traceback.

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals
git commit -m "test(do): add eval scenarios for digitalocean-dns-zones"
```

---

### Task 3: Validator `do_dns_tf_lint.py` (TDD)

**Files:**
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/test_do_dns_tf_lint.py`
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/do_dns_tf_lint.py`

The validator exposes a pure function `lint_text(text) -> list[Finding]` (each `Finding` is a dict with `severity`, `rule`, `message`, `label`) plus a CLI `main(argv)`. The CLI: exit 0 = clean, 1 = findings, 2 = usage/IO error; `--format json` emits findings as a JSON array; `--help` documents usage.

- [ ] **Step 1: Write the failing test**

```python
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("do_dns_tf_lint.py")
sys.path.insert(0, str(SCRIPT.parent))
import do_dns_tf_lint as lint  # noqa: E402

APEX_CNAME = '''
resource "digitalocean_record" "apex" {
  domain = digitalocean_domain.main.id
  type   = "CNAME"
  name   = "@"
  value  = "lb.example.com."
}
'''

CNAME_NO_DOT = '''
resource "digitalocean_record" "api" {
  domain = digitalocean_domain.main.id
  type   = "CNAME"
  name   = "api"
  value  = "www.example.com"
}
'''

CLEAN = '''
resource "digitalocean_record" "www" {
  domain = digitalocean_domain.main.id
  type   = "A"
  name   = "www"
  value  = "192.168.0.11"
  ttl    = 300
}

resource "digitalocean_record" "cname_ok" {
  domain = digitalocean_domain.main.id
  type   = "CNAME"
  name   = "api"
  value  = "www.example.com."
}
'''


class TestLintText(unittest.TestCase):
    def test_apex_cname_is_error(self):
        findings = lint.lint_text(APEX_CNAME)
        rules = [(f["severity"], f["rule"]) for f in findings]
        self.assertIn(("error", "apex-cname"), rules)

    def test_cname_without_trailing_dot_is_warning(self):
        findings = lint.lint_text(CNAME_NO_DOT)
        rules = [(f["severity"], f["rule"]) for f in findings]
        self.assertIn(("warning", "cname-relative-value"), rules)

    def test_clean_config_has_no_findings(self):
        self.assertEqual(lint.lint_text(CLEAN), [])

    def test_low_ttl_is_not_flagged(self):
        # Provider docs: ttl >= 0 (default 1800). A sub-30 TTL is legal — no finding.
        text = CLEAN.replace("ttl    = 300", "ttl    = 5")
        self.assertEqual(lint.lint_text(text), [])


class TestCli(unittest.TestCase):
    def _run(self, text, *args):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".tf", delete=False) as fh:
            fh.write(text)
            path = fh.name
        return subprocess.run(
            [sys.executable, str(SCRIPT), path, *args],
            capture_output=True, text=True,
        )

    def test_exit_zero_on_clean(self):
        self.assertEqual(self._run(CLEAN).returncode, 0)

    def test_exit_one_on_findings(self):
        self.assertEqual(self._run(APEX_CNAME).returncode, 1)

    def test_json_format_is_parseable(self):
        proc = self._run(APEX_CNAME, "--format", "json")
        data = json.loads(proc.stdout)
        self.assertTrue(any(f["rule"] == "apex-cname" for f in data))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/test_do_dns_tf_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'do_dns_tf_lint'` (script not created yet).

- [ ] **Step 3: Write the validator**

```python
#!/usr/bin/env python3
"""Heuristic linter for digitalocean_record Terraform blocks.

This is NOT a full HCL parser — the Python stdlib has none. It scans .tf
text for `resource "digitalocean_record" "<label>" { ... }` blocks via
brace-depth matching and flags two DigitalOcean-specific DNS mistakes:

  - apex-cname (error):  type = "CNAME" with name = "@". DigitalOcean has no
    apex CNAME and no CNAME flattening; such a record breaks the zone.
  - cname-relative-value (warning): a CNAME/MX value without a trailing dot.
    The DigitalOcean provider treats a dotless value as relative and appends
    the domain, producing a doubled FQDN (api.example.com.example.com).

Deliberately NOT checked: a TTL below 30. The provider documents ttl >= 0
(default 1800), so a low TTL is legal and flagging it would be a false
positive. Heuristic limits: interpolated values (value = local.x) and
`dynamic` blocks are skipped, not guessed.

Exit codes: 0 = clean, 1 = findings, 2 = usage/IO error.
"""
import argparse
import json
import re
import sys

RESOURCE_RE = re.compile(
    r'resource\s+"digitalocean_record"\s+"(?P<label>[^"]+)"\s*\{'
)
# Matches `key = "value"` (quoted string args only — interpolations are skipped).
ATTR_RE = re.compile(r'(?P<key>\w+)\s*=\s*"(?P<val>[^"]*)"')


def _iter_blocks(text):
    """Yield (label, body_text) for each digitalocean_record block."""
    for m in RESOURCE_RE.finditer(text):
        start = m.end()  # position just after the opening brace
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        body = text[start:i - 1]
        yield m.group("label"), body


def _attrs(body):
    return {m.group("key"): m.group("val") for m in ATTR_RE.finditer(body)}


def lint_text(text):
    """Return a list of finding dicts for the given Terraform text."""
    findings = []
    for label, body in _iter_blocks(text):
        attrs = _attrs(body)
        rtype = attrs.get("type")
        name = attrs.get("name")
        value = attrs.get("value")

        if rtype == "CNAME" and name == "@":
            findings.append({
                "severity": "error",
                "rule": "apex-cname",
                "label": label,
                "message": (
                    "CNAME at the zone apex (name = \"@\"). DigitalOcean has no "
                    "apex CNAME and no flattening; use an A/AAAA record instead."
                ),
            })

        if rtype in ("CNAME", "MX") and value is not None and not value.endswith("."):
            findings.append({
                "severity": "warning",
                "rule": "cname-relative-value",
                "label": label,
                "message": (
                    f'{rtype} value "{value}" has no trailing dot; the provider '
                    "treats it as relative and appends the domain. Use a full "
                    f'FQDN: value = "{value}."'
                ),
            })
    return findings


def _format_text(findings):
    lines = []
    for f in findings:
        lines.append(
            f'[{f["severity"].upper()}] {f["rule"]} '
            f'(digitalocean_record.{f["label"]}): {f["message"]}'
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint digitalocean_record Terraform blocks for DO DNS traps."
    )
    parser.add_argument("paths", nargs="+", help="Terraform .tf files to scan")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args(argv)

    all_findings = []
    for path in args.paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        for f in lint_text(text):
            f = dict(f, file=path)
            all_findings.append(f)

    if args.format == "json":
        print(json.dumps(all_findings, indent=2))
    elif all_findings:
        print(_format_text(all_findings))

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/test_do_dns_tf_lint.py -v`
Expected: PASS — all tests OK.

- [ ] **Step 5: Verify `--help` works**

Run: `python3 plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/do_dns_tf_lint.py --help`
Expected: usage text printed, exit 0.

- [ ] **Step 6: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts
git commit -m "feat(do): add do_dns_tf_lint.py validator with tests"
```

---

### Task 4: Write `SKILL.md`

**Files:**
- Create: `plugins/digitalocean-skills/skills/digitalocean-dns-zones/SKILL.md`

- [ ] **Step 1: Write the frontmatter + body**

Use this exact frontmatter (function-first description, real trigger keywords):

```yaml
---
name: digitalocean-dns-zones
description: Operates DigitalOcean DNS zones and records via doctl, the DigitalOcean API v2, and the digitalocean Terraform provider — domain/record CRUD, the apex CNAME / no-flattening trap when migrating from Cloudflare, account-wide token handling, FQDN trailing-dot semantics, DNS-01 ACME wildcard certs, and nameserver delegation. Use when working with DigitalOcean DNS, doctl compute domain, DIGITALOCEAN_ACCESS_TOKEN, api.digitalocean.com domains, digitalocean_record/digitalocean_domain Terraform, apex CNAME questions, wildcard cert DNS-01, or moving a zone between Cloudflare and DigitalOcean.
---
```

Body sections (prose, under 500 lines total — verify with the line-count check in Task 5). Write each section using the verified facts below; do not invent flags beyond these:

1. **When to invoke** — symptom list: apex won't take a CNAME; CNAME resolves to a doubled FQDN; wildcard cert needed; migrating a zone to/from Cloudflare; choosing doctl vs Terraform vs raw API.

2. **Cross-cutting rules** (lead the body):
   1. The `DIGITALOCEAN_ACCESS_TOKEN` is **account-wide** — it cannot be scoped to a single zone the way a Cloudflare API Token can. Treat it as a high-value secret; never echo it into shared shells or CI logs. (Contrast explicitly with the `cloudflare-dns-zones` skill's "scope tokens to the minimum" rule.)
   2. **Domain before records** — `doctl compute domain create example.com` (or `digitalocean_domain`) must exist before any record. The apex uses `name = "@"`.
   3. **No apex CNAME, no flattening** — DigitalOcean has no ALIAS/ANAME and no CNAME flattening. A bare apex must be `A`/`AAAA`. This is the #1 Cloudflare-migration trap. Verified: the provider's apex examples are only A/AAAA/MX/TXT/CAA.
   4. **CNAME/MX values are FQDNs ending in a dot** — `value = "mail.example.com."`. A dotless value is treated as relative and the domain is appended.
   5. **List-then-act for idempotency** — no upsert-by-name; look up by name+type, then create or update by record ID.
   6. **`ttl` is `>= 0`, default 1800** — do NOT assert a 30s floor (stale control-panel lore; the provider permits any non-negative value).

3. **doctl record CRUD** — verified commands:
   ```bash
   doctl compute domain create example.com
   doctl compute domain records list example.com
   doctl compute domain records create example.com \
     --record-type A --record-name www --record-data 192.168.0.11
   doctl compute domain records update example.com --record-id <id> --record-data <new>
   doctl compute domain records delete example.com <record-id>
   ```
   Note: record names are relative to the zone; `@` (or the apex) targets the domain itself.

4. **Terraform** — verified `digitalocean_record` shape (show A apex, www CNAME with trailing dot, MX with priority, TXT/SPF). Reference the `scripts/do_dns_tf_lint.py` validator and how to run it (`python3 scripts/do_dns_tf_lint.py path/to/*.tf --format json`). Spell out the two rules it enforces and the one it deliberately doesn't (low TTL).

5. **DNS-01 ACME / wildcard certs** — cert-manager DigitalOcean DNS solver (a `tokenSecretRef` to a secret holding the DO token) or lego/acme.sh with the token in the environment. Reuse the account-wide-token caution. Keep this concise; do not duplicate the `cloudflare-dns-zones` DNS-01 prose — cross-reference it in plain text.

6. **Nameserver delegation & migration** — DO nameservers are `ns1.digitalocean.com`, `ns2.digitalocean.com`, `ns3.digitalocean.com`, set at the registrar. DigitalOcean has no BIND zone-file export endpoint, so migrating a zone to/from Cloudflare is record-by-record via the API/doctl — flag this as real friction and point at the list commands above to script it.

7. **Cross-skill note** (plain prose, no wikilinks): "For Cloudflare DNS, see the `cloudflare-dns-zones` skill; for identifying which S3-compatible provider a bucket belongs to (incl. DO Spaces), see `cloud-storage-identification`."

- [ ] **Step 2: Verify frontmatter parses and body is under 500 lines**

Run:
```bash
python3 - <<'PY'
import sys
p = "plugins/digitalocean-skills/skills/digitalocean-dns-zones/SKILL.md"
lines = open(p).read().splitlines()
assert lines[0] == "---", "missing frontmatter open"
end = lines.index("---", 1)
print("frontmatter ok, total lines:", len(lines))
assert len(lines) < 500, "SKILL.md exceeds 500 lines"
print("PASS")
PY
```
Expected: `frontmatter ok ...` then `PASS`.

- [ ] **Step 3: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-dns-zones/SKILL.md
git commit -m "feat(do): add digitalocean-dns-zones SKILL.md"
```

---

### Task 5: Registry self-check + finalize

**Files:**
- Modify (if needed): any file flagged by the self-check.

- [ ] **Step 1: Run the registry self-check**

Verify against the CLAUDE.md self-check list:
```bash
# Frontmatter name shape and length
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("plugins/digitalocean-skills/skills/digitalocean-dns-zones/SKILL.md")
fm = p.read_text().split("---")[1]
name = re.search(r"name:\s*(.+)", fm).group(1).strip()
desc = re.search(r"description:\s*(.+)", fm).group(1).strip()
assert re.fullmatch(r"[a-z0-9-]+", name), f"bad name: {name}"
assert len(name) <= 64, "name too long"
assert "claude" not in name and "anthropic" not in name
assert len(desc) <= 1024, "description too long"
print("frontmatter PASS:", name, f"({len(desc)} desc chars)")
PY

# No wikilinks, no Windows paths anywhere in the skill
! grep -rn '\[\[' plugins/digitalocean-skills/ && echo "no wikilinks PASS"
! grep -rn 'scripts\\\\' plugins/digitalocean-skills/ && echo "no windows paths PASS"

# Validator tests still green
python3 plugins/digitalocean-skills/skills/digitalocean-dns-zones/scripts/test_do_dns_tf_lint.py
```
Expected: `frontmatter PASS ...`, `no wikilinks PASS`, `no windows paths PASS`, and `OK` from unittest.

- [ ] **Step 2: Confirm at least 3 evals exist**

Run: `ls plugins/digitalocean-skills/skills/digitalocean-dns-zones/evals/*.json | wc -l`
Expected: `3` (or more).

- [ ] **Step 3: Update the root registry if it tracks plugins**

Check whether a top-level README or marketplace manifest enumerates plugins:
```bash
grep -rln "do-registry-cleanup\|devops-skills" README.md .claude-plugin 2>/dev/null
```
If a manifest lists plugins, add `digitalocean-skills` to it in the same format, then `git add` it. If nothing matches, skip — no manifest to update.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore(do): finalize digitalocean-dns-zones skill"
```

- [ ] **Step 5: Offer to open a PR**

Ask the user whether to push `skill/digitalocean-dns-zones` and open a PR against `main`. Do not push without confirmation (repo convention: commit/push only when asked).

---

## Self-Review

**Spec coverage:** plugin scaffold (Task 1) ✓; DNS-only scope ✓; the five cross-cutting rules incl. corrected TTL guidance (Task 4 §2) ✓; doctl + Terraform side by side (Task 4 §3–4) ✓; DNS-01 wildcard (§5) ✓; delegation/migration (§6) ✓; validator with the two real rules and dropped TTL check (Task 3) ✓; three evals written before the body (Task 2) ✓; Context7 verification done before plan (provider + doctl) ✓; cross-skill prose reference, no wikilinks (§7, Task 5) ✓.

**Placeholder scan:** validator and tests are complete code; eval and plugin JSON are complete; SKILL.md §-by-§ content is specified with verified facts. The SKILL prose itself is authored in Task 4 (a writing task, not a placeholder) — acceptable for a documentation deliverable.

**Type consistency:** `lint_text` returns finding dicts with keys `severity`, `rule`, `label`, `message` (CLI adds `file`); tests assert on `severity`/`rule`; CLI exit codes 0/1/2 consistent across the validator docstring, tests, and Task 5. Rule names `apex-cname` and `cname-relative-value` match between validator, tests, and SKILL §4.
