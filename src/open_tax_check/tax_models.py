"""Immutable contracts for the customer-self-use tax health-check API."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re


TAX_SCHEMA_VERSION = "1.0.0"
_INPUT_HASH = re.compile(r"[0-9a-f]{64}")
_JURISDICTION = re.compile(r"CN(?:-\d{2})?")
_CONFIRMATION_EVIDENCE_REFERENCE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
)
_SUPPORTED_ANSWER_TYPES = frozenset(
    {"text", "choice", "integer", "boolean", "date", "decimal"}
)


class FactState(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"


class TaxStatus(StrEnum):
    CALCULATED = "calculated"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_EVIDENCE = "needs_evidence"
    NEEDS_LOCAL_POLICY = "needs_local_policy"
    BLOCKED = "blocked"


class PackMaturity(StrEnum):
    PROTOTYPE = "prototype"
    SCREENING = "screening"
    CALCULATION = "calculation"


def _coerce_fact_state(value: FactState) -> FactState:
    try:
        return FactState(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_fact_state") from error


def _coerce_tax_status(value: TaxStatus) -> TaxStatus:
    try:
        return TaxStatus(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_tax_status") from error


def _coerce_pack_maturity(value: PackMaturity) -> PackMaturity:
    try:
        return PackMaturity(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_pack_maturity") from error


def _require_identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty_identifier")


def _require_identifiers(values: tuple[str, ...]) -> None:
    for value in values:
        _require_identifier(value)


def _validate_priority(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError("invalid_priority")


def _validate_fact_value(value: str | int | bool | None) -> None:
    if not isinstance(value, str | int | bool | type(None)):
        raise ValueError("invalid_fact_value")


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid_timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_timestamp")


class _EvaluationOutcomeValidationError(ValueError):
    """Identifies stable collection-validation failures for evaluator dispatch."""


def _canonical_evaluation_collection(value: object, code: str) -> tuple[object, ...]:
    if not isinstance(value, tuple | list):
        raise _EvaluationOutcomeValidationError(code)
    return tuple(value)


@dataclass(frozen=True, slots=True)
class TaxDefinition:
    tax_id: str
    name_zh: str
    category: str

    def __post_init__(self) -> None:
        _require_identifier(self.tax_id)


@dataclass(frozen=True, slots=True)
class PromptChoice:
    value: str | bool
    label_zh: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str | bool) or isinstance(self.value, str) and not self.value.strip():
            raise ValueError("invalid_prompt_choice")
        if not isinstance(self.label_zh, str) or not self.label_zh.strip():
            raise ValueError("invalid_prompt_text")


@dataclass(frozen=True, slots=True)
class FactPrompt:
    fact_key: str
    question_zh: str
    help_zh: str
    answer_type: str
    choices: tuple[PromptChoice, ...]
    evidence_help_zh: str

    def __post_init__(self) -> None:
        _require_identifier(self.fact_key)
        if not isinstance(self.answer_type, str) or self.answer_type not in _SUPPORTED_ANSWER_TYPES:
            raise ValueError("unsupported_answer_type")
        for value in (self.question_zh, self.help_zh, self.evidence_help_zh):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("invalid_prompt_text")
        choices = tuple(self.choices)
        object.__setattr__(self, "choices", choices)
        if not all(isinstance(choice, PromptChoice) for choice in choices):
            raise ValueError("invalid_prompt_choices")
        if self.answer_type == "boolean":
            if choices != (PromptChoice(True, "是"), PromptChoice(False, "否")):
                raise ValueError("invalid_prompt_choices")
        elif self.answer_type == "choice":
            if not choices or any(not isinstance(item.value, str) or not item.value.strip() for item in choices):
                raise ValueError("invalid_prompt_choices")
            if len({item.value for item in choices}) != len(choices):
                raise ValueError("invalid_prompt_choices")
        elif choices:
            raise ValueError("invalid_prompt_choices")


@dataclass(frozen=True, slots=True)
class RuleSource:
    title: str
    url: str
    published_on: date
    issuer: str = ""
    document_number: str = ""
    article_refs: tuple[str, ...] = ()
    verified_on: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_refs", tuple(self.article_refs))


@dataclass(frozen=True, slots=True)
class FactRequirement:
    fact_key: str
    answer_type: str
    evidence_types: tuple[str, ...]
    priority: int
    blocking: bool
    critical: bool

    def __post_init__(self) -> None:
        _require_identifier(self.fact_key)
        if (
            not isinstance(self.answer_type, str)
            or self.answer_type not in _SUPPORTED_ANSWER_TYPES
        ):
            raise ValueError("unsupported_answer_type")
        _validate_priority(self.priority)
        object.__setattr__(self, "evidence_types", tuple(self.evidence_types))


@dataclass(frozen=True, slots=True)
class StatutoryRateEntry:
    """One exact selector-to-rate mapping from a versioned official source."""

    selector: str
    label_zh: str
    rate: str
    maximum_tax_base: str | None = None
    maximum_deductions: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.selector)
        if not isinstance(self.label_zh, str) or not self.label_zh.strip():
            raise ValueError("invalid_statutory_rate_entry")
        if not isinstance(self.rate, str) or not self.rate.strip():
            raise ValueError("invalid_statutory_rate_entry")
        try:
            parsed = Decimal(self.rate)
        except InvalidOperation as error:
            raise ValueError("invalid_statutory_rate_entry") from error
        if not parsed.is_finite() or parsed < 0 or parsed > 1:
            raise ValueError("invalid_statutory_rate_entry")
        if self.maximum_tax_base is not None:
            try:
                maximum_tax_base = Decimal(self.maximum_tax_base)
            except (InvalidOperation, TypeError) as error:
                raise ValueError("invalid_statutory_rate_entry") from error
            if not maximum_tax_base.is_finite() or maximum_tax_base < 0:
                raise ValueError("invalid_statutory_rate_entry")
        if self.maximum_deductions is not None:
            try:
                maximum_deductions = Decimal(self.maximum_deductions)
            except (InvalidOperation, TypeError) as error:
                raise ValueError("invalid_statutory_rate_entry") from error
            if not maximum_deductions.is_finite() or maximum_deductions < 0:
                raise ValueError("invalid_statutory_rate_entry")


@dataclass(frozen=True, slots=True)
class StatutoryRateTable:
    """An immutable, exact-match statutory rate lookup."""

    selector_fact_key: str
    entries: tuple[StatutoryRateEntry, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.selector_fact_key)
        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)
        if (
            not entries
            or not all(isinstance(entry, StatutoryRateEntry) for entry in entries)
            or len({entry.selector for entry in entries}) != len(entries)
        ):
            raise ValueError("invalid_statutory_rate_table")


@dataclass(frozen=True, slots=True)
class StatutoryBracketEntry:
    """One upper-bounded progressive band with an official quick deduction."""

    maximum_tax_base: str | None
    rate: str
    quick_deduction: str

    def __post_init__(self) -> None:
        try:
            rate = Decimal(self.rate)
            quick_deduction = Decimal(self.quick_deduction)
            maximum = (
                Decimal(self.maximum_tax_base)
                if self.maximum_tax_base is not None
                else None
            )
        except (InvalidOperation, TypeError) as error:
            raise ValueError("invalid_statutory_bracket_entry") from error
        if (
            not rate.is_finite()
            or rate < 0
            or rate > 1
            or not quick_deduction.is_finite()
            or quick_deduction < 0
            or (
                maximum is not None
                and (not maximum.is_finite() or maximum < 0)
            )
        ):
            raise ValueError("invalid_statutory_bracket_entry")


@dataclass(frozen=True, slots=True)
class StatutoryBracketTable:
    """Ordered progressive bands selected deterministically from the tax base."""

    entries: tuple[StatutoryBracketEntry, ...]
    selector_fact_key: str | None = None
    required_selector: str | None = None

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)
        if (self.selector_fact_key is None) != (self.required_selector is None):
            raise ValueError("invalid_statutory_bracket_table")
        if self.selector_fact_key is not None:
            _require_identifier(self.selector_fact_key)
            _require_identifier(self.required_selector)
        finite_maxima = [
            Decimal(entry.maximum_tax_base)
            for entry in entries
            if entry.maximum_tax_base is not None
        ]
        if (
            not entries
            or not all(isinstance(entry, StatutoryBracketEntry) for entry in entries)
            or entries[-1].maximum_tax_base is not None
            or any(entry.maximum_tax_base is None for entry in entries[:-1])
            or finite_maxima != sorted(finite_maxima)
            or len(set(finite_maxima)) != len(finite_maxima)
        ):
            raise ValueError("invalid_statutory_bracket_table")


@dataclass(frozen=True, slots=True)
class StatutoryRateRange:
    """Inclusive statutory bounds for a customer-confirmed local rate."""

    minimum: str
    maximum: str

    def __post_init__(self) -> None:
        try:
            minimum = Decimal(self.minimum)
            maximum = Decimal(self.maximum)
        except (InvalidOperation, TypeError) as error:
            raise ValueError("invalid_statutory_rate_range") from error
        if (
            not minimum.is_finite()
            or not maximum.is_finite()
            or minimum < 0
            or maximum > 1
            or minimum > maximum
        ):
            raise ValueError("invalid_statutory_rate_range")


@dataclass(frozen=True, slots=True)
class CalculationSpec:
    """Allowlisted fact bindings for a deterministic calculation evaluator."""

    formula_id: str
    tax_base_fact_key: str
    rate_fact_key: str | None = None
    deductions_fact_key: str | None = None
    rate_table: StatutoryRateTable | None = None
    rate_range: StatutoryRateRange | None = None
    fixed_rate: str | None = None
    bracket_table: StatutoryBracketTable | None = None

    def __post_init__(self) -> None:
        for value in (self.formula_id, self.tax_base_fact_key):
            _require_identifier(value)
        if sum(
            source is not None
            for source in (
                self.rate_fact_key,
                self.rate_table,
                self.fixed_rate,
                self.bracket_table,
            )
        ) != 1:
            raise ValueError("invalid_calculation_spec")
        if self.rate_fact_key is not None:
            _require_identifier(self.rate_fact_key)
        if self.rate_table is not None and not isinstance(self.rate_table, StatutoryRateTable):
            raise ValueError("invalid_calculation_spec")
        if self.bracket_table is not None and not isinstance(
            self.bracket_table, StatutoryBracketTable
        ):
            raise ValueError("invalid_calculation_spec")
        if self.rate_range is not None and (
            self.rate_fact_key is None
            or self.rate_table is not None
            or self.fixed_rate is not None
            or self.bracket_table is not None
            or not isinstance(self.rate_range, StatutoryRateRange)
        ):
            raise ValueError("invalid_calculation_spec")
        if self.fixed_rate is not None:
            try:
                parsed_fixed_rate = Decimal(self.fixed_rate)
            except (InvalidOperation, TypeError) as error:
                raise ValueError("invalid_calculation_spec") from error
            if (
                not parsed_fixed_rate.is_finite()
                or parsed_fixed_rate < 0
                or parsed_fixed_rate > 1
            ):
                raise ValueError("invalid_calculation_spec")
        if self.deductions_fact_key is not None:
            _require_identifier(self.deductions_fact_key)


@dataclass(frozen=True, slots=True)
class CalculationRecord:
    """Auditable, serialized output of a confirmed tax calculation."""

    formula_id: str
    tax_base: str
    deductions: str
    net_base: str
    rate: str
    amount: str
    evidence_refs: tuple[str, ...]
    rate_selector: str | None = None
    rate_selector_label_zh: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.formula_id)
        for value in (self.tax_base, self.deductions, self.net_base, self.rate, self.amount):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("invalid_calculation_record")
        refs = tuple(self.evidence_refs)
        if any(not isinstance(reference, str) or not reference.strip() for reference in refs):
            raise ValueError("invalid_calculation_record")
        object.__setattr__(self, "evidence_refs", refs)
        if (self.rate_selector is None) != (self.rate_selector_label_zh is None):
            raise ValueError("invalid_calculation_record")
        if self.rate_selector is not None:
            _require_identifier(self.rate_selector)
            if not isinstance(self.rate_selector_label_zh, str) or not self.rate_selector_label_zh.strip():
                raise ValueError("invalid_calculation_record")


@dataclass(frozen=True, slots=True)
class TaxFact:
    key: str
    value: str | int | bool | None
    state: FactState
    evidence_refs: tuple[str, ...] = ()
    confirmed_by: str | None = None
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.key)
        _validate_fact_value(self.value)
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "state", _coerce_fact_state(self.state))
        if self.state is FactState.CONFIRMED:
            if not isinstance(self.confirmed_by, str) or not self.confirmed_by.strip() or self.confirmed_at is None:
                raise ValueError("invalid_confirmation")
            _validate_timestamp(self.confirmed_at)
        elif self.state is FactState.INFERRED and (
            self.confirmed_by is not None or self.confirmed_at is not None
        ):
            raise ValueError("invalid_inferred_confirmation")


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("invalid_date_range")


@dataclass(frozen=True, slots=True)
class TaxPack:
    pack_id: str
    tax_id: str
    jurisdiction: str
    effective_period: DateRange
    maturity: PackMaturity
    requirements: tuple[FactRequirement, ...]
    local_parameter_keys: tuple[str, ...]
    evaluator_id: str | None
    sources: tuple[RuleSource, ...]
    rule_version: str = "1.0.0"
    fact_catalog_version: str = ""
    applicability_fact_keys: tuple[str, ...] = ()
    calculation: CalculationSpec | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.pack_id)
        _require_identifier(self.tax_id)
        _require_identifier(self.jurisdiction)
        object.__setattr__(self, "maturity", _coerce_pack_maturity(self.maturity))
        if self.evaluator_id is not None:
            _require_identifier(self.evaluator_id)
        object.__setattr__(self, "requirements", tuple(self.requirements))
        object.__setattr__(self, "local_parameter_keys", tuple(self.local_parameter_keys))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "applicability_fact_keys", tuple(self.applicability_fact_keys))
        _require_identifiers(self.local_parameter_keys)
        if self.maturity is PackMaturity.SCREENING:
            _require_identifier(self.rule_version)
            _require_identifier(self.fact_catalog_version)
            if not self.applicability_fact_keys:
                raise ValueError("invalid_applicability_fact_keys")
            requirements = {item.fact_key: item for item in self.requirements}
            for fact_key in self.applicability_fact_keys:
                requirement = requirements.get(fact_key)
                if requirement is None:
                    raise ValueError("unknown_applicability_fact_key")
                if requirement.answer_type != "boolean":
                    raise ValueError("non_boolean_applicability_fact")
        if self.calculation is not None and not isinstance(self.calculation, CalculationSpec):
            raise ValueError("invalid_calculation_spec")
        if self.calculation is not None:
            requirement_keys = {item.fact_key for item in self.requirements}
            bound_keys = {self.calculation.tax_base_fact_key}
            if self.calculation.rate_fact_key is not None:
                bound_keys.add(self.calculation.rate_fact_key)
            if self.calculation.rate_table is not None:
                bound_keys.add(self.calculation.rate_table.selector_fact_key)
            if self.calculation.deductions_fact_key is not None:
                bound_keys.add(self.calculation.deductions_fact_key)
            if not bound_keys.issubset(requirement_keys):
                raise ValueError("invalid_calculation_spec")
            if (
                self.calculation.formula_id == "generic.base_times_rate.v1"
                and self.calculation.deductions_fact_key is not None
            ):
                raise ValueError("invalid_calculation_spec")


@dataclass(frozen=True, slots=True)
class PackSegment:
    period: DateRange
    pack: TaxPack


@dataclass(frozen=True, slots=True)
class PackSelection:
    segments: tuple[PackSegment, ...]
    uncovered_periods: tuple[DateRange, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "uncovered_periods", tuple(self.uncovered_periods))


@dataclass(frozen=True, slots=True)
class FactGap:
    fact_key: str
    reason_code: str
    answer_type: str
    evidence_types: tuple[str, ...]
    priority: int
    blocking: bool

    def __post_init__(self) -> None:
        _require_identifier(self.fact_key)
        _validate_priority(self.priority)
        object.__setattr__(self, "evidence_types", tuple(self.evidence_types))


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    status: TaxStatus
    diagnostics: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    calculation: CalculationRecord | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _coerce_tax_status(self.status))
        object.__setattr__(
            self,
            "diagnostics",
            _canonical_evaluation_collection(self.diagnostics, "invalid_evaluation_diagnostics"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _canonical_evaluation_collection(self.evidence_refs, "invalid_evaluation_evidence_refs"),
        )
        if self.calculation is not None and not isinstance(self.calculation, CalculationRecord):
            raise ValueError("invalid_evaluation_calculation")


@dataclass(frozen=True, slots=True)
class TaxResult:
    tax_id: str
    status: TaxStatus
    rule_pack_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    gaps: tuple[FactGap, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    rule_sources: tuple[RuleSource, ...] = ()
    calculation: CalculationRecord | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.tax_id)
        object.__setattr__(self, "status", _coerce_tax_status(self.status))
        object.__setattr__(self, "rule_pack_ids", tuple(self.rule_pack_ids))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "gaps", tuple(self.gaps))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "rule_sources", tuple(self.rule_sources))
        _require_identifiers(self.rule_pack_ids)
        if self.status is TaxStatus.NOT_APPLICABLE and not self.evidence_refs:
            raise ValueError("not_applicable_without_evidence")
        if self.calculation is not None and not isinstance(self.calculation, CalculationRecord):
            raise ValueError("invalid_tax_result_calculation")


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    fact_key: str
    tax_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    answer_type: str
    evidence_types: tuple[str, ...]
    priority: int
    blocking: bool
    question_zh: str = ""
    help_zh: str = ""
    evidence_help_zh: str = ""
    choices: tuple[PromptChoice, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.question_id)
        _require_identifier(self.fact_key)
        _validate_priority(self.priority)
        object.__setattr__(self, "tax_ids", tuple(self.tax_ids))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "evidence_types", tuple(self.evidence_types))
        object.__setattr__(self, "choices", tuple(self.choices))
        _require_identifiers(self.tax_ids)


@dataclass(frozen=True, slots=True)
class Confirmation:
    question_id: str
    fact_key: str
    value: str | int | bool | None
    confirmed_by: str
    confirmed_at: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.question_id)
        _require_identifier(self.fact_key)
        _validate_fact_value(self.value)
        if not isinstance(self.confirmed_by, str) or not self.confirmed_by.strip():
            raise ValueError("invalid_confirmation")
        _validate_timestamp(self.confirmed_at)
        if not isinstance(self.evidence_refs, tuple | list):
            raise ValueError("invalid_confirmation_evidence_refs")
        evidence_refs = tuple(self.evidence_refs)
        if any(
            not isinstance(reference, str)
            or _CONFIRMATION_EVIDENCE_REFERENCE.fullmatch(reference) is None
            for reference in evidence_refs
        ):
            raise ValueError("invalid_confirmation_evidence_ref")
        object.__setattr__(self, "evidence_refs", evidence_refs)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    occurred_at: str
    tax_ids: tuple[str, ...] = ()
    fact_keys: tuple[str, ...] = ()
    confirmation_digest: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.event_type)
        _validate_timestamp(self.occurred_at)
        object.__setattr__(self, "tax_ids", tuple(self.tax_ids))
        object.__setattr__(self, "fact_keys", tuple(self.fact_keys))
        _require_identifiers(self.tax_ids)
        _require_identifiers(self.fact_keys)


@dataclass(frozen=True, slots=True)
class TaxCheckRequest:
    period: DateRange
    jurisdiction: str
    facts: tuple[TaxFact, ...] = ()
    input_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.jurisdiction, str)
            or _JURISDICTION.fullmatch(self.jurisdiction) is None
        ):
            raise ValueError("invalid_jurisdiction")
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "input_hashes", tuple(self.input_hashes))
        if any(
            not isinstance(input_hash, str)
            or _INPUT_HASH.fullmatch(input_hash) is None
            for input_hash in self.input_hashes
        ):
            raise ValueError("invalid_input_hash")
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("duplicate_fact_key")


@dataclass(frozen=True, slots=True)
class TaxCheckSnapshot:
    request: TaxCheckRequest
    results: tuple[TaxResult, ...]
    questions: tuple[Question, ...]
    audit_events: tuple[AuditEvent, ...]
    revision: int
    schema_version: str = TAX_SCHEMA_VERSION
    question_history: tuple[Question, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "questions", tuple(self.questions))
        object.__setattr__(self, "audit_events", tuple(self.audit_events))
        object.__setattr__(self, "question_history", tuple(self.question_history))
        if len({result.tax_id for result in self.results}) != len(self.results):
            raise ValueError("duplicate_result_tax_id")
