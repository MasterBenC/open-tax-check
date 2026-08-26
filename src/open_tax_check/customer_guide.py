from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Mapping

from .case import init_case, resolve_safe_case_path, write_json
from . import source_paths
from .scenario_map import scenario_catalog_payload
from .tax_models import Confirmation


CUSTOMER_PROFILE_SCHEMA_VERSION = "customer-profile/v1"
CASE_GUIDE_SCHEMA_VERSION = "case-guide/v2"
SCENARIO_FACTS_SCHEMA_VERSION = "tax-scenario-facts/v2"
SCENARIO_FACT_KEYS = (
    "has_real_estate_activity",
    "has_taxable_contracts",
    "has_employees_or_shareholder_flows",
    "has_related_party_or_restructuring_activity",
)
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


_SCENARIO_QUESTION_TEXT = {
    "has_real_estate_activity": "本检查期间是否发生房屋、土地、车辆或其他资产相关活动？",
    "has_taxable_contracts": "本检查期间是否签订应税合同或发生跨境、特殊交易？",
    "has_employees_or_shareholder_flows": "本检查期间是否有员工、股东或其他人员资金往来？",
    "has_related_party_or_restructuring_activity": "本检查期间是否存在关联方交易、重组或其他特殊安排？",
}
_SCENARIO_FACT_SCENARIOS = {
    "has_real_estate_activity": {"assets_and_real_estate"},
    "has_taxable_contracts": {"contracts_and_special_transactions"},
    "has_employees_or_shareholder_flows": {"people_shareholders_and_related_parties"},
    "has_related_party_or_restructuring_activity": {
        "contracts_and_special_transactions",
        "people_shareholders_and_related_parties",
    },
}
_BASELINE_SCENARIOS = ("revenue_and_turnover", "costs_and_expenses")
_MISSING = object()
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCENARIO_FACT_FIELDS = frozenset(
    {
        "question_id",
        "value",
        "confirmed_by",
        "confirmed_at",
        "evidence_refs",
        "scenario_revision",
        "question_digest",
        "confirmation_digest",
    }
)


def _scenario_catalog() -> tuple[Mapping[str, object], ...]:
    payload = scenario_catalog_payload()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("scenarios"), list):
        raise ValueError("invalid_scenario_catalog")
    scenarios = tuple(item for item in payload["scenarios"] if isinstance(item, Mapping))
    if len(scenarios) != len(payload["scenarios"]):
        raise ValueError("invalid_scenario_catalog")
    required = {"scenario_id", "name_zh", "description_zh", "material_items_zh"}
    if any(not required.issubset(item) for item in scenarios):
        raise ValueError("invalid_scenario_catalog")
    return scenarios


def _scenario_questions(confirmed_facts: Mapping[str, bool]) -> list[dict[str, str]]:
    return [
        {
            "question_id": f"scenario-{fact_key}",
            "fact_key": fact_key,
            "question_zh": _SCENARIO_QUESTION_TEXT[fact_key],
            "answer_type": "boolean",
        }
        for fact_key in SCENARIO_FACT_KEYS
        if fact_key not in confirmed_facts
    ]


def _scenario_question_digest(question: Mapping[str, object]) -> str:
    canonical = {
        "schema_version": "tax-scenario-question/v1",
        "question_id": question["question_id"],
        "fact_key": question["fact_key"],
        "question_zh": question["question_zh"],
        "answer_type": question["answer_type"],
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def load_scenario_fact_store(
    case_dir: Path,
    *,
    strict: bool = False,
) -> dict[str, object]:
    """Load the exact validated v2 fact store or an empty unconfirmed store."""

    root = resolve_safe_case_path(case_dir)
    facts_path = root / "work" / "scenario-facts.json"
    if not os.path.lexists(facts_path):
        return {
            "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
            "revision": 0,
            "facts": {},
        }
    try:
        safe_path = source_paths.resolve_safe_source(facts_path, root / "work")
        payload = json.loads(safe_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
            "revision": 0,
            "facts": {},
        }
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
        if strict:
            raise ValueError("invalid_scenario_facts") from None
        return {
            "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
            "revision": 0,
            "facts": {},
        }
    revision = payload.get("revision") if isinstance(payload, Mapping) else None
    facts = payload.get("facts") if isinstance(payload, Mapping) else None
    if (
        isinstance(payload, Mapping)
        and payload.get("schema_version") == "tax-scenario-facts/v1"
    ):
        return {
            "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
            "revision": 0,
            "facts": {},
        }
    valid = (
        isinstance(payload, Mapping)
        and set(payload) == {"schema_version", "revision", "facts"}
        and payload.get("schema_version") == SCENARIO_FACTS_SCHEMA_VERSION
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
        and isinstance(facts, Mapping)
    )
    if valid:
        for fact_key, record in facts.items():
            if (
                fact_key not in SCENARIO_FACT_KEYS
                or not isinstance(record, Mapping)
                or set(record) != _SCENARIO_FACT_FIELDS
                or record.get("question_id") != f"scenario-{fact_key}"
                or not isinstance(record.get("value"), bool)
                or not _is_human_confirmer(record.get("confirmed_by"))
                or not isinstance(record.get("evidence_refs"), list)
                or not record["evidence_refs"]
                or not isinstance(record.get("scenario_revision"), int)
                or isinstance(record.get("scenario_revision"), bool)
                or not 0 <= record["scenario_revision"] < revision
                or _SHA256_RE.fullmatch(str(record.get("question_digest"))) is None
                or _SHA256_RE.fullmatch(str(record.get("confirmation_digest"))) is None
            ):
                valid = False
                break
            question = {
                "question_id": record["question_id"],
                "fact_key": fact_key,
                "question_zh": _SCENARIO_QUESTION_TEXT[fact_key],
                "answer_type": "boolean",
            }
            if record["question_digest"] != _scenario_question_digest(question):
                valid = False
                break
            confirmation_payload = {
                "confirmation_kind": "scenario",
                "question_id": record["question_id"],
                "fact_key": fact_key,
                "value": record["value"],
                "confirmed_by": record["confirmed_by"],
                "confirmed_at": record["confirmed_at"],
                "evidence_refs": record["evidence_refs"],
                "scenario_revision": record["scenario_revision"],
                "question_digest": record["question_digest"],
            }
            expected_confirmation_digest = hashlib.sha256(
                json.dumps(
                    confirmation_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if record["confirmation_digest"] != expected_confirmation_digest:
                valid = False
                break
            try:
                Confirmation(
                    question_id=record["question_id"],
                    fact_key=fact_key,
                    value=record["value"],
                    confirmed_by=record["confirmed_by"],
                    confirmed_at=record["confirmed_at"],
                    evidence_refs=tuple(record["evidence_refs"]),
                )
            except (TypeError, ValueError):
                valid = False
                break
    if not valid:
        if strict:
            raise ValueError("invalid_scenario_facts")
        return {
            "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
            "revision": 0,
            "facts": {},
        }
    return {
        "schema_version": SCENARIO_FACTS_SCHEMA_VERSION,
        "revision": revision,
        "facts": {str(key): dict(value) for key, value in facts.items()},
    }


def build_scenario_question_tasks(case_dir: Path) -> tuple[dict[str, object], ...]:
    """Return pending scenario questions bound to the current server contract."""

    confirmed = {
        fact_key: value
        for fact_key, value in load_confirmed_scenario_facts(case_dir).items()
        if fact_key in SCENARIO_FACT_KEYS
    }
    store = load_scenario_fact_store(case_dir)
    revision = int(store["revision"])
    return tuple(
        {
            "task_id": f"agent-task-{question['question_id']}",
            "confirmation_kind": "scenario",
            **question,
            "required_evidence_types": ["customer_confirmation"],
            "scenario_revision": revision,
            "question_digest": _scenario_question_digest(question),
        }
        for question in _scenario_questions(confirmed)
    )


def _active_scenarios(confirmed_facts: Mapping[str, bool]) -> list[str]:
    active = set(_BASELINE_SCENARIOS)
    for fact_key, value in confirmed_facts.items():
        if value is True:
            active.update(_SCENARIO_FACT_SCENARIOS[fact_key])
    catalog = _scenario_catalog()
    return [str(item["scenario_id"]) for item in catalog if item["scenario_id"] in active]


def _document_groups(active_scenarios: tuple[str, ...] | list[str]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = [
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
    ]
    active = set(active_scenarios)
    for scenario in _scenario_catalog():
        scenario_id = str(scenario["scenario_id"])
        if scenario_id not in active:
            continue
        groups.append(
            {
                "group_id": scenario_id,
                "scenario_id": scenario_id,
                "title_zh": str(scenario["name_zh"]),
                "items_zh": list(scenario["material_items_zh"]),
                "applicability_note_zh": "本清单仅用于资料收集，不代表已认定相关税种或事项适用。",
            }
        )
    return groups


def load_confirmed_scenario_facts(case_dir: Path) -> dict[str, bool]:
    """Load source-safe, human-confirmed boolean scenario facts."""

    store = load_scenario_fact_store(case_dir)
    facts = store["facts"]
    assert isinstance(facts, Mapping)
    return {
        str(fact_key): record["value"]
        for fact_key, record in facts.items()
        if isinstance(record, Mapping) and isinstance(record.get("value"), bool)
    }


def _is_human_confirmer(value: object) -> bool:
    return value == "customer" or (
        isinstance(value, str)
        and value.startswith("human:")
        and bool(value.removeprefix("human:").strip())
    )


def _guide(
    stage: str,
    message_zh: str,
    *,
    profile: Mapping[str, object] | None = None,
    confirmed_facts: Mapping[str, bool] | None = None,
) -> dict[str, object]:
    facts = dict(confirmed_facts or {})
    active_scenarios = _active_scenarios(facts)
    result: dict[str, object] = {
        "schema_version": CASE_GUIDE_SCHEMA_VERSION,
        "stage": stage,
        "message_zh": message_zh,
        "profile_questions": _profile_questions(),
        "scenario_questions": _scenario_questions(facts),
        "active_scenarios": active_scenarios,
        "privacy_zh": "请只提供本次税务健康检查所需资料，并在上传前妥善处理无关个人敏感信息。",
        "disclaimer_zh": "本工具提供资料核对和税务健康检查提示，不代表税务机关认定、法律意见或最终申报结论。",
        "profile_digest": None,
        "document_groups": [],
    }
    if profile is not None:
        result["profile_digest"] = _profile_digest(profile)
        result["document_groups"] = _document_groups(active_scenarios)
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
        safe_profile_path = source_paths.resolve_safe_source(profile_path, root / "work")
        payload = json.loads(safe_profile_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != CUSTOMER_PROFILE_SCHEMA_VERSION:
            raise ValueError("invalid_customer_profile")
        customer_profile = _customer_profile(payload)
        _ensure_existing_case_period(root, customer_profile)
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
    confirmed_facts = {
        fact_key: value
        for fact_key, value in load_confirmed_scenario_facts(root).items()
        if fact_key in SCENARIO_FACT_KEYS
    }
    return _guide(
        stage,
        messages[stage],
        profile=payload,
        confirmed_facts=confirmed_facts,
    )
