"""Safe, metadata-only enumeration of top-level case inputs.

This module deliberately does not select or inspect workbook contents.  It
classifies directory entries from ``lstat`` metadata, hashes only ordinary
files within the configured limit, and derives cautious evidence-role signals
from filenames alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Mapping

from .schemas import CoverageItem, SourceRecord


SUPPORTED = frozenset({".csv", ".xlsx", ".xlsm", ".xls"})
MAX_ENTRIES = 50
MAX_FILE_BYTES = 100 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024

REQUIRED_ROLES = (
    "journal",
    "trial_balance",
    "balance_sheet",
    "income_statement",
    "vat_return",
    "cit_return",
    "entity_tax_profile",
    "chart_of_accounts",
)
RECOMMENDED_ROLES = (
    "cash_flow_statement",
    "output_invoice_schedule",
    "input_credit_schedule",
    "payroll_iit_social_insurance",
    "fixed_asset_register",
    "receivable_payable_aging",
    "related_party_schedule",
    "annual_cit_adjustment_schedule",
    "rnd_schedule",
    "employee_welfare_schedule",
    "business_entertainment_schedule",
    "advertising_schedule",
    "tax_incentive_documents",
)
RISK_TRIGGERED_ROLES = (
    "contracts",
    "invoices",
    "bank_records",
    "personnel_records",
    "ownership_records",
    "rnd_evidence",
    "cross_border_payment_evidence",
    "property_records",
    "other_targeted_support",
)


@dataclass(frozen=True, slots=True)
class IntakeIssue:
    """A stable, metadata-only intake diagnostic."""

    filename: str | None
    code: str


@dataclass(frozen=True, slots=True)
class IntakeResult:
    """The complete result of safe file-level intake."""

    sources: tuple[SourceRecord, ...]
    issues: tuple[IntakeIssue, ...]
    coverage: tuple[CoverageItem, ...]
    status: str
    next_action: str


def _entry_kind(path: Path) -> str:
    """Classify an entry without following directory links or reparse points."""

    metadata = _lstat(path)
    if metadata is None:
        return "other"
    return _metadata_kind(metadata)


def _metadata_kind(metadata: os.stat_result) -> str:
    mode = metadata.st_mode
    if stat.S_ISLNK(mode):
        return "symlink"

    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if attributes & reparse_flag:
        return "junction" if stat.S_ISDIR(mode) else "reparse_point"
    if stat.S_ISREG(mode):
        return "regular_file"
    if stat.S_ISDIR(mode):
        return "directory"
    return "other"


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _bounded_entries(input_dir: Path) -> tuple[Path, ...] | None:
    """Collect no more than the permitted direct entries before sorting them."""

    entries: list[Path] = []
    for path in input_dir.iterdir():
        entries.append(path)
        if len(entries) > MAX_ENTRIES:
            return None
    return tuple(sorted(entries, key=lambda item: item.name.casefold()))


def sha256_file(path: Path) -> str:
    """Hash a regular file in bounded chunks; callers must classify it first."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_record(path: Path, metadata: os.stat_result, digest: str, status: str) -> SourceRecord:
    return SourceRecord.from_digest(
        filename=path.name,
        digest=digest,
        size_bytes=metadata.st_size,
        extension=path.suffix.lower(),
        status=status,
    )


def _ignored_lock_record(path: Path, metadata: os.stat_result) -> SourceRecord:
    """Represent an ordinary Office lock without opening or hashing it."""

    return SourceRecord(
        source_id="",
        filename=path.name,
        sha256="",
        size_bytes=metadata.st_size,
        extension=path.suffix.lower(),
        status="ignored_office_lock",
    )


def _normalise_filename(filename: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", filename.casefold()).strip()


def _contains_filename_signal(normalised_name: str, signal: str) -> bool:
    if any("\u3400" <= character <= "\u9fff" for character in signal):
        return signal in normalised_name
    expression = r"(?<![a-z0-9])" + re.escape(signal).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(expression, normalised_name) is not None


_ROLE_FILENAME_SIGNALS: Mapping[str, tuple[str, ...]] = {
    "journal": ("journal", "general ledger", "序时账", "日记账"),
    "trial_balance": ("trial balance", "trialbalance", "科目余额表", "试算平衡"),
    "balance_sheet": ("balance sheet", "资产负债表"),
    "income_statement": ("income statement", "profit and loss", "利润表", "损益表"),
    "vat_return": ("vat return", "增值税申报", "增值税纳税申报"),
    "cit_return": ("cit return", "corporate income tax return", "企业所得税申报"),
    "entity_tax_profile": ("entity tax profile", "taxpayer profile", "纳税人信息", "税务档案"),
    "chart_of_accounts": ("chart of accounts", "accounting policy", "会计科目", "会计政策"),
    "cash_flow_statement": ("cash flow statement", "现金流量表"),
    "output_invoice_schedule": ("output invoice schedule", "销项发票", "销项税额"),
    "input_credit_schedule": ("input credit schedule", "进项抵扣", "进项税额"),
    "payroll_iit_social_insurance": ("payroll", "individual income tax", "social insurance", "工资薪金", "个人所得税", "社会保险"),
    "fixed_asset_register": ("fixed asset register", "固定资产", "折旧明细"),
    "receivable_payable_aging": ("receivable payable aging", "aging schedule", "账龄", "应收应付"),
    "related_party_schedule": ("related party schedule", "related transaction", "关联方", "关联交易"),
    "annual_cit_adjustment_schedule": ("annual cit adjustment", "corporate income tax adjustment", "企业所得税汇算", "纳税调整"),
    "rnd_schedule": ("rnd schedule", "r d schedule", "研发费用", "研究开发"),
    "employee_welfare_schedule": ("employee welfare", "职工福利"),
    "business_entertainment_schedule": ("business entertainment", "业务招待"),
    "advertising_schedule": ("advertising schedule", "广告费", "业务宣传"),
    "tax_incentive_documents": ("tax incentive", "优惠备案", "税收优惠"),
    "contracts": ("contracts", "contract", "合同"),
    "invoices": ("invoices", "invoice", "发票"),
    "bank_records": ("bank records", "bank statement", "银行流水", "银行对账"),
    "personnel_records": ("personnel records", "personnel", "人事档案", "员工档案"),
    "ownership_records": ("ownership records", "shareholder register", "股权", "股东名册"),
    "rnd_evidence": ("rnd evidence", "research evidence", "研发证据"),
    "cross_border_payment_evidence": ("cross border payment", "境外付款", "跨境支付"),
    "property_records": ("property records", "real estate", "房产", "不动产"),
    "other_targeted_support": ("targeted support", "专项支持材料"),
}


def detect_filename_roles(sources: Iterable[SourceRecord]) -> dict[str, tuple[str, ...]]:
    """Return deterministic, conservative role candidates from source filenames only."""

    candidates: dict[str, list[str]] = {}
    for source in sorted(sources, key=lambda item: (item.filename.casefold(), item.source_id)):
        if source.status not in {"accepted", "present_unparsed"}:
            continue
        normalised_name = _normalise_filename(Path(source.filename).stem)
        if not normalised_name:
            continue
        for role, signals in _ROLE_FILENAME_SIGNALS.items():
            if any(_contains_filename_signal(normalised_name, signal) for signal in signals):
                candidates.setdefault(role, []).append(source.source_id)
    return {role: tuple(sorted(set(source_ids))) for role, source_ids in candidates.items()}


def _source_ids_by_role(
    detected_roles: Mapping[str, Iterable[str]] | Iterable[str],
) -> dict[str, tuple[str, ...]]:
    if isinstance(detected_roles, Mapping):
        return {
            role: tuple(sorted(set(source_ids)))
            for role, source_ids in detected_roles.items()
        }
    return {role: () for role in detected_roles}


def build_coverage_matrix(
    detected_roles: Mapping[str, Iterable[str]] | Iterable[str],
) -> tuple[CoverageItem, ...]:
    """Build the complete three-tier evidence matrix without risk conclusions."""

    source_ids_by_role = _source_ids_by_role(detected_roles)
    matrix: list[CoverageItem] = []
    for tier, roles in (
        ("required", REQUIRED_ROLES),
        ("recommended", RECOMMENDED_ROLES),
        ("risk_triggered", RISK_TRIGGERED_ROLES),
    ):
        for role in roles:
            source_ids = source_ids_by_role.get(role, ())
            if tier == "risk_triggered":
                status = "not_applicable"
                procedure_status = "not_applicable"
            elif source_ids:
                status = "present_unparsed"
                procedure_status = "present_unparsed"
            else:
                status = "missing"
                procedure_status = "not_performed_missing_evidence"
            matrix.append(
                CoverageItem(
                    evidence_role=role,
                    tier=tier,
                    status=status,
                    procedure_status=procedure_status,
                    source_ids=source_ids,
                    trigger_rule_id=None,
                    requested_at=None,
                    request_status="not_requested",
                )
            )
    return tuple(matrix)


def _result(
    sources: Iterable[SourceRecord], issues: Iterable[IntakeIssue], *, review_period: dict | None
) -> IntakeResult:
    source_records = tuple(sources)
    issue_records = tuple(issues)
    coverage = build_coverage_matrix(detect_filename_roles(source_records))
    if issue_records:
        return IntakeResult(source_records, issue_records, coverage, "needs_intake_review", "resolve_intake_issues")
    if review_period is None:
        return IntakeResult(source_records, issue_records, coverage, "needs_intake_review", "set_case_period")
    return IntakeResult(source_records, issue_records, coverage, "partial_scope_ready", "inspect_workbooks")


def inventory_inputs(case_dir: Path, review_period: dict | None = None) -> IntakeResult:
    """Safely inventory only the direct children of ``case_dir / 'input'``."""

    input_dir = case_dir / "input"
    input_metadata = _lstat(input_dir)
    if input_metadata is None:
        return _result((), (IntakeIssue(None, "missing_input_directory"),), review_period=review_period)
    if _metadata_kind(input_metadata) != "directory":
        return _result((), (IntakeIssue("input", "unsafe_input_directory"),), review_period=review_period)
    try:
        entries = _bounded_entries(input_dir)
    except OSError:
        return _result((), (IntakeIssue(None, "missing_input_directory"),), review_period=review_period)

    if entries is None:
        return _result((), (IntakeIssue(None, "too_many_input_entries"),), review_period=review_period)

    sources: list[SourceRecord] = []
    issues: list[IntakeIssue] = []
    for path in entries:
        kind = _entry_kind(path)
        if kind != "regular_file":
            issues.append(IntakeIssue(path.name, f"rejected_{kind}"))
            continue

        metadata = _lstat(path)
        if metadata is None:
            issues.append(IntakeIssue(path.name, "unreadable_input_entry"))
            continue
        if path.name.startswith("~$"):
            sources.append(_ignored_lock_record(path, metadata))
            continue
        if metadata.st_size > MAX_FILE_BYTES:
            issues.append(IntakeIssue(path.name, "file_too_large"))
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            issues.append(IntakeIssue(path.name, "unreadable_input_entry"))
            continue
        status = "accepted" if path.suffix.lower() in SUPPORTED else "present_unparsed"
        sources.append(_source_record(path, metadata, digest, status))

    return _result(sources, issues, review_period=review_period)
