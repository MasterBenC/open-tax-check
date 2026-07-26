import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check.reviews import (
    MappingReviewError,
    confirm_mapping,
    confirm_sheet_selection,
    load_active_mappings,
    save_mapping_proposal,
)


class MappingReviewTests(unittest.TestCase):
    def make_case(self, root: Path) -> Path:
        case = root / "case"
        init_case(case, "Fictional Company", "2026-01-01", "2026-12-31")
        (case / "work").mkdir(exist_ok=True)
        (case / "work" / "workbook_inventory.json").write_text(
            json.dumps(
                {
                    "workbooks": [
                        {
                            "source_id": "a" * 16,
                            "filename": "journal.xlsx",
                            "sha256": "a" * 64,
                            "sheets": [
                                {
                                    "sheet_id": "sheet-0001",
                                    "name": "Journal",
                                    "used_range": "A1:F20",
                                }
                            ],
                        },
                        {
                            "source_id": "b" * 16,
                            "filename": "trial-balance.xlsx",
                            "sha256": "b" * 64,
                            "sheets": [
                                {
                                    "sheet_id": "sheet-0001",
                                    "name": "Trial Balance",
                                    "used_range": "A1:G20",
                                }
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        confirm_sheet_selection(
            case,
            role="journal",
            source_id="a" * 16,
            sheet_id="sheet-0001",
            header_rows=(3,),
            hidden_rows="exclude",
            reviewer="host:selector",
        )
        confirm_sheet_selection(
            case,
            role="trial_balance",
            source_id="b" * 16,
            sheet_id="sheet-0001",
            header_rows=(2,),
            hidden_rows="exclude",
            reviewer="host:selector",
        )
        return case

    def valid_journal_payload(self, *, extra_header: str | None = None) -> dict:
        headers = ["voucher", "date", "account", "debit", "credit"]
        if extra_header is not None:
            headers.append(extra_header)
        return {
            "role": "journal",
            "source_id": "a" * 16,
            "sheet_id": "sheet-0001",
            "header_rows": [3],
            "headers": headers,
            "mapping": {
                "voucher": "voucher_no",
                "date": "date",
                "account": "account_name",
                "debit": "debit",
                "credit": "credit",
            },
            "exclusions": {},
            "reviewer": "host:reviewer",
        }

    def test_ai_proposal_is_inactive_until_confirmed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            save_mapping_proposal(
                case,
                self.valid_journal_payload(),
                proposal_source="ai",
            )

            active = load_active_mappings(case)

        self.assertEqual(active, {})

    def test_confirmation_rejects_duplicate_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            payload = self.valid_journal_payload()
            payload["headers"].append("debit-2")
            payload["mapping"]["debit-2"] = "debit"

            with self.assertRaisesRegex(
                MappingReviewError, "duplicate_mapping_target"
            ):
                confirm_mapping(case, payload)

    def test_confirmation_requires_every_header_mapped_or_excluded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            with self.assertRaisesRegex(
                MappingReviewError, "unresolved_source_header"
            ):
                confirm_mapping(
                    case,
                    self.valid_journal_payload(extra_header="internal-tag"),
                )

    def test_exclusion_requires_a_nonempty_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            payload = self.valid_journal_payload(extra_header="internal-tag")
            payload["exclusions"] = {"internal-tag": ""}

            with self.assertRaisesRegex(
                MappingReviewError, "missing_exclusion_reason"
            ):
                confirm_mapping(case, payload)

    def test_confirmation_persists_composites_and_activates_only_current_binding(self):
        payload = {
            "role": "trial_balance",
            "source_id": "b" * 16,
            "sheet_id": "sheet-0001",
            "header_rows": [2],
            "headers": [
                "account",
                "opening-amount",
                "opening-direction",
                "period-debit",
                "period-credit",
                "closing-amount",
                "closing-direction",
            ],
            "mapping": {
                "account": "account_name",
                "period-debit": "period_debit",
                "period-credit": "period_credit",
            },
            "composites": {
                "opening": {
                    "amount_field": "opening-amount",
                    "direction_field": "opening-direction",
                    "debit_field": "opening_debit",
                    "credit_field": "opening_credit",
                },
                "closing": {
                    "amount_field": "closing-amount",
                    "direction_field": "closing-direction",
                    "debit_field": "closing_debit",
                    "credit_field": "closing_credit",
                },
            },
            "exclusions": {},
            "reviewer": "host:reviewer",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))

            decision = confirm_mapping(case, payload)
            persisted = json.loads(
                (case / "reviews" / "mapping_review.json").read_text(
                    encoding="utf-8"
                )
            )
            active = load_active_mappings(case)

        self.assertEqual(decision["composites"], payload["composites"])
        self.assertEqual(persisted["confirmations"][0]["composites"], payload["composites"])
        self.assertEqual(active["trial_balance"][0]["mapping"], payload["mapping"])
        self.assertEqual(decision["binding"]["source_sha256"], "b" * 64)
        self.assertEqual(decision["binding"]["header_rows"], [2])
        self.assertEqual(decision["binding"]["case_period"], {"start": "2026-01-01", "end": "2026-12-31"})

    def test_mapping_becomes_inactive_when_confirmed_header_selection_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            confirm_mapping(case, self.valid_journal_payload())
            selection_path = case / "reviews" / "sheet_selection.json"
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            decision = selection["selections"][0]
            decision["header_rows"] = [5]
            decision["binding"]["header_rows"] = [5]
            binding = {
                key: value
                for key, value in decision["binding"].items()
                if key != "fingerprint"
            }
            decision["binding"]["fingerprint"] = hashlib.sha256(
                json.dumps(
                    binding,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            selection_path.write_text(json.dumps(selection), encoding="utf-8")

            active = load_active_mappings(case)

        self.assertEqual(active, {})

    def test_cli_emits_no_source_header_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case = self.make_case(Path(temp_dir))
            payload = self.valid_journal_payload()
            private_header = "PRIVATE-SOURCE-HEADER"
            payload["headers"].append(private_header)
            payload["exclusions"] = {private_header: "internal-only"}
            mapping_path = case / "mapping.json"
            mapping_path.write_text(json.dumps(payload), encoding="utf-8")

            from open_tax_check.cli import main

            output = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "opentaxcheck",
                    "confirm-mapping",
                    "--case",
                    str(case),
                    "--mapping",
                    str(mapping_path),
                ],
            ), contextlib.redirect_stdout(output):
                code = main()

        response = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(response["status"], "mapping_confirmed")
        self.assertNotIn(private_header, json.dumps(response))


if __name__ == "__main__":
    unittest.main()
