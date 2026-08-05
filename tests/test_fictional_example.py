import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "examples/fictional-company/input/journal.csv"
EXPECTED_JOURNAL_ROWS = [
    ["凭证号", "记账日期", "科目编码", "科目名称", "业务说明", "借方发生额", "贷方发生额"],
    ["记-001", "2025-01-02", "1002", "银行存款", "确认销售收入", "1000000", "0"],
    ["记-001", "2025-01-02", "6001", "主营业务收入", "确认销售收入", "0", "1000000"],
    ["记-002", "2025-03-08", "6602", "管理费用-业务招待费", "客户接待", "20000", "0"],
    ["记-002", "2025-03-08", "1002", "银行存款", "客户接待", "0", "20000"],
    ["记-003", "2025-09-18", "6602", "管理费用-办公费", "购买中秋礼品及高档茶叶", "5000", "0"],
    ["记-003", "2025-09-18", "1002", "银行存款", "购买中秋礼品及高档茶叶", "0", "5000"],
]


class FictionalExampleTests(unittest.TestCase):
    def test_five_cross_check_acceptance_scenarios_have_bounded_machine_outcomes(self):
        from scripts.run_fictional_example import _prepare_complete_pipeline_case

        scenarios = (
            ("all_matched", {}, "matched"),
            ("inside_both_tolerances", {"vat_sales": "62500.05"}, "within_tolerance"),
            ("vat_sales_output_difference", {"vat_sales": "65000.00"}, "difference_found"),
            ("cit_revenue_profit_entertainment_difference", {"cit_revenue": "900000.00"}, "difference_found"),
            (
                "multiple_missing_sources",
                {"include_structured": False, "confirm_classification": False},
                "not_performed",
            ),
        )
        for name, options, expected_status in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                case_dir = Path(temp_dir) / "case"
                _prepare_complete_pipeline_case(case_dir, **options)
                payload = json.loads(
                    (case_dir / "work" / "tax-cross-checks.json").read_text(encoding="utf-8")
                )

                self.assertEqual(payload["schema_version"], "tax-cross-checks/v1")
                self.assertEqual(len(payload["results"]), 5)
                self.assertLessEqual(len(payload["questions"]), 3)
                if name == "all_matched":
                    self.assertTrue(all(item["status"] == "matched" for item in payload["results"]))
                elif name == "inside_both_tolerances":
                    self.assertEqual(payload["results"][1]["status"], expected_status)
                elif name == "vat_sales_output_difference":
                    self.assertEqual(payload["results"][1]["status"], expected_status)
                elif name == "cit_revenue_profit_entertainment_difference":
                    self.assertEqual(payload["results"][3]["status"], expected_status)
                else:
                    self.assertTrue(all(item["status"] == expected_status for item in payload["results"]))

    def test_mapping_payloads_use_preset_reviewer_mappings_not_proposals(self):
        """Changing proposal heuristics must not change the fictional review decision."""
        from scripts.run_fictional_example import _mapping_payloads

        proposals = {
            "proposals": [
                {
                    "role": "journal",
                    "source_id": "runtime-journal-source",
                    "sheet_id": "sheet-runtime-1",
                    "header_rows": [7],
                    "mapping": {"proposal-only": "wrong"},
                    "composites": {"proposal-only": {"wrong": "wrong"}},
                },
                {
                    "role": "trial_balance",
                    "source_id": "runtime-tb-source",
                    "sheet_id": "sheet-runtime-2",
                    "header_rows": [9],
                    "mapping": {"proposal-only": "wrong"},
                    "composites": {"proposal-only": {"wrong": "wrong"}},
                },
            ]
        }
        expected = {
            "journal": {
                "凭证号": "voucher_no",
                "记账日期": "date",
                "科目编码": "account_code",
                "科目名称": "account_name",
                "业务说明": "summary",
                "借方发生额": "debit",
                "贷方发生额": "credit",
            },
            "trial_balance": {
                "科目编码": "account_code",
                "科目名称": "account_name",
                "期初借方": "opening_debit",
                "期初贷方": "opening_credit",
                "本期借方": "period_debit",
                "本期贷方": "period_credit",
                "期末借方": "closing_debit",
                "期末贷方": "closing_credit",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            case = Path(temp_dir)
            (case / "work").mkdir()
            (case / "work/mapping_review.json").write_text(
                json.dumps(proposals), encoding="utf-8"
            )

            payloads = _mapping_payloads(case)

        self.assertEqual({item["role"] for item in payloads}, set(expected))
        for payload in payloads:
            self.assertEqual(payload["mapping"], expected[payload["role"]])
            self.assertEqual(payload["composites"], {})
            self.assertEqual(payload["exclusions"], {})
            self.assertEqual(payload["reviewer"], "host:fictional-example")
            self.assertTrue(payload["source_id"].startswith("runtime-"))

    def test_journal_matches_documented_balanced_fictional_case(self):
        with JOURNAL.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        self.assertEqual(rows, EXPECTED_JOURNAL_ROWS)
        vouchers = {}
        for row in rows[1:]:
            debit, credit = vouchers.setdefault(row[0], [0, 0])
            vouchers[row[0]] = [debit + int(row[5]), credit + int(row[6])]
        self.assertTrue(all(debit == credit for debit, credit in vouchers.values()))

    def test_example_readme_declares_complete_fictional_and_license_boundary(self):
        text = (ROOT / "examples/fictional-company/README.md").read_text(encoding="utf-8")

        self.assertIn("公司、交易、凭证、金额、人物和结论均为完全虚构", text)
        self.assertIn("不包含任何真实客户信息", text)
        self.assertIn("随本仓库按 Apache License 2.0 许可", text)
        self.assertIn("`--output` 必须指向不存在或空目录", text)

    def test_fictional_excel_and_csv_findings_match(self):
        """Removing Excel mapping confirmation or changing its input changes results."""
        from scripts.run_fictional_example import run_fictional_case

        csv_result = run_fictional_case(format="csv")
        excel_result = run_fictional_case(format="excel")

        self.assertEqual(csv_result.rule_amounts, excel_result.rule_amounts)
        self.assertIsNotNone(excel_result.cross_check_payload)
        self.assertIsNotNone(csv_result.cross_check_payload)
        self.assertEqual(
            excel_result.cross_check_payload["schema_version"],
            "tax-cross-checks/v1",
        )
        semantic = lambda payload: {
            "status": payload["status"],
            "results": [
                {
                    key: item[key]
                    for key in (
                        "check_id", "status", "ledger_amount", "comparison_amount",
                        "difference_amount", "difference_ratio", "reason_codes",
                    )
                }
                for item in payload["results"]
            ],
            "questions": [item["check_id"] for item in payload["questions"]],
            "audit_events": payload["audit_events"],
        }
        self.assertEqual(
            semantic(csv_result.cross_check_payload),
            semantic(excel_result.cross_check_payload),
        )
        self.assertNotEqual(
            csv_result.cross_check_payload["source_manifest_sha256"],
            excel_result.cross_check_payload["source_manifest_sha256"],
        )
        self.assertLessEqual(len(excel_result.cross_check_payload["questions"]), 3)
        self.assertEqual(csv_result.rule_amounts, {"CIT-BE-001": "15000.00"})
        self.assertEqual(excel_result.status, "needs_professional_review")
        self.assertTrue(excel_result.trial_balance_passed)
        self.assertTrue(excel_result.journal_to_trial_balance_passed)

    def test_complete_fictional_case_uses_only_the_generated_excel_journal(self):
        from scripts.run_fictional_example import run_fictional_case
        from open_tax_check.delivery import DeliveryError

        try:
            result = run_fictional_case(format="excel", complete=True)
        except DeliveryError as error:
            if str(error) == "pdf_renderer_unavailable":
                self.skipTest("Chromium is unavailable for the formal PDF delivery")
            raise

        self.assertEqual(result.status, "completed_with_limitations")
        self.assertEqual(result.rule_amounts, {"CIT-BE-001": "15000.00"})
        self.assertTrue(result.trial_balance_passed)
        self.assertTrue(result.journal_to_trial_balance_passed)
        self.assertEqual(result.journey_stages, (
            "welcome",
            "awaiting_documents",
            "check_in_progress",
            "awaiting_customer_input",
            "draft_report_ready",
        ))

    def test_complete_cli_builds_the_documented_case_and_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "complete-case"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_fictional_example.py"),
                    "--output",
                    str(case_dir),
                    "--complete",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            if proc.returncode != 0 and "pdf_renderer_unavailable" in proc.stderr:
                self.skipTest("Chromium is unavailable for the formal PDF delivery")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((case_dir / "input/journal.csv").exists())
            for filename in (
                "journal.xlsx",
                "trial-balance.xlsx",
                "fictional-tax-note.png",
                "fictional-digital.pdf",
                "balance-sheet.txt",
                "income-statement.txt",
                "cash-flow-statement.txt",
                "vat-return.txt",
                "surcharge-return.txt",
                "cit-return.txt",
                "iit-return.txt",
            ):
                self.assertTrue((case_dir / "input" / filename).is_file(), filename)
            from scripts.run_fictional_example import COMPLETE_VAT_FILENAMES
            for filename in COMPLETE_VAT_FILENAMES:
                self.assertTrue((case_dir / "input" / filename).is_file(), filename)

            structured = case_dir / "work/structured-documents"
            self.assertEqual(
                len(list(structured.glob("*.fs.json"))),
                3,
            )
            self.assertEqual(len(list(structured.glob("*.tr.json"))), 19)
            self.assertTrue((case_dir / "work/structured-document-index.json").is_file())
            structured_index = json.loads(
                (case_dir / "work/structured-document-index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                len({item["source_id"] for item in structured_index["documents"]}),
                22,
            )

            private_snapshot = json.loads(
                (case_dir / "work/tax-health-snapshot.private.json").read_text(
                    encoding="utf-8"
                )
            )
            cross_checks = json.loads(
                (case_dir / "work" / "tax-cross-checks.json").read_text(encoding="utf-8")
            )
            self.assertTrue(all(item["status"] == "matched" for item in cross_checks["results"]))
            classification = json.loads(
                (case_dir / "reviews" / "account_classification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(classification["reviewer"], "human:Chen Li")
            event_types = {
                event["event_type"] for event in private_snapshot["audit_events"]
            }
            self.assertIn("confirmation_conflict", event_types)
            self.assertIn("confirmation_reaffirmed", event_types)
            self.assertEqual(
                private_snapshot["limitations"][0]["code"],
                "special_tax_path_not_covered",
            )
            facts = {
                item["key"]: item["value"]
                for item in private_snapshot["request"]["facts"]
            }
            self.assertIs(facts["activity.has_vat_taxable_transaction"], True)
            self.assertIs(facts["entity.has_enterprise_income_tax_scope"], True)
            self.assertEqual(facts["vat.taxable_sales"], "1000000.00")
            calculated = {
                item["tax_id"]: item["calculation"]
                for item in private_snapshot["results"]
                if item["calculation"] is not None
            }
            self.assertIn("vat", calculated)
            self.assertIn("enterprise_income_tax", calculated)
            self.assertGreater(float(calculated["vat"]["amount"]), 0)
            self.assertGreater(
                float(calculated["enterprise_income_tax"]["amount"]), 0
            )
            tasks = json.loads(
                (case_dir / "work/customer-tasks.json").read_text(encoding="utf-8")
            )
            self.assertEqual(tasks["status"], "completed_with_limitations")
            self.assertEqual(tasks["tasks"], [])
            self.assertFalse((case_dir / "work/limitations.json").exists())
            journey = json.loads(
                (case_dir / "work/customer-journey.json").read_text(encoding="utf-8")
            )
            self.assertEqual(journey["schema_version"], "customer-journey/v1")
            self.assertEqual(journey["stages"], [
                "welcome",
                "awaiting_documents",
                "check_in_progress",
                "awaiting_customer_input",
                "draft_report_ready",
            ])
            self.assertNotIn("welcome", journey["stages"][1:])
            self.assertEqual(len(journey["profile_digest"]), 64)

            pointer = json.loads(
                (case_dir / "work/delivery.json").read_text(encoding="utf-8")
            )
            manifest_path = case_dir / pointer["manifest_path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact_kinds = {item["kind"] for item in manifest["artifacts"]}
            self.assertTrue(
                {
                    "html_report",
                    "pdf_report",
                    "workpaper",
                    "machine_snapshot",
                    "evidence_bundle",
                }.issubset(artifact_kinds)
            )
            revision_dir = manifest_path.parent
            for item in manifest["artifacts"]:
                artifact_path = revision_dir / item["relative_path"]
                self.assertTrue(artifact_path.is_file())
                self.assertEqual(
                    hashlib.sha256(artifact_path.read_bytes()).hexdigest(), item["sha256"]
                )
            with ZipFile(revision_dir / "evidence-bundle.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn(
                    "case-audit/structured-document-index.json", archive.namelist()
                )
                bundle_manifest = json.loads(archive.read("bundle-manifest.json"))
                self.assertEqual(
                    bundle_manifest["limitations"], private_snapshot["limitations"]
                )

            machine_snapshot = json.loads(
                (revision_dir / "tax-health-snapshot.json").read_text(encoding="utf-8")
            )
            self.assertEqual(machine_snapshot["limitations"], private_snapshot["limitations"])
            report = (revision_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn(private_snapshot["limitations"][0]["message_zh"], report)
            pdf_path = revision_dir / "税务健康检查报告.pdf"
            self.assertTrue(pdf_path.is_file())
            from pypdf import PdfReader
            pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
            self.assertIn("税务健康检查报告", pdf_text)
            self.assertIn("虚构科技有限公司", pdf_text)
            from openpyxl import load_workbook
            workbook = load_workbook(revision_dir / "税务健康检查工作底稿.xlsx", read_only=True)
            try:
                overview_values = {
                    cell.value
                    for row in workbook["项目概览"].iter_rows()
                    for cell in row
                }
                self.assertIn(private_snapshot["limitations"][0]["message_zh"], overview_values)
            finally:
                workbook.close()

            payload = json.loads(proc.stdout)
            self.assertEqual(payload["delivery_status"], manifest["status"])
            self.assertEqual(payload["delivery_revision"], manifest["revision"])
            self.assertEqual(payload["status"], "completed_with_limitations")
            handoff = json.loads(
                (revision_dir / "税务健康检查交付说明.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {
                    manifest["revision"],
                    machine_snapshot["revision"],
                    handoff["revision"],
                    payload["delivery_revision"],
                },
                {manifest["revision"]},
            )

    def test_example_generates_and_runs_fictional_xlsx_with_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_before = JOURNAL.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            generated = Path(temp_dir) / "input/journal.xlsx"
            self.assertTrue(generated.is_file())
            self.assertTrue((Path(temp_dir) / "input/trial-balance.xlsx").is_file())
            for filename in (
                "balance-sheet.txt",
                "income-statement.txt",
                "vat-return.txt",
                "cit-return.txt",
            ):
                self.assertTrue((Path(temp_dir) / "input" / filename).is_file())
            state = json.loads(
                (Path(temp_dir) / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "needs_professional_review")
            self.assertTrue(
                (Path(temp_dir) / "work/workbook_inventory.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/sheet_selection.json").is_file()
            )
            self.assertTrue(
                (Path(temp_dir) / "work/coverage_matrix.json").is_file()
            )
            coverage = json.loads(
                (Path(temp_dir) / "work/coverage_matrix.json").read_text(
                    encoding="utf-8"
                )
            )["items"]
            by_procedure = {item["procedure"]: item for item in coverage}
            self.assertEqual(
                by_procedure["financial_statement_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertEqual(
                by_procedure["vat_return_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertEqual(
                by_procedure["cit_return_reconciliation"]["status"],
                "present_unparsed",
            )
            self.assertTrue((Path(temp_dir) / "work/normalized_journal.jsonl").is_file())
            self.assertTrue((Path(temp_dir) / "work/normalized_trial_balance.jsonl").is_file())
            self.assertTrue((Path(temp_dir) / "work/reconciliation.json").is_file())
            self.assertTrue((Path(temp_dir) / "output/findings.json").is_file())
            self.assertTrue((Path(temp_dir) / "output/report.md").is_file())
            confirmations = json.loads(
                (Path(temp_dir) / "reviews/mapping_review.json").read_text(
                    encoding="utf-8"
                )
            )["confirmations"]
            journal = next(item for item in confirmations if item["role"] == "journal")
            self.assertEqual(
                journal["mapping"]["业务说明"],
                "summary",
            )
            self.assertEqual(journal["composites"], {})
            self.assertEqual(journal["exclusions"], {})
            self.assertEqual(journal["reviewer"], "host:fictional-example")
            self.assertEqual(JOURNAL.read_bytes(), source_before)

    def test_example_emits_utf8_when_inherited_encoding_cannot_encode_chinese(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp1252"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "case"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_fictional_example.py"),
                    "--output",
                    str(output_dir),
                ],
                capture_output=True,
                env=env,
            )

        self.assertEqual(
            proc.returncode,
            0,
            proc.stderr.decode("utf-8", errors="replace"),
        )
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(payload["entity_name"], "虚构科技有限公司")

    def test_example_refuses_nonempty_output_without_changing_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            state = output_dir / "state.json"
            journal = output_dir / "input/journal.xlsx"
            marker = output_dir / "keep.me"
            journal.parent.mkdir()
            state.write_text("sentinel state", encoding="utf-8")
            journal.write_text("sentinel journal", encoding="utf-8")
            marker.write_text("sentinel marker", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(proc.returncode, 2)
            self.assertIn("output directory must not exist or must be empty", proc.stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), "sentinel state")
            self.assertEqual(journal.read_text(encoding="utf-8"), "sentinel journal")
            self.assertEqual(marker.read_text(encoding="utf-8"), "sentinel marker")
            self.assertFalse((output_dir / "output").exists())

    def test_root_readme_runs_the_example_without_claiming_it_is_missing(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = text.split("## 运行虚构示例", 1)[1].split("## 输出", 1)[0]

        self.assertIn(r".\.venv\Scripts\python.exe scripts\run_fictional_example.py", section)
        self.assertIn("python scripts/run_fictional_example.py", section)
        self.assertIn("coverage_matrix.json", section)
        self.assertIn("normalized_trial_balance.jsonl", section)
        self.assertIn("reconciliation.json", section)
        self.assertIn("needs_professional_review", section)
        self.assertIn("`--output` 必须指向不存在或空目录", section)
        self.assertNotIn("尚未提供", section)


if __name__ == "__main__":
    unittest.main()
