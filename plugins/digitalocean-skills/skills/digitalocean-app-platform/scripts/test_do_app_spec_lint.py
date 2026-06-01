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


if __name__ == "__main__":
    unittest.main()
