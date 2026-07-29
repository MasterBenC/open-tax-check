from datetime import date

import pytest

from open_tax_check.agent_orchestration import (
    build_agent_question_tasks,
    confirmation_from_task,
    task_payload,
)
from open_tax_check.tax_models import (
    DateRange,
    Question,
    TaxCheckRequest,
    TaxCheckSnapshot,
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


def test_build_agent_question_tasks_is_priority_ordered_and_bounded():
    tasks = build_agent_question_tasks(snapshot_with_questions(), max_questions=1)

    assert len(tasks) == 1
    assert tasks[0].question_id == "question-a"
    assert tasks[0].task_id == "agent-task-question-a"
    assert task_payload(tasks[0])["question_zh"] == "是否有 A？"


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
