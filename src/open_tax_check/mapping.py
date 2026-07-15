"""Canonical field mapping with explicit unresolved-field gates."""

from __future__ import annotations

import re


HEADER_ALIASES = {
    "voucher_no": {"凭证号", "凭证编号", "凭证号码", "字号"},
    "date": {"日期", "记账日期", "凭证日期", "业务日期"},
    "account_code": {"科目编码", "科目代码", "科目编号"},
    "account_name": {"科目名称", "会计科目", "科目"},
    "summary": {"摘要", "业务说明", "凭证摘要", "业务摘要"},
    "debit": {"借方", "借方发生额", "本期借方", "借方金额"},
    "credit": {"贷方", "贷方发生额", "本期贷方", "贷方金额"},
    "counterparty": {"对方单位", "往来单位", "交易对手", "客商名称"},
}

REQUIRED_JOURNAL_FIELDS = {"voucher_no", "date", "account_name", "debit", "credit"}


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-—（）()\[\]【】]+", "", str(value or "")).lower()


NORMALIZED_ALIASES = {
    _normalize(alias): canonical
    for canonical, aliases in HEADER_ALIASES.items()
    for alias in aliases
}


def map_headers(headers: list[str]) -> dict:
    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    for header in headers:
        canonical = NORMALIZED_ALIASES.get(_normalize(header))
        if canonical:
            mapping[header] = canonical
        else:
            unresolved.append(header)

    mapped_fields = set(mapping.values())
    missing_required = sorted(REQUIRED_JOURNAL_FIELDS - mapped_fields)
    return {
        "mapping": mapping,
        "unresolved": unresolved,
        "missing_required": missing_required,
        "ready_for_calculation": not unresolved and not missing_required,
        "mapping_version": "0.1.0",
    }

