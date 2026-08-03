import json
from pathlib import Path

import pytest

from open_tax_check.customer_guide import build_case_guide, start_case_from_profile
from open_tax_check.case import init_case


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


def test_initialized_case_without_profile_can_be_completed_without_rewriting_scope(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    init_case(case_dir, "示例科技有限公司", "2025-01-01", "2025-12-31")
    state_before = (case_dir / "state.json").read_bytes()

    result = start_case_from_profile(case_dir, _profile())

    assert result["schema_version"] == "customer-profile/v1"
    assert (case_dir / "state.json").read_bytes() == state_before
    assert build_case_guide(case_dir)["stage"] == "awaiting_documents"


def test_invalid_stored_profile_can_be_repaired_without_rewriting_scope(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    init_case(case_dir, "示例科技有限公司", "2025-01-01", "2025-12-31")
    state_before = (case_dir / "state.json").read_bytes()
    (case_dir / "work" / "customer-profile.json").write_text("{invalid", encoding="utf-8")

    result = start_case_from_profile(case_dir, _profile())

    assert json.loads((case_dir / "work" / "customer-profile.json").read_text(encoding="utf-8")) == result
    assert (case_dir / "state.json").read_bytes() == state_before


def test_existing_case_rejects_profile_with_a_different_review_period(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    init_case(case_dir, "示例科技有限公司", "2025-01-01", "2025-12-31")
    state_before = (case_dir / "state.json").read_bytes()

    with pytest.raises(ValueError, match="^case_period_mismatch$"):
        start_case_from_profile(case_dir, _profile(period_start="2026-01-01", period_end="2026-12-31"))

    assert (case_dir / "state.json").read_bytes() == state_before
    assert not (case_dir / "work" / "customer-profile.json").exists()


def test_profile_accepts_both_formal_period_endpoints(tmp_path: Path) -> None:
    result = start_case_from_profile(
        tmp_path / "case",
        _profile(period_start="2023-01-01", period_end="2026-12-31"),
    )

    assert result["period_start"] == "2023-01-01"
    assert result["period_end"] == "2026-12-31"


def test_empty_case_directory_is_initialized_as_a_new_case(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    start_case_from_profile(case_dir, _profile())

    assert json.loads((case_dir / "state.json").read_text(encoding="utf-8"))["status"] == "ready_for_input"


def test_profile_creates_progressive_material_groups(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    start_case_from_profile(case_dir, _profile())

    guide = build_case_guide(case_dir)

    assert guide["stage"] == "awaiting_documents"
    assert [item["group_id"] for item in guide["document_groups"]] == [
        "minimum_to_start",
        "recommended_if_available",
        "conditional_business_materials",
    ]
    assert "序时账或明细账" in guide["document_groups"][0]["items_zh"]
    assert "契税" not in guide["document_groups"][0]["items_zh"]
    assert guide["privacy_zh"]
    assert guide["disclaimer_zh"]
    assert len(guide["profile_digest"]) == 64


def test_every_guide_stage_contains_privacy_and_disclaimer(tmp_path: Path) -> None:
    welcome = build_case_guide(tmp_path / "missing")
    case_dir = tmp_path / "case"
    init_case(case_dir, "示例科技有限公司", "2025-01-01", "2025-12-31")
    awaiting_profile = build_case_guide(case_dir)

    for guide in (welcome, awaiting_profile):
        assert guide["privacy_zh"]
        assert "不代表" in guide["disclaimer_zh"]
