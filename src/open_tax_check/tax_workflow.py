"""Immutable customer-confirmation workflow for tax health-check snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .tax_models import (
    AuditEvent,
    Confirmation,
    FactGap,
    FactState,
    Question,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
    TaxPack,
    TaxResult,
    TaxStatus,
    _validate_timestamp,
)
from .tax_registry import select_pack_segments
from .tax_screening import RuleEvaluator, build_question_queue, screen_tax_items


class ConfirmationError(ValueError):
    """A public, stable confirmation error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _confirmation_digest(value: str | int | bool | None) -> str:
    canonical_value = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical_value.encode("utf-8")).hexdigest()


def _updated_request(request: TaxCheckRequest, facts: Mapping[str, TaxFact]) -> TaxCheckRequest:
    ordered_facts = tuple(
        facts[fact.key] for fact in request.facts
    ) + tuple(
        fact for key, fact in facts.items() if key not in {item.key for item in request.facts}
    )
    return TaxCheckRequest(
        period=request.period,
        jurisdiction=request.jurisdiction,
        facts=ordered_facts,
        input_hashes=request.input_hashes,
    )


def _previous_question_tax_ids(snapshot: TaxCheckSnapshot, fact_key: str) -> tuple[str, ...]:
    for event in reversed(snapshot.audit_events):
        if event.event_type == "fact_confirmed" and event.fact_keys == (fact_key,):
            return event.tax_ids
    return ()


def _previous_question(
    snapshot: TaxCheckSnapshot,
    fact_key: str,
    tax_ids: tuple[str, ...],
    packs: tuple[TaxPack, ...],
) -> Question | None:
    """Reconstruct stable question metadata without retaining customer answers."""
    requirements = []
    for tax_id in tax_ids:
        selection = select_pack_segments(
            packs,
            tax_id=tax_id,
            jurisdiction=snapshot.request.jurisdiction,
            period=snapshot.request.period,
        )
        requirements.extend(
            requirement
            for segment in selection.segments
            for requirement in segment.pack.requirements
            if requirement.fact_key == fact_key
        )
    if not requirements:
        return None
    return Question(
        question_id="question-" + hashlib.sha256(fact_key.encode("utf-8")).hexdigest()[:16],
        fact_key=fact_key,
        tax_ids=tax_ids,
        reason_codes=("previously_confirmed",),
        answer_type=min(item.answer_type for item in requirements),
        evidence_types=tuple(sorted({evidence for item in requirements for evidence in item.evidence_types})),
        priority=max(item.priority for item in requirements),
        blocking=any(item.blocking for item in requirements),
    )


def _known_confirmation_question(
    snapshot: TaxCheckSnapshot,
    confirmation: Confirmation,
    questions: Mapping[str, Question],
    packs: tuple[TaxPack, ...],
) -> Question | None:
    question = questions.get(confirmation.question_id)
    if question is not None:
        return question
    fact = next((item for item in snapshot.request.facts if item.key == confirmation.fact_key), None)
    stable_id = "question-" + hashlib.sha256(confirmation.fact_key.encode("utf-8")).hexdigest()[:16]
    if fact is not None and fact.state is FactState.CONFIRMED and confirmation.question_id == stable_id:
        tax_ids = _previous_question_tax_ids(snapshot, confirmation.fact_key)
        return _previous_question(snapshot, confirmation.fact_key, tax_ids, packs)
    return None


def _merge_results(
    results: tuple[TaxResult, ...],
    replacements: tuple[TaxResult, ...],
) -> tuple[TaxResult, ...]:
    by_tax_id = {item.tax_id: item for item in replacements}
    return tuple(by_tax_id.get(item.tax_id, item) for item in results)


def _conflict_result(result: TaxResult, question: Question) -> TaxResult:
    conflict_gap = FactGap(
        fact_key=question.fact_key,
        reason_code="confirmation_conflict",
        answer_type=question.answer_type,
        evidence_types=question.evidence_types,
        priority=question.priority,
        blocking=question.blocking,
    )
    other_gaps = tuple(gap for gap in result.gaps if gap.fact_key != question.fact_key)
    return TaxResult(
        tax_id=result.tax_id,
        status=TaxStatus.BLOCKED,
        rule_pack_ids=result.rule_pack_ids,
        diagnostics=("confirmation_conflict",),
        gaps=other_gaps + (conflict_gap,),
        evidence_refs=result.evidence_refs,
        rule_sources=result.rule_sources,
    )


def start_tax_health_check(
    request: TaxCheckRequest,
    *,
    packs: tuple[TaxPack, ...],
    evaluators: Mapping[str, RuleEvaluator],
    occurred_at: str,
) -> TaxCheckSnapshot:
    """Create revision one without accessing a system clock or source files."""
    _validate_timestamp(occurred_at)
    results = screen_tax_items(request, packs, evaluators)
    return TaxCheckSnapshot(
        request=request,
        results=results,
        questions=build_question_queue(results),
        audit_events=(AuditEvent("check_started", occurred_at),),
        revision=1,
    )


def apply_confirmations(
    snapshot: TaxCheckSnapshot,
    confirmations: tuple[Confirmation, ...],
    *,
    packs: tuple[TaxPack, ...],
    evaluators: Mapping[str, RuleEvaluator],
) -> TaxCheckSnapshot:
    """Apply a validated customer batch, rerunning only materially affected taxes."""
    questions = {question.question_id: question for question in snapshot.questions}
    resolved: list[tuple[Confirmation, Question]] = []
    for confirmation in confirmations:
        question = _known_confirmation_question(snapshot, confirmation, questions, packs)
        if question is None:
            raise ConfirmationError("unknown_question")
        if question.fact_key != confirmation.fact_key:
            raise ConfirmationError("confirmation_fact_mismatch")
        resolved.append((confirmation, question))

    facts = {fact.key: fact for fact in snapshot.request.facts}
    changed_tax_ids: list[str] = []
    conflicts: list[tuple[Confirmation, Question]] = []
    audit_events: list[AuditEvent] = []
    for confirmation, question in resolved:
        existing = facts.get(confirmation.fact_key)
        digest = _confirmation_digest(confirmation.value)
        if existing is not None and existing.state is FactState.CONFIRMED:
            if existing.value == confirmation.value:
                continue
            conflicts.append((confirmation, question))
            audit_events.append(AuditEvent(
                "confirmation_conflict", confirmation.confirmed_at,
                tax_ids=question.tax_ids, fact_keys=(confirmation.fact_key,),
                confirmation_digest=digest,
            ))
            continue
        facts[confirmation.fact_key] = TaxFact(
            key=confirmation.fact_key,
            value=confirmation.value,
            state=FactState.CONFIRMED,
            evidence_refs=confirmation.evidence_refs,
            confirmed_by=confirmation.confirmed_by,
            confirmed_at=confirmation.confirmed_at,
        )
        changed_tax_ids.extend(question.tax_ids)
        audit_events.append(AuditEvent(
            "fact_confirmed", confirmation.confirmed_at,
            tax_ids=question.tax_ids, fact_keys=(confirmation.fact_key,),
            confirmation_digest=digest,
        ))

    if not audit_events:
        return snapshot

    request = _updated_request(snapshot.request, facts)
    affected_tax_ids = tuple(dict.fromkeys(changed_tax_ids))
    results = snapshot.results
    if affected_tax_ids:
        results = _merge_results(
            results,
            screen_tax_items(request, packs, evaluators, tax_ids=affected_tax_ids),
        )
    for _, question in conflicts:
        existing_results = {result.tax_id: result for result in results}
        replacements = tuple(
            _conflict_result(existing_results[tax_id], question)
            for tax_id in question.tax_ids
        )
        results = _merge_results(results, replacements)

    return TaxCheckSnapshot(
        request=request,
        results=results,
        questions=build_question_queue(results),
        audit_events=snapshot.audit_events + tuple(audit_events),
        revision=snapshot.revision + 1,
    )
