"""Versioned tax-pack registry, manifest validation, and period selection."""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass
import json
from importlib.resources import files
import re

from .tax_models import (
    DateRange,
    CalculationSpec,
    FactRequirement,
    PackMaturity,
    PackSegment,
    PackSelection,
    RuleSource,
    StatutoryBracketEntry,
    StatutoryBracketTable,
    StatutoryRateEntry,
    StatutoryRateRange,
    StatutoryRateTable,
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

_PACK_V1_KEYS = frozenset({
    "schema_version", "pack_id", "tax_id", "jurisdiction", "effective_from",
    "effective_to", "maturity", "requirements", "local_parameter_keys", "evaluator_id", "sources",
})
_PACK_V1_1_KEYS = _PACK_V1_KEYS | frozenset({
    "rule_version", "fact_catalog_version", "applicability_fact_keys",
})
_PACK_V1_2_KEYS = _PACK_V1_1_KEYS | frozenset({"calculation"})
_PACK_V1_3_KEYS = _PACK_V1_2_KEYS
_PACK_V1_4_KEYS = _PACK_V1_3_KEYS
_MAX_PACK_MANIFEST_BYTES = 256 * 1024
_MAX_PACK_MANIFESTS = 64
_TAX_IDS = frozenset(item.tax_id for item in TAX_DEFINITIONS)
_JURISDICTION = re.compile(r"CN(?:-\d{2})?$")


class TaxPackError(ValueError):
    """A public, stable manifest or selection error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TaxPackIssue:
    tax_id: str
    code: str


@dataclass(frozen=True, slots=True)
class TaxPackBundle:
    packs: tuple[TaxPack, ...]
    issues: tuple[TaxPackIssue, ...]


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


def _parse_source(value: object, *, schema_version: str) -> RuleSource:
    required = {"title", "url", "published_on"}
    if schema_version in {"1.1.0", "1.2.0", "1.3.0", "1.4.0"}:
        required |= {"issuer", "document_number", "article_refs", "verified_on"}
    if not isinstance(value, dict) or set(value) != required:
        raise TaxPackError("invalid_source_metadata" if schema_version in {"1.1.0", "1.2.0", "1.3.0", "1.4.0"} else "invalid_source")
    metadata_code = "invalid_source_metadata" if schema_version in {"1.1.0", "1.2.0", "1.3.0", "1.4.0"} else "invalid_source_url"
    url = _required_string(value["url"], metadata_code)
    if not url.startswith("https://"):
        raise TaxPackError(metadata_code)
    if schema_version == "1.0.0":
        return RuleSource(_required_string(value["title"], "invalid_source"), url, _parse_date(value["published_on"], "invalid_source_date"))
    refs = value["article_refs"]
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
        raise TaxPackError("invalid_source_metadata")
    try:
        return RuleSource(
            title=_required_string(value["title"], "invalid_source_metadata"), url=url,
            published_on=_parse_date(value["published_on"], "invalid_source_metadata"),
            issuer=_required_string(value["issuer"], "invalid_source_metadata"),
            document_number=_required_string(value["document_number"], "invalid_source_metadata"),
            article_refs=tuple(refs), verified_on=_parse_date(value["verified_on"], "invalid_source_metadata"),
        )
    except TaxPackError:
        raise
    except ValueError as error:
        raise TaxPackError("invalid_source_metadata") from error


def validate_pack_payload(payload: object) -> TaxPack:
    """Strictly parse one supported JSON manifest into a TaxPack."""
    if not isinstance(payload, dict) or not isinstance(payload.get("schema_version"), str):
        raise TaxPackError("unsupported_schema_version")
    schema_version = payload["schema_version"]
    if schema_version not in {"1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"}:
        raise TaxPackError("unsupported_schema_version")
    expected_keys = (
        _PACK_V1_KEYS if schema_version == "1.0.0"
        else _PACK_V1_1_KEYS if schema_version == "1.1.0"
        else _PACK_V1_2_KEYS if schema_version == "1.2.0"
        else _PACK_V1_3_KEYS if schema_version == "1.3.0"
        else _PACK_V1_4_KEYS
    )
    if set(payload) != expected_keys:
        raise TaxPackError("invalid_pack_keys")
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
    sources = tuple(_parse_source(item, schema_version=schema_version) for item in raw_sources)
    rule_version = "1.0.0"
    fact_catalog_version = ""
    applicability_fact_keys: tuple[str, ...] = ()
    calculation = None
    if schema_version in {"1.1.0", "1.2.0", "1.3.0", "1.4.0"}:
        rule_version = _required_string(payload["rule_version"], "missing_rule_version")
        fact_catalog_version = _required_string(payload["fact_catalog_version"], "missing_fact_catalog_version")
        raw_applicability = payload["applicability_fact_keys"]
        if not isinstance(raw_applicability, list) or not all(isinstance(item, str) and item.strip() for item in raw_applicability):
            raise TaxPackError("invalid_applicability_fact_keys")
        if schema_version == "1.1.0" and not raw_applicability:
            raise TaxPackError("invalid_applicability_fact_keys")
        applicability_fact_keys = tuple(raw_applicability)
        requirements_by_key = {item.fact_key: item for item in requirements}
        for key in applicability_fact_keys:
            requirement = requirements_by_key.get(key)
            if requirement is None:
                raise TaxPackError("unknown_applicability_fact_key")
            if requirement.answer_type != "boolean":
                raise TaxPackError("non_boolean_applicability_fact")
        if maturity is PackMaturity.SCREENING and (
            not raw_applicability or evaluator_id != "generic.applicability.screening.v1"
        ):
            raise TaxPackError("invalid_screening_evaluator")
        if schema_version in {"1.2.0", "1.3.0", "1.4.0"}:
            raw_calculation = payload["calculation"]
            expected_calculation_keys = {
                "formula_id", "tax_base_fact_key", "rate_fact_key", "deductions_fact_key"
            }
            if not isinstance(raw_calculation, dict):
                raise TaxPackError("invalid_calculation_spec")
            calculation_keys = set(raw_calculation)
            if schema_version in {"1.3.0", "1.4.0"}:
                valid_key_sets = (
                    expected_calculation_keys | {"rate_table"},
                    expected_calculation_keys | {"rate_range"},
                    expected_calculation_keys | {"fixed_rate"},
                )
                if schema_version == "1.4.0":
                    valid_key_sets += (
                        expected_calculation_keys | {"bracket_table"},
                    )
                if calculation_keys not in valid_key_sets:
                    raise TaxPackError("invalid_calculation_spec")
            elif calculation_keys != expected_calculation_keys:
                raise TaxPackError("invalid_calculation_spec")
            try:
                calculation_values = dict(raw_calculation)
                if "rate_table" in calculation_values:
                    raw_rate_table = calculation_values.pop("rate_table")
                    if not isinstance(raw_rate_table, dict) or set(raw_rate_table) != {
                        "selector_fact_key", "entries"
                    }:
                        raise ValueError("invalid_calculation_spec")
                    raw_entries = raw_rate_table["entries"]
                    if not isinstance(raw_entries, list):
                        raise ValueError("invalid_calculation_spec")
                    entries = []
                    for raw_entry in raw_entries:
                        optional_entry_keys = {
                            "maximum_tax_base",
                            "maximum_deductions",
                        }
                        if (
                            not isinstance(raw_entry, dict)
                            or not {"selector", "label_zh", "rate"}.issubset(raw_entry)
                            or set(raw_entry)
                            - {"selector", "label_zh", "rate"}
                            - optional_entry_keys
                        ):
                            raise ValueError("invalid_calculation_spec")
                        entries.append(StatutoryRateEntry(**raw_entry))
                    calculation_values["rate_table"] = StatutoryRateTable(
                        raw_rate_table["selector_fact_key"], tuple(entries)
                    )
                if "rate_range" in calculation_values:
                    raw_rate_range = calculation_values.pop("rate_range")
                    if not isinstance(raw_rate_range, dict) or set(raw_rate_range) != {
                        "minimum", "maximum"
                    }:
                        raise ValueError("invalid_calculation_spec")
                    calculation_values["rate_range"] = StatutoryRateRange(**raw_rate_range)
                if "bracket_table" in calculation_values:
                    raw_bracket_table = calculation_values.pop("bracket_table")
                    if (
                        not isinstance(raw_bracket_table, dict)
                        or set(raw_bracket_table)
                        not in (
                            {"entries"},
                            {"entries", "selector_fact_key", "required_selector"},
                        )
                        or not isinstance(raw_bracket_table["entries"], list)
                    ):
                        raise ValueError("invalid_calculation_spec")
                    bracket_entries = []
                    for raw_entry in raw_bracket_table["entries"]:
                        if not isinstance(raw_entry, dict) or set(raw_entry) != {
                            "maximum_tax_base",
                            "rate",
                            "quick_deduction",
                        }:
                            raise ValueError("invalid_calculation_spec")
                        bracket_entries.append(StatutoryBracketEntry(**raw_entry))
                    calculation_values["bracket_table"] = StatutoryBracketTable(
                        tuple(bracket_entries),
                        raw_bracket_table.get("selector_fact_key"),
                        raw_bracket_table.get("required_selector"),
                    )
                calculation = CalculationSpec(**calculation_values)
            except (TypeError, ValueError) as error:
                raise TaxPackError("invalid_calculation_spec") from error
            requirement_keys = {item.fact_key for item in requirements}
            bound_keys = {calculation.tax_base_fact_key}
            if calculation.rate_fact_key is not None:
                bound_keys.add(calculation.rate_fact_key)
            if calculation.rate_table is not None:
                bound_keys.add(calculation.rate_table.selector_fact_key)
            if (
                calculation.bracket_table is not None
                and calculation.bracket_table.selector_fact_key is not None
            ):
                bound_keys.add(calculation.bracket_table.selector_fact_key)
            if calculation.deductions_fact_key is not None:
                bound_keys.add(calculation.deductions_fact_key)
            if not bound_keys.issubset(requirement_keys):
                raise TaxPackError("invalid_calculation_spec")
            numeric_keys = {calculation.tax_base_fact_key}
            if calculation.rate_fact_key is not None:
                numeric_keys.add(calculation.rate_fact_key)
            if calculation.deductions_fact_key is not None:
                numeric_keys.add(calculation.deductions_fact_key)
            if any(
                requirements_by_key[key].answer_type != "decimal"
                for key in numeric_keys
            ):
                raise TaxPackError("invalid_calculation_spec")
            if (
                calculation.rate_table is not None
                and requirements_by_key[
                    calculation.rate_table.selector_fact_key
                ].answer_type
                != "choice"
            ):
                raise TaxPackError("invalid_calculation_spec")
            if (
                calculation.bracket_table is not None
                and calculation.bracket_table.selector_fact_key is not None
                and requirements_by_key[
                    calculation.bracket_table.selector_fact_key
                ].answer_type
                != "choice"
            ):
                raise TaxPackError("invalid_calculation_spec")
            if calculation.formula_id == "generic.base_times_rate.v1" and calculation.deductions_fact_key is not None:
                raise TaxPackError("invalid_calculation_spec")
            if maturity is not PackMaturity.CALCULATION or evaluator_id != "generic.calculation.base_minus_deductions_times_rate.v1":
                raise TaxPackError("invalid_calculation_spec")
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
            rule_version=rule_version,
            fact_catalog_version=fact_catalog_version,
            applicability_fact_keys=applicability_fact_keys,
            calculation=calculation,
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
                and first.maturity is second.maturity
                and _periods_overlap(first.effective_period, second.effective_period)
            ):
                raise TaxPackError("overlapping_tax_packs")


def load_builtin_tax_packs() -> tuple[TaxPack, ...]:
    """Load every packaged JSON manifest in deterministic filename order."""
    package = files("open_tax_check.tax_packs")
    manifests = sorted((entry for entry in package.iterdir() if entry.name.endswith(".json") and not entry.name.startswith("fact-catalog")), key=lambda entry: entry.name)
    if len(manifests) > _MAX_PACK_MANIFESTS:
        raise TaxPackError("too_many_pack_manifests")
    packs: list[TaxPack] = []
    for manifest in manifests:
        try:
            if manifest.stat().st_size > _MAX_PACK_MANIFEST_BYTES:
                raise TaxPackError("pack_manifest_too_large")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except TaxPackError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise TaxPackError("invalid_pack_json") from error
        packs.append(validate_pack_payload(payload))
    result = tuple(packs)
    validate_pack_set(result)
    return result


def load_builtin_parameterized_calculation_packs() -> tuple[TaxPack, ...]:
    """Build explicit customer-confirmed calculation templates for all taxes."""
    screening_by_tax: dict[str, TaxPack] = {}
    for pack in load_builtin_tax_packs():
        if pack.maturity is PackMaturity.SCREENING:
            screening_by_tax.setdefault(pack.tax_id, pack)
    templates: list[TaxPack] = []
    for definition in TAX_DEFINITIONS:
        source_pack = screening_by_tax.get(definition.tax_id)
        if source_pack is None:
            raise TaxPackError("missing_screening_source")
        requirements = [
            FactRequirement(
                "calc.tax_base", "decimal",
                ("tax_return", "accounting_ledger", "contract_register"),
                100, True, True,
            ),
            FactRequirement(
                "calc.rate", "decimal",
                ("official_rule_source", "customer_confirmation"),
                100, True, True,
            ),
        ]
        formula_id = "generic.base_times_rate.v1"
        deductions_key = None
        if definition.tax_id == "vat":
            requirements.append(FactRequirement(
                "calc.deductions", "decimal",
                ("tax_return", "input_credit_schedule"),
                90, False, False,
            ))
            formula_id = "generic.base_minus_deductions_times_rate.v1"
            deductions_key = "calc.deductions"
        templates.append(TaxPack(
            pack_id=f"{definition.tax_id}-cn-2023-2026-parameterized-calculation-v1",
            tax_id=definition.tax_id,
            jurisdiction="CN",
            effective_period=DateRange(date(2023, 1, 1), date(2026, 12, 31)),
            maturity=PackMaturity.CALCULATION,
            requirements=tuple(requirements),
            local_parameter_keys=("calc.rate",) if source_pack.local_parameter_keys else (),
            evaluator_id="generic.calculation.base_minus_deductions_times_rate.v1",
            sources=source_pack.sources,
            rule_version="parameterized-2026.1",
            fact_catalog_version="zh-CN.v1",
            applicability_fact_keys=(),
            calculation=CalculationSpec(
                formula_id, "calc.tax_base", "calc.rate", deductions_key
            ),
        ))
    return tuple(templates)


_PACK_FILENAME_TAX_IDS = {
    "vat-cn-2023-2025.screening.json": "vat",
    "vat-cn-2026.screening.json": "vat",
    "vat-cn-2023-2025.calculation.json": "vat",
    "vat-cn-2026.calculation.json": "vat",
    "vat-cn-2023-2026.prototype.json": "vat",
    "consumption-tax-cn-2023-2026.screening.json": "consumption_tax",
    "enterprise-income-tax-cn-2023-2026.screening.json": "enterprise_income_tax",
    "enterprise-income-tax-cn-2023-2026.calculation.json": "enterprise_income_tax",
    "individual-income-tax-cn-2023-2026.screening.json": "individual_income_tax",
    "individual-income-tax-cn-2023-2026.calculation.json": "individual_income_tax",
    "resource-tax-cn-2023-2026.screening.json": "resource_tax",
    "resource-tax-cn-2023-2026.calculation.json": "resource_tax",
    "urban-maintenance-construction-tax-cn-2023-2026.screening.json": "urban_maintenance_construction_tax",
    "urban-maintenance-construction-tax-cn-2023-2026.calculation.json": "urban_maintenance_construction_tax",
    "house-property-tax-cn-2023-2026.screening.json": "house_property_tax",
    "house-property-tax-cn-2023-2026.calculation.json": "house_property_tax",
    "stamp-tax-cn-2023-2026.screening.json": "stamp_tax",
    "stamp-tax-cn-2023-08-27.calculation.json": "stamp_tax",
    "stamp-tax-cn-2023-08-28-2026.calculation.json": "stamp_tax",
    "urban-land-use-tax-cn-2023-2026.screening.json": "urban_land_use_tax",
    "urban-land-use-tax-cn-2023-2026.calculation.json": "urban_land_use_tax",
    "land-appreciation-tax-cn-2023-2026.screening.json": "land_appreciation_tax",
    "vehicle-purchase-tax-cn-2023-2026.screening.json": "vehicle_purchase_tax",
    "vehicle-purchase-tax-cn-2023-2026.calculation.json": "vehicle_purchase_tax",
    "vehicle-vessel-tax-cn-2023-2026.screening.json": "vehicle_vessel_tax",
    "vehicle-vessel-tax-cn-2023-2026.calculation.json": "vehicle_vessel_tax",
    "vessel-tonnage-tax-cn-2023-2026.screening.json": "vessel_tonnage_tax",
    "vessel-tonnage-tax-cn-2023-2026.calculation.json": "vessel_tonnage_tax",
    "cultivated-land-occupation-tax-cn-2023-2026.screening.json": "cultivated_land_occupation_tax",
    "cultivated-land-occupation-tax-cn-2023-2026.calculation.json": "cultivated_land_occupation_tax",
    "deed-tax-cn-2023-2026.screening.json": "deed_tax",
    "deed-tax-cn-2023-2026.calculation.json": "deed_tax",
    "tobacco-tax-cn-2023-2026.screening.json": "tobacco_tax",
    "tobacco-tax-cn-2023-2026.calculation.json": "tobacco_tax",
    "environmental-protection-tax-cn-2023-2026.screening.json": "environmental_protection_tax",
    "environmental-protection-tax-cn-2023-2026.calculation.json": "environmental_protection_tax",
    "customs-duty-cn-2023-2026.screening.json": "customs_duty",
    "customs-duty-cn-2023-2024-11.calculation.json": "customs_duty",
    "customs-duty-cn-2024-12-2026.calculation.json": "customs_duty",
    "education-surcharge-cn-2023-2026.screening.json": "education_surcharge",
    "education-surcharge-cn-2023-2026.calculation.json": "education_surcharge",
    "local-education-surcharge-cn-2023-2026.screening.json": "local_education_surcharge",
    "local-education-surcharge-cn-2023-2026.calculation.json": "local_education_surcharge",
}


def load_builtin_tax_pack_bundle() -> TaxPackBundle:
    """Load valid built-ins while isolating one malformed manifest by tax ID."""
    package = files("open_tax_check.tax_packs")
    packs: list[TaxPack] = []
    issues: list[TaxPackIssue] = []
    for manifest in sorted(
        (entry for entry in package.iterdir() if entry.name.endswith(".json") and not entry.name.startswith("fact-catalog")),
        key=lambda entry: entry.name,
    ):
        try:
            packs.append(validate_pack_payload(json.loads(manifest.read_text(encoding="utf-8"))))
        except Exception:
            tax_id = _PACK_FILENAME_TAX_IDS.get(manifest.name, "unknown")
            issues.append(TaxPackIssue(tax_id, "invalid_pack_json"))
    return TaxPackBundle(tuple(packs), tuple(issues))


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
    maturity: PackMaturity | None = None,
) -> PackSelection:
    """Select exact-jurisdiction, then national packs across an inclusive period."""
    validate_pack_set(packs)
    candidates = [
        pack for pack in packs
        if pack.tax_id == tax_id
        and (maturity is None or pack.maturity is maturity)
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
        if include_prototypes:
            prototypes = [pack for pack in choices if pack.maturity is PackMaturity.PROTOTYPE]
            if prototypes:
                choices = prototypes
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
