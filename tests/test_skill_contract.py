import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "open-tax-check"


class SkillContractTests(unittest.TestCase):
    def test_skill_declares_required_boundaries(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: open-tax-check", text)
        self.assertIn("needs_mapping_review", text)
        self.assertIn("blocked_data_quality", text)
        self.assertIn("professional review", text.lower())
        self.assertIn("Never turn a keyword or semantic match into a tax conclusion", text)
        self.assertIn("Its status remains `candidate`", text)
        self.assertIn("Preserve source lineage", text)
        self.assertIn("Never call code output “100% accurate.”", text)

    def test_skill_uses_only_portable_commands_and_ai_modes(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--ai-mode off", text)
        self.assertIn("`assist`", text)
        self.assertIn("`deep`", text)
        self.assertNotIn("enhanced", text)
        self.assertNotIn("<skill-dir>/scripts/taxcheck.py", text)
        self.assertNotIn("$tax-health-check", text)

    def test_skill_links_each_reference_directly(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for name in ("canonical-schema.md", "methodology.md", "rule-catalog.md"):
            link = f"[references/{name}](references/{name})"
            with self.subTest(reference=name):
                self.assertIn(link, text)

    def test_openai_metadata_matches_interface_contract(self):
        expected = (
            'interface:\n'
            '  display_name: "OpenTaxCheck｜开源税务健康检查"\n'
            '  short_description: "用确定性规则、AI税务助理和专业复核执行企业税务健康检查"\n'
            '  default_prompt: "Use $open-tax-check to run a resumable Chinese '
            'enterprise tax health check and stop at every mapping, data-quality, '
            'cloud-authorization, and professional-review gate."\n'
        )
        actual = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_skill_contains_only_required_files(self):
        expected = {
            "SKILL.md",
            "agents/openai.yaml",
            "references/canonical-schema.md",
            "references/methodology.md",
            "references/rule-catalog.md",
            "scripts/opentaxcheck.py",
        }
        actual = {
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)

    def test_skill_wrapper_is_exact_minimal_adapter(self):
        expected = (
            "#!/usr/bin/env python3\n"
            '"""Portable Agent Skill entry point for the installed OpenTaxCheck package."""\n'
            "\n"
            "from open_tax_check.cli import main\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )
        text = (SKILL / "scripts" / "opentaxcheck.py").read_text(encoding="utf-8")
        self.assertEqual(text, expected)


if __name__ == "__main__":
    unittest.main()
