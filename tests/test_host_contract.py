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


def test_customer_handoff_requires_the_declared_schema():
    assert validate_customer_handoff({"schema_version": "tax-customer-handoff/v1"}) is None
