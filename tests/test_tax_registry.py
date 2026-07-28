"""Behavioral tests for the bundled tax registry and pack selection."""

from copy import deepcopy
from datetime import date
import unittest

from open_tax_check.tax_models import DateRange
from open_tax_check.tax_registry import (
    TAX_DEFINITIONS,
    TaxPackError,
    load_builtin_tax_packs,
    select_pack_segments,
    validate_pack_payload,
    validate_pack_set,
)


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
        "maturity": "screening",
        "requirements": [{
            "fact_key": "entity.registration",
            "answer_type": "choice",
            "evidence_types": ["registration_record"],
            "priority": 100,
            "blocking": True,
            "critical": True,
        }],
        "local_parameter_keys": [],
        "evaluator_id": "vat.screening",
        "sources": [{
            "title": "Official source",
            "url": "https://example.test/source",
            "published_on": "2023-01-01",
        }],
    }
    result.update(overrides)
    return result


class TaxRegistryTests(unittest.TestCase):
    def test_registry_has_the_exact_twenty_tax_definitions(self) -> None:
        self.assertEqual(len(TAX_DEFINITIONS), 20)
        self.assertEqual({item.tax_id: item.name_zh for item in TAX_DEFINITIONS}, EXPECTED)
        self.assertEqual(len({item.tax_id for item in TAX_DEFINITIONS}), 20)
        self.assertEqual(
            [item.category for item in TAX_DEFINITIONS],
            ["tax"] * 18 + ["surcharge"] * 2,
        )

    def test_bundled_prototype_is_excluded_until_explicitly_included(self) -> None:
        packs = load_builtin_tax_packs()
        period = DateRange(date(2023, 1, 1), date(2026, 12, 31))

        excluded = select_pack_segments(packs, tax_id="vat", jurisdiction="CN", period=period)
        included = select_pack_segments(
            packs, tax_id="vat", jurisdiction="CN", period=period, include_prototypes=True
        )

        self.assertEqual(excluded.segments, ())
        self.assertEqual(excluded.uncovered_periods, (period,))
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


if __name__ == "__main__":
    unittest.main()
