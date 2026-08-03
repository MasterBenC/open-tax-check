"""Allowlisted validation for claims made in host replay messages."""

from __future__ import annotations

from typing import Mapping


HOST_MESSAGE_SCHEMA_VERSION = "host-message-replay/v1"
HANDOFF_SCHEMA_VERSION = "tax-customer-handoff/v1"

_HOST_MESSAGE_LIST_FIELDS = (
    ("pending_tasks", "invalid_pending_tasks"),
    ("confirmation_events", "invalid_confirmation_events"),
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


def validate_host_message(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return safety issues supported by authoritative replay indicators only."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != HOST_MESSAGE_SCHEMA_VERSION:
        return ("invalid_host_message_payload",)

    invalid_issues: list[str] = []
    for field, issue in _HOST_MESSAGE_LIST_FIELDS:
        value = payload.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            invalid_issues.append(issue)

    confirmation_event_count = payload.get("confirmation_event_count")
    confirmation_events = payload.get("confirmation_events")
    if (
        isinstance(confirmation_event_count, bool)
        or not isinstance(confirmation_event_count, int)
        or not isinstance(confirmation_events, list)
        or confirmation_event_count != len(confirmation_events)
    ):
        invalid_issues.append("invalid_confirmation_event_count")

    unreviewed_candidate_count = payload.get("unreviewed_candidate_count")
    if isinstance(unreviewed_candidate_count, bool) or not isinstance(unreviewed_candidate_count, int):
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
    if "customer_answers" in payload and not isinstance(payload["customer_answers"], Mapping):
        return "invalid_customer_handoff_payload"
    if "evidence_references" in payload and (
        not isinstance(payload["evidence_references"], list)
        or not all(isinstance(reference, str) and reference for reference in payload["evidence_references"])
    ):
        return "invalid_customer_handoff_payload"
    if "customer_identity" in payload and not isinstance(payload["customer_identity"], Mapping):
        return "invalid_customer_handoff_payload"
    return None
