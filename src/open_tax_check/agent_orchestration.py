"""Deterministic host-Agent interaction envelopes for tax-health questions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .evidence_bundle import EvidenceBundleResult
from .tax_models import Confirmation, PromptChoice, Question, TaxCheckSnapshot


@dataclass(frozen=True, slots=True)
class AgentQuestionTask:
    task_id: str
    question_id: str
    fact_key: str
    tax_ids: tuple[str, ...]
    question_zh: str
    help_zh: str
    evidence_help_zh: str
    evidence_types: tuple[str, ...]
    answer_type: str
    choices: tuple[PromptChoice, ...]
    priority: int
    blocking: bool


def evidence_bundle_delivery_payload(
    bundle: EvidenceBundleResult | None,
) -> dict[str, object]:
    """Return the common allowlisted evidence-bundle delivery envelope."""
    if bundle is None:
        return {
            "evidence_bundle_status": None,
            "evidence_bundle_path": None,
            "evidence_bundle_sha256": None,
            "includes_evidence_files": False,
        }
    return {
        "evidence_bundle_status": "verified",
        "evidence_bundle_path": str(bundle.path),
        "evidence_bundle_sha256": bundle.sha256,
        "includes_evidence_files": bundle.includes_evidence_files,
    }


def build_agent_question_tasks(
    snapshot: TaxCheckSnapshot,
    *,
    max_questions: int | None = None,
) -> tuple[AgentQuestionTask, ...]:
    """Convert active unresolved questions into a bounded, stable host queue."""
    if max_questions is not None and (
        not isinstance(max_questions, int) or isinstance(max_questions, bool) or max_questions < 0
    ):
        raise ValueError("invalid_question_budget")
    ordered_questions = tuple(sorted(snapshot.questions, key=lambda question: (-question.priority, question.fact_key)))
    questions = ordered_questions[:max_questions] if max_questions is not None else ordered_questions
    return tuple(
        AgentQuestionTask(
            task_id=f"agent-task-{question.question_id}",
            question_id=question.question_id,
            fact_key=question.fact_key,
            tax_ids=question.tax_ids,
            question_zh=question.question_zh,
            help_zh=question.help_zh,
            evidence_help_zh=question.evidence_help_zh,
            evidence_types=question.evidence_types,
            answer_type=question.answer_type,
            choices=question.choices,
            priority=question.priority,
            blocking=question.blocking,
        )
        for question in questions
    )


def task_payload(task: AgentQuestionTask) -> dict[str, object]:
    """Return the allowlisted JSON envelope a host Agent may present or store."""
    payload = asdict(task)
    payload["choices"] = [asdict(choice) for choice in task.choices]
    return payload


def _value_matches(task: AgentQuestionTask, value: object) -> bool:
    if task.answer_type == "choice":
        return isinstance(value, str) and bool(value) and (
            not task.choices or value in {choice.value for choice in task.choices}
        )
    if task.answer_type == "text":
        return isinstance(value, str) and bool(value)
    if task.answer_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if task.answer_type == "boolean":
        return isinstance(value, bool)
    if task.answer_type == "date":
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if task.answer_type == "decimal":
        if not isinstance(value, str) or not value:
            return False
        try:
            Decimal(value)
        except InvalidOperation:
            return False
        return True
    return False


def confirmation_from_task(
    task: AgentQuestionTask,
    value: str | int | bool | None,
    *,
    confirmed_by: str,
    confirmed_at: str,
    evidence_refs: tuple[str, ...] = (),
) -> Confirmation:
    """Create a validated confirmation without allowing the host to change identity."""
    if not _value_matches(task, value):
        raise ValueError("invalid_confirmation_value")
    return Confirmation(
        question_id=task.question_id,
        fact_key=task.fact_key,
        value=value,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
        evidence_refs=evidence_refs,
    )
