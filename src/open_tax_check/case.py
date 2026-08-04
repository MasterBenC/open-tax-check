from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timezone
from pathlib import Path


def write_json(path: Path, payload) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_point)


def _direct_stat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError("unsafe_case_path") from error


def _reject_unsafe_node(
    path_stat: os.stat_result | None,
    *,
    directory: bool,
) -> None:
    if path_stat is None:
        return
    if stat.S_ISLNK(path_stat.st_mode) or _is_reparse_point(path_stat):
        raise ValueError("unsafe_case_path")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(path_stat.st_mode):
        raise ValueError("unsafe_case_path")


def resolve_safe_case_path(case_dir: Path) -> Path:
    """Canonicalize parent aliases while rejecting links at managed nodes."""

    absolute_case_dir = case_dir.absolute()
    _reject_unsafe_node(_direct_stat(absolute_case_dir), directory=True)

    missing_parents = []
    existing_parent = absolute_case_dir.parent
    while True:
        try:
            existing_parent.lstat()
            break
        except FileNotFoundError:
            missing_parents.append(existing_parent.name)
            parent = existing_parent.parent
            if parent == existing_parent:
                raise ValueError("unsafe_case_path")
            existing_parent = parent
            continue
        except OSError as error:
            raise ValueError("unsafe_case_path") from error

    try:
        canonical_parent = existing_parent.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("unsafe_case_path") from error
    _reject_unsafe_node(_direct_stat(canonical_parent), directory=True)
    canonical_case_dir = canonical_parent.joinpath(
        *reversed(missing_parents),
        absolute_case_dir.name,
    )
    _reject_unsafe_node(_direct_stat(canonical_case_dir), directory=True)

    state_path = canonical_case_dir / "state.json"
    _reject_unsafe_node(_direct_stat(state_path), directory=False)
    for name in ("input", "work", "output", "runs", "reviews"):
        _reject_unsafe_node(
            _direct_stat(canonical_case_dir / name),
            directory=True,
        )
    return canonical_case_dir


def _review_period(start: str | None, end: str | None) -> dict | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("incomplete_review_period")
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as error:
        raise ValueError("invalid_review_period") from error
    if start_date > end_date:
        raise ValueError("invalid_review_period")
    return {"start": start_date.isoformat(), "end": end_date.isoformat()}


def init_case(
    case_dir: Path,
    entity_name: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> dict:
    review_period = _review_period(period_start, period_end)
    case_dir = resolve_safe_case_path(case_dir)
    if case_dir.exists() and any(case_dir.iterdir()):
        raise FileExistsError(f"case directory must be empty: {case_dir}")
    case_dir.mkdir(parents=True, exist_ok=True)
    for child in ("input", "work", "output"):
        (case_dir / child).mkdir(exist_ok=True)
    state = {
        "schema_version": "0.1.0",
        "entity_name": entity_name,
        "status": "ready_for_input",
        "completed_steps": ["init"],
        "next_action": "add_source_files",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [],
        "current_run": None,
        "run_history": [],
        "review_period": review_period,
    }
    write_json(case_dir / "state.json", state)
    return state


def set_case_scope(case_dir: Path, period_start: str, period_end: str) -> dict:
    case_dir = resolve_safe_case_path(case_dir)
    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review_period"] = _review_period(period_start, period_end)
    state["next_action"] = "add_source_files"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_path, state)
    return state
