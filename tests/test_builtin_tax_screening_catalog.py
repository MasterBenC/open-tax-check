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

    def test_vat_calculation_packs_cover_supported_statutory_paths(self):
        packs = tuple(
            pack
            for pack in self.packs
            if pack.tax_id == "vat"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(
            tuple((pack.effective_period.start, pack.effective_period.end) for pack in packs),
            (
                (date(2023, 1, 1), date(2025, 12, 31)),
                (date(2026, 1, 1), date(2026, 12, 31)),
            ),
        )
        for pack in packs:
            with self.subTest(pack_id=pack.pack_id):
                self.assertEqual(
                    pack.calculation.formula_id,
                    "generic.base_times_rate_minus_credits.v1",
                )
                self.assertEqual(pack.calculation.tax_base_fact_key, "vat.taxable_sales")
                self.assertEqual(
                    pack.calculation.deductions_fact_key,
                    "vat.input_tax_credits",
                )
                self.assertEqual(
                    pack.calculation.rate_table.selector_fact_key,
                    "vat.rate_path",
                )
                entries = {
                    entry.selector: entry
                    for entry in pack.calculation.rate_table.entries
                }
                self.assertEqual(
                    {key: entry.rate for key, entry in entries.items()},
                    {
                        "general_taxpayer_13": "0.13",
                        "general_taxpayer_9": "0.09",
                        "general_taxpayer_6": "0.06",
                        "small_scale_reduced_1": "0.01",
                    },
                )
                self.assertEqual(
                    entries["small_scale_reduced_1"].maximum_deductions,
                    "0",
                )

    def test_vat_general_and_small_scale_paths_calculate_from_confirmed_facts(self):
        period = DateRange(date(2026, 1, 1), date(2026, 1, 31))
        timestamp = "2026-07-29T10:00:00+08:00"
        cases = (
            ("general_taxpayer_13", "8000.00", "5000.00"),
            ("small_scale_reduced_1", "0", "1000.00"),
        )
        for rate_path, input_credits, expected_amount in cases:
            with self.subTest(rate_path=rate_path):
                facts = (
                    TaxFact(
                        "activity.has_vat_taxable_transaction",
                        True,
                        FactState.CONFIRMED,
                        ("vat-return",),
                        "customer",
                        timestamp,
                    ),
                    TaxFact(
                        "vat.taxable_sales",
                        "100000.00",
                        FactState.CONFIRMED,
                        ("vat-return",),
                        "customer",
                        timestamp,
                    ),
                    TaxFact(
                        "vat.input_tax_credits",
                        input_credits,
                        FactState.CONFIRMED,
                        ("vat-return",),
                        "customer",
                        timestamp,
                    ),
                    TaxFact(
                        "vat.rate_path",
                        rate_path,
                        FactState.CONFIRMED,
                        ("vat-return",),
                        "customer",
                        timestamp,
                    ),
                )

                result = screen_tax_items(
                    TaxCheckRequest(period, "CN", facts),
                    self.packs,
                    builtin_tax_evaluators(),
                    tax_ids=("vat",),
                )[0]

                self.assertEqual(result.status, TaxStatus.CALCULATED)
                self.assertEqual(result.calculation.amount, expected_amount)

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
        self.assertEqual(pack.calculation.tax_base_fact_key, "deed_tax.tax_base")
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
        self.assertEqual(table.selector_fact_key, "urban_maintenance_construction_tax.rate_path")
        self.assertEqual(
            {entry.selector: entry.rate for entry in table.entries},
            {
                "urban_area_standard": "0.07",
                "urban_area_half_relief": "0.035",
                "county_or_town_standard": "0.05",
                "county_or_town_half_relief": "0.025",
                "other_standard": "0.01",
                "other_half_relief": "0.005",
            },
        )
        self.assertEqual(packs[0].sources[0].article_refs, ("第二条", "第三条", "第四条", "第五条"))

    def test_education_surcharge_packs_use_official_fixed_rates(self):
        expected = {
            "education_surcharge": {"standard": "0.03", "qualified_half_relief": "0.015"},
            "local_education_surcharge": {"standard": "0.02", "qualified_half_relief": "0.01"},
        }
        for tax_id, rates in expected.items():
            with self.subTest(tax_id=tax_id):
                packs = tuple(
                    pack for pack in self.packs
                    if pack.tax_id == tax_id
                    and pack.maturity is PackMaturity.CALCULATION
                )
                self.assertEqual(len(packs), 1)
                self.assertEqual(
                    {
                        entry.selector: entry.rate
                        for entry in packs[0].calculation.rate_table.entries
                    },
                    rates,
                )
                self.assertEqual(
                    packs[0].calculation.tax_base_fact_key,
                    "surcharge.vat_consumption_tax_base",
                )

    def test_enterprise_income_tax_pack_has_audited_common_rate_paths(self):
        packs = tuple(
            pack for pack in self.packs
            if pack.tax_id == "enterprise_income_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(len(packs), 1)
        entries = {
            entry.selector: entry
            for entry in packs[0].calculation.rate_table.entries
        }
        self.assertEqual(entries["standard_resident_enterprise"].rate, "0.25")
        self.assertEqual(entries["qualified_high_technology_enterprise"].rate, "0.15")
        self.assertEqual(entries["qualified_small_low_profit_enterprise"].rate, "0.05")
        self.assertEqual(
            entries["qualified_small_low_profit_enterprise"].maximum_tax_base,
            "3000000.00",
        )

    def test_vehicle_purchase_and_tobacco_tax_use_fixed_statutory_rates(self):
        expected = {
            "vehicle_purchase_tax": ("vehicle_purchase_tax.tax_base", "0.10"),
            "tobacco_tax": ("tobacco_tax.purchase_price_total", "0.20"),
        }
        for tax_id, (base_key, rate) in expected.items():
            with self.subTest(tax_id=tax_id):
                packs = tuple(
                    pack
                    for pack in self.packs
                    if pack.tax_id == tax_id
                    and pack.maturity is PackMaturity.CALCULATION
                )
                self.assertEqual(len(packs), 1)
                self.assertEqual(packs[0].effective_period, PERIOD)
                self.assertEqual(
                    packs[0].calculation.formula_id,
                    "generic.base_times_rate.v1",
                )
                self.assertEqual(packs[0].calculation.tax_base_fact_key, base_key)
                self.assertEqual(packs[0].calculation.fixed_rate, rate)

    def test_house_property_tax_pack_uses_common_statutory_rate_paths(self):
        packs = tuple(
            pack
            for pack in self.packs
            if pack.tax_id == "house_property_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertEqual(pack.effective_period, PERIOD)
        self.assertEqual(
            pack.calculation.tax_base_fact_key,
            "house_property_tax.confirmed_tax_base",
        )
        self.assertEqual(
            pack.calculation.rate_table.selector_fact_key,
            "house_property_tax.rate_path",
        )
        self.assertEqual(
            {
                entry.selector: entry.rate
                for entry in pack.calculation.rate_table.entries
            },
            {
                "residual_value_standard": "0.012",
                "rental_income_standard": "0.12",
            },
        )

    def test_house_property_tax_calculates_only_after_local_policy_confirmation(self):
        period = DateRange(date(2026, 1, 1), date(2026, 12, 31))
        timestamp = "2026-07-29T10:00:00+08:00"
        common_facts = (
            TaxFact(
                "assets.has_owned_taxable_house",
                True,
                FactState.CONFIRMED,
                ("property-register",),
                "customer",
                timestamp,
            ),
            TaxFact(
                "house_property_tax.confirmed_tax_base",
                "1000000.00",
                FactState.CONFIRMED,
                ("property-tax-workpaper",),
                "customer",
                timestamp,
            ),
            TaxFact(
                "house_property_tax.rate_path",
                "residual_value_standard",
                FactState.CONFIRMED,
                ("property-tax-workpaper",),
                "customer",
                timestamp,
            ),
        )

        missing_policy = screen_tax_items(
            TaxCheckRequest(period, "CN", common_facts),
            self.packs,
            builtin_tax_evaluators(),
            tax_ids=("house_property_tax",),
        )[0]
        confirmed_policy = screen_tax_items(
            TaxCheckRequest(
                period,
                "CN",
                common_facts
                + (
                    TaxFact(
                        "policy.house_property_tax_local_rule",
                        "客户已确认适用省级减除幅度及征收期间",
                        FactState.CONFIRMED,
                        ("local-policy",),
                        "customer",
                        timestamp,
                    ),
                ),
            ),
            self.packs,
            builtin_tax_evaluators(),
            tax_ids=("house_property_tax",),
        )[0]

        self.assertEqual(missing_policy.status, TaxStatus.NEEDS_LOCAL_POLICY)
        self.assertEqual(confirmed_policy.status, TaxStatus.CALCULATED)
        self.assertEqual(confirmed_policy.calculation.amount, "12000.00")

    def test_individual_income_tax_pack_has_annual_comprehensive_brackets(self):
        packs = tuple(
            pack
            for pack in self.packs
            if pack.tax_id == "individual_income_tax"
            and pack.maturity is PackMaturity.CALCULATION
        )

        self.assertEqual(len(packs), 1)
        table = packs[0].calculation.bracket_table
        self.assertEqual(
            tuple(
                (entry.maximum_tax_base, entry.rate, entry.quick_deduction)
                for entry in table.entries
            ),
            (
                ("36000", "0.03", "0"),
                ("144000", "0.10", "2520"),
                ("300000", "0.20", "16920"),
                ("420000", "0.25", "31920"),
                ("660000", "0.30", "52920"),
                ("960000", "0.35", "85920"),
                (None, "0.45", "181920"),
            ),
        )
        self.assertEqual(
            table.required_selector,
            "resident_annual_comprehensive_income",
        )

    def test_individual_income_tax_annual_comprehensive_path_calculates(self):
        period = DateRange(date(2026, 1, 1), date(2026, 12, 31))
        timestamp = "2026-07-29T10:00:00+08:00"
        facts = (
            TaxFact(
                "activity.has_individual_income_tax_duty",
                True,
                FactState.CONFIRMED,
                ("annual-return",),
                "customer",
                timestamp,
            ),
            TaxFact(
                "individual_income_tax.annual_comprehensive_taxable_income",
                "100000",
                FactState.CONFIRMED,
                ("annual-return",),
                "customer",
                timestamp,
            ),
            TaxFact(
                "individual_income_tax.calculation_path",
                "resident_annual_comprehensive_income",
                FactState.CONFIRMED,
                ("annual-return",),
                "customer",
                timestamp,
            ),
        )

        result = screen_tax_items(
            TaxCheckRequest(period, "CN", facts),
            self.packs,
            builtin_tax_evaluators(),
            tax_ids=("individual_income_tax",),
        )[0]

        self.assertEqual(result.status, TaxStatus.CALCULATED)
        self.assertEqual(result.calculation.rate, "0.1")
        self.assertEqual(result.calculation.deductions, "2520.00")
        self.assertEqual(result.calculation.amount, "7480.00")

    def test_formal_calculation_packs_do_not_reuse_unrelated_tax_bases(self):
        base_keys = {
            pack.tax_id: pack.calculation.tax_base_fact_key
            for pack in self.packs
            if pack.maturity is PackMaturity.CALCULATION
        }

        self.assertEqual(base_keys["stamp_tax"], "stamp_tax.tax_base")
        self.assertEqual(base_keys["deed_tax"], "deed_tax.tax_base")
        for tax_id in (
            "urban_maintenance_construction_tax",
            "education_surcharge",
            "local_education_surcharge",
        ):
            self.assertEqual(
                base_keys[tax_id],
                "surcharge.vat_consumption_tax_base",
            )


if __name__ == "__main__":
    unittest.main()
