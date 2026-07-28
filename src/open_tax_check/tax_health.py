"""Model-independent public façade for tax-health-check workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from .tax_models import Confirmation, TaxCheckRequest, TaxCheckSnapshot, TaxPack
from .tax_registry import load_builtin_tax_packs
from .tax_screening import RuleEvaluator
from .tax_workflow import apply_confirmations, start_tax_health_check


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
        evaluators={} if evaluators is None else evaluators,
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
        evaluators={} if evaluators is None else evaluators,
    )
