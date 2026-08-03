import json
from pathlib import Path

from open_tax_check.host_contract import (
    validate_customer_handoff,
    validate_customer_response,
    validate_host_message,
)


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


def confirmation_event():
    return {
        "question_id": "question-001",
        "fact_key": "entity.vat_registration",
        "value": "general",
        "confirmed_by": "customer",
        "confirmed_at": "2026-08-03T10:00:00+08:00",
        "evidence_refs": ["source-001"],
    }


def test_confirmation_claim_requires_a_strict_customer_confirmation_record():
    payload = load_host_fixture("workbuddy-overclaim.json") | {
        "confirmation_event_count": 1,
        "confirmation_events": ["host-supplied-confirmation"],
    }

    assert validate_host_message(payload) == ("invalid_confirmation_events",)


def test_valid_customer_confirmation_event_authorizes_confirmation_wording():
    payload = load_host_fixture("workbuddy-overclaim.json") | {
        "confirmation_event_count": 1,
        "confirmation_events": [confirmation_event()],
    }

    assert validate_host_message(payload) == (
        "completion_overclaim",
        "unreviewed_candidate_in_client_conclusion",
        "missing_declared_artifact",
    )


def test_confirmation_event_rejects_unknown_fields_host_identity_and_empty_evidence():
    payload = load_host_fixture("workbuddy-overclaim.json")
    invalid_events = (
        confirmation_event() | {"case_status": "completed"},
        confirmation_event() | {"confirmed_by": "host"},
        confirmation_event() | {"evidence_refs": []},
    )

    for event in invalid_events:
        candidate = payload | {"confirmation_event_count": 1, "confirmation_events": [event]}
        assert validate_host_message(candidate) == ("invalid_confirmation_events",)


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


def test_unreviewed_candidate_count_rejects_negative_integers():
    payload = load_host_fixture("workbuddy-overclaim.json") | {
        "unreviewed_candidate_count": -1,
    }

    assert validate_host_message(payload) == ("invalid_unreviewed_candidate_count",)


def test_customer_response_allows_only_customer_answers_evidence_and_identity():
    handoff = {
        "schema_version": "tax-customer-response/v1",
        "customer_answers": [
            {
                "question_id": "question-001",
                "fact_key": "entity.vat_registration",
                "value": "general",
            }
        ],
        "evidence_references": ["evidence/tax-return.pdf"],
        "customer_identity": {"customer_id": "customer-001"},
    }

    assert validate_customer_response(handoff) is None
    for field in (
        "confirmation_event_count",
        "local_policy_facts",
        "rates",
        "mappings",
        "case_status",
        "tax_conclusions",
    ):
        assert validate_customer_response(handoff | {field: "host-claim"}) == (
            "unknown_or_host_owned_response_field"
        )


def test_customer_response_requires_all_customer_owned_fields():
    handoff = {
        "schema_version": "tax-customer-response/v1",
        "customer_answers": [
            {
                "question_id": "question-001",
                "fact_key": "entity.vat_registration",
                "value": True,
            }
        ],
        "evidence_references": ["source-001"],
        "customer_identity": {"customer_id": "customer-001"},
    }

    for field in ("customer_answers", "evidence_references", "customer_identity"):
        incomplete = handoff.copy()
        incomplete.pop(field)
        assert validate_customer_response(incomplete) == "invalid_customer_response_payload"


def test_customer_response_rejects_nested_host_fields_and_non_scalar_answers():
    handoff = {
        "schema_version": "tax-customer-response/v1",
        "customer_answers": [
            {
                "question_id": "question-001",
                "fact_key": "entity.vat_registration",
                "value": True,
            }
        ],
        "evidence_references": ["source-001"],
        "customer_identity": {"customer_id": "customer-001"},
    }
    invalid_answers = (
        handoff["customer_answers"][0] | {"case_status": "completed"},
        handoff["customer_answers"][0] | {"value": {"rate": "0.13"}},
    )

    for answer in invalid_answers:
        assert validate_customer_response(handoff | {"customer_answers": [answer]}) == (
            "invalid_customer_response_payload"
        )
    assert validate_customer_response(
        handoff | {"customer_identity": {"customer_id": "customer-001", "tax_conclusions": []}}
    ) == "invalid_customer_response_payload"
