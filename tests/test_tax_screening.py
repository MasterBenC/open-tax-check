"""Behavioral tests for deterministic tax screening and question merging."""

from datetime import date
import hashlib
import json
import unittest

from open_tax_check.schemas import to_dict
from open_tax_check.tax_models import (
    CalculationSpec,
    DateRange,
    EvaluationOutcome,
    FactGap,
    FactRequirement,
    FactState,
    FactPrompt,
    PackMaturity,
    PromptChoice,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxFact,
    TaxPack,
    TaxResult,
    TaxStatus,
)
from open_tax_check.tax_screening import (
    GENERIC_SCREENING_EVALUATOR_ID,
    TaxEvaluationContext,
    build_question_queue,
    builtin_tax_evaluators,
    generic_screening_evaluator,
    screen_tax_items,
)


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
    all_requirements = requirements if requirements is not None else (requirement(),)
    all_requirements += (FactRequirement("activity.screening_applies", "boolean", (), 1, False, False),)
    return TaxPack(
        pack_id,
        tax_id,
        "CN",
        period,
        PackMaturity.CALCULATION,
        all_requirements,
        local_parameter_keys,
        evaluator_id,
        (SOURCE,),
        "1.0.0",
        "",
        ("activity.screening_applies",),
    )


def request(*facts: TaxFact, period: DateRange = PERIOD) -> TaxCheckRequest:
    applicability = TaxFact("activity.screening_applies", True, FactState.OBSERVED, ("source-001",))
    return TaxCheckRequest(period, "CN", (applicability, *facts))


def calculated(context: object) -> EvaluationOutcome:
    return EvaluationOutcome(
        status=TaxStatus.CALCULATED,
        diagnostics=("demo_check_complete",),
        evidence_refs=("source-001",),
    )


def generic_pack(
    *extra_requirements: FactRequirement,
    local_parameter_keys: tuple[str, ...] = (),
    applicability_critical: bool = True,
) -> TaxPack:
    applicability = FactRequirement(
        "activity.has_vat_taxable_transaction",
        "boolean",
        ("customer_confirmation",),
        100,
        True,
        applicability_critical,
    )
    return TaxPack(
        "vat-cn-2026-screening-v1",
        "vat",
        "CN",
        PERIOD,
        PackMaturity.SCREENING,
        (applicability, *extra_requirements),
        local_parameter_keys,
        GENERIC_SCREENING_EVALUATOR_ID,
        (SOURCE,),
        "2026.1",
        "zh-CN.v1",
        (applicability.fact_key,),
    )


def applicability_request(
    value: bool,
    state: FactState,
    *,
    evidence_refs: tuple[str, ...] = (),
    extra_facts: tuple[TaxFact, ...] = (),
) -> TaxCheckRequest:
    confirmation = (
        {"confirmed_by": "customer", "confirmed_at": "2026-07-28T10:00:00+08:00"}
        if state is FactState.CONFIRMED
        else {}
    )
    return TaxCheckRequest(
        PERIOD,
        "CN",
        (
            TaxFact(
                "activity.has_vat_taxable_transaction",
                value,
                state,
                evidence_refs,
                **confirmation,
            ),
            *extra_facts,
        ),
    )


class TaxScreeningTests(unittest.TestCase):
    def test_calculation_pack_requires_confirmation_before_amount_calculation(self) -> None:
        calculation_pack = TaxPack(
            pack_id="vat-calculation-v1",
            tax_id="vat",
            jurisdiction="CN",
            effective_period=PERIOD,
            maturity=PackMaturity.CALCULATION,
            requirements=(
                FactRequirement("calc.tax_base", "decimal", ("vat_return",), 100, True, True),
                FactRequirement("calc.rate", "decimal", ("rule_source",), 100, True, True),
            ),
            local_parameter_keys=(),
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=(RuleSource("official VAT source", "https://example.test/vat", date(2026, 1, 1)),),
            calculation=CalculationSpec(
                "generic.base_minus_deductions_times_rate.v1", "calc.tax_base", "calc.rate"
            ),
        )
        request_value = TaxCheckRequest(
            PERIOD,
            "CN",
            (
                TaxFact("calc.tax_base", "1000.00", FactState.OBSERVED, ("vat-return-2026",)),
                TaxFact("calc.rate", "0.13", FactState.CONFIRMED, ("rule-source-2026",), "customer", "2026-01-02T00:00:00+08:00"),
            ),
        )

        result = screen_tax_items(
            request_value, (calculation_pack,), builtin_tax_evaluators(), tax_ids=("vat",)
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(tuple(gap.fact_key for gap in result.gaps), ("calc.tax_base",))

    def test_calculation_pack_returns_auditable_amount_and_formula(self) -> None:
        calculation_pack = TaxPack(
            pack_id="vat-calculation-v1",
            tax_id="vat",
            jurisdiction="CN",
            effective_period=PERIOD,
            maturity=PackMaturity.CALCULATION,
            requirements=(
                FactRequirement("calc.tax_base", "decimal", ("vat_return",), 100, True, True),
                FactRequirement("calc.rate", "decimal", ("rule_source",), 100, True, True),
                FactRequirement("calc.deductions", "decimal", ("vat_return",), 90, False, False),
            ),
            local_parameter_keys=(),
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=(RuleSource("official VAT source", "https://example.test/vat", date(2026, 1, 1)),),
            calculation=CalculationSpec(
                "generic.base_minus_deductions_times_rate.v1",
                "calc.tax_base",
                "calc.rate",
                "calc.deductions",
            ),
        )
        request_value = TaxCheckRequest(
            PERIOD,
            "CN",
            (
                TaxFact("calc.tax_base", "1000.00", FactState.CONFIRMED, ("vat-return-2026",), "customer", "2026-01-02T00:00:00+08:00"),
                TaxFact("calc.rate", "0.13", FactState.CONFIRMED, ("rule-source-2026",), "customer", "2026-01-02T00:00:00+08:00"),
                TaxFact("calc.deductions", "100.00", FactState.CONFIRMED, ("vat-return-2026",), "customer", "2026-01-02T00:00:00+08:00"),
            ),
        )

        result = screen_tax_items(
            request_value,
            (calculation_pack,),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)
        self.assertIsNotNone(result.calculation)
        self.assertEqual(result.calculation.amount, "117.00")
        self.assertEqual(result.calculation.formula_id, "generic.base_minus_deductions_times_rate.v1")
        self.assertEqual(result.calculation.evidence_refs, ("vat-return-2026", "rule-source-2026"))

    def test_base_times_rate_formula_returns_amount_without_deductions(self) -> None:
        calculation_pack = TaxPack(
            pack_id="stamp-calculation-v1",
            tax_id="stamp_tax",
            jurisdiction="CN",
            effective_period=PERIOD,
            maturity=PackMaturity.CALCULATION,
            requirements=(
                FactRequirement("calc.tax_base", "decimal", ("contract",), 100, True, True),
                FactRequirement("calc.rate", "decimal", ("rule_source",), 100, True, True),
            ),
            local_parameter_keys=(),
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=(RuleSource("official stamp source", "https://example.test/stamp", date(2026, 1, 1)),),
            calculation=CalculationSpec(
                "generic.base_times_rate.v1", "calc.tax_base", "calc.rate"
            ),
        )
        request_value = TaxCheckRequest(
            PERIOD,
            "CN",
            (
                TaxFact("calc.tax_base", "1000.00", FactState.CONFIRMED, ("contract-2026",), "customer", "2026-01-02T00:00:00+08:00"),
                TaxFact("calc.rate", "0.03", FactState.CONFIRMED, ("rule-source-2026",), "customer", "2026-01-02T00:00:00+08:00"),
            ),
        )

        result = screen_tax_items(
            request_value, (calculation_pack,), builtin_tax_evaluators(), tax_ids=("stamp_tax",)
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)
        self.assertEqual(result.calculation.amount, "30.00")
        self.assertEqual(result.calculation.formula_id, "generic.base_times_rate.v1")
    def test_direct_generic_evaluator_rejects_inferred_applicability_fact(self) -> None:
        inferred = TaxFact(
            "activity.has_vat_taxable_transaction",
            False,
            FactState.INFERRED,
        )
        context = TaxEvaluationContext(
            "vat",
            applicability_request(False, FactState.INFERRED),
            (generic_pack(),),
            {inferred.key: inferred},
        )

        with self.assertRaisesRegex(ValueError, "^invalid_applicability_fact$"):
            generic_screening_evaluator(context)

    def test_confirmed_negative_applicability_is_not_applicable(self) -> None:
        result = screen_tax_items(
            applicability_request(False, FactState.CONFIRMED),
            (generic_pack(),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NOT_APPLICABLE)
        self.assertRegex(result.evidence_refs[0], r"^confirmation-[0-9a-f]{16}$")

    def test_inferred_negative_applicability_still_needs_evidence(self) -> None:
        result = screen_tax_items(
            applicability_request(False, FactState.INFERRED),
            (generic_pack(),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(
            tuple(gap.fact_key for gap in result.gaps),
            ("activity.has_vat_taxable_transaction",),
        )

    def test_inferred_noncritical_applicability_still_needs_evidence(self) -> None:
        result = screen_tax_items(
            applicability_request(False, FactState.INFERRED),
            (generic_pack(applicability_critical=False),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(
            tuple(gap.fact_key for gap in result.gaps),
            ("activity.has_vat_taxable_transaction",),
        )

    def test_screening_custom_evaluator_cannot_calculate(self) -> None:
        result = screen_tax_items(
            applicability_request(True, FactState.CONFIRMED),
            (generic_pack(),),
            {GENERIC_SCREENING_EVALUATOR_ID: calculated},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.BLOCKED)
        self.assertEqual(result.diagnostics, ("invalid_evaluator_outcome",))

    def test_confirmed_positive_without_calculation_pack_never_calculates(self) -> None:
        result = screen_tax_items(
            applicability_request(True, FactState.CONFIRMED),
            (generic_pack(),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(result.diagnostics, ("calculation_rule_unavailable",))

    def test_negative_applicability_does_not_request_irrelevant_local_parameters(self) -> None:
        local = FactRequirement("policy.vat_local_rule", "boolean", (), 90, True, True)
        result = screen_tax_items(
            applicability_request(False, FactState.CONFIRMED),
            (generic_pack(local, local_parameter_keys=(local.fact_key,)),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NOT_APPLICABLE)
        self.assertEqual(result.gaps, ())

    def test_positive_applicability_with_missing_local_parameter_needs_local_policy(self) -> None:
        local = FactRequirement("policy.vat_local_rule", "boolean", (), 90, True, True)
        result = screen_tax_items(
            applicability_request(True, FactState.CONFIRMED),
            (generic_pack(local, local_parameter_keys=(local.fact_key,)),),
            builtin_tax_evaluators(),
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.NEEDS_LOCAL_POLICY)
        self.assertEqual(result.gaps[0].reason_code, "missing_local_policy")

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

    def test_schema_1_0_calculation_evaluator_can_calculate(self) -> None:
        result = screen_tax_items(
            request(TaxFact("entity.registration", "general", FactState.OBSERVED, ("file-001",))),
            (pack(),),
            {"vat.screening": calculated},
            tax_ids=("vat",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)
        self.assertEqual(result.evidence_refs, ("source-001",))
        self.assertEqual(result.diagnostics, ("demo_check_complete",))

    def test_invalid_request_jurisdiction_cannot_reach_cn_evaluator(self) -> None:
        calls = 0

        def cn_evaluator(context: object) -> EvaluationOutcome:
            nonlocal calls
            calls += 1
            return calculated(context)

        with self.assertRaisesRegex(ValueError, "^invalid_jurisdiction$"):
            invalid_request = TaxCheckRequest(
                PERIOD,
                "US",
                (
                    TaxFact(
                        "entity.registration",
                        "general",
                        FactState.OBSERVED,
                        ("file-001",),
                    ),
                ),
            )
            screen_tax_items(
                invalid_request,
                (pack(),),
                {"vat.screening": cn_evaluator},
                tax_ids=("vat",),
            )

        self.assertEqual(calls, 0)

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
    def test_question_queue_uses_shared_chinese_prompt(self) -> None:
        results = (
            TaxResult("deed_tax", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("activity.shared", "missing_fact", "boolean", (), 70, False),
            )),
            TaxResult("stamp_tax", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("activity.shared", "missing_fact", "boolean", (), 80, True),
            )),
        )
        prompt = FactPrompt(
            "activity.shared", "是否发生相关交易？", "请按实际业务回答。", "boolean",
            (PromptChoice(True, "是"), PromptChoice(False, "否")), "请提供合同或发票。",
        )

        question = build_question_queue(results, {"activity.shared": prompt})[0]

        self.assertEqual(question.tax_ids, ("deed_tax", "stamp_tax"))
        self.assertEqual(question.question_zh, prompt.question_zh)
        self.assertEqual(question.help_zh, prompt.help_zh)
        self.assertEqual(question.evidence_help_zh, prompt.evidence_help_zh)
        self.assertEqual(question.choices, prompt.choices)

    def test_formal_question_requires_prompt(self) -> None:
        result = TaxResult("deed_tax", TaxStatus.NEEDS_EVIDENCE, ("deed-tax-cn-2026-screening",), gaps=(
            FactGap("activity.shared", "missing_fact", "choice", (), 70, False),
        ))
        with self.assertRaisesRegex(ValueError, "^missing_fact_prompt$"):
            build_question_queue((result,), {})

    def test_question_prompt_answer_type_must_match_gap(self) -> None:
        result = TaxResult("deed_tax", TaxStatus.NEEDS_EVIDENCE, ("deed-tax-cn-2026-screening",), gaps=(
            FactGap("activity.shared", "missing_fact", "choice", (), 70, False),
        ))
        prompt = FactPrompt(
            "activity.shared", "问题", "帮助", "boolean",
            (PromptChoice(True, "是"), PromptChoice(False, "否")), "证据",
        )
        with self.assertRaisesRegex(ValueError, "^incompatible_prompt_answer_type$"):
            build_question_queue((result,), {"activity.shared": prompt})

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

    def test_question_queue_rejects_incompatible_answer_types_for_one_fact(self) -> None:
        results = (
            TaxResult("vat", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("entity.registration", "missing_fact", "choice", (), 90, True),
            )),
            TaxResult("enterprise_income_tax", TaxStatus.NEEDS_EVIDENCE, gaps=(
                FactGap("entity.registration", "missing_fact", "integer", (), 90, True),
            )),
        )

        with self.assertRaisesRegex(
            ValueError,
            "^incompatible_question_answer_types$",
        ):
            build_question_queue(results)


if __name__ == "__main__":
    unittest.main()
