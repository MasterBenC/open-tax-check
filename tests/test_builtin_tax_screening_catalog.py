import unittest
from datetime import date

from open_tax_check.tax_models import DateRange, FactState, TaxCheckRequest, TaxFact, TaxStatus
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
}


class BuiltinTaxScreeningCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs = load_builtin_tax_packs()

    def test_core_inventory_and_periods(self):
        for tax_id, fact_key in EXPECTED.items():
            with self.subTest(tax_id=tax_id):
                segments = select_pack_segments(self.packs, tax_id=tax_id, jurisdiction="CN", period=PERIOD).segments
                self.assertTrue(segments)
                self.assertEqual(tuple(item.pack.applicability_fact_keys for item in segments), tuple((fact_key,) for _ in segments))
                self.assertTrue(all(item.pack.evaluator_id == "generic.applicability.screening.v1" for item in segments))
                self.assertTrue(all(item.pack.fact_catalog_version == "zh-CN.v1" for item in segments))

    def test_all_core_scenarios(self):
        for tax_id, fact_key in EXPECTED.items():
            packs = tuple(item.pack for item in select_pack_segments(self.packs, tax_id=tax_id, jurisdiction="CN", period=PERIOD).segments)
            for value, expected in ((False, TaxStatus.NOT_APPLICABLE), (True, TaxStatus.NEEDS_EVIDENCE)):
                result = screen_tax_items(TaxCheckRequest(PERIOD, "CN", (TaxFact(fact_key, value, FactState.CONFIRMED, confirmed_by="customer", confirmed_at="2026-07-28T10:00:00+08:00"),)), packs, builtin_tax_evaluators(), tax_ids=(tax_id,))[0]
                local_policy_ids = {"local_education_surcharge", "house_property_tax", "urban_land_use_tax", "vehicle_vessel_tax", "cultivated_land_occupation_tax", "deed_tax"}
                expected_status = TaxStatus.NEEDS_LOCAL_POLICY if tax_id in local_policy_ids and value else expected
                self.assertEqual(result.status, expected_status)


if __name__ == "__main__":
    unittest.main()
