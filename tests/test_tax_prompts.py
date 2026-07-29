import unittest
from unittest.mock import patch

from open_tax_check.tax_prompts import (
    FactCatalogError,
    load_builtin_fact_catalog,
    validate_fact_catalog_payload,
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


def catalog(**overrides):
    value = {
        "schema_version": "1.0.0", "catalog_version": "zh-CN.v1", "locale": "zh-CN",
        "prompts": [{
            "fact_key": "activity.has_vat_taxable_transaction", "question_zh": "是否发生应税交易？",
            "help_zh": "按实际业务回答。", "answer_type": "boolean",
            "choices": [{"value": True, "label_zh": "是"}, {"value": False, "label_zh": "否"}],
            "evidence_help_zh": "请提供申报表。",
        }],
    }
    value.update(overrides)
    return value


class TaxPromptTests(unittest.TestCase):
    def test_builtin_stamp_tax_item_prompt_has_all_statutory_choices(self):
        prompt = load_builtin_fact_catalog()["stamp_tax.tax_item"]

        self.assertEqual(prompt.answer_type, "choice")
        self.assertEqual(
            tuple((choice.value, choice.label_zh) for choice in prompt.choices),
            (
                ("loan_contract", "借款合同"),
                ("finance_lease_contract", "融资租赁合同"),
                ("sale_contract", "买卖合同"),
                ("work_contract", "承揽合同"),
                ("construction_contract", "建设工程合同"),
                ("transport_contract", "运输合同"),
                ("technology_contract", "技术合同"),
                ("lease_contract", "租赁合同"),
                ("custody_contract", "保管合同"),
                ("warehousing_contract", "仓储合同"),
                ("property_insurance_contract", "财产保险合同"),
                ("land_use_right_grant", "土地使用权出让书据"),
                ("land_or_building_transfer", "土地使用权、房屋等权属转让书据"),
                ("equity_transfer", "股权转让书据"),
                ("intellectual_property_transfer", "知识产权转让书据"),
                ("business_books", "营业账簿"),
                ("securities_transaction", "证券交易"),
            ),
        )
        self.assertIn("不得由AI自动判断", prompt.help_zh)

    def test_builtin_calculation_base_prompt_is_customer_facing_chinese(self):
        prompt = load_builtin_fact_catalog()["calc.tax_base"]

        self.assertEqual(prompt.answer_type, "decimal")
        self.assertIn("计税依据", prompt.question_zh)
        self.assertEqual(prompt.choices, ())

    def test_builtin_deed_tax_rate_prompt_requires_local_policy_evidence(self):
        prompt = load_builtin_fact_catalog()["deed_tax.applicable_rate"]

        self.assertEqual(prompt.answer_type, "decimal")
        self.assertIn("省级", prompt.question_zh)
        self.assertIn("3%至5%", prompt.help_zh)
        self.assertIn("不得由AI", prompt.help_zh)
        self.assertEqual(prompt.choices, ())

    def test_builtin_urban_maintenance_location_prompt_has_three_legal_categories(self):
        prompt = load_builtin_fact_catalog()[
            "urban_maintenance_construction_tax.location_category"
        ]

        self.assertEqual(prompt.answer_type, "choice")
        self.assertEqual(
            tuple((choice.value, choice.label_zh) for choice in prompt.choices),
            (("urban_area", "市区"), ("county_or_town", "县城、镇"), ("other", "其他地区")),
        )
        self.assertIn("不得由AI", prompt.help_zh)

    def test_builtin_enterprise_income_tax_path_prompt_requires_eligibility_confirmation(self):
        prompt = load_builtin_fact_catalog()["enterprise_income_tax.rate_path"]

        self.assertEqual(prompt.answer_type, "choice")
        self.assertEqual(
            tuple(choice.value for choice in prompt.choices),
            (
                "standard_resident_enterprise",
                "qualified_high_technology_enterprise",
                "qualified_small_low_profit_enterprise",
            ),
        )
        self.assertIn("不得由AI", prompt.help_zh)
        self.assertIn("300万元", prompt.help_zh)

    def test_builtin_catalog_is_immutable_mapping(self):
        prompts = load_builtin_fact_catalog()
        self.assertEqual(prompts["activity.has_vat_taxable_transaction"].question_zh, "2023至2026年是否发生过销售货物、服务、无形资产、不动产或者进口货物？")
        with self.assertRaises(TypeError):
            prompts["another"] = object()

    def test_catalog_rejects_untrusted_contract_variants(self):
        cases = [
            (catalog(prompts=[{**catalog()["prompts"][0], "fact_key": ""}]), "invalid_fact_key"),
            (catalog(prompts=catalog()["prompts"] * 2), "duplicate_fact_key"),
            (catalog(prompts=[{**catalog()["prompts"][0], "answer_type": "currency"}]), "unsupported_answer_type"),
            (catalog(prompts=[{**catalog()["prompts"][0], "question_zh": ""}]), "invalid_prompt_text"),
            (catalog(unexpected=True), "invalid_catalog_keys"),
            (catalog(catalog_version="zh-CN.v2"), "unsupported_catalog_version"),
        ]
        for payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(FactCatalogError, f"^{code}$"):
                    validate_fact_catalog_payload(payload)

    def test_catalog_rejects_collection_limit_before_parsing(self):
        with self.assertRaisesRegex(FactCatalogError, "^too_many_fact_prompts$"):
            validate_fact_catalog_payload(catalog(prompts=[object()] * 257))

    def test_catalog_file_limit_is_checked_before_json_parsing(self):
        oversized = _Entry("fact-catalog.zh-CN.v1.json", 512 * 1024 + 1, "not json")
        with patch("open_tax_check.tax_prompts.files", return_value=_Package([oversized])):
            with self.assertRaisesRegex(FactCatalogError, "^fact_catalog_too_large$"):
                load_builtin_fact_catalog()


if __name__ == "__main__":
    unittest.main()
