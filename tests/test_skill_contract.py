import re
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "open-tax-check"


class SkillContractTests(unittest.TestCase):
    def test_public_docs_share_one_agent_install_source_without_store_claims(self):
        documents = {
            "skill": (SKILL / "SKILL.md").read_text(encoding="utf-8"),
            "host": (SKILL / "references" / "host-integrations.md").read_text(
                encoding="utf-8"
            ),
            "readme": (ROOT / "README.md").read_text(encoding="utf-8"),
        }
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertIn(
                    "https://github.com/MasterBenC/open-tax-check",
                    text,
                )
                self.assertIn("一句话安装指令", text)
                self.assertIn("尚未在其他智能体商店正式发布", text)

    def test_skill_host_reference_uses_case_guide_v2_contract(self):
        host = (SKILL / "references" / "host-integrations.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("case-guide/v1", host)
        for required in (
            "case-guide/v2",
            "scenario_questions",
            "active_scenarios",
            "document_groups",
            "接口状态码",
            "已执行且未见异常",
            "已执行需关注",
            "待补资料",
            "已收到待解析",
            "不适用",
            "未执行",
            "宿主只展示中文字段/文案，不直接展示代码",
        ):
            with self.subTest(required=required):
                self.assertIn(required, host)

    def test_case_guide_flow_reloads_dynamic_v2_after_case_start(self):
        documents = {
            "skill": (SKILL / "SKILL.md").read_text(encoding="utf-8"),
            "host": (SKILL / "references" / "host-integrations.md").read_text(
                encoding="utf-8"
            ),
            "readme": (ROOT / "README.md").read_text(encoding="utf-8"),
        }
        host = documents["host"]
        self.assertIn("第一次 `case-guide` 的 `document_groups` 可为空", host)
        self.assertIn("`case-start` 后必须再次调用 `case-guide`", host)
        self.assertLess(
            host.index("第一次 `case-guide`"),
            host.index("`case-start` 后必须再次调用 `case-guide`"),
        )
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertNotIn("原样展示 `active_scenarios`", text)
                self.assertNotIn("原样展示 `case-guide/v2`", text)
                self.assertIn("仅供宿主控制，不向客户直接展示", text)
                for field in (
                    "message_zh",
                    "question_zh",
                    "title_zh",
                    "items_zh",
                    "applicability_note_zh",
                    "privacy_zh",
                    "disclaimer_zh",
                ):
                    self.assertIn(field, text)

    def test_skill_and_readme_put_first_guide_before_profile_write(self):
        documents = (
            (SKILL / "SKILL.md").read_text(encoding="utf-8"),
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )
        for text in documents:
            with self.subTest(document=text[:40]):
                first_guide = "先运行第一次 `case-guide`"
                profile_write = "再把这五项信息写入"
                self.assertIn(first_guide, text)
                self.assertIn(profile_write, text)
                self.assertLess(text.index(first_guide), text.index(profile_write))

    def test_skill_and_readme_describe_dynamic_document_groups(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("三组资料", skill)
        self.assertNotIn("三组清单", readme)
        for text in (skill, readme):
            self.assertIn("按基础资料+已激活场景动态生成的 `document_groups`", text)

    def test_skill_describes_scenario_map_without_claiming_no_risk(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for required in (
            "经营场景",
            "收入与流转",
            "成本费用",
            "资产与不动产",
            "合同及特殊交易",
            "人员、股东及关联事项",
            "case-guide/v2",
            "待补资料、已收到待解析和未执行均不计入已执行或已通过",
            "AI 智能体不得替客户确认经营事实",
            "场景命中不等于税种适用",
            "外部清单中的未核验条目只能作为询问型候选",
            "BOOK-FS-REV-001",
            "FS-VAT-SALES-001",
            "BOOK-VAT-OUTPUT-001",
            "FS-CIT-REV-PROFIT-001",
            "BOOK-CIT-BE-001",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)

    def test_skill_contains_opening_model_and_handoff_contracts(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        models = (SKILL / "references" / "model-recommendations.md").read_text(encoding="utf-8")
        integrations = (SKILL / "references" / "host-integrations.md").read_text(encoding="utf-8")
        methodology = (SKILL / "references" / "methodology.md").read_text(encoding="utf-8")

        self.assertIn("税务健康检查 AI 智能体", skill)
        self.assertIn("资料不需要一次准备齐", skill)
        self.assertIn("case-guide", skill)
        self.assertIn("case-start", skill)
        for family in ("DeepSeek", "Qwen", "GLM", "混元", "豆包", "GPT", "Claude"):
            self.assertIn(family, models)
        self.assertIn("不绑定具体版本号", models)
        self.assertIn("结构化 JSON", models)
        self.assertNotIn("qwen3.8-max-preview", models)
        self.assertIn("tax-customer-handoff/v1", integrations)
        self.assertIn("不得自行改写", integrations)
        self.assertIn("模型选择不得改变", methodology)

    def test_customer_inference_pressure_contract_stays_fail_closed(self):
        fixture = json.loads((
            ROOT / "tests" / "fixtures" / "skill-pressure"
            / "customer-inference-pressure.json"
        ).read_text(encoding="utf-8"))

        self.assertEqual(fixture["schema_version"], "skill-pressure/v1")
        self.assertEqual(fixture["scenario"]["reverse_derived_calculation_count"], 8)
        self.assertEqual(fixture["scenario"]["missing_evidence_tax_count"], 12)
        self.assertEqual(fixture["scenario"]["unreviewed_candidate_count"], 2)
        self.assertEqual(fixture["baseline"]["violation_codes"], [
            "completion_overclaim",
            "invented_confirmation_claim",
            "unreviewed_candidate_in_client_conclusion",
            "unresolved_tax_closed_by_host",
        ])
        self.assertEqual(fixture["revised_output"], {
            "case_status": "waiting_for_customer_confirmation",
            "customer_title_zh": "第一轮账务初筛已完成",
            "confirmed_fact_count": 0,
            "calculation_claim": "trial_only",
            "candidate_client_status": "excluded",
            "next_action": "answer_customer_tasks",
        })
        serialized = json.dumps(fixture["revised_output"], ensure_ascii=False)
        for forbidden in ("检查已完成", "疑似漏缴", "不适用"):
            self.assertNotIn(forbidden, serialized)

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

        self.assertTrue(description.startswith("用于检查中国企业的税务资料"))
        self.assertIn("Use when ", description)
        self.assertIn("不出具鉴证、专业意见或申报结论", description)
        self.assertNotIn("including field normalization", description)
        self.assertIn("# OpenTaxCheck 税务健康检查", text)
        self.assertLessEqual(len(text.splitlines()), 165)

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
        self.assertIn("独立的《税务健康检查报告》", methodology)
        self.assertIn("不以税务专业人员复核、签字或盖章作为生成条件", methodology)

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
        commands = text.split("## 命令 × 状态 × 退出码 × 下一步", 1)[1].split(
            "## 失败处理",
            1,
        )[0]
        csv = text.split("## 5. CSV 序时账兼容流程", 1)[1].split(
            "## 6. 客户内部《税务健康检查报告》",
            1,
        )[0]

        self.assertIn(
            "| `inspect` | `needs_intake_review`（`inspect_processing_error`） | `4` |",
            commands,
        )
        self.assertIn(
            "| `run` | `blocked_processing_error`（`inspect_processing_error`） | `5` |",
            commands,
        )
        self.assertIn(
            "`blocked_processing_error` 与退出码 `5` 只属于 `run`，"
            "Excel `inspect` 的处理异常一律是 `needs_intake_review` + 退出码 `4`",
            commands,
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
        self.assertIn("旧 finding 交付", schema)
        self.assertIn("独立的《税务健康检查报告》", schema)
        self.assertIn("PDF、图片已提供可选的本地离线 OCR；联网企业数据和复杂版式理解尚未实现", text)

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
        cursor = text.index("## 3. Excel 工作簿盘点与选表")
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
        excel = text.split("## 3. Excel 工作簿盘点与选表", 1)[1].split(
            "## 压力请求的固定输出",
            1,
        )[0]
        commands = text.split("## 命令 × 状态 × 退出码 × 下一步", 1)[1].split(
            "## 失败处理",
            1,
        )[0]

        self.assertIn(
            "| `inspect` | `partial_scope_ready`"
            "（`await_phase_2b_excel_normalization`） | `0` |",
            commands,
        )
        self.assertIn("confirm-mapping", excel)
        self.assertIn("来源绑定映射经复核确认后", excel)
        self.assertNotIn("run_available_checks", excel + commands)

    def test_skill_gives_low_capability_agent_a_fixed_pressure_response_shape(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("## 压力请求的固定输出", text)
        self.assertIn("回答采用以下固定形状", text)
        pressure = text.split("## 压力请求的固定输出", 1)[1].split(
            "## 4. 映射确认、标准化与勾稽",
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
        for name in (
            "canonical-schema.md",
            "methodology.md",
            "rule-catalog.md",
            "kb-mount.md",
            "tax-packs.md",
            "excel-intake.md",
            "professional-review.md",
            "example-dialogue.md",
        ):
            link = f"[references/{name}](references/{name})"
            with self.subTest(reference=name):
                self.assertIn(link, text)

    def test_skill_documents_customer_self_check_without_professional_approval_gate(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        host = (SKILL / "references" / "host-integrations.md").read_text(
            encoding="utf-8"
        )

        for document in (text, host):
            self.assertIn("《税务健康检查报告》", document)
            self.assertIn("不以税务专业人员复核、签字或盖章作为生成条件", document)
        self.assertIn("--include-parameterized-packs", host)
        self.assertNotIn("当前 v0.1 仍只生成内部 Markdown 草稿", text)

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
            '  default_prompt: "请使用 $open-tax-check 检查中国企业税务资料：首次对话先运行 case-guide 收集五项基础档案并建案，再按 state.json 推进安全盘点、选表、数据质量与专业复核门槛，不要替客户确认经营事实。"\n'
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
            "references/kb-mount.md",
            "references/host-integrations.md",
            "references/model-recommendations.md",
            "references/tax-packs.md",
            "references/excel-intake.md",
            "references/professional-review.md",
            "references/example-dialogue.md",
            "references/ocr-and-structured-import.md",
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
            '#!/usr/bin/env python3\n'
            '"""Portable Agent Skill entry point for the installed OpenTaxCheck package."""\n'
            '\n'
            'import sys\n'
            '\n'
            'INSTALL_HINT = (\n'
            '    \'  python -m pip install "open-tax-check[excel,report,ocr]"\\n\'\n'
            '    "  python -m playwright install chromium\\n"\n'
            ')\n'
            '\n'
            'MODULE_HINTS = {\n'
            '    "open_tax_check": "尚未安装 OpenTaxCheck 引擎包 open_tax_check",\n'
            '    "openpyxl": "Excel 工作簿读写组件 openpyxl 未安装（可选依赖 excel）",\n'
            '    "xlrd": "旧版 .xls 读取组件 xlrd 未安装（可选依赖 excel）",\n'
            '    "defusedxml": "安全 XML 解析组件 defusedxml 未安装（可选依赖 excel）",\n'
            '    "playwright": "PDF 报告渲染组件 playwright 未安装（可选依赖 report）",\n'
            '    "pypdf": "PDF 解析组件 pypdf 未安装（可选依赖 report / ocr）",\n'
            '    "pypdfium2": "PDF 渲染组件 pypdfium2 未安装（可选依赖 ocr）",\n'
            '    "PIL": "图像处理组件 Pillow（模块名 PIL）未安装（可选依赖 ocr）",\n'
            '    "paddleocr": "本地离线 OCR 组件 paddleocr 未安装（可选依赖 ocr）",\n'
            '    "paddle": "本地离线 OCR 推理后端 paddlepaddle（模块名 paddle）未安装（可选依赖 ocr）",\n'
            '}\n'
            '\n'
            'BROWSER_HINTS = ("Executable doesn\'t exist", "playwright install")\n'
            '\n'
            '\n'
            'def _root_module(error):\n'
            '    name = getattr(error, "name", None) or ""\n'
            '    return name.split(".")[0]\n'
            '\n'
            '\n'
            'def _dependency_message(error):\n'
            '    root = _root_module(error)\n'
            '    hint = MODULE_HINTS.get(root)\n'
            '    if hint is None:\n'
            '        hint = f"缺少 Python 模块 {root or getattr(error, \'name\', \'unknown\')}"\n'
            '    return f"{hint}。\\n修复步骤：\\n{INSTALL_HINT}"\n'
            '\n'
            '\n'
            'def _fail(message):\n'
            '    sys.stderr.write(f"{message}\\n")\n'
            '    return 2\n'
            '\n'
            '\n'
            'def main():\n'
            '    try:\n'
            '        from open_tax_check.cli import main as cli_main\n'
            '    except ModuleNotFoundError as error:\n'
            '        return _fail(f"无法启动 opentaxcheck：{_dependency_message(error)}")\n'
            '    try:\n'
            '        return cli_main()\n'
            '    except ModuleNotFoundError as error:\n'
            '        return _fail(f"运行中断：{_dependency_message(error)}")\n'
            '    except Exception as error:  # noqa: BLE001 - 只在缺少浏览器内核时改写提示\n'
            '        message = str(error)\n'
            '        if any(token in message for token in BROWSER_HINTS):\n'
            '            return _fail(\n'
            '                "PDF 报告渲染需要 Chromium 浏览器内核，但当前环境未安装。\\n"\n'
            '                "修复步骤：\\n  python -m playwright install chromium"\n'
            '            )\n'
            '        raise\n'
            '\n'
            '\n'
            'if __name__ == "__main__":\n'
            '    raise SystemExit(main())\n'
        )
        text = (SKILL / "scripts" / "opentaxcheck.py").read_text(encoding="utf-8")
        self.assertEqual(text, expected)


    def test_kb_mount_reference_grounds_personal_kb_cites(self):
        mount = (SKILL / "references" / "kb-mount.md").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue((SKILL / "references" / "kb-mount.md").is_file())
        for required in (
            "personal-kb",
            "02-税务",
            "needs_verification",
            "PERSONAL_KB_ROOT",
            "miit-qualification-kb",
        ):
            with self.subTest(required=required):
                self.assertIn(required, mount)
        self.assertIn("[references/kb-mount.md](references/kb-mount.md)", skill)


    def _load_skill_wrapper(self):
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "opentaxcheck_skill_wrapper", SKILL / "scripts" / "opentaxcheck.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        return module

    def _run_wrapper_with_cli(self, cli_main):
        import io
        import sys
        import types
        from unittest import mock

        module = self._load_skill_wrapper()
        package = types.ModuleType("open_tax_check")
        cli_module = types.ModuleType("open_tax_check.cli")
        setattr(cli_module, "main", cli_main)
        setattr(package, "cli", cli_module)
        stderr = io.StringIO()
        with mock.patch.dict(
            sys.modules, {"open_tax_check": package, "open_tax_check.cli": cli_module}
        ), mock.patch("sys.stderr", stderr):
            try:
                status = module.main()
            except BaseException as error:  # noqa: BLE001 - 透传场景由测试断言
                return error, stderr.getvalue()
        return status, stderr.getvalue()

    def test_skill_wrapper_explains_missing_engine_package(self):
        import builtins
        import io
        from unittest import mock

        module = self._load_skill_wrapper()
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "open_tax_check" or name.startswith("open_tax_check."):
                raise ModuleNotFoundError(
                    "No module named 'open_tax_check'", name="open_tax_check"
                )
            return real_import(name, *args, **kwargs)

        stderr = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=blocked_import), mock.patch(
            "sys.stderr", stderr
        ):
            status = module.main()

        self.assertEqual(status, 2)
        message = stderr.getvalue()
        self.assertIn("open_tax_check", message)
        self.assertIn('python -m pip install "open-tax-check[excel,report,ocr]"', message)
        self.assertIn("python -m playwright install chromium", message)

    def test_skill_wrapper_explains_missing_optional_dependency_at_runtime(self):
        def failing_main():
            raise ModuleNotFoundError("No module named 'openpyxl'", name="openpyxl")

        status, message = self._run_wrapper_with_cli(failing_main)

        self.assertEqual(status, 2)
        self.assertIn("openpyxl", message)
        self.assertIn('python -m pip install "open-tax-check[excel,report,ocr]"', message)

    def test_skill_wrapper_explains_missing_playwright_module(self):
        def failing_main():
            raise ModuleNotFoundError(
                "No module named 'playwright'", name="playwright"
            )

        status, message = self._run_wrapper_with_cli(failing_main)

        self.assertEqual(status, 2)
        self.assertIn("playwright", message)
        self.assertIn("python -m playwright install chromium", message)

    def test_skill_wrapper_explains_missing_chromium_binary(self):
        def failing_main():
            raise RuntimeError(
                "Executable doesn't exist at "
                "/root/.cache/ms-playwright/chromium-1200/headless_shell"
            )

        status, message = self._run_wrapper_with_cli(failing_main)

        self.assertEqual(status, 2)
        self.assertIn("Chromium", message)
        self.assertIn("python -m playwright install chromium", message)

    def test_skill_wrapper_passes_through_engine_status_codes(self):
        for expected_status in (0, 3, 4, 5):
            with self.subTest(status=expected_status):
                status, message = self._run_wrapper_with_cli(
                    lambda code=expected_status: code
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(message, "")

    def test_skill_wrapper_reraises_unrelated_runtime_errors(self):
        def failing_main():
            raise ValueError("unexpected engine failure")

        status, _ = self._run_wrapper_with_cli(failing_main)

        self.assertIsInstance(status, ValueError)


if __name__ == "__main__":
    unittest.main()
