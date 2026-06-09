import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("lint_skills.py")
sys.path.insert(0, str(SCRIPT.parent))
import lint_skills as L  # noqa: E402


def make_skill(root, plugin, skill, name=None, description="Does a thing. Use when X.", body="\n# Title\n"):
    """Create plugins/<plugin>/skills/<skill>/SKILL.md under root and return its dir."""
    name = skill if name is None else name
    d = root / "plugins" / plugin / "skills" / skill
    d.mkdir(parents=True, exist_ok=True)
    fm = "---\n"
    if name is not None:
        fm += f"name: {name}\n"
    if description is not None:
        fm += f"description: {description}\n"
    fm += "---\n"
    (d / "SKILL.md").write_text(fm + body, encoding="utf-8")
    return d


def make_plugin_json(root, plugin, name=None, description="A plugin."):
    name = plugin if name is None else name
    d = root / "plugins" / plugin / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({"name": name, "description": description}), encoding="utf-8")


def make_marketplace(root, plugins):
    """plugins: list of dicts already shaped like marketplace entries."""
    d = root / ".claude-plugin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "marketplace.json").write_text(json.dumps({"name": "reg", "plugins": plugins}), encoding="utf-8")


class TestFrontmatter(unittest.TestCase):
    def test_parses_name_and_description(self):
        fields, ok = L.parse_frontmatter("---\nname: foo-bar\ndescription: Hello world.\n---\n# Body\n")
        self.assertTrue(ok)
        self.assertEqual(fields["name"], "foo-bar")
        self.assertEqual(fields["description"], "Hello world.")

    def test_no_frontmatter_returns_not_ok(self):
        fields, ok = L.parse_frontmatter("# Just a heading\n")
        self.assertFalse(ok)
        self.assertEqual(fields, {})

    def test_unterminated_block_is_not_ok(self):
        _, ok = L.parse_frontmatter("---\nname: foo\n# never closed\n")
        self.assertFalse(ok)

    def test_strips_quotes(self):
        fields, _ = L.parse_frontmatter('---\nname: "foo"\ndescription: \'bar\'\n---\n')
        self.assertEqual(fields["name"], "foo")
        self.assertEqual(fields["description"], "bar")


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_help_exits_zero(self):
        self.assertEqual(self._run("--help").returncode, 0)

    def test_clean_repo_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_skill(root, "p1", "alpha-skill")
            make_plugin_json(root, "p1")
            make_marketplace(root, [{"name": "p1", "description": "A plugin."}])
            r = self._run("--root", t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_json_format_is_parseable(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_skill(root, "p1", "alpha-skill")
            make_plugin_json(root, "p1")
            make_marketplace(root, [{"name": "p1", "description": "A plugin."}])
            r = self._run("--root", t, "--format", "json")
            self.assertEqual(json.loads(r.stdout), [])

    def test_coverage_summary_on_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_skill(root, "p1", "alpha-skill")
            make_skill(root, "p1", "beta-skill")
            make_plugin_json(root, "p1")
            make_marketplace(root, [{"name": "p1", "description": "A plugin."}])
            r = self._run("--root", t, "--format", "json")
            self.assertIn("Linted 2 skill(s) across 1 plugin(s)", r.stderr)

    def test_empty_root_warns(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run("--root", t)
            self.assertIn("no skills found", r.stderr)


class TestSkillRules(unittest.TestCase):
    def _rules(self, **kw):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), "p1", "alpha-skill", **kw)
            return {f.rule for f in L.lint_skill(d)}

    def test_clean_skill_no_findings(self):
        self.assertEqual(self._rules(), set())

    def test_missing_frontmatter(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "plugins/p1/skills/x"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
            self.assertIn("frontmatter-present", {f.rule for f in L.lint_skill(d)})

    def test_name_charset(self):
        self.assertIn("name-charset", self._rules(name="Bad_Name"))

    def test_name_length(self):
        self.assertIn("name-length", self._rules(name="a" + "-b" * 40))

    def test_name_forbidden_word(self):
        self.assertIn("name-forbidden-word", self._rules(name="claude-helper-x"))

    def test_name_claude_md_prefix_allowed(self):
        # references the CLAUDE.md file, not the assistant
        self.assertNotIn("name-forbidden-word", self._rules(name="claude-md-optimizer"))

    def test_name_vague(self):
        self.assertIn("name-vague", self._rules(name="tools"))

    def test_desc_length(self):
        self.assertIn("desc-length", self._rules(description="x" * 1100))

    def test_desc_first_person(self):
        self.assertIn("desc-first-person", self._rules(description="I can help you with stuff."))

    def test_desc_empty(self):
        self.assertIn("desc-nonempty", self._rules(description=""))

    def test_body_line_count(self):
        self.assertIn("body-line-count", self._rules(body="\n".join(str(i) for i in range(600))))

    def test_wikilinks(self):
        self.assertIn("wikilinks", self._rules(body="see [[other-skill]] here"))

    def test_wikilink_in_inline_code_is_ignored(self):
        # `[[routes]]` is TOML array-of-tables, not an Obsidian link
        self.assertNotIn("wikilinks", self._rules(body="attach a `[[routes]]` pattern"))

    def test_windows_path(self):
        self.assertIn("windows-path", self._rules(body="run scripts\\helper.py now"))

    def test_windows_path_in_fenced_code_is_ignored(self):
        self.assertNotIn("windows-path", self._rules(body="```\njsonpath '{.data.\\.dockerconfigjson}'\n```"))


class TestReferenceRules(unittest.TestCase):
    def _rules_for_refs(self, files):
        """files: dict of relative-path -> content under references/."""
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), "p1", "alpha-skill")
            for rel, content in files.items():
                p = d / "references" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            return {f.rule for f in L.lint_skill(d)}

    def test_clean_reference(self):
        toc = "# Topic\n\n- [A](#a)\n- [B](#b)\n\n" + "\n".join(f"line {i}" for i in range(120))
        self.assertEqual(self._rules_for_refs({"topic.md": toc}), set())

    def test_reference_with_frontmatter(self):
        self.assertIn("refs-no-frontmatter", self._rules_for_refs({"topic.md": "---\nx: 1\n---\nbody\n"}))

    def test_nested_reference_dir_is_allowed(self):
        # references/recipes/x.md is valid organization, not a violation
        self.assertEqual(self._rules_for_refs({"recipes/x.md": "# X\n\nhi\n"}), set())

    def test_long_reference_without_toc(self):
        body = "# Topic\n\n" + "\n".join(f"line {i}" for i in range(120))
        self.assertIn("refs-toc", self._rules_for_refs({"topic.md": body}))

    def test_reference_wikilink(self):
        self.assertIn("wikilinks", self._rules_for_refs({"topic.md": "see [[x]]\n"}))


class TestManifestRules(unittest.TestCase):
    def _rules(self, root):
        return {f.rule for f in L.lint_manifests(root)}

    def _base(self, t):
        root = Path(t)
        make_skill(root, "p1", "alpha-skill")
        make_plugin_json(root, "p1")
        return root

    def test_clean_manifests(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_marketplace(root, [{"name": "p1", "description": "A plugin."}])
            self.assertEqual(self._rules(root), set())

    def test_invalid_marketplace_json(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (root / ".claude-plugin" / "marketplace.json").write_text("{not json", encoding="utf-8")
            self.assertIn("json-valid", self._rules(root))

    def test_plugin_not_registered(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_marketplace(root, [])  # p1 on disk but absent from marketplace
            self.assertIn("plugin-registered", self._rules(root))

    def test_marketplace_extra_plugin(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_marketplace(root, [{"name": "p1", "description": "ok"},
                                    {"name": "ghost", "description": "ok"}])
            self.assertIn("plugin-registered", self._rules(root))

    def test_plugin_name_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_skill(root, "p1", "alpha-skill")
            make_plugin_json(root, "p1", name="wrong-name")
            make_marketplace(root, [{"name": "p1", "description": "ok"}])
            self.assertIn("plugin-name-matches-dir", self._rules(root))

    def test_plugin_desc_empty(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_skill(root, "p1", "alpha-skill")
            make_plugin_json(root, "p1", description="")
            make_marketplace(root, [{"name": "p1", "description": "ok"}])
            self.assertIn("plugin-desc-nonempty", self._rules(root))

    def test_skills_array_stale(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_marketplace(root, [{"name": "p1", "description": "ok",
                                     "skills": ["./plugins/p1/skills/ghost"]}])
            self.assertIn("marketplace-skill-stale", self._rules(root))

    def test_skills_array_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_skill(root, "p1", "beta-skill")  # second skill on disk
            make_marketplace(root, [{"name": "p1", "description": "ok",
                                     "skills": ["./plugins/p1/skills/alpha-skill"]}])
            self.assertIn("marketplace-skill-complete", self._rules(root))

    def test_no_skills_array_is_ok(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._base(t)
            make_skill(root, "p1", "beta-skill")
            make_marketplace(root, [{"name": "p1", "description": "ok"}])  # no skills[]
            rules = self._rules(root)
            self.assertNotIn("marketplace-skill-complete", rules)
            self.assertNotIn("marketplace-skill-stale", rules)


if __name__ == "__main__":
    unittest.main()
