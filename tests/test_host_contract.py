import json
from pathlib import Path

from open_tax_check.host_contract import validate_customer_handoff, validate_host_message


def load_host_fixture(name: str) -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "host-replays" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_workbuddy_overclaim_is_rejected():
    payload = load_host_fixture("workbuddy-overclaim.json")

    assert validate_host_message(payload) == (
        "completion_overclaim",
        "invented_confirmation_claim",
        "unreviewed_candidate_in_client_conclusion",
        "missing_declared_artifact",
    )


def test_hermes_cannot_close_missing_evidence_as_not_applicable():
    payload = load_host_fixture("hermes-overclaim.json")

    assert "unresolved_tax_closed_by_host" in validate_host_message(payload)


def test_host_cannot_self_declare_completion_with_pending_tasks():
    payload = load_host_fixture("workbuddy-overclaim.json") | {"case_status": "completed"}

    assert "completion_overclaim" in validate_host_message(payload)


def test_missing_confirmation_count_fails_closed():
    payload = load_host_fixture("workbuddy-overclaim.json")
    payload.pop("confirmation_event_count")

    assert validate_host_message(payload) == (
        "invalid_confirmation_event_count",
    )


def test_host_message_rejects_malformed_or_missing_authoritative_lists():
    payload = load_host_fixture("hermes-overclaim.json")

    assert validate_host_message(payload | {"declared_artifacts": ["request.json", {}]}) == (
        "invalid_declared_artifacts",
    )
    assert validate_host_message(payload | {"existing_paths": {"request.json"}}) == (
        "invalid_existing_paths",
    )
    assert validate_host_message(payload | {"unresolved_tax_ids": [["tax-01"]]}) == (
        "invalid_unresolved_tax_ids",
    )
    assert validate_host_message(payload | {"host_declared_not_applicable_tax_ids": None}) == (
        "invalid_host_declared_not_applicable_tax_ids",
    )
    for field, issue in (
        ("declared_artifacts", "invalid_declared_artifacts"),
        ("existing_paths", "invalid_existing_paths"),
        ("unresolved_tax_ids", "invalid_unresolved_tax_ids"),
        (
            "host_declared_not_applicable_tax_ids",
            "invalid_host_declared_not_applicable_tax_ids",
        ),
    ):
        incomplete = payload.copy()
        incomplete.pop(field)
        assert validate_host_message(incomplete) == (issue,)


def test_customer_handoff_allows_only_customer_answers_evidence_and_identity():
    handoff = {
        "schema_version": "tax-customer-handoff/v1",
        "customer_answers": {"entity.vat_registration": "general"},
        "evidence_references": ["evidence/tax-return.pdf"],
        "customer_identity": {"customer_id": "customer-001"},
    }

    assert validate_customer_handoff(handoff) is None
    for field in (
        "confirmation_event_count",
        "local_policy_facts",
        "rates",
        "mappings",
        "case_status",
        "tax_conclusions",
    ):
        assert validate_customer_handoff(handoff | {field: "host-claim"}) == (
            "unknown_or_host_owned_handoff_field"
        )
