"""Behavioral tests for deterministic tax screening and question merging."""

from datetime import date
import hashlib
import json
import unittest

from open_tax_check.schemas import to_dict
from open_tax_check.tax_models import (
    DateRange,
    EvaluationOutcome,
    FactGap,
    FactRequirement,
    FactState,
    PackMaturity,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxFact,
    TaxPack,
    TaxResult,
    TaxStatus,
)
from open_tax_check.tax_screening import build_question_queue, screen_tax_items


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
SOURCE = RuleSource("Official source", "https://example.test/source", date(2026, 1, 1))


def requirement(
    key: str = "entity.registration",
    *,
    critical: bool = True,
    priority: int = 90,
    blocking: bool = True,
) -> FactRequirement:
    return FactRequirement(key, "choice", ("registration_record",), priority, blocking, critical)


def pack(
    *,
    tax_id: str = "vat",
    pack_id: str = "vat-cn-2026",
    requirements: tuple[FactRequirement, ...] | None = None,
    local_parameter_keys: tuple[str, ...] = (),
    evaluator_id: str | None = "vat.screening",
    period: DateRange = PERIOD,
) -> TaxPack:
    return TaxPack(
        pack_id,
        tax_id,
        "CN",
        period,
        PackMaturity.SCREENING,
        requirements if requirements is not None else (requirement(),),
        local_parameter_keys,
        evaluator_id,
        (SOURCE,),
    )


def request(*facts: TaxFact, period: DateRange = PERIOD) -> TaxCheckRequest:
    return TaxCheckRequest(period, "CN", facts)


def calculated(context: object) -> EvaluationOutcome:
    return EvaluationOutcome(
        status=TaxStatus.CALCULATED,
        diagnostics=("demo_check_complete",),
        evidence_refs=("source-001",),
    )


class TaxScreeningTests(unittest.TestCase):
    def test_missing_formal_pack_blocks_with_public_diagnostic(self) -> None:
        result = screen_tax_items(request(), (), {}, tax_ids=("vat",))[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("rule_pack_unavailable",))

    def test_partial_formal_coverage_blocks_before_missing_requirements(self) -> None:
        short_pack = pack(period=DateRange(date(2026, 1, 1), date(2026, 1, 15)))
        result = screen_tax_items(request(), (short_pack,), {}, tax_ids=("vat",))[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("rule_pack_unavailable",))

    def test_missing_normal_requirement_needs_evidence(self) -> None:
        normal_pack = pack(requirements=(requirement(critical=False),))
        result = screen_tax_items(request(), (normal_pack,), {}, tax_ids=("vat",))[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(result.gaps[0].reason_code, "missing_fact")

    def test_missing_local_parameter_precedes_other_missing_evidence(self) -> None:
        local = requirement("entity.local")
        ordinary = requirement("entity.registration", critical=False)
        local_pack = pack(requirements=(ordinary, local), local_parameter_keys=("entity.local",))
        result = screen_tax_items(request(), (local_pack,), {}, tax_ids=("vat",))[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_LOCAL_POLICY)
        self.assertEqual(
            {gap.fact_key: gap.reason_code for gap in result.gaps},
            {"entity.registration": "missing_fact", "entity.local": "missing_local_policy"},
        )

    def test_inferred_critical_fact_is_insufficient(self) -> None:
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.INFERRED)),
            (pack(),),
            {},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(result.gaps[0].fact_key, "entity.registration")

    def test_observed_critical_fact_with_evidence_is_sufficient(self) -> None:
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": calculated},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)

    def test_confirmed_critical_fact_is_sufficient_without_file_reference(self) -> None:
        result = screen_tax_items(
            request(TaxFact(
                "entity.registration", "general", FactState.CONFIRMED,
                confirmed_by="customer", confirmed_at="2026-07-28T10:00:00+08:00",
            )),
            (pack(),),
            {"vat.screening": calculated},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)

    def test_missing_registered_evaluator_blocks(self) -> None:
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("rule_evaluator_unavailable",))

    def test_evidence_backed_evaluator_can_calculate(self) -> None:
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": calculated},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)
        self.assertEqual(result.evidence_refs, ("source-001",))
        self.assertEqual(result.diagnostics, ("demo_check_complete",))

    def test_not_applicable_without_evidence_becomes_blocked(self) -> None:
        def unsupported(context: object) -> EvaluationOutcome:
            return EvaluationOutcome(TaxStatus.NOT_APPLICABLE)

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": unsupported},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))

    def test_evaluator_failure_has_privacy_safe_diagnostic(self) -> None:
        def failing(context: object) -> EvaluationOutcome:
            raise RuntimeError("customer-secret")

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": failing},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("rule_evaluator_failed",))
        self.assertNotIn("customer-secret", repr(result))

    def test_period_transition_with_different_evaluators_blocks_without_dispatch(self) -> None:
        calls: list[str] = []

        def january_start(context: object) -> EvaluationOutcome:
            calls.append("january_start")
            return calculated(context)

        def january_end(context: object) -> EvaluationOutcome:
            calls.append("january_end")
            return calculated(context)

        first = pack(
            pack_id="vat-cn-2026-start",
            evaluator_id="vat.start",
            period=DateRange(date(2026, 1, 1), date(2026, 1, 15)),
        )
        second = pack(
            pack_id="vat-cn-2026-end",
            evaluator_id="vat.end",
            period=DateRange(date(2026, 1, 16), date(2026, 1, 31)),
        )
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (first, second),
            {"vat.start": january_start, "vat.end": january_end},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("inconsistent_rule_evaluators",))
        self.assertEqual(calls, [])

    def test_unsafe_evaluator_diagnostic_is_blocked_without_leakage(self) -> None:
        def unsafe_diagnostic(context: object) -> EvaluationOutcome:
            return EvaluationOutcome(
                TaxStatus.CALCULATED,
                diagnostics=("customer secret",),
                evidence_refs=("source-001",),
            )

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": unsafe_diagnostic},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))
        self.assertNotIn("customer secret", repr(result))

    def test_unsafe_evaluator_evidence_reference_is_blocked_without_leakage(self) -> None:
        def unsafe_evidence(context: object) -> EvaluationOutcome:
            return EvaluationOutcome(
                TaxStatus.CALCULATED,
                diagnostics=("demo_check_complete",),
                evidence_refs=("customer/file",),
            )

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": unsafe_evidence},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))
        self.assertNotIn("customer/file", repr(result))

    def test_scalar_diagnostic_validation_error_becomes_safe_invalid_outcome(self) -> None:
        def scalar_diagnostic(context: object) -> EvaluationOutcome:
            return EvaluationOutcome(
                TaxStatus.CALCULATED,
                diagnostics="customersecret",
                evidence_refs=("source-001",),
            )

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": scalar_diagnostic},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))
        self.assertNotIn("customersecret", json.dumps(to_dict(result)))

    def test_scalar_evidence_validation_error_becomes_safe_invalid_outcome(self) -> None:
        def scalar_evidence(context: object) -> EvaluationOutcome:
            return EvaluationOutcome(
                TaxStatus.CALCULATED,
                diagnostics=("demo_check_complete",),
                evidence_refs="customerref",
            )

        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": scalar_evidence},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))
        self.assertNotIn("customerref", json.dumps(to_dict(result)))


class QuestionQueueTests(unittest.TestCase):
    def test_question_queue_merges_same_fact_key_deterministically(self) -> None:
        results = (
            TaxResult("vat", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("entity.registration", "missing_fact", "choice", ("license",), 70, False),
            )),
            TaxResult("enterprise_income_tax", TaxStatus.NEEDS_LOCAL_POLICY, gaps=(
                FactGap("entity.registration", "missing_local_policy", "choice", ("registration_record",), 95, True),
            )),
        )

        questions = build_question_queue(results)

        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].tax_ids, ("enterprise_income_tax", "vat"))
        self.assertEqual(questions[0].reason_codes, ("missing_fact", "missing_local_policy"))
        self.assertEqual(questions[0].evidence_types, ("license", "registration_record"))
        self.assertEqual(questions[0].priority, 95)
        self.assertTrue(questions[0].blocking)
        self.assertEqual(
            questions[0].question_id,
            "question-" + hashlib.sha256(b"entity.registration").hexdigest()[:16],
        )

    def test_question_queue_sorts_by_descending_priority_then_fact_key(self) -> None:
        results = (
            TaxResult("vat", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("zeta", "missing_fact", "text", (), 60, False),
                FactGap("alpha", "missing_fact", "text", (), 60, False),
                FactGap("beta", "missing_fact", "text", (), 90, False),
            )),
        )

        questions = build_question_queue(results)

        self.assertEqual([question.fact_key for question in questions], ["beta", "alpha", "zeta"])


if __name__ == "__main__":
    unittest.main()
