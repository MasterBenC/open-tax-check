import json
import unittest
from dataclasses import FrozenInstanceError

from open_tax_check.schemas import CoverageItem, RoleScore, SheetRecord, SourceRecord, to_dict


class VersionedSchemaTests(unittest.TestCase):
    def test_source_record_uses_digest_prefix_as_source_id(self):
        record = SourceRecord.from_digest(
            filename="虚构序时账.xlsx",
            digest="a" * 64,
            size_bytes=128,
            extension=".xlsx",
            status="accepted",
        )

        self.assertEqual(record.source_id, "a" * 16)
        self.assertEqual(to_dict(record)["schema_version"], "0.2.0")
        with self.assertRaises(FrozenInstanceError):
            record.status = "rejected"

    def test_role_score_rejects_out_of_range_score(self):
        with self.assertRaisesRegex(ValueError, "invalid_role_score"):
            RoleScore(role="journal", score=101, signals=())

    def test_sheet_record_preserves_only_immutable_metadata(self):
        record = SheetRecord(
            source_id="source-001",
            sheet_id="sheet-0001",
            index=0,
            name="序时账",
            visibility="visible",
            used_range="A1:G10",
            visited_cell_estimate=70,
            header_rows=(1, 2),
            candidate_header_rows=((1,), (1, 2)),
            role_scores=(RoleScore("journal", 95, ("required_headers",)),),
            has_formulas=None,
            has_merged_ranges=True,
            has_hidden_data=None,
            merged_ranges=("A1:B1",),
        )

        serialized = to_dict(record)
        self.assertEqual(serialized["header_rows"], [1, 2])
        self.assertEqual(serialized["candidate_header_rows"], [[1], [1, 2]])
        self.assertEqual(serialized["role_scores"][0]["signals"], ["required_headers"])
        self.assertIsNone(serialized["has_formulas"])
        self.assertIsNone(serialized["has_hidden_data"])
        self.assertEqual(serialized["merged_ranges"], ["A1:B1"])
        self.assertNotIn("cell_values", serialized)

    def test_coverage_item_serializes_tuple_source_ids_as_a_list(self):
        item = CoverageItem(
            evidence_role="journal",
            tier="required",
            status="present_unparsed",
            procedure_status="not_performed",
            source_ids=("source-001",),
            trigger_rule_id=None,
            requested_at=None,
            request_status="not_requested",
        )

        self.assertEqual(to_dict(item)["source_ids"], ["source-001"])
        self.assertEqual(to_dict(item)["schema_version"], "0.2.0")

    def test_to_dict_recursively_serializes_tuples_and_dataclasses(self):
        value = {"items": (RoleScore("journal", 0, ("empty",)),)}

        self.assertEqual(
            to_dict(value),
            {"items": [{"role": "journal", "score": 0, "signals": ["empty"]}]},
        )

    def test_to_dict_recursively_serializes_nested_lists(self):
        value = [
            RoleScore("journal", 10, ("header",)),
            {"nested": (RoleScore("trial_balance", 20, ("balance",)),)},
        ]

        serialized = to_dict(value)

        self.assertEqual(
            serialized,
            [
                {"role": "journal", "score": 10, "signals": ["header"]},
                {
                    "nested": [
                        {"role": "trial_balance", "score": 20, "signals": ["balance"]}
                    ]
                },
            ],
        )
        json.dumps(serialized)


if __name__ == "__main__":
    unittest.main()
