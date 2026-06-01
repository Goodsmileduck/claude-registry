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


if __name__ == "__main__":
    unittest.main()
