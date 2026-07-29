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
