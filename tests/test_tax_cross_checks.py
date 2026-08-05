import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

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
    TaxCrossCheckRun,
    compare_amounts,
    tax_cross_check_payload,
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
