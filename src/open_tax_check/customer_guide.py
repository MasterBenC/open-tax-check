from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Mapping

from .case import init_case, resolve_safe_case_path, write_json


CUSTOMER_PROFILE_SCHEMA_VERSION = "customer-profile/v1"
CASE_GUIDE_SCHEMA_VERSION = "case-guide/v1"
_FORMAL_PERIOD_START = date(2023, 1, 1)
_FORMAL_PERIOD_END = date(2026, 12, 31)
_VAT_TAXPAYER_TYPES = {"general", "small_scale", "unknown"}


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    entity_name: str
    province_city: str
    primary_business: str
    period_start: date
    period_end: date
    vat_taxpayer_type: Literal["general", "small_scale", "unknown"]


def _required_text(profile: Mapping[str, object], field: str, error_code: str) -> str:
    value = profile.get(field)
    if not isinstance(value, str) or not (value := value.strip()):
        raise ValueError(error_code)
    return value


def _profile_date(profile: Mapping[str, object], field: str) -> date:
    value = profile.get(field)
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("invalid_review_period")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid_review_period") from error


def _customer_profile(profile: Mapping[str, object]) -> CustomerProfile:
    entity_name = _required_text(profile, "entity_name", "invalid_entity_name")
    province_city = _required_text(profile, "province_city", "invalid_province_city")
    primary_business = _required_text(profile, "primary_business", "invalid_primary_business")
    period_start = _profile_date(profile, "period_start")
    period_end = _profile_date(profile, "period_end")
    vat_taxpayer_type = profile.get("vat_taxpayer_type")
    if vat_taxpayer_type not in _VAT_TAXPAYER_TYPES:
        raise ValueError("invalid_vat_taxpayer_type")
    if period_start > period_end:
        raise ValueError("invalid_review_period")
    if period_start < _FORMAL_PERIOD_START or period_end > _FORMAL_PERIOD_END:
        raise ValueError("review_period_out_of_range")
    return CustomerProfile(
        entity_name=entity_name,
        province_city=province_city,
        primary_business=primary_business,
        period_start=period_start,
        period_end=period_end,
        vat_taxpayer_type=vat_taxpayer_type,
    )


def _profile_payload(profile: CustomerProfile) -> dict[str, object]:
    payload = asdict(profile)
    payload["period_start"] = profile.period_start.isoformat()
    payload["period_end"] = profile.period_end.isoformat()
    return {"schema_version": CUSTOMER_PROFILE_SCHEMA_VERSION, **payload}


def _profile_questions() -> list[dict[str, str]]:
    return [
        {"field": "entity_name", "label_zh": "企业名称"},
        {"field": "province_city", "label_zh": "所在省/市"},
        {"field": "primary_business", "label_zh": "主营业务"},
        {"field": "period", "label_zh": "检查期间"},
        {"field": "vat_taxpayer_type", "label_zh": "增值税纳税人类型"},
    ]


def _guide(stage: str, message_zh: str) -> dict[str, object]:
    return {
        "schema_version": CASE_GUIDE_SCHEMA_VERSION,
        "stage": stage,
        "message_zh": message_zh,
        "profile_questions": _profile_questions(),
    }


def start_case_from_profile(case_dir: Path, profile: Mapping[str, object]) -> dict[str, object]:
    """Validate an opening profile before creating a resumable case."""

    customer_profile = _customer_profile(profile)
    init_case(
        case_dir,
        customer_profile.entity_name,
        customer_profile.period_start.isoformat(),
        customer_profile.period_end.isoformat(),
    )
    payload = _profile_payload(customer_profile)
    root = resolve_safe_case_path(case_dir)
    write_json(root / "work" / "customer-profile.json", payload)
    return payload


def build_case_guide(case_dir: Path) -> dict[str, object]:
    """Return the customer-facing opening prompt for a case directory."""

    root = resolve_safe_case_path(case_dir)
    if not root.exists():
        return _guide(
            "welcome",
            "欢迎使用税务健康检查 AI 智能体，资料不需要一次准备齐，可以按引导逐步补充。",
        )
    profile_path = root / "work" / "customer-profile.json"
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != CUSTOMER_PROFILE_SCHEMA_VERSION:
            raise ValueError("invalid_customer_profile")
        _customer_profile(payload)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return _guide(
            "awaiting_business_profile",
            "请先补充企业基本信息；资料不需要一次准备齐，可以按引导逐步补充。",
        )
    return _guide("ready_for_materials", "企业基本信息已确认，可以按引导逐步补充资料。")
