"""Fixed business-scenario catalog for the tax risk map."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal, Mapping, TypeAlias

from .tax_registry import TAX_DEFINITIONS


SCENARIO_CATALOG_SCHEMA_VERSION = "tax-scenario-catalog/v1"
SCENARIO_COVERAGE_SCHEMA_VERSION = "tax-scenario-coverage/v1"
_CAPABILITIES = frozenset({"deterministic", "question_candidate"})
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")

ScenarioCheckState: TypeAlias = Literal[
    "executed_clear",
    "executed_risk",
    "awaiting_evidence",
    "received_unparsed",
    "not_applicable",
    "not_performed",
]

_CHECK_STATE_CONVERSION: dict[str, ScenarioCheckState] = {
    "received_unparsed": "received_unparsed",
    "not_applicable": "not_applicable",
    "matched": "executed_clear",
    "within_tolerance": "executed_clear",
    "not_triggered": "executed_clear",
    "difference_found": "executed_risk",
    "suspected": "executed_risk",
    "confirmed": "executed_risk",
    "needs_evidence": "awaiting_evidence",
    "needs_local_policy": "awaiting_evidence",
    "blocked": "awaiting_evidence",
    "received_pending": "received_unparsed",
    "extraction_partial": "received_unparsed",
    "present_unparsed": "received_unparsed",
    "skipped": "not_performed",
    "not_performed": "not_performed",
}

_MATERIAL_STATE_CONVERSION: dict[str, ScenarioCheckState | None] = {
    "not_provided": "awaiting_evidence",
    "needs_evidence": "awaiting_evidence",
    "needs_local_policy": "awaiting_evidence",
    "blocked": "awaiting_evidence",
    "received_pending": "received_unparsed",
    "extraction_partial": "received_unparsed",
    "present_unparsed": "received_unparsed",
    "imported_reconciled": None,
}


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value


def _require_chinese_text(value: object, code: str) -> str:
    text = _require_text(value, code)
    if _CHINESE_RE.search(text) is None:
        raise ValueError(code)
    return text


def _require_text_items(values: Iterable[object], code: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(code)
    try:
        items = tuple(values)
    except TypeError as error:
        raise ValueError(code) from error
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(code)
    return items


def _require_chinese_items(values: Iterable[object], code: str) -> tuple[str, ...]:
    items = _require_text_items(values, code)
    if any(_CHINESE_RE.search(item) is None for item in items):
        raise ValueError(code)
    return items


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    scenario_id: str
    name_zh: str
    description_zh: str
    tax_ids: tuple[str, ...]
    check_ids: tuple[str, ...]
    trigger_fact_keys: tuple[str, ...]
    material_items_zh: tuple[str, ...]
    capability: str

    def __post_init__(self) -> None:
        _require_text(self.scenario_id, "invalid_scenario_id")
        _require_chinese_text(self.name_zh, "invalid_scenario_name")
        _require_chinese_text(self.description_zh, "invalid_scenario_description")
        object.__setattr__(self, "tax_ids", _require_text_items(self.tax_ids, "invalid_tax_ids"))
        object.__setattr__(self, "check_ids", _require_text_items(self.check_ids, "invalid_check_ids"))
        object.__setattr__(
            self,
            "trigger_fact_keys",
            _require_text_items(self.trigger_fact_keys, "invalid_trigger_fact_keys"),
        )
        object.__setattr__(
            self,
            "material_items_zh",
            _require_chinese_items(self.material_items_zh, "invalid_material_items"),
        )
        if self.capability not in _CAPABILITIES:
            raise ValueError("invalid_scenario_capability")


@dataclass(frozen=True, slots=True)
class ScenarioCoverageItem:
    scenario_id: str
    name_zh: str
    total_count: int
    executed_clear_count: int
    executed_risk_count: int
    awaiting_evidence_count: int
    received_unparsed_count: int
    not_applicable_count: int
    not_performed_count: int
    check_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.scenario_id, "invalid_scenario_id")
        _require_chinese_text(self.name_zh, "invalid_scenario_name")
        object.__setattr__(self, "check_ids", _require_text_items(self.check_ids, "invalid_check_ids"))
        counts = (
            self.total_count,
            self.executed_clear_count,
            self.executed_risk_count,
            self.awaiting_evidence_count,
            self.received_unparsed_count,
            self.not_applicable_count,
            self.not_performed_count,
        )
        if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
            raise ValueError("invalid_scenario_coverage_count")
        if sum(counts[1:]) != self.total_count:
            raise ValueError("invalid_scenario_coverage_counts")
        if self.total_count != len(self.check_ids):
            raise ValueError("invalid_scenario_coverage_counts")


@dataclass(frozen=True, slots=True)
class ScenarioCoverageSnapshot:
    schema_version: str
    catalog_version: str
    revision: int
    items: tuple[ScenarioCoverageItem, ...]
    unique_check_count: int

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "invalid_scenario_coverage_schema_version")
        _require_text(self.catalog_version, "invalid_scenario_catalog_version")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision <= 0:
            raise ValueError("invalid_scenario_coverage_revision")
        if not isinstance(self.unique_check_count, int) or isinstance(self.unique_check_count, bool):
            raise ValueError("invalid_scenario_unique_check_count")
        object.__setattr__(self, "items", tuple(self.items))
        if self.unique_check_count != len({check_id for item in self.items for check_id in item.check_ids}):
            raise ValueError("invalid_scenario_unique_check_count")


def _validate_catalog(catalog: tuple[ScenarioDefinition, ...]) -> None:
    scenario_ids = tuple(item.scenario_id for item in catalog)
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("duplicate_scenario_id")
    known_tax_ids = {item.tax_id for item in TAX_DEFINITIONS}
    mapped_tax_ids = {tax_id for item in catalog for tax_id in item.tax_ids}
    if mapped_tax_ids != known_tax_ids:
        raise ValueError("tax_catalog_incomplete")


_SCENARIO_CATALOG = (
    ScenarioDefinition(
        "revenue_and_turnover",
        "收入与营业额",
        "围绕收入确认、营业额口径及增值税和企业所得税申报之间的勾稽关系识别风险。",
        (
            "vat", "consumption_tax", "enterprise_income_tax",
            "urban_maintenance_construction_tax", "education_surcharge",
            "local_education_surcharge",
        ),
        (
            "BOOK-FS-REV-001", "FS-VAT-SALES-001", "BOOK-VAT-OUTPUT-001",
            "FS-CIT-REV-PROFIT-001",
        ),
        ("revenue_recognition", "turnover_scope", "tax_return_sales"),
        ("收入合同与发票", "收入明细账", "增值税及企业所得税申报表"),
        "deterministic",
    ),
    ScenarioDefinition(
        "costs_and_expenses",
        "成本与费用",
        "围绕成本费用归集、税前扣除及纳税调整识别账税差异和证据不足风险。",
        (
            "resource_tax", "environmental_protection_tax",
        ),
        ("BOOK-CIT-BE-001",),
        ("cost_expense_deduction", "business_entertainment", "tax_adjustment"),
        ("成本费用明细账", "费用发票及合同", "企业所得税纳税调整明细"),
        "deterministic",
    ),
    ScenarioDefinition(
        "assets_and_real_estate",
        "资产与不动产",
        "围绕房产、土地、车辆及其他资产的取得、持有和处置识别相关税费风险。",
        (
            "house_property_tax", "urban_land_use_tax", "land_appreciation_tax",
            "vehicle_purchase_tax", "vehicle_vessel_tax", "vessel_tonnage_tax",
            "cultivated_land_occupation_tax", "deed_tax",
        ),
        (),
        ("asset_ownership", "asset_acquisition", "asset_disposal"),
        ("不动产权属及交易资料", "固定资产与土地台账", "车辆和船舶登记资料"),
        "question_candidate",
    ),
    ScenarioDefinition(
        "contracts_and_special_transactions",
        "合同与特殊交易",
        "围绕重大合同、跨境及特殊交易识别印花税、关税和交易环节税费适用风险。",
        ("tobacco_tax", "customs_duty", "stamp_tax"),
        (),
        ("major_contract", "cross_border_transaction", "special_transaction"),
        ("重大合同", "报关及物流单证", "特殊交易审批或备案资料"),
        "question_candidate",
    ),
    ScenarioDefinition(
        "people_shareholders_and_related_parties",
        "人员、股东与关联方",
        "围绕人员薪酬、股东及关联方交易识别个人所得税和关联交易资料完整性风险。",
        ("individual_income_tax",),
        (),
        ("employee_compensation", "shareholder_transaction", "related_party"),
        ("工资薪金及个税申报", "股东和关联方清单", "关联交易合同及定价资料"),
        "question_candidate",
    ),
)

_validate_catalog(_SCENARIO_CATALOG)


def scenario_catalog() -> tuple[ScenarioDefinition, ...]:
    """Return the stable, immutable scenario catalog in contract order."""

    return _SCENARIO_CATALOG


def scenario_catalog_payload() -> dict[str, object]:
    """Return the catalog as a JSON-compatible versioned payload."""

    return {
        "schema_version": SCENARIO_CATALOG_SCHEMA_VERSION,
        "scenarios": [
            {
                "scenario_id": item.scenario_id,
                "name_zh": item.name_zh,
                "description_zh": item.description_zh,
                "tax_ids": list(item.tax_ids),
                "check_ids": list(item.check_ids),
                "trigger_fact_keys": list(item.trigger_fact_keys),
                "material_items_zh": list(item.material_items_zh),
                "capability": item.capability,
            }
            for item in _SCENARIO_CATALOG
        ],
    }


def _scenario_check_state(value: object) -> ScenarioCheckState:
    try:
        return _CHECK_STATE_CONVERSION[value]
    except (KeyError, TypeError) as error:
        raise ValueError("unknown_scenario_check_state") from error


def _scenario_material_state(value: object) -> ScenarioCheckState | None:
    try:
        return _MATERIAL_STATE_CONVERSION[value]
    except (KeyError, TypeError) as error:
        raise ValueError("unknown_scenario_material_state") from error


def build_scenario_coverage(
    *,
    revision: int,
    check_states: Mapping[str, str],
    material_states: Mapping[str, str],
    confirmed_facts: Mapping[str, object],
) -> ScenarioCoverageSnapshot:
    """Build a deterministic, non-assurance coverage snapshot by scenario."""

    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise ValueError("invalid_scenario_coverage_revision")
    catalog = scenario_catalog()
    converted_check_states = {
        check_id: _scenario_check_state(state)
        for check_id, state in check_states.items()
    }
    converted_material_states = {
        check_id: _scenario_material_state(state)
        for check_id, state in material_states.items()
    }
    known_fact_keys = {fact_key for scenario in catalog for fact_key in scenario.trigger_fact_keys}
    for fact_key, fact_value in confirmed_facts.items():
        if not isinstance(fact_key, str) or fact_key not in known_fact_keys:
            raise ValueError("unknown_scenario_fact")
        if not isinstance(fact_value, bool):
            raise ValueError("invalid_scenario_fact_value")
    items = []
    for scenario in catalog:
        scenario_is_not_applicable = bool(scenario.trigger_fact_keys) and all(
            confirmed_facts.get(fact_key) is False
            for fact_key in scenario.trigger_fact_keys
        )
        states = tuple(
            _effective_scenario_check_state(
                check_state=converted_check_states.get(check_id, "not_performed"),
                material_state=converted_material_states.get(check_id),
                scenario_is_not_applicable=scenario_is_not_applicable,
            )
            for check_id in scenario.check_ids
        )
        items.append(
            ScenarioCoverageItem(
                scenario_id=scenario.scenario_id,
                name_zh=scenario.name_zh,
                total_count=len(scenario.check_ids),
                executed_clear_count=states.count("executed_clear"),
                executed_risk_count=states.count("executed_risk"),
                awaiting_evidence_count=states.count("awaiting_evidence"),
                received_unparsed_count=states.count("received_unparsed"),
                not_applicable_count=states.count("not_applicable"),
                not_performed_count=states.count("not_performed"),
                check_ids=scenario.check_ids,
            )
        )
    return ScenarioCoverageSnapshot(
        schema_version=SCENARIO_COVERAGE_SCHEMA_VERSION,
        catalog_version=SCENARIO_CATALOG_SCHEMA_VERSION,
        revision=revision,
        items=tuple(items),
        unique_check_count=len({check_id for item in items for check_id in item.check_ids}),
    )


def _effective_scenario_check_state(
    *,
    check_state: ScenarioCheckState,
    material_state: ScenarioCheckState | None,
    scenario_is_not_applicable: bool,
) -> ScenarioCheckState:
    if material_state in {"awaiting_evidence", "received_unparsed"}:
        return material_state
    if scenario_is_not_applicable:
        return "not_applicable"
    return check_state


def scenario_coverage_payload(snapshot: ScenarioCoverageSnapshot) -> dict[str, object]:
    """Return a JSON-compatible scenario coverage snapshot."""

    return {
        "schema_version": snapshot.schema_version,
        "catalog_version": snapshot.catalog_version,
        "revision": snapshot.revision,
        "items": [
            {
                "scenario_id": item.scenario_id,
                "name_zh": item.name_zh,
                "total_count": item.total_count,
                "executed_clear_count": item.executed_clear_count,
                "executed_risk_count": item.executed_risk_count,
                "awaiting_evidence_count": item.awaiting_evidence_count,
                "received_unparsed_count": item.received_unparsed_count,
                "not_applicable_count": item.not_applicable_count,
                "not_performed_count": item.not_performed_count,
                "check_ids": list(item.check_ids),
            }
            for item in snapshot.items
        ],
        "unique_check_count": snapshot.unique_check_count,
    }


__all__ = [
    "SCENARIO_CATALOG_SCHEMA_VERSION",
    "SCENARIO_COVERAGE_SCHEMA_VERSION",
    "ScenarioCheckState",
    "ScenarioCoverageItem",
    "ScenarioCoverageSnapshot",
    "ScenarioDefinition",
    "build_scenario_coverage",
    "scenario_catalog",
    "scenario_catalog_payload",
    "scenario_coverage_payload",
]
