from __future__ import annotations

import json
import hashlib
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
CASE_TO_GUIDE_STAGE = {
    "ready_for_input": "awaiting_documents",
    "waiting_for_materials": "awaiting_documents",
    "waiting_for_extraction": "inventory_in_progress",
    "ready_for_self_check": "check_in_progress",
    "needs_professional_review": "check_in_progress",
    "waiting_for_customer_confirmation": "awaiting_customer_input",
    "completed_with_limitations": "draft_report_ready",
    "completed": "completed",
    "blocked_data_quality": "blocked",
    "failed": "blocked",
}


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


def _profile_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_groups(profile: Mapping[str, object]) -> list[dict[str, object]]:
    business = str(profile.get("primary_business", "")).strip()
    conditional = [
        "如发生房屋、土地权属转移，请提供契税申报表、合同及完税凭证",
        "如签订应税凭证，请提供印花税申报表及合同台账",
        "如存在关联交易、跨境交易或特殊重组，请提供相关协议和说明",
    ]
    if business:
        conditional.insert(0, f"与“{business}”特殊交易模式有关的合同、结算单和业务说明（如有）")
    return [
        {
            "group_id": "minimum_to_start",
            "title_zh": "可以先提供的基础资料",
            "items_zh": ["序时账或明细账", "科目余额表", "纳税申报表及财务报表"],
        },
        {
            "group_id": "recommended_if_available",
            "title_zh": "有的话建议一并提供",
            "items_zh": ["主要合同台账", "发票汇总资料", "固定资产和人员工资明细"],
        },
        {
            "group_id": "conditional_business_materials",
            "title_zh": "发生相关业务时再补充",
            "items_zh": conditional,
            "applicability_note_zh": "本清单仅用于询问资料，不代表已认定相关税种或事项适用。",
        },
    ]


def _guide(
    stage: str,
    message_zh: str,
    *,
    profile: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": CASE_GUIDE_SCHEMA_VERSION,
        "stage": stage,
        "message_zh": message_zh,
        "profile_questions": _profile_questions(),
        "privacy_zh": "请只提供本次税务健康检查所需资料，并在上传前妥善处理无关个人敏感信息。",
        "disclaimer_zh": "本工具提供资料核对和税务健康检查提示，不代表税务机关认定、法律意见或最终申报结论。",
        "profile_digest": None,
        "document_groups": [],
    }
    if profile is not None:
        result["profile_digest"] = _profile_digest(profile)
        result["document_groups"] = _document_groups(profile)
    return result


def _ensure_existing_case_period(root: Path, profile: CustomerProfile) -> None:
    try:
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise ValueError("invalid_case_state") from error
    if not isinstance(state, Mapping) or state.get("review_period") != {
        "start": profile.period_start.isoformat(),
        "end": profile.period_end.isoformat(),
    }:
        raise ValueError("case_period_mismatch")
    if state.get("entity_name") != profile.entity_name:
        raise ValueError("case_entity_mismatch")


def start_case_from_profile(case_dir: Path, profile: Mapping[str, object]) -> dict[str, object]:
    """Validate an opening profile before creating a resumable case."""

    customer_profile = _customer_profile(profile)
    root = resolve_safe_case_path(case_dir)
    if (root / "state.json").exists():
        _ensure_existing_case_period(root, customer_profile)
    else:
        init_case(
            case_dir,
            customer_profile.entity_name,
            customer_profile.period_start.isoformat(),
            customer_profile.period_end.isoformat(),
        )
        root = resolve_safe_case_path(case_dir)
    payload = _profile_payload(customer_profile)
    write_json(root / "work" / "customer-profile.json", payload)
    return payload


def build_case_guide(case_dir: Path) -> dict[str, object]:
    """Return the customer-facing opening prompt for a case directory."""

    root = resolve_safe_case_path(case_dir)
    if not root.exists() or not any(root.iterdir()):
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
    try:
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        case_status = state.get("status") if isinstance(state, Mapping) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        case_status = None
    stage = CASE_TO_GUIDE_STAGE.get(case_status, "awaiting_documents")
    messages = {
        "awaiting_documents": "企业基本信息已确认，可以按引导逐步补充资料。",
        "inventory_in_progress": "资料已收到，正在进行清单和可读取性检查。",
        "check_in_progress": "资料已进入确定性检查流程。",
        "awaiting_customer_input": "发现需要确认的信息，请按问题逐项补充。",
        "draft_report_ready": "税务健康检查报告草稿已生成，请查看限制和待补资料。",
        "completed": "税务健康检查交付已完成。",
        "blocked": "当前资料存在阻断项，请先按提示处理。",
    }
    return _guide(stage, messages[stage], profile=payload)
