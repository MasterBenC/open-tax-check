import json
from pathlib import Path

import pytest

from open_tax_check.customer_guide import build_case_guide, start_case_from_profile


def _profile(**overrides: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "entity_name": "示例科技有限公司",
        "province_city": "广东省深圳市",
        "primary_business": "软件开发",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "vat_taxpayer_type": "general",
    }
    profile.update(overrides)
    return profile


def test_missing_case_returns_approved_opening(tmp_path: Path) -> None:
    guide = build_case_guide(tmp_path / "case")

    assert guide["schema_version"] == "case-guide/v1"
    assert guide["stage"] == "welcome"
    assert "税务健康检查 AI 智能体" in guide["message_zh"]
    assert "资料不需要一次准备齐" in guide["message_zh"]
    assert [item["field"] for item in guide["profile_questions"]] == [
        "entity_name",
        "province_city",
        "primary_business",
        "period",
        "vat_taxpayer_type",
    ]


def test_profile_initializes_case_and_persists_immutable_contract(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"

    result = start_case_from_profile(case_dir, _profile())

    assert result == {
        "schema_version": "customer-profile/v1",
        "entity_name": "示例科技有限公司",
        "province_city": "广东省深圳市",
        "primary_business": "软件开发",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "vat_taxpayer_type": "general",
    }
    assert json.loads((case_dir / "work" / "customer-profile.json").read_text(encoding="utf-8")) == result
    assert json.loads((case_dir / "state.json").read_text(encoding="utf-8"))["review_period"] == {
        "start": "2025-01-01",
        "end": "2025-12-31",
    }


@pytest.mark.parametrize("vat_taxpayer_type", ["general", "small_scale", "unknown"])
def test_profile_accepts_supported_vat_taxpayer_types(tmp_path: Path, vat_taxpayer_type: str) -> None:
    result = start_case_from_profile(tmp_path / vat_taxpayer_type, _profile(vat_taxpayer_type=vat_taxpayer_type))

    assert result["vat_taxpayer_type"] == vat_taxpayer_type


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"entity_name": "  "}, "invalid_entity_name"),
        ({"province_city": "  "}, "invalid_province_city"),
        ({"primary_business": "  "}, "invalid_primary_business"),
        ({"period_start": "2025-12-31", "period_end": "2025-01-01"}, "invalid_review_period"),
        ({"period_start": "2022-12-31"}, "review_period_out_of_range"),
        ({"period_end": "2027-01-01"}, "review_period_out_of_range"),
        ({"vat_taxpayer_type": "not-a-type"}, "invalid_vat_taxpayer_type"),
    ],
)
def test_profile_rejects_invalid_values_before_case_initialization(
    tmp_path: Path, overrides: dict[str, object], error_code: str
) -> None:
    case_dir = tmp_path / "case"

    with pytest.raises(ValueError, match=f"^{error_code}$"):
        start_case_from_profile(case_dir, _profile(**overrides))

    assert not case_dir.exists()


def test_initialized_case_without_a_valid_profile_awaits_business_profile(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "state.json").write_text("{}", encoding="utf-8")

    guide = build_case_guide(case_dir)

    assert guide["schema_version"] == "case-guide/v1"
    assert guide["stage"] == "awaiting_business_profile"
    assert guide["profile_questions"]
