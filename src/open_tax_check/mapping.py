"""Role-specific canonical field mapping with explicit review gates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
import re


JOURNAL_ALIASES = {
    "voucher_no": {"凭证号", "凭证编号", "凭证号码", "记账凭证号", "字号"},
    "date": {"日期", "记账日期", "凭证日期", "业务日期"},
    "account_code": {"科目编码", "科目代码", "科目编号"},
    "account_name": {"科目名称", "会计科目", "科目"},
    "summary": {"摘要", "业务说明", "凭证摘要", "业务摘要"},
    "debit": {"借方", "借方发生额", "本期借方", "借方金额"},
    "credit": {"贷方", "贷方发生额", "本期贷方", "贷方金额"},
    "counterparty": {"对方单位", "往来单位", "交易对手", "客商名称"},
}

TRIAL_BALANCE_ALIASES = {
    "account_code": {"科目编码", "科目代码", "科目编号"},
    "account_name": {"科目名称", "会计科目", "科目"},
    "opening_debit": {
        "期初余额|借方",
        "期初借方余额",
        "期初余额借方",
        "期初借方",
    },
    "opening_credit": {
        "期初余额|贷方",
        "期初贷方余额",
        "期初余额贷方",
        "期初贷方",
    },
    "period_debit": {
        "本期发生额|借方",
        "本期借方发生额",
        "本期发生额借方",
        "本期借方",
        "借方发生额",
    },
    "period_credit": {
        "本期发生额|贷方",
        "本期贷方发生额",
        "本期发生额贷方",
        "本期贷方",
        "贷方发生额",
    },
    "closing_debit": {
        "期末余额|借方",
        "期末借方余额",
        "期末余额借方",
        "期末借方",
    },
    "closing_credit": {
        "期末余额|贷方",
        "期末贷方余额",
        "期末余额贷方",
        "期末贷方",
    },
    "account_level": {"科目级次", "科目层级", "级次"},
    "parent_account_code": {"上级科目编码", "父级科目编码", "上级科目代码"},
    "auxiliary_type": {"辅助核算类型", "辅助类型"},
    "auxiliary_name": {"辅助核算名称", "辅助核算", "辅助名称"},
    "currency": {"币种", "货币"},
}

ROLE_FIELDS = {
    "journal": {
        "required": frozenset(
            {"voucher_no", "date", "account_name", "debit", "credit"}
        ),
        "optional": frozenset(
            {"account_code", "summary", "counterparty"}
        ),
    },
    "trial_balance": {
        "required": frozenset(
            {
                "account_name",
                "opening_debit",
                "opening_credit",
                "period_debit",
                "period_credit",
                "closing_debit",
                "closing_credit",
            }
        ),
        "optional": frozenset(
            {
                "account_code",
                "account_level",
                "parent_account_code",
                "auxiliary_type",
                "auxiliary_name",
                "currency",
            }
        ),
    },
}

ROLE_ALIASES = {
    "journal": JOURNAL_ALIASES,
    "trial_balance": TRIAL_BALANCE_ALIASES,
}

# Backward-compatible public names used by the CSV-only interface.
HEADER_ALIASES = JOURNAL_ALIASES
REQUIRED_JOURNAL_FIELDS = set(ROLE_FIELDS["journal"]["required"])
MAPPING_VERSION = "0.2.0"


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-—（）()\[\]【】]+", "", str(value or "")).lower()


def _normalized_aliases(role: str) -> dict[str, str]:
    return {
        _normalize(alias): canonical
        for canonical, aliases in ROLE_ALIASES[role].items()
        for alias in aliases
    }


NORMALIZED_ALIASES = _normalized_aliases("journal")


@dataclass(frozen=True)
class MappingProposal:
    role: str
    mapping: dict[str, str]
    unresolved: tuple[str, ...]
    missing_required: tuple[str, ...]
    conflicts: dict[str, tuple[str, ...]]
    mapping_version: str = MAPPING_VERSION

    @property
    def ready_for_confirmation(self) -> bool:
        return not self.unresolved and not self.missing_required and not self.conflicts

    def to_legacy_dict(self) -> dict:
        return {
            "mapping": dict(self.mapping),
            "unresolved": list(self.unresolved),
            "missing_required": list(self.missing_required),
            "conflicts": {
                canonical: list(headers)
                for canonical, headers in self.conflicts.items()
            },
            "ready_for_calculation": self.ready_for_confirmation,
            "mapping_version": self.mapping_version,
        }


def required_fields(role: str) -> frozenset[str]:
    fields = ROLE_FIELDS.get(role)
    if fields is None:
        raise ValueError("unknown_mapping_role")
    return fields["required"]


def propose_mapping(role: str, headers: Sequence[str]) -> MappingProposal:
    if role not in ROLE_FIELDS:
        raise ValueError("unknown_mapping_role")

    aliases = _normalized_aliases(role)
    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    targets: defaultdict[str, list[str]] = defaultdict(list)
    for header in headers:
        canonical = aliases.get(_normalize(header))
        if canonical is None:
            unresolved.append(header)
            continue
        mapping[header] = canonical
        targets[canonical].append(header)

    conflicts = {
        canonical: tuple(source_headers)
        for canonical, source_headers in targets.items()
        if len(source_headers) > 1
    }
    missing = tuple(sorted(required_fields(role) - set(mapping.values())))
    return MappingProposal(
        role=role,
        mapping=mapping,
        unresolved=tuple(unresolved),
        missing_required=missing,
        conflicts=conflicts,
    )


def map_headers(headers: list[str]) -> dict:
    """Return the legacy CSV journal mapping payload."""

    return propose_mapping("journal", headers).to_legacy_dict()
