import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("audit_skill_scripts.py")
sys.path.insert(0, str(SCRIPT.parent))
import audit_skill_scripts as A  # noqa: E402


def make_script(root, plugin, skill, filename, body):
    """Write plugins/<plugin>/skills/<skill>/scripts/<filename> and return its path."""
    d = root / "plugins" / plugin / "skills" / skill / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(body, encoding="utf-8")
    return p


class TestPythonSinks(unittest.TestCase):
    def _rules(self, body):
        with tempfile.TemporaryDirectory() as t:
            p = make_script(Path(t), "p1", "s1", "helper.py", body)
            return {f.rule for f in A.audit_python(p, body)}

    def test_clean_script(self):
        self.assertEqual(self._rules("import subprocess\nsubprocess.run(['ls'])\n"), set())

    def test_eval_flagged(self):
        self.assertIn("dangerous-call", self._rules("x = eval('1+1')\n"))

    def test_exec_flagged(self):
        self.assertIn("dangerous-call", self._rules("exec('print(1)')\n"))

    def test_os_system_flagged(self):
        self.assertIn("dangerous-call", self._rules("import os\nos.system('rm -rf /tmp/x')\n"))

    def test_os_popen_flagged(self):
        self.assertIn("dangerous-call", self._rules("import os\nos.popen('ls')\n"))

    def test_dunder_import_flagged(self):
        self.assertIn("dangerous-call", self._rules("m = __import__('os')\n"))

    def test_shell_true_flagged(self):
        self.assertIn("shell-true", self._rules("import subprocess\nsubprocess.run('ls', shell=True)\n"))

    def test_subprocess_without_shell_is_clean(self):
        self.assertEqual(self._rules("import subprocess\nsubprocess.run(['ls'], shell=False)\n"), set())

    def test_eval_in_comment_not_flagged(self):
        # AST-based: the word in a comment or string is not a call
        self.assertEqual(self._rules("# do not eval() here\nx = 'eval(this)'\n"), set())

    def test_unparseable_script_reported(self):
        self.assertIn("py-syntax", self._rules("def broken(:\n"))


class TestShellSinks(unittest.TestCase):
    def _rules(self, body):
        with tempfile.TemporaryDirectory() as t:
            p = make_script(Path(t), "p1", "s1", "install.sh", body)
            return {f.rule for f in A.audit_shell(p, body)}

    def test_pipe_to_shell_flagged(self):
        self.assertIn("pipe-to-shell", self._rules("curl https://x.sh | sh\n"))

    def test_wget_pipe_bash_flagged(self):
        self.assertIn("pipe-to-shell", self._rules("wget -qO- https://x | sudo bash\n"))

    def test_plain_curl_is_clean(self):
        self.assertEqual(self._rules("curl -o out.json https://api.example.com\n"), set())


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_help_exits_zero(self):
        self.assertEqual(self._run("--help").returncode, 0)

    def test_clean_repo_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            make_script(Path(t), "p1", "s1", "ok.py", "import subprocess\nsubprocess.run(['ls'])\n")
            r = self._run("--root", t, "--format", "json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout), [])
            self.assertIn("Audited 1 script(s)", r.stderr)

    def test_finding_exits_one(self):
        with tempfile.TemporaryDirectory() as t:
            make_script(Path(t), "p1", "s1", "bad.py", "exec('x')\n")
            r = self._run("--root", t, "--format", "json")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout)[0]["rule"], "dangerous-call")


if __name__ == "__main__":
    unittest.main()
