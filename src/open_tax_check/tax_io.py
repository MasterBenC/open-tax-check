"""Strict JSON conversion for the model-independent tax-health request."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from .schemas import to_dict
from .tax_models import DateRange, FactState, TaxCheckRequest, TaxFact


_REQUEST_FIELDS = frozenset({"period", "jurisdiction", "facts", "input_hashes"})
_PERIOD_FIELDS = frozenset({"start", "end"})
_FACT_FIELDS = frozenset({"key", "value", "state", "evidence_refs", "confirmed_by", "confirmed_at"})


def tax_request_from_dict(payload: Mapping[str, object]) -> TaxCheckRequest:
    if not isinstance(payload, Mapping) or set(payload) != _REQUEST_FIELDS:
        raise ValueError("invalid_tax_health_request")
    period_payload = payload["period"]
    if not isinstance(period_payload, Mapping) or set(period_payload) != _PERIOD_FIELDS:
        raise ValueError("invalid_tax_health_request_period")
    try:
        period = DateRange(date.fromisoformat(str(period_payload["start"])), date.fromisoformat(str(period_payload["end"])))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_tax_health_request_period") from error
    raw_facts = payload["facts"]
    if not isinstance(raw_facts, list):
        raise ValueError("invalid_tax_health_request_facts")
    facts = []
    for raw_fact in raw_facts:
        if not isinstance(raw_fact, Mapping) or set(raw_fact) != _FACT_FIELDS:
            raise ValueError("invalid_tax_health_request_fact")
        try:
            facts.append(
                TaxFact(
                    key=raw_fact["key"],
                    value=raw_fact["value"],
                    state=FactState(raw_fact["state"]),
                    evidence_refs=tuple(raw_fact["evidence_refs"]),
                    confirmed_by=raw_fact["confirmed_by"],
                    confirmed_at=raw_fact["confirmed_at"],
                )
            )
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("invalid_tax_health_request_fact") from error
    input_hashes = payload["input_hashes"]
    if not isinstance(input_hashes, list):
        raise ValueError("invalid_tax_health_request_hashes")
    try:
        return TaxCheckRequest(
            period=period,
            jurisdiction=payload["jurisdiction"],
            facts=tuple(facts),
            input_hashes=tuple(input_hashes),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_tax_health_request") from error


def tax_request_to_dict(request: TaxCheckRequest) -> dict[str, object]:
    return to_dict(request)
