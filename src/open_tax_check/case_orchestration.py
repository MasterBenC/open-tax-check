"""Persistent, model-independent orchestration for a self-service case."""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
from enum import StrEnum
import json
import hashlib
import os
from pathlib import Path
from typing import Mapping

from .agent_orchestration import (
    AgentQuestionTask,
    build_agent_question_tasks,
    build_cross_check_question_tasks,
    task_payload,
)
from .case import resolve_safe_case_path, write_json
from .evidence import verify_evidence_file
from .schemas import to_dict
from .source_paths import resolve_safe_source
from .tax_health import continue_tax_health_check, run_tax_health_check
from .tax_io import tax_request_from_dict
from .tax_models import (
    AuditEvent,
    CalculationRecord,
    Confirmation,
    DateRange,
    FactGap,
    FactState,
    Limitation,
    PromptChoice,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
    TaxResult,
    TaxStatus,
)
from .tax_reporting import effective_limitations, write_tax_health_report, write_tax_health_snapshot
from .tax_cross_checks import (
    AmountBridgeItem,
    CrossCheckExplanation,
    TaxCrossCheckRun,
    apply_cross_check_explanation,
    parse_tax_cross_check_payload,
    rerun_tax_cross_check,
    tax_cross_check_payload,
)


CASE_SCHEMA_VERSION = "tax-case/v1"
TASKS_SCHEMA_VERSION = "tax-customer-tasks/v1"
PRIVATE_SNAPSHOT_NAME = "tax-health-snapshot.private.json"
PUBLIC_SNAPSHOT_NAME = "tax-health-snapshot.json"
REPORT_NAME = "税务健康检查报告.md"
HANDOFF_NAME = "税务健康检查交付说明.json"
_INPUT_MANIFEST_LIMIT = 2 * 1024 * 1024
_CROSS_CHECK_LIMIT = 4 * 1024 * 1024
_CROSS_CHECK_INPUT_BINDING_PREFIX = "cross-check-input-binding:"
_CROSS_CHECK_CONFIRMATION_FIELDS = frozenset(
    {
        "question_id",
        "check_id",
        "source_manifest_sha256",
        "category",
        "explanation_zh",
        "confirmed_by",
        "confirmed_at",
        "evidence_refs",
        "bridge",
    }
)


class CaseStatus(StrEnum):
    WAITING_FOR_MATERIALS = "waiting_for_materials"
    WAITING_FOR_EXTRACTION = "waiting_for_extraction"
    WAITING_FOR_CUSTOMER_CONFIRMATION = "waiting_for_customer_confirmation"
    BLOCKED_DATA_QUALITY = "blocked_data_quality"
    READY_FOR_SELF_CHECK = "ready_for_self_check"
    COMPLETED_WITH_LIMITATIONS = "completed_with_limitations"
    COMPLETED = "completed"
    FAILED = "failed"


_STATUS_LABELS_ZH = {
    CaseStatus.WAITING_FOR_MATERIALS: "等待补充资料",
    CaseStatus.WAITING_FOR_EXTRACTION: "等待完成资料提取",
    CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION: "等待客户确认",
    CaseStatus.BLOCKED_DATA_QUALITY: "数据质量阻塞",
    CaseStatus.READY_FOR_SELF_CHECK: "可运行税务健康自查",
    CaseStatus.COMPLETED_WITH_LIMITATIONS: "已完成，存在明确限制",
    CaseStatus.COMPLETED: "已完成",
    CaseStatus.FAILED: "运行失败",
}

_STATUS_MESSAGES_ZH = {
    CaseStatus.WAITING_FOR_MATERIALS: "请补充本次检查所需的账簿、财务报表、纳税申报表和相关证明资料。",
    CaseStatus.WAITING_FOR_EXTRACTION: "请完成尚未提取的文档，或提供可验证的结构化结果。",
    CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION: "请按补件任务逐项确认事实并提供相应证据。",
    CaseStatus.BLOCKED_DATA_QUALITY: "账务、报表或关键勾稽存在阻塞问题，请先修复来源资料。",
    CaseStatus.READY_FOR_SELF_CHECK: "现有资料可以运行当前已覆盖的税务健康自查程序。",
    CaseStatus.COMPLETED_WITH_LIMITATIONS: "报告已经生成，请同时查看尚未覆盖或仍待补充的限制。",
    CaseStatus.COMPLETED: "当前已覆盖程序已经完成，请下载并核对交付物。",
    CaseStatus.FAILED: "案件运行失败，请检查稳定错误码和相关处理记录。",
}

_STATUS_EVIDENCE_TYPES = {
    CaseStatus.WAITING_FOR_MATERIALS: ("case_source_files",),
    CaseStatus.WAITING_FOR_EXTRACTION: ("extractable_document_or_structured_result",),
    CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION: ("customer_confirmation",),
    CaseStatus.BLOCKED_DATA_QUALITY: ("corrected_source_records",),
    CaseStatus.READY_FOR_SELF_CHECK: (),
    CaseStatus.COMPLETED_WITH_LIMITATIONS: ("limitation_supporting_evidence",),
    CaseStatus.COMPLETED: (),
    CaseStatus.FAILED: ("processing_error_record",),
}


def _case_dir(case_dir: Path) -> Path:
    root = resolve_safe_case_path(Path(case_dir))
    state_path = root / "state.json"
    if not state_path.is_file():
        raise ValueError("case_state_missing")
    return root


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_case_artifact") from error


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("invalid_case_snapshot")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid_case_snapshot") from error


def _source(payload: object) -> RuleSource:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        return RuleSource(
            title=payload["title"],
            url=payload["url"],
            published_on=_date(payload["published_on"]),
            issuer=payload.get("issuer", ""),
            document_number=payload.get("document_number", ""),
            article_refs=tuple(payload.get("article_refs", ())),
            verified_on=_date(payload["verified_on"]) if payload.get("verified_on") else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_case_snapshot") from error


def _calculation(payload: object) -> CalculationRecord | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        return CalculationRecord(
            formula_id=payload["formula_id"],
            tax_base=payload["tax_base"],
            deductions=payload["deductions"],
            net_base=payload["net_base"],
            rate=payload["rate"],
            amount=payload["amount"],
            evidence_refs=tuple(payload["evidence_refs"]),
            rate_selector=payload.get("rate_selector"),
            rate_selector_label_zh=payload.get("rate_selector_label_zh"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_case_snapshot") from error


def _fact_gap(payload: object) -> FactGap:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        return FactGap(
            fact_key=payload["fact_key"],
            reason_code=payload["reason_code"],
            answer_type=payload["answer_type"],
            evidence_types=tuple(payload["evidence_types"]),
            priority=payload["priority"],
            blocking=payload["blocking"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_case_snapshot") from error


def _result(payload: object) -> TaxResult:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        return TaxResult(
            tax_id=payload["tax_id"],
            status=TaxStatus(payload["status"]),
            rule_pack_ids=tuple(payload.get("rule_pack_ids", ())),
            diagnostics=tuple(payload.get("diagnostics", ())),
            gaps=tuple(_fact_gap(item) for item in payload.get("gaps", ())),
            evidence_refs=tuple(payload.get("evidence_refs", ())),
            rule_sources=tuple(_source(item) for item in payload.get("rule_sources", ())),
            calculation=_calculation(payload.get("calculation")),
            rule_pack_versions=tuple(payload.get("rule_pack_versions", ())),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_case_snapshot") from error


def _question(payload: object) -> Question:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        raw_choices = payload.get("choices", ())
        if not isinstance(raw_choices, list | tuple) or any(
            not isinstance(item, Mapping) for item in raw_choices
        ):
            raise ValueError("invalid_case_snapshot")
        choices = tuple(
            PromptChoice(item["value"], item["label_zh"])
            for item in raw_choices
        )
        return Question(
            question_id=payload["question_id"],
            fact_key=payload["fact_key"],
            tax_ids=tuple(payload.get("tax_ids", ())),
            reason_codes=tuple(payload.get("reason_codes", ())),
            answer_type=payload["answer_type"],
            evidence_types=tuple(payload.get("evidence_types", ())),
            priority=payload["priority"],
            blocking=payload["blocking"],
            question_zh=payload.get("question_zh", ""),
            help_zh=payload.get("help_zh", ""),
            evidence_help_zh=payload.get("evidence_help_zh", ""),
            choices=choices,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_case_snapshot") from error


def _snapshot_from_dict(payload: object) -> TaxCheckSnapshot:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_case_snapshot")
    try:
        request = payload["request"]
        if not isinstance(request, Mapping):
            raise ValueError("invalid_case_snapshot")
        raw_facts = request.get("facts", ())
        if not isinstance(raw_facts, list | tuple) or any(
            not isinstance(item, Mapping) for item in raw_facts
        ):
            raise ValueError("invalid_case_snapshot")
        facts = tuple(
            TaxFact(
                key=item["key"],
                value=item["value"],
                state=FactState(item["state"]),
                evidence_refs=tuple(item.get("evidence_refs", ())),
                confirmed_by=item.get("confirmed_by"),
                confirmed_at=item.get("confirmed_at"),
            )
            for item in raw_facts
        )
        request = TaxCheckRequest(
            period=DateRange(_date(request["period"]["start"]), _date(request["period"]["end"])),
            jurisdiction=request["jurisdiction"],
            facts=facts,
            input_hashes=tuple(request.get("input_hashes", ())),
        )
        raw_events = payload.get("audit_events", ())
        raw_questions = payload.get("questions", ())
        raw_history = payload.get("question_history", ())
        raw_results = payload.get("results", ())
        raw_limitations = payload.get("limitations", ())
        for collection in (raw_events, raw_questions, raw_history, raw_results, raw_limitations):
            if not isinstance(collection, list | tuple) or any(
                not isinstance(item, Mapping) for item in collection
            ):
                raise ValueError("invalid_case_snapshot")
        events = tuple(
            AuditEvent(
                event_type=item["event_type"],
                occurred_at=item["occurred_at"],
                tax_ids=tuple(item.get("tax_ids", ())),
                fact_keys=tuple(item.get("fact_keys", ())),
                confirmation_digest=item.get("confirmation_digest"),
            )
            for item in raw_events
        )
        questions = tuple(_question(item) for item in raw_questions)
        history = tuple(_question(item) for item in raw_history)
        return TaxCheckSnapshot(
            request=request,
            results=tuple(_result(item) for item in raw_results),
            questions=questions,
            audit_events=events,
            revision=payload["revision"],
            schema_version=payload.get("schema_version", "1.0.0"),
            question_history=history,
            limitations=tuple(
                Limitation(item["code"], item["message_zh"])
                for item in raw_limitations
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "invalid_case_snapshot":
            raise
        raise ValueError("invalid_case_snapshot") from error


def load_case_snapshot(case_dir: Path) -> TaxCheckSnapshot:
    root = _case_dir(case_dir)
    path = root / "work" / PRIVATE_SNAPSHOT_NAME
    if not path.is_file():
        raise ValueError("case_snapshot_missing")
    return _snapshot_from_dict(_read_json(path))


def _derive_status(snapshot: TaxCheckSnapshot) -> CaseStatus:
    if snapshot.questions:
        return CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION
    statuses = {result.status for result in snapshot.results}
    if TaxStatus.BLOCKED in statuses:
        return CaseStatus.BLOCKED_DATA_QUALITY
    if snapshot.limitations or statuses & {TaxStatus.NEEDS_EVIDENCE, TaxStatus.NEEDS_LOCAL_POLICY}:
        return CaseStatus.COMPLETED_WITH_LIMITATIONS
    return CaseStatus.COMPLETED


def _next_action(status: CaseStatus) -> str:
    return {
        CaseStatus.WAITING_FOR_MATERIALS: "add_source_files",
        CaseStatus.WAITING_FOR_EXTRACTION: "complete_document_extraction",
        CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION: "answer_customer_tasks",
        CaseStatus.BLOCKED_DATA_QUALITY: "repair_source_data",
        CaseStatus.READY_FOR_SELF_CHECK: "run_self_check",
        CaseStatus.COMPLETED_WITH_LIMITATIONS: "review_limitations",
        CaseStatus.COMPLETED: "download_delivery",
        CaseStatus.FAILED: "inspect_processing_error",
    }.get(status, "review_case")


def _status_before_snapshot(state: Mapping[str, object]) -> CaseStatus:
    current = state.get("status")
    if current == CaseStatus.BLOCKED_DATA_QUALITY.value:
        return CaseStatus.BLOCKED_DATA_QUALITY
    if current in {"blocked_processing_error", CaseStatus.FAILED.value}:
        return CaseStatus.FAILED
    if current in {"waiting_for_extraction", "needs_extraction"}:
        return CaseStatus.WAITING_FOR_EXTRACTION
    if current in {"needs_sheet_selection", "needs_mapping_review"}:
        return CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION
    if current in {"partial_scope_ready", "needs_professional_review", "ready_for_self_check"}:
        return CaseStatus.READY_FOR_SELF_CHECK
    return CaseStatus.WAITING_FOR_MATERIALS


def _case_state(root: Path) -> Mapping[str, object]:
    state = _read_json(root / "state.json")
    if not isinstance(state, Mapping):
        raise ValueError("invalid_case_state")
    if state.get("status") == CaseStatus.BLOCKED_DATA_QUALITY.value:
        raise ValueError("blocked_data_quality")
    return state


def _persist_case_view(root: Path, snapshot: TaxCheckSnapshot) -> dict[str, object]:
    snapshot = replace(snapshot, limitations=effective_limitations(snapshot))
    status = _derive_status(snapshot)
    private_path = root / "work" / PRIVATE_SNAPSHOT_NAME
    public_path = root / "work" / PUBLIC_SNAPSHOT_NAME
    tasks_path = root / "work" / "customer-tasks.json"
    report_path = root / "output" / REPORT_NAME
    _write_json(private_path, to_dict(snapshot))
    tasks = build_case_customer_tasks(root)
    write_tax_health_snapshot(snapshot, public_path)
    write_tax_health_report(snapshot, report_path)
    _write_json(
        tasks_path,
        {
            "schema_version": TASKS_SCHEMA_VERSION,
            "revision": snapshot.revision,
            "status": status.value,
            "tasks": [task_payload(task) for task in tasks],
        },
    )
    state_path = root / "state.json"
    state = _read_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("invalid_case_state")
    state.update(
        {
            "case_schema_version": CASE_SCHEMA_VERSION,
            "status": status.value,
            "case_status": status.value,
            "revision": snapshot.revision,
            "next_action": _next_action(status),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": sorted(
                set(state.get("artifacts", ()))
                | {
                    "work/" + PRIVATE_SNAPSHOT_NAME,
                    "work/" + PUBLIC_SNAPSHOT_NAME,
                    "work/customer-tasks.json",
                    "output/" + REPORT_NAME,
                    "output/" + HANDOFF_NAME,
                }
            ),
        }
    )
    _write_json(state_path, state)
    build_case_customer_handoff(root)
    return case_status_payload(root)


def run_case_self_check(
    case_dir: Path,
    request: TaxCheckRequest,
    *,
    occurred_at: str | None = None,
    packs=None,
    evaluators: Mapping[str, object] | None = None,
    limitations: tuple[Limitation, ...] = (),
) -> dict[str, object]:
    """Run the deterministic check and atomically publish its resumable view."""
    root = _case_dir(case_dir)
    state = _case_state(root)
    if any(fact.state is FactState.CONFIRMED for fact in request.facts):
        raise ValueError("initial_confirmed_fact_forbidden")
    review_period = state.get("review_period")
    if review_period is not None:
        if not isinstance(review_period, Mapping) or (
            review_period.get("start") != request.period.start.isoformat()
            or review_period.get("end") != request.period.end.isoformat()
        ):
            raise ValueError("case_period_mismatch")
    private_path = root / "work" / PRIVATE_SNAPSHOT_NAME
    if private_path.is_file():
        existing = load_case_snapshot(root)
        expected_limitations = effective_limitations(
            replace(existing, limitations=tuple(limitations))
        )
        if existing.request == request and existing.limitations == expected_limitations:
            return case_status_payload(root)
        raise ValueError("case_snapshot_exists")
    snapshot = run_tax_health_check(
        request,
        packs=packs,
        evaluators=evaluators,
        occurred_at=occurred_at,
    )
    snapshot = replace(snapshot, limitations=tuple(limitations))
    return _persist_case_view(root, snapshot)


def _load_case_cross_check_run(root: Path) -> TaxCrossCheckRun | None:
    path = root / "work" / "tax-cross-checks.json"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > _CROSS_CHECK_LIMIT:
        raise ValueError("invalid_tax_cross_check")
    return parse_tax_cross_check_payload(json.loads(raw.decode("utf-8")))


def build_case_customer_tasks(
    case_dir: Path,
) -> tuple[AgentQuestionTask | dict[str, object], ...]:
    root = _case_dir(case_dir)
    try:
        health_tasks = tuple(build_agent_question_tasks(load_case_snapshot(root)))
    except ValueError as error:
        if str(error) != "case_snapshot_missing":
            raise
        health_tasks = ()
    run = _load_case_cross_check_run(root)
    cross_check_tasks = (
        build_cross_check_question_tasks(run, max_questions=3)
        if run is not None and run.status == "completed"
        else ()
    )
    combined: list[AgentQuestionTask | dict[str, object]] = []
    seen: set[str] = set()
    for item in (*cross_check_tasks, *health_tasks):
        task_id = item.task_id if isinstance(item, AgentQuestionTask) else str(item["task_id"])
        if task_id in seen:
            continue
        seen.add(task_id)
        combined.append(item)
        if len(combined) == 3:
            break
    return tuple(combined)


def _verified_manifest_source(root: Path, source_id: str) -> bool:
    try:
        manifest_path = resolve_safe_source(
            root / "work" / "input_manifest.json", root / "work"
        )
        raw = manifest_path.read_bytes()
        if len(raw) > _INPUT_MANIFEST_LIMIT:
            return False
        payload = json.loads(raw.decode("utf-8"))
        sources = payload.get("sources") if isinstance(payload, Mapping) else None
        if not isinstance(sources, list):
            return False
        matches = [
            item for item in sources
            if isinstance(item, Mapping) and item.get("source_id") == source_id
        ]
        if len(matches) != 1:
            return False
        item = matches[0]
        filename = item.get("filename")
        sha256 = item.get("sha256")
        if (
            not isinstance(filename, str)
            or not filename
            or item.get("status") not in {"accepted", "present_unparsed"}
        ):
            return False
        result = verify_evidence_file(root / "input", filename, sha256)
        return result.status == "verified"
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _confirmation_evidence_is_verified(root: Path, confirmation: Confirmation) -> bool:
    customer_ref = f"customer-confirmation:{confirmation.question_id}"
    return all(
        reference == customer_ref or _verified_manifest_source(root, reference)
        for reference in confirmation.evidence_refs
    )


def _cross_check_bridge(payload: object) -> tuple[AmountBridgeItem, ...]:
    if not isinstance(payload, list):
        raise ValueError("invalid_cross_check_confirmation")
    items: list[AmountBridgeItem] = []
    for item in payload:
        if not isinstance(item, Mapping) or set(item) not in (
            {"amount", "label_zh", "evidence_refs"},
            {"amount", "label_zh", "evidence_refs", "component_id"},
        ) or not isinstance(item.get("evidence_refs"), list):
            raise ValueError("invalid_cross_check_confirmation")
        try:
            amount = __import__("decimal").Decimal(item["amount"])
            items.append(
                AmountBridgeItem(
                    amount=amount,
                    label_zh=item["label_zh"],
                    evidence_refs=tuple(item["evidence_refs"]),
                    component_id=item.get("component_id"),
                )
            )
        except (ArithmeticError, TypeError, ValueError):
            raise ValueError("invalid_cross_check_confirmation") from None
    return tuple(items)


def _cross_check_rerun_inputs(
    root: Path,
    run: TaxCrossCheckRun,
    check_id: str,
) -> TaxCrossCheckRun:
    from .account_classification import load_active_account_classifications
    from .pipeline import load_case_structured_documents
    from .tax_cross_checks import CrossCheckContext

    binding_events = tuple(
        event[len(_CROSS_CHECK_INPUT_BINDING_PREFIX):]
        for event in run.audit_events
        if event.startswith(_CROSS_CHECK_INPUT_BINDING_PREFIX)
    )
    if len(binding_events) > 1:
        raise ValueError("cross_check_rerun_inputs_invalid")
    if binding_events:
        try:
            binding = json.loads(binding_events[0])
        except (TypeError, ValueError):
            raise ValueError("cross_check_rerun_inputs_invalid") from None
        if not isinstance(binding, Mapping) or set(binding) != {
            "normalized_journal_sha256", "structured_document_sha256"
        } or not isinstance(binding["structured_document_sha256"], Mapping):
            raise ValueError("cross_check_rerun_inputs_invalid")
        expected_journal = binding["normalized_journal_sha256"]
        journal_path = root / "work" / "normalized_journal.jsonl"
        if expected_journal is not None:
            if not isinstance(expected_journal, str) or not journal_path.is_file():
                raise ValueError("cross_check_rerun_inputs_invalid")
            journal_raw = resolve_safe_source(journal_path, root / "work").read_bytes()
            if hashlib.sha256(journal_raw).hexdigest() != expected_journal:
                raise ValueError("cross_check_rerun_inputs_invalid")
        for relative, expected_digest in binding["structured_document_sha256"].items():
            if not isinstance(relative, str) or not isinstance(expected_digest, str):
                raise ValueError("cross_check_rerun_inputs_invalid")
            try:
                path = resolve_safe_source(root / "work" / relative, root / "work")
                actual_digest = (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.is_file()
                    else None
                )
            except (OSError, ValueError):
                raise ValueError("cross_check_rerun_inputs_invalid") from None
            if actual_digest != expected_digest:
                raise ValueError("cross_check_rerun_inputs_invalid")

    journal_path = root / "work" / "normalized_journal.jsonl"
    rows: tuple[Mapping[str, object], ...] = ()
    if journal_path.is_file():
        raw = resolve_safe_source(journal_path, root / "work").read_bytes()
        if len(raw) > _CROSS_CHECK_LIMIT:
            raise ValueError("cross_check_rerun_inputs_invalid")
        digest_path = root / "work" / "normalized_journal.sha256"
        try:
            expected_digest = digest_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise ValueError("cross_check_rerun_inputs_invalid") from None
        if expected_digest != hashlib.sha256(raw).hexdigest():
            raise ValueError("cross_check_rerun_inputs_invalid")
        parsed_rows = tuple(
            json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()
        )
        if any(not isinstance(item, Mapping) for item in parsed_rows):
            raise ValueError("cross_check_rerun_inputs_invalid")
        rows = parsed_rows
    try:
        classifications = load_active_account_classifications(
            root, run.source_manifest_sha256
        )
    except ValueError:
        classifications = None
    statements, returns, loader_issues = load_case_structured_documents(root)
    if loader_issues:
        raise ValueError("cross_check_rerun_inputs_invalid")
    state = _read_json(root / "state.json")
    if not isinstance(state, Mapping) or not isinstance(state.get("review_period"), Mapping):
        raise ValueError("cross_check_rerun_inputs_invalid")
    review_period = state["review_period"]
    document = (statements + returns)[0] if statements or returns else None
    return rerun_tax_cross_check(
        run,
        check_id,
        rows,
        classifications,
        statements,
        returns,
        CrossCheckContext(
            period_start=review_period["start"],
            period_end=review_period["end"],
            entity_name=state["entity_name"],
            currency=document.currency if document is not None else "CNY",
            unit_multiplier=document.unit_multiplier if document is not None else "1",
        ),
        run.source_manifest_sha256,
    )


def _pending_explanation_matches(
    run: TaxCrossCheckRun,
    payload: Mapping[str, object],
) -> bool:
    target = next((item for item in run.results if item.check_id == payload.get("check_id")), None)
    if target is None or target.resolution_status != "explained_pending_verification" or not target.explanations:
        return False
    previous = target.explanations[-1]
    return (
        previous.category == payload.get("category")
        and previous.explanation_zh == payload.get("explanation_zh")
        and previous.confirmed_by == payload.get("confirmed_by")
        and previous.confirmed_at == payload.get("confirmed_at")
        and isinstance(payload.get("evidence_refs"), list)
        and set(previous.evidence_refs).issubset(payload["evidence_refs"])
    )


def confirm_cross_check_explanation(
    case_dir: Path,
    payload: dict,
) -> TaxCrossCheckRun:
    """Persist one evidence-bound customer explanation as a new cross-check revision."""

    root = _case_dir(case_dir)
    if not isinstance(payload, dict) or set(payload) != _CROSS_CHECK_CONFIRMATION_FIELDS:
        raise ValueError("invalid_cross_check_confirmation")
    lock_path = root / "work" / ".cross-check-confirm.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("cross_check_confirmation_locked") from None
    os.close(lock_fd)
    try:
        cross_path = resolve_safe_source(
            root / "work" / "tax-cross-checks.json", root / "work"
        )
        raw = cross_path.read_bytes()
        if len(raw) > _CROSS_CHECK_LIMIT:
            raise ValueError("invalid_cross_check_confirmation")
        run = parse_tax_cross_check_payload(json.loads(raw.decode("utf-8")))
        manifest_path = resolve_safe_source(
            root / "work" / "input_manifest.json", root / "work"
        )
        manifest_raw = manifest_path.read_bytes()
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        if (
            payload.get("source_manifest_sha256") != manifest_sha256
            or run.source_manifest_sha256 != manifest_sha256
        ):
            raise ValueError("cross_check_confirmation_binding_mismatch")
        question = next(
            (
                item for item in run.questions
                if item.question_id == payload.get("question_id")
                and item.check_id == payload.get("check_id")
            ),
            None,
        )
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event_prefix = f"cross-check-confirmation:{payload.get('question_id')}:"
        previous = tuple(item for item in run.audit_events if item.startswith(event_prefix))
        if previous:
            if event_prefix + digest in previous:
                return run
            if not _pending_explanation_matches(run, payload):
                raise ValueError("cross_check_confirmation_conflict")
        pending_target = next(
            (
                item for item in run.results
                if item.check_id == payload.get("check_id")
                and item.resolution_status == "explained_pending_verification"
            ),
            None,
        )
        if pending_target is not None and not _pending_explanation_matches(run, payload):
            raise ValueError("cross_check_confirmation_conflict")
        if question is None:
            raise ValueError("cross_check_confirmation_binding_mismatch")
        evidence_refs = payload.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError("invalid_cross_check_confirmation")
        bridge = _cross_check_bridge(payload.get("bridge"))
        all_refs = tuple(evidence_refs) + tuple(
            ref for item in bridge for ref in item.evidence_refs
        )
        if any(not _verified_manifest_source(root, ref) for ref in all_refs):
            raise ValueError("unverified_cross_check_evidence")
        try:
            explanation = CrossCheckExplanation(
                check_id=payload["check_id"],
                category=payload["category"],
                explanation_zh=payload["explanation_zh"],
                confirmed_by=payload["confirmed_by"],
                confirmed_at=payload["confirmed_at"],
                evidence_refs=tuple(evidence_refs),
            )
            rerun = _cross_check_rerun_inputs(root, run, payload["check_id"])
            target = next(
                item for item in rerun.results if item.check_id == payload["check_id"]
            )
            revised = (
                apply_cross_check_explanation(
                    rerun,
                    explanation,
                    bridge=bridge,
                    verified_evidence_refs=all_refs,
                )
                if target.status.value == "difference_found"
                else rerun
            )
        except ValueError as error:
            if str(error) in {
                "unverified_cross_check_evidence",
                "cross_check_rerun_inputs_invalid",
            }:
                raise
            raise ValueError("invalid_cross_check_confirmation") from None
        revised = replace(
            revised,
            audit_events=revised.audit_events + (event_prefix + digest,),
        )
        pointer = root / "work" / "delivery.json"
        private_path = root / "work" / PRIVATE_SNAPSHOT_NAME
        transaction_paths = (
            cross_path,
            private_path,
            root / "work" / PUBLIC_SNAPSHOT_NAME,
            root / "work" / "customer-tasks.json",
            root / "output" / REPORT_NAME,
            root / "output" / HANDOFF_NAME,
            root / "state.json",
            pointer,
        )
        original_bytes = {
            path: path.read_bytes() if path.is_file() else None
            for path in transaction_paths
        }
        try:
            pointer.unlink(missing_ok=True)
            write_json(cross_path, tax_cross_check_payload(revised))
            if private_path.is_file():
                snapshot = load_case_snapshot(root)
                target = next(
                    item for item in revised.results if item.check_id == payload["check_id"]
                )
                snapshot = replace(
                    snapshot,
                    revision=snapshot.revision + 1,
                    audit_events=snapshot.audit_events
                    + (
                        AuditEvent(
                            event_type="cross_check_confirmation",
                            occurred_at=payload["confirmed_at"],
                            tax_ids=target.tax_ids,
                            confirmation_digest=digest,
                        ),
                    ),
                )
                _persist_case_view(root, snapshot)
        except Exception:
            for path, content in original_bytes.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        temporary = path.with_name(f".{path.name}.cross-check-restore")
                        temporary.write_bytes(content)
                        os.replace(temporary, path)
                except OSError:
                    pass
            raise ValueError("cross_check_confirmation_write_failed") from None
        return revised
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_cross_check_confirmation") from error
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def apply_case_confirmations(
    case_dir: Path,
    confirmations: tuple[Confirmation, ...],
    *,
    packs=None,
    evaluators: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Apply a validated batch and persist only the affected tax reruns."""
    root = _case_dir(case_dir)
    _case_state(root)
    if any(not confirmation.evidence_refs for confirmation in confirmations):
        raise ValueError("confirmation_evidence_required")
    if any(
        not _confirmation_evidence_is_verified(root, confirmation)
        for confirmation in confirmations
    ):
        raise ValueError("unverified_confirmation_evidence")
    snapshot = load_case_snapshot(root)
    updated = continue_tax_health_check(
        snapshot,
        tuple(confirmations),
        packs=packs,
        evaluators=evaluators,
    )
    return _persist_case_view(root, updated)


def case_status_payload(case_dir: Path) -> dict[str, object]:
    root = _case_dir(case_dir)
    private_path = root / "work" / PRIVATE_SNAPSHOT_NAME
    state = _read_json(root / "state.json")
    if not isinstance(state, Mapping):
        raise ValueError("invalid_case_state")
    state_status = _status_before_snapshot(state)
    hard_state = state_status in {CaseStatus.BLOCKED_DATA_QUALITY, CaseStatus.FAILED}
    tasks: tuple[AgentQuestionTask | dict[str, object], ...] = ()
    cross_check_run = None
    if not hard_state:
        tasks = build_case_customer_tasks(root)
        cross_check_run = _load_case_cross_check_run(root)
    if private_path.is_file() and not hard_state:
        snapshot = load_case_snapshot(root)
        status = _derive_status(snapshot)
        revision = snapshot.revision
    else:
        status = state_status
        revision = load_case_snapshot(root).revision if private_path.is_file() else 0
    if tasks and not hard_state:
        status = CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION
        pending_task_count = len(tasks)
        required_evidence_types = sorted({
            evidence_type
            for task in tasks
            for evidence_type in (
                task.get("required_evidence_types", ())
                if isinstance(task, Mapping)
                else task.evidence_types
            )
        })
        cross_check_ids = {
            str(task["check_id"])
            for task in tasks
            if isinstance(task, Mapping) and isinstance(task.get("check_id"), str)
        }
        affected_tax_ids = sorted({
            tax_id
            for result in (cross_check_run.results if cross_check_run is not None else ())
            if result.check_id in cross_check_ids
            for tax_id in result.tax_ids
        } | {
            tax_id
            for task in tasks
            if isinstance(task, AgentQuestionTask)
            for tax_id in task.tax_ids
        })
        first = tasks[0]
        customer_message_zh = (
            first["question_zh"] if isinstance(first, Mapping) else first.question_zh
        )
    elif private_path.is_file() and not hard_state:
        pending_task_count = 0
        required_evidence_types = []
        affected_tax_ids = []
        customer_message_zh = _STATUS_MESSAGES_ZH[status]
    else:
        pending_task_count = 0
        required_evidence_types = list(_STATUS_EVIDENCE_TYPES[status])
        affected_tax_ids = []
        customer_message_zh = _STATUS_MESSAGES_ZH[status]
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "status": status.value,
        "revision": revision,
        "next_action": _next_action(status),
        "pending_task_count": pending_task_count,
        "status_label_zh": _STATUS_LABELS_ZH[status],
        "customer_message_zh": customer_message_zh,
        "required_evidence_types": required_evidence_types,
        "affected_tax_ids": affected_tax_ids,
        "task_path": f"work/customer-tasks.json",
        "report_path": f"output/{REPORT_NAME}",
        "handoff_path": f"output/{HANDOFF_NAME}",
        "snapshot_path": f"work/{PUBLIC_SNAPSHOT_NAME}",
    }


def customer_handoff_payload(
    snapshot: TaxCheckSnapshot,
    status: Mapping[str, object],
    artifacts: tuple[dict[str, object], ...],
) -> dict[str, object]:
    case_status = str(status["status"])
    title = (
        "本次可执行检查程序已完成"
        if case_status == CaseStatus.COMPLETED.value
        else "第一轮账务初筛已完成"
    )
    pending_count = int(status["pending_task_count"])
    unresolved_count = sum(
        result.status in {TaxStatus.NEEDS_EVIDENCE, TaxStatus.NEEDS_LOCAL_POLICY}
        for result in snapshot.results
    )
    return {
        "schema_version": "tax-customer-handoff/v1",
        "revision": snapshot.revision,
        "case_status": case_status,
        "title_zh": title,
        "message_zh": (
            f"本次已运行现有资料支持的确定性检查程序；仍有 {pending_count} 项待客户确认，"
            f"{unresolved_count} 个税种受资料或当地政策限制。"
        ),
        "amount_disclaimer_zh": "试算金额不代表应补税额、欠税金额或最终申报金额。",
        "pending_task_count": pending_count,
        "limitations": [to_dict(item) for item in effective_limitations(snapshot)],
        "artifacts": list(artifacts),
    }


def build_case_customer_handoff(case_dir: Path) -> dict[str, object]:
    root = _case_dir(case_dir)
    snapshot = load_case_snapshot(root)
    status = case_status_payload(root)
    from .delivery import verified_delivery_artifacts

    payload = customer_handoff_payload(
        snapshot,
        status,
        verified_delivery_artifacts(root),
    )
    _write_json(root / "output" / HANDOFF_NAME, payload)
    return payload
