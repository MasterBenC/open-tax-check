"""File-level deterministic pipeline for a single-entity case."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .mapping import map_headers
from .quality import validate_voucher_balance
from .rules import business_entertainment_adjustment
from .semantic import detect_semantic_candidates


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_state(case_dir: Path, **changes) -> dict:
    state_path = case_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(changes)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(state_path, state)
    return state


def _find_journal(case_dir: Path) -> Path:
    candidates = sorted((case_dir / "input").glob("*.csv"))
    if not candidates:
        raise FileNotFoundError("No CSV journal found in case/input")
    return candidates[0]


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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _hard_rule_findings(rows: list[dict]) -> list[dict]:
    revenue = Decimal("0")
    entertainment = Decimal("0")
    entertainment_sources = []
    for row in rows:
        account = str(row.get("account_name") or "")
        if "主营业务收入" in account or account == "营业收入":
            revenue += _decimal(row.get("credit")) - _decimal(row.get("debit"))
        if "业务招待费" in account:
            amount = _decimal(row.get("debit")) - _decimal(row.get("credit"))
            entertainment += amount
            entertainment_sources.append(
                {
                    "source_file": row.get("source_file"),
                    "source_sheet": row.get("source_sheet"),
                    "source_row": row.get("source_row"),
                    "voucher_no": row.get("voucher_no"),
                }
            )

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
    work_dir = case_dir / "work"
    output_dir = case_dir / "output"
    work_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    source_file = _find_journal(case_dir)
    mapping, rows = _load_and_normalize(source_file)
    _write_json(work_dir / "mapping_review.json", mapping)
    if not mapping["ready_for_calculation"]:
        state = _update_state(
            case_dir,
            status="needs_mapping_review",
            next_action="confirm_field_mapping",
            artifacts=["work/mapping_review.json"],
        )
        return 3, {"status": state["status"], "next_action": state["next_action"]}

    _write_jsonl(work_dir / "normalized_journal.jsonl", rows)
    quality_findings = validate_voucher_balance(rows)
    _write_json(work_dir / "data_quality.json", quality_findings)
    if quality_findings:
        state = _update_state(
            case_dir,
            status="blocked_data_quality",
            next_action="resolve_data_quality_findings",
            artifacts=["work/normalized_journal.jsonl", "work/data_quality.json"],
        )
        return 4, {"status": state["status"], "next_action": state["next_action"]}

    findings = _hard_rule_findings(rows) + detect_semantic_candidates(rows)
    _write_json(output_dir / "findings.json", findings)
    entity_name = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))["entity_name"]
    (output_dir / "report.md").write_text(
        _render_markdown(entity_name, len(rows), findings, ai_mode), encoding="utf-8"
    )
    artifacts = [
        "work/mapping_review.json",
        "work/normalized_journal.jsonl",
        "work/data_quality.json",
        "output/findings.json",
        "output/report.md",
    ]
    state = _update_state(
        case_dir,
        status="needs_professional_review",
        completed_steps=["init", "ingest", "mapping", "data_quality", "hard_rules", "semantic_screening"],
        next_action="review_findings",
        artifacts=artifacts,
    )
    return 0, {
        "status": state["status"],
        "next_action": state["next_action"],
        "finding_count": len(findings),
        "artifacts": artifacts,
    }

