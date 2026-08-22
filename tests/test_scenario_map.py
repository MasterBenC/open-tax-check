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
from open_tax_check.scenario_map import (
    build_scenario_coverage,
    scenario_coverage_payload,
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


def test_skipped_and_unparsed_are_not_executed():
    coverage = build_scenario_coverage(
        revision=3,
        check_states={
            "BOOK-FS-REV-001": "skipped",
            "FS-VAT-SALES-001": "received_unparsed",
        },
        material_states={},
        confirmed_facts={},
    )

    revenue = next(item for item in coverage.items if item.scenario_id == "revenue_and_turnover")
    assert revenue.executed_clear_count == 0
    assert revenue.not_performed_count >= 1
    assert revenue.received_unparsed_count == 1


def test_global_check_total_deduplicates_multi_scenario_checks():
    coverage = build_scenario_coverage(
        revision=1,
        check_states={"FS-CIT-REV-PROFIT-001": "matched"},
        material_states={},
        confirmed_facts={},
    )

    assert coverage.unique_check_count == len(set(
        check_id for item in coverage.items for check_id in item.check_ids
    ))


@pytest.mark.parametrize(
    ("input_state", "count_name"),
    [
        ("matched", "executed_clear_count"),
        ("within_tolerance", "executed_clear_count"),
        ("not_triggered", "executed_clear_count"),
        ("difference_found", "executed_risk_count"),
        ("suspected", "executed_risk_count"),
        ("confirmed", "executed_risk_count"),
        ("needs_evidence", "awaiting_evidence_count"),
        ("needs_local_policy", "awaiting_evidence_count"),
        ("blocked", "awaiting_evidence_count"),
        ("received_pending", "received_unparsed_count"),
        ("extraction_partial", "received_unparsed_count"),
        ("present_unparsed", "received_unparsed_count"),
        ("not_applicable", "not_applicable_count"),
        ("skipped", "not_performed_count"),
        ("not_performed", "not_performed_count"),
    ],
)
def test_check_state_conversion_uses_closed_mapping(input_state, count_name):
    coverage = build_scenario_coverage(
        revision=1,
        check_states={"BOOK-CIT-BE-001": input_state},
        material_states={},
        confirmed_facts={},
    )

    costs = next(item for item in coverage.items if item.scenario_id == "costs_and_expenses")
    assert getattr(costs, count_name) == 1
    assert costs.total_count == 1


def test_coverage_counts_account_for_every_scenario_check():
    coverage = build_scenario_coverage(
        revision=1,
        check_states={"BOOK-FS-REV-001": "matched"},
        material_states={},
        confirmed_facts={},
    )

    for item in coverage.items:
        assert sum((
            item.executed_clear_count,
            item.executed_risk_count,
            item.awaiting_evidence_count,
            item.received_unparsed_count,
            item.not_applicable_count,
            item.not_performed_count,
        )) == item.total_count


@pytest.mark.parametrize("revision", [0, -1, True, False, 1.5, "1"])
def test_coverage_rejects_non_positive_integer_revision(revision):
    with pytest.raises(ValueError, match="invalid_scenario_coverage_revision"):
        build_scenario_coverage(
            revision=revision,
            check_states={},
            material_states={},
            confirmed_facts={},
        )


def test_coverage_rejects_unknown_check_state():
    with pytest.raises(ValueError, match="unknown_scenario_check_state"):
        build_scenario_coverage(
            revision=1,
            check_states={"BOOK-FS-REV-001": "unknown"},
            material_states={},
            confirmed_facts={},
        )


def test_coverage_payload_is_versioned_serializable_snapshot():
    snapshot = build_scenario_coverage(
        revision=2,
        check_states={"BOOK-FS-REV-001": "matched"},
        material_states={},
        confirmed_facts={},
    )

    payload = scenario_coverage_payload(snapshot)
    assert payload == {
        "schema_version": "tax-scenario-coverage/v1",
        "catalog_version": SCENARIO_CATALOG_SCHEMA_VERSION,
        "revision": 2,
        "items": [
            {
                "scenario_id": "revenue_and_turnover",
                "name_zh": "收入与营业额",
                "total_count": 4,
                "executed_clear_count": 1,
                "executed_risk_count": 0,
                "awaiting_evidence_count": 0,
                "received_unparsed_count": 0,
                "not_applicable_count": 0,
                "not_performed_count": 3,
                "check_ids": [
                    "BOOK-FS-REV-001",
                    "FS-VAT-SALES-001",
                    "BOOK-VAT-OUTPUT-001",
                    "FS-CIT-REV-PROFIT-001",
                ],
            },
            {
                "scenario_id": "costs_and_expenses",
                "name_zh": "成本与费用",
                "total_count": 1,
                "executed_clear_count": 0,
                "executed_risk_count": 0,
                "awaiting_evidence_count": 0,
                "received_unparsed_count": 0,
                "not_applicable_count": 0,
                "not_performed_count": 1,
                "check_ids": ["BOOK-CIT-BE-001"],
            },
            {
                "scenario_id": "assets_and_real_estate",
                "name_zh": "资产与不动产",
                "total_count": 0,
                "executed_clear_count": 0,
                "executed_risk_count": 0,
                "awaiting_evidence_count": 0,
                "received_unparsed_count": 0,
                "not_applicable_count": 0,
                "not_performed_count": 0,
                "check_ids": [],
            },
            {
                "scenario_id": "contracts_and_special_transactions",
                "name_zh": "合同与特殊交易",
                "total_count": 0,
                "executed_clear_count": 0,
                "executed_risk_count": 0,
                "awaiting_evidence_count": 0,
                "received_unparsed_count": 0,
                "not_applicable_count": 0,
                "not_performed_count": 0,
                "check_ids": [],
            },
            {
                "scenario_id": "people_shareholders_and_related_parties",
                "name_zh": "人员、股东与关联方",
                "total_count": 0,
                "executed_clear_count": 0,
                "executed_risk_count": 0,
                "awaiting_evidence_count": 0,
                "received_unparsed_count": 0,
                "not_applicable_count": 0,
                "not_performed_count": 0,
                "check_ids": [],
            },
        ],
        "unique_check_count": 5,
    }
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
