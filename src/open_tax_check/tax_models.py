"""Immutable contracts for the customer-self-use tax health-check API."""

from dataclasses import dataclass
from datetime import date, datetime
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
        if self.maturity is PackMaturity.SCREENING and self.rule_version != "1.0.0":
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


@dataclass(frozen=True, slots=True)
class TaxResult:
    tax_id: str
    status: TaxStatus
    rule_pack_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    gaps: tuple[FactGap, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    rule_sources: tuple[RuleSource, ...] = ()

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
