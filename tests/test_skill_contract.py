import re
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

    def test_skill_declares_complete_professional_review_gate(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("needs_professional_review", text)
        self.assertIn(
            "Never put an unreviewed finding into a client-facing conclusion.", text
        )
        self.assertIn(
            "Only reviewed findings with an approved report status may enter a "
            "client-facing report.",
            text,
        )

    def test_skill_requires_unambiguous_csv_selection_and_explains_run_history(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Put exactly one journal CSV", text)
        self.assertIn("provide_exactly_one_csv_journal", text)
        self.assertIn("`runs/run-XXXX/manifest.json`", text)

    def test_skill_uses_only_portable_commands_and_ai_modes(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        expected_mode_declaration = (
            "- Supported AI modes are exactly `off`, `assist`, and `deep`; use "
            "`off` when AI is unavailable."
        )
        mode_declarations = [
            line for line in text.splitlines() if "ai mode" in line.casefold()
        ]
        self.assertEqual(mode_declarations, [expected_mode_declaration])

        command_targets = re.findall(
            r"python <skill-dir>/scripts/([^\s]+)", text
        )
        self.assertEqual(set(command_targets), {"opentaxcheck.py"})
        self.assertEqual(len(command_targets), 2)

        cli_modes = set(re.findall(r"--ai-mode ([a-z][a-z0-9-]*)", text))
        prose_modes = re.search(
            r"Use `([a-z][a-z0-9-]*)` or `([a-z][a-z0-9-]*)` only if", text
        )
        self.assertIsNotNone(prose_modes)
        documented_modes = cli_modes | set(prose_modes.groups())
        self.assertEqual(documented_modes, {"off", "assist", "deep"})

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
