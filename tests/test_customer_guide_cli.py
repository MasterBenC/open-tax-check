import json
from pathlib import Path

from open_tax_check import cli


def test_case_start_and_guide_round_trip_share_profile_digest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    case_dir = tmp_path / "case"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "entity_name": "示例科技有限公司",
                "province_city": "广东省深圳市",
                "primary_business": "软件开发",
                "period_start": "2023-01-01",
                "period_end": "2026-12-31",
                "vat_taxpayer_type": "general",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["opentaxcheck", "case-start", "--case", str(case_dir), "--profile", str(profile_path)],
    )
    assert cli.main() == 0
    started = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(cli.sys, "argv", ["opentaxcheck", "case-guide", "--case", str(case_dir)])
    assert cli.main() == 0
    guide = json.loads(capsys.readouterr().out)

    assert started["stage"] == guide["stage"] == "awaiting_documents"
    assert started["profile_digest"] == guide["profile_digest"]


def test_case_start_rejects_invalid_profile_with_stable_error(tmp_path: Path, monkeypatch, capsys) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["opentaxcheck", "case-start", "--case", str(tmp_path / "case"), "--profile", str(profile_path)],
    )

    assert cli.main() == 4
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error_code": "invalid_entity_name",
    }


def test_case_guide_cli_exposes_versioned_scenario_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    case_dir = tmp_path / "case"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({
            "entity_name": "示例科技有限公司",
            "province_city": "广东省深圳市",
            "primary_business": "软件开发",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "vat_taxpayer_type": "general",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["opentaxcheck", "case-start", "--case", str(case_dir), "--profile", str(profile_path)],
    )
    assert cli.main() == 0
    capsys.readouterr()

    monkeypatch.setattr(cli.sys, "argv", ["opentaxcheck", "case-guide", "--case", str(case_dir)])
    assert cli.main() == 0
    guide = json.loads(capsys.readouterr().out)

    assert guide["schema_version"] == "case-guide/v2"
    assert guide["scenario_questions"]
    assert guide["active_scenarios"][:2] == ["revenue_and_turnover", "costs_and_expenses"]
    assert {group["scenario_id"] for group in guide["document_groups"] if "scenario_id" in group} >= {
        "revenue_and_turnover",
        "costs_and_expenses",
    }
