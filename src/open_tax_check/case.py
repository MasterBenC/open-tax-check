from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def write_json(path: Path, payload) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def init_case(case_dir: Path, entity_name: str) -> dict:
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
    }
    write_json(case_dir / "state.json", state)
    return state
