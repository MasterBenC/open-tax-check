import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check import pipeline
from open_tax_check.case import init_case
from open_tax_check.pipeline import inspect_case, run_case
from open_tax_check.reviews import confirm_mapping, confirm_sheet_selection


JOURNAL_HEADERS = (
    "voucher",
    "date",
    "account-code",
    "account-name",
    "description",
    "debit",
    "credit",
)
TRIAL_BALANCE_HEADERS = (
    "account-code",
    "account-name",
    "opening-debit",
    "opening-credit",
    "period-debit",
    "period-credit",
    "closing-debit",
    "closing-credit",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_workbook(path: Path, title: str, rows, *, hidden_row: int | None = None):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    if hidden_row is not None:
        sheet.row_dimensions[hidden_row].hidden = True
    workbook.save(path)
    workbook.close()


class ExcelPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def prepared_excel_case(
        self,
        *,
        confirm: bool,
        reconciles: bool = True,
        hidden_journal_row: int | None = None,
    ) -> Path:
        case = self.root / "case"
        init_case(case, "Fictional Company", "2025-01-01", "2025-12-31")
        write_workbook(
            case / "input" / "journal.xlsx",
            "Journal",
            (
                JOURNAL_HEADERS,
                ("J-001", "2025-01-02", "1002", "Bank", "Revenue", 1_000_000, 0),
                ("J-001", "2025-01-02", "6001", "主营业务收入", "Revenue", 0, 1_000_000),
                ("J-002", "2025-03-08", "6602", "管理费用-业务招待费", "Hosting", 20_000, 0),
                ("J-002", "2025-03-08", "1002", "Bank", "Hosting", 0, 20_000),
            ),
            hidden_row=hidden_journal_row,
        )
        bank_debit = 1_000_000 if reconciles else 999_999
        write_workbook(
            case / "input" / "trial-balance.xlsx",
            "Trial Balance",
            (
                TRIAL_BALANCE_HEADERS,
                ("1002", "Bank", 0, 0, bank_debit, 20_000, 980_000, 0),
                ("6001", "主营业务收入", 0, 0, 0, 1_000_000, 0, 1_000_000),
                ("6602", "管理费用-业务招待费", 0, 0, 20_000, 0, 20_000, 0),
            ),
        )

        inspect_code, _ = inspect_case(case)
        self.assertIn(inspect_code, (0, 4))
        inventory = load_json(case / "work" / "workbook_inventory.json")
        for role, filename in (
            ("journal", "journal.xlsx"),
            ("trial_balance", "trial-balance.xlsx"),
        ):
            source = next(
                item for item in inventory["workbooks"] if item["filename"] == filename
            )
            confirm_sheet_selection(
                case,
                role=role,
                source_id=source["source_id"],
                sheet_id="sheet-0001",
                header_rows=(1,),
                hidden_rows="exclude",
                reviewer="host:test",
            )

        if confirm:
            inventory_by_filename = {
                item["filename"]: item for item in inventory["workbooks"]
            }
            confirm_mapping(
                case,
                {
                    "role": "journal",
                    "source_id": inventory_by_filename["journal.xlsx"]["source_id"],
                    "sheet_id": "sheet-0001",
                    "header_rows": [1],
                    "mapping": {
                        "voucher": "voucher_no",
                        "date": "date",
                        "account-code": "account_code",
                        "account-name": "account_name",
                        "description": "summary",
                        "debit": "debit",
                        "credit": "credit",
                    },
                    "exclusions": {},
                    "reviewer": "host:test",
                },
            )
            confirm_mapping(
                case,
                {
                    "role": "trial_balance",
                    "source_id": inventory_by_filename["trial-balance.xlsx"]["source_id"],
                    "sheet_id": "sheet-0001",
                    "header_rows": [1],
                    "mapping": {
                        "account-code": "account_code",
                        "account-name": "account_name",
                        "opening-debit": "opening_debit",
                        "opening-credit": "opening_credit",
                        "period-debit": "period_debit",
                        "period-credit": "period_credit",
                        "closing-debit": "closing_debit",
                        "closing-credit": "closing_credit",
                    },
                    "exclusions": {},
                    "reviewer": "host:test",
                },
            )
        return case

    def business_artifact_bytes(self, case: Path) -> dict[str, bytes]:
        names = (
            "work/normalized_journal.jsonl",
            "work/normalized_trial_balance.jsonl",
            "work/reconciliation.json",
            "work/coverage_matrix.json",
            "output/findings.json",
            "output/report.md",
        )
        return {name: (case / name).read_bytes() for name in names}

    def test_excel_run_reconciles_and_reaches_professional_review(self):
        case = self.prepared_excel_case(confirm=True, reconciles=True)

        code, result = run_case(case, "off")

        reconciliation = load_json(case / "work" / "reconciliation.json")
        findings = load_json(case / "output" / "findings.json")
        self.assertEqual(code, 0)
        self.assertTrue(reconciliation["journal_to_trial_balance"]["passed"])
        self.assertTrue(reconciliation["trial_balance"]["passed"])
        self.assertEqual(result["status"], "needs_professional_review")
        entertainment = next(item for item in findings if item["rule_id"] == "CIT-BE-001")
        self.assertEqual(entertainment["tax_adjustment"], "15000.00")

    def test_unconfirmed_mapping_stops_before_normalized_artifacts(self):
        case = self.prepared_excel_case(confirm=False)

        code, result = run_case(case, "off")

        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "needs_mapping_review")
        self.assertTrue((case / "work" / "mapping_review.json").is_file())
        self.assertFalse((case / "work" / "normalized_journal.jsonl").exists())
        self.assertFalse((case / "work" / "normalized_trial_balance.jsonl").exists())

    def test_trial_balance_failure_blocks_only_dependent_procedure(self):
        case = self.prepared_excel_case(confirm=True, reconciles=False)

        code, result = run_case(case, "off")

        reconciliation = load_json(case / "work" / "reconciliation.json")
        coverage = load_json(case / "work" / "coverage_matrix.json")["items"]
        coverage_by_procedure = {item["procedure"]: item for item in coverage}
        findings = load_json(case / "output" / "findings.json")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "needs_professional_review")
        self.assertFalse(reconciliation["journal_to_trial_balance"]["passed"])
        self.assertEqual(coverage_by_procedure["journal_tb_reconciliation"]["status"], "blocked")
        self.assertEqual(coverage_by_procedure["journal_hard_rules"]["status"], "performed")
        self.assertIn("CIT-BE-001", [item["rule_id"] for item in findings])

    def test_unchanged_rerun_has_identical_business_artifacts(self):
        case = self.prepared_excel_case(confirm=True, reconciles=True)

        run_case(case, "off")
        first = self.business_artifact_bytes(case)
        run_case(case, "off")
        second = self.business_artifact_bytes(case)

        self.assertEqual(first, second)

    def test_changed_journal_invalidates_only_its_dependent_decisions(self):
        case = self.prepared_excel_case(confirm=True, reconciles=True)
        first_code, _ = run_case(case, "off")
        self.assertEqual(first_code, 0)
        journal = case / "input" / "journal.xlsx"
        from openpyxl import load_workbook

        workbook = load_workbook(journal)
        workbook["Journal"]["H1"] = "new-ignored-column"
        workbook.save(journal)
        workbook.close()

        code, result = run_case(case, "off")

        selection = load_json(case / "work" / "sheet_selection.json")
        persisted = load_json(case / "reviews" / "sheet_selection.json")
        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "needs_sheet_selection")
        self.assertEqual(selection["trial_balance"][0]["decision_source"], "confirmed")
        self.assertTrue(
            not selection["journal"]
            or selection["journal"][0]["decision_source"] != "confirmed"
        )
        self.assertEqual(len(persisted["selections"]), 2)
        self.assertFalse((case / "work" / "normalized_journal.jsonl").exists())

    def test_hidden_row_exclusion_never_silently_processes_hidden_data(self):
        case = self.prepared_excel_case(confirm=True, hidden_journal_row=4)

        code, result = run_case(case, "off")

        quality = load_json(case / "work" / "data_quality.json")
        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        self.assertIn("hidden_row_exclusion_unavailable", [item["code"] for item in quality])
        self.assertFalse((case / "output" / "findings.json").exists())

    def test_unexpected_excel_failure_preserves_previous_current_view(self):
        case = self.prepared_excel_case(confirm=True, reconciles=True)
        first_code, _ = run_case(case, "off")
        self.assertEqual(first_code, 0)
        state_before = (case / "state.json").read_bytes()
        artifacts_before = self.business_artifact_bytes(case)

        with patch.object(
            pipeline,
            "normalize_journal",
            side_effect=RuntimeError("PRIVATE-WORKBOOK-CELL"),
        ):
            code, result = run_case(case, "off")

        self.assertEqual(code, 5)
        self.assertEqual(result["status"], "blocked_processing_error")
        self.assertNotIn("PRIVATE-WORKBOOK-CELL", json.dumps(result))
        self.assertEqual((case / "state.json").read_bytes(), state_before)
        self.assertEqual(self.business_artifact_bytes(case), artifacts_before)
        failed = case / result["failed_run_manifest"]
        self.assertEqual(load_json(failed)["error_type"], "unexpected_processing_error")

    def test_mixed_csv_journal_and_excel_trial_balance_reconcile(self):
        case = self.root / "mixed-case"
        init_case(case, "Fictional Company", "2025-01-01", "2025-12-31")
        with (case / "input" / "journal.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["凭证号", "记账日期", "科目编码", "科目名称", "摘要", "借方发生额", "贷方发生额"]
            )
            writer.writerows(
                [
                    ["J-001", "2025-01-02", "1002", "Bank", "Revenue", 1_000_000, 0],
                    ["J-001", "2025-01-02", "6001", "主营业务收入", "Revenue", 0, 1_000_000],
                    ["J-002", "2025-03-08", "6602", "管理费用-业务招待费", "Hosting", 20_000, 0],
                    ["J-002", "2025-03-08", "1002", "Bank", "Hosting", 0, 20_000],
                ]
            )
        write_workbook(
            case / "input" / "trial-balance.xlsx",
            "Trial Balance",
            (
                TRIAL_BALANCE_HEADERS,
                ("1002", "Bank", 0, 0, 1_000_000, 20_000, 980_000, 0),
                ("6001", "主营业务收入", 0, 0, 0, 1_000_000, 0, 1_000_000),
                ("6602", "管理费用-业务招待费", 0, 0, 20_000, 0, 20_000, 0),
            ),
        )
        inspect_case(case)
        inventory = load_json(case / "work" / "workbook_inventory.json")
        source = next(
            item
            for item in inventory["workbooks"]
            if item["filename"] == "trial-balance.xlsx"
        )
        confirm_sheet_selection(
            case,
            role="trial_balance",
            source_id=source["source_id"],
            sheet_id="sheet-0001",
            header_rows=(1,),
            hidden_rows="exclude",
            reviewer="host:test",
        )
        confirm_mapping(
            case,
            {
                "role": "trial_balance",
                "source_id": source["source_id"],
                "sheet_id": "sheet-0001",
                "header_rows": [1],
                "mapping": {
                    "account-code": "account_code",
                    "account-name": "account_name",
                    "opening-debit": "opening_debit",
                    "opening-credit": "opening_credit",
                    "period-debit": "period_debit",
                    "period-credit": "period_credit",
                    "closing-debit": "closing_debit",
                    "closing-credit": "closing_credit",
                },
                "exclusions": {},
                "reviewer": "host:test",
            },
        )

        code, result = run_case(case, "off")

        rows = [
            json.loads(line)
            for line in (case / "work" / "normalized_journal.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        reconciliation = load_json(case / "work" / "reconciliation.json")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "needs_professional_review")
        self.assertEqual(rows[0]["field_lineage"]["voucher_no"]["sheet_id"], "csv")
        self.assertTrue(reconciliation["journal_to_trial_balance"]["passed"])

    def test_automatic_selection_flows_to_mapping_review(self):
        case = self.root / "automatic-case"
        init_case(case, "Fictional Company", "2025-01-01", "2025-12-31")
        write_workbook(
            case / "input" / "journal.xlsx",
            "Journal",
            (
                ("凭证号", "日期", "科目名称", "借方", "贷方"),
                ("J-001", "2025-01-02", "Bank", 100, 0),
                ("J-001", "2025-01-02", "主营业务收入", 0, 100),
            ),
        )

        code, result = run_case(case, "off")

        mapping_review = load_json(case / "work" / "mapping_review.json")
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "needs_mapping_review")
        self.assertEqual(mapping_review["proposals"][0]["role"], "journal")
        self.assertFalse((case / "work" / "normalized_journal.jsonl").exists())

    def test_automatic_selection_mapping_can_be_confirmed_then_run(self):
        case = self.root / "automatic-confirmation-case"
        init_case(case, "Fictional Company", "2025-01-01", "2025-12-31")
        write_workbook(
            case / "input" / "journal.xlsx",
            "Journal",
            (
                ("凭证号", "日期", "科目名称", "借方", "贷方"),
                ("J-001", "2025-01-02", "Bank", 100, 0),
                ("J-001", "2025-01-02", "主营业务收入", 0, 100),
            ),
        )
        first_code, first_result = run_case(case, "off")
        proposal = load_json(case / "work" / "mapping_review.json")["proposals"][0]

        confirm_mapping(
            case,
            {
                "role": proposal["role"],
                "source_id": proposal["source_id"],
                "sheet_id": proposal["sheet_id"],
                "header_rows": proposal["header_rows"],
                "mapping": proposal["mapping"],
                "composites": proposal["composites"],
                "exclusions": {},
                "reviewer": "host:test",
            },
        )
        second_code, second_result = run_case(case, "off")

        self.assertEqual(first_code, 3)
        self.assertEqual(first_result["status"], "needs_mapping_review")
        self.assertEqual(second_code, 0)
        self.assertEqual(second_result["status"], "needs_professional_review")
        self.assertTrue((case / "work" / "normalized_journal.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
