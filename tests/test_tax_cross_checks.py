import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from open_tax_check.tax_cross_checks import (
    ABSOLUTE_TOLERANCE,
    CHECK_ORDER,
    RELATIVE_TOLERANCE,
    CrossCheckComponent,
    CrossCheckQuestion,
    CrossCheckResult,
    CrossCheckSourceRef,
    CrossCheckStatus,
    CrossCheckContext,
    DateRange,
    TaxCrossCheckRun,
    AmountBridgeItem,
    CrossCheckExplanation,
    apply_cross_check_explanation,
    classified_ledger_total,
    compare_amounts,
    run_tax_cross_checks,
    rerun_tax_cross_check,
    parse_tax_cross_check_payload,
    statement_total,
    tax_fact_total,
    tax_cross_check_payload,
)
from open_tax_check.account_classification import parse_account_classification_review
from open_tax_check.structured_documents import (
    FinancialStatement,
    FinancialStatementLine,
    SourceAnchor,
    TaxReturn,
    TaxReturnLine,
)


@pytest.mark.parametrize(
    ("left", "right", "status", "difference", "ratio"),
    [
        ("0.00", "0.00", "matched", "0.00", "0"),
        ("100.00", "100.00", "matched", "0.00", "0"),
        (
            "100000.00",
            "100009.00",
            "within_tolerance",
            "9.00",
            "0.00008999190072893439590436860683",
        ),
        (
            "100.00",
            "109.00",
            "difference_found",
            "9.00",
            "0.08256880733944954128440366972",
        ),
        ("0.00", "1.00", "difference_found", "1.00", "1"),
        (
            "-100.00",
            "-110.01",
            "difference_found",
            "10.01",
            "0.09099172802472502499772747932",
        ),
    ],
)
def test_compare_amounts_uses_both_fixed_thresholds(
    left, right, status, difference, ratio
):
    assert compare_amounts(Decimal(left), Decimal(right)) == (
        CrossCheckStatus(status),
        Decimal(difference),
        Decimal(ratio),
    )


def source_ref() -> CrossCheckSourceRef:
    return CrossCheckSourceRef(
        source_id="source-1",
        source_type="ledger_account",
        locator="6001|主营业务收入",
        account_category="main_business_revenue",
    )


def question(check_id: str = CHECK_ORDER[0]) -> CrossCheckQuestion:
    return CrossCheckQuestion(
        question_id=f"question-{check_id}",
        check_id=check_id,
        period_start="2025-01-01",
        period_end="2025-12-31",
        source_refs=(source_ref(),),
        question_zh="请确认差异原因并提供资料。",
        help_zh="请按账套与申报表逐项说明。",
        evidence_types=("ledger", "tax_return"),
        priority=100,
    )


def result(check_id: str, status=CrossCheckStatus.MATCHED) -> CrossCheckResult:
    return CrossCheckResult(
        check_id=check_id,
        tax_ids=("cit",),
        status=status,
        ledger_amount=Decimal("100.00"),
        comparison_amount=Decimal("100.00"),
        difference_amount=Decimal("0.00"),
        difference_ratio=Decimal("0"),
        absolute_tolerance=ABSOLUTE_TOLERANCE,
        relative_tolerance=RELATIVE_TOLERANCE,
        period_start="2025-01-01",
        period_end="2025-12-31",
        source_refs=(source_ref(),),
        title_zh="账套收入与利润表营业收入核对",
        reason_codes=(),
        question=None,
        components=(),
        audit_refs=(),
    )


def completed_run() -> TaxCrossCheckRun:
    return TaxCrossCheckRun(
        schema_version="tax-cross-checks/v1",
        status="completed",
        results=tuple(result(check_id) for check_id in CHECK_ORDER),
        questions=(),
        source_manifest_sha256="a" * 64,
        audit_events=(),
        blocking_issue_refs=(),
    )


def difference_run() -> TaxCrossCheckRun:
    base = completed_run()
    target = CrossCheckResult(
        check_id=CHECK_ORDER[1],
        tax_ids=("vat",),
        status=CrossCheckStatus.DIFFERENCE_FOUND,
        ledger_amount=Decimal("1000.00"),
        comparison_amount=Decimal("1100.00"),
        difference_amount=Decimal("100.00"),
        difference_ratio=Decimal("0.09090909090909090909090909091"),
        absolute_tolerance=ABSOLUTE_TOLERANCE,
        relative_tolerance=RELATIVE_TOLERANCE,
        period_start="2025-01-01",
        period_end="2025-12-31",
        source_refs=(source_ref(),),
        title_zh="利润表营业收入与增值税申报销售额核对",
        reason_codes=("timing_difference",),
        question=question(CHECK_ORDER[1]),
        components=(),
        audit_refs=(),
    )
    results = list(base.results)
    results[1] = target
    return TaxCrossCheckRun(
        base.schema_version,
        base.status,
        tuple(results),
        (target.question,),
        base.source_manifest_sha256,
        (),
        (),
    )


def explanation() -> CrossCheckExplanation:
    return CrossCheckExplanation(
        check_id=CHECK_ORDER[1],
        category="timing_difference",
        explanation_zh="该差异为已核验的申报与账务确认期间差异。",
        confirmed_by="human:Chen Li",
        confirmed_at="2026-08-05T10:00:00+08:00",
        evidence_refs=("source-1",),
    )


def test_compare_amounts_is_independent_of_ambient_decimal_precision():
    with localcontext() as decimal_context:
        decimal_context.prec = 6
        actual = compare_amounts(Decimal("100000.00"), Decimal("100009.00"))

    assert actual == (
        CrossCheckStatus.WITHIN_TOLERANCE,
        Decimal("9.00"),
        Decimal("0.00008999190072893439590436860683"),
    )


@pytest.mark.parametrize(
    "confirmed_by",
    (
        "Chen Li",
        "ai:tax-agent",
        "agent:reviewer",
        "model:gpt",
        "human:ai",
        "human:ChatGPT",
        "human:Claude",
        "human:DeepSeek",
        "human:Qwen",
        "human:Hermes Agent",
        "human:GPT5",
        "human:Claude4.8",
        "human:DeepSeekV4",
        "human:ＧＰＴ５",
        "human:通义千问",
        "human:Clаude4",
        "human:G P T 5",
    ),
)
def test_cross_check_explanation_requires_explicit_non_ai_human_attestation(
    confirmed_by,
):
    with pytest.raises(ValueError, match="invalid_tax_cross_check"):
        CrossCheckExplanation(
            check_id=CHECK_ORDER[1],
            category="timing_difference",
            explanation_zh="\u8be5\u5dee\u5f02\u7531\u4eba\u5de5\u590d\u6838\u786e\u8ba4\u3002",
            confirmed_by=confirmed_by,
            confirmed_at="2026-08-05T10:00:00+08:00",
            evidence_refs=("source-1",),
        )


def test_text_only_explanation_cannot_close_difference():
    updated = apply_cross_check_explanation(
        difference_run(), explanation(), bridge=(), verified_evidence_refs=("source-1",)
    )

    assert updated.results[1].resolution_status == "explained_pending_verification"
    assert updated.results[1].status is CrossCheckStatus.DIFFERENCE_FOUND
    assert updated.results[1].difference_amount == Decimal("100.00")


def test_rerun_preserves_prior_explanation_history(monkeypatch):
    import open_tax_check.tax_cross_checks as cross_checks

    pending = apply_cross_check_explanation(
        difference_run(), explanation(), bridge=(), verified_evidence_refs=("source-1",)
    )
    fresh = replace(
        pending.results[1],
        question=question(CHECK_ORDER[1]),
        audit_refs=(),
        resolution_status="unresolved",
        explanations=(),
    )
    monkeypatch.setattr(cross_checks, "_execute_cross_check", lambda *args: fresh)

    rerun = rerun_tax_cross_check(
        pending,
        CHECK_ORDER[1],
        (),
        None,
        (),
        (),
        CrossCheckContext(
            "2025-01-01", "2025-12-31", "虚构科技有限公司", "CNY"
        ),
        pending.source_manifest_sha256,
    )
    target = rerun.results[1]
    assert target.explanations == pending.results[1].explanations
    assert target.audit_refs == pending.results[1].audit_refs
    assert target.resolution_status == "explained_pending_verification"
    assert any(event.startswith("cross-check-rerun-before:") for event in rerun.audit_events)


def test_rerun_always_preserves_original_result_without_prior_explanation(monkeypatch):
    import open_tax_check.tax_cross_checks as cross_checks

    original = difference_run()
    fresh = replace(
        original.results[1],
        status=CrossCheckStatus.NOT_PERFORMED,
        ledger_amount=None,
        comparison_amount=None,
        difference_amount=None,
        difference_ratio=None,
        source_refs=(),
        reason_codes=("missing_source",),
        question=question(CHECK_ORDER[1]),
    )
    monkeypatch.setattr(cross_checks, "_execute_cross_check", lambda *args: fresh)

    rerun = rerun_tax_cross_check(
        original,
        CHECK_ORDER[1],
        (),
        None,
        (),
        (),
        CrossCheckContext("2025-01-01", "2025-12-31", "虚构科技有限公司", "CNY"),
        original.source_manifest_sha256,
    )

    history = [
        event for event in rerun.audit_events
        if event.startswith("cross-check-rerun-before:")
    ]
    assert len(history) == 1
    prior = json.loads(history[0].split(":", 1)[1])
    assert prior["status"] == "difference_found"
    assert prior["difference_amount"] == "100.00"


def test_rerun_preserves_closed_explanation_without_reopening_question(monkeypatch):
    import open_tax_check.tax_cross_checks as cross_checks

    bridge = AmountBridgeItem(
        amount=Decimal("100.00"),
        label_zh="申报确认期间差异",
        evidence_refs=("source-1",),
    )
    closed = apply_cross_check_explanation(
        difference_run(),
        explanation(),
        bridge=(bridge,),
        verified_evidence_refs=("source-1",),
    )
    fresh = replace(
        closed.results[1],
        question=question(CHECK_ORDER[1]),
        audit_refs=(),
        resolution_status="unresolved",
        explanations=(),
    )
    monkeypatch.setattr(cross_checks, "_execute_cross_check", lambda *args: fresh)

    rerun = rerun_tax_cross_check(
        closed,
        CHECK_ORDER[1],
        (),
        None,
        (),
        (),
        CrossCheckContext(
            "2025-01-01", "2025-12-31", "虚构科技有限公司", "CNY"
        ),
        closed.source_manifest_sha256,
    )
    target = rerun.results[1]
    assert target.resolution_status == "closed"
    assert target.question is None
    assert not any(item.check_id == CHECK_ORDER[1] for item in rerun.questions)


def test_rerun_clears_explanation_when_check_becomes_matched(monkeypatch):
    import open_tax_check.tax_cross_checks as cross_checks

    pending = apply_cross_check_explanation(
        difference_run(), explanation(), bridge=(), verified_evidence_refs=("source-1",)
    )
    fresh = replace(
        pending.results[1],
        status=CrossCheckStatus.MATCHED,
        ledger_amount=Decimal("100.00"),
        comparison_amount=Decimal("100.00"),
        difference_amount=Decimal("0.00"),
        difference_ratio=Decimal("0"),
        question=None,
        resolution_status="unresolved",
        explanations=(),
    )
    monkeypatch.setattr(cross_checks, "_execute_cross_check", lambda *args: fresh)

    rerun = rerun_tax_cross_check(
        pending,
        CHECK_ORDER[1],
        (),
        None,
        (),
        (),
        CrossCheckContext("2025-01-01", "2025-12-31", "虚构科技有限公司", "CNY"),
        pending.source_manifest_sha256,
    )
    target = rerun.results[1]
    assert target.status is CrossCheckStatus.MATCHED
    assert target.explanations == ()
    assert target.audit_refs == pending.results[1].audit_refs


def test_complete_verified_bridge_can_close_difference():
    bridge = AmountBridgeItem(
        amount=Decimal("100.00"),
        label_zh="申报确认期间差异",
        evidence_refs=("source-1",),
    )

    updated = apply_cross_check_explanation(
        difference_run(),
        explanation(),
        bridge=(bridge,),
        verified_evidence_refs=("source-1",),
    )

    assert updated.results[1].resolution_status == "closed"
    assert updated.results[1].status is CrossCheckStatus.DIFFERENCE_FOUND
    assert updated.results[1].difference_amount == Decimal("100.00")


def test_unverified_bridge_or_wrong_total_cannot_close_difference():
    wrong = AmountBridgeItem(
        amount=Decimal("99.99"),
        label_zh="未完全桥接的期间差异",
        evidence_refs=("source-1",),
    )
    updated = apply_cross_check_explanation(
        difference_run(), explanation(), bridge=(wrong,), verified_evidence_refs=("source-1",)
    )
    assert updated.results[1].resolution_status == "explained_pending_verification"

    with pytest.raises(ValueError, match="unverified_cross_check_evidence"):
        apply_cross_check_explanation(
            difference_run(), explanation(), bridge=(wrong,), verified_evidence_refs=()
        )


def test_cross_check_payload_round_trips_explanations_exactly():
    bridge = AmountBridgeItem(
        amount=Decimal("100.00"),
        label_zh="申报确认期间差异",
        evidence_refs=("source-1",),
    )
    updated = apply_cross_check_explanation(
        difference_run(), explanation(), bridge=(bridge,), verified_evidence_refs=("source-1",)
    )

    assert parse_tax_cross_check_payload(tax_cross_check_payload(updated)) == updated


def test_completed_run_requires_exact_fixed_order_and_is_immutable():
    run = completed_run()

    assert tuple(item.check_id for item in run.results) == CHECK_ORDER
    with pytest.raises(FrozenInstanceError):
        run.status = "blocked_data_quality"


@pytest.mark.parametrize(
    "mutation",
    ["schema", "status", "order", "duplicate", "threshold", "float", "nan"],
)
def test_invalid_completed_contract_is_rejected(mutation):
    run = completed_run()
    values = {
        "schema_version": run.schema_version,
        "status": run.status,
        "results": run.results,
        "questions": run.questions,
        "source_manifest_sha256": run.source_manifest_sha256,
        "audit_events": run.audit_events,
        "blocking_issue_refs": run.blocking_issue_refs,
    }
    if mutation == "schema":
        values["schema_version"] = "tax-cross-checks/v2"
    elif mutation == "status":
        values["status"] = "passed"
    elif mutation == "order":
        values["results"] = tuple(reversed(run.results))
    elif mutation == "duplicate":
        values["results"] = (run.results[0],) * len(CHECK_ORDER)
    else:
        target = run.results[0]
        result_values = {
            field: getattr(target, field)
            for field in target.__dataclass_fields__
        }
        if mutation == "threshold":
            result_values["absolute_tolerance"] = Decimal("11.00")
        elif mutation == "float":
            result_values["ledger_amount"] = 100.0
        else:
            result_values["difference_ratio"] = Decimal("NaN")
        with pytest.raises(ValueError, match="invalid_tax_cross_check"):
            CrossCheckResult(**result_values)
        return

    with pytest.raises(ValueError, match="invalid_tax_cross_check"):
        TaxCrossCheckRun(**values)


def test_blocked_run_must_not_contain_results_or_questions():
    with pytest.raises(ValueError, match="invalid_tax_cross_check"):
        TaxCrossCheckRun(
            schema_version="tax-cross-checks/v1",
            status="blocked_data_quality",
            results=completed_run().results,
            questions=(question(),),
            source_manifest_sha256="a" * 64,
            audit_events=(),
            blocking_issue_refs=("journal_unbalanced",),
        )


def test_not_performed_requires_missing_amounts_and_reason():
    values = {
        field: getattr(result(CHECK_ORDER[0]), field)
        for field in CrossCheckResult.__dataclass_fields__
    }
    values.update(
        status=CrossCheckStatus.NOT_PERFORMED,
        ledger_amount=None,
        comparison_amount=None,
        difference_amount=None,
        difference_ratio=None,
        source_refs=(),
        reason_codes=("missing_financial_statement",),
        question=question(),
    )

    item = CrossCheckResult(**values)
    assert item.status is CrossCheckStatus.NOT_PERFORMED


def test_component_requires_sources_period_and_exact_derived_difference():
    with pytest.raises(ValueError, match="invalid_tax_cross_check"):
        CrossCheckComponent(
            component_id="revenue",
            ledger_amount=Decimal("100.00"),
            comparison_amount=Decimal("90.00"),
            difference_amount=Decimal("9.00"),
            difference_ratio=Decimal("0.10"),
            source_refs=(),
        )


def test_payload_is_stable_json_safe_and_uses_decimal_strings():
    first = tax_cross_check_payload(completed_run())
    second = tax_cross_check_payload(completed_run())

    assert first == second
    assert first["results"][0]["ledger_amount"] == "100.00"
    assert first["results"][0]["difference_ratio"] == "0"
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


def anchor(source_id: str) -> SourceAnchor:
    return SourceAnchor(source_id, 1, None, None, None)


def statement(
    *, revenue: str = "1000.00", total_profit: str = "200.00",
    unit_multiplier: str = "1",
) -> FinancialStatement:
    lines = tuple(
        FinancialStatementLine(
            code,
            name,
            name,
            amount,
            None,
            anchor("statement-source"),
            "customer",
            "confirmed",
        )
        for code, name, amount in (
            ("revenue", "营业收入", revenue),
            ("total_profit", "利润总额", total_profit),
        )
    )
    return FinancialStatement(
        "financial-statement/v1",
        "statement-1",
        "statement-source",
        "示例公司",
        "income_statement",
        "2025-01-01",
        "2025-12-31",
        None,
        "CNY",
        unit_multiplier,
        lines,
        (("revenue", revenue), ("total_profit", total_profit)),
        (),
    )


def tax_return(
    tax_id: str,
    facts: dict[str, str],
    *,
    return_id: str | None = None,
    period_start: str = "2025-01-01",
    period_end: str = "2025-12-31",
    filing_status: str = "filed",
    filed_at: str | None = "2026-01-20T08:00:00+08:00",
    unit_multiplier: str = "1",
) -> TaxReturn:
    lines = tuple(
        TaxReturnLine(
            "A100000" if tax_id == "cit" else "VAT001",
            str(index),
            fact_key,
            fact_key,
            value,
            "decimal",
            anchor(f"{tax_id}-source"),
            "customer",
            "confirmed",
        )
        for index, (fact_key, value) in enumerate(facts.items(), start=1)
    )
    return TaxReturn(
        "tax-return/v1",
        return_id or f"{tax_id}-return",
        f"{tax_id}-source",
        "示例公司",
        tax_id,
        "annual_return",
        period_start,
        period_end,
        filing_status,
        filed_at,
        "CNY",
        unit_multiplier,
        lines,
        (),
        (),
    )


def classifications():
    return parse_account_classification_review(
        {
            "schema_version": "account-classification/v1",
            "source_manifest_sha256": "a" * 64,
            "mapping_fingerprint": "b" * 64,
            "reviewer": "human:Chen Li",
            "confirmed_at": "2026-08-05T09:30:00+08:00",
            "classifications": [
                {"account_code": "6001", "account_name": "主营业务收入", "category": "main_business_revenue"},
                {"account_code": "6051", "account_name": "其他业务收入", "category": "other_business_revenue"},
                {"account_code": "222101", "account_name": "销项税额", "category": "vat_output_tax"},
                {"account_code": "660201", "account_name": "业务招待费", "category": "business_entertainment"},
            ],
        }
    )


def ledger_rows() -> list[dict]:
    def lineage(row: int, column: int) -> dict:
        label = chr(64 + column)
        return {
            "source_id": "ledger-source",
            "sheet_id": "journal",
            "row": row,
            "column": column,
            "coordinate": f"{label}{row}",
            "original_type": "str",
            "value_origin": "source",
            "mapping_version": "mapping/v1",
        }

    values = (
        ("6001", "主营业务收入", "0.00", "1000.00"),
        ("6051", "其他业务收入", "0.00", "0.00"),
        ("222101", "销项税额", "0.00", "130.00"),
        ("660201", "业务招待费", "10.00", "0.00"),
    )
    return [
        {
            "account_code": code,
            "account_name": name,
            "debit": debit,
            "credit": credit,
            "field_lineage": {
                field: lineage(row_number, column)
                for column, field in enumerate(
                    ("account_code", "account_name", "debit", "credit"), start=1
                )
            },
        }
        for row_number, (code, name, debit, credit) in enumerate(values, start=2)
    ]


def context() -> CrossCheckContext:
    return CrossCheckContext(
        period_start="2025-01-01",
        period_end="2025-12-31",
        entity_name="示例公司",
        currency="CNY",
        blocked_issue_refs=(),
    )


def complete_returns() -> tuple[TaxReturn, ...]:
    return (
        tax_return(
            "vat", {"vat.taxable_sales": "250.00", "vat.output_tax": "32.50"},
            return_id="vat-q1", period_start="2025-01-01", period_end="2025-03-31",
        ),
        tax_return(
            "cit",
            {
                "cit.revenue": "1000.00",
                "cit.total_profit": "200.00",
                "cit.business_entertainment.book_amount": "10.00",
                "cit.business_entertainment.tax_amount": "5.00",
            },
        ),
        tax_return(
            "vat", {"vat.taxable_sales": "250.00", "vat.output_tax": "32.50"},
            return_id="vat-q2", period_start="2025-04-01", period_end="2025-06-30",
        ),
        tax_return(
            "vat", {"vat.taxable_sales": "250.00", "vat.output_tax": "32.50"},
            return_id="vat-q3", period_start="2025-07-01", period_end="2025-09-30",
        ),
        tax_return(
            "vat", {"vat.taxable_sales": "250.00", "vat.output_tax": "32.50"},
            return_id="vat-q4", period_start="2025-10-01", period_end="2025-12-31",
        ),
    )


def test_source_extractors_scale_confirmed_values_and_preserve_refs():
    statement_amount = statement_total((statement(unit_multiplier="10000"),), "income_statement", "revenue")
    tax_amount = tax_fact_total(
        (tax_return(
            "vat", {"vat.taxable_sales": "100.00"}, unit_multiplier="10000",
            period_start="2025-01-01", period_end="2025-03-31",
        ),),
        "vat",
        "vat.taxable_sales",
        DateRange("2025-01-01", "2025-03-31"),
    )
    ledger_amount = classified_ledger_total(ledger_rows(), classifications(), "business_entertainment")

    assert statement_amount.amount == Decimal("10000000.00")
    assert tax_amount.amount == Decimal("1000000.00")
    assert ledger_amount.amount == Decimal("10.00")
    assert all(item.source_id for item in statement_amount.source_refs + tax_amount.source_refs + ledger_amount.source_refs)


def test_run_tax_cross_checks_returns_five_matched_results_in_fixed_order():
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), complete_returns(), context(), "a" * 64
    )

    assert run.status == "completed"
    assert tuple(item.check_id for item in run.results) == CHECK_ORDER
    assert all(item.status is CrossCheckStatus.MATCHED for item in run.results)
    assert len(run.results[3].components) == 2
    assert len(run.results[4].components) == 3
    assert run.questions == ()


def test_cross_checks_accept_enterprise_income_tax_structured_identifier():
    returns = list(complete_returns())
    returns[1] = replace(returns[1], tax_id="enterprise_income_tax")

    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )

    assert run.results[3].status is CrossCheckStatus.MATCHED
    assert run.results[4].status is CrossCheckStatus.MATCHED


def test_cross_checks_accept_enterprise_income_tax_fact_keys():
    returns = list(complete_returns())
    cit = returns[1]
    returns[1] = replace(
        cit,
        tax_id="enterprise_income_tax",
        lines=tuple(
            replace(
                line,
                fact_key=(
                    line.fact_key.replace("cit.", "enterprise_income_tax.", 1)
                    if line.fact_key is not None
                    else None
                ),
            )
            for line in cit.lines
        ),
    )

    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )

    assert run.results[3].status is CrossCheckStatus.MATCHED
    assert run.results[4].status is CrossCheckStatus.MATCHED


def test_missing_sources_are_not_performed_and_questions_are_bounded_to_three():
    run = run_tax_cross_checks(
        ledger_rows(), None, (), (), context(), "a" * 64
    )

    assert all(item.status is CrossCheckStatus.NOT_PERFORMED for item in run.results)
    assert len(run.questions) == 3
    assert len({item.question_id for item in run.questions}) == 3
    assert all("漏税" not in item.question_zh and "应补税" not in item.question_zh for item in run.questions)


def test_incomplete_or_overlapping_vat_periods_are_not_summed():
    first = tax_return(
        "vat", {"vat.taxable_sales": "500.00"}, return_id="vat-1",
        period_start="2025-01-01", period_end="2025-06-30",
    )
    gap = tax_return(
        "vat", {"vat.taxable_sales": "500.00"}, return_id="vat-2",
        period_start="2025-07-02", period_end="2025-12-31",
    )
    overlap = tax_return(
        "vat", {"vat.taxable_sales": "500.00"}, return_id="vat-3",
        period_start="2025-06-30", period_end="2025-12-31",
    )
    period = DateRange("2025-01-01", "2025-12-31")

    assert tax_fact_total((first, gap), "vat", "vat.taxable_sales", period) is None
    assert tax_fact_total((first, overlap), "vat", "vat.taxable_sales", period) is None


def test_unambiguous_latest_amended_return_replaces_original():
    original = tax_return(
        "vat", {"vat.taxable_sales": "900.00"}, return_id="original",
        period_start="2025-01-01", period_end="2025-03-31",
    )
    amended = tax_return(
        "vat", {"vat.taxable_sales": "1000.00"}, return_id="amended",
        filing_status="amended", filed_at="2026-02-01T08:00:00+08:00",
        period_start="2025-01-01", period_end="2025-03-31",
    )

    total = tax_fact_total(
        (original, amended), "vat", "vat.taxable_sales",
        DateRange("2025-01-01", "2025-03-31"),
    )
    assert total.amount == Decimal("1000.00")


def test_latest_amendment_missing_requested_fact_does_not_fall_back_to_original():
    original = tax_return(
        "vat", {"vat.taxable_sales": "900.00"}, return_id="original",
        period_start="2025-01-01", period_end="2025-03-31",
    )
    amended = tax_return(
        "vat",
        {"vat.output_tax": "130.00"},
        return_id="amended",
        filing_status="amended",
        filed_at="2026-02-01T08:00:00+08:00",
        period_start="2025-01-01",
        period_end="2025-03-31",
    )

    assert tax_fact_total(
        (original, amended),
        "vat",
        "vat.taxable_sales",
        DateRange("2025-01-01", "2025-03-31"),
    ) is None


def test_vat_requires_complete_monthly_or_quarterly_filing_cadence():
    monthly_periods = (
        ("2025-01-01", "2025-01-31"),
        ("2025-02-01", "2025-02-28"),
        ("2025-03-01", "2025-03-31"),
        ("2025-04-01", "2025-04-30"),
        ("2025-05-01", "2025-05-31"),
        ("2025-06-01", "2025-06-30"),
        ("2025-07-01", "2025-07-31"),
        ("2025-08-01", "2025-08-31"),
        ("2025-09-01", "2025-09-30"),
        ("2025-10-01", "2025-10-31"),
        ("2025-11-01", "2025-11-30"),
        ("2025-12-01", "2025-12-31"),
    )
    quarterly_periods = (
        ("2025-01-01", "2025-03-31"),
        ("2025-04-01", "2025-06-30"),
        ("2025-07-01", "2025-09-30"),
        ("2025-10-01", "2025-12-31"),
    )

    def returns(periods):
        return tuple(
            tax_return(
                "vat",
                {"vat.taxable_sales": "100.00"},
                return_id=f"vat-{index}",
                period_start=start,
                period_end=end,
            )
            for index, (start, end) in enumerate(periods, start=1)
        )

    period = DateRange("2025-01-01", "2025-12-31")
    assert tax_fact_total(
        returns(monthly_periods), "vat", "vat.taxable_sales", period
    ).amount == Decimal("1200.00")
    assert tax_fact_total(
        returns(quarterly_periods), "vat", "vat.taxable_sales", period
    ).amount == Decimal("400.00")
    assert tax_fact_total(
        returns((("2025-01-01", "2025-12-31"),)),
        "vat",
        "vat.taxable_sales",
        period,
    ) is None
    assert tax_fact_total(
        returns(
            (
                ("2025-01-01", "2025-06-30"),
                ("2025-07-01", "2025-12-31"),
            )
        ),
        "vat",
        "vat.taxable_sales",
        period,
    ) is None


def test_cit_components_do_not_cancel_each_other():
    returns = list(complete_returns())
    returns[1] = tax_return(
        "cit",
        {
            "cit.revenue": "1100.00",
            "cit.total_profit": "100.00",
            "cit.business_entertainment.book_amount": "10.00",
            "cit.business_entertainment.tax_amount": "5.00",
        },
    )

    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )
    cit = run.results[3]
    assert cit.ledger_amount == cit.comparison_amount == Decimal("1200.00")
    assert cit.status is CrossCheckStatus.DIFFERENCE_FOUND
    assert cit.difference_amount == Decimal("200.00")


def test_multi_component_closure_requires_an_exact_bridge_for_each_component():
    returns = list(complete_returns())
    returns[1] = tax_return(
        "cit",
        {
            "cit.revenue": "1100.00",
            "cit.total_profit": "100.00",
            "cit.business_entertainment.book_amount": "10.00",
            "cit.business_entertainment.tax_amount": "5.00",
        },
    )
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )
    target = run.results[3]
    explanation = CrossCheckExplanation(
        check_id=target.check_id,
        category="timing_difference",
        explanation_zh="\u5df2\u9010\u9879\u6838\u5bf9\u6536\u5165\u548c\u5229\u6da6\u5dee\u5f02\u3002",
        confirmed_by="human:Chen Li",
        confirmed_at="2026-08-05T10:00:00+08:00",
        evidence_refs=("source-1",),
    )
    cancelling = (
        AmountBridgeItem(
            amount=Decimal("100.00"),
            label_zh="\u6536\u5165\u5dee\u5f02",
            evidence_refs=("source-1",),
        ),
        AmountBridgeItem(
            amount=Decimal("-100.00"),
            label_zh="\u5229\u6da6\u5dee\u5f02",
            evidence_refs=("source-1",),
        ),
    )

    aggregate_only = apply_cross_check_explanation(
        run,
        explanation,
        bridge=cancelling,
        verified_evidence_refs=("source-1",),
    )
    exact = apply_cross_check_explanation(
        run,
        explanation,
        bridge=(
            __import__("dataclasses").replace(cancelling[0], component_id="revenue"),
            __import__("dataclasses").replace(cancelling[1], component_id="total_profit"),
        ),
        verified_evidence_refs=("source-1",),
    )

    assert aggregate_only.results[3].resolution_status == "explained_pending_verification"
    assert exact.results[3].resolution_status == "closed"
    assert parse_tax_cross_check_payload(tax_cross_check_payload(exact)) == exact


def test_business_entertainment_persists_deductible_and_tax_add_back_components():
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), complete_returns(), context(), "a" * 64
    )
    components = {item.component_id: item for item in run.results[4].components}

    assert components["calculated_deductible"].ledger_amount == Decimal("5.00")
    assert components["calculated_tax_add_back"].ledger_amount == Decimal("5.00")
    assert components["calculated_tax_add_back"].comparison_amount == Decimal("5.00")


@pytest.mark.parametrize("check_index", range(5))
def test_each_cross_check_can_surface_a_real_difference(check_index):
    rows = ledger_rows()
    returns = list(complete_returns())
    if check_index == 0:
        rows[0]["credit"] = "1100.00"
    elif check_index == 1:
        returns[0] = tax_return(
            "vat", {"vat.taxable_sales": "150.00", "vat.output_tax": "32.50"},
            return_id="vat-q1", period_start="2025-01-01", period_end="2025-03-31",
        )
    elif check_index == 2:
        rows[2]["credit"] = "150.00"
    elif check_index == 3:
        returns[1] = tax_return(
            "cit",
            {
                "cit.revenue": "900.00",
                "cit.total_profit": "200.00",
                "cit.business_entertainment.book_amount": "10.00",
                "cit.business_entertainment.tax_amount": "5.00",
            },
        )
    else:
        returns[1] = tax_return(
            "cit",
            {
                "cit.revenue": "1000.00",
                "cit.total_profit": "200.00",
                "cit.business_entertainment.book_amount": "20.00",
                "cit.business_entertainment.tax_amount": "5.00",
            },
        )

    run = run_tax_cross_checks(
        rows, classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )
    assert run.results[check_index].status is CrossCheckStatus.DIFFERENCE_FOUND
    assert run.results[check_index].reason_codes == (
        "unbilled_revenue",
        "deemed_sale",
        "non_taxable_or_exempt_revenue",
        "tax_inclusive_conversion",
        "timing_difference",
        "amended_return",
        "book_adjustment",
        "other",
    )


def test_dual_threshold_is_used_by_an_executed_check():
    returns = list(complete_returns())
    returns[0] = tax_return(
        "vat", {"vat.taxable_sales": "250.01", "vat.output_tax": "32.50"},
        return_id="vat-q1", period_start="2025-01-01", period_end="2025-03-31",
    )
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), tuple(returns), context(), "a" * 64
    )
    assert run.results[1].status is CrossCheckStatus.WITHIN_TOLERANCE


@pytest.mark.parametrize("field", ["entity_name", "currency", "unit_multiplier"])
def test_document_context_mismatch_blocks_the_whole_run(field):
    values = {
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "entity_name": "示例公司",
        "currency": "CNY",
        "unit_multiplier": "1",
        "blocked_issue_refs": (),
    }
    values[field] = {
        "entity_name": "另一公司",
        "currency": "USD",
        "unit_multiplier": "10000",
    }[field]
    run = run_tax_cross_checks(
        ledger_rows(), classifications(), (statement(),), complete_returns(),
        CrossCheckContext(**values), "a" * 64,
    )
    assert run.status == "blocked_data_quality"
    assert run.results == () and run.questions == ()
    assert run.blocking_issue_refs


def test_draft_and_ambiguous_amended_returns_are_not_used():
    draft = tax_return(
        "vat", {"vat.taxable_sales": "1000.00"}, filing_status="draft"
    )
    amended_one = tax_return(
        "vat", {"vat.taxable_sales": "1000.00"}, return_id="amended-1",
        filing_status="amended", filed_at="2026-02-01T08:00:00+08:00",
    )
    amended_two = tax_return(
        "vat", {"vat.taxable_sales": "1001.00"}, return_id="amended-2",
        filing_status="amended", filed_at="2026-02-01T08:00:00+08:00",
    )
    period = DateRange("2025-01-01", "2025-12-31")
    assert tax_fact_total((draft,), "vat", "vat.taxable_sales", period) is None
    assert tax_fact_total(
        (amended_one, amended_two), "vat", "vat.taxable_sales", period
    ) is None
