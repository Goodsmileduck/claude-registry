# digitalocean-app-platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `digitalocean-app-platform` skill to the `digitalocean-skills` plugin that lints App Platform app specs (JSON, block-YAML subset, and Terraform `digitalocean_app`) for 11 security/reliability/correctness/sizing anti-patterns.

**Architecture:** One stdlib-only Python linter, `do_app_spec_lint.py`. Three input front-ends (JSON via `json`, a hand-rolled block-YAML subset parser, and an HCL block parser for `digitalocean_app`) all normalize to one canonical spec dict. Checks run against the normalized dict only, so they are parser-agnostic. Text + `--format json` output; exit non-zero on any error-severity finding.

**Tech Stack:** Python 3 stdlib only (`argparse`, `json`, `re`, `sys`). `unittest` for tests. Markdown SKILL.md + JSON evals. Mirrors the existing `digitalocean-dns-zones` skill exactly.

Spec: `docs/superpowers/specs/2026-06-01-digitalocean-app-platform-design.md`.

---

## File Structure

```
plugins/digitalocean-skills/skills/digitalocean-app-platform/
├── SKILL.md                        # entry point (Task 9)
├── evals/                          # Task 8
│   ├── secret-plaintext.json
│   ├── single-instance-no-healthcheck.json
│   └── deprecated-routes.json
└── scripts/
    ├── do_app_spec_lint.py         # the linter (Tasks 1-7)
    └── test_do_app_spec_lint.py    # unittest suite (Tasks 1-7)
```

Also modified:
- `plugins/digitalocean-skills/.claude-plugin/plugin.json` (Task 10)
- repo-root `marketplace.json` and `README` if they carry per-skill detail (Task 10)

### Canonical normalized spec (the contract every parser produces)

```python
# _normalize(raw_dict) -> normalized dict with these guaranteed keys:
{
  "name": str | None,
  "region": str | None,
  "envs": [ {"key": str, "value": str|None, "type": str|None, "scope": str|None}, ... ],  # app-level
  "ingress": {"rules": [ {"prefix": str|None, "component": str|None}, ... ]},
  "databases": [ {"name": str|None, "production": bool|None, "region": str|None}, ... ],
  "components": [ {                       # services+workers+jobs+functions+static_sites merged
      "kind": "service"|"worker"|"job"|"function"|"static_site",
      "name": str|None,
      "instance_count": int|None,
      "instance_size_slug": str|None,
      "http_port": int|None,
      "autoscaling": dict|None,
      "health_check": {"http_path": str|None, "port": int|None} | None,
      "has_git": bool,                    # any of git/github/gitlab present
      "has_image": bool,
      "routes": [ {"path": str|None}, ... ],   # component-level (deprecated) routes
      "envs": [ {"key","value","type","scope"}, ... ],
  }, ... ],
}
```

Checks consume only this shape. The `Finding` dict shape (matches dns-zones plus two fields):

```python
{"severity": "error"|"warning", "rule": str, "component": str, "message": str, "fix": str}
```

---

## Task 1: Linter skeleton — CLI, JSON input, normalizer, reporter

**Files:**
- Create: `plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/do_app_spec_lint.py`
- Create: `plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

Create `test_do_app_spec_lint.py`:

```python
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("do_app_spec_lint.py")
sys.path.insert(0, str(SCRIPT.parent))
import do_app_spec_lint as lint  # noqa: E402

# A minimal, clean service spec (dict form, as json.load would produce).
CLEAN = {
    "name": "web-app",
    "region": "nyc",
    "services": [{
        "name": "web",
        "instance_count": 2,
        "instance_size_slug": "apps-s-1vcpu-1gb",
        "http_port": 8080,
        "health_check": {"http_path": "/healthz"},
        "envs": [{"key": "LOG_LEVEL", "value": "info", "scope": "RUN_TIME"}],
    }],
}


class TestNormalizeAndPipeline(unittest.TestCase):
    def test_normalize_merges_components_and_defaults(self):
        norm = lint._normalize(CLEAN)
        self.assertEqual(norm["name"], "web-app")
        self.assertEqual(len(norm["components"]), 1)
        c = norm["components"][0]
        self.assertEqual(c["kind"], "service")
        self.assertEqual(c["instance_count"], 2)
        self.assertEqual(c["health_check"]["http_path"], "/healthz")
        self.assertEqual(norm["databases"], [])
        self.assertEqual(norm["ingress"]["rules"], [])

    def test_clean_spec_has_no_findings(self):
        self.assertEqual(lint.lint_spec(lint._normalize(CLEAN)), [])


class TestCli(unittest.TestCase):
    def _run(self, args, **kw):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            capture_output=True, text=True, **kw)

    def test_help(self):
        r = self._run(["--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("app spec", r.stdout.lower())

    def test_clean_json_file_exits_zero(self):
        p = Path(self.id())  # unique-ish temp name in cwd-independent tmp
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps(CLEAN).encode())
        os.close(fd)
        try:
            r = self._run([path])
            self.assertEqual(r.returncode, 0, r.stderr)
            r2 = self._run([path, "--format", "json"])
            self.assertEqual(json.loads(r2.stdout), [])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts && python3 -m unittest test_do_app_spec_lint -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'do_app_spec_lint'`.

- [ ] **Step 3: Write minimal implementation**

Create `do_app_spec_lint.py`:

```python
#!/usr/bin/env python3
"""Linter for DigitalOcean App Platform app specs.

Accepts three input formats, all normalized to one canonical dict before
checks run:

  - JSON (primary): output of `doctl apps spec get --format json` or the API
    app `spec` object. Parsed with the stdlib json module.
  - Block-YAML subset (fallback): the indent-based YAML DigitalOcean emits in
    `.do/app.yaml`. NOT a full YAML parser — anchors, flow collections, and
    folded/literal block scalars are rejected with a clear error directing the
    user to pass JSON.
  - Terraform: `resource "digitalocean_app"` -> `spec { ... }`, via a small
    HCL block parser.

Checks (rule ids): secret-not-encrypted, secret-build-scope, no-health-check,
single-instance, dev-db-as-prod, port-mismatch, route-overlap, source-conflict,
deprecated-routes, unknown-instance-slug, db-region-mismatch.

Exit codes: 0 = clean (or warnings only), 1 = at least one error-severity
finding, 2 = usage/IO/parse error.
"""
import argparse
import json
import sys

COMPONENT_KINDS = ("services", "workers", "jobs", "functions", "static_sites")
# Component kinds that serve HTTP traffic and so warrant health-check / scaling
# checks. Jobs and functions are excluded (no long-running listener).
HTTP_KINDS = ("service", "worker", "static_site")


def _as_list(val):
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def _norm_env(e):
    return {
        "key": e.get("key"),
        "value": e.get("value"),
        "type": e.get("type"),
        "scope": e.get("scope"),
    }


def _norm_component(raw, kind_plural):
    kind = kind_plural.rstrip("s") if kind_plural != "static_sites" else "static_site"
    hc = raw.get("health_check")
    health = None
    if isinstance(hc, dict):
        health = {"http_path": hc.get("http_path"), "port": hc.get("port")}
    return {
        "kind": kind,
        "name": raw.get("name"),
        "instance_count": raw.get("instance_count"),
        "instance_size_slug": raw.get("instance_size_slug"),
        "http_port": raw.get("http_port"),
        "autoscaling": raw.get("autoscaling") if isinstance(raw.get("autoscaling"), dict) else None,
        "health_check": health,
        "has_git": any(raw.get(k) for k in ("git", "github", "gitlab")),
        "has_image": bool(raw.get("image")),
        "routes": [{"path": r.get("path")} for r in _as_list(raw.get("routes")) if isinstance(r, dict)],
        "envs": [_norm_env(e) for e in _as_list(raw.get("envs")) if isinstance(e, dict)],
    }


def _norm_ingress(raw):
    rules = []
    ingress = raw.get("ingress")
    if isinstance(ingress, dict):
        for rule in _as_list(ingress.get("rules")):
            if not isinstance(rule, dict):
                continue
            match = rule.get("match") or {}
            path = match.get("path") or {}
            comp = rule.get("component") or {}
            rules.append({"prefix": path.get("prefix"), "component": comp.get("name")})
    return {"rules": rules}


def _norm_database(d):
    return {
        "name": d.get("name"),
        "production": d.get("production"),
        "region": d.get("region"),
    }


def _normalize(raw):
    """Turn a raw spec dict (JSON/YAML/HCL origin) into the canonical shape."""
    if not isinstance(raw, dict):
        raise ValueError("spec root must be a mapping")
    # A spec may be wrapped as {"spec": {...}} (API response) — unwrap it.
    if "spec" in raw and isinstance(raw["spec"], dict) and not any(
            k in raw for k in COMPONENT_KINDS):
        raw = raw["spec"]
    components = []
    for plural in COMPONENT_KINDS:
        for c in _as_list(raw.get(plural)):
            if isinstance(c, dict):
                components.append(_norm_component(c, plural))
    return {
        "name": raw.get("name"),
        "region": raw.get("region"),
        "envs": [_norm_env(e) for e in _as_list(raw.get("envs")) if isinstance(e, dict)],
        "ingress": _norm_ingress(raw),
        "databases": [_norm_database(d) for d in _as_list(raw.get("databases")) if isinstance(d, dict)],
        "components": components,
    }


# --- check registry -------------------------------------------------------
# Each check: (normalized_spec) -> list[Finding]. Registered in CHECKS.
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def lint_spec(norm):
    findings = []
    for fn in CHECKS:
        findings.extend(fn(norm))
    return findings


# --- input dispatch -------------------------------------------------------

def load_spec(text, path):
    """Parse raw text (format auto-detected) into a normalized spec."""
    stripped = text.lstrip()
    if path.endswith(".tf") or 'resource "digitalocean_app"' in text:
        raw = parse_hcl_app(text)            # Task 7
    elif stripped.startswith("{"):
        raw = json.loads(text)
    else:
        raw = parse_yaml_subset(text)        # Task 6
    return _normalize(raw)


# --- reporter -------------------------------------------------------------

def _format_text(findings):
    lines = []
    for f in findings:
        comp = f"({f['component']}) " if f.get("component") else ""
        lines.append(f"[{f['severity'].upper()}] {f['rule']} {comp}{f['message']}")
        if f.get("fix"):
            lines.append(f"    fix: {f['fix']}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Lint DigitalOcean App Platform app specs (JSON, YAML subset, or Terraform).")
    parser.add_argument("paths", nargs="+", help="app spec files (.json/.yaml/.yml/.tf)")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format (default: text)")
    args = parser.parse_args(argv)

    all_findings = []
    for path in args.paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        try:
            norm = load_spec(text, path)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"error: cannot parse {path}: {exc}", file=sys.stderr)
            return 2
        for f in lint_spec(norm):
            all_findings.append(dict(f, file=path))

    if args.format == "json":
        print(json.dumps(all_findings, indent=2))
    elif all_findings:
        print(_format_text(all_findings))

    return 1 if any(f["severity"] == "error" for f in all_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: `parse_yaml_subset` and `parse_hcl_app` are referenced but defined in Tasks 6–7. Until then they don't exist; Task 1 tests use JSON only, so add temporary stubs at the bottom of the module so import succeeds:

```python
def parse_yaml_subset(text):  # replaced in Task 6
    raise ValueError("YAML parsing not yet implemented; pass JSON")


def parse_hcl_app(text):  # replaced in Task 7
    raise ValueError("Terraform parsing not yet implemented; pass JSON")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): scaffold do_app_spec_lint with JSON input, normalizer, CLI"
```

---

## Task 2: Secret checks (`secret-not-encrypted`, `secret-build-scope`)

**Files:**
- Modify: `do_app_spec_lint.py` (add constants + two checks)
- Test: `test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
class TestSecretChecks(unittest.TestCase):
    def _findings(self, spec):
        return {f["rule"] for f in lint.lint_spec(lint._normalize(spec))}

    def test_plaintext_secret_value_flagged(self):
        spec = {"services": [{"name": "api", "envs": [
            {"key": "API_KEY", "value": "AKIA1234567890ABCDEF", "type": "GENERAL"}]}]}
        self.assertIn("secret-not-encrypted", self._findings(spec))

    def test_substitution_ref_not_flagged(self):
        spec = {"services": [{"name": "api", "envs": [
            {"key": "API_KEY", "value": "${API_KEY}", "type": "SECRET"}]}]}
        self.assertNotIn("secret-not-encrypted", self._findings(spec))

    def test_bindable_ref_not_flagged(self):
        spec = {"services": [{"name": "api", "envs": [
            {"key": "DATABASE_URL", "value": "${db.DATABASE_URL}"}]}]}
        self.assertNotIn("secret-not-encrypted", self._findings(spec))

    def test_pem_value_flagged_even_with_benign_key(self):
        spec = {"services": [{"name": "api", "envs": [
            {"key": "CONFIG", "value": "-----BEGIN PRIVATE KEY-----\nMII...", "type": "GENERAL"}]}]}
        self.assertIn("secret-not-encrypted", self._findings(spec))

    def test_secret_in_build_scope_flagged(self):
        spec = {"services": [{"name": "api", "envs": [
            {"key": "TOKEN", "value": "${TOKEN}", "type": "SECRET",
             "scope": "RUN_AND_BUILD_TIME"}]}]}
        self.assertIn("secret-build-scope", self._findings(spec))

    def test_app_level_envs_checked_too(self):
        spec = {"envs": [{"key": "PASSWORD", "value": "hunter2hunter2hunter2", "type": "GENERAL"}],
                "services": [{"name": "api"}]}
        self.assertIn("secret-not-encrypted", self._findings(spec))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestSecretChecks -v`
Expected: FAIL — findings sets are empty (no checks registered yet).

- [ ] **Step 3: Write minimal implementation**

Insert after the `check` decorator definition in `do_app_spec_lint.py`:

```python
# Env-key substrings that strongly imply the value is a credential.
SECRET_KEY_HINTS = (
    "SECRET", "TOKEN", "PASSWORD", "PASSWD", "APIKEY", "API_KEY",
    "PRIVATE_KEY", "ACCESS_KEY", "CLIENT_SECRET",
)
# A value-only signal: long, whitespace-free strings that mix letters+digits
# look like raw credentials. 24 chars avoids flagging short slugs/enum values
# while catching typical API keys/tokens (AWS keys are 20-40, JWTs longer).
MIN_SECRET_VALUE_LEN = 24


def _is_substitution(value):
    # ${VAR}, ${db.X}, ${APP_URL} are GitHub-secret / bindable / app-wide refs,
    # never literal secrets. Any value containing ${ is treated as a ref.
    return isinstance(value, str) and "${" in value


def _looks_like_secret_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("-----BEGIN"):
        return True
    v = value.strip()
    if len(v) >= MIN_SECRET_VALUE_LEN and not any(c.isspace() for c in v):
        has_alpha = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        return has_alpha and has_digit
    return False


def _key_implies_secret(key):
    return isinstance(key, str) and any(h in key.upper() for h in SECRET_KEY_HINTS)


def _iter_envs(norm):
    """Yield (component_label, env) for app-level and component envs."""
    for e in norm["envs"]:
        yield "app", e
    for c in norm["components"]:
        for e in c["envs"]:
            yield c["name"] or c["kind"], e


@check
def check_secret_not_encrypted(norm):
    findings = []
    for label, e in _iter_envs(norm):
        value, etype = e["value"], e["type"]
        if _is_substitution(value):
            continue
        if etype == "SECRET":
            continue
        if _key_implies_secret(e["key"]) or _looks_like_secret_value(value):
            findings.append({
                "severity": "error", "rule": "secret-not-encrypted",
                "component": label,
                "message": f'env "{e["key"]}" holds a secret-looking value with type != SECRET.',
                "fix": 'set type: SECRET and supply the value via ${VAR} substitution, not inline.',
            })
    return findings


@check
def check_secret_build_scope(norm):
    findings = []
    for label, e in _iter_envs(norm):
        if e["type"] == "SECRET" and e["scope"] == "RUN_AND_BUILD_TIME":
            findings.append({
                "severity": "warning", "rule": "secret-build-scope",
                "component": label,
                "message": f'secret env "{e["key"]}" is scoped RUN_AND_BUILD_TIME and leaks into the build layer.',
                "fix": "use scope: RUN_TIME unless the build genuinely needs the secret.",
            })
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS (all prior + 6 new).

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add secret-not-encrypted and secret-build-scope checks"
```

---

## Task 3: Reliability checks (`no-health-check`, `single-instance`, `dev-db-as-prod`)

**Files:**
- Modify: `do_app_spec_lint.py`
- Test: `test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

```python
class TestReliabilityChecks(unittest.TestCase):
    def _findings(self, spec):
        return {f["rule"] for f in lint.lint_spec(lint._normalize(spec))}

    def test_service_without_health_check_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2}]}
        self.assertIn("no-health-check", self._findings(spec))

    def test_service_with_health_check_not_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"}}]}
        self.assertNotIn("no-health-check", self._findings(spec))

    def test_job_without_health_check_not_flagged(self):
        spec = {"jobs": [{"name": "migrate"}]}
        self.assertNotIn("no-health-check", self._findings(spec))

    def test_single_instance_no_autoscaling_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 1,
                              "health_check": {"http_path": "/"}}]}
        self.assertIn("single-instance", self._findings(spec))

    def test_autoscaling_suppresses_single_instance(self):
        spec = {"services": [{"name": "web", "instance_count": 1,
                              "autoscaling": {"min_instance_count": 1, "max_instance_count": 3},
                              "health_check": {"http_path": "/"}}]}
        self.assertNotIn("single-instance", self._findings(spec))

    def test_two_instances_not_flagged_single(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"}}]}
        self.assertNotIn("single-instance", self._findings(spec))

    def test_dev_db_flagged(self):
        spec = {"databases": [{"name": "db", "production": False}],
                "services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"}}]}
        self.assertIn("dev-db-as-prod", self._findings(spec))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestReliabilityChecks -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Append checks to `do_app_spec_lint.py`:

```python
@check
def check_no_health_check(norm):
    findings = []
    for c in norm["components"]:
        if c["kind"] not in HTTP_KINDS:
            continue
        if c["kind"] == "static_site":  # static sites have no health_check concept
            continue
        hc = c["health_check"]
        if not hc or (not hc.get("http_path") and not hc.get("port")):
            findings.append({
                "severity": "warning", "rule": "no-health-check",
                "component": c["name"] or c["kind"],
                "message": "service has no health_check; App Platform falls back to a TCP check only.",
                "fix": "add health_check.http_path (e.g. /healthz) so unhealthy instances are recycled.",
            })
    return findings


@check
def check_single_instance(norm):
    findings = []
    for c in norm["components"]:
        if c["kind"] != "service":
            continue
        count = c["instance_count"]
        # None means unspecified, which App Platform defaults to 1 -> still a SPOF.
        if c["autoscaling"] is None and (count is None or count == 1):
            findings.append({
                "severity": "warning", "rule": "single-instance",
                "component": c["name"] or c["kind"],
                "message": "service runs a single instance with no autoscaling (single point of failure).",
                "fix": "set instance_count >= 2 or add an autoscaling block for HA.",
            })
    return findings


@check
def check_dev_db_as_prod(norm):
    findings = []
    for d in norm["databases"]:
        if d["production"] is False:
            findings.append({
                "severity": "warning", "rule": "dev-db-as-prod",
                "component": d["name"] or "database",
                "message": "database has production: false (a dev database — no backups, no standby).",
                "fix": "set production: true for any database backing a real workload.",
            })
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add reliability checks (health check, single instance, dev db)"
```

---

## Task 4: Correctness checks (`port-mismatch`, `route-overlap`, `source-conflict`, `deprecated-routes`)

**Files:**
- Modify: `do_app_spec_lint.py`
- Test: `test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

```python
class TestCorrectnessChecks(unittest.TestCase):
    def _findings(self, spec):
        return {f["rule"] for f in lint.lint_spec(lint._normalize(spec))}

    def test_port_mismatch_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2, "http_port": 8080,
                              "health_check": {"http_path": "/", "port": 9090}}]}
        self.assertIn("port-mismatch", self._findings(spec))

    def test_matching_ports_not_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2, "http_port": 8080,
                              "health_check": {"http_path": "/", "port": 8080}}]}
        self.assertNotIn("port-mismatch", self._findings(spec))

    def test_duplicate_route_prefix_flagged(self):
        spec = {"ingress": {"rules": [
            {"match": {"path": {"prefix": "/api"}}, "component": {"name": "a"}},
            {"match": {"path": {"prefix": "/api"}}, "component": {"name": "b"}}]}}
        self.assertIn("route-overlap", self._findings(spec))

    def test_prefix_shadow_flagged(self):
        spec = {"ingress": {"rules": [
            {"match": {"path": {"prefix": "/"}}, "component": {"name": "a"}},
            {"match": {"path": {"prefix": "/api"}}, "component": {"name": "b"}}]}}
        self.assertIn("route-overlap", self._findings(spec))

    def test_distinct_routes_not_flagged(self):
        spec = {"ingress": {"rules": [
            {"match": {"path": {"prefix": "/api"}}, "component": {"name": "a"}},
            {"match": {"path": {"prefix": "/web"}}, "component": {"name": "b"}}]}}
        self.assertNotIn("route-overlap", self._findings(spec))

    def test_source_conflict_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"},
                              "github": {"repo": "o/r", "branch": "main"},
                              "image": {"registry_type": "DOCR", "repository": "web"}}]}
        self.assertIn("source-conflict", self._findings(spec))

    def test_deprecated_routes_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"},
                              "routes": [{"path": "/"}]}]}
        self.assertIn("deprecated-routes", self._findings(spec))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestCorrectnessChecks -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Append checks:

```python
@check
def check_port_mismatch(norm):
    findings = []
    for c in norm["components"]:
        hc = c["health_check"]
        hc_port = hc.get("port") if hc else None
        if hc_port is not None and c["http_port"] is not None and hc_port != c["http_port"]:
            findings.append({
                "severity": "error", "rule": "port-mismatch",
                "component": c["name"] or c["kind"],
                "message": f'health_check.port ({hc_port}) != http_port ({c["http_port"]}); the check probes the wrong port.',
                "fix": "align health_check.port with http_port (or drop it to inherit http_port).",
            })
    return findings


@check
def check_route_overlap(norm):
    findings = []
    rules = [r for r in norm["ingress"]["rules"] if r["prefix"] is not None]
    seen = []
    for r in rules:
        for prev in seen:
            a, b = prev["prefix"], r["prefix"]
            # exact duplicate, or one prefix shadows the other, to different components
            shadows = a == b or b.startswith(a.rstrip("/") + "/") or a.startswith(b.rstrip("/") + "/") or a == "/" or b == "/"
            if shadows and prev["component"] != r["component"]:
                findings.append({
                    "severity": "error", "rule": "route-overlap",
                    "component": r["component"] or "ingress",
                    "message": f'ingress prefix "{b}" overlaps "{a}" (routes to a different component); matching is order-sensitive and ambiguous.',
                    "fix": "make prefixes mutually exclusive, or order most-specific-first and verify intent.",
                })
        seen.append(r)
    return findings


@check
def check_source_conflict(norm):
    findings = []
    for c in norm["components"]:
        if c["has_git"] and c["has_image"]:
            findings.append({
                "severity": "error", "rule": "source-conflict",
                "component": c["name"] or c["kind"],
                "message": "component sets both a git source and an image; App Platform needs exactly one.",
                "fix": "keep either the git/github/gitlab block or the image block, not both.",
            })
    return findings


@check
def check_deprecated_routes(norm):
    findings = []
    for c in norm["components"]:
        if c["routes"]:
            findings.append({
                "severity": "warning", "rule": "deprecated-routes",
                "component": c["name"] or c["kind"],
                "message": "component-level routes is deprecated in favour of top-level ingress.rules.",
                "fix": "move routing to spec.ingress.rules with match.path.prefix + component.name.",
            })
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add correctness checks (port, route overlap, source, routes)"
```

---

## Task 5: Sizing checks (`unknown-instance-slug`, `db-region-mismatch`)

**IMPLEMENTATION NOTE — verify before coding:** Confirm the current `instance_size_slug` enum via Context7 (`/digitalocean/app_action` or the digitalocean Terraform provider) or the live App Platform docs. The set below is the known-good list as of this plan; update `KNOWN_INSTANCE_SLUGS` to match what the docs return and keep the sourced comment.

**Files:**
- Modify: `do_app_spec_lint.py`
- Test: `test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

```python
class TestSizingChecks(unittest.TestCase):
    def _findings(self, spec):
        return {f["rule"] for f in lint.lint_spec(lint._normalize(spec))}

    def test_unknown_slug_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"},
                              "instance_size_slug": "mega-ultra-9000"}]}
        self.assertIn("unknown-instance-slug", self._findings(spec))

    def test_known_slug_not_flagged(self):
        spec = {"services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"},
                              "instance_size_slug": "apps-s-1vcpu-1gb"}]}
        self.assertNotIn("unknown-instance-slug", self._findings(spec))

    def test_region_mismatch_flagged(self):
        spec = {"region": "nyc",
                "databases": [{"name": "db", "production": True, "region": "sfo3"}],
                "services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"}}]}
        self.assertIn("db-region-mismatch", self._findings(spec))

    def test_region_prefix_match_not_flagged(self):
        # app region "nyc" vs db region "nyc1" share a datacenter -> no flag
        spec = {"region": "nyc",
                "databases": [{"name": "db", "production": True, "region": "nyc1"}],
                "services": [{"name": "web", "instance_count": 2,
                              "health_check": {"http_path": "/"}}]}
        self.assertNotIn("db-region-mismatch", self._findings(spec))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestSizingChecks -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Append constant + checks:

```python
# Known App Platform instance_size_slugs. Source: App Platform docs / digitalocean
# Terraform provider. VERIFY against live docs at implementation time and update.
KNOWN_INSTANCE_SLUGS = frozenset({
    # current "apps-" generation (shared = -s-, dedicated = -d-)
    "apps-s-1vcpu-0.5gb", "apps-s-1vcpu-1gb", "apps-s-1vcpu-2gb", "apps-s-2vcpu-4gb",
    "apps-d-1vcpu-0.5gb", "apps-d-1vcpu-1gb", "apps-d-1vcpu-2gb", "apps-d-2vcpu-4gb",
    "apps-d-2vcpu-8gb", "apps-d-4vcpu-8gb", "apps-d-4vcpu-16gb", "apps-d-8vcpu-32gb",
    # legacy generation still accepted by the API
    "basic-xxs", "basic-xs", "basic-s", "basic-m",
    "professional-xs", "professional-s", "professional-m",
    "professional-1l", "professional-l", "professional-xl",
})


def _region_match(a, b):
    if not a or not b:
        return True  # unspecified -> don't guess a mismatch
    # App spec region ("nyc") and DB region ("nyc1") share a datacenter prefix.
    return a == b or a.startswith(b) or b.startswith(a)


@check
def check_unknown_instance_slug(norm):
    findings = []
    for c in norm["components"]:
        slug = c["instance_size_slug"]
        if slug and slug not in KNOWN_INSTANCE_SLUGS:
            findings.append({
                "severity": "warning", "rule": "unknown-instance-slug",
                "component": c["name"] or c["kind"],
                "message": f'instance_size_slug "{slug}" is not a recognised App Platform size.',
                "fix": "use a current slug such as apps-s-1vcpu-1gb (run `doctl apps tier instance-size list`).",
            })
    return findings


@check
def check_db_region_mismatch(norm):
    findings = []
    for d in norm["databases"]:
        if not _region_match(norm["region"], d["region"]):
            findings.append({
                "severity": "warning", "rule": "db-region-mismatch",
                "component": d["name"] or "database",
                "message": f'database region "{d["region"]}" differs from app region "{norm["region"]}" (cross-region latency).',
                "fix": "co-locate the database with the app region unless cross-region is intentional.",
            })
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add sizing checks (unknown slug, db region mismatch)"
```

---

## Task 6: YAML subset parser

**Files:**
- Modify: `do_app_spec_lint.py` (replace the `parse_yaml_subset` stub)
- Test: `test_do_app_spec_lint.py`

- [ ] **Step 1: Write the failing test**

```python
class TestYamlSubset(unittest.TestCase):
    def test_parses_nested_block_mapping_and_sequence(self):
        text = """
name: web-app
region: nyc
services:
- name: web
  instance_count: 2
  http_port: 8080
  health_check:
    http_path: /healthz
  envs:
  - key: API_KEY
    value: ${API_KEY}
    type: SECRET
databases:
- name: db
  production: false
""".lstrip()
        raw = lint.parse_yaml_subset(text)
        self.assertEqual(raw["name"], "web-app")
        self.assertEqual(raw["services"][0]["instance_count"], 2)
        self.assertTrue(raw["services"][0]["http_port"] == 8080)
        self.assertEqual(raw["services"][0]["health_check"]["http_path"], "/healthz")
        self.assertEqual(raw["services"][0]["envs"][0]["type"], "SECRET")
        self.assertIs(raw["databases"][0]["production"], False)

    def test_end_to_end_yaml_file_flags_dev_db(self):
        text = "services:\n- name: web\n  instance_count: 2\n  health_check:\n    http_path: /\ndatabases:\n- name: db\n  production: false\n"
        norm = lint.load_spec(text, "app.yaml")
        rules = {f["rule"] for f in lint.lint_spec(norm)}
        self.assertIn("dev-db-as-prod", rules)

    def test_unsupported_construct_raises(self):
        for bad in ["a: &anchor 1\n", "a: {inline: 1}\n", "a: >\n  folded\n"]:
            with self.assertRaises(ValueError):
                lint.parse_yaml_subset(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestYamlSubset -v`
Expected: FAIL — stub raises "not yet implemented".

- [ ] **Step 3: Write minimal implementation**

Replace the `parse_yaml_subset` stub with:

```python
def _yaml_scalar(token):
    """Convert a YAML scalar token to a Python value."""
    t = token.strip()
    if t == "" or t == "~" or t == "null":
        return None
    if (t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'"):
        return t[1:-1]
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def parse_yaml_subset(text):
    """Parse the block-YAML subset DigitalOcean emits.

    Supports nested block mappings, block sequences (`- ` items), and plain or
    quoted scalars. Rejects anchors/aliases, flow collections, and folded/literal
    block scalars — directing the user to JSON. NOT a general YAML parser.
    """
    # Strip comments and blank lines, keep (indent, content) pairs.
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.strip() in ("---", "..."):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        for ch in ("&", "*"):
            if content.startswith(ch) or f": {ch}" in content:
                raise ValueError(f"unsupported YAML anchor/alias: {content!r}; pass JSON")
        if content.endswith((">", "|")):
            raise ValueError(f"unsupported YAML block scalar: {content!r}; pass JSON")
        if ": {" in content or ": [" in content or content in ("{", "["):
            raise ValueError(f"unsupported YAML flow collection: {content!r}; pass JSON")
        lines.append((indent, content))

    pos = [0]

    def parse_block(min_indent):
        # Decide mapping vs sequence by the first line at this level.
        if pos[0] >= len(lines):
            return None
        indent, content = lines[pos[0]]
        if content.startswith("- "):
            return parse_sequence(indent)
        return parse_mapping(indent)

    def parse_mapping(indent):
        node = {}
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise ValueError(f"unexpected indent at {content!r}")
            if content.startswith("- "):
                break
            if ":" not in content:
                raise ValueError(f"expected key: value, got {content!r}")
            key, _, rest = content.partition(":")
            key, rest = key.strip(), rest.strip()
            pos[0] += 1
            if rest == "":
                # A nested block follows when the next line is more-indented,
                # OR is a block sequence at the SAME indent as this key (YAML
                # allows `key:` then `- item` aligned with the key). A same-indent
                # non-sequence line is a sibling key -> this key's value is null.
                nested = False
                if pos[0] < len(lines):
                    nxt_indent, nxt_content = lines[pos[0]]
                    if nxt_indent > indent:
                        nested = True
                    elif nxt_indent == indent and nxt_content.startswith("- "):
                        nested = True
                node[key] = parse_block(indent + 1) if nested else None
            else:
                node[key] = _yaml_scalar(rest)
        return node

    def parse_sequence(indent):
        items = []
        while pos[0] < len(lines):
            cur_indent, content = lines[pos[0]]
            if cur_indent < indent or not content.startswith("- "):
                break
            if cur_indent > indent:
                raise ValueError(f"unexpected indent at {content!r}")
            inner = content[2:].strip()
            # Rewrite "- key: val" as a mapping line at indent+2 and recurse.
            if ":" in inner:
                lines[pos[0]] = (indent + 2, inner)
                items.append(parse_mapping(indent + 2))
            else:
                pos[0] += 1
                items.append(_yaml_scalar(inner))
        return items

    result = parse_block(0)
    return result if isinstance(result, dict) else {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS. If the `- key: val` rewrite test fails on multi-key list items, confirm the second key of a list item is indented to `indent + 2` in the fixture (it is, in the test above).

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add block-YAML subset parser with clear unsupported-syntax errors"
```

---

## Task 7: Terraform `digitalocean_app` parser

**Files:**
- Modify: `do_app_spec_lint.py` (replace the `parse_hcl_app` stub)
- Test: `test_do_app_spec_lint.py`

HCL nuance: in the `digitalocean_app` resource, repeated nested blocks (`service`, `env`, `database`, `rule`) are singular keywords that repeat and must become **lists**; singular blocks (`spec`, `health_check`, `autoscaling`, `image`, `github`, `git`, `gitlab`, `ingress`, `match`, `path`, `component`) become single objects. Map block keyword → normalized plural where needed (`service`→`services`, `env`→`envs`, `database`→`databases`, `rule`→`rules`, `route`→`routes`).

- [ ] **Step 1: Write the failing test**

```python
HCL_APP = '''
resource "digitalocean_app" "x" {
  spec {
    name   = "web-app"
    region = "nyc"
    service {
      name               = "web"
      instance_count     = 1
      instance_size_slug = "apps-s-1vcpu-1gb"
      http_port          = 8080
      health_check { http_path = "/" port = 9090 }
      env {
        key   = "API_KEY"
        value = "AKIA1234567890ABCDEF"
        type  = "GENERAL"
      }
    }
    database {
      name       = "db"
      production = false
    }
  }
}
'''


class TestHcl(unittest.TestCase):
    def test_parses_app_spec_blocks(self):
        raw = lint.parse_hcl_app(HCL_APP)
        self.assertEqual(raw["name"], "web-app")
        self.assertEqual(len(raw["services"]), 1)
        svc = raw["services"][0]
        self.assertEqual(svc["instance_count"], 1)
        self.assertEqual(svc["http_port"], 8080)
        self.assertEqual(svc["health_check"]["port"], 9090)
        self.assertEqual(svc["envs"][0]["key"], "API_KEY")
        self.assertIs(raw["databases"][0]["production"], False)

    def test_end_to_end_hcl_flags_expected(self):
        norm = lint.load_spec(HCL_APP, "main.tf")
        rules = {f["rule"] for f in lint.lint_spec(norm)}
        self.assertIn("secret-not-encrypted", rules)   # plaintext API_KEY
        self.assertIn("port-mismatch", rules)           # 9090 != 8080
        self.assertIn("single-instance", rules)         # count 1, no autoscaling
        self.assertIn("dev-db-as-prod", rules)          # production false

    def test_no_app_resource_returns_empty(self):
        self.assertEqual(lint.parse_hcl_app('resource "other" "y" {}'), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_do_app_spec_lint.TestHcl -v`
Expected: FAIL — stub raises.

- [ ] **Step 3: Write minimal implementation**

Replace the `parse_hcl_app` stub with a small recursive HCL block reader plus a normalizer. Add `import re` at the top of the module if not already present.

```python
import re  # ensure present at module top

# Block keywords that repeat inside a digitalocean_app spec -> normalized to lists
# under these plural keys. Everything else stays a singular object.
_HCL_REPEATED = {
    "service": "services", "worker": "workers", "job": "jobs",
    "function": "functions", "static_site": "static_sites",
    "database": "databases", "env": "envs", "rule": "rules", "route": "routes",
}
_HCL_ATTR = re.compile(r'(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<val>"(?:[^"\\]|\\.)*"|[^\s{}]+)')
_HCL_BLOCK_OPEN = re.compile(r'(?P<name>[A-Za-z0-9_]+)\s*(?:"[^"]*"\s*)*\{')


def _hcl_scalar(tok):
    if tok and tok[0] == '"' and tok[-1] == '"':
        return tok[1:-1].encode().decode("unicode_escape")
    if tok == "true":
        return True
    if tok == "false":
        return False
    try:
        return int(tok)
    except ValueError:
        return tok


def _parse_hcl_body(text, i):
    """Parse a brace body starting at index i (just after '{').

    Returns (node_dict, index_after_closing_brace). Repeated child blocks are
    collected into lists keyed by their normalized plural name.
    """
    node = {}
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "}":
            return node, i + 1
        if ch in " \t\r\n,":
            i += 1
            continue
        if ch == "#" or text[i:i + 2] == "//":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        # Try a nested block first: NAME [labels] {
        bm = _HCL_BLOCK_OPEN.match(text, i)
        if bm and _balanced_is_block(text, bm.end() - 1):
            name = bm.group("name")
            child, i = _parse_hcl_body(text, bm.end())
            if name in _HCL_REPEATED:
                node.setdefault(_HCL_REPEATED[name], []).append(child)
            else:
                node[name] = child
            continue
        # Otherwise an attribute: key = value
        am = _HCL_ATTR.match(text, i)
        if am:
            node[am.group("key")] = _hcl_scalar(am.group("val"))
            i = am.end()
            continue
        i += 1
    return node, i


def _balanced_is_block(text, brace_idx):
    # Heuristic: the matched '{' begins a block (not a string). Always true here
    # because _HCL_BLOCK_OPEN only matches an identifier immediately before '{'.
    return True


def parse_hcl_app(text):
    """Extract the spec of the first resource "digitalocean_app" as a raw dict."""
    m = re.search(r'resource\s+"digitalocean_app"\s+"[^"]*"\s*\{', text)
    if not m:
        return {}
    body, _ = _parse_hcl_body(text, m.end())
    spec = body.get("spec", {})
    return spec if isinstance(spec, dict) else {}
```

Note on `git`/`github`/`gitlab`/`image`/`autoscaling`: these are singular blocks, so `_parse_hcl_body` stores them as plain keys on the component dict — exactly what `_norm_component` checks via `raw.get("github")` etc. `health_check` likewise becomes a dict. No extra mapping needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_do_app_spec_lint -v`
Expected: PASS (full suite).

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts/
git commit -m "feat(do): add Terraform digitalocean_app parser"
```

---

## Task 8: Eval scenarios

**Files:**
- Create: `.../evals/secret-plaintext.json`
- Create: `.../evals/single-instance-no-healthcheck.json`
- Create: `.../evals/deprecated-routes.json`

- [ ] **Step 1: Write the three eval files**

`secret-plaintext.json`:

```json
{
  "query": "Is it safe to put my API key directly in the value field of my DigitalOcean app.yaml?",
  "files": ["app.yaml"],
  "expected_behavior": "Explains that a literal credential in an env value with type GENERAL is stored unencrypted and visible in the spec; the fix is type: SECRET with the value supplied via ${VAR} substitution, not inline. The do_app_spec_lint.py validator flags this as secret-not-encrypted."
}
```

`single-instance-no-healthcheck.json`:

```json
{
  "query": "My DigitalOcean App Platform service keeps going down with no warning and there's downtime on every deploy. Here's my spec.",
  "files": ["app.yaml"],
  "expected_behavior": "Identifies two reliability gaps: a single instance with no autoscaling (single point of failure) and a missing health_check (App Platform falls back to a TCP check and can't recycle unhealthy instances). Recommends instance_count >= 2 or an autoscaling block and a health_check.http_path. do_app_spec_lint.py flags single-instance and no-health-check."
}
```

`deprecated-routes.json`:

```json
{
  "query": "DigitalOcean is warning that `routes` is deprecated in my app spec — what do I use instead?",
  "files": ["app.yaml"],
  "expected_behavior": "Explains that component-level routes is superseded by top-level spec.ingress.rules with match.path.prefix and component.name. The do_app_spec_lint.py validator flags deprecated-routes."
}
```

- [ ] **Step 2: Validate they are well-formed JSON**

Run: `cd plugins/digitalocean-skills/skills/digitalocean-app-platform && python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('evals/*.json')]; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/evals/
git commit -m "test(do): add eval scenarios for digitalocean-app-platform"
```

---

## Task 9: SKILL.md

**Files:**
- Create: `.../digitalocean-app-platform/SKILL.md`

**Before writing:** verify current field names (`ingress.rules`, env `type`/`scope` enums, `instance_size_slug`) against live docs via Context7 (`/digitalocean/app_action`) — see CLAUDE.md.

- [ ] **Step 1: Write SKILL.md**

Frontmatter (function-first, third person, trigger terms a user types). Body sections: When to invoke; Cross-cutting rules; Running the validator (3 input modes + `--format json`); the four check categories with rule ids; a `## Proactive triggers` block (4–8 entries). Keep under 500 lines.

```markdown
---
name: digitalocean-app-platform
description: Lints DigitalOcean App Platform app specs (app.yaml / doctl apps spec JSON / digitalocean_app Terraform) for security, reliability, correctness, and sizing anti-patterns — plaintext secrets, missing health checks, single-instance services, dev databases in production, port mismatches, overlapping ingress routes, conflicting git/image sources, deprecated routes, unknown instance sizes, and app/database region mismatch. Use when working with DigitalOcean App Platform, app.yaml, .do/app.yaml, doctl apps, the digitalocean_app Terraform resource, or reviewing an App Platform deployment for problems.
---

# DigitalOcean App Platform

Reviews App Platform app specs for the mistakes that cause downtime, leaked
secrets, and broken routing. Ships a stdlib-only validator, `do_app_spec_lint.py`,
that ingests the spec as JSON (recommended), the block-YAML DO emits, or the
`digitalocean_app` Terraform resource, and reports findings with a rule id,
severity, and a one-line fix.

## When to invoke

- Reviewing or authoring an `app.yaml` / `.do/app.yaml` / `digitalocean_app`.
- A service has downtime on deploy or flaps with no warning (health check / HA).
- DigitalOcean warns that `routes` is deprecated.
- A credential may be sitting in an env `value` in plaintext.
- Ingress routing behaves unexpectedly (overlapping prefixes).

## Cross-cutting rules

1. **Prefer JSON input.** `doctl apps spec get <app-id> --format json` is the
   most reliable input; the YAML path is a subset parser and rejects anchors,
   flow collections, and folded/literal scalars.
2. **Never put a literal secret in an env `value`.** Use `type: SECRET` and a
   `${VAR}` substitution. Values containing `${...}` (GitHub secrets, `${db.X}`
   bindable refs, `${APP_URL}` app-wide vars) are references, not literals.
3. **The app spec is the source of truth.** App Platform reconciles to the spec
   on every deploy; fix the spec, not the running app.

## Running the validator

```bash
# JSON (recommended)
doctl apps spec get <app-id> --format json > spec.json
python3 scripts/do_app_spec_lint.py spec.json

# YAML subset, or Terraform — format auto-detected by extension/content
python3 scripts/do_app_spec_lint.py .do/app.yaml
python3 scripts/do_app_spec_lint.py main.tf

# machine-readable
python3 scripts/do_app_spec_lint.py spec.json --format json
```

Exit 0 = clean or warnings only; 1 = at least one error-severity finding;
2 = unreadable/unparseable input.

## Checks

- **Secrets** — `secret-not-encrypted` (literal secret with type != SECRET),
  `secret-build-scope` (SECRET scoped RUN_AND_BUILD_TIME leaks into the build).
- **Reliability** — `no-health-check`, `single-instance` (one instance, no
  autoscaling), `dev-db-as-prod` (database with production: false).
- **Correctness** — `port-mismatch`, `route-overlap`, `source-conflict` (both
  git and image), `deprecated-routes`.
- **Sizing** — `unknown-instance-slug`, `db-region-mismatch`.

## Proactive triggers

- env `value` is a literal API key/token/password (type != SECRET) → flag
  `secret-not-encrypted`; move to `type: SECRET` + `${VAR}`.
- a `service` has `instance_count: 1` and no `autoscaling` → warn single point
  of failure.
- a `service` has no `health_check.http_path` → warn deploys can't detect
  unhealthy instances.
- both a git source and an `image` on one component → flag `source-conflict`.
- component-level `routes` present → recommend `spec.ingress.rules`.
- `production: false` on a database backing real traffic → warn dev database.
```

- [ ] **Step 2: Self-check against CLAUDE.md skill rules**

Verify: name lowercase-hyphens ≤64; description third-person, function-then-trigger, ≤1024 chars; body < 500 lines; no wikilinks, no Windows paths, no time-sensitive text. Fix inline.

- [ ] **Step 3: Commit**

```bash
git add plugins/digitalocean-skills/skills/digitalocean-app-platform/SKILL.md
git commit -m "feat(do): add digitalocean-app-platform SKILL.md"
```

---

## Task 10: Plugin metadata + registration + final verification

**Files:**
- Modify: `plugins/digitalocean-skills/.claude-plugin/plugin.json`
- Modify (if they carry per-skill detail): repo-root `marketplace.json`, `README.md`

- [ ] **Step 1: Update plugin.json**

Extend `description` to cover BOTH skills (DNS zones + App Platform spec linting) and add keywords. Read the file first, then edit. Add keywords: `app-platform`, `app-spec`, `digitalocean_app`, `app-yaml`, `doctl-apps`, `app-platform-lint`. Bump `version` to `0.2.0`.

- [ ] **Step 2: Check marketplace.json / README**

Run: `cd /home/goodsmileduck/local/personal/claude-registry && grep -rl "digitalocean-skills" marketplace.json README.md 2>/dev/null`
If `marketplace.json` lists the plugin with a description, confirm it still reads correctly for two skills (per project memory marketplace.json is authoritative; README is stale — only touch README if it already enumerates skills). Update the plugin description there to match plugin.json if needed.

- [ ] **Step 3: Full verification (evidence before claiming done)**

```bash
cd plugins/digitalocean-skills/skills/digitalocean-app-platform/scripts
python3 -m unittest test_do_app_spec_lint -v          # all green
python3 do_app_spec_lint.py --help                     # exit 0, usage prints
echo '{"services":[{"name":"web","instance_count":1,"envs":[{"key":"API_KEY","value":"AKIA1234567890ABCDEF","type":"GENERAL"}]}]}' > /tmp/bad.json
python3 do_app_spec_lint.py /tmp/bad.json; echo "exit=$?"   # prints findings, exit=1
python3 do_app_spec_lint.py /tmp/bad.json --format json | python3 -c "import json,sys; json.load(sys.stdin); print('valid json')"
cd /home/goodsmileduck/local/personal/claude-registry
python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('plugins/digitalocean-skills/skills/digitalocean-app-platform/evals/*.json')]; print('evals ok')"
```

Expected: unittest all PASS; `--help` exit 0; bad.json prints `secret-not-encrypted` and `single-instance`, exit=1; valid json; evals ok.

- [ ] **Step 4: Confirm no stray cache files committed**

Run: `git status --porcelain plugins/digitalocean-skills/skills/digitalocean-app-platform/`
Expected: no `__pycache__` or `.pytest_cache` staged. If present, add to `.gitignore` or remove before commit (the dns-zones skill left some locally; do not repeat).

- [ ] **Step 5: Commit**

```bash
git add plugins/digitalocean-skills/.claude-plugin/plugin.json marketplace.json
git commit -m "chore(do): register digitalocean-app-platform skill in plugin metadata"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** All 11 checks map to Tasks 2–5; JSON/YAML/HCL front-ends to Tasks 1/6/7; SKILL.md Task 9; evals Task 8; plugin.json + marketplace Task 10. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The only deferred value is `KNOWN_INSTANCE_SLUGS`, which carries an explicit "verify against live docs" instruction with a concrete starting set — intentional, not a placeholder. ✅

**Type consistency:** `_normalize` output keys (`components`, `health_check`, `has_git`, `has_image`, `instance_size_slug`, `ingress.rules` with `prefix`/`component`) are used identically across all checks; `Finding` keys (`severity`/`rule`/`component`/`message`/`fix`) consistent in checks and `_format_text`. `parse_yaml_subset`/`parse_hcl_app`/`load_spec`/`lint_spec`/`_normalize` names match across tasks. ✅
