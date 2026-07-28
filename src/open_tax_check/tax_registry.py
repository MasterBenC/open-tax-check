"""Versioned tax-pack registry, manifest validation, and period selection."""

from __future__ import annotations

from datetime import date, timedelta
import json
from importlib.resources import files
import re

from .tax_models import (
    DateRange,
    FactRequirement,
    PackMaturity,
    PackSegment,
    PackSelection,
    RuleSource,
    TaxDefinition,
    TaxPack,
)


TAX_DEFINITIONS: tuple[TaxDefinition, ...] = (
    TaxDefinition("vat", "增值税", "tax"),
    TaxDefinition("consumption_tax", "消费税", "tax"),
    TaxDefinition("enterprise_income_tax", "企业所得税", "tax"),
    TaxDefinition("individual_income_tax", "个人所得税", "tax"),
    TaxDefinition("resource_tax", "资源税", "tax"),
    TaxDefinition("urban_maintenance_construction_tax", "城市维护建设税", "tax"),
    TaxDefinition("house_property_tax", "房产税", "tax"),
    TaxDefinition("stamp_tax", "印花税", "tax"),
    TaxDefinition("urban_land_use_tax", "城镇土地使用税", "tax"),
    TaxDefinition("land_appreciation_tax", "土地增值税", "tax"),
    TaxDefinition("vehicle_purchase_tax", "车辆购置税", "tax"),
    TaxDefinition("vehicle_vessel_tax", "车船税", "tax"),
    TaxDefinition("vessel_tonnage_tax", "船舶吨税", "tax"),
    TaxDefinition("cultivated_land_occupation_tax", "耕地占用税", "tax"),
    TaxDefinition("deed_tax", "契税", "tax"),
    TaxDefinition("tobacco_tax", "烟叶税", "tax"),
    TaxDefinition("environmental_protection_tax", "环境保护税", "tax"),
    TaxDefinition("customs_duty", "关税", "tax"),
    TaxDefinition("education_surcharge", "教育费附加", "surcharge"),
    TaxDefinition("local_education_surcharge", "地方教育附加", "surcharge"),
)

_PACK_KEYS = frozenset({
    "schema_version", "pack_id", "tax_id", "jurisdiction", "effective_from",
    "effective_to", "maturity", "requirements", "local_parameter_keys", "evaluator_id", "sources",
})
_TAX_IDS = frozenset(item.tax_id for item in TAX_DEFINITIONS)
_JURISDICTION = re.compile(r"CN(?:-\d{2})?$")


class TaxPackError(ValueError):
    """A public, stable manifest or selection error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _required_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaxPackError(code)
    return value


def _parse_date(value: object, code: str) -> date:
    if not isinstance(value, str):
        raise TaxPackError(code)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise TaxPackError(code) from error


def _parse_requirement(value: object) -> FactRequirement:
    if not isinstance(value, dict) or set(value) != {
        "fact_key", "answer_type", "evidence_types", "priority", "blocking", "critical"
    }:
        raise TaxPackError("invalid_requirement")
    evidence_types = value["evidence_types"]
    if not isinstance(evidence_types, list) or not all(isinstance(item, str) and item.strip() for item in evidence_types):
        raise TaxPackError("invalid_requirement")
    if not isinstance(value["blocking"], bool) or not isinstance(value["critical"], bool):
        raise TaxPackError("invalid_requirement")
    try:
        return FactRequirement(
            fact_key=_required_string(value["fact_key"], "invalid_requirement"),
            answer_type=_required_string(value["answer_type"], "invalid_requirement"),
            evidence_types=tuple(evidence_types),
            priority=value["priority"],
            blocking=value["blocking"],
            critical=value["critical"],
        )
    except ValueError as error:
        if str(error) == "unsupported_answer_type":
            raise TaxPackError("unsupported_answer_type") from error
        raise TaxPackError("invalid_requirement") from error


def _parse_source(value: object) -> RuleSource:
    if not isinstance(value, dict) or set(value) != {"title", "url", "published_on"}:
        raise TaxPackError("invalid_source")
    url = _required_string(value["url"], "invalid_source_url")
    if not url.startswith("https://"):
        raise TaxPackError("invalid_source_url")
    return RuleSource(
        title=_required_string(value["title"], "invalid_source"),
        url=url,
        published_on=_parse_date(value["published_on"], "invalid_source_date"),
    )


def validate_pack_payload(payload: object) -> TaxPack:
    """Strictly parse one version 1.0.0 JSON manifest into a TaxPack."""
    if not isinstance(payload, dict) or set(payload) != _PACK_KEYS:
        raise TaxPackError("invalid_pack_keys")
    if payload["schema_version"] != "1.0.0":
        raise TaxPackError("unsupported_schema_version")
    tax_id = _required_string(payload["tax_id"], "invalid_tax_id")
    if tax_id not in _TAX_IDS:
        raise TaxPackError("unknown_tax_id")
    jurisdiction = _required_string(payload["jurisdiction"], "invalid_jurisdiction")
    if not _JURISDICTION.fullmatch(jurisdiction):
        raise TaxPackError("invalid_jurisdiction")
    effective_from = _parse_date(payload["effective_from"], "invalid_effective_from")
    effective_to = _parse_date(payload["effective_to"], "invalid_effective_to")
    try:
        period = DateRange(effective_from, effective_to)
    except ValueError as error:
        raise TaxPackError("invalid_effective_period") from error
    try:
        maturity = PackMaturity(payload["maturity"])
    except (TypeError, ValueError) as error:
        raise TaxPackError("invalid_maturity") from error
    raw_requirements = payload["requirements"]
    if not isinstance(raw_requirements, list):
        raise TaxPackError("invalid_requirements")
    requirements = tuple(_parse_requirement(item) for item in raw_requirements)
    raw_parameters = payload["local_parameter_keys"]
    if not isinstance(raw_parameters, list) or not all(isinstance(item, str) and item.strip() for item in raw_parameters):
        raise TaxPackError("invalid_local_parameter_keys")
    local_parameter_keys = tuple(raw_parameters)
    if not set(local_parameter_keys).issubset({item.fact_key for item in requirements}):
        raise TaxPackError("invalid_local_parameter_key")
    evaluator_id = payload["evaluator_id"]
    if evaluator_id is None:
        if maturity is not PackMaturity.PROTOTYPE:
            raise TaxPackError("missing_evaluator_id")
    else:
        evaluator_id = _required_string(evaluator_id, "invalid_evaluator_id")
    raw_sources = payload["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise TaxPackError("missing_sources")
    sources = tuple(_parse_source(item) for item in raw_sources)
    try:
        return TaxPack(
            pack_id=_required_string(payload["pack_id"], "invalid_pack_id"),
            tax_id=tax_id,
            jurisdiction=jurisdiction,
            effective_period=period,
            maturity=maturity,
            requirements=requirements,
            local_parameter_keys=local_parameter_keys,
            evaluator_id=evaluator_id,
            sources=sources,
        )
    except ValueError as error:
        raise TaxPackError("invalid_tax_pack") from error


def _periods_overlap(first: DateRange, second: DateRange) -> bool:
    return first.start <= second.end and second.start <= first.end


def validate_pack_set(packs: tuple[TaxPack, ...]) -> None:
    """Reject ambiguous non-prototype packs at equal jurisdiction priority."""
    for index, first in enumerate(packs):
        for second in packs[index + 1:]:
            if (
                first.maturity is not PackMaturity.PROTOTYPE
                and second.maturity is not PackMaturity.PROTOTYPE
                and first.tax_id == second.tax_id
                and first.jurisdiction == second.jurisdiction
                and _periods_overlap(first.effective_period, second.effective_period)
            ):
                raise TaxPackError("overlapping_tax_packs")


def load_builtin_tax_packs() -> tuple[TaxPack, ...]:
    """Load every packaged JSON manifest in deterministic filename order."""
    package = files("open_tax_check.tax_packs")
    manifests = sorted((entry for entry in package.iterdir() if entry.name.endswith(".json")), key=lambda entry: entry.name)
    packs: list[TaxPack] = []
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaxPackError("invalid_pack_json") from error
        packs.append(validate_pack_payload(payload))
    result = tuple(packs)
    validate_pack_set(result)
    return result


def _merge_periods(periods: list[DateRange]) -> tuple[DateRange, ...]:
    merged: list[DateRange] = []
    for period in periods:
        if merged and period.start <= merged[-1].end + timedelta(days=1):
            merged[-1] = DateRange(merged[-1].start, max(merged[-1].end, period.end))
        else:
            merged.append(period)
    return tuple(merged)


def select_pack_segments(
    packs: tuple[TaxPack, ...],
    *,
    tax_id: str,
    jurisdiction: str,
    period: DateRange,
    include_prototypes: bool = False,
) -> PackSelection:
    """Select exact-jurisdiction, then national packs across an inclusive period."""
    validate_pack_set(packs)
    candidates = [
        pack for pack in packs
        if pack.tax_id == tax_id
        and (include_prototypes or pack.maturity is not PackMaturity.PROTOTYPE)
        and _periods_overlap(pack.effective_period, period)
    ]
    boundaries = {period.start, period.end + timedelta(days=1)}
    for pack in candidates:
        boundaries.add(max(period.start, pack.effective_period.start))
        boundaries.add(min(period.end + timedelta(days=1), pack.effective_period.end + timedelta(days=1)))
    ordered_boundaries = sorted(boundaries)
    segments: list[PackSegment] = []
    uncovered: list[DateRange] = []
    for start, end_exclusive in zip(ordered_boundaries, ordered_boundaries[1:]):
        segment_period = DateRange(start, end_exclusive - timedelta(days=1))
        active = [pack for pack in candidates if pack.effective_period.start <= start <= pack.effective_period.end]
        exact = [pack for pack in active if pack.jurisdiction == jurisdiction]
        national = [pack for pack in active if pack.jurisdiction == "CN"]
        choices = exact or national
        if len(choices) > 1:
            raise TaxPackError("overlapping_tax_packs")
        if choices:
            selected = choices[0]
            if segments and segments[-1].pack == selected and segments[-1].period.end + timedelta(days=1) == segment_period.start:
                segments[-1] = PackSegment(DateRange(segments[-1].period.start, segment_period.end), selected)
            else:
                segments.append(PackSegment(segment_period, selected))
        else:
            uncovered.append(segment_period)
    return PackSelection(tuple(segments), _merge_periods(uncovered))
