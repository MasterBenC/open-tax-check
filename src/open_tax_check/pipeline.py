"""File-level deterministic pipeline for a single-entity case."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .intake import (
    IntakeResult,
    build_coverage_matrix,
    detect_filename_roles,
    inventory_inputs,
)
from .mapping import map_headers
from .numeric import exact_add, exact_subtract, format_money, parse_decimal
from .quality import validate_journal_quality
from .reviews import load_valid_sheet_selections
from .rules import business_entertainment_adjustment
from .schemas import SCHEMA_VERSION, SheetRecord, SourceRecord, to_dict
from .semantic import detect_semantic_candidates
from .sheet_detection import confidence_band, detect_sheet
from .workbooks import (
    WORKBOOK_ADAPTER_VERSION,
    WorkbookError,
    open_workbook,
)


_CURRENT_VIEW_DIRS = ("work", "output")
_STAGING_PREFIX = ".opentaxcheck-current-stage-"
_BACKUP_PREFIX = ".opentaxcheck-current-backup-"
_STATE_BACKUP_PREFIX = ".opentaxcheck-state-backup-"
_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xlsm", ".xls"})
_INSPECTION_ROLES = ("journal", "trial_balance")
STATUS_PRIORITY = (
    "needs_intake_review",
    "needs_sheet_selection",
    "partial_scope_ready",
)


@dataclass(frozen=True, slots=True)
class _WorkbookInspection:
    workbooks: tuple[dict, ...]
    issues: tuple[dict, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value: Decimal) -> str:
    return format_money(value)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _write_json(path: Path, payload) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    _atomic_write_text(path, content + ("\n" if content else ""))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _describe_input_entry(path: Path) -> dict:
    if path.is_symlink():
        entry_type = "symlink"
    elif path.is_file():
        entry_type = "regular_file"
    elif path.is_dir():
        entry_type = "directory"
    else:
        entry_type = "other"
    return {"name": path.name, "entry_type": entry_type}


def _input_manifest(entries: list[Path]) -> list[dict]:
    return [
        {
            "path": f"input/{path.name}",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in entries
        if _describe_input_entry(path)["entry_type"] == "regular_file"
    ]


def _input_entries_manifest(entries: list[Path]) -> list[dict]:
    manifest = []
    for path in entries:
        description = _describe_input_entry(path)
        item = {
            "path": f"input/{path.name}",
            "entry_type": description["entry_type"],
            "size_bytes": None,
            "sha256": None,
        }
        if description["entry_type"] == "regular_file":
            item["size_bytes"] = path.stat().st_size
            item["sha256"] = _sha256(path)
        manifest.append(item)
    return manifest


def _next_run_id(case_dir: Path) -> str:
    run_numbers = []
    for path in (case_dir / "runs").glob("run-[0-9][0-9][0-9][0-9]"):
        if path.is_dir():
            run_numbers.append(int(path.name.removeprefix("run-")))
    return f"run-{max(run_numbers, default=0) + 1:04d}"


def _start_run(case_dir: Path) -> tuple[str, Path, str]:
    runs_dir = case_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    run_id = _next_run_id(case_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    (run_dir / "work").mkdir()
    (run_dir / "output").mkdir()
    return run_id, run_dir, _utc_now()


def _transaction_dir(case_dir: Path, prefix: str, run_id: str) -> Path:
    case_root = case_dir.resolve()
    path = case_dir / f"{prefix}{run_id}-{uuid.uuid4().hex}"
    if path.resolve(strict=False).parent != case_root or not path.name.startswith(prefix):
        raise RuntimeError("invalid current-view transaction path")
    return path


def _transaction_file(case_dir: Path, prefix: str, run_id: str) -> Path:
    case_root = case_dir.resolve()
    path = case_dir / f"{prefix}{run_id}-{uuid.uuid4().hex}.json"
    if path.resolve(strict=False).parent != case_root or not path.name.startswith(prefix):
        raise RuntimeError("invalid state transaction path")
    return path


def _remove_transaction_dir(case_dir: Path, path: Path, prefix: str) -> None:
    try:
        if path.resolve(strict=False).parent != case_dir.resolve():
            return
        if not path.name.startswith(prefix):
            return
        if path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def _remove_transaction_file(case_dir: Path, path: Path, prefix: str) -> None:
    try:
        if path.resolve(strict=False).parent != case_dir.resolve():
            return
        if not path.name.startswith(prefix):
            return
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _rollback_current_view(
    case_dir: Path,
    staging_dir: Path,
    backup_dir: Path,
    activated: list[str],
    backed_up: list[str],
) -> bool:
    restored = True
    for name in reversed(activated):
        current = case_dir / name
        staged = staging_dir / name
        if not current.exists():
            continue
        try:
            os.replace(current, staged)
        except OSError:
            try:
                shutil.rmtree(current)
            except OSError:
                restored = False

    for name in reversed(backed_up):
        backup = backup_dir / name
        current = case_dir / name
        if not backup.exists():
            continue
        if current.exists():
            try:
                shutil.rmtree(current)
            except OSError:
                restored = False
                continue
        try:
            os.replace(backup, current)
        except OSError:
            restored = False
    return restored


def _commit_current_view_and_state(
    *,
    case_dir: Path,
    run_id: str,
    run_dir: Path,
    state_path: Path,
    state: dict,
    original_state: bytes,
) -> None:
    staging_dir = _transaction_dir(case_dir, _STAGING_PREFIX, run_id)
    backup_dir = _transaction_dir(case_dir, _BACKUP_PREFIX, run_id)
    state_backup = _transaction_file(case_dir, _STATE_BACKUP_PREFIX, run_id)
    activated: list[str] = []
    backed_up: list[str] = []

    staging_dir.mkdir()
    try:
        for name in _CURRENT_VIEW_DIRS:
            shutil.copytree(run_dir / name, staging_dir / name)
    except Exception:
        _remove_transaction_dir(case_dir, staging_dir, _STAGING_PREFIX)
        raise

    try:
        _atomic_write_bytes(state_backup, original_state)
        backup_dir.mkdir()
    except Exception:
        _remove_transaction_file(case_dir, state_backup, _STATE_BACKUP_PREFIX)
        _remove_transaction_dir(case_dir, staging_dir, _STAGING_PREFIX)
        _remove_transaction_dir(case_dir, backup_dir, _BACKUP_PREFIX)
        raise

    try:
        for name in _CURRENT_VIEW_DIRS:
            current = case_dir / name
            backup = backup_dir / name
            staged = staging_dir / name
            if current.exists():
                os.replace(current, backup)
                backed_up.append(name)
            os.replace(staged, current)
            activated.append(name)
    except Exception:
        restored = _rollback_current_view(
            case_dir, staging_dir, backup_dir, activated, backed_up
        )
        if restored:
            _remove_transaction_dir(case_dir, staging_dir, _STAGING_PREFIX)
            _remove_transaction_dir(case_dir, backup_dir, _BACKUP_PREFIX)
            _remove_transaction_file(case_dir, state_backup, _STATE_BACKUP_PREFIX)
        raise

    try:
        _write_json(state_path, state)
    except Exception:
        restored = _rollback_current_view(
            case_dir, staging_dir, backup_dir, activated, backed_up
        )
        state_restored = False
        try:
            os.replace(state_backup, state_path)
            state_restored = True
        except OSError:
            try:
                state_restored = state_path.read_bytes() == original_state
            except OSError:
                state_restored = False
        if restored:
            _remove_transaction_dir(case_dir, staging_dir, _STAGING_PREFIX)
            _remove_transaction_dir(case_dir, backup_dir, _BACKUP_PREFIX)
        if state_restored:
            _remove_transaction_file(case_dir, state_backup, _STATE_BACKUP_PREFIX)
        raise

    _remove_transaction_dir(case_dir, staging_dir, _STAGING_PREFIX)
    _remove_transaction_dir(case_dir, backup_dir, _BACKUP_PREFIX)
    _remove_transaction_file(case_dir, state_backup, _STATE_BACKUP_PREFIX)


def _finalize_run(
    *,
    case_dir: Path,
    run_id: str,
    run_dir: Path,
    started_at: str,
    ai_mode: str,
    input_files: list[dict],
    input_entries: list[dict],
    status: str,
    next_action: str,
    artifacts: list[str],
    completed_steps: list[str],
    error_type: str | None = None,
    publish_current_view: bool = True,
) -> dict:
    completed_at = _utc_now()
    manifest_path = run_dir / "manifest.json"
    manifest = {
        "schema_version": "0.1.0",
        "run_id": run_id,
        "status": status,
        "ai_mode": ai_mode,
        "started_at": started_at,
        "completed_at": completed_at,
        "input_files": input_files,
        "input_entries": input_entries,
        "artifacts": [*artifacts, "manifest.json"],
    }
    if error_type is not None:
        manifest["error_type"] = error_type
    _write_json(manifest_path, manifest)

    if not publish_current_view:
        result = {
            "status": status,
            "next_action": next_action,
            "artifacts": artifacts,
            "failed_run": run_id,
            "failed_run_manifest": f"runs/{run_id}/manifest.json",
        }
        if error_type is not None:
            result["error_type"] = error_type
        return result

    state_path = case_dir / "state.json"
    original_state = state_path.read_bytes()
    state = json.loads(original_state.decode("utf-8"))
    history = list(state.get("run_history", []))
    history.append(
        {
            "run_id": run_id,
            "status": status,
            "manifest": f"runs/{run_id}/manifest.json",
            "completed_at": completed_at,
        }
    )
    state.update(
        {
            "status": status,
            "completed_steps": completed_steps,
            "next_action": next_action,
            "updated_at": completed_at,
            "artifacts": artifacts,
            "current_run": run_id,
            "current_run_manifest": f"runs/{run_id}/manifest.json",
            "run_history": history,
        }
    )
    if error_type is None:
        state.pop("error_type", None)
    else:
        state["error_type"] = error_type
    _commit_current_view_and_state(
        case_dir=case_dir,
        run_id=run_id,
        run_dir=run_dir,
        state_path=state_path,
        state=state,
        original_state=original_state,
    )
    return state


def _safe_error_type(error: Exception) -> str:
    if isinstance(error, ValueError):
        return "invalid_numeric_value"
    if isinstance(error, UnicodeError):
        return "input_encoding_error"
    if isinstance(error, csv.Error):
        return "invalid_csv"
    if isinstance(error, OSError):
        return "input_output_error"
    return "unexpected_processing_error"


def _run_artifacts(run_dir: Path) -> list[str]:
    return sorted(
        path.relative_to(run_dir).as_posix()
        for folder in (run_dir / "work", run_dir / "output")
        for path in folder.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


def _select_journal(
    case_dir: Path, run_work_dir: Path
) -> tuple[Path | None, list[Path], str | None]:
    input_entries = sorted((case_dir / "input").iterdir(), key=lambda path: path.name)
    described = [(path, _describe_input_entry(path)) for path in input_entries]
    input_files = [
        path for path, item in described if item["entry_type"] == "regular_file"
    ]
    candidates = [path for path in input_files if path.suffix.lower() == ".csv"]
    unsupported = [
        (path, item)
        for path, item in described
        if not (
            item["entry_type"] == "regular_file" and path.suffix.lower() == ".csv"
        )
    ]
    selected = candidates[0] if len(candidates) == 1 and not unsupported else None
    if unsupported:
        next_action = "remove_unsupported_input_files"
    elif selected is None:
        next_action = "provide_exactly_one_csv_journal"
    else:
        next_action = None
    selection = {
        "status": "selected" if selected else "blocked",
        "required_count": 1,
        "candidate_count": len(candidates),
        "candidates": [path.name for path in candidates],
        "input_files": [path.name for path in input_files],
        "unsupported_files": [
            path.name
            for path, item in unsupported
            if item["entry_type"] == "regular_file"
        ],
        "input_entries": [item for _, item in described],
        "unsupported_entries": [item for _, item in unsupported],
        "selected_file": selected.name if selected else None,
        "next_action": next_action,
    }
    _write_json(run_work_dir / "input_selection.json", selection)
    return selected, input_entries, next_action


def _load_and_normalize(source_file: Path) -> tuple[dict, list[dict]]:
    with source_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        mapping = map_headers(headers)
        if not mapping["ready_for_calculation"]:
            return mapping, []

        normalized = []
        for source_row, row in enumerate(reader, start=2):
            entry = {canonical: row.get(original) for original, canonical in mapping["mapping"].items()}
            structure_issues = []
            if None in row:
                structure_issues.append("extra_cells")
            if any(row.get(header) is None for header in headers):
                structure_issues.append("missing_cells")
            if structure_issues:
                entry["_csv_structure_issues"] = structure_issues
            entry.update(
                {
                    "source_file": source_file.name,
                    "source_sheet": "CSV",
                    "source_row": source_row,
                    "mapping_version": mapping["mapping_version"],
                }
            )
            normalized.append(entry)
        return mapping, normalized


def _lineage(row: dict, contribution: Decimal) -> dict:
    return {
        "source_file": row.get("source_file"),
        "source_sheet": row.get("source_sheet"),
        "source_row": row.get("source_row"),
        "voucher_no": row.get("voucher_no"),
        "period": row.get("period"),
        "tax_year": row.get("tax_year"),
        "contribution": _money(contribution),
    }


def _is_recognized_revenue_account(account_code: object, account_name: object) -> bool:
    code = str(account_code or "").strip()
    name = str(account_name or "").strip()
    return (
        code.startswith(("6001", "6051"))
        or "主营业务收入" in name
        or "其他业务收入" in name
        or name == "营业收入"
    )


def _hard_rule_findings(rows: list[dict]) -> list[dict]:
    annual: dict[str, dict] = {}
    for row in rows:
        tax_year = str(row["tax_year"])
        totals = annual.setdefault(
            tax_year,
            {
                "revenue": Decimal("0"),
                "entertainment": Decimal("0"),
                "revenue_sources": [],
                "entertainment_sources": [],
            },
        )
        account = str(row.get("account_name") or "")
        if _is_recognized_revenue_account(row.get("account_code"), account):
            contribution = exact_subtract(
                parse_decimal(row.get("credit")), parse_decimal(row.get("debit"))
            )
            totals["revenue"] = exact_add(totals["revenue"], contribution)
            totals["revenue_sources"].append(_lineage(row, contribution))
        if "业务招待费" in account:
            contribution = exact_subtract(
                parse_decimal(row.get("debit")), parse_decimal(row.get("credit"))
            )
            totals["entertainment"] = exact_add(
                totals["entertainment"], contribution
            )
            totals["entertainment_sources"].append(_lineage(row, contribution))

    findings = []
    for tax_year in sorted(annual):
        totals = annual[tax_year]
        revenue = totals["revenue"]
        entertainment = totals["entertainment"]
        revenue_sources = totals["revenue_sources"]
        entertainment_sources = totals["entertainment_sources"]
        if entertainment <= 0:
            continue
        result = business_entertainment_adjustment(entertainment, revenue)
        result.update(
            {
                "finding_id": f"CIT-BE-001-{tax_year}",
                "tax_year": tax_year,
                "review_status": "unreviewed",
                "severity": "medium" if Decimal(result["tax_adjustment"]) > 0 else "low",
                "confidence": "deterministic_from_mapped_data",
                "required_evidence": [
                    "annual_cit_return",
                    "revenue_reconciliation",
                    "deemed_sales_revenue_reconciliation",
                    "expense_ledger",
                ],
                "basis_scope": "recognized_ledger_revenue_accounts",
                "basis_completeness": "unconfirmed",
                "conclusion_boundary": (
                    "Calculated from recognized ledger revenue accounts only; "
                    "reconcile to the annual CIT return and deemed-sales revenue "
                    "before treating the adjustment as final."
                ),
                "revenue_basis": _money(revenue),
                "calculation_inputs": {
                    "actual_expense": {
                        "value": _money(entertainment),
                        "lineage": entertainment_sources,
                    },
                    "revenue_basis": {
                        "value": _money(revenue),
                        "lineage": revenue_sources,
                    },
                },
                "source": entertainment_sources,
            }
        )
        findings.append(result)
    return findings


def _render_markdown(entity_name: str, row_count: int, findings: list[dict], ai_mode: str) -> str:
    hard_count = sum(item.get("confidence") == "deterministic_from_mapped_data" for item in findings)
    candidate_count = sum(item.get("status") == "candidate" for item in findings)
    lines = [
        f"# {entity_name} Tax Health Check - Internal Draft",
        "",
        "> This draft is not a formal assurance conclusion. Professional review is required before client release.",
        "",
        "## Processing summary",
        "",
        f"- Normalized journal rows: {row_count}",
        f"- Deterministic hard-rule results: {hard_count}",
        f"- Semantic review candidates: {candidate_count}",
        f"- AI mode: {ai_mode}",
        "- Overall numeric score: not issued in v0.1",
        "",
        "## Findings awaiting professional review",
        "",
    ]
    for finding in findings:
        lines.extend(
            [
                f"### {finding.get('rule_id')} - {finding.get('status')}",
                "",
                f"- Review status: {finding.get('review_status', 'unreviewed')}",
                f"- Severity: {finding.get('severity', 'not_assigned')}",
                f"- Required evidence: {', '.join(finding.get('required_evidence', []))}",
                "",
            ]
        )
    return "\n".join(lines)


def load_case_state(case_dir: Path) -> dict:
    return json.loads((case_dir / "state.json").read_text(encoding="utf-8"))


def _used_range(max_row: int, max_column: int) -> str | None:
    if max_row <= 0 or max_column <= 0:
        return None
    column = ""
    value = max_column
    while value:
        value, remainder = divmod(value - 1, 26)
        column = chr(ord("A") + remainder) + column
    return f"A1:{column}{max_row}"


def inspect_workbooks(
    case_dir: Path,
    sources: tuple[SourceRecord, ...],
) -> _WorkbookInspection:
    workbooks = []
    issues = []
    for source in sources:
        if source.status != "accepted" or source.extension not in _EXCEL_EXTENSIONS:
            continue
        path = case_dir / "input" / source.filename
        workbook_record = {
            "schema_version": SCHEMA_VERSION,
            "source_id": source.source_id,
            "filename": source.filename,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "extension": source.extension,
            "adapter_version": WORKBOOK_ADAPTER_VERSION,
            "status": "inspected",
            "diagnostic_code": None,
            "sheets": [],
        }
        try:
            with open_workbook(path) as reader:
                sheets = []
                for sheet in reader.sheets():
                    detection = detect_sheet(
                        reader,
                        sheet,
                        merged_ranges=sheet.merged_ranges,
                    )
                    sheets.append(
                        to_dict(
                            SheetRecord(
                                source_id=source.source_id,
                                sheet_id=sheet.sheet_id,
                                index=sheet.index,
                                name=sheet.name,
                                visibility=sheet.visibility,
                                used_range=_used_range(
                                    sheet.max_row,
                                    sheet.max_column,
                                ),
                                visited_cell_estimate=(
                                    max(sheet.max_row, 0)
                                    * min(max(sheet.max_column, 0), 256)
                                ),
                                header_rows=detection.header_rows,
                                candidate_header_rows=(
                                    detection.candidate_header_rows
                                ),
                                role_scores=detection.role_scores,
                                has_formulas=sheet.has_formulas,
                                has_merged_ranges=(
                                    sheet.has_merged_ranges
                                ),
                                has_hidden_data=sheet.has_hidden_data,
                                merged_ranges=sheet.merged_ranges,
                            )
                        )
                    )
        except WorkbookError as error:
            diagnostic_code = str(error)
            workbook_record.update(
                {
                    "status": "error",
                    "diagnostic_code": diagnostic_code,
                }
            )
            workbooks.append(workbook_record)
            issues.append(
                {
                    "source_id": source.source_id,
                    "filename": source.filename,
                    "code": diagnostic_code,
                }
            )
            continue
        workbook_record["sheets"] = sheets
        workbooks.append(workbook_record)
    return _WorkbookInspection(tuple(workbooks), tuple(issues))


def _score_for(sheet: dict, role: str) -> int:
    for item in sheet["role_scores"]:
        if item["role"] == role:
            return item["score"]
    return 0


def build_sheet_selection(inventory: _WorkbookInspection) -> dict:
    candidates_by_role: dict[str, list[dict]] = {
        role: [] for role in _INSPECTION_ROLES
    }
    for workbook in inventory.workbooks:
        for sheet in workbook["sheets"]:
            for role in _INSPECTION_ROLES:
                score = _score_for(sheet, role)
                if score < 60:
                    continue
                candidates_by_role[role].append(
                    {
                        "source_id": workbook["source_id"],
                        "filename": workbook["filename"],
                        "sheet_id": sheet["sheet_id"],
                        "sheet_name": sheet["name"],
                        "visibility": sheet["visibility"],
                        "header_rows": sheet["header_rows"],
                        "score": score,
                        "confidence": confidence_band(score),
                        "has_hidden_data": sheet["has_hidden_data"],
                    }
                )

    selection = {"schema_version": SCHEMA_VERSION}
    for role, candidates in candidates_by_role.items():
        candidates.sort(
            key=lambda item: (
                item["filename"].casefold(),
                item["source_id"],
                item["sheet_id"],
            )
        )
        automatic = (
            len(candidates) == 1
            and candidates[0]["visibility"] == "visible"
            and candidates[0]["score"] >= 85
            and candidates[0]["has_hidden_data"] is False
        )
        selection[role] = [
            {
                **candidate,
                "decision_source": "automatic" if automatic else "manual",
            }
            for candidate in candidates
        ]
    return selection


def _confirmed_sheet_candidate(
    decision: dict,
    inventory: _WorkbookInspection,
) -> dict:
    binding = decision["binding"]
    workbook = next(
        item
        for item in inventory.workbooks
        if item["sha256"] == binding["source_sha256"]
    )
    sheet = next(
        item
        for item in workbook["sheets"]
        if item["sheet_id"] == binding["sheet_id"]
    )
    score = _score_for(sheet, decision["role"])
    return {
        "source_id": workbook["source_id"],
        "filename": workbook["filename"],
        "sheet_id": sheet["sheet_id"],
        "sheet_name": sheet["name"],
        "visibility": sheet["visibility"],
        "header_rows": decision["header_rows"],
        "score": score,
        "confidence": confidence_band(score),
        "has_hidden_data": sheet["has_hidden_data"],
        "decision_source": "confirmed",
        "hidden_rows": decision["hidden_rows"],
        "reviewer": decision["reviewer"],
        "selection_fingerprint": binding["fingerprint"],
    }


def apply_sheet_selection_reviews(
    case_dir: Path,
    inventory: _WorkbookInspection,
    selection: dict,
) -> set[str]:
    inventory_payload = {
        "schema_version": SCHEMA_VERSION,
        "workbooks": list(inventory.workbooks),
        "issues": list(inventory.issues),
    }
    valid, invalid = load_valid_sheet_selections(
        case_dir,
        inventory_payload,
    )
    confirmed_by_role = {
        role: [
            _confirmed_sheet_candidate(decision, inventory)
            for decision in decisions
        ]
        for role, decisions in valid.items()
    }
    invalid_roles = {
        item["role"]
        for item in invalid
        if item.get("role") in _INSPECTION_ROLES
    }
    for role in _INSPECTION_ROLES:
        confirmed = []
        current_keys = set()
        for candidate in confirmed_by_role.get(role, []):
            key = (candidate["source_id"], candidate["sheet_id"])
            if key not in current_keys:
                confirmed.append(candidate)
                current_keys.add(key)
        if role not in invalid_roles:
            if role in valid:
                selection[role] = confirmed
            continue

        affected_candidates = []
        for candidate in selection[role]:
            key = (candidate["source_id"], candidate["sheet_id"])
            if key not in current_keys:
                affected_candidates.append(
                    {**candidate, "decision_source": "manual"}
                )
                current_keys.add(key)
        selection[role] = [*confirmed, *affected_candidates]
    return invalid_roles


def inspection_state(
    intake: IntakeResult,
    inventory: _WorkbookInspection,
    selection: dict,
    invalid_review_roles: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, str]:
    outcomes = {}
    if intake.issues:
        outcomes["needs_intake_review"] = "resolve_intake_issues"
    elif inventory.issues:
        outcomes["needs_intake_review"] = "resolve_workbook_issues"
    for role in _INSPECTION_ROLES:
        candidates = selection[role]
        resolved = (
            role not in invalid_review_roles
            and candidates
            and (
                (
                    len(candidates) == 1
                    and candidates[0]["decision_source"] == "automatic"
                )
                or all(
                    candidate["decision_source"] == "confirmed"
                    for candidate in candidates
                )
            )
        )
        if (candidates or role in invalid_review_roles) and not resolved:
            outcomes.setdefault(
                "needs_sheet_selection",
                f"select_{role}_sheet",
            )
    outcomes["partial_scope_ready"] = "await_phase_2b_excel_normalization"
    for status in STATUS_PRIORITY:
        if status in outcomes:
            return status, outcomes[status]
    raise RuntimeError("invalid_inspection_state")


def _coverage_from_inspection(
    intake: IntakeResult,
    inventory: _WorkbookInspection,
) -> tuple:
    detected = {
        role: set(source_ids)
        for role, source_ids in detect_filename_roles(intake.sources).items()
    }
    for workbook in inventory.workbooks:
        for sheet in workbook["sheets"]:
            for item in sheet["role_scores"]:
                if item["score"] >= 60:
                    detected.setdefault(item["role"], set()).add(
                        workbook["source_id"]
                    )
    return build_coverage_matrix(detected)


def write_inspection_artifacts(
    *,
    run_work_dir: Path,
    intake: IntakeResult,
    inventory: _WorkbookInspection,
    selection: dict,
) -> list[str]:
    _write_json(
        run_work_dir / "input_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "sources": to_dict(intake.sources),
            "issues": to_dict(intake.issues),
        },
    )
    _write_json(
        run_work_dir / "workbook_inventory.json",
        {
            "schema_version": SCHEMA_VERSION,
            "workbooks": list(inventory.workbooks),
            "issues": list(inventory.issues),
        },
    )
    _write_json(run_work_dir / "sheet_selection.json", selection)
    _write_json(
        run_work_dir / "coverage_matrix.json",
        {
            "schema_version": SCHEMA_VERSION,
            "items": to_dict(_coverage_from_inspection(intake, inventory)),
        },
    )
    return _run_artifacts(run_work_dir.parent)


def finalize_inspection(
    *,
    case_dir: Path,
    run_id: str,
    run_dir: Path,
    started_at: str,
    status: str,
    next_action: str,
    artifacts: list[str],
    input_files: list[dict],
    input_entries: list[dict],
    error_type: str | None = None,
) -> tuple[int, dict]:
    state = _finalize_run(
        case_dir=case_dir,
        run_id=run_id,
        run_dir=run_dir,
        started_at=started_at,
        ai_mode="off",
        input_files=input_files,
        input_entries=input_entries,
        status=status,
        next_action=next_action,
        artifacts=artifacts,
        completed_steps=(
            ["init"]
            if not artifacts
            else [
                "init",
                "intake",
                "workbook_inventory",
                "sheet_selection",
                "coverage_matrix",
            ]
        ),
        error_type=error_type,
        publish_current_view=error_type is None,
    )
    return (0 if status == "partial_scope_ready" else 4), state


def _failed_inspection(
    *,
    case_dir: Path,
    run_id: str,
    run_dir: Path,
    started_at: str,
    intake: IntakeResult | None,
) -> tuple[int, dict]:
    _write_json(
        run_dir / "work" / "processing_error.json",
        {"error_type": "inspect_processing_error"},
    )
    return finalize_inspection(
        case_dir=case_dir,
        run_id=run_id,
        run_dir=run_dir,
        started_at=started_at,
        status="needs_intake_review",
        next_action="inspect_processing_error",
        artifacts=_run_artifacts(run_dir),
        input_files=(
            [
                to_dict(source)
                for source in intake.sources
                if source.status == "accepted"
            ]
            if intake is not None
            else []
        ),
        input_entries=(
            to_dict(intake.sources)
            if intake is not None
            else []
        ),
        error_type="inspect_processing_error",
    )


def inspect_case(case_dir: Path) -> tuple[int, dict]:
    run_id, run_dir, started_at = _start_run(case_dir)
    intake: IntakeResult | None = None
    try:
        state = load_case_state(case_dir)
        if not state.get("review_period"):
            return finalize_inspection(
                case_dir=case_dir,
                run_id=run_id,
                run_dir=run_dir,
                started_at=started_at,
                status="needs_intake_review",
                next_action="set_case_period",
                artifacts=[],
                input_files=[],
                input_entries=[],
            )

        intake = inventory_inputs(
            case_dir=case_dir,
            review_period=state["review_period"],
        )
        inventory = inspect_workbooks(case_dir, intake.sources)
        selection = build_sheet_selection(inventory)
        invalid_review_roles = apply_sheet_selection_reviews(
            case_dir,
            inventory,
            selection,
        )
        status, next_action = inspection_state(
            intake,
            inventory,
            selection,
            invalid_review_roles,
        )
        artifacts = write_inspection_artifacts(
            run_work_dir=run_dir / "work",
            intake=intake,
            inventory=inventory,
            selection=selection,
        )
        return finalize_inspection(
            case_dir=case_dir,
            run_id=run_id,
            run_dir=run_dir,
            started_at=started_at,
            status=status,
            next_action=next_action,
            artifacts=artifacts,
            input_files=[
                to_dict(source)
                for source in intake.sources
                if source.status == "accepted"
            ],
            input_entries=to_dict(intake.sources),
        )
    except Exception:
        return _failed_inspection(
            case_dir=case_dir,
            run_id=run_id,
            run_dir=run_dir,
            started_at=started_at,
            intake=intake,
        )


def run_case(case_dir: Path, ai_mode: str) -> tuple[int, dict]:
    run_id, run_dir, started_at = _start_run(case_dir)
    run_work_dir = run_dir / "work"
    run_output_dir = run_dir / "output"
    input_files: list[dict] = []
    input_entries: list[dict] = []
    error_type = None

    try:
        source_file, discovered_entries, selection_next_action = _select_journal(
            case_dir, run_work_dir
        )
        input_files = _input_manifest(discovered_entries)
        input_entries = _input_entries_manifest(discovered_entries)

        if source_file is None:
            exit_code = 4
            status = "blocked_data_quality"
            next_action = selection_next_action or "provide_exactly_one_csv_journal"
            artifacts = ["work/input_selection.json"]
            completed_steps = ["init", "input_selection"]
            response_details = {}
        else:
            mapping, rows = _load_and_normalize(source_file)
            _write_json(run_work_dir / "mapping_review.json", mapping)
            if not mapping["ready_for_calculation"]:
                exit_code = 3
                status = "needs_mapping_review"
                next_action = "confirm_field_mapping"
                artifacts = ["work/input_selection.json", "work/mapping_review.json"]
                completed_steps = ["init", "input_selection", "ingest", "mapping"]
                response_details = {}
            else:
                try:
                    quality_findings = validate_journal_quality(rows)
                finally:
                    _write_jsonl(run_work_dir / "normalized_journal.jsonl", rows)
                _write_json(run_work_dir / "data_quality.json", quality_findings)
                if quality_findings:
                    exit_code = 4
                    status = "blocked_data_quality"
                    next_action = "resolve_data_quality_findings"
                    artifacts = [
                        "work/input_selection.json",
                        "work/mapping_review.json",
                        "work/normalized_journal.jsonl",
                        "work/data_quality.json",
                    ]
                    completed_steps = [
                        "init",
                        "input_selection",
                        "ingest",
                        "mapping",
                        "data_quality",
                    ]
                    response_details = {}
                else:
                    findings = _hard_rule_findings(rows) + detect_semantic_candidates(rows)
                    _write_json(run_output_dir / "findings.json", findings)
                    entity_name = json.loads(
                        (case_dir / "state.json").read_text(encoding="utf-8")
                    )["entity_name"]
                    _atomic_write_text(
                        run_output_dir / "report.md",
                        _render_markdown(entity_name, len(rows), findings, ai_mode),
                    )
                    exit_code = 0
                    status = "needs_professional_review"
                    next_action = "review_findings"
                    artifacts = [
                        "work/input_selection.json",
                        "work/mapping_review.json",
                        "work/normalized_journal.jsonl",
                        "work/data_quality.json",
                        "output/findings.json",
                        "output/report.md",
                    ]
                    completed_steps = [
                        "init",
                        "input_selection",
                        "ingest",
                        "mapping",
                        "data_quality",
                        "hard_rules",
                        "semantic_screening",
                    ]
                    response_details = {
                        "finding_count": len(findings),
                        "artifacts": artifacts,
                    }
    except Exception as error:
        error_type = _safe_error_type(error)
        _write_json(
            run_work_dir / "processing_error.json",
            {
                "status": "blocked_processing_error",
                "next_action": "inspect_processing_error",
                "error_type": error_type,
            },
        )
        exit_code = 5
        status = "blocked_processing_error"
        next_action = "inspect_processing_error"
        artifacts = _run_artifacts(run_dir)
        completed_steps = ["init", "processing_error"]
        response_details = {"error_type": error_type}

    state = _finalize_run(
        case_dir=case_dir,
        run_id=run_id,
        run_dir=run_dir,
        started_at=started_at,
        ai_mode=ai_mode,
        input_files=input_files,
        input_entries=input_entries,
        status=status,
        next_action=next_action,
        artifacts=artifacts,
        completed_steps=completed_steps,
        error_type=error_type,
    )
    return exit_code, {
        "status": state["status"],
        "next_action": state["next_action"],
        **response_details,
    }
