"""Allowlisted validation for claims made in host replay messages."""

from __future__ import annotations

from typing import Mapping


HOST_MESSAGE_SCHEMA_VERSION = "host-message-replay/v1"
HANDOFF_SCHEMA_VERSION = "tax-customer-handoff/v1"


def validate_host_message(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return the safety issues explicitly evidenced by a host replay message."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != HOST_MESSAGE_SCHEMA_VERSION:
        return ("invalid_host_message_payload",)

    issues: list[str] = []
    message = payload.get("message_zh")
    if payload.get("case_status") != "completed" and isinstance(message, str) and "检查已完成" in message:
        issues.append("completion_overclaim")
    if payload.get("confirmation_event_count") == 0 and isinstance(message, str) and "已确认" in message:
        issues.append("invented_confirmation_claim")
    if payload.get("unreviewed_candidate_count") and isinstance(message, str) and any(
        word in message for word in ("风险", "疑似")
    ):
        issues.append("unreviewed_candidate_in_client_conclusion")

    declared_artifacts = payload.get("declared_artifacts")
    existing_paths = payload.get("existing_paths")
    if isinstance(declared_artifacts, list) and isinstance(existing_paths, list):
        if any(isinstance(path, str) and path not in existing_paths for path in declared_artifacts):
            issues.append("missing_declared_artifact")

    unresolved_tax_ids = payload.get("unresolved_tax_ids")
    not_applicable_tax_ids = payload.get("host_declared_not_applicable_tax_ids")
    if isinstance(unresolved_tax_ids, list) and isinstance(not_applicable_tax_ids, list):
        if set(unresolved_tax_ids).intersection(not_applicable_tax_ids):
            issues.append("unresolved_tax_closed_by_host")
    return tuple(issues)


def validate_customer_handoff(payload: Mapping[str, object]) -> str | None:
    """Reject a customer handoff that does not declare the handoff schema."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        return "invalid_customer_handoff_payload"
    return None
