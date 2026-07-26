"""Persistent, source-bound human review decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .case import resolve_safe_case_path
from .intake import sha256_file
from .mapping import MAPPING_VERSION, ROLE_FIELDS, required_fields
from .schemas import SCHEMA_VERSION
from .sheet_detection import selected_headers
from .workbooks import WorkbookError, open_workbook


SHEET_ROLES = frozenset({"journal", "trial_balance"})


class ReviewError(ValueError):
    """A stable review-layer validation failure."""


class MappingReviewError(ReviewError):
    """A stable mapping-confirmation validation failure."""


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
    _require_safe_file(
        case_dir / "reviews" / "mapping_review.json",
        missing_ok=True,
    )
    _require_safe_file(
        case_dir / "reviews" / "mapping_proposals.json",
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


_MAPPING_CONFIRMATION_FIELDS = frozenset(
    {
        "role",
        "source_id",
        "sheet_id",
        "header_rows",
        "mapping",
        "composites",
        "exclusions",
        "reviewer",
        "binding",
    }
)
_MAPPING_BINDING_FIELDS = frozenset(
    {
        "source_sha256",
        "sheet_id",
        "header_rows",
        "case_period",
        "mapping_schema_version",
        "source_headers_fingerprint",
        "fingerprint",
    }
)
_COMPOSITE_TARGETS = {
    "opening": ("opening_debit", "opening_credit"),
    "closing": ("closing_debit", "closing_credit"),
}
_COMPOSITE_FIELDS = frozenset(
    {"amount_field", "direction_field", "debit_field", "credit_field"}
)


def _mapping_error(code: str) -> MappingReviewError:
    return MappingReviewError(code)


def _validate_source_headers(headers: Sequence[str]) -> tuple[str, ...]:
    if (
        isinstance(headers, str)
        or not isinstance(headers, Sequence)
        or any(not isinstance(header, str) or not header for header in headers)
        or len(headers) != len(set(headers))
    ):
        raise _mapping_error("invalid_source_headers")
    return tuple(headers)


def _validated_composites(role: object, composites: object) -> dict[str, dict]:
    if composites is None:
        return {}
    if not isinstance(composites, dict):
        raise _mapping_error("invalid_composite_mapping")
    if composites and role != "trial_balance":
        raise _mapping_error("invalid_composite_mapping")

    checked: dict[str, dict] = {}
    for scope, composite in composites.items():
        if (
            scope not in _COMPOSITE_TARGETS
            or not isinstance(composite, dict)
            or set(composite) != _COMPOSITE_FIELDS
            or any(
                not isinstance(composite.get(key), str)
                or not composite[key]
                for key in _COMPOSITE_FIELDS
            )
            or (
                composite["debit_field"],
                composite["credit_field"],
            )
            != _COMPOSITE_TARGETS[scope]
        ):
            raise _mapping_error("invalid_composite_mapping")
        if composite["amount_field"] == composite["direction_field"]:
            raise _mapping_error("invalid_composite_mapping")
        checked[scope] = {
            key: composite[key] for key in sorted(_COMPOSITE_FIELDS)
        }
    return checked


def _composite_source_headers(composites: dict[str, dict]) -> list[str]:
    return [
        composite[field]
        for composite in composites.values()
        for field in ("amount_field", "direction_field")
    ]


def _source_headers_from_confirmation(payload: dict) -> tuple[str, ...]:
    mapping = payload.get("mapping", {})
    exclusions = payload.get("exclusions", {})
    composites = payload.get("composites", {})
    source_headers = list(mapping) + list(exclusions)
    if isinstance(composites, dict):
        source_headers.extend(_composite_source_headers(composites))
    return _validate_source_headers(source_headers)


def _headers_fingerprint(headers: Sequence[str]) -> str:
    return _fingerprint({"headers": list(headers)})


def _actual_selected_headers(
    case_dir: Path,
    inventory: dict,
    selection: dict,
) -> tuple[str, ...]:
    binding = selection["binding"]
    source = _current_source(inventory, binding)
    if source is None:
        raise _mapping_error("source_missing")
    filename = source.get("filename")
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
    ):
        raise _mapping_error("source_headers_unavailable")
    input_dir = case_dir / "input"
    _require_safe_directory(input_dir)
    source_path = input_dir / filename
    _require_safe_file(source_path)
    try:
        if sha256_file(source_path) != binding["source_sha256"]:
            raise _mapping_error("source_digest_changed")
        with open_workbook(source_path) as reader:
            sheet = next(
                (
                    item
                    for item in reader.sheets()
                    if item.sheet_id == binding["sheet_id"]
                    and item.name == binding["sheet_name"]
                ),
                None,
            )
            if sheet is None:
                raise _mapping_error("sheet_missing")
            return _validate_source_headers(
                selected_headers(reader, sheet, selection["header_rows"])
            )
    except MappingReviewError:
        raise
    except (OSError, ValueError, WorkbookError):
        raise _mapping_error("source_headers_unavailable") from None


def validate_confirmation(headers: Sequence[str], payload: dict) -> None:
    """Require a complete, non-conflicting, role-valid mapping decision."""

    if not isinstance(payload, dict):
        raise _mapping_error("invalid_mapping_confirmation")
    role = payload.get("role")
    if role not in ROLE_FIELDS:
        raise _mapping_error("unknown_mapping_role")
    mapping = payload.get("mapping", {})
    exclusions = payload.get("exclusions", {})
    if not isinstance(mapping, dict) or not isinstance(exclusions, dict):
        raise _mapping_error("invalid_mapping_confirmation")
    if any(
        not isinstance(header, str)
        or not header
        or not isinstance(target, str)
        or not target
        for header, target in mapping.items()
    ) or any(
        not isinstance(header, str) or not header
        for header in exclusions
    ):
        raise _mapping_error("invalid_mapping_confirmation")
    composites = _validated_composites(role, payload.get("composites", {}))
    checked_headers = _validate_source_headers(headers)
    composite_headers = _composite_source_headers(composites)
    mapped_headers = set(mapping) | set(composite_headers)
    excluded_headers = set(exclusions)
    if mapped_headers & excluded_headers:
        raise _mapping_error("header_mapped_and_excluded")
    if len(mapped_headers) != len(mapping) + len(composite_headers):
        raise _mapping_error("header_mapped_twice")
    if set(checked_headers) != mapped_headers | excluded_headers:
        raise _mapping_error("unresolved_source_header")
    if any(not isinstance(reason, str) or not reason.strip() for reason in exclusions.values()):
        raise _mapping_error("missing_exclusion_reason")

    targets = list(mapping.values()) + [
        target
        for scope in composites
        for target in _COMPOSITE_TARGETS[scope]
    ]
    if len(targets) != len(set(targets)):
        raise _mapping_error("duplicate_mapping_target")
    valid_targets = required_fields(role) | ROLE_FIELDS[role]["optional"]
    if any(target not in valid_targets for target in targets):
        raise _mapping_error("unknown_mapping_target")
    if required_fields(role) - set(targets):
        raise _mapping_error("missing_required_mapping")


def mapping_binding(selection: dict, payload: dict) -> dict:
    """Return the stable source and scope binding for one confirmation."""

    case_dir = payload.get("case_dir")
    if not isinstance(case_dir, Path):
        raise _mapping_error("invalid_mapping_confirmation")
    source_headers = payload.get("source_headers")
    if source_headers is None:
        source_headers = _source_headers_from_confirmation(payload)
    source_headers = _validate_source_headers(source_headers)
    basis = {
        "source_sha256": selection["binding"]["source_sha256"],
        "sheet_id": selection["binding"]["sheet_id"],
        "header_rows": selection["header_rows"],
        "case_period": _review_period(case_dir),
        "mapping_schema_version": MAPPING_VERSION,
        "source_headers_fingerprint": _headers_fingerprint(source_headers),
    }
    return {**basis, "fingerprint": _fingerprint(basis)}


def _validate_mapping_confirmation(decision: dict) -> dict:
    invalid = _mapping_error("invalid_mapping_review")
    if not isinstance(decision, dict) or set(decision) != _MAPPING_CONFIRMATION_FIELDS:
        raise invalid
    if (
        not isinstance(decision.get("role"), str)
        or not isinstance(decision.get("source_id"), str)
        or not isinstance(decision.get("sheet_id"), str)
        or not _is_valid_header_sequence(decision.get("header_rows"))
        or not isinstance(decision.get("reviewer"), str)
        or not decision["reviewer"].strip()
    ):
        raise invalid
    try:
        validate_confirmation(_source_headers_from_confirmation(decision), decision)
    except MappingReviewError as error:
        raise invalid from error

    binding = decision.get("binding")
    if (
        not isinstance(binding, dict)
        or set(binding) != _MAPPING_BINDING_FIELDS
        or not isinstance(binding.get("source_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["source_sha256"]) is None
        or decision["source_id"] != binding["source_sha256"][:16]
        or binding.get("sheet_id") != decision["sheet_id"]
        or binding.get("header_rows") != decision["header_rows"]
        or not _is_valid_review_period(binding.get("case_period"))
        or not isinstance(binding.get("mapping_schema_version"), str)
        or not binding["mapping_schema_version"]
        or not isinstance(binding.get("source_headers_fingerprint"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", binding["source_headers_fingerprint"]
        )
        is None
        or not isinstance(binding.get("fingerprint"), str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["fingerprint"]) is None
    ):
        raise invalid
    fingerprint_basis = {
        key: value for key, value in binding.items() if key != "fingerprint"
    }
    if binding["fingerprint"] != _fingerprint(fingerprint_basis):
        raise invalid
    return decision


def _load_mapping_review(path: Path) -> dict:
    if _safe_path_stat(path) is None:
        return {"schema_version": SCHEMA_VERSION, "confirmations": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _mapping_error("invalid_mapping_review") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "confirmations"}
        or payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("confirmations"), list)
    ):
        raise _mapping_error("invalid_mapping_review")
    seen = set()
    for decision in payload["confirmations"]:
        _validate_mapping_confirmation(decision)
        key = (
            decision["role"],
            decision["binding"]["source_sha256"],
            decision["sheet_id"],
        )
        if key in seen:
            raise _mapping_error("invalid_mapping_review")
        seen.add(key)
    return payload


def _selected_mapping_target(
    selections: dict[str, list[dict]],
    payload: dict,
) -> dict:
    role = payload.get("role")
    source_id = payload.get("source_id")
    sheet_id = payload.get("sheet_id")
    header_rows = payload.get("header_rows")
    if (
        role not in SHEET_ROLES
        or not isinstance(source_id, str)
        or not isinstance(sheet_id, str)
        or not _is_valid_header_sequence(header_rows)
    ):
        raise _mapping_error("invalid_mapping_confirmation")
    for selection in selections.get(role, []):
        binding = selection["binding"]
        if (
            binding["source_sha256"][:16] == source_id
            and binding["sheet_id"] == sheet_id
            and selection["header_rows"] == header_rows
        ):
            return selection
    raise _mapping_error("sheet_selection_unconfirmed")


def confirm_mapping(case_dir: Path, payload: dict) -> dict:
    """Persist a complete human mapping confirmation bound to one selection."""

    case_dir = _validate_review_paths(case_dir, require_inventory=True)
    inventory = _load_current_inventory(case_dir)
    selections, _ = load_valid_sheet_selections(case_dir, inventory)
    selection = _selected_mapping_target(selections, payload)
    headers = _actual_selected_headers(case_dir, inventory, selection)
    validate_confirmation(headers, payload)
    composites = _validated_composites(payload["role"], payload.get("composites", {}))
    binding = mapping_binding(
        selection,
        {**payload, "case_dir": case_dir, "source_headers": headers},
    )
    decision = {
        "role": payload["role"],
        "source_id": payload["source_id"],
        "sheet_id": payload["sheet_id"],
        "header_rows": list(payload["header_rows"]),
        "mapping": dict(payload["mapping"]),
        "composites": composites,
        "exclusions": dict(payload["exclusions"]),
        "reviewer": payload.get("reviewer"),
        "binding": binding,
    }
    if not isinstance(decision["reviewer"], str) or not decision["reviewer"].strip():
        raise _mapping_error("reviewer_required")
    _validate_mapping_confirmation(decision)

    review_path = case_dir / "reviews" / "mapping_review.json"
    review = _load_mapping_review(review_path)
    key = (decision["role"], binding["source_sha256"], decision["sheet_id"])
    for index, existing in enumerate(review["confirmations"]):
        existing_key = (
            existing["role"],
            existing["binding"]["source_sha256"],
            existing["sheet_id"],
        )
        if existing_key != key:
            continue
        if existing == decision:
            return existing
        review["confirmations"][index] = decision
        break
    else:
        review["confirmations"].append(decision)
    _atomic_write_review(review_path, review)
    return decision


def save_mapping_proposal(
    case_dir: Path,
    payload: dict,
    *,
    proposal_source: str,
) -> dict:
    """Persist an untrusted proposal without making it active."""

    case_dir = _validate_review_paths(case_dir, require_inventory=False)
    if proposal_source not in {"ai", "host"} or not isinstance(payload, dict):
        raise _mapping_error("invalid_mapping_proposal")
    try:
        proposal = json.loads(json.dumps(payload, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise _mapping_error("invalid_mapping_proposal") from error
    path = case_dir / "reviews" / "mapping_proposals.json"
    if _safe_path_stat(path) is None:
        proposals = {"schema_version": SCHEMA_VERSION, "proposals": []}
    else:
        try:
            proposals = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise _mapping_error("invalid_mapping_proposal") from error
        if (
            not isinstance(proposals, dict)
            or set(proposals) != {"schema_version", "proposals"}
            or proposals.get("schema_version") != SCHEMA_VERSION
            or not isinstance(proposals.get("proposals"), list)
        ):
            raise _mapping_error("invalid_mapping_proposal")
    decision = {"proposal_source": proposal_source, "payload": proposal}
    proposals["proposals"].append(decision)
    _atomic_write_review(path, proposals)
    return decision


def load_active_mappings(case_dir: Path) -> dict[str, list[dict]]:
    """Load only confirmations still bound to current selections and scope."""

    case_dir = _validate_review_paths(case_dir, require_inventory=True)
    review_path = case_dir / "reviews" / "mapping_review.json"
    if _safe_path_stat(review_path) is None:
        return {}
    inventory = _load_current_inventory(case_dir)
    selections, _ = load_valid_sheet_selections(case_dir, inventory)
    current_period = _review_period(case_dir)
    review = _load_mapping_review(review_path)
    active: dict[str, list[dict]] = {}
    for decision in review["confirmations"]:
        binding = decision["binding"]
        if (
            binding["mapping_schema_version"] != MAPPING_VERSION
            or binding["case_period"] != current_period
        ):
            continue
        selection = next(
            (
                item
                for item in selections.get(decision["role"], [])
                if item["binding"]["source_sha256"] == binding["source_sha256"]
                and item["binding"]["sheet_id"] == binding["sheet_id"]
                and item["header_rows"] == binding["header_rows"]
            ),
            None,
        )
        if selection is not None:
            try:
                headers = _actual_selected_headers(case_dir, inventory, selection)
                validate_confirmation(headers, decision)
            except MappingReviewError:
                continue
            if binding["source_headers_fingerprint"] != _headers_fingerprint(headers):
                continue
            active.setdefault(decision["role"], []).append(decision)
    return active
