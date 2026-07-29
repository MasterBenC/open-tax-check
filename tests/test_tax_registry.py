"""Behavioral tests for the bundled tax registry and pack selection."""

from copy import deepcopy
from datetime import date
import json
import unittest
from unittest.mock import patch

from open_tax_check.tax_models import DateRange, PackMaturity
from open_tax_check.tax_registry import (
    TAX_DEFINITIONS,
    TaxPackIssue,
    TaxPackError,
    load_builtin_tax_pack_bundle,
    load_builtin_tax_packs,
    load_builtin_parameterized_calculation_packs,
    select_pack_segments,
    validate_pack_payload,
    validate_pack_set,
)


class _Entry:
    def __init__(self, name, size=1, text="{}"):
        self.name = name
        self._size = size
        self._text = text

    def stat(self):
        return type("Stat", (), {"st_size": self._size})()

    def read_text(self, *, encoding):
        return self._text


class _Package:
    def __init__(self, entries):
        self._entries = entries

    def iterdir(self):
        return iter(self._entries)


EXPECTED = {
    "vat": "增值税",
    "consumption_tax": "消费税",
    "enterprise_income_tax": "企业所得税",
    "individual_income_tax": "个人所得税",
    "resource_tax": "资源税",
    "urban_maintenance_construction_tax": "城市维护建设税",
    "house_property_tax": "房产税",
    "stamp_tax": "印花税",
    "urban_land_use_tax": "城镇土地使用税",
    "land_appreciation_tax": "土地增值税",
    "vehicle_purchase_tax": "车辆购置税",
    "vehicle_vessel_tax": "车船税",
    "vessel_tonnage_tax": "船舶吨税",
    "cultivated_land_occupation_tax": "耕地占用税",
    "deed_tax": "契税",
    "tobacco_tax": "烟叶税",
    "environmental_protection_tax": "环境保护税",
    "customs_duty": "关税",
    "education_surcharge": "教育费附加",
    "local_education_surcharge": "地方教育附加",
}


def payload(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "pack_id": "vat-cn-base",
        "tax_id": "vat",
        "jurisdiction": "CN",
        "effective_from": "2023-01-01",
        "effective_to": "2023-12-31",
        "maturity": "calculation",
        "requirements": [{
            "fact_key": "entity.registration",
            "answer_type": "choice",
            "evidence_types": ["registration_record"],
            "priority": 100,
            "blocking": True,
            "critical": True,
        }],
        "local_parameter_keys": [],
        "evaluator_id": "vat.calculation",
        "sources": [{
            "title": "Official source",
            "url": "https://example.test/source",
            "published_on": "2023-01-01",
        }],
    }
    result.update(overrides)
    return result


def screening_payload(**overrides: object) -> dict[str, object]:
    result = payload(
        schema_version="1.1.0",
        pack_id="vat-cn-screening-v1",
        maturity="screening",
        requirements=[{
            "fact_key": "activity.acquired_land_or_building_rights",
            "answer_type": "boolean",
            "evidence_types": ["contract"],
            "priority": 100,
            "blocking": True,
            "critical": True,
        }],
        evaluator_id="generic.applicability.screening.v1",
        sources=[{
            "title": "中华人民共和国契税法", "url": "https://www.gov.cn/",
            "published_on": "2020-08-11", "issuer": "全国人民代表大会常务委员会",
            "document_number": "中华人民共和国主席令第五十二号", "article_refs": ["第一条"],
            "verified_on": "2026-07-28",
        }],
        rule_version="2026.1",
        fact_catalog_version="zh-CN.v1",
        applicability_fact_keys=["activity.acquired_land_or_building_rights"],
    )
    result.update(overrides)
    return result


def statutory_calculation_payload(**overrides: object) -> dict[str, object]:
    result = payload(
        schema_version="1.3.0",
        pack_id="stamp-tax-statutory-calculation",
        tax_id="stamp_tax",
        evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
        requirements=[
            {
                "fact_key": "calc.tax_base",
                "answer_type": "decimal",
                "evidence_types": ["contract_register"],
                "priority": 100,
                "blocking": True,
                "critical": True,
            },
            {
                "fact_key": "stamp_tax.tax_item",
                "answer_type": "choice",
                "evidence_types": ["taxable_instrument"],
                "priority": 100,
                "blocking": True,
                "critical": True,
            },
        ],
        sources=[{
            "title": "中华人民共和国印花税法",
            "url": "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193058/content.html",
            "published_on": "2021-06-10",
            "issuer": "全国人民代表大会常务委员会",
            "document_number": "中华人民共和国主席令第八十九号",
            "article_refs": ["第四条", "印花税税目税率表"],
            "verified_on": "2026-07-29",
        }],
        rule_version="2026.1",
        fact_catalog_version="zh-CN.v1",
        applicability_fact_keys=[],
        calculation={
            "formula_id": "generic.base_times_rate.v1",
            "tax_base_fact_key": "calc.tax_base",
            "rate_fact_key": None,
            "deductions_fact_key": None,
            "rate_table": {
                "selector_fact_key": "stamp_tax.tax_item",
                "entries": [{
                    "selector": "sale_contract",
                    "label_zh": "买卖合同",
                    "rate": "0.0003",
                }],
            },
        },
    )
    result.update(overrides)
    return result


class TaxRegistryTests(unittest.TestCase):
    def test_schema_1_3_parses_statutory_rate_table(self) -> None:
        pack = validate_pack_payload(statutory_calculation_payload())

        self.assertEqual(pack.calculation.rate_fact_key, None)
        self.assertEqual(pack.calculation.rate_table.selector_fact_key, "stamp_tax.tax_item")
        self.assertEqual(pack.calculation.rate_table.entries[0].rate, "0.0003")

    def test_schema_1_3_rejects_duplicate_rate_selectors(self) -> None:
        value = statutory_calculation_payload()
        value["calculation"]["rate_table"]["entries"].append({
            "selector": "sale_contract",
            "label_zh": "重复税目",
            "rate": "0.0005",
        })

        with self.assertRaisesRegex(TaxPackError, "^invalid_calculation_spec$"):
            validate_pack_payload(value)

    def test_schema_1_3_parses_confirmed_rate_with_statutory_range(self) -> None:
        value = statutory_calculation_payload()
        value["requirements"][1]["fact_key"] = "deed_tax.applicable_rate"
        value["requirements"][1]["answer_type"] = "decimal"
        value["calculation"] = {
            "formula_id": "generic.base_times_rate.v1",
            "tax_base_fact_key": "calc.tax_base",
            "rate_fact_key": "deed_tax.applicable_rate",
            "deductions_fact_key": None,
            "rate_range": {"minimum": "0.03", "maximum": "0.05"},
        }

        pack = validate_pack_payload(value)

        self.assertEqual(pack.calculation.rate_range.minimum, "0.03")
        self.assertEqual(pack.calculation.rate_range.maximum, "0.05")

    def test_calculation_pack_rejects_non_decimal_numeric_binding(self) -> None:
        value = statutory_calculation_payload()
        value["requirements"][0]["answer_type"] = "text"

        with self.assertRaisesRegex(TaxPackError, "^invalid_calculation_spec$"):
            validate_pack_payload(value)

    def test_schema_1_3_parses_fixed_statutory_rate(self) -> None:
        value = statutory_calculation_payload()
        value["requirements"] = value["requirements"][:1]
        value["calculation"] = {
            "formula_id": "generic.base_times_rate.v1",
            "tax_base_fact_key": "calc.tax_base",
            "rate_fact_key": None,
            "deductions_fact_key": None,
            "fixed_rate": "0.03",
        }

        pack = validate_pack_payload(value)

        self.assertEqual(pack.calculation.fixed_rate, "0.03")

    def test_screening_and_calculation_packs_may_overlap_by_maturity(self) -> None:
        screening = validate_pack_payload(screening_payload(
            tax_id="stamp_tax",
            pack_id="stamp-tax-screening",
        ))
        calculation = validate_pack_payload(statutory_calculation_payload())

        validate_pack_set((screening, calculation))

        screening_selection = select_pack_segments(
            (screening, calculation),
            tax_id="stamp_tax",
            jurisdiction="CN",
            period=DateRange(date(2023, 1, 1), date(2023, 12, 31)),
            maturity=PackMaturity.SCREENING,
        )
        calculation_selection = select_pack_segments(
            (screening, calculation),
            tax_id="stamp_tax",
            jurisdiction="CN",
            period=DateRange(date(2023, 1, 1), date(2023, 12, 31)),
            maturity=PackMaturity.CALCULATION,
        )

        self.assertEqual(
            tuple(segment.pack.pack_id for segment in screening_selection.segments),
            ("stamp-tax-screening",),
        )
        self.assertEqual(
            tuple(segment.pack.pack_id for segment in calculation_selection.segments),
            ("stamp-tax-statutory-calculation",),
        )

    def test_parameterized_calculation_pack_examples_are_strictly_valid(self) -> None:
        root = __import__("pathlib").Path("examples/calculation-packs")
        paths = sorted(root.glob("*.json"))
        self.assertEqual([path.name for path in paths], [
            "deed-tax-cn-2023-2026.parameterized.json",
            "stamp-tax-cn-2023-2026.parameterized.json",
            "vat-cn-2023-2026.parameterized.json",
        ])
        packs = tuple(validate_pack_payload(json.loads(path.read_text(encoding="utf-8"))) for path in paths)
        self.assertEqual({pack.tax_id for pack in packs}, {"vat", "stamp_tax", "deed_tax"})
        self.assertTrue(all(pack.calculation is not None for pack in packs))

    def test_schema_1_2_calculation_pack_parses_formula_bindings(self) -> None:
        value = payload(
            schema_version="1.2.0",
            calculation={
                "formula_id": "generic.base_minus_deductions_times_rate.v1",
                "tax_base_fact_key": "calc.tax_base",
                "rate_fact_key": "calc.rate",
                "deductions_fact_key": "calc.deductions",
            },
            rule_version="2026.1",
            fact_catalog_version="zh-CN.v1",
            applicability_fact_keys=[],
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=[{
                "title": "Official source", "url": "https://example.test/source",
                "published_on": "2023-01-01", "issuer": "Official issuer",
                "document_number": "DOC-2023", "article_refs": ["Article 1"],
                "verified_on": "2026-01-01",
            }],
            requirements=[
                {
                    "fact_key": "calc.tax_base", "answer_type": "decimal",
                    "evidence_types": ["vat_return"], "priority": 100,
                    "blocking": True, "critical": True,
                },
                {
                    "fact_key": "calc.rate", "answer_type": "decimal",
                    "evidence_types": ["rule_source"], "priority": 100,
                    "blocking": True, "critical": True,
                },
                {
                    "fact_key": "calc.deductions", "answer_type": "decimal",
                    "evidence_types": ["vat_return"], "priority": 90,
                    "blocking": False, "critical": False,
                },
            ],
        )

        pack = validate_pack_payload(value)

        self.assertEqual(pack.calculation.formula_id, "generic.base_minus_deductions_times_rate.v1")
        self.assertEqual(pack.calculation.deductions_fact_key, "calc.deductions")

    def test_schema_1_2_accepts_base_times_rate_formula_without_deductions(self) -> None:
        value = payload(
            schema_version="1.2.0",
            rule_version="2026.1",
            fact_catalog_version="zh-CN.v1",
            applicability_fact_keys=[],
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=[{
                "title": "Official source", "url": "https://example.test/source",
                "published_on": "2023-01-01", "issuer": "Official issuer",
                "document_number": "DOC-2023", "article_refs": ["Article 1"],
                "verified_on": "2026-01-01",
            }],
            requirements=[
                {
                    "fact_key": "calc.tax_base", "answer_type": "decimal",
                    "evidence_types": ["tax_return"], "priority": 100,
                    "blocking": True, "critical": True,
                },
                {
                    "fact_key": "calc.rate", "answer_type": "decimal",
                    "evidence_types": ["rule_source"], "priority": 100,
                    "blocking": True, "critical": True,
                },
            ],
            calculation={
                "formula_id": "generic.base_times_rate.v1",
                "tax_base_fact_key": "calc.tax_base",
                "rate_fact_key": "calc.rate",
                "deductions_fact_key": None,
            },
        )

        pack = validate_pack_payload(value)

        self.assertEqual(pack.calculation.formula_id, "generic.base_times_rate.v1")

    def test_schema_1_2_rejects_deductions_binding_for_base_times_rate(self) -> None:
        value = payload(
            schema_version="1.2.0",
            rule_version="2026.1",
            fact_catalog_version="zh-CN.v1",
            applicability_fact_keys=[],
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=[{
                "title": "Official source", "url": "https://example.test/source",
                "published_on": "2023-01-01", "issuer": "Official issuer",
                "document_number": "DOC-2023", "article_refs": ["Article 1"],
                "verified_on": "2026-01-01",
            }],
            requirements=[
                {"fact_key": "calc.tax_base", "answer_type": "decimal", "evidence_types": ["tax_return"], "priority": 100, "blocking": True, "critical": True},
                {"fact_key": "calc.rate", "answer_type": "decimal", "evidence_types": ["rule_source"], "priority": 100, "blocking": True, "critical": True},
                {"fact_key": "calc.deductions", "answer_type": "decimal", "evidence_types": ["tax_return"], "priority": 90, "blocking": False, "critical": False},
            ],
            calculation={
                "formula_id": "generic.base_times_rate.v1",
                "tax_base_fact_key": "calc.tax_base",
                "rate_fact_key": "calc.rate",
                "deductions_fact_key": "calc.deductions",
            },
        )

        with self.assertRaisesRegex(TaxPackError, "^invalid_calculation_spec$"):
            validate_pack_payload(value)

    def test_schema_1_2_rejects_unbound_calculation_fact(self) -> None:
        value = payload(
            schema_version="1.2.0",
            rule_version="2026.1",
            fact_catalog_version="zh-CN.v1",
            applicability_fact_keys=[],
            sources=[{
                "title": "Official source", "url": "https://example.test/source",
                "published_on": "2023-01-01", "issuer": "Official issuer",
                "document_number": "DOC-2023", "article_refs": ["Article 1"],
                "verified_on": "2026-01-01",
            }],
            calculation={
                "formula_id": "generic.base_minus_deductions_times_rate.v1",
                "tax_base_fact_key": "calc.unknown",
                "rate_fact_key": "entity.registration",
                "deductions_fact_key": None,
            },
        )

        with self.assertRaisesRegex(TaxPackError, "^invalid_calculation_spec$"):
            validate_pack_payload(value)

    def test_builtin_pack_bundle_has_explicit_issue_contract(self) -> None:
        bundle = load_builtin_tax_pack_bundle()
        self.assertTrue(bundle.packs)
        self.assertEqual(bundle.issues, ())

    def test_bundle_isolates_corrupt_manifest_by_filename_tax_id(self) -> None:
        valid = _Entry(
            "vat-cn-2026.screening.json",
            text=json.dumps(screening_payload()),
        )
        corrupt = _Entry("deed-tax-cn-2023-2026.screening.json", text="not json")
        with patch("open_tax_check.tax_registry.files", return_value=_Package([valid, corrupt])):
            bundle = load_builtin_tax_pack_bundle()

        self.assertEqual(tuple(pack.tax_id for pack in bundle.packs), ("vat",))
        self.assertEqual(bundle.issues, (TaxPackIssue("deed_tax", "invalid_pack_json"),))

    def test_bundle_handles_unknown_manifest_without_exposing_filename(self) -> None:
        unknown = _Entry("unexpected.json", text="not json")
        with patch("open_tax_check.tax_registry.files", return_value=_Package([unknown])):
            bundle = load_builtin_tax_pack_bundle()

        self.assertEqual(bundle.packs, ())
        self.assertEqual(bundle.issues, (TaxPackIssue("unknown", "invalid_pack_json"),))

    def test_manifest_limits_are_checked_before_json_parsing(self):
        oversized = _Entry("vat.json", 256 * 1024 + 1, "not json")
        with patch("open_tax_check.tax_registry.files", return_value=_Package([oversized])):
            with self.assertRaisesRegex(TaxPackError, "^pack_manifest_too_large$"):
                load_builtin_tax_packs()
        manifests = [_Entry(f"pack-{index}.json", text="not json") for index in range(65)]
        with patch("open_tax_check.tax_registry.files", return_value=_Package(manifests)):
            with self.assertRaisesRegex(TaxPackError, "^too_many_pack_manifests$"):
                load_builtin_tax_packs()
    def test_screening_manifest_returns_versioned_contract(self):
        pack = validate_pack_payload(screening_payload())
        self.assertEqual(pack.rule_version, "2026.1")
        self.assertEqual(pack.fact_catalog_version, "zh-CN.v1")
        self.assertEqual(pack.applicability_fact_keys, ("activity.acquired_land_or_building_rights",))

    def test_screening_manifest_rejects_stable_contract_errors(self):
        cases = [
            (payload(schema_version="2.0.0"), "unsupported_schema_version"),
            (screening_payload(rule_version=""), "missing_rule_version"),
            (screening_payload(fact_catalog_version=""), "missing_fact_catalog_version"),
            (screening_payload(applicability_fact_keys=[]), "invalid_applicability_fact_keys"),
            (screening_payload(applicability_fact_keys=["activity.unknown"]), "unknown_applicability_fact_key"),
            (screening_payload(requirements=[{**screening_payload()["requirements"][0], "answer_type": "text"}]), "non_boolean_applicability_fact"),
            (screening_payload(evaluator_id="vat.screening"), "invalid_screening_evaluator"),
            (screening_payload(sources=[{**screening_payload()["sources"][0], "issuer": ""}]), "invalid_source_metadata"),
        ]
        for invalid_payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(TaxPackError, f"^{code}$"):
                    validate_pack_payload(invalid_payload)
    def test_registry_has_the_exact_twenty_tax_definitions(self) -> None:
        self.assertEqual(len(TAX_DEFINITIONS), 20)
        self.assertEqual({item.tax_id: item.name_zh for item in TAX_DEFINITIONS}, EXPECTED)
        self.assertEqual(len({item.tax_id for item in TAX_DEFINITIONS}), 20)
        self.assertEqual(
            [item.category for item in TAX_DEFINITIONS],
            ["tax"] * 18 + ["surcharge"] * 2,
        )

    def test_parameterized_calculation_templates_cover_all_twenty_tax_items(self) -> None:
        packs = load_builtin_parameterized_calculation_packs()

        self.assertEqual(len(packs), 20)
        self.assertEqual(
            {pack.tax_id for pack in packs},
            {definition.tax_id for definition in TAX_DEFINITIONS},
        )
        self.assertTrue(all(pack.maturity is PackMaturity.CALCULATION for pack in packs))
        self.assertTrue(all(pack.calculation is not None for pack in packs))
        self.assertEqual(
            next(pack for pack in packs if pack.tax_id == "vat").calculation.formula_id,
            "generic.base_minus_deductions_times_rate.v1",
        )
        self.assertEqual(
            next(pack for pack in packs if pack.tax_id == "stamp_tax").calculation.formula_id,
            "generic.base_times_rate.v1",
        )

    def test_bundled_prototype_is_excluded_until_explicitly_included(self) -> None:
        packs = load_builtin_tax_packs()
        period = DateRange(date(2023, 1, 1), date(2026, 12, 31))

        excluded = select_pack_segments(packs, tax_id="vat", jurisdiction="CN", period=period)
        included = select_pack_segments(
            packs, tax_id="vat", jurisdiction="CN", period=period, include_prototypes=True
        )

        self.assertEqual(
            tuple(segment.pack.pack_id for segment in excluded.segments),
            ("vat-cn-2023-2025-screening-v1", "vat-cn-2026-screening-v1"),
        )
        self.assertEqual(excluded.uncovered_periods, ())
        self.assertEqual(included.segments[0].pack.pack_id, "vat-cn-2023-2026-prototype")
        self.assertEqual(included.segments[0].period, period)

    def test_prototype_covers_both_effective_boundary_dates_only_when_included(self) -> None:
        packs = load_builtin_tax_packs()
        for requested in (date(2023, 1, 1), date(2026, 12, 31)):
            selection = select_pack_segments(
                packs,
                tax_id="vat",
                jurisdiction="CN",
                period=DateRange(requested, requested),
                include_prototypes=True,
            )
            self.assertEqual(selection.segments[0].pack.pack_id, "vat-cn-2023-2026-prototype")
        for requested in (date(2022, 12, 31), date(2027, 1, 1)):
            selection = select_pack_segments(
                packs,
                tax_id="vat",
                jurisdiction="CN",
                period=DateRange(requested, requested),
                include_prototypes=True,
            )
            self.assertEqual(selection.segments, ())
            self.assertEqual(selection.uncovered_periods, (DateRange(requested, requested),))

    def test_local_pack_overrides_cn_only_during_its_effective_segment(self) -> None:
        national = validate_pack_payload(payload(pack_id="national", effective_to="2023-12-31"))
        local = validate_pack_payload(payload(
            pack_id="beijing", jurisdiction="CN-11", effective_from="2023-04-01", effective_to="2023-06-30"
        ))

        selection = select_pack_segments(
            (national, local),
            tax_id="vat",
            jurisdiction="CN-11",
            period=DateRange(date(2023, 1, 1), date(2023, 12, 31)),
        )

        self.assertEqual(
            [(segment.period, segment.pack.pack_id) for segment in selection.segments],
            [
                (DateRange(date(2023, 1, 1), date(2023, 3, 31)), "national"),
                (DateRange(date(2023, 4, 1), date(2023, 6, 30)), "beijing"),
                (DateRange(date(2023, 7, 1), date(2023, 12, 31)), "national"),
            ],
        )
        self.assertEqual(selection.uncovered_periods, ())

    def test_uncovered_intervals_are_explicit_and_adjacent_intervals_merge(self) -> None:
        only_january = validate_pack_payload(payload(effective_to="2023-01-31"))

        selection = select_pack_segments(
            (only_january,),
            tax_id="vat",
            jurisdiction="CN",
            period=DateRange(date(2023, 1, 1), date(2023, 3, 31)),
        )

        self.assertEqual(
            selection.uncovered_periods,
            (DateRange(date(2023, 2, 1), date(2023, 3, 31)),),
        )

    def test_overlapping_same_jurisdiction_packs_are_rejected(self) -> None:
        first = validate_pack_payload(payload(pack_id="first", effective_to="2023-06-30"))
        second = validate_pack_payload(payload(pack_id="second", effective_from="2023-06-01"))

        with self.assertRaisesRegex(TaxPackError, "^overlapping_tax_packs$") as raised:
            validate_pack_set((first, second))

        self.assertEqual(raised.exception.code, "overlapping_tax_packs")

    def test_manifest_validation_returns_stable_codes_for_invalid_payloads(self) -> None:
        cases = [
            (payload(effective_from="2023-99-01"), "invalid_effective_from"),
            (payload(tax_id="unknown"), "unknown_tax_id"),
            (payload(sources=[{"title": "Bad", "url": "http://example.test", "published_on": "2023-01-01"}]), "invalid_source_url"),
            (payload(sources=[]), "missing_sources"),
            (payload(evaluator_id=None), "missing_evaluator_id"),
        ]
        for invalid_payload, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaisesRegex(TaxPackError, f"^{expected_code}$") as raised:
                    validate_pack_payload(deepcopy(invalid_payload))
                self.assertEqual(raised.exception.code, expected_code)

    def test_manifest_requires_exact_keys_and_local_parameters_must_be_requirements(self) -> None:
        with_extra = payload(unexpected=True)
        with self.assertRaisesRegex(TaxPackError, "^invalid_pack_keys$"):
            validate_pack_payload(with_extra)
        with self.assertRaisesRegex(TaxPackError, "^invalid_local_parameter_key$"):
            validate_pack_payload(payload(local_parameter_keys=["entity.missing"]))

    def test_prototype_manifest_may_omit_evaluator_and_all_lists_become_tuples(self) -> None:
        pack = validate_pack_payload(payload(maturity="prototype", evaluator_id=None))
        self.assertIsInstance(pack.requirements, tuple)
        self.assertIsInstance(pack.requirements[0].evidence_types, tuple)
        self.assertIsInstance(pack.sources, tuple)

    def test_manifest_rejects_unsupported_requirement_answer_type_safely(self) -> None:
        invalid_payload = payload()
        invalid_payload["requirements"][0]["answer_type"] = "customer/private/path"

        with self.assertRaisesRegex(TaxPackError, "^unsupported_answer_type$") as raised:
            validate_pack_payload(invalid_payload)

        self.assertEqual(raised.exception.code, "unsupported_answer_type")
        self.assertNotIn("customer/private/path", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
