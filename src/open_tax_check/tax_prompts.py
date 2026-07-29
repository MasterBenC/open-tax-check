"""Strict loading for the immutable Chinese fact-prompt catalog."""

from __future__ import annotations

import json
from importlib.resources import files
from types import MappingProxyType
from typing import Mapping

from .tax_models import FactPrompt, PromptChoice


_CATALOG_KEYS = frozenset({"schema_version", "catalog_version", "locale", "prompts"})
_PROMPT_KEYS = frozenset({"fact_key", "question_zh", "help_zh", "answer_type", "choices", "evidence_help_zh"})
_CHOICE_KEYS = frozenset({"value", "label_zh"})
_MAX_CATALOG_BYTES = 512 * 1024
_MAX_PROMPTS = 256


class FactCatalogError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_fact_catalog_payload(payload: object) -> Mapping[str, FactPrompt]:
    if not isinstance(payload, dict) or set(payload) != _CATALOG_KEYS:
        raise FactCatalogError("invalid_catalog_keys")
    if payload["schema_version"] != "1.0.0":
        raise FactCatalogError("unsupported_catalog_schema_version")
    if payload["catalog_version"] != "zh-CN.v1":
        raise FactCatalogError("unsupported_catalog_version")
    if payload["locale"] != "zh-CN":
        raise FactCatalogError("invalid_catalog_locale")
    raw_prompts = payload["prompts"]
    if not isinstance(raw_prompts, list):
        raise FactCatalogError("invalid_prompts")
    if len(raw_prompts) > _MAX_PROMPTS:
        raise FactCatalogError("too_many_fact_prompts")
    parsed: dict[str, FactPrompt] = {}
    for raw_prompt in raw_prompts:
        if not isinstance(raw_prompt, dict) or set(raw_prompt) != _PROMPT_KEYS:
            raise FactCatalogError("invalid_prompt")
        fact_key = raw_prompt["fact_key"]
        if not isinstance(fact_key, str) or not fact_key.strip():
            raise FactCatalogError("invalid_fact_key")
        if fact_key in parsed:
            raise FactCatalogError("duplicate_fact_key")
        raw_choices = raw_prompt["choices"]
        if not isinstance(raw_choices, list) or any(not isinstance(choice, dict) or set(choice) != _CHOICE_KEYS for choice in raw_choices):
            raise FactCatalogError("invalid_prompt_choices")
        try:
            parsed[fact_key] = FactPrompt(fact_key, raw_prompt["question_zh"], raw_prompt["help_zh"], raw_prompt["answer_type"], tuple(PromptChoice(choice["value"], choice["label_zh"]) for choice in raw_choices), raw_prompt["evidence_help_zh"])
        except ValueError as error:
            code = str(error)
            if code in {"unsupported_answer_type", "invalid_prompt_text", "invalid_prompt_choices"}:
                raise FactCatalogError(code) from error
            raise FactCatalogError("invalid_prompt") from error
    return MappingProxyType(parsed)


def load_builtin_fact_catalog() -> Mapping[str, FactPrompt]:
    package = files("open_tax_check.tax_packs")
    catalogs = [entry for entry in package.iterdir() if entry.name.startswith("fact-catalog") and entry.name.endswith(".json")]
    if len(catalogs) != 1:
        raise FactCatalogError("invalid_builtin_catalog")
    catalog = catalogs[0]
    try:
        if catalog.stat().st_size > _MAX_CATALOG_BYTES:
            raise FactCatalogError("fact_catalog_too_large")
        return validate_fact_catalog_payload(json.loads(catalog.read_text(encoding="utf-8")))
    except FactCatalogError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise FactCatalogError("invalid_catalog_json") from error
