"""Safe, metadata-only enumeration of top-level case inputs.

This module deliberately does not select or inspect workbook contents.  It
classifies directory entries from ``lstat`` metadata, hashes only ordinary
files within the configured limit, and derives cautious evidence-role signals
from filenames alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Iterable, Mapping
import unicodedata
import uuid
from zipfile import BadZipFile, ZipFile

from .schemas import CoverageItem, SourceRecord


SUPPORTED = frozenset({".csv", ".xlsx", ".xlsm", ".xls"})
ARCHIVE_EXTENSIONS = frozenset({".zip"})
MAX_ENTRIES = 50
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 500
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100
MAX_ARCHIVE_DEPTH = 8
_ARCHIVE_CACHE_PREFIX = ".opentaxcheck-archive-"
_ARCHIVE_CACHE_NAME = re.compile(r"^\.opentaxcheck-archive-[0-9a-f]{64}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_WINDOWS_DEVICE_DIGITS = str.maketrans("¹²³", "123")
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
# Each archive member can introduce one node per path component.  Shared
# ancestors reduce the actual count, so this is a conservative bounded cap.
_MAX_ARCHIVE_CACHE_NODES = MAX_ARCHIVE_MEMBERS * MAX_ARCHIVE_DEPTH
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


@dataclass(slots=True)
class _ArchiveBudget:
    members: int = 0
    uncompressed_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _ExtractedArchiveMember:
    path: Path
    digest: str
    size_bytes: int


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
        if _is_archive_cache_directory(path):
            continue
        entries.append(path)
        if len(entries) > MAX_ENTRIES:
            return None
    return tuple(sorted(entries, key=lambda item: item.name.casefold()))


def _is_archive_cache_directory(path: Path) -> bool:
    return _ARCHIVE_CACHE_NAME.fullmatch(path.name) is not None and _entry_kind(path) == "directory"


def sha256_file(path: Path) -> str:
    """Hash a regular file in bounded chunks; callers must classify it first."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size


def _snapshot_regular_file(path: Path, metadata: os.stat_result) -> tuple[bytes, str]:
    """Read one bounded file from a verified handle and bind the bytes to its path identity."""

    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if _metadata_kind(opened) != "regular_file" or _file_identity(opened) != _file_identity(metadata):
            raise OSError("input_changed_during_intake")
        content = handle.read(MAX_FILE_BYTES + 1)
        after_read = os.fstat(handle.fileno())
    current = _lstat(path)
    if (
        len(content) > MAX_FILE_BYTES
        or len(content) != metadata.st_size
        or _file_identity(after_read) != _file_identity(opened)
        or current is None
        or _file_identity(current) != _file_identity(opened)
        or _metadata_kind(current) != "regular_file"
    ):
        raise OSError("input_changed_during_intake")
    return content, hashlib.sha256(content).hexdigest()


def _source_record(path: Path, metadata: os.stat_result, digest: str, status: str) -> SourceRecord:
    return SourceRecord.from_digest(
        filename=path.name,
        digest=digest,
        size_bytes=metadata.st_size,
        extension=path.suffix.lower(),
        status=status,
    )


def _archive_member_name(raw_name: str) -> tuple[str, tuple[str, ...], str, bool]:
    """Return a portable member name, path parts, comparison key, and directory flag."""

    if not isinstance(raw_name, str) or not raw_name or "\x00" in raw_name:
        raise ValueError("archive_unsafe_member_name")
    normalised = raw_name.replace("\\", "/")
    is_directory = normalised.endswith("/")
    member_name = normalised[:-1] if is_directory else normalised
    raw_parts = member_name.split("/")
    if (
        normalised.startswith("/")
        or re.match(r"^[A-Za-z]:", normalised)
        or not raw_parts
        or any(part in {"", ".", ".."} for part in raw_parts)
        or len(raw_parts) > MAX_ARCHIVE_DEPTH
    ):
        raise ValueError("archive_unsafe_member_name")
    parts: list[str] = []
    for raw_part in raw_parts:
        part = unicodedata.normalize("NFC", raw_part).rstrip(" .")
        basename = (
            part.split(".", 1)[0]
            .rstrip(" .")
            .casefold()
            .translate(_WINDOWS_DEVICE_DIGITS)
        )
        if (
            not part
            or part in {".", ".."}
            or basename in _WINDOWS_RESERVED_NAMES
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part)
            or any(ord(character) < 32 for character in part)
        ):
            raise ValueError("archive_unsafe_member_name")
        parts.append(part)
    portable_name = "/".join(parts)
    return portable_name, tuple(parts), portable_name.casefold(), is_directory


def _archive_member_is_link(info) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return stat.S_ISLNK(mode)


def _safe_archive_cache_dir(input_dir: Path, digest: str) -> Path:
    cache_dir = input_dir / f"{_ARCHIVE_CACHE_PREFIX}{digest}"
    if cache_dir.parent != input_dir:
        raise ValueError("archive_cache_path_invalid")
    return cache_dir


def _cache_tree(cache_dir: Path) -> tuple[dict[tuple[str, ...], os.stat_result], set[tuple[str, ...]]] | None:
    files: dict[tuple[str, ...], os.stat_result] = {}
    directories: set[tuple[str, ...]] = set()
    node_count = 0
    pending: list[tuple[Path, tuple[str, ...]]] = [(cache_dir, ())]
    while pending:
        directory, relative = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    node_count += 1
                    if node_count > _MAX_ARCHIVE_CACHE_NODES:
                        return None
                    path = Path(entry.path)
                    entry_metadata = entry.stat(follow_symlinks=False)
                    kind = _metadata_kind(entry_metadata)
                    child_relative = relative + (entry.name,)
                    metadata = _lstat(path)
                    if metadata is None or _metadata_kind(metadata) != kind:
                        return None
                    if kind == "directory":
                        directories.add(child_relative)
                        pending.append((path, child_relative))
                    elif kind == "regular_file":
                        files[child_relative] = metadata
                    else:
                        return None
        except OSError:
            return None
    return files, directories


def _cache_ancestors_are_directories(cache_dir: Path, parts: tuple[str, ...]) -> bool:
    current = cache_dir
    if _entry_kind(current) != "directory":
        return False
    for part in parts[:-1]:
        current = current / part
        if _entry_kind(current) != "directory":
            return False
    return True


def _remove_archive_temp(path: Path, input_dir: Path) -> None:
    try:
        if path.resolve(strict=False).parent != input_dir.resolve(strict=False):
            return
        if path.name.startswith(_ARCHIVE_CACHE_PREFIX) and path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def _extract_safe_archive(
    archive_snapshot: bytes,
    input_dir: Path,
    archive_digest: str,
    budget: _ArchiveBudget,
) -> tuple[tuple[_ExtractedArchiveMember, ...], tuple[str, ...]]:
    """Validate and extract a bounded ZIP into a case-local deterministic cache."""

    cache_dir = _safe_archive_cache_dir(input_dir, archive_digest)
    temp_dir = input_dir / f"{_ARCHIVE_CACHE_PREFIX}{archive_digest}.tmp-{uuid.uuid4().hex}"
    members: list[tuple[object, tuple[str, ...], str]] = []
    directories: set[tuple[str, ...]] = set()
    seen_member_keys: set[str] = set()
    file_keys: set[str] = set()
    directory_keys: set[str] = set()
    try:
        with ZipFile(BytesIO(archive_snapshot)) as archive:
            infos = archive.infolist()
            budget.members += len(infos)
            if budget.members > MAX_ARCHIVE_MEMBERS:
                return (), ("archive_member_limit_exceeded",)
            for info in infos:
                try:
                    portable_name, parts, key, name_is_directory = _archive_member_name(info.filename)
                except ValueError as error:
                    return (), (str(error),)
                if key in seen_member_keys:
                    return (), ("archive_duplicate_member",)
                seen_member_keys.add(key)
                parent_keys = {
                    "/".join(parts[:index]).casefold()
                    for index in range(1, len(parts))
                }
                is_directory = info.is_dir() or name_is_directory
                if key in file_keys or (not is_directory and key in directory_keys) or parent_keys & file_keys:
                    return (), ("archive_member_path_collision",)
                if _archive_member_is_link(info):
                    return (), ("archive_link_member",)
                if info.flag_bits & 0x1:
                    return (), ("archive_encrypted_member",)
                directory_keys.update(parent_keys)
                directories.update(parts[:index] for index in range(1, len(parts)))
                if is_directory:
                    directory_keys.add(key)
                    directories.add(parts)
                    continue
                file_keys.add(key)
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    return (), ("archive_member_too_large",)
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                    return (), ("archive_compression_ratio_exceeded",)
                budget.uncompressed_bytes += info.file_size
                if budget.uncompressed_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    return (), ("archive_uncompressed_limit_exceeded",)
                members.append((info, parts, portable_name))
            if _lstat(cache_dir) is not None:
                if _entry_kind(cache_dir) != "directory":
                    return (), ("archive_cache_unsafe",)
                tree = _cache_tree(cache_dir)
                expected_files = {parts for _, parts, _ in members}
                if tree is None or set(tree[0]) != expected_files or tree[1] != directories:
                    return (), ("archive_cache_changed",)
                extracted: list[_ExtractedArchiveMember] = []
                for info, parts, _ in members:
                    candidate = cache_dir.joinpath(*parts)
                    if not _cache_ancestors_are_directories(cache_dir, parts):
                        return (), ("archive_cache_changed",)
                    try:
                        actual, actual_digest = _snapshot_regular_file(candidate, tree[0][parts])
                        expected = archive.read(info)
                    except OSError:
                        return (), ("archive_cache_changed",)
                    if (
                        len(expected) != info.file_size
                        or len(actual) != info.file_size
                        or actual != expected
                    ):
                        return (), ("archive_cache_changed",)
                    extracted.append(_ExtractedArchiveMember(candidate, actual_digest, len(actual)))
                return tuple(extracted), ()

            temp_dir.mkdir()
            for parts in sorted(directories, key=lambda item: (len(item), item)):
                temp_dir.joinpath(*parts).mkdir()
            extracted: list[_ExtractedArchiveMember] = []
            for info, parts, _ in members:
                destination = temp_dir.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as source, destination.open("xb") as target:
                    for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                        size += len(chunk)
                        if size > info.file_size or size > MAX_ARCHIVE_MEMBER_BYTES:
                            return (), ("archive_member_too_large",)
                        digest.update(chunk)
                        target.write(chunk)
                if size != info.file_size:
                    return (), ("archive_member_size_mismatch",)
                extracted.append(
                    _ExtractedArchiveMember(cache_dir.joinpath(*parts), digest.hexdigest(), size)
                )
            try:
                os.replace(temp_dir, cache_dir)
            except OSError:
                return (), ("archive_cache_race",)
            return tuple(extracted), ()
    except BadZipFile:
        return (), ("archive_corrupt",)
    except (OSError, RuntimeError, ValueError):
        return (), ("archive_read_error",)
    finally:
        if temp_dir.exists():
            _remove_archive_temp(temp_dir, input_dir)


def _archive_sources(
    archive_name: str,
    archive_snapshot: bytes,
    input_dir: Path,
    archive_digest: str,
    budget: _ArchiveBudget,
) -> tuple[list[SourceRecord], list[IntakeIssue]]:
    extracted, codes = _extract_safe_archive(archive_snapshot, input_dir, archive_digest, budget)
    issues = [IntakeIssue(archive_name, code) for code in codes]
    if codes:
        return [], issues
    sources: list[SourceRecord] = []
    for member in extracted:
        relative = member.path.relative_to(input_dir).as_posix()
        status = "accepted" if member.path.suffix.lower() in SUPPORTED else "present_unparsed"
        sources.append(
            SourceRecord.from_digest(
                filename=relative,
                digest=member.digest,
                size_bytes=member.size_bytes,
                extension=member.path.suffix.lower(),
                status=status,
            )
        )
    return sources, issues


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
    archive_budget = _ArchiveBudget()
    for path in entries:
        kind = _entry_kind(path)
        if _is_archive_cache_directory(path):
            continue
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
            if path.suffix.lower() in ARCHIVE_EXTENSIONS:
                snapshot, digest = _snapshot_regular_file(path, metadata)
            else:
                digest = sha256_file(path)
        except OSError:
            issues.append(IntakeIssue(path.name, "unreadable_input_entry"))
            continue
        status = "accepted" if path.suffix.lower() in SUPPORTED else "present_unparsed"
        sources.append(_source_record(path, metadata, digest, status))
        if path.suffix.lower() in ARCHIVE_EXTENSIONS:
            member_sources, archive_issues = _archive_sources(
                path.name,
                snapshot,
                input_dir,
                digest,
                archive_budget,
            )
            sources.extend(member_sources)
            issues.extend(archive_issues)

    return _result(sources, issues, review_period=review_period)
