import unittest
from datetime import date

from open_tax_check.tax_models import (
    DateRange,
    FactState,
    PackMaturity,
    TaxCheckRequest,
    TaxFact,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS, load_builtin_tax_packs, select_pack_segments
from open_tax_check.tax_screening import screen_tax_items, builtin_tax_evaluators


PERIOD = DateRange(date(2023, 1, 1), date(2026, 12, 31))
EXPECTED = {
    "vat": "activity.has_vat_taxable_transaction",
    "consumption_tax": "activity.has_consumption_tax_goods",
    "enterprise_income_tax": "entity.has_enterprise_income_tax_scope",
    "individual_income_tax": "activity.has_individual_income_tax_duty",
    "urban_maintenance_construction_tax": "tax.has_vat_or_consumption_tax_payable",
    "education_surcharge": "tax.has_vat_or_consumption_tax_payable",
    "local_education_surcharge": "tax.has_vat_or_consumption_tax_payable",
    "house_property_tax": "assets.has_owned_taxable_house",
    "stamp_tax": "activity.has_stamp_taxable_instrument_or_securities_transaction",
    "urban_land_use_tax": "assets.uses_urban_taxable_land",
    "land_appreciation_tax": "activity.has_real_estate_transfer",
    "vehicle_purchase_tax": "activity.acquired_taxable_vehicle",
    "vehicle_vessel_tax": "assets.owns_or_manages_taxable_vehicle_or_vessel",
    "cultivated_land_occupation_tax": "activity.occupies_cultivated_land_for_construction",
    "deed_tax": "activity.acquired_land_or_building_rights",
    "resource_tax": "activity.extracts_or_sells_taxable_resources",
    "environmental_protection_tax": "activity.directly_discharges_taxable_pollutants",
    "tobacco_tax": "activity.purchases_tobacco_leaves",
    "vessel_tonnage_tax": "activity.has_foreign_vessel_entering_china_port",
    "customs_duty": "activity.imports_or_exports_dutiable_goods",
}


class BuiltinTaxScreeningCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs = load_builtin_tax_packs()

    def test_core_inventory_and_periods(self):
        for tax_id, fact_key in EXPECTED.items():
            with self.subTest(tax_id=tax_id):
                segments = select_pack_segments(
                    self.packs,
                    tax_id=tax_id,
                    jurisdiction="CN",
                    period=PERIOD,
                    maturity=PackMaturity.SCREENING,
                ).segments
                self.assertTrue(segments)
                self.assertEqual(tuple(item.pack.applicability_fact_keys for item in segments), tuple((fact_key,) for _ in segments))
                self.assertTrue(all(item.pack.evaluator_id == "generic.applicability.screening.v1" for item in segments))
                self.assertTrue(all(item.pack.fact_catalog_version == "zh-CN.v1" for item in segments))

    def test_all_core_scenarios(self):
        for tax_id, fact_key in EXPECTED.items():
            packs = tuple(item.pack for item in select_pack_segments(
                self.packs,
                tax_id=tax_id,
                jurisdiction="CN",
                period=PERIOD,
                maturity=PackMaturity.SCREENING,
            ).segments)
            for value, expected in ((False, TaxStatus.NOT_APPLICABLE), (True, TaxStatus.NEEDS_EVIDENCE)):
                result = screen_tax_items(TaxCheckRequest(PERIOD, "CN", (TaxFact(fact_key, value, FactState.CONFIRMED, confirmed_by="customer", confirmed_at="2026-07-28T10:00:00+08:00"),)), packs, builtin_tax_evaluators(), tax_ids=(tax_id,))[0]
                local_policy_ids = {"local_education_surcharge", "house_property_tax", "urban_land_use_tax", "vehicle_vessel_tax", "cultivated_land_occupation_tax", "deed_tax", "resource_tax", "environmental_protection_tax"}
                expected_status = TaxStatus.NEEDS_LOCAL_POLICY if tax_id in local_policy_ids and value else expected
                self.assertEqual(result.status, expected_status)

    def test_stamp_tax_calculation_packs_cover_official_rate_periods(self):
        packs = tuple(
            pack
            for pack in self.packs
            if pack.tax_id == "stamp_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(
            tuple((pack.effective_period.start, pack.effective_period.end) for pack in packs),
            (
                (date(2023, 1, 1), date(2023, 8, 27)),
                (date(2023, 8, 28), date(2026, 12, 31)),
            ),
        )
        expected_common_rates = {
            "loan_contract": "0.00005",
            "finance_lease_contract": "0.00005",
            "sale_contract": "0.0003",
            "work_contract": "0.0003",
            "construction_contract": "0.0003",
            "transport_contract": "0.0003",
            "technology_contract": "0.0003",
            "lease_contract": "0.001",
            "custody_contract": "0.001",
            "warehousing_contract": "0.001",
            "property_insurance_contract": "0.001",
            "land_use_right_grant": "0.0005",
            "land_or_building_transfer": "0.0005",
            "equity_transfer": "0.0005",
            "intellectual_property_transfer": "0.0003",
            "business_books": "0.00025",
        }
        first = {
            entry.selector: entry.rate
            for entry in packs[0].calculation.rate_table.entries
        }
        second = {
            entry.selector: entry.rate
            for entry in packs[1].calculation.rate_table.entries
        }

        self.assertEqual(
            {key: first[key] for key in expected_common_rates},
            expected_common_rates,
        )
        self.assertEqual(
            {key: second[key] for key in expected_common_rates},
            expected_common_rates,
        )
        self.assertEqual(first["securities_transaction"], "0.001")
        self.assertEqual(second["securities_transaction"], "0.0005")
        self.assertEqual(len(first), 17)
        self.assertEqual(len(second), 17)
        self.assertEqual(
            {source.document_number for source in packs[1].sources},
            {
                "中华人民共和国主席令第八十九号",
                "财政部 税务总局公告2023年第39号",
            },
        )

    def test_deed_tax_calculation_pack_requires_confirmed_local_rate(self):
        packs = tuple(
            pack for pack in self.packs
            if pack.tax_id == "deed_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertEqual(pack.effective_period, PERIOD)
        self.assertEqual(pack.calculation.formula_id, "generic.base_times_rate.v1")
        self.assertEqual(pack.calculation.tax_base_fact_key, "calc.tax_base")
        self.assertEqual(pack.calculation.rate_fact_key, "deed_tax.applicable_rate")
        self.assertIsNone(pack.calculation.rate_table)
        self.assertEqual(pack.local_parameter_keys, ("deed_tax.applicable_rate",))
        self.assertEqual(pack.sources[0].document_number, "中华人民共和国主席令第五十二号")
        self.assertEqual(pack.sources[0].article_refs, ("第三条", "第四条", "第五条"))

    def test_urban_maintenance_tax_pack_uses_statutory_location_rates(self):
        packs = tuple(
            pack for pack in self.packs
            if pack.tax_id == "urban_maintenance_construction_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(len(packs), 1)
        table = packs[0].calculation.rate_table
        self.assertEqual(table.selector_fact_key, "urban_maintenance_construction_tax.location_category")
        self.assertEqual(
            {entry.selector: entry.rate for entry in table.entries},
            {"urban_area": "0.07", "county_or_town": "0.05", "other": "0.01"},
        )
        self.assertEqual(packs[0].sources[0].article_refs, ("第二条", "第三条", "第四条", "第五条"))

    def test_education_surcharge_packs_use_official_fixed_rates(self):
        expected = {
            "education_surcharge": "0.03",
            "local_education_surcharge": "0.02",
        }
        for tax_id, rate in expected.items():
            with self.subTest(tax_id=tax_id):
                packs = tuple(
                    pack for pack in self.packs
                    if pack.tax_id == tax_id
                    and pack.maturity is PackMaturity.CALCULATION
                )
                self.assertEqual(len(packs), 1)
                self.assertEqual(packs[0].calculation.fixed_rate, rate)
                self.assertEqual(packs[0].calculation.tax_base_fact_key, "calc.tax_base")


if __name__ == "__main__":
    unittest.main()
