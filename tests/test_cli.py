import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from open_tax_check.cli import main


def test_confirm_account_classification_rejects_ai_reviewer(tmp_path):
    case = tmp_path / "case"
    (case / "reviews").mkdir(parents=True)
    payload_path = tmp_path / "classification.json"
    payload_path.write_text(
        json.dumps(
            {
                "schema_version": "account-classification/v1",
                "source_manifest_sha256": "a" * 64,
                "mapping_fingerprint": "b" * 64,
                "reviewer": "agent",
                "confirmed_at": "2026-08-05T09:30:00+08:00",
                "classifications": [],
            }
        ),
        encoding="utf-8",
    )
    output = io.StringIO()

    with patch.object(
        sys,
        "argv",
        [
            "opentaxcheck",
            "confirm-account-classification",
            "--case",
            str(case),
            "--input",
            str(payload_path),
        ],
    ), contextlib.redirect_stdout(output):
        code = main()

    assert code == 3
    assert json.loads(output.getvalue()) == {
        "status": "needs_account_classification_review",
        "error_type": "invalid_account_classification",
    }


def test_confirm_account_classification_emits_stable_stale_binding_error(tmp_path):
    case = tmp_path / "case"
    (case / "reviews").mkdir(parents=True)
    payload_path = tmp_path / "classification.json"
    payload_path.write_text(
        json.dumps(
            {
                "schema_version": "account-classification/v1",
                "source_manifest_sha256": "a" * 64,
                "mapping_fingerprint": "b" * 64,
                "reviewer": "Chen Li",
                "confirmed_at": "2026-08-05T09:30:00+08:00",
                "classifications": [],
            }
        ),
        encoding="utf-8",
    )
    output = io.StringIO()

    with patch.object(
        sys,
        "argv",
        [
            "opentaxcheck",
            "confirm-account-classification",
            "--case",
            str(case),
            "--input",
            str(payload_path),
        ],
    ), contextlib.redirect_stdout(output):
        code = main()

    assert code == 3
    assert json.loads(output.getvalue()) == {
        "status": "needs_account_classification_review",
        "error_type": "account_classification_binding_mismatch",
    }
