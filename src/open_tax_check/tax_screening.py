"""Deterministic tax-pack screening and customer question aggregation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType

from .tax_models import (
    EvaluationOutcome,
    FactGap,
    FactPrompt,
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
    _EvaluationOutcomeValidationError,
)
from .tax_registry import TAX_DEFINITIONS, select_pack_segments


@dataclass(frozen=True, slots=True)
class TaxEvaluationContext:
    """Immutable inputs supplied to one formal rule evaluator."""

    tax_id: str
    request: TaxCheckRequest
    packs: tuple[TaxPack, ...]
    facts: Mapping[str, TaxFact]


RuleEvaluator = Callable[[TaxEvaluationContext], EvaluationOutcome]

GENERIC_SCREENING_EVALUATOR_ID = "generic.applicability.screening.v1"
_DIAGNOSTIC_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EVIDENCE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _ordered_unique(values: tuple[object, ...]) -> tuple[object, ...]:
    result: list[object] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _selected_pack_details(selected_packs: tuple[TaxPack, ...]) -> tuple[tuple[str, ...], tuple[RuleSource, ...]]:
    pack_ids = tuple(_ordered_unique(tuple(pack.pack_id for pack in selected_packs)))
    sources = tuple(_ordered_unique(tuple(source for pack in selected_packs for source in pack.sources)))
    return pack_ids, sources


def _fact_is_sufficient(
    requirement: FactRequirement,
    fact: TaxFact | None,
    *,
    is_applicability_fact: bool = False,
) -> bool:
    if fact is None or fact.value is None:
        return False
    if is_applicability_fact:
        return isinstance(fact.value, bool) and (
            fact.state is FactState.CONFIRMED
            or (fact.state is FactState.OBSERVED and bool(fact.evidence_refs))
        )
    if not requirement.critical:
        return True
    if fact.state is FactState.CONFIRMED:
        return True
    return fact.state is FactState.OBSERVED and bool(fact.evidence_refs)


def _gaps_for_packs(
    selected_packs: tuple[TaxPack, ...],
    facts: Mapping[str, TaxFact],
    *,
    include_keys: frozenset[str] | None = None,
    exclude_keys: frozenset[str] = frozenset(),
) -> tuple[FactGap, ...]:
    gaps: list[FactGap] = []
    applicability_fact_keys = frozenset(
        fact_key
        for pack in selected_packs
        for fact_key in pack.applicability_fact_keys
    )
    for pack in selected_packs:
        local_parameter_keys = frozenset(pack.local_parameter_keys)
        for requirement in pack.requirements:
            if (
                (include_keys is not None and requirement.fact_key not in include_keys)
                or requirement.fact_key in exclude_keys
            ):
                continue
            fact = facts.get(requirement.fact_key)
            if _fact_is_sufficient(
                requirement,
                fact,
                is_applicability_fact=requirement.fact_key in applicability_fact_keys,
            ):
                continue
            gaps.append(FactGap(
                fact_key=requirement.fact_key,
                reason_code=(
                    "missing_local_policy"
                    if requirement.fact_key in local_parameter_keys
                    else "missing_fact"
                ),
                answer_type=requirement.answer_type,
                evidence_types=requirement.evidence_types,
                priority=requirement.priority,
                blocking=requirement.blocking,
            ))
    return tuple(gaps)


def _confirmation_evidence_reference(fact: TaxFact) -> str:
    digest = hashlib.sha256(
        "\x1f".join((fact.key, fact.confirmed_by or "", fact.confirmed_at or "")).encode("utf-8")
    ).hexdigest()[:16]
    return f"confirmation-{digest}"


def generic_screening_evaluator(context: TaxEvaluationContext) -> EvaluationOutcome:
    """Evaluate evidence-backed boolean applicability without calculating tax."""
    applicability_keys = tuple(_ordered_unique(tuple(
        key for pack in context.packs for key in pack.applicability_fact_keys
    )))
    facts: list[TaxFact] = []
    for key in applicability_keys:
        fact = context.facts.get(key)
        if (
            fact is None
            or not isinstance(fact.value, bool)
            or fact.state is FactState.INFERRED
            or (fact.state is FactState.OBSERVED and not fact.evidence_refs)
        ):
            raise _EvaluationOutcomeValidationError("invalid_applicability_fact")
        facts.append(fact)

    if any(fact.value for fact in facts):
        return EvaluationOutcome(
            TaxStatus.NEEDS_EVIDENCE,
            diagnostics=("calculation_rule_unavailable",),
        )

    evidence_refs = tuple(_ordered_unique(tuple(
        reference
        for fact in facts
        for reference in (
            fact.evidence_refs
            if fact.evidence_refs
            else (_confirmation_evidence_reference(fact),)
        )
    )))
    return EvaluationOutcome(TaxStatus.NOT_APPLICABLE, evidence_refs=evidence_refs)


def builtin_tax_evaluators() -> Mapping[str, RuleEvaluator]:
    """Return immutable built-in evaluator dispatch."""
    return MappingProxyType({GENERIC_SCREENING_EVALUATOR_ID: generic_screening_evaluator})


def _blocked_result(
    tax_id: str,
    diagnostic: str,
    pack_ids: tuple[str, ...] = (),
    sources: tuple[RuleSource, ...] = (),
) -> TaxResult:
    return TaxResult(
        tax_id=tax_id,
        status=TaxStatus.BLOCKED,
        rule_pack_ids=pack_ids,
        diagnostics=(diagnostic,),
        rule_sources=sources,
    )


def _is_safe_evaluator_outcome(
    outcome: object, selected_packs: tuple[TaxPack, ...]
) -> bool:
    screening_incomplete = (
        isinstance(outcome, EvaluationOutcome)
        and outcome.status is TaxStatus.NEEDS_EVIDENCE
        and bool(selected_packs)
        and all(pack.maturity is PackMaturity.SCREENING for pack in selected_packs)
        and outcome.diagnostics == ("calculation_rule_unavailable",)
        and not outcome.evidence_refs
    )
    return (
        isinstance(outcome, EvaluationOutcome)
        and (
            outcome.status is TaxStatus.NOT_APPLICABLE
            or (
                outcome.status is TaxStatus.CALCULATED
                and not any(pack.maturity is PackMaturity.SCREENING for pack in selected_packs)
            )
            or screening_incomplete
        )
        and (outcome.status is not TaxStatus.NOT_APPLICABLE or bool(outcome.evidence_refs))
        and all(
            isinstance(diagnostic, str) and _DIAGNOSTIC_CODE.fullmatch(diagnostic)
            for diagnostic in outcome.diagnostics
        )
        and all(
            isinstance(reference, str) and _EVIDENCE_REFERENCE.fullmatch(reference)
            for reference in outcome.evidence_refs
        )
    )


def _screen_one_tax(
    tax_id: str,
    request: TaxCheckRequest,
    packs: tuple[TaxPack, ...],
    evaluators: Mapping[str, RuleEvaluator],
    facts: Mapping[str, TaxFact],
) -> TaxResult:
    selection = select_pack_segments(
        packs,
        tax_id=tax_id,
        jurisdiction=request.jurisdiction,
        period=request.period,
    )
    selected_packs = tuple(segment.pack for segment in selection.segments)
    pack_ids, sources = _selected_pack_details(selected_packs)
    if selection.uncovered_periods:
        return _blocked_result(tax_id, "rule_pack_unavailable", pack_ids, sources)

    applicability_keys = frozenset(
        key for pack in selected_packs for key in pack.applicability_fact_keys
    )
    if applicability_keys:
        applicability_gaps = _gaps_for_packs(
            selected_packs, facts, include_keys=applicability_keys
        )
        if applicability_gaps:
            return TaxResult(
                tax_id,
                TaxStatus.NEEDS_EVIDENCE,
                pack_ids,
                gaps=applicability_gaps,
                rule_sources=sources,
            )

    all_applicability_false = bool(applicability_keys) and all(
        facts[key].value is False for key in applicability_keys
    )
    gaps = (
        ()
        if all_applicability_false
        else _gaps_for_packs(selected_packs, facts, exclude_keys=applicability_keys)
    )
    if any(gap.reason_code == "missing_local_policy" for gap in gaps):
        return TaxResult(
            tax_id, TaxStatus.NEEDS_LOCAL_POLICY, pack_ids, gaps=gaps, rule_sources=sources
        )
    if gaps:
        return TaxResult(tax_id, TaxStatus.NEEDS_EVIDENCE, pack_ids, gaps=gaps, rule_sources=sources)

    evaluator_ids = tuple(_ordered_unique(tuple(pack.evaluator_id for pack in selected_packs)))
    if len(evaluator_ids) != 1:
        return _blocked_result(tax_id, "inconsistent_rule_evaluators", pack_ids, sources)
    if not evaluator_ids or any(evaluator_id is None or evaluator_id not in evaluators for evaluator_id in evaluator_ids):
        return _blocked_result(tax_id, "rule_evaluator_unavailable", pack_ids, sources)

    evaluator = evaluators[evaluator_ids[0]]
    context = TaxEvaluationContext(tax_id, request, selected_packs, facts)
    try:
        outcome = evaluator(context)
    except _EvaluationOutcomeValidationError:
        return _blocked_result(tax_id, "invalid_evaluator_outcome", pack_ids, sources)
    except Exception:
        return _blocked_result(tax_id, "rule_evaluator_failed", pack_ids, sources)
    if not _is_safe_evaluator_outcome(outcome, selected_packs):
        return _blocked_result(tax_id, "invalid_evaluator_outcome", pack_ids, sources)
    return TaxResult(
        tax_id=tax_id,
        status=outcome.status,
        rule_pack_ids=pack_ids,
        diagnostics=outcome.diagnostics,
        evidence_refs=outcome.evidence_refs,
        rule_sources=sources,
    )


def screen_tax_items(
    request: TaxCheckRequest,
    packs: tuple[TaxPack, ...],
    evaluators: Mapping[str, RuleEvaluator],
    tax_ids: tuple[str, ...] | None = None,
) -> tuple[TaxResult, ...]:
    """Screen selected taxes using formal packs only, in stable registry order."""
    selected_tax_ids = (
        tuple(item.tax_id for item in TAX_DEFINITIONS) if tax_ids is None else tuple(tax_ids)
    )
    facts: Mapping[str, TaxFact] = MappingProxyType({fact.key: fact for fact in request.facts})
    return tuple(
        _screen_one_tax(tax_id, request, tuple(packs), evaluators, facts)
        for tax_id in selected_tax_ids
    )


def build_question_queue(
    results: tuple[TaxResult, ...],
    prompts: Mapping[str, FactPrompt] | None = None,
) -> tuple[Question, ...]:
    """Merge all unresolved fact gaps into a stable, one-question-per-fact queue."""
    grouped: dict[str, list[tuple[str, FactGap]]] = {}
    for result in results:
        for gap in result.gaps:
            grouped.setdefault(gap.fact_key, []).append((result.tax_id, gap))

    questions: list[Question] = []
    for fact_key, entries in grouped.items():
        tax_ids = tuple(sorted({tax_id for tax_id, _ in entries}))
        gaps = tuple(gap for _, gap in entries)
        answer_types = {gap.answer_type for gap in gaps}
        if len(answer_types) != 1:
            raise ValueError("incompatible_question_answer_types")
        prompt = (prompts or {}).get(fact_key)
        if prompt is None and prompts is not None and any(
            pack_id.endswith("-screening") or "-screening-" in pack_id
            for tax_id, _ in entries
            for result in results
            if result.tax_id == tax_id
            for pack_id in result.rule_pack_ids
        ):
            raise ValueError("missing_fact_prompt")
        if prompt is not None and prompt.answer_type != next(iter(answer_types)):
            raise ValueError("incompatible_prompt_answer_type")
        questions.append(Question(
            question_id="question-" + hashlib.sha256(fact_key.encode("utf-8")).hexdigest()[:16],
            fact_key=fact_key,
            tax_ids=tax_ids,
            reason_codes=tuple(sorted({gap.reason_code for gap in gaps})),
            answer_type=next(iter(answer_types)),
            evidence_types=tuple(sorted({item for gap in gaps for item in gap.evidence_types})),
            priority=max(gap.priority for gap in gaps),
            blocking=any(gap.blocking for gap in gaps),
            question_zh=prompt.question_zh if prompt is not None else "",
            help_zh=prompt.help_zh if prompt is not None else "",
            evidence_help_zh=prompt.evidence_help_zh if prompt is not None else "",
            choices=prompt.choices if prompt is not None else (),
        ))
    return tuple(sorted(questions, key=lambda question: (-question.priority, question.fact_key)))
