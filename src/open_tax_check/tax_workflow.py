"""Immutable customer-confirmation workflow for tax health-check snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from .tax_models import (
    AuditEvent,
    Confirmation,
    FactGap,
    FactPrompt,
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
from .tax_screening import RuleEvaluator, build_question_queue, screen_tax_items


class ConfirmationError(ValueError):
    """A public, stable confirmation error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _confirmation_digest(value: str | int | bool | None) -> str:
    canonical_value = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical_value.encode("utf-8")).hexdigest()


def _confirmation_value_is_valid(answer_type: str, value: object) -> bool:
    if answer_type in ("text", "choice"):
        return isinstance(value, str) and bool(value)
    if answer_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if answer_type == "boolean":
        return isinstance(value, bool)
    if answer_type == "date":
        if (
            not isinstance(value, str)
            or _ISO_DATE.fullmatch(value) is None
        ):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if answer_type == "decimal":
        if not isinstance(value, str) or not value:
            return False
        try:
            Decimal(value)
        except InvalidOperation:
            return False
        return True
    return False


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


def _question_history(snapshot: TaxCheckSnapshot) -> tuple[Question, ...]:
    """Preserve original question records before removing resolved questions."""
    by_id = {question.question_id: question for question in snapshot.question_history}
    for question in snapshot.questions:
        by_id.setdefault(question.question_id, question)
    return tuple(by_id.values())


def _known_questions(snapshot: TaxCheckSnapshot) -> Mapping[str, Question]:
    return {question.question_id: question for question in _question_history(snapshot)}


def _has_recorded_conflict(
    snapshot: TaxCheckSnapshot, fact_key: str, digest: str
) -> bool:
    return any(
        event.event_type == "confirmation_conflict"
        and event.fact_keys == (fact_key,)
        and event.confirmation_digest == digest
        for event in snapshot.audit_events
    )


def _active_conflict_fact_keys(snapshot: TaxCheckSnapshot) -> frozenset[str]:
    return frozenset(
        question.fact_key
        for question in snapshot.questions
        if "confirmation_conflict" in question.reason_codes
    )


def _merge_evidence_refs(
    existing: tuple[str, ...], supplemental: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(existing + supplemental))


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
    prompts: Mapping[str, FactPrompt] | None = None,
    occurred_at: str,
) -> TaxCheckSnapshot:
    """Create revision one without accessing a system clock or source files."""
    _validate_timestamp(occurred_at)
    results = screen_tax_items(request, packs, evaluators)
    questions = build_question_queue(results, prompts)
    return TaxCheckSnapshot(
        request=request,
        results=results,
        questions=questions,
        audit_events=(AuditEvent("check_started", occurred_at),),
        revision=1,
        question_history=questions,
    )


def apply_confirmations(
    snapshot: TaxCheckSnapshot,
    confirmations: tuple[Confirmation, ...],
    *,
    packs: tuple[TaxPack, ...],
    evaluators: Mapping[str, RuleEvaluator],
    prompts: Mapping[str, FactPrompt] | None = None,
) -> TaxCheckSnapshot:
    """Apply a validated customer batch, rerunning only materially affected taxes."""
    questions = _known_questions(snapshot)
    resolved: list[tuple[Confirmation, Question]] = []
    for confirmation in confirmations:
        question = questions.get(confirmation.question_id)
        if question is None:
            raise ConfirmationError("unknown_question")
        if question.fact_key != confirmation.fact_key:
            raise ConfirmationError("confirmation_fact_mismatch")
        if not _confirmation_value_is_valid(question.answer_type, confirmation.value):
            raise ConfirmationError("invalid_confirmation_value")
        resolved.append((confirmation, question))

    facts = {fact.key: fact for fact in snapshot.request.facts}
    active_conflicts = _active_conflict_fact_keys(snapshot)
    pending: list[tuple[Confirmation, Question]] = []
    for confirmation, question in resolved:
        existing = facts.get(confirmation.fact_key)
        digest = _confirmation_digest(confirmation.value)
        if (
            existing is not None
            and existing.state is FactState.CONFIRMED
            and existing.value != confirmation.value
            and _has_recorded_conflict(snapshot, question.fact_key, digest)
        ):
            continue
        pending.append((confirmation, question))

    changed_tax_ids: list[str] = []
    conflicts: list[tuple[Confirmation, Question]] = []
    audit_events: list[AuditEvent] = []
    for confirmation, question in pending:
        existing = facts.get(confirmation.fact_key)
        digest = _confirmation_digest(confirmation.value)
        if existing is not None and existing.state is FactState.CONFIRMED:
            if existing.value == confirmation.value:
                active_conflict = confirmation.fact_key in active_conflicts
                exact_replay = (
                    existing.confirmed_by == confirmation.confirmed_by
                    and existing.confirmed_at == confirmation.confirmed_at
                    and existing.evidence_refs == confirmation.evidence_refs
                )
                if exact_replay and not active_conflict:
                    continue
                facts[confirmation.fact_key] = TaxFact(
                    key=confirmation.fact_key,
                    value=existing.value,
                    state=FactState.CONFIRMED,
                    evidence_refs=_merge_evidence_refs(
                        existing.evidence_refs,
                        confirmation.evidence_refs,
                    ),
                    confirmed_by=confirmation.confirmed_by,
                    confirmed_at=confirmation.confirmed_at,
                )
                changed_tax_ids.extend(question.tax_ids)
                audit_events.append(AuditEvent(
                    (
                        "confirmation_reaffirmed"
                        if active_conflict
                        else "confirmation_updated"
                    ),
                    confirmation.confirmed_at,
                    tax_ids=question.tax_ids,
                    fact_keys=(confirmation.fact_key,),
                    confirmation_digest=digest,
                ))
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
        questions=build_question_queue(results, prompts),
        audit_events=snapshot.audit_events + tuple(audit_events),
        revision=snapshot.revision + 1,
        question_history=_question_history(snapshot),
    )
