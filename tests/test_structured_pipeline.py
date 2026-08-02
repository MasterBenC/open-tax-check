import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check.pipeline import inspect_case, load_case_structured_documents, run_case
import open_tax_check.structured_document_providers as structured_providers
from test_structured_documents import financial_payload, tax_return_payload


def _write_docs(case: Path, *, imbalance=False, period_mismatch=False, malformed=False):
    if (case / "input/journal.csv").is_file():
        financial_filename = tax_filename = "journal.csv"
    else:
        financial_filename = "financial-source.txt"
        tax_filename = "tax-source.txt"
        (case / "input" / financial_filename).write_bytes(b"fictional financial source\n")
        (case / "input" / tax_filename).write_bytes(b"fictional tax source\n")
    inspect_case(case)
    manifest = json.loads(
        (case / "work/input_manifest.json").read_text(encoding="utf-8")
    )
    source_ids = {
        item["filename"]: item["source_id"] for item in manifest["sources"]
    }
    financial = financial_payload()
    financial["statement_id"] = "bs-2026"
    financial["source_id"] = source_ids[financial_filename]
    financial["period_start"] = "2026-01-01"
    financial["period_end"] = "2026-12-31"
    financial["as_of_date"] = "2026-12-31"
    financial["totals"] = {
        "assets": "1000.00",
        "liabilities_and_equity": "900.00" if imbalance else "1000.00",
    }
    financial["lines"][0]["source_anchor"]["source_id"] = financial["source_id"]
    tax = tax_return_payload()
    tax["return_id"] = "vat-2026"
    tax["source_id"] = source_ids[tax_filename]
    tax["period_start"] = "2026-01-01"
    tax["period_end"] = "2026-06-30" if period_mismatch else "2026-12-31"
    tax["filing_status"] = "draft"
    tax["filed_at"] = None
    tax["lines"][0]["source_anchor"]["source_id"] = tax["source_id"]
    financial_path = structured_providers.persist_financial_statement(
        case, financial["source_id"], financial
    )
    if malformed:
        financial_path.write_text("{", encoding="utf-8")
        index_path = case / "work/structured-document-index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        import hashlib
        index["documents"][0]["document_sha256"] = hashlib.sha256(b"{").hexdigest()
        index_path.write_text(json.dumps(index), encoding="utf-8")
        return
    structured_providers.persist_tax_return(
        case, tax["source_id"], tax
    )


class StructuredPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _manifest_source(self, case: Path, filename: str = "statement.pdf") -> str:
        (case / "input" / filename).write_bytes(b"fictional structured source\n")
        inspect_case(case)
        manifest = json.loads(
            (case / "work/input_manifest.json").read_text(encoding="utf-8")
        )
        return next(
            item["source_id"]
            for item in manifest["sources"]
            if item["filename"] == filename
        )

    def test_persisted_structured_document_receipt_binds_manifest_source_and_document_hash(self):
        case = self.root / "receipt"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        source_id = self._manifest_source(case)
        payload = financial_payload()

        path = structured_providers.persist_financial_statement(
            case, source_id, payload
        )

        receipt = json.loads(
            (case / "work/structured-document-index.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (case / "work/input_manifest.json").read_text(encoding="utf-8")
        )
        source = next(item for item in manifest["sources"] if item["source_id"] == source_id)
        entry = receipt["documents"][0]
        import hashlib

        self.assertEqual(entry["source_id"], source_id)
        self.assertEqual(entry["source_sha256"], source["sha256"])
        self.assertEqual(entry["relative_path"], path.name)
        self.assertEqual(entry["document_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        statements, returns, issues = load_case_structured_documents(case)
        self.assertEqual(len(statements), 1)
        self.assertEqual(returns, ())
        self.assertEqual(issues, ())

    def test_loader_rejects_unregistered_structured_document(self):
        case = self.root / "unregistered"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        source_id = self._manifest_source(case)
        payload = financial_payload()
        structured_providers.persist_financial_statement(case, source_id, payload)
        forged = financial_payload()
        forged["statement_id"] = "forged-statement"
        (case / "work/structured-documents/forged.financial-statement.json").write_text(
            json.dumps(forged, ensure_ascii=False), encoding="utf-8"
        )

        statements, _, issues = load_case_structured_documents(case)

        self.assertEqual(statements, ())
        self.assertIn("unregistered_structured_document", issues)

    def test_loader_rejects_changed_document_and_changed_manifest_source_hash(self):
        for mutation in ("document", "manifest"):
            with self.subTest(mutation=mutation):
                case = self.root / f"changed-{mutation}"
                init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
                source_id = self._manifest_source(case)
                path = structured_providers.persist_financial_statement(
                    case, source_id, financial_payload()
                )
                if mutation == "document":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["totals"]["assets"] = "999.00"
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                else:
                    manifest_path = case / "work/input_manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    next(item for item in manifest["sources"] if item["source_id"] == source_id)["sha256"] = "0" * 64
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                statements, _, issues = load_case_structured_documents(case)

                self.assertEqual(statements, ())
                self.assertIn("structured_document_receipt_mismatch", issues)

    def test_persistence_rejects_linked_structured_document_directory_without_external_write(self):
        case = self.root / "linked-write"
        outside = self.root / "outside"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        outside.mkdir()
        source_id = self._manifest_source(case)
        target = case / "work/structured-documents"
        try:
            target.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks are unavailable: {error}")

        with self.assertRaisesRegex(
            structured_providers.StructuredDocumentImportError,
            "^unsafe_structured_document_path$",
        ):
            structured_providers.persist_financial_statement(
                case, source_id, financial_payload()
            )

        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
    def test_persistence_rejects_junctioned_structured_document_directory_without_external_write(self):
        case = self.root / "junction-write"
        outside = self.root / "junction-outside"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        outside.mkdir()
        source_id = self._manifest_source(case)
        target = case / "work/structured-documents"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"junctions are unavailable: {result.stderr.strip()}")

        with self.assertRaisesRegex(
            structured_providers.StructuredDocumentImportError,
            "^unsafe_structured_document_path$",
        ):
            structured_providers.persist_financial_statement(
                case, source_id, financial_payload()
            )

        self.assertEqual(list(outside.iterdir()), [])

    def test_structured_document_index_enforces_document_count_and_byte_budgets(self):
        case = self.root / "index-budget"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        source_id = self._manifest_source(case)
        structured_providers.persist_financial_statement(
            case, source_id, financial_payload()
        )
        second = financial_payload()
        second["statement_id"] = "second-statement"

        with patch.object(
            structured_providers, "STRUCTURED_DOCUMENT_INDEX_MAX_DOCUMENTS", 1
        ):
            with self.assertRaisesRegex(
                structured_providers.StructuredDocumentImportError,
                "^structured_document_index_limit_exceeded$",
            ):
                structured_providers.persist_financial_statement(
                    case, source_id, second
                )

        index_path = case / "work/structured-document-index.json"
        with patch.object(
            structured_providers,
            "STRUCTURED_DOCUMENT_INDEX_MAX_BYTES",
            len(index_path.read_bytes()) - 1,
        ):
            with self.assertRaisesRegex(
                structured_providers.StructuredDocumentImportError,
                "^structured_document_index_limit_exceeded$",
            ):
                structured_providers.persist_financial_statement(
                    case, source_id, financial_payload()
                )

    def test_loader_enforces_index_document_and_entry_count_budgets(self):
        case = self.root / "loader-budget"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        source_id = self._manifest_source(case)
        path = structured_providers.persist_financial_statement(
            case, source_id, financial_payload()
        )
        index_path = case / "work/structured-document-index.json"

        with patch(
            "open_tax_check.pipeline.STRUCTURED_DOCUMENT_INDEX_MAX_BYTES",
            len(index_path.read_bytes()) - 1,
        ):
            statements, returns, issues = load_case_structured_documents(case)
        self.assertEqual((statements, returns), ((), ()))
        self.assertIn("structured_document_budget_exceeded", issues)

        with patch(
            "open_tax_check.pipeline.STRUCTURED_DOCUMENT_INDEX_MAX_DOCUMENTS", 0
        ):
            statements, returns, issues = load_case_structured_documents(case)
        self.assertEqual((statements, returns), ((), ()))
        self.assertIn("structured_document_receipt_mismatch", issues)

        with patch(
            "open_tax_check.pipeline.STRUCTURED_DOCUMENT_MAX_BYTES",
            len(path.read_bytes()) - 1,
        ):
            statements, returns, issues = load_case_structured_documents(case)
        self.assertEqual((statements, returns), ((), ()))
        self.assertIn("structured_document_budget_exceeded", issues)

    def test_oversized_index_and_document_are_rejected_before_unbounded_read_helpers(self):
        case = self.root / "bounded-read"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        source_id = self._manifest_source(case)
        document_path = structured_providers.persist_financial_statement(
            case, source_id, financial_payload()
        )
        index_path = case / "work/structured-document-index.json"
        index_bytes = index_path.read_bytes()

        with index_path.open("r+b") as handle:
            handle.truncate(1025)
        with patch.object(
            structured_providers, "STRUCTURED_DOCUMENT_INDEX_MAX_BYTES", 1024
        ), patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            with self.assertRaisesRegex(
                structured_providers.StructuredDocumentImportError,
                "^structured_document_index_limit_exceeded$",
            ):
                structured_providers.persist_financial_statement(
                    case, source_id, financial_payload()
                )

        index_path.write_bytes(index_bytes)
        with document_path.open("r+b") as handle:
            handle.truncate(1025)
        with patch(
            "open_tax_check.pipeline.STRUCTURED_DOCUMENT_MAX_BYTES", 1024
        ), patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            statements, returns, issues = load_case_structured_documents(case)
        self.assertEqual((statements, returns), ((), ()))
        self.assertIn("structured_document_budget_exceeded", issues)

    def test_persistence_rejects_oversized_case_metadata_without_read_text(self):
        for relative_path in ("state.json", "work/input_manifest.json"):
            with self.subTest(relative_path=relative_path):
                case = self.root / relative_path.replace("/", "-")
                init_case(
                    case,
                    "完全虚构科技有限公司",
                    "2026-01-01",
                    "2026-12-31",
                )
                source_id = self._manifest_source(case)
                metadata_path = case / relative_path
                with metadata_path.open("r+b") as handle:
                    handle.truncate(1025)

                with patch.object(
                    structured_providers,
                    "STRUCTURED_DOCUMENT_CASE_METADATA_MAX_BYTES",
                    1024,
                ), patch.object(
                    Path, "read_text", side_effect=AssertionError("unbounded read")
                ):
                    with self.assertRaisesRegex(
                        structured_providers.StructuredDocumentImportError,
                        "^source_mismatch$",
                    ):
                        structured_providers.persist_financial_statement(
                            case, source_id, financial_payload()
                        )

    def test_balanced_imported_contracts_reach_partial_scope_without_tax_report(self):
        case = self.root / "case"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
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
        self.assertTrue(
            any((case / "work" / "structured-documents").glob("*.fs.json"))
        )

    def test_unbalanced_imported_contract_blocks_without_normal_output(self):
        case = self.root / "unbalanced"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        _write_docs(case, imbalance=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        self.assertFalse((case / "output" / "findings.json").exists())
        payload = json.loads((case / "work" / "structured-reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "blocked_data_quality")

    def test_period_mismatch_blocks_structured_run(self):
        case = self.root / "period-mismatch"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        _write_docs(case, period_mismatch=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")

    def test_malformed_structured_document_is_a_stable_data_quality_block(self):
        case = self.root / "malformed"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        _write_docs(case, malformed=True)

        code, result = run_case(case, "off")

        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "blocked_data_quality")
        quality = json.loads((case / "work" / "data_quality.json").read_text(encoding="utf-8"))
        self.assertIn("invalid_structured_document", [item["code"] for item in quality])

    def test_unsafe_structured_document_path_has_distinct_code(self):
        case = self.root / "unsafe"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
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

    def test_csv_run_also_reconciles_imported_structured_documents(self):
        case = self.root / "combined"
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        journal = case / "input" / "journal.csv"
        journal.write_text(
            "凭证号,记账日期,科目编码,科目名称,业务说明,借方发生额,贷方发生额\n"
            "记-001,2026-01-02,1002,银行存款,销售,1000,0\n"
            "记-001,2026-01-02,6001,主营业务收入,销售,0,1000\n",
            encoding="utf-8",
        )
        _write_docs(case)

        code, result = run_case(case, "off")

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "needs_professional_review")
        self.assertTrue((case / "work" / "structured-reconciliation.json").is_file())

    def test_combined_run_preserves_structured_documents_in_deep_case_path(self):
        case = (
            self.root
            / ("deep-" + "x" * 85)
            / ("case-" + "y" * 35)
        )
        init_case(case, "完全虚构科技有限公司", "2026-01-01", "2026-12-31")
        (case / "input/journal.csv").write_text(
            "凭证号,记账日期,科目编码,科目名称,业务说明,借方发生额,贷方发生额\n"
            "记-001,2026-01-02,1002,银行存款,销售,1000,0\n"
            "记-001,2026-01-02,6001,主营业务收入,销售,0,1000\n",
            encoding="utf-8",
        )
        _write_docs(case)

        code, result = run_case(case, "off")

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "needs_professional_review")
        self.assertTrue(
            any((case / "work/structured-documents").glob("*.fs.json"))
        )
