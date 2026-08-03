"""Allowlisted validation for claims made in host replay messages."""

from __future__ import annotations

from typing import Mapping

from .tax_models import Confirmation


HOST_MESSAGE_SCHEMA_VERSION = "host-message-replay/v1"
HANDOFF_SCHEMA_VERSION = "tax-customer-handoff/v1"

_HOST_MESSAGE_LIST_FIELDS = (
    ("pending_tasks", "invalid_pending_tasks"),
    ("declared_artifacts", "invalid_declared_artifacts"),
    ("existing_paths", "invalid_existing_paths"),
    ("unresolved_tax_ids", "invalid_unresolved_tax_ids"),
    (
        "host_declared_not_applicable_tax_ids",
        "invalid_host_declared_not_applicable_tax_ids",
    ),
)
_HANDOFF_FIELDS = frozenset(
    {"schema_version", "customer_answers", "evidence_references", "customer_identity"}
)
_CONFIRMATION_FIELDS = frozenset(
    {"question_id", "fact_key", "value", "confirmed_by", "confirmed_at", "evidence_refs"}
)
_CUSTOMER_ANSWER_FIELDS = frozenset({"question_id", "fact_key", "value"})
_CUSTOMER_IDENTITY_FIELDS = frozenset({"customer_id"})


def _valid_confirmation_event(event: object) -> bool:
    if not isinstance(event, Mapping) or set(event) != _CONFIRMATION_FIELDS:
        return False
    evidence_refs = event.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        return False
    if event.get("confirmed_by") != "customer":
        return False
    try:
        Confirmation(
            question_id=event["question_id"],
            fact_key=event["fact_key"],
            value=event["value"],
            confirmed_by=event["confirmed_by"],
            confirmed_at=event["confirmed_at"],
            evidence_refs=tuple(evidence_refs),
        )
    except (TypeError, ValueError):
        return False
    return True


def validate_host_message(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return safety issues supported by authoritative replay indicators only."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != HOST_MESSAGE_SCHEMA_VERSION:
        return ("invalid_host_message_payload",)

    invalid_issues: list[str] = []
    for field, issue in _HOST_MESSAGE_LIST_FIELDS:
        value = payload.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            invalid_issues.append(issue)

    confirmation_events = payload.get("confirmation_events")
    if not isinstance(confirmation_events, list) or not all(
        _valid_confirmation_event(event) for event in confirmation_events
    ):
        invalid_issues.append("invalid_confirmation_events")

    confirmation_event_count = payload.get("confirmation_event_count")
    if (
        isinstance(confirmation_event_count, bool)
        or not isinstance(confirmation_event_count, int)
        or not isinstance(confirmation_events, list)
        or confirmation_event_count != len(confirmation_events)
    ):
        invalid_issues.append("invalid_confirmation_event_count")

    unreviewed_candidate_count = payload.get("unreviewed_candidate_count")
    if (
        isinstance(unreviewed_candidate_count, bool)
        or not isinstance(unreviewed_candidate_count, int)
        or unreviewed_candidate_count < 0
    ):
        invalid_issues.append("invalid_unreviewed_candidate_count")
    if invalid_issues:
        return tuple(invalid_issues)

    pending_tasks = payload["pending_tasks"]
    unresolved_tax_ids = payload["unresolved_tax_ids"]
    declared_artifacts = payload["declared_artifacts"]
    existing_paths = payload["existing_paths"]
    not_applicable_tax_ids = payload["host_declared_not_applicable_tax_ids"]
    confirmation_events = payload["confirmation_events"]

    issues: list[str] = []
    message = payload.get("message_zh")
    if isinstance(message, str) and "检查已完成" in message and (pending_tasks or unresolved_tax_ids):
        issues.append("completion_overclaim")
    if isinstance(message, str) and "已确认" in message and not confirmation_events:
        issues.append("invented_confirmation_claim")
    if unreviewed_candidate_count and isinstance(message, str) and any(
        word in message for word in ("风险", "疑似")
    ):
        issues.append("unreviewed_candidate_in_client_conclusion")
    if any(path not in existing_paths for path in declared_artifacts):
        issues.append("missing_declared_artifact")
    if set(unresolved_tax_ids).intersection(not_applicable_tax_ids):
        issues.append("unresolved_tax_closed_by_host")
    return tuple(issues)


def validate_customer_handoff(payload: Mapping[str, object]) -> str | None:
    """Allow only customer answers, evidence references, and identity metadata."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        return "invalid_customer_handoff_payload"
    if not set(payload).issubset(_HANDOFF_FIELDS):
        return "unknown_or_host_owned_handoff_field"
    if set(payload) != _HANDOFF_FIELDS:
        return "invalid_customer_handoff_payload"

    customer_answers = payload["customer_answers"]
    if not isinstance(customer_answers, list) or not customer_answers:
        return "invalid_customer_handoff_payload"
    for answer in customer_answers:
        if not isinstance(answer, Mapping) or set(answer) != _CUSTOMER_ANSWER_FIELDS:
            return "invalid_customer_handoff_payload"
        if not all(isinstance(answer[field], str) and answer[field] for field in ("question_id", "fact_key")):
            return "invalid_customer_handoff_payload"
        value = answer["value"]
        if not (value is None or isinstance(value, str | int | bool)) or isinstance(value, float):
            return "invalid_customer_handoff_payload"

    evidence_references = payload["evidence_references"]
    if (
        not isinstance(evidence_references, list)
        or not evidence_references
        or not all(isinstance(reference, str) and reference for reference in evidence_references)
    ):
        return "invalid_customer_handoff_payload"

    customer_identity = payload["customer_identity"]
    if (
        not isinstance(customer_identity, Mapping)
        or set(customer_identity) != _CUSTOMER_IDENTITY_FIELDS
        or not isinstance(customer_identity["customer_id"], str)
        or not customer_identity["customer_id"]
    ):
        return "invalid_customer_handoff_payload"
    return None
