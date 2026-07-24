"""Persistent, source-bound human review decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .schemas import SCHEMA_VERSION


SHEET_ROLES = frozenset({"journal", "trial_balance"})


class ReviewError(ValueError):
    """A stable review-layer validation failure."""


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
        not header_rows
        or any(
            not isinstance(row, int) or isinstance(row, bool) or row < 1
            for row in header_rows
        )
        or len(set(header_rows)) != len(header_rows)
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
) -> dict:
    payload = {
        "source_sha256": source["sha256"],
        "source_filename": source["filename"],
        "sheet_id": sheet["sheet_id"],
        "sheet_name": sheet["name"],
        "header_rows": list(header_rows),
        "review_period": review_period,
        "selection_schema_version": SCHEMA_VERSION,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


def _load_review_file(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "selections": []}
    payload = _read_json(path, "invalid_sheet_selection_review")
    if not isinstance(payload.get("selections"), list):
        raise ReviewError("invalid_sheet_selection_review")
    return payload


def _atomic_write_review(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def confirm_sheet_selection(
    case_dir: Path,
    role: str,
    source_id: str,
    sheet_id: str,
    header_rows: tuple[int, ...],
    hidden_rows: str,
    reviewer: str,
) -> dict:
    if role not in SHEET_ROLES:
        raise ReviewError("unknown_sheet_role")
    if hidden_rows not in {"include", "exclude"}:
        raise ReviewError("hidden_row_decision_required")

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
    binding = decision.get("binding")
    if not isinstance(binding, dict):
        return ["invalid_selection_binding"]

    reasons = []
    fingerprint_payload = {
        key: value for key, value in binding.items() if key != "fingerprint"
    }
    if binding.get("fingerprint") != _fingerprint(fingerprint_payload):
        reasons.append("selection_fingerprint_changed")
    if binding.get("selection_schema_version") != SCHEMA_VERSION:
        reasons.append("selection_schema_changed")
    if binding.get("review_period") != review_period:
        reasons.append("review_period_changed")
    if decision.get("header_rows") != binding.get("header_rows"):
        reasons.append("header_rows_changed")

    source = _current_source(inventory, binding)
    if source is None:
        reasons.append("source_missing")
        return reasons
    if source.get("sha256") != binding.get("source_sha256"):
        reasons.append("source_digest_changed")

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
    if sheet.get("header_rows") != binding.get("header_rows"):
        reasons.append("header_rows_changed")
    return list(dict.fromkeys(reasons))


def load_valid_sheet_selections(
    case_dir: Path,
    inventory: dict,
) -> tuple[dict, list[dict]]:
    review_path = case_dir / "reviews" / "sheet_selection.json"
    if not review_path.exists():
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
