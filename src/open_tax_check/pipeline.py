"""File-level deterministic pipeline for a single-entity case."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .mapping import map_headers
from .quality import validate_voucher_balance
from .rules import business_entertainment_adjustment
from .semantic import detect_semantic_candidates


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
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


def _input_manifest(candidates: list[Path]) -> list[dict]:
    return [
        {
            "path": f"input/{path.name}",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in candidates
    ]


def _next_run_id(case_dir: Path) -> str:
    run_numbers = []
    for path in (case_dir / "runs").glob("run-[0-9][0-9][0-9][0-9]"):
        if path.is_dir():
            run_numbers.append(int(path.name.removeprefix("run-")))
    return f"run-{max(run_numbers, default=0) + 1:04d}"


def _reset_current_view(case_dir: Path) -> None:
    for name in ("work", "output"):
        path = case_dir / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir()


def _start_run(case_dir: Path) -> tuple[str, Path, str]:
    runs_dir = case_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    run_id = _next_run_id(case_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir()
    (run_dir / "work").mkdir()
    (run_dir / "output").mkdir()
    return run_id, run_dir, _utc_now()


def _publish_current_view(case_dir: Path, run_dir: Path) -> None:
    _reset_current_view(case_dir)
    for name in ("work", "output"):
        shutil.copytree(run_dir / name, case_dir / name, dirs_exist_ok=True)


def _finalize_run(
    *,
    case_dir: Path,
    run_id: str,
    run_dir: Path,
    started_at: str,
    ai_mode: str,
    input_files: list[dict],
    status: str,
    next_action: str,
    artifacts: list[str],
    completed_steps: list[str],
    error_type: str | None = None,
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
        "artifacts": [*artifacts, "manifest.json"],
    }
    if error_type is not None:
        manifest["error_type"] = error_type
    _write_json(manifest_path, manifest)
    _publish_current_view(case_dir, run_dir)

    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
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
    _write_json(state_path, state)
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


def _select_journal(case_dir: Path, run_work_dir: Path) -> tuple[Path | None, list[Path]]:
    candidates = sorted((case_dir / "input").glob("*.csv"))
    selected = candidates[0] if len(candidates) == 1 else None
    selection = {
        "status": "selected" if selected else "blocked",
        "required_count": 1,
        "candidate_count": len(candidates),
        "candidates": [path.name for path in candidates],
        "selected_file": selected.name if selected else None,
        "next_action": None if selected else "provide_exactly_one_csv_journal",
    }
    _write_json(run_work_dir / "input_selection.json", selection)
    return selected, candidates


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
        "contribution": _money(contribution),
    }


def _hard_rule_findings(rows: list[dict]) -> list[dict]:
    revenue = Decimal("0")
    entertainment = Decimal("0")
    revenue_sources = []
    entertainment_sources = []
    for row in rows:
        account = str(row.get("account_name") or "")
        if "主营业务收入" in account or account == "营业收入":
            contribution = _decimal(row.get("credit")) - _decimal(row.get("debit"))
            revenue += contribution
            revenue_sources.append(_lineage(row, contribution))
        if "业务招待费" in account:
            contribution = _decimal(row.get("debit")) - _decimal(row.get("credit"))
            entertainment += contribution
            entertainment_sources.append(_lineage(row, contribution))

    findings = []
    if entertainment > 0:
        result = business_entertainment_adjustment(entertainment, revenue)
        result.update(
            {
                "finding_id": "CIT-BE-001-aggregate",
                "review_status": "unreviewed",
                "severity": "medium" if Decimal(result["tax_adjustment"]) > 0 else "low",
                "confidence": "deterministic_from_mapped_data",
                "required_evidence": ["annual_cit_return", "revenue_reconciliation", "expense_ledger"],
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


def run_case(case_dir: Path, ai_mode: str) -> tuple[int, dict]:
    run_id, run_dir, started_at = _start_run(case_dir)
    run_work_dir = run_dir / "work"
    run_output_dir = run_dir / "output"
    input_files: list[dict] = []
    error_type = None

    try:
        source_file, candidates = _select_journal(case_dir, run_work_dir)
        input_files = _input_manifest(candidates)

        if source_file is None:
            exit_code = 4
            status = "blocked_data_quality"
            next_action = "provide_exactly_one_csv_journal"
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
                _write_jsonl(run_work_dir / "normalized_journal.jsonl", rows)
                quality_findings = validate_voucher_balance(rows)
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
