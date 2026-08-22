"""Fixed business-scenario catalog for the tax risk map."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .tax_registry import TAX_DEFINITIONS


SCENARIO_CATALOG_SCHEMA_VERSION = "tax-scenario-catalog/v1"
_CAPABILITIES = frozenset({"deterministic", "question_candidate"})


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value


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
        _require_text(self.name_zh, "invalid_scenario_name")
        _require_text(self.description_zh, "invalid_scenario_description")
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
            _require_text_items(self.material_items_zh, "invalid_material_items"),
        )
        if self.capability not in _CAPABILITIES:
            raise ValueError("invalid_scenario_capability")


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
            "individual_income_tax", "resource_tax", "environmental_protection_tax",
            "customs_duty", "stamp_tax",
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
        ("tobacco_tax",),
        (),
        ("major_contract", "cross_border_transaction", "special_transaction"),
        ("重大合同", "报关及物流单证", "特殊交易审批或备案资料"),
        "question_candidate",
    ),
    ScenarioDefinition(
        "people_shareholders_and_related_parties",
        "人员、股东与关联方",
        "围绕人员薪酬、股东及关联方交易识别个人所得税和关联交易资料完整性风险。",
        (),
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


__all__ = [
    "SCENARIO_CATALOG_SCHEMA_VERSION",
    "ScenarioDefinition",
    "scenario_catalog",
    "scenario_catalog_payload",
]
