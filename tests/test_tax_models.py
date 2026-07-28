import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from open_tax_check.schemas import to_dict
from open_tax_check.tax_models import (
    AuditEvent,
    Confirmation,
    DateRange,
    EvaluationOutcome,
    FactRequirement,
    FactState,
    PackMaturity,
    Question,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
    TaxPack,
    TaxResult,
    TaxStatus,
)


class TaxModelTests(unittest.TestCase):
    def test_tax_status_contract_is_exact(self):
        self.assertEqual(
            {item.value for item in TaxStatus},
            {
                "calculated",
                "not_applicable",
                "needs_evidence",
                "needs_local_policy",
                "blocked",
            },
        )

    def test_tax_fact_is_immutable_and_json_serializable(self):
        fact = TaxFact(
            key="entity.vat_registration_status",
            value="general",
            state=FactState.CONFIRMED,
            evidence_refs=["source-001"],
            confirmed_by="customer",
            confirmed_at="2026-07-28T10:00:00+08:00",
        )

        self.assertEqual(to_dict(fact)["state"], "confirmed")
        self.assertEqual(fact.evidence_refs, ("source-001",))
        json.dumps(to_dict(fact), ensure_ascii=False)
        with self.assertRaises(FrozenInstanceError):
            fact.value = "small_scale"

    def test_date_range_rejects_reverse_dates(self):
        with self.assertRaisesRegex(ValueError, "invalid_date_range"):
            DateRange(date(2026, 2, 1), date(2026, 1, 1))

    def test_confirmed_fact_requires_complete_timezone_aware_confirmation(self):
        with self.assertRaisesRegex(ValueError, "invalid_confirmation"):
            TaxFact("entity.type", "company", FactState.CONFIRMED)
        with self.assertRaisesRegex(ValueError, "invalid_timestamp"):
            TaxFact(
                "entity.type",
                "company",
                FactState.CONFIRMED,
                confirmed_by="customer",
                confirmed_at="2026-07-28T10:00:00",
            )

    def test_inferred_fact_cannot_include_confirmation_metadata(self):
        with self.assertRaisesRegex(ValueError, "invalid_inferred_confirmation"):
            TaxFact(
                "entity.type",
                "company",
                FactState.INFERRED,
                confirmed_by="customer",
                confirmed_at="2026-07-28T10:00:00+08:00",
            )

    def test_fact_rejects_float_values(self):
        with self.assertRaisesRegex(ValueError, "invalid_fact_value"):
            TaxFact("income.amount", 1.5, FactState.OBSERVED)

    def test_not_applicable_result_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "not_applicable_without_evidence"):
            TaxResult(tax_id="vat", status=TaxStatus.NOT_APPLICABLE)

    def test_not_applicable_status_string_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "not_applicable_without_evidence"):
            TaxResult(tax_id="vat", status="not_applicable")

    def test_evaluation_outcome_canonicalizes_list_collections(self):
        outcome = EvaluationOutcome(
            TaxStatus.CALCULATED,
            diagnostics=["demo_check_complete"],
            evidence_refs=["source-001"],
        )

        self.assertEqual(outcome.diagnostics, ("demo_check_complete",))
        self.assertEqual(outcome.evidence_refs, ("source-001",))

    def test_evaluation_outcome_rejects_scalar_and_non_sequence_diagnostics(self):
        for invalid_value in ("customersecret", b"customersecret", 1):
            with self.subTest(value_type=type(invalid_value).__name__):
                with self.assertRaisesRegex(ValueError, "^invalid_evaluation_diagnostics$"):
                    EvaluationOutcome(TaxStatus.CALCULATED, diagnostics=invalid_value)

    def test_evaluation_outcome_rejects_scalar_and_non_sequence_evidence_references(self):
        for invalid_value in ("customerref", b"customerref", 1):
            with self.subTest(value_type=type(invalid_value).__name__):
                with self.assertRaisesRegex(ValueError, "^invalid_evaluation_evidence_refs$"):
                    EvaluationOutcome(TaxStatus.CALCULATED, evidence_refs=invalid_value)

    def test_tax_fact_rejects_invalid_deserialized_state(self):
        with self.assertRaisesRegex(ValueError, "invalid_fact_state"):
            TaxFact("entity.type", "company", "unknown")

    def test_confirmed_state_string_requires_confirmation_metadata(self):
        with self.assertRaisesRegex(ValueError, "invalid_confirmation"):
            TaxFact("entity.type", "company", "confirmed")

    def test_tax_result_rejects_invalid_deserialized_status(self):
        with self.assertRaisesRegex(ValueError, "invalid_tax_status"):
            TaxResult("vat", "unknown")

    def test_tax_pack_rejects_invalid_deserialized_maturity(self):
        with self.assertRaisesRegex(ValueError, "invalid_pack_maturity"):
            TaxPack(
                "vat-cn",
                "vat",
                "CN",
                DateRange(date(2026, 1, 1), date(2026, 1, 31)),
                "unknown",
                (),
                (),
                None,
                (),
            )

    def test_question_rejects_boolean_and_out_of_range_priority(self):
        base = dict(
            question_id="q-001",
            fact_key="entity.type",
            tax_ids=("vat",),
            reason_codes=("missing_fact",),
            answer_type="text",
            evidence_types=(),
            blocking=True,
        )
        with self.assertRaisesRegex(ValueError, "invalid_priority"):
            Question(priority=True, **base)
        with self.assertRaisesRegex(ValueError, "invalid_priority"):
            Question(priority=101, **base)

    def test_request_rejects_duplicate_fact_keys(self):
        fact = TaxFact("entity.type", "company", FactState.OBSERVED)
        with self.assertRaisesRegex(ValueError, "duplicate_fact_key"):
            TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 1, 31)), "CN", [fact, fact])

    def test_request_accepts_only_lowercase_sha256_input_hashes(self):
        valid_hashes = ("0" * 64, "abcdef0123456789" * 4)

        request = TaxCheckRequest(
            DateRange(date(2026, 1, 1), date(2026, 1, 31)),
            "CN",
            input_hashes=list(valid_hashes),
        )

        self.assertEqual(request.input_hashes, valid_hashes)
        self.assertEqual(to_dict(request)["input_hashes"], list(valid_hashes))
        for unsafe_value in (
            "A" * 64,
            "f" * 63,
            "f" * 65,
            "sha256:" + "f" * 64,
            r"C:\customer\source.xlsx",
            "/customer/source.xlsx",
            "customer source contents",
        ):
            with self.subTest(unsafe_value=unsafe_value):
                with self.assertRaisesRegex(ValueError, "^invalid_input_hash$"):
                    TaxCheckRequest(
                        DateRange(date(2026, 1, 1), date(2026, 1, 31)),
                        "CN",
                        input_hashes=(unsafe_value,),
                    )

    def test_request_accepts_only_supported_china_jurisdictions(self):
        period = DateRange(date(2026, 1, 1), date(2026, 1, 31))

        self.assertEqual(TaxCheckRequest(period, "CN").jurisdiction, "CN")
        self.assertEqual(TaxCheckRequest(period, "CN-11").jurisdiction, "CN-11")
        for unsafe_value in ("", "cn", "CN-1", "CN-111", "CN-AA", "US", "CN/11"):
            with self.subTest(unsafe_value=unsafe_value):
                with self.assertRaisesRegex(ValueError, "^invalid_jurisdiction$"):
                    TaxCheckRequest(period, unsafe_value)

    def test_snapshot_rejects_duplicate_result_tax_ids(self):
        request = TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 1, 31)), "CN")
        result = TaxResult("vat", TaxStatus.CALCULATED)
        with self.assertRaisesRegex(ValueError, "duplicate_result_tax_id"):
            TaxCheckSnapshot(request, [result, result], [], [], 0)

    def test_all_record_collections_are_canonical_tuples(self):
        requirement = FactRequirement("entity.type", "text", ["license"], 10, True, True)
        confirmation = Confirmation(
            "q-001", "entity.type", "company", "customer", "2026-07-28T10:00:00+08:00", ["source-001"]
        )
        event = AuditEvent("created", "2026-07-28T10:00:00+08:00", ["vat"], ["entity.type"])

        self.assertEqual(requirement.evidence_types, ("license",))
        self.assertEqual(confirmation.evidence_refs, ("source-001",))
        self.assertEqual(event.tax_ids, ("vat",))
        self.assertEqual(event.fact_keys, ("entity.type",))

    def test_confirmation_accepts_only_safe_sequence_evidence_references(self):
        confirmed = Confirmation(
            "q-001",
            "entity.type",
            "company",
            "customer",
            "2026-07-28T10:00:00+08:00",
            ["source:vat_2026.01-abc"],
        )

        self.assertEqual(confirmed.evidence_refs, ("source:vat_2026.01-abc",))
        for unsafe_collection in (
            r"C:\customer\source.xlsx",
            b"source-001",
            1,
            {"source-001"},
        ):
            with self.subTest(collection_type=type(unsafe_collection).__name__):
                with self.assertRaisesRegex(
                    ValueError,
                    "^invalid_confirmation_evidence_refs$",
                ):
                    Confirmation(
                        "q-001",
                        "entity.type",
                        "company",
                        "customer",
                        "2026-07-28T10:00:00+08:00",
                        unsafe_collection,
                    )

        for unsafe_reference in (
            "",
            "_source",
            "/customer/source.xlsx",
            r"C:\customer\source.xlsx",
            "customer source",
            "a" * 129,
            1,
        ):
            with self.subTest(reference=unsafe_reference):
                with self.assertRaisesRegex(
                    ValueError,
                    "^invalid_confirmation_evidence_ref$",
                ):
                    Confirmation(
                        "q-001",
                        "entity.type",
                        "company",
                        "customer",
                        "2026-07-28T10:00:00+08:00",
                        (unsafe_reference,),
                    )

    def test_fact_requirement_accepts_only_supported_answer_types(self):
        supported = ("text", "choice", "integer", "boolean", "date", "decimal")

        for answer_type in supported:
            with self.subTest(answer_type=answer_type):
                requirement = FactRequirement(
                    "entity.type",
                    answer_type,
                    (),
                    10,
                    True,
                    True,
                )
                self.assertEqual(requirement.answer_type, answer_type)

        for answer_type in ("", "currency", "Text", None, ["text"]):
            with self.subTest(answer_type=answer_type):
                with self.assertRaisesRegex(ValueError, "^unsupported_answer_type$"):
                    FactRequirement(
                        "entity.type",
                        answer_type,
                        (),
                        10,
                        True,
                        True,
                    )

    def test_snapshot_canonicalizes_question_history(self):
        request = TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 1, 31)), "CN")
        question = Question(
            "q-001", "entity.type", ("vat",), ("missing_fact",), "text", (), 10, True
        )

        snapshot = TaxCheckSnapshot(
            request, (TaxResult("vat", TaxStatus.CALCULATED),), (), (), 1,
            question_history=[question],
        )

        self.assertEqual(snapshot.question_history, (question,))

    def test_snapshot_preserves_schema_version_as_sixth_positional_argument(self):
        request = TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 1, 31)), "CN")

        snapshot = TaxCheckSnapshot(
            request, (TaxResult("vat", TaxStatus.CALCULATED),), (), (), 1,
            "custom-schema",
        )

        self.assertEqual(snapshot.schema_version, "custom-schema")
        self.assertEqual(snapshot.question_history, ())


if __name__ == "__main__":
    unittest.main()
