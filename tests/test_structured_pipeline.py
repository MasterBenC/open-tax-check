import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check.pipeline import run_case
from test_structured_documents import financial_payload, tax_return_payload


def _write_docs(case: Path, *, imbalance=False, period_mismatch=False, malformed=False):
    directory = case / "work" / "structured-documents"
    directory.mkdir(parents=True)
    financial = financial_payload()
    financial["statement_id"] = "bs-2026"
    financial["source_id"] = "bs-source"
    financial["period_start"] = "2026-01-01"
    financial["period_end"] = "2026-12-31"
    financial["as_of_date"] = "2026-12-31"
    financial["totals"] = {
        "assets": "1000.00",
        "liabilities_and_equity": "900.00" if imbalance else "1000.00",
    }
    financial["lines"][0]["source_anchor"]["source_id"] = "bs-source"
    tax = tax_return_payload()
    tax["return_id"] = "vat-2026"
    tax["source_id"] = "vat-source"
    tax["period_start"] = "2026-01-01"
    tax["period_end"] = "2026-06-30" if period_mismatch else "2026-12-31"
    tax["filing_status"] = "draft"
    tax["filed_at"] = None
    tax["lines"][0]["source_anchor"]["source_id"] = "vat-source"
    if malformed:
        (directory / "bad.financial-statement.json").write_text("{", encoding="utf-8")
        return
    (directory / "bs.financial-statement.json").write_text(
        json.dumps(financial, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "vat.tax-return.json").write_text(
        json.dumps(tax, ensure_ascii=False), encoding="utf-8"
    )


class StructuredPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_balanced_imported_contracts_reach_partial_scope_without_tax_report(self):
        case = self.root / "case"
        init_case(case, "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃", "2026-01-01", "2026-12-31")
        _write_docs(case)

        code, result = run_case(case, "off")

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "partial_scope_ready")
        self.assertEqual(result["next_action"], "review_coverage")
        self.assertTrue((case / "work" / "structured-reconciliation.json").is_file())
        self.assertFalse((case / "output" / "findings.json").exists())

        second_code, second_result = run_case(case, "off")

        self.assertEqual(second_code, 0)
        self.assertEqual(second_result["status"], "partial_scope_ready")
        self.assertTrue((case / "work" / "structured-documents" / "bs.financial-statement.json").is_file())

    def test_unbalanced_imported_contract_blocks_without_normal_output(self):
        case = self.root / "unbalanced"
        init_case(case, "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃", "2026-01-01", "2026-12-31")
        _write_docs(case, imbalance=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        self.assertFalse((case / "output" / "findings.json").exists())
        payload = json.loads((case / "work" / "structured-reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "blocked_data_quality")

    def test_period_mismatch_blocks_structured_run(self):
        case = self.root / "period-mismatch"
        init_case(case, "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃", "2026-01-01", "2026-12-31")
        _write_docs(case, period_mismatch=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")

    def test_malformed_structured_document_is_a_stable_data_quality_block(self):
        case = self.root / "malformed"
        init_case(case, "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃", "2026-01-01", "2026-12-31")
        _write_docs(case, malformed=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        quality = json.loads((case / "work" / "data_quality.json").read_text(encoding="utf-8"))
        self.assertIn("invalid_structured_document", [item["code"] for item in quality])

    def test_unsafe_structured_document_path_has_distinct_code(self):
        case = self.root / "unsafe"
        init_case(case, "瀹屽叏铏氭瀯绉戞妧鏈夐檺鍏徃", "2026-01-01", "2026-12-31")
        _write_docs(case)

        with patch(
            "open_tax_check.pipeline.resolve_safe_source",
            side_effect=ValueError("unsafe_source_path"),
        ):
            code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        quality = json.loads((case / "work" / "data_quality.json").read_text(encoding="utf-8"))
        self.assertIn("unsafe_structured_document_path", [item["code"] for item in quality])
