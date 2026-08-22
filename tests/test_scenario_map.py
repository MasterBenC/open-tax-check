import json
from dataclasses import FrozenInstanceError, is_dataclass
import re

import pytest

from open_tax_check import (
    SCENARIO_CATALOG_SCHEMA_VERSION,
    ScenarioDefinition,
    scenario_catalog,
    scenario_catalog_payload,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS


def test_catalog_has_five_stable_unique_scenarios():
    catalog = scenario_catalog()
    assert [item.scenario_id for item in catalog] == [
        "revenue_and_turnover", "costs_and_expenses", "assets_and_real_estate",
        "contracts_and_special_transactions", "people_shareholders_and_related_parties",
    ]
    assert len({item.scenario_id for item in catalog}) == 5


def test_catalog_contains_all_fixed_cross_checks():
    check_ids = {check_id for item in scenario_catalog() for check_id in item.check_ids}
    assert {
        "BOOK-FS-REV-001", "FS-VAT-SALES-001", "BOOK-VAT-OUTPUT-001",
        "FS-CIT-REV-PROFIT-001", "BOOK-CIT-BE-001",
    } <= check_ids


def test_catalog_associates_fixed_cross_checks_with_revenue_and_costs():
    catalog = {item.scenario_id: item for item in scenario_catalog()}
    assert set(catalog["revenue_and_turnover"].check_ids) == {
        "BOOK-FS-REV-001", "FS-VAT-SALES-001", "BOOK-VAT-OUTPUT-001",
        "FS-CIT-REV-PROFIT-001",
    }
    assert catalog["costs_and_expenses"].check_ids == ("BOOK-CIT-BE-001",)


def test_catalog_maps_every_tax_definition_without_tax_rule_copies():
    mapped_tax_ids = {tax_id for item in scenario_catalog() for tax_id in item.tax_ids}
    assert mapped_tax_ids == {item.tax_id for item in TAX_DEFINITIONS}


def test_catalog_definitions_are_immutable_and_have_valid_labels_and_capabilities():
    catalog = scenario_catalog()
    assert is_dataclass(catalog[0])
    assert all(isinstance(item.tax_ids, tuple) for item in catalog)
    assert all(isinstance(item.check_ids, tuple) for item in catalog)
    assert all(isinstance(item.trigger_fact_keys, tuple) for item in catalog)
    assert all(isinstance(item.material_items_zh, tuple) for item in catalog)
    assert all(item.name_zh.strip() and item.description_zh.strip() for item in catalog)
    assert {item.capability for item in catalog} <= {"deterministic", "question_candidate"}
    with pytest.raises(FrozenInstanceError):
        catalog[0].name_zh = "变更"


def test_scenario_definition_rejects_invalid_identity_labels_and_capability():
    kwargs = dict(
        scenario_id="sample",
        name_zh="场景",
        description_zh="描述",
        tax_ids=("vat",),
        check_ids=(),
        trigger_fact_keys=("fact",),
        material_items_zh=("资料",),
        capability="deterministic",
    )
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "scenario_id": ""})
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "name_zh": ""})
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "description_zh": ""})
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "name_zh": "English label"})
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "description_zh": "English description"})
    with pytest.raises(ValueError):
        ScenarioDefinition(**{**kwargs, "capability": "unknown"})


def test_catalog_scenario_descriptions_match_their_tax_ids():
    catalog = {item.scenario_id: item for item in scenario_catalog()}
    assert "individual_income_tax" in catalog["people_shareholders_and_related_parties"].tax_ids
    assert "individual_income_tax" not in catalog["costs_and_expenses"].tax_ids
    assert {"stamp_tax", "customs_duty"} <= set(
        catalog["contracts_and_special_transactions"].tax_ids
    )


def test_catalog_payload_is_versioned_and_serializable():
    payload = scenario_catalog_payload()
    assert payload["schema_version"] == SCENARIO_CATALOG_SCHEMA_VERSION
    scenarios = payload["scenarios"]
    assert tuple(item["scenario_id"] for item in scenarios) == tuple(
        item.scenario_id for item in scenario_catalog()
    )
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    for item in scenarios:
        for field in ("tax_ids", "check_ids", "trigger_fact_keys", "material_items_zh"):
            assert isinstance(item[field], list)


def test_catalog_chinese_labels_contain_chinese_characters():
    chinese = re.compile(r"[\u3400-\u9fff]")
    for item in scenario_catalog():
        assert chinese.search(item.name_zh)
        assert chinese.search(item.description_zh)
        assert all(chinese.search(material) for material in item.material_items_zh)
