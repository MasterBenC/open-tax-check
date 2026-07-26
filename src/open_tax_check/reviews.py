"""Persistent, source-bound human review decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import date
from pathlib import Path

from .case import resolve_safe_case_path
from .schemas import SCHEMA_VERSION


SHEET_ROLES = frozenset({"journal", "trial_balance"})


class ReviewError(ValueError):
    """A stable review-layer validation failure."""


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(
        getattr(path_stat, "st_file_attributes", 0) & reparse_point
    )


def _safe_path_stat(path: Path) -> os.stat_result | None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ReviewError("unsafe_review_path") from error
    if stat.S_ISLNK(path_stat.st_mode) or _is_reparse_point(path_stat):
        raise ReviewError("unsafe_review_path")
    return path_stat


def _require_safe_directory(path: Path, *, missing_ok: bool = False) -> None:
    path_stat = _safe_path_stat(path)
    if path_stat is None:
        if missing_ok:
            return
        raise ReviewError("unsafe_review_path")
    if not stat.S_ISDIR(path_stat.st_mode):
        raise ReviewError("unsafe_review_path")


def _require_safe_file(path: Path, *, missing_ok: bool = False) -> None:
    path_stat = _safe_path_stat(path)
    if path_stat is None:
        if missing_ok:
            return
        raise ReviewError("unsafe_review_path")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ReviewError("unsafe_review_path")


def _validate_review_paths(
    case_dir: Path,
    *,
    require_inventory: bool,
) -> Path:
    try:
        case_dir = resolve_safe_case_path(case_dir)
    except ValueError as error:
        raise ReviewError("unsafe_review_path") from error
    _require_safe_directory(case_dir)
    _require_safe_file(case_dir / "state.json", missing_ok=True)
    if require_inventory:
        _require_safe_directory(case_dir / "work")
        _require_safe_file(
            case_dir / "work" / "workbook_inventory.json",
            missing_ok=True,
        )
    _require_safe_directory(case_dir / "reviews", missing_ok=True)
    _require_safe_file(
        case_dir / "reviews" / "sheet_selection.json",
        missing_ok=True,
    )
    return case_dir


def _read_json(path: Path, error_type: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewError(error_type) from error
    if not isinstance(payload, dict):
        raise ReviewError(error_type)
    return payload


def _review_period(case_dir: Path) -> dict | None:
    state = _read_json(case_dir / "state.json", "invalid_case_state")
    period = state.get("review_period")
    return dict(period) if isinstance(period, dict) else None


def _load_current_inventory(case_dir: Path) -> dict:
    return _read_json(
        case_dir / "work" / "workbook_inventory.json",
        "workbook_inventory_unavailable",
    )


def _require_inventory_match(
    inventory: dict,
    source_id: str,
    sheet_id: str,
) -> tuple[dict, dict]:
    source = next(
        (
            item
            for item in inventory.get("workbooks", ())
            if item.get("source_id") == source_id
        ),
        None,
    )
    if source is None:
        raise ReviewError("unknown_inventory_source")
    sheet = next(
        (
            item
            for item in source.get("sheets", ())
            if item.get("sheet_id") == sheet_id
        ),
        None,
    )
    if sheet is None:
        raise ReviewError("unknown_inventory_sheet")
    return source, sheet


def _maximum_row(used_range: object) -> int:
    if not isinstance(used_range, str):
        return 0
    match = re.search(r"[A-Za-z]+(\d+)$", used_range)
    return int(match.group(1)) if match else 0


def _validated_header_rows(
    header_rows: tuple[int, ...],
    sheet: dict,
) -> tuple[int, ...]:
    if (
        not 1 <= len(header_rows) <= 3
        or any(
            not isinstance(row, int) or isinstance(row, bool) or row < 1
            for row in header_rows
        )
        or any(
            current != previous + 1
            for previous, current in zip(header_rows, header_rows[1:])
        )
    ):
        raise ReviewError("invalid_header_rows")
    maximum_row = _maximum_row(sheet.get("used_range"))
    if maximum_row < 1 or any(row > maximum_row for row in header_rows):
        raise ReviewError("header_row_out_of_bounds")
    return tuple(header_rows)


def _fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def selection_binding(
    source: dict,
    sheet: dict,
    header_rows: tuple[int, ...],
    review_period: dict | None,
    *,
    role: str,
    hidden_rows: str,
    reviewer: str,
) -> dict:
    payload = {
        "role": role,
        "hidden_rows": hidden_rows,
        "reviewer": reviewer,
        "source_sha256": source["sha256"],
        "source_filename": source["filename"],
        "sheet_id": sheet["sheet_id"],
        "sheet_name": sheet["name"],
        "header_rows": list(header_rows),
        "review_period": review_period,
        "selection_schema_version": SCHEMA_VERSION,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


_BINDING_FIELDS = frozenset(
    {
        "role",
        "hidden_rows",
        "reviewer",
        "source_sha256",
        "source_filename",
        "sheet_id",
        "sheet_name",
        "header_rows",
        "review_period",
        "selection_schema_version",
        "fingerprint",
    }
)
_DECISION_FIELDS = frozenset(
    {"role", "reviewer", "hidden_rows", "header_rows", "binding"}
)


def _is_valid_review_period(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        return False
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    try:
        return date.fromisoformat(start) <= date.fromisoformat(end)
    except ValueError:
        return False


def _is_valid_header_sequence(value: object) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 3
        and all(
            isinstance(row, int) and not isinstance(row, bool) and row >= 1
            for row in value
        )
        and all(
            current == previous + 1
            for previous, current in zip(value, value[1:])
        )
    )


def _validate_review_payload(payload: dict) -> dict:
    invalid = ReviewError("invalid_sheet_selection_review")
    if (
        set(payload) != {"schema_version", "selections"}
        or payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("selections"), list)
    ):
        raise invalid

    occupied_sheets = set()
    occupied_logical_sheets = set()
    for decision in payload["selections"]:
        if not isinstance(decision, dict) or set(decision) != _DECISION_FIELDS:
            raise invalid
        binding = decision.get("binding")
        if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
            raise invalid
        role = decision.get("role")
        hidden_rows = decision.get("hidden_rows")
        reviewer = decision.get("reviewer")
        headers = decision.get("header_rows")
        if (
            not isinstance(role, str)
            or role not in SHEET_ROLES
            or not isinstance(hidden_rows, str)
            or hidden_rows not in {"include", "exclude"}
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or not _is_valid_header_sequence(headers)
            or binding.get("role") != role
            or binding.get("hidden_rows") != hidden_rows
            or binding.get("reviewer") != reviewer
            or binding.get("header_rows") != headers
            or not isinstance(binding.get("source_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                binding["source_sha256"],
            )
            is None
            or not isinstance(binding.get("source_filename"), str)
            or not binding["source_filename"]
            or not isinstance(binding.get("sheet_id"), str)
            or not binding["sheet_id"]
            or not isinstance(binding.get("sheet_name"), str)
            or not binding["sheet_name"]
            or not _is_valid_review_period(binding.get("review_period"))
            or not isinstance(
                binding.get("selection_schema_version"),
                str,
            )
            or not binding["selection_schema_version"]
            or not isinstance(binding.get("fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", binding["fingerprint"]) is None
        ):
            raise invalid
        fingerprint_payload = {
            key: value
            for key, value in binding.items()
            if key != "fingerprint"
        }
        if binding["fingerprint"] != _fingerprint(fingerprint_payload):
            raise invalid
        sheet_key = (binding["source_sha256"], binding["sheet_id"])
        logical_key = (binding["source_filename"], binding["sheet_id"])
        if (
            sheet_key in occupied_sheets
            or logical_key in occupied_logical_sheets
        ):
            raise invalid
        occupied_sheets.add(sheet_key)
        occupied_logical_sheets.add(logical_key)
    return payload


def _load_review_file(path: Path) -> dict:
    if _safe_path_stat(path) is None:
        return {"schema_version": SCHEMA_VERSION, "selections": []}
    payload = _read_json(path, "invalid_sheet_selection_review")
    return _validate_review_payload(payload)


def _atomic_write_review(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        _require_safe_file(temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)


def confirm_sheet_selection(
    case_dir: Path,
    role: str,
    source_id: str,
    sheet_id: str,
    header_rows: tuple[int, ...],
    hidden_rows: str,
    reviewer: str,
) -> dict:
    case_dir = _validate_review_paths(case_dir, require_inventory=True)
    if role not in SHEET_ROLES:
        raise ReviewError("unknown_sheet_role")
    if hidden_rows not in {"include", "exclude"}:
        raise ReviewError("hidden_row_decision_required")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ReviewError("reviewer_required")

    source, sheet = _require_inventory_match(
        _load_current_inventory(case_dir),
        source_id,
        sheet_id,
    )
    checked_headers = _validated_header_rows(header_rows, sheet)
    decision = {
        "role": role,
        "reviewer": reviewer,
        "hidden_rows": hidden_rows,
        "header_rows": list(checked_headers),
        "binding": selection_binding(
            source,
            sheet,
            checked_headers,
            _review_period(case_dir),
            role=role,
            hidden_rows=hidden_rows,
            reviewer=reviewer,
        ),
    }

    review_path = case_dir / "reviews" / "sheet_selection.json"
    review = _load_review_file(review_path)
    selections = review["selections"]
    matching_index = None
    for index, existing in enumerate(selections):
        binding = existing.get("binding", {})
        same_current_sheet = (
            binding.get("source_sha256") == source["sha256"]
            and binding.get("sheet_id") == sheet_id
        )
        same_logical_selection = (
            existing.get("role") == role
            and binding.get("source_filename") == source["filename"]
            and binding.get("sheet_id") == sheet_id
        )
        if not same_current_sheet and not same_logical_selection:
            continue
        if same_current_sheet and existing.get("role") != role:
            raise ReviewError("overlapping_sheet_selection")
        if existing == decision:
            return existing
        matching_index = index
        break

    if matching_index is None:
        selections.append(decision)
    else:
        selections[matching_index] = decision
    review["schema_version"] = SCHEMA_VERSION
    _validate_review_payload(review)
    _atomic_write_review(review_path, review)
    return decision


def _current_source(inventory: dict, binding: dict) -> dict | None:
    workbooks = inventory.get("workbooks", ())
    source_id = str(binding.get("source_sha256", ""))[:16]
    direct = [
        source for source in workbooks if source.get("source_id") == source_id
    ]
    if len(direct) == 1:
        return direct[0]
    if direct:
        return None
    by_filename = [
        source
        for source in workbooks
        if source.get("filename") == binding.get("source_filename")
    ]
    return by_filename[0] if len(by_filename) == 1 else None


def _invalidation_reasons(
    decision: dict,
    inventory: dict,
    review_period: dict | None,
) -> list[str]:
    binding = decision["binding"]
    reasons = []
    if binding.get("selection_schema_version") != SCHEMA_VERSION:
        reasons.append("selection_schema_changed")
    if binding.get("review_period") != review_period:
        reasons.append("review_period_changed")

    source = _current_source(inventory, binding)
    if source is None:
        reasons.append("source_missing")
        return reasons
    if source.get("sha256") != binding.get("source_sha256"):
        reasons.append("source_digest_changed")
    if source.get("filename") != binding.get("source_filename"):
        reasons.append("source_filename_changed")

    sheet = next(
        (
            item
            for item in source.get("sheets", ())
            if item.get("sheet_id") == binding.get("sheet_id")
        ),
        None,
    )
    if sheet is None:
        reasons.append("sheet_missing")
        return reasons
    if sheet.get("name") != binding.get("sheet_name"):
        reasons.append("sheet_name_changed")
    return list(dict.fromkeys(reasons))


def load_valid_sheet_selections(
    case_dir: Path,
    inventory: dict,
) -> tuple[dict, list[dict]]:
    case_dir = _validate_review_paths(case_dir, require_inventory=False)
    review_path = case_dir / "reviews" / "sheet_selection.json"
    if _safe_path_stat(review_path) is None:
        return {}, []

    review = _load_review_file(review_path)
    period = _review_period(case_dir)
    valid: dict[str, list[dict]] = {}
    invalid = []
    for decision in review["selections"]:
        reasons = _invalidation_reasons(decision, inventory, period)
        role = decision.get("role")
        if reasons:
            invalid.append(
                {
                    "role": role,
                    "binding": decision.get("binding"),
                    "reasons": reasons,
                }
            )
        elif role in SHEET_ROLES:
            valid.setdefault(role, []).append(decision)
    return valid, invalid
