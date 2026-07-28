import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "open-tax-check"


class SkillContractTests(unittest.TestCase):
    def test_seven_host_integration_contract_is_explicit(self):
        text = (SKILL / "references" / "host-integrations.md").read_text(encoding="utf-8")
        for host in ("Codex", "Hermes", "WorkBuddy", "扣子", "Qoder", "Claude Code", "OpenClaw"):
            self.assertIn(host, text)
        for route in ("Python / CLI / JSON 核心", "Agent Skills", "MCP / HTTP"):
            self.assertIn(route, text)
        self.assertIn("需按当前官方文档核验", text)
        self.assertIn("远程传输前必须披露", text)
        self.assertIn("租户隔离", text)
        self.assertIn("question_zh", text)
        self.assertIn("无风险", text)
        self.assertTrue((ROOT / "tests" / "fixtures" / "tax-health-host-contract.json").is_file())

    def test_skill_documents_confirmed_excel_mapping_and_reconciliation_boundaries(self):
        """Removing a 2B activation gate or assurance boundary must fail the contract."""
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        schema = (SKILL / "references" / "canonical-schema.md").read_text(
            encoding="utf-8"
        )
        methodology = (SKILL / "references" / "methodology.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("confirm-mapping", skill)
        self.assertIn("normalized_trial_balance.jsonl", skill)
        self.assertIn("reconciliation.json", skill)
        self.assertIn("勾稽通过不表示不存在税务风险", skill)
        self.assertIn("present_unparsed", schema)
        self.assertIn("完整基线健康检查尚未完成", schema)
        self.assertIn("勾稽通过不表示不存在税务风险", methodology)

    def test_skill_frontmatter_is_trigger_only_and_chinese_body_is_concise(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        description = next(
            line.removeprefix("description: ").strip()
            for line in frontmatter.splitlines()
            if line.startswith("description: ")
        )

        self.assertTrue(description.startswith("Use when "))
        self.assertNotIn("including field normalization", description)
        self.assertIn("# OpenTaxCheck 税务健康检查", text)
        self.assertLessEqual(len(text.splitlines()), 160)

    def test_skill_declares_required_boundaries(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: open-tax-check", text)
        self.assertIn("needs_mapping_review", text)
        self.assertIn("blocked_data_quality", text)
        self.assertIn("professional review", text.lower())
        self.assertIn("不得把关键词或语义命中变成税务结论", text)
        self.assertIn("状态保持 `candidate`", text)
        self.assertIn("来源血缘", text)
        self.assertIn("永远不要称代码输出“100% 准确”", text)

    def test_skill_declares_complete_professional_review_gate(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        methodology = (SKILL / "references" / "methodology.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("needs_professional_review", text)
        self.assertIn("不得把未复核结果放入客户结论", text)
        self.assertIn("才具备进入客户报告的资格", text)
        self.assertIn("`report_status`", methodology)
        self.assertIn("恰为 `approved`", methodology)
        self.assertIn("不生成正式客户报告", methodology)

    def test_skill_requires_unambiguous_csv_selection_and_explains_run_history(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("只能放一份普通、非符号链接的 CSV 序时账", text)
        self.assertIn("provide_exactly_one_csv_journal", text)
        self.assertIn("`runs/run-XXXX/manifest.json`", text)
        self.assertIn("`blocked_processing_error`", text)
        self.assertIn("`inspect_processing_error`", text)
        self.assertIn("退出码 `0`", text)
        self.assertIn("`5` 表示已安全记录处理错误", text)

    def test_skill_distinguishes_excel_inspect_from_csv_run_processing_errors(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        excel = text.split("## 2. Excel / 工作簿走 Phase 2A", 1)[1].split(
            "## 压力请求的固定输出",
            1,
        )[0]
        csv = text.split("## 3. CSV 走兼容流程", 1)[1].split(
            "## 4. 专业复核与交付边界",
            1,
        )[0]

        self.assertIn(
            "| `needs_intake_review`（`next_action=inspect_processing_error`） | "
            "检查本次 `inspect` 失败；退出码为 `4`，不复用旧输出。 |",
            excel,
        )
        self.assertNotIn("| `blocked_processing_error` |", excel)
        self.assertIn(
            "`blocked_processing_error` 与退出码 `5` 只属于下文 CSV `run_case`",
            excel,
        )
        self.assertIn(
            "- `blocked_processing_error` / `inspect_processing_error`",
            csv,
        )
        self.assertIn("`5` 表示已安全记录处理错误", csv)

    def test_skill_requires_explicit_excel_sheet_and_hidden_row_review(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        schema = (SKILL / "references" / "canonical-schema.md").read_text(
            encoding="utf-8"
        )
        methodology = (SKILL / "references" / "methodology.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("needs_sheet_selection", text)
        self.assertIn("select-sheet", text)
        self.assertIn("不得自动选择隐藏工作表", text)
        self.assertIn("必须是 `include` 或 `exclude`", text)
        self.assertIn("hidden_row_exclusion_unavailable", text)
        self.assertIn("`exclude` 不会实际过滤", text)
        self.assertIn("复核后改为 `include`，或停止处理", text)
        self.assertIn("`.xlsx`、`.xlsm`、`.xls`", text)
        self.assertIn("资料覆盖矩阵", methodology)
        self.assertIn("Phase 2A 自身止于安全盘点", schema)
        self.assertIn("当前 Phase 2B", schema)
        self.assertIn("不生成正式客户报告", schema)
        self.assertIn("PDF、图片 OCR 和联网企业数据尚未实现", text)

    def test_skill_application_contract_requires_confirmed_mapping_for_phase_2b(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        expected_sequence = (
            "读取 `state.json`",
            "`needs_sheet_selection`",
            "`select-sheet`",
            "`--hidden-rows include` 或 `--hidden-rows exclude`",
            "再次运行 `inspect`",
            "`partial_scope_ready`",
        )
        cursor = text.index("## 2. Excel / 工作簿走 Phase 2A")
        positions = []
        for item in expected_sequence:
            position = text.find(item, cursor)
            positions.append(position)
            cursor = position + len(item)
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))
        self.assertIn("confirm-mapping", text)
        self.assertIn("映射提案在 `confirm-mapping` 前一律不生效", text)
        self.assertNotIn("# STOP：`partial_scope_ready`", text)

    def test_skill_treats_excel_next_action_as_mapping_confirmation_gate(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        excel = text.split("## 2. Excel / 工作簿走 Phase 2A", 1)[1].split(
            "## 3. CSV 走兼容流程",
            1,
        )[0]

        self.assertIn(
            "| `partial_scope_ready`"
            "（`next_action=await_phase_2b_excel_normalization`） |",
            excel,
        )
        self.assertIn("confirm-mapping", excel)
        self.assertIn("来源绑定映射经复核确认后", excel)
        self.assertNotIn("run_available_checks", excel)

    def test_skill_gives_low_capability_agent_a_fixed_pressure_response_shape(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("## 压力请求的固定输出", text)
        self.assertIn("回答采用以下固定形状", text)
        pressure = text.split("## 压力请求的固定输出", 1)[1].split(
            "## 3. CSV 走兼容流程",
            1,
        )[0]
        self.assertIn("opentaxcheck.py inspect --case <case-dir>", pressure)
        self.assertIn("opentaxcheck.py select-sheet", pressure)
        self.assertIn("confirm-mapping", pressure)
        self.assertIn("映射提案", pressure)

    def test_skill_uses_only_portable_commands_and_ai_modes(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        expected_mode_declaration = (
            "- AI 模式只允许 `off`、`assist`、`deep`；无可用 AI 时使用 `off`。"
        )
        mode_declarations = [
            line for line in text.splitlines() if line.startswith("- AI 模式")
        ]
        self.assertEqual(mode_declarations, [expected_mode_declaration])

        command_targets = re.findall(
            r"python <skill-dir>/scripts/([^\s]+)", text
        )
        self.assertEqual(set(command_targets), {"opentaxcheck.py"})
        self.assertEqual(len(command_targets), 9)

        cli_modes = set(re.findall(r"--ai-mode ([a-z][a-z0-9-]*)", text))
        documented_modes = cli_modes | set(
            re.findall(r"`(off|assist|deep)`", expected_mode_declaration)
        )
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

    def test_canonical_schema_documents_conflicting_alias_gate(self):
        text = (SKILL / "references" / "canonical-schema.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("记录到 `conflicts`", text)
        self.assertIn("阻止自动计算", text)

    def test_skill_documents_deterministic_date_and_period_quality_contract(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        schema = (SKILL / "references" / "canonical-schema.md").read_text(
            encoding="utf-8"
        )
        methodology = (SKILL / "references" / "methodology.md").read_text(
            encoding="utf-8"
        )
        catalog = (SKILL / "references" / "rule-catalog.md").read_text(
            encoding="utf-8"
        )

        for date_form in ("`YYYY-MM-DD`", "`YYYY/M/D`", "`YYYY.M.D`"):
            self.assertIn(date_form, schema)
        self.assertIn("`period`（`YYYY-MM`）", schema)
        self.assertIn("`tax_year`（`YYYY`）", schema)
        self.assertIn("按每个会计期间内的凭证号核对借贷", methodology)
        self.assertIn("缺失或无效日期会阻止硬规则", methodology)
        self.assertIn("按税年分别计算", catalog)
        self.assertIn("resolve_data_quality_findings", skill)

    def test_skill_documents_csv_input_and_numeric_integrity_gates(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        schema = (SKILL / "references" / "canonical-schema.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("`.csv` 后缀不区分大小写", skill)
        self.assertIn("不支持文件会阻断处理", skill)
        self.assertIn("多余/缺失单元格", skill)
        self.assertIn("ASCII 数字", schema)
        self.assertIn("合法三位千分位", schema)
        self.assertIn("科学计数法", schema)
        self.assertIn("18 位整数", schema)
        self.assertIn("2 位小数", schema)
        self.assertIn("20 位整数", schema)
        self.assertIn("绝对值之和", schema)
        self.assertIn("ROUND_HALF_UP", schema)
        self.assertIn("申报和法定列示规则", schema)

    def test_skill_documents_input_entry_and_entertainment_basis_boundaries(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        catalog = (SKILL / "references" / "rule-catalog.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("普通、非符号链接的 CSV", skill)
        self.assertIn("目录、符号链接", skill)
        self.assertIn("当前规则版本：`2026.2`", catalog)
        self.assertIn("主营业务和其他业务收入", catalog)
        self.assertIn("视同销售收入", catalog)
        self.assertIn("基数完整性仍未确认", catalog)

    def test_openai_metadata_matches_interface_contract(self):
        expected = (
            'interface:\n'
            '  display_name: "OpenTaxCheck｜开源税务健康检查"\n'
            '  short_description: "安全盘点Excel与CSV资料并守住企业税务检查专业复核边界"\n'
            '  default_prompt: "请使用 $open-tax-check 安全盘点中国企业税务检查案件，'
            '并严格停在选表、数据质量与专业复核门槛。"\n'
        )
        actual = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)
        short_description = re.search(
            r'(?m)^  short_description: "([^"]+)"$', actual
        ).group(1)
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)
        for line in actual.splitlines()[1:]:
            self.assertRegex(line, r'^  [a-z_]+: ".+"$')

    def test_skill_public_files_remain_below_250_kib(self):
        total = sum(
            path.stat().st_size for path in SKILL.rglob("*") if path.is_file()
        )
        self.assertLess(total, 250 * 1024, total)

    def test_skill_contains_only_required_files(self):
        expected = {
            "SKILL.md",
            "agents/openai.yaml",
            "references/canonical-schema.md",
            "references/methodology.md",
            "references/rule-catalog.md",
            "references/host-integrations.md",
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
