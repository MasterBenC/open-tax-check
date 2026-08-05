import hashlib
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from open_tax_check.account_classification import (
    AccountSourceRef,
    aggregate_classified_amount,
    confirm_account_classifications,
    load_active_account_classifications,
    parse_account_classification_review,
)
from open_tax_check.reviews import effective_journal_mapping_fingerprint


def valid_review_payload() -> dict:
    return {
        "schema_version": "account-classification/v1",
        "source_manifest_sha256": "a" * 64,
        "mapping_fingerprint": "b" * 64,
        "reviewer": "Chen Li",
        "confirmed_at": "2026-08-05T09:30:00+08:00",
        "classifications": [
            {
                "account_code": "6001",
                "account_name": "主营业务收入",
                "category": "main_business_revenue",
            },
            {
                "account_code": "222101",
                "account_name": "应交税费-应交增值税-销项税额",
                "category": "vat_output_tax",
            },
        ],
    }


def mutated_review_payload(mutation: str) -> dict:
    payload = valid_review_payload()
    if mutation == "duplicate_account":
        payload["classifications"].append(dict(payload["classifications"][0]))
    elif mutation == "unknown_category":
        payload["classifications"][0]["category"] = "inferred_category"
    elif mutation == "bad_hash":
        payload["mapping_fingerprint"] = "B" * 64
    elif mutation == "ai_reviewer":
        payload["reviewer"] = "ai"
    elif mutation == "ai_prefixed_reviewer":
        payload["reviewer"] = "ai:finance"
    else:
        raise ValueError(f"unknown mutation: {mutation}")
    return payload


def review():
    return parse_account_classification_review(valid_review_payload())


def rows() -> list[dict]:
    def lineage(column: int) -> dict:
        return {
            "source_id": "source-1",
            "sheet_id": "sheet-1",
            "row": 2,
            "column": column,
            "coordinate": f"{chr(64 + column)}2",
            "original_type": "str" if column < 3 else "Decimal",
            "value_origin": "source",
            "mapping_version": "mapping/v1",
        }

    return [
        {
            "account_code": "222101",
            "account_name": "应交税费-应交增值税-销项税额",
            "debit": "20.00",
            "credit": "150.00",
            "field_lineage": {
                "account_code": lineage(1),
                "account_name": lineage(2),
                "debit": lineage(3),
                "credit": lineage(4),
            },
        },
        {
            "account_code": "222101",
            "account_name": " 应交税费-应交增值税-销项税额 ",
            "debit": "0.00",
            "credit": "0.00",
            "field_lineage": {
                "account_code": {**lineage(1), "row": 3, "coordinate": "A3"},
                "account_name": {**lineage(2), "row": 3, "coordinate": "B3"},
                "debit": {**lineage(3), "row": 3, "coordinate": "C3"},
                "credit": {**lineage(4), "row": 3, "coordinate": "D3"},
            },
        },
        {
            "account_code": "222101",
            "account_name": "应交税费-其他税额",
            "debit": "0.00",
            "credit": "999.00",
            "field_lineage": {
                "account_code": {**lineage(1), "row": 4, "coordinate": "A4"},
                "account_name": {**lineage(2), "row": 4, "coordinate": "B4"},
                "debit": {**lineage(3), "row": 4, "coordinate": "C4"},
                "credit": {**lineage(4), "row": 4, "coordinate": "D4"},
            },
        },
    ]


def test_review_is_bound_to_manifest_mapping_and_unique_accounts():
    review = parse_account_classification_review(valid_review_payload())

    assert review.schema_version == "account-classification/v1"
    assert review.source_manifest_sha256 == "a" * 64
    assert review.mapping_fingerprint == "b" * 64
    assert review.classifications[0].category == "main_business_revenue"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_account",
        "unknown_category",
        "bad_hash",
        "ai_reviewer",
        "ai_prefixed_reviewer",
    ],
)
def test_invalid_or_non_human_classification_is_rejected(mutation):
    with pytest.raises(ValueError, match="invalid_account_classification"):
        parse_account_classification_review(mutated_review_payload(mutation))


@pytest.mark.parametrize("field", ["reviewer", "confirmed_at", "classifications"])
def test_review_requires_the_exact_contract_key_set(field):
    payload = valid_review_payload()
    del payload[field]

    with pytest.raises(ValueError, match="invalid_account_classification"):
        parse_account_classification_review(payload)


def test_aggregate_uses_only_confirmed_exact_account_identity():
    amount, refs = aggregate_classified_amount(rows(), review(), "vat_output_tax")

    assert amount == Decimal("130.00")
    assert refs == (
        AccountSourceRef(
            "222101",
            "应交税费-应交增值税-销项税额",
            "vat_output_tax",
        ),
    )


def test_aggregate_returns_none_when_no_category_is_confirmed():
    assert aggregate_classified_amount(rows(), review(), "business_entertainment") is None


@pytest.mark.parametrize("mutation", ["source", "sheet", "row", "minimal"])
def test_aggregate_rejects_incoherent_or_incomplete_lineage(mutation):
    corrupted = rows()
    lineage = corrupted[0]["field_lineage"]
    if mutation == "minimal":
        lineage["debit"] = {"source_id": "source-1"}
    else:
        field = {"source": "source_id", "sheet": "sheet_id", "row": "row"}[mutation]
        lineage["debit"][field] = "other" if mutation != "row" else 99

    with pytest.raises(ValueError, match="invalid_account_classification_lineage"):
        aggregate_classified_amount(corrupted, review(), "vat_output_tax")


@pytest.mark.parametrize(
    "reviewer",
    ["OpenAI", "ChatGPT", "GPT-5.6", "Claude 4.8", "DeepSeek V4", "Qwen", "Hermes Agent"],
)
def test_known_ai_or_agent_identity_cannot_confirm_classification(reviewer):
    payload = valid_review_payload()
    payload["reviewer"] = reviewer

    with pytest.raises(ValueError, match="invalid_account_classification"):
        parse_account_classification_review(payload)


def test_loading_requires_the_supplied_current_manifest_binding(tmp_path):
    review_path = tmp_path / "reviews" / "account_classification.json"
    review_path.parent.mkdir()
    review_path.write_text(__import__("json").dumps(valid_review_payload()), encoding="utf-8")

    assert load_active_account_classifications(tmp_path, "c" * 64) is None


def test_confirmation_binds_legacy_mapping_and_preserves_active_review_on_write_failure(tmp_path):
    case = tmp_path / "case"
    work = case / "work"
    reviews = case / "reviews"
    work.mkdir(parents=True)
    reviews.mkdir()
    manifest_path = work / "input_manifest.json"
    manifest_path.write_text('{"sources":[]}', encoding="utf-8")
    (work / "workbook_inventory.json").write_text("{}", encoding="utf-8")
    (work / "mapping_review.json").write_text(
        json.dumps(
            {
                "mapping_version": "legacy-csv/v1",
                "mapping": {
                    "voucher": "voucher_no",
                    "date": "date",
                    "account": "account_name",
                    "debit": "debit",
                    "credit": "credit",
                },
                "unresolved": [],
                "missing_required": [],
                "conflicts": {},
                "ready_for_calculation": True,
                "source_binding": {"filename": "journal.csv"},
            }
        ),
        encoding="utf-8",
    )
    payload = valid_review_payload()
    payload["source_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    payload["mapping_fingerprint"] = effective_journal_mapping_fingerprint(case)

    confirmed = confirm_account_classifications(case, payload)
    review_path = reviews / "account_classification.json"
    original = review_path.read_text(encoding="utf-8")
    active = load_active_account_classifications(
        case,
        payload["source_manifest_sha256"],
    )

    stale_payload = dict(payload)
    stale_payload["source_manifest_sha256"] = "c" * 64
    with pytest.raises(
        ValueError,
        match="account_classification_binding_mismatch",
    ):
        confirm_account_classifications(case, stale_payload)

    with patch("open_tax_check.reviews.os.replace", side_effect=OSError("full")):
        with pytest.raises(OSError, match="full"):
            confirm_account_classifications(case, payload)

    manifest_path.write_text('{"sources":["changed"]}', encoding="utf-8")

    assert confirmed == payload
    assert active == parse_account_classification_review(payload)
    assert review_path.read_text(encoding="utf-8") == original
    assert load_active_account_classifications(
        case,
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    ) is None
