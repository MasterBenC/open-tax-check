"""Model-independent public façade for tax-health-check workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from .tax_models import Confirmation, TaxCheckRequest, TaxCheckSnapshot, TaxPack
from .tax_prompts import load_builtin_fact_catalog
from .tax_registry import load_builtin_tax_packs
from .tax_screening import (
    RuleEvaluator,
    builtin_tax_evaluators,
)
from .tax_workflow import apply_confirmations, start_tax_health_check


def _resolved_evaluators(
    packs: tuple[TaxPack, ...] | None,
    evaluators: Mapping[str, RuleEvaluator] | None,
) -> Mapping[str, RuleEvaluator]:
    if evaluators is not None:
        return evaluators
    if packs is None:
        return builtin_tax_evaluators()
    return {}


def run_tax_health_check(
    request: TaxCheckRequest,
    *,
    packs: tuple[TaxPack, ...] | None = None,
    evaluators: Mapping[str, RuleEvaluator] | None = None,
    occurred_at: str | None = None,
) -> TaxCheckSnapshot:
    """Start a deterministic check without requiring an Agent or model provider."""
    return start_tax_health_check(
        request,
        packs=load_builtin_tax_packs() if packs is None else tuple(packs),
        evaluators=_resolved_evaluators(packs, evaluators),
        prompts=load_builtin_fact_catalog() if packs is None else {},
        occurred_at=datetime.now(timezone.utc).isoformat() if occurred_at is None else occurred_at,
    )


def continue_tax_health_check(
    snapshot: TaxCheckSnapshot,
    confirmations: tuple[Confirmation, ...],
    *,
    packs: tuple[TaxPack, ...] | None = None,
    evaluators: Mapping[str, RuleEvaluator] | None = None,
) -> TaxCheckSnapshot:
    """Apply customer confirmations using the supplied or bundled formal packs."""
    return apply_confirmations(
        snapshot,
        tuple(confirmations),
        packs=load_builtin_tax_packs() if packs is None else tuple(packs),
        evaluators=_resolved_evaluators(packs, evaluators),
        prompts=load_builtin_fact_catalog() if packs is None else {},
    )
