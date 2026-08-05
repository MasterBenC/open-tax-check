from datetime import date
from pathlib import Path

import pytest

from open_tax_check.agent_orchestration import (
    build_agent_question_tasks,
    build_cross_check_question_tasks,
    confirmation_from_task,
    evidence_bundle_delivery_payload,
    task_payload,
)
from test_tax_cross_checks import difference_run
from open_tax_check.evidence_bundle import EvidenceBundleResult
from open_tax_check.tax_health import run_tax_health_check
from open_tax_check.tax_models import (
    DateRange,
    FactState,
    Question,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
)


def snapshot_with_questions():
    questions = (
        Question(
            "question-b", "entity.b", ("vat",), ("missing_fact",), "boolean", ("client_confirmation",), 40, False,
            "是否有 B？", "请确认 B。", "提供 B 的资料。",
        ),
        Question(
            "question-a", "entity.a", ("vat", "stamp_tax"), ("missing_fact",), "choice", ("contract",), 90, True,
            "是否有 A？", "请确认 A。", "提供 A 合同。",
        ),
    )
    return TaxCheckSnapshot(
        request=TaxCheckRequest(DateRange(date(2026, 1, 1), date(2026, 12, 31)), "CN"),
        results=(), questions=questions, audit_events=(), revision=2,
    )


def test_evidence_bundle_delivery_payload_has_one_allowlisted_contract_for_all_hosts():
    bundle = EvidenceBundleResult(
        path=Path("output") / "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u8bc1\u636e\u5305.zip",
        sha256="a" * 64,
        includes_evidence_files=False,
    )

    assert evidence_bundle_delivery_payload(bundle) == {
        "evidence_bundle_status": "verified",
        "evidence_bundle_path": str(bundle.path),
        "evidence_bundle_sha256": "a" * 64,
        "includes_evidence_files": False,
    }


def test_evidence_bundle_delivery_payload_is_backward_compatible_when_disabled():
    assert evidence_bundle_delivery_payload(None) == {
        "evidence_bundle_status": None,
        "evidence_bundle_path": None,
        "evidence_bundle_sha256": None,
        "includes_evidence_files": False,
    }


def test_build_agent_question_tasks_is_priority_ordered_and_bounded():
    tasks = build_agent_question_tasks(snapshot_with_questions(), max_questions=1)

    assert len(tasks) == 1
    assert tasks[0].question_id == "question-a"
    assert tasks[0].task_id == "agent-task-question-a"
    assert task_payload(tasks[0])["question_zh"] == "是否有 A？"


def test_cross_check_agent_envelope_is_bounded_and_allowlisted():
    tasks = build_cross_check_question_tasks(difference_run(), max_questions=3)

    assert len(tasks) == 1
    payload = tasks[0]
    assert payload["check_id"] == "FS-VAT-SALES-001"
    assert payload["allowed_explanation_categories"] == [
        "unbilled_revenue",
        "deemed_sale",
        "non_taxable_or_exempt_revenue",
        "tax_inclusive_conversion",
        "timing_difference",
        "amended_return",
        "book_adjustment",
        "other",
    ]
    assert set(payload) == {
        "task_id",
        "question_id",
        "check_id",
        "question_zh",
        "help_zh",
        "allowed_explanation_categories",
        "required_evidence_types",
        "source_labels",
    }
    serialized = __import__("json").dumps(payload, ensure_ascii=False)
    assert "absolute_tolerance" not in serialized
    assert "filesystem" not in serialized and "source_manifest_sha256" not in serialized


def test_confirmation_from_task_preserves_identity_and_evidence():
    task = build_agent_question_tasks(snapshot_with_questions())[1]

    confirmation = confirmation_from_task(
        task, True, confirmed_by="customer", confirmed_at="2026-07-29T10:00:00+08:00", evidence_refs=("file:a",)
    )

    assert confirmation.question_id == task.question_id
    assert confirmation.fact_key == task.fact_key
    assert confirmation.evidence_refs == ("file:a",)


def test_confirmation_from_task_rejects_mismatched_task_type():
    task = build_agent_question_tasks(snapshot_with_questions())[0]

    with pytest.raises(ValueError, match="invalid_confirmation_value"):
        confirmation_from_task(task, object(), confirmed_by="customer", confirmed_at="2026-07-29T10:00:00+08:00")


def test_builtin_stamp_tax_question_is_customer_safe_for_host_agents():
    snapshot = run_tax_health_check(TaxCheckRequest(
        DateRange(date(2026, 1, 1), date(2026, 12, 31)),
        "CN",
        facts=(
            TaxFact(
                "activity.has_stamp_taxable_instrument_or_securities_transaction",
                True,
                FactState.CONFIRMED,
                ("contract-register",),
                "customer",
                "2026-07-29T10:00:00+08:00",
            ),
        ),
    ))

    task = next(
        item for item in build_agent_question_tasks(snapshot)
        if item.fact_key == "stamp_tax.tax_item"
    )
    payload = task_payload(task)
    assert payload["question_zh"] == "\u672c\u6761\u5370\u82b1\u7a0e\u8ba1\u7b97\u5bf9\u5e94\u54ea\u4e2a\u6cd5\u5b9a\u7a0e\u76ee\uff1f"
    assert "\u4e0d\u5f97\u7531AI\u81ea\u52a8\u5224\u65ad" in payload["help_zh"]
    assert {"value": "sale_contract", "label_zh": "\u4e70\u5356\u5408\u540c"} in payload["choices"]
