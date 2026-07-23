from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path


def write_json(path: Path, payload) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


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
    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["review_period"] = _review_period(period_start, period_end)
    state["next_action"] = "add_source_files"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_path, state)
    return state
