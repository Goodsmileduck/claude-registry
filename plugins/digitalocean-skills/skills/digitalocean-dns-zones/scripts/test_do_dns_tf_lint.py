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
