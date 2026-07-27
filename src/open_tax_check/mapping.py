"""Role-specific canonical field mapping with explicit review gates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
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

TRIAL_BALANCE_COMPOSITE_ALIASES = {
    "opening": {
        "amount": {"期初余额金额", "期初余额|金额", "期初金额"},
        "direction": {"期初余额方向", "期初余额|方向", "期初方向"},
        "debit_field": "opening_debit",
        "credit_field": "opening_credit",
    },
    "closing": {
        "amount": {"期末余额金额", "期末余额|金额", "期末金额"},
        "direction": {"期末余额方向", "期末余额|方向", "期末方向"},
        "debit_field": "closing_debit",
        "credit_field": "closing_credit",
    },
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
MAPPING_VERSION = "0.3.0"


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
class CompositeMapping:
    """A balance amount and its direction, which together produce split fields."""

    amount_field: str
    direction_field: str
    debit_field: str
    credit_field: str


@dataclass(frozen=True)
class MappingProposal:
    role: str
    mapping: dict[str, str]
    unresolved: tuple[str, ...]
    missing_required: tuple[str, ...]
    conflicts: dict[str, tuple[str, ...]]
    mapping_version: str = MAPPING_VERSION
    composites: dict[str, CompositeMapping] = field(default_factory=dict)

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


def _composite_components(role: str) -> dict[str, tuple[str, str]]:
    if role != "trial_balance":
        return {}
    return {
        _normalize(alias): (scope, component)
        for scope, fields in TRIAL_BALANCE_COMPOSITE_ALIASES.items()
        for component in ("amount", "direction")
        for alias in fields[component]
    }


def propose_mapping(role: str, headers: Sequence[str]) -> MappingProposal:
    if role not in ROLE_FIELDS:
        raise ValueError("unknown_mapping_role")

    aliases = _normalized_aliases(role)
    composite_components = _composite_components(role)
    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    targets: defaultdict[str, list[str]] = defaultdict(list)
    composite_sources: defaultdict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for header in headers:
        canonical = aliases.get(_normalize(header))
        if canonical is not None:
            mapping[header] = canonical
            targets[canonical].append(header)
            continue
        component = composite_components.get(_normalize(header))
        if component is None:
            unresolved.append(header)
            continue
        scope, kind = component
        composite_sources[scope][kind].append(header)

    conflicts = {
        canonical: tuple(source_headers)
        for canonical, source_headers in targets.items()
        if len(source_headers) > 1
    }
    composites: dict[str, CompositeMapping] = {}
    for scope, components in composite_sources.items():
        amount_headers = components["amount"]
        direction_headers = components["direction"]
        if len(amount_headers) == 1 and len(direction_headers) == 1:
            fields = TRIAL_BALANCE_COMPOSITE_ALIASES[scope]
            composite = CompositeMapping(
                amount_field=amount_headers[0],
                direction_field=direction_headers[0],
                debit_field=fields["debit_field"],
                credit_field=fields["credit_field"],
            )
            composites[scope] = composite
            for target in (composite.debit_field, composite.credit_field):
                if targets[target]:
                    conflicts[target] = tuple(
                        targets[target]
                        + [composite.amount_field, composite.direction_field]
                    )
            continue
        unresolved.extend(amount_headers)
        unresolved.extend(direction_headers)

    composite_targets = {
        field
        for composite in composites.values()
        for field in (composite.debit_field, composite.credit_field)
    }
    missing = tuple(
        sorted(required_fields(role) - set(mapping.values()) - composite_targets)
    )
    return MappingProposal(
        role=role,
        mapping=mapping,
        unresolved=tuple(unresolved),
        missing_required=missing,
        conflicts=conflicts,
        composites=composites,
    )


def map_headers(headers: list[str]) -> dict:
    """Return the legacy CSV journal mapping payload."""

    return propose_mapping("journal", headers).to_legacy_dict()
