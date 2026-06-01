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

    def test_sequence_aligned_with_parent_key(self):
        # YAML allows a block sequence at the same indent as its parent key.
        raw = lint.parse_yaml_subset("services:\n- name: web\n- name: api\n")
        self.assertEqual([s["name"] for s in raw["services"]], ["web", "api"])

    def test_empty_value_key_then_sibling_is_null(self):
        # An empty-value key followed by a same-indent NON-sequence sibling key
        # must yield null for the empty key, not swallow the sibling.
        raw = lint.parse_yaml_subset("a:\nb: 1\n")
        self.assertEqual(raw, {"a": None, "b": 1})


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


if __name__ == "__main__":
    unittest.main()
