from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from .case import init_case, resolve_safe_case_path, set_case_scope
from .customer_guide import build_case_guide, start_case_from_profile
from .case_orchestration import (
    apply_case_confirmations,
    build_case_customer_tasks,
    case_status_payload,
    run_case_self_check,
)
from .delivery import DeliveryError, build_case_delivery
from .agent_orchestration import evidence_bundle_delivery_payload, task_payload
from .document_extraction import document_extraction_from_dict, document_extraction_to_dict
from .document_providers import ExtractionOptions, HostProvider, LocalDocumentSource
from .evidence_bundle import (
    BundleArtifact,
    EvidenceBundleError,
    build_evidence_bundle,
    parse_evidence_manifest,
    validate_request_confirmation_evidence,
    verify_evidence_manifest,
)
from .local_ocr import LocalOcrProvider
from .ocr_runtime import OcrDependencyError, prepare_ocr_models
from .pipeline import inspect_case, run_case
from .tax_health import run_tax_health_check
from .tax_io import tax_request_from_dict
from .tax_models import Confirmation
from .tax_reporting import (
    PdfExportUnavailable,
    write_tax_health_report_pdf,
    write_tax_health_snapshot,
)
from .report_view import REPORT_SCHEMA_VERSION_V3
from .tax_registry import (
    TaxPackError,
    load_builtin_parameterized_calculation_packs,
    validate_pack_payload,
)
from .tax_screening import builtin_tax_evaluators
from .reviews import (
    MappingReviewError,
    ReviewError,
    confirm_mapping,
    confirm_sheet_selection,
)
from .account_classification import confirm_account_classifications
from .source_paths import resolve_safe_source
from .structured_document_providers import (
    StructuredDocumentImportError,
    StructuredDocumentSource,
    persist_financial_statement,
    persist_tax_return,
)
from .structured_documents import (
    financial_statement_from_dict,
    tax_return_from_dict,
)


_EVIDENCE_ERROR_CODES = frozenset({
    "invalid_evidence_manifest",
    "unsafe_evidence_path",
    "evidence_verification_failed",
    "unverified_evidence_reference",
    "evidence_bundle_size_limit_exceeded",
    "evidence_bundle_write_failed",
})
_STRUCTURED_KINDS = frozenset({"financial-statement", "tax-return"})
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STRUCTURED_JSON_LIMIT = 20 * 1024 * 1024


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _read_structured_json(path: Path) -> dict[str, object]:
    try:
        safe_path = resolve_safe_source(path, path.parent)
        raw = safe_path.read_bytes()
        if len(raw) > _STRUCTURED_JSON_LIMIT:
            raise ValueError("invalid_structured_document")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("invalid_structured_document") from None
    if not isinstance(payload, dict):
        raise ValueError("invalid_structured_document")
    return payload


def _structured_case_source(case_dir: Path, source_id: str) -> StructuredDocumentSource:
    if _SOURCE_ID.fullmatch(source_id) is None:
        raise ValueError("invalid_source")
    try:
        case_dir = resolve_safe_case_path(case_dir)
        manifest_path = resolve_safe_source(case_dir / "work" / "input_manifest.json", case_dir / "work")
        state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = next(item for item in manifest["sources"] if item.get("source_id") == source_id)
        filename = Path(item["filename"])
        review_period = state["review_period"]
        media_type = item["media_type"]
        sha256 = item["sha256"]
        entity_name = state["entity_name"]
        period_start = review_period["start"]
        period_end = review_period["end"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, StopIteration, TypeError):
        raise ValueError("invalid_source") from None
    if (
        filename.is_absolute()
        or ".." in filename.parts
        or not isinstance(media_type, str)
        or not isinstance(sha256, str)
        or not isinstance(entity_name, str)
        or not isinstance(period_start, str)
        or not isinstance(period_end, str)
    ):
        raise ValueError("invalid_source")
    return StructuredDocumentSource(
        source_id=source_id,
        path=case_dir / "input" / filename,
        media_type=media_type,
        input_root=case_dir / "input",
        sha256=sha256,
        entity_name=entity_name,
        period_start=period_start,
        period_end=period_end,
    )


def _safe_input_path(path: Path, input_dir: Path) -> bool:
    try:
        root = input_dir.absolute()
        candidate = path.absolute()
        if candidate == root or root not in candidate.parents:
            return False
        current = candidate
        while current != root:
            if current.is_symlink() or bool(getattr(current.lstat(), "st_file_attributes", 0) & 0x400):
                return False
            current = current.parent
        resolved_root = root.resolve(strict=True)
        resolved_path = candidate.resolve(strict=True)
        return resolved_path != resolved_root and resolved_root in resolved_path.parents and resolved_path.is_file()
    except OSError:
        return False


def _safe_pack_path(path: Path) -> bool:
    """Reject explicit rule-pack links/reparse points before reading them."""
    try:
        candidate = path.absolute()
        if not candidate.is_file():
            return False
        current = candidate
        while True:
            if current.is_symlink() or bool(getattr(current.lstat(), "st_file_attributes", 0) & 0x400):
                return False
            if current.parent == current:
                break
            current = current.parent
        return True
    except OSError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Create a resumable case directory")
    init_parser.add_argument("--case", required=True, type=Path)
    init_parser.add_argument("--entity", required=True)
    init_parser.add_argument("--period-start")
    init_parser.add_argument("--period-end")
    guide_parser = subparsers.add_parser("case-guide", help="Read the customer-facing case guide")
    guide_parser.add_argument("--case", required=True, type=Path)
    start_parser = subparsers.add_parser("case-start", help="Start or resume a case from a customer profile")
    start_parser.add_argument("--case", required=True, type=Path)
    start_parser.add_argument("--profile", required=True, type=Path)
    set_scope_parser = subparsers.add_parser("set-scope", help="Set the case review period")
    set_scope_parser.add_argument("--case", required=True, type=Path)
    set_scope_parser.add_argument("--period-start", required=True)
    set_scope_parser.add_argument("--period-end", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inventory workbook sheets and evidence coverage",
    )
    inspect_parser.add_argument("--case", required=True, type=Path)
    select_parser = subparsers.add_parser(
        "select-sheet",
        help="Confirm a source-bound worksheet selection",
    )
    select_parser.add_argument("--case", required=True, type=Path)
    select_parser.add_argument("--role", required=True)
    select_parser.add_argument("--source", required=True)
    select_parser.add_argument("--sheet", required=True)
    select_parser.add_argument(
        "--header-row",
        required=True,
        action="append",
        type=int,
    )
    select_parser.add_argument("--hidden-rows", required=True)
    select_parser.add_argument("--reviewer", required=True)
    mapping_parser = subparsers.add_parser(
        "confirm-mapping",
        help="Confirm a complete source-bound field mapping",
    )
    mapping_parser.add_argument("--case", required=True, type=Path)
    mapping_parser.add_argument("--mapping", required=True, type=Path)
    classification_parser = subparsers.add_parser(
        "confirm-account-classification",
        help="Confirm source-bound account classifications",
    )
    classification_parser.add_argument("--case", required=True, type=Path)
    classification_parser.add_argument("--input", required=True, type=Path)
    run_parser = subparsers.add_parser(
        "run",
        help="Normalize selected CSV/Excel accounting sources and run screening",
    )
    run_parser.add_argument("--case", required=True, type=Path)
    run_parser.add_argument("--ai-mode", choices=["off", "assist", "deep"], default="assist")
    validate_parser = subparsers.add_parser("validate-extraction", help="Validate document extraction JSON")
    validate_parser.add_argument("--input", required=True, type=Path)
    import_parser = subparsers.add_parser("import-extraction", help="Import host extraction JSON into a case")
    import_parser.add_argument("--case", required=True, type=Path)
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--input", required=True, type=Path)
    structured_validate_parser = subparsers.add_parser(
        "validate-structured-document", help="Validate a structured financial or tax document"
    )
    structured_validate_parser.add_argument("--kind", required=True)
    structured_validate_parser.add_argument("--input", required=True, type=Path)
    structured_import_parser = subparsers.add_parser(
        "import-structured-document", help="Import a source-bound structured financial or tax document"
    )
    structured_import_parser.add_argument("--case", required=True, type=Path)
    structured_import_parser.add_argument("--source", required=True)
    structured_import_parser.add_argument("--kind", required=True)
    structured_import_parser.add_argument("--input", required=True, type=Path)
    prepare_parser = subparsers.add_parser("prepare-ocr", help="Prepare and attest local OCR models")
    prepare_parser.add_argument("--model-dir", required=True, type=Path)
    extract_parser = subparsers.add_parser("extract-document", help="Extract text from a local document")
    extract_parser.add_argument("--case", required=True, type=Path)
    extract_parser.add_argument("--source", required=True)
    extract_parser.add_argument("--provider", required=True, choices=["local-paddleocr"])
    extract_parser.add_argument("--model-dir", required=True, type=Path)
    health_parser = subparsers.add_parser("tax-health-check", help="Run the Chinese tax health self-check")
    health_parser.add_argument("--request", required=True, type=Path)
    health_parser.add_argument("--output-dir", required=True, type=Path)
    health_parser.add_argument("--evidence-manifest", type=Path)
    health_parser.add_argument("--evidence-root", type=Path)
    health_parser.add_argument("--include-evidence-files", action="store_true")
    health_parser.add_argument("--pack", action="append", type=Path)
    health_parser.add_argument(
        "--include-parameterized-packs",
        action="store_true",
        help="Include customer-confirmed calculation templates for all 20 tax items",
    )
    self_check_parser = subparsers.add_parser(
        "case-self-check", help="Run and persist a resumable customer self-check case"
    )
    self_check_parser.add_argument("--case", required=True, type=Path)
    self_check_parser.add_argument("--request", required=True, type=Path)
    self_check_parser.add_argument("--occurred-at")
    status_parser = subparsers.add_parser("case-status", help="Read the deterministic case status")
    status_parser.add_argument("--case", required=True, type=Path)
    tasks_parser = subparsers.add_parser("case-tasks", help="Read pending customer confirmation tasks")
    tasks_parser.add_argument("--case", required=True, type=Path)
    confirm_case_parser = subparsers.add_parser("case-confirm", help="Apply a structured customer confirmation batch")
    confirm_case_parser.add_argument("--case", required=True, type=Path)
    confirm_case_parser.add_argument("--input", required=True, type=Path)
    delivery_parser = subparsers.add_parser(
        "case-delivery", help="Publish one revision-consistent customer delivery"
    )
    delivery_parser.add_argument("--case", required=True, type=Path)
    delivery_parser.add_argument("--include-evidence-files", action="store_true")
    return parser


def main() -> int:
    _configure_utf8_output()
    args = build_parser().parse_args()
    if args.command == "init":
        print(
            json.dumps(
                init_case(args.case, args.entity, args.period_start, args.period_end),
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "case-guide":
        try:
            result = build_case_guide(args.case)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            allowed = {"unsafe_case_path", "invalid_case_state"}
            error_code = str(error) if str(error) in allowed else "case_guide_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "case-start":
        try:
            profile_path = resolve_safe_source(args.profile, args.profile.parent)
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if not isinstance(profile, dict):
                raise ValueError("invalid_customer_profile")
            start_case_from_profile(args.case, profile)
            result = build_case_guide(args.case)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            allowed = {
                "invalid_entity_name", "invalid_province_city", "invalid_primary_business",
                "invalid_review_period", "review_period_out_of_range", "invalid_vat_taxpayer_type",
                "invalid_customer_profile", "case_period_mismatch", "case_entity_mismatch",
                "invalid_case_state", "unsafe_case_path",
            }
            error_code = str(error) if str(error) in allowed else "case_start_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "set-scope":
        print(json.dumps(set_case_scope(args.case, args.period_start, args.period_end), ensure_ascii=False))
        return 0
    if args.command == "inspect":
        code, result = inspect_case(args.case)
        print(json.dumps(result, ensure_ascii=False))
        return code
    if args.command == "select-sheet":
        try:
            decision = confirm_sheet_selection(
                args.case,
                role=args.role,
                source_id=args.source,
                sheet_id=args.sheet,
                header_rows=tuple(args.header_row),
                hidden_rows=args.hidden_rows,
                reviewer=args.reviewer,
            )
        except ReviewError as error:
            print(
                json.dumps(
                    {
                        "status": "needs_sheet_selection",
                        "error_type": str(error),
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "needs_sheet_selection",
                        "error_type": "select_sheet_failed",
                    },
                    ensure_ascii=False,
                )
            )
            return 4
        print(
            json.dumps(
                {
                    "status": "sheet_selection_confirmed",
                    "selection": decision,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "confirm-mapping":
        try:
            mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": "invalid_mapping_file",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        try:
            confirm_mapping(args.case, mapping_payload)
        except MappingReviewError as error:
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": str(error),
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "needs_mapping_review",
                        "error_type": "confirm_mapping_failed",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        print(json.dumps({"status": "mapping_confirmed"}, ensure_ascii=False))
        return 0
    if args.command == "confirm-account-classification":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            print(
                json.dumps(
                    {
                        "status": "needs_account_classification_review",
                        "error_type": "invalid_account_classification_file",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        try:
            confirm_account_classifications(args.case, payload)
        except (ReviewError, ValueError) as error:
            allowed = {
                "invalid_account_classification",
                "account_classification_binding_mismatch",
                "unsafe_review_path",
            }
            error_type = (
                str(error)
                if str(error) in allowed
                else "confirm_account_classification_failed"
            )
            print(
                json.dumps(
                    {
                        "status": "needs_account_classification_review",
                        "error_type": error_type,
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "needs_account_classification_review",
                        "error_type": "confirm_account_classification_failed",
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        print(json.dumps({"status": "account_classification_confirmed"}, ensure_ascii=False))
        return 0
    if args.command == "run":
        code, result = run_case(args.case, args.ai_mode)
        print(json.dumps(result, ensure_ascii=False))
        return code
    if args.command == "case-self-check":
        try:
            request = tax_request_from_dict(json.loads(args.request.read_text(encoding="utf-8")))
            result = run_case_self_check(args.case, request, occurred_at=args.occurred_at)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            allowed = {
                "case_state_missing", "unsafe_case_path", "invalid_case_state",
                "blocked_data_quality", "case_period_mismatch",
                "initial_confirmed_fact_forbidden", "case_snapshot_exists",
            }
            error_code = str(error) if str(error) in allowed else "case_self_check_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "case-status":
        try:
            result = case_status_payload(args.case)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            allowed = {"case_snapshot_missing", "case_state_missing", "unsafe_case_path"}
            error_code = str(error) if str(error) in allowed else "case_status_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "case-tasks":
        try:
            tasks = build_case_customer_tasks(args.case)
            result = {
                "schema_version": "tax-customer-tasks/v1",
                "tasks": [task_payload(task) for task in tasks],
            }
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            allowed = {"case_snapshot_missing", "case_state_missing", "unsafe_case_path"}
            error_code = str(error) if str(error) in allowed else "case_tasks_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "case-confirm":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("invalid_confirmation_batch")
            required_confirmation_fields = {
                "question_id", "fact_key", "value", "confirmed_by", "confirmed_at",
            }
            allowed_confirmation_fields = required_confirmation_fields | {"evidence_refs"}
            if any(
                not required_confirmation_fields.issubset(item)
                or not set(item).issubset(allowed_confirmation_fields)
                for item in payload
            ):
                raise ValueError("invalid_confirmation_batch")
            confirmations = tuple(
                Confirmation(
                    question_id=item["question_id"],
                    fact_key=item["fact_key"],
                    value=item["value"],
                    confirmed_by=item["confirmed_by"],
                    confirmed_at=item["confirmed_at"],
                    evidence_refs=tuple(item.get("evidence_refs", ())),
                )
                for item in payload
            )
            result = apply_case_confirmations(args.case, confirmations)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            allowed = {
                "unknown_question", "confirmation_fact_mismatch", "invalid_confirmation_value",
                "invalid_confirmation_batch", "case_snapshot_missing", "case_state_missing", "unsafe_case_path",
                "blocked_data_quality",
                "confirmation_evidence_required",
                "unverified_confirmation_evidence",
            }
            error_code = str(error) if str(error) in allowed else "case_confirmation_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "case-delivery":
        try:
            result = build_case_delivery(
                args.case,
                include_evidence_files=args.include_evidence_files,
            )
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            allowed = {
                "case_state_missing", "unsafe_case_path", "case_snapshot_missing",
                "delivery_blocked_data_quality", "delivery_source_manifest_invalid",
                "delivery_evidence_unverified", "delivery_pointer_invalid",
                "delivery_manifest_invalid", "delivery_revision_conflict",
                "delivery_pointer_write_failed", "delivery_write_failed",
            }
            error_code = str(error) if str(error) in allowed else "case_delivery_failed"
            print(json.dumps({"status": "failed", "error_code": error_code}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "validate-structured-document":
        if args.kind not in _STRUCTURED_KINDS:
            print(json.dumps({"valid": False, "error_code": "invalid_kind"}, ensure_ascii=False))
            return 4
        try:
            payload = _read_structured_json(args.input)
            value = (
                financial_statement_from_dict(payload)
                if args.kind == "financial-statement"
                else tax_return_from_dict(payload)
            )
        except (TypeError, ValueError):
            print(json.dumps({"valid": False, "error_code": "invalid_structured_document"}, ensure_ascii=False))
            return 4
        print(json.dumps({"valid": True, "kind": args.kind, "schema_version": value.schema_version}, ensure_ascii=False))
        return 0
    if args.command == "import-structured-document":
        if args.kind not in _STRUCTURED_KINDS:
            print(json.dumps({"status": "failed", "error_code": "invalid_kind"}, ensure_ascii=False))
            return 4
        try:
            _structured_case_source(args.case, args.source)
            payload = _read_structured_json(args.input)
            target = (
                persist_financial_statement(args.case, args.source, payload)
                if args.kind == "financial-statement"
                else persist_tax_return(args.case, args.source, payload)
            )
        except StructuredDocumentImportError as error:
            print(json.dumps({"status": "failed", "error_code": error.code}, ensure_ascii=False))
            return 4
        except ValueError as error:
            code = str(error) if str(error) in {"invalid_kind", "invalid_source", "invalid_structured_document"} else "invalid_structured_document"
            print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False))
            return 4
        except (OSError, TypeError):
            print(json.dumps({"status": "failed", "error_code": "write_failed"}, ensure_ascii=False))
            return 3
        print(json.dumps({
            "status": "completed", "kind": args.kind, "source_id": args.source,
            "output_path": str(target),
        }, ensure_ascii=False))
        return 0
    if args.command == "validate-extraction":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            value = document_extraction_from_dict(payload)
        except Exception:
            print(json.dumps({"valid": False, "error_code": "invalid_document_extraction"}, ensure_ascii=False))
            return 2
        print(json.dumps({"valid": True, "schema_version": value.schema_version}, ensure_ascii=False))
        return 0
    if args.command == "import-extraction":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            inventory = json.loads((args.case / "work" / "workbook_inventory.json").read_text(encoding="utf-8"))
            source = next(item for item in inventory.get("sources", []) if item.get("source_id") == args.source)
            extension = Path(source["filename"]).suffix.casefold()
            media_type = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(extension, "application/octet-stream")
            local = LocalDocumentSource(args.source, args.case / "input" / source["filename"], media_type, args.case / "input")
            value = HostProvider().import_result(local, payload)
            target = args.case / "work" / "document-extractions" / f"{args.source}.json"
            _write_json_atomic(target, document_extraction_to_dict(value))
        except Exception:
            print(json.dumps({"status": "failed", "error_code": "invalid_document_extraction_import"}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": value.status, "path": str(target)}, ensure_ascii=False))
        return 0
    if args.command == "prepare-ocr":
        try:
            manifest = prepare_ocr_models(args.model_dir)
        except OcrDependencyError as error:
            print(json.dumps({"status": "failed", "error_code": str(error)}, ensure_ascii=False))
            return 5
        payload = asdict(manifest) if is_dataclass(manifest) else manifest
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "extract-document":
        try:
            inventory = json.loads((args.case / "work" / "workbook_inventory.json").read_text(encoding="utf-8"))
            source = next(item for item in inventory.get("sources", []) if item.get("source_id") == args.source)
            filename = Path(source["filename"])
            if filename.is_absolute() or ".." in filename.parts:
                raise ValueError("unsafe_source_path")
            extension = filename.suffix.casefold()
            media_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(extension)
            if media_type is None:
                raise ValueError("unsupported_document_media_type")
            local = LocalDocumentSource(
                args.source, args.case / "input" / filename, media_type, args.case / "input"
            )
            if not _safe_input_path(local.path, local.case_input_dir):
                raise ValueError("unsafe_source_path")
            value = LocalOcrProvider().extract(local, ExtractionOptions(model_dir=args.model_dir))
            target = args.case / "work" / "document-extractions" / f"{args.source}.json"
            _write_json_atomic(target, document_extraction_to_dict(value))
        except OcrDependencyError as error:
            print(json.dumps({"status": "failed", "error_code": str(error)}, ensure_ascii=False))
            return 5
        except (KeyError, StopIteration, OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            code = str(error) if str(error) in {"unsafe_source_path", "unsupported_document_media_type"} else "invalid_document_source"
            print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False))
            return 4
        except Exception:
            print(json.dumps({"status": "failed", "error_code": "local_ocr_failed"}, ensure_ascii=False))
            return 3
        print(json.dumps({"status": value.status, "path": str(target)}, ensure_ascii=False))
        if value.status == "not_performed":
            code = next((issue.code for issue in value.issues if issue.code in {"local_ocr_dependency_missing", "local_ocr_model_unavailable"}), "local_ocr_unavailable")
            return 5 if code in {"local_ocr_dependency_missing", "local_ocr_model_unavailable"} else 3
        return 0 if value.status in {"completed", "partial"} else 3
    if args.command == "tax-health-check":
        has_manifest = args.evidence_manifest is not None
        has_root = args.evidence_root is not None
        if has_manifest != has_root or (args.include_evidence_files and not has_manifest):
            print(json.dumps({
                "status": "failed",
                "error_code": "invalid_evidence_manifest",
            }, ensure_ascii=False))
            return 4
        try:
            request_payload = json.loads(args.request.read_text(encoding="utf-8"))
            request = tax_request_from_dict(request_payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            print(json.dumps({"status": "failed", "error_code": "invalid_tax_health_request"}, ensure_ascii=False))
            return 4
        verified_evidence = None
        if has_manifest:
            try:
                manifest_payload = json.loads(
                    args.evidence_manifest.read_text(encoding="utf-8")
                )
                manifest = parse_evidence_manifest(manifest_payload)
                verified_evidence = verify_evidence_manifest(
                    manifest,
                    args.evidence_root,
                )
            except EvidenceBundleError as error:
                code = (
                    error.code
                    if error.code in _EVIDENCE_ERROR_CODES
                    and error.code != "evidence_bundle_write_failed"
                    else "invalid_evidence_manifest"
                )
                print(json.dumps({
                    "status": "failed",
                    "error_code": code,
                }, ensure_ascii=False))
                return 4
            except (OSError, UnicodeError, json.JSONDecodeError):
                print(json.dumps({
                    "status": "failed",
                    "error_code": "invalid_evidence_manifest",
                }, ensure_ascii=False))
                return 4
        try:
            validate_request_confirmation_evidence(request, verified_evidence)
            explicit_packs = None
            if args.pack or args.include_parameterized_packs:
                if args.pack and any(not _safe_pack_path(path) for path in args.pack):
                    raise TaxPackError("unsafe_tax_rule_pack_path")
                explicit_packs = tuple(
                    validate_pack_payload(json.loads(path.read_text(encoding="utf-8")))
                    for path in (args.pack or ())
                ) + (
                    load_builtin_parameterized_calculation_packs()
                    if args.include_parameterized_packs
                    else ()
                )
            snapshot = run_tax_health_check(
                request,
                packs=explicit_packs,
                evaluators=builtin_tax_evaluators() if explicit_packs is not None else None,
            )
            snapshot_path = write_tax_health_snapshot(snapshot, args.output_dir / "tax-health-snapshot.json")
            pdf_path = write_tax_health_report_pdf(
                snapshot,
                args.output_dir / "税务健康检查报告.pdf",
            )
            bundle = None
            if verified_evidence is not None:
                artifacts = [
                    BundleArtifact(
                        "pdf_report",
                        pdf_path,
                        "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u62a5\u544a.pdf",
                    ),
                    BundleArtifact(
                        "machine_snapshot",
                        snapshot_path,
                        "tax-health-snapshot.json",
                    ),
                ]
                bundle = build_evidence_bundle(
                    snapshot,
                    verified_evidence,
                    tuple(artifacts),
                    args.output_dir / "\u7a0e\u52a1\u5065\u5eb7\u68c0\u67e5\u8bc1\u636e\u5305.zip",
                    include_evidence_files=args.include_evidence_files,
                )
        except EvidenceBundleError as error:
            code = (
                error.code
                if error.code in _EVIDENCE_ERROR_CODES
                else "evidence_bundle_write_failed"
            )
            print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False))
            return 3 if code == "evidence_bundle_write_failed" else 4
        except PdfExportUnavailable as error:
            print(json.dumps({"status": "failed", "error_code": str(error)}, ensure_ascii=False))
            return 3
        except ValueError as error:
            code = (
                "unverified_confirmation_evidence"
                if str(error) == "unverified_confirmation_evidence"
                else "invalid_tax_rule_pack"
            )
            print(json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False))
            return 4
        except (OSError, UnicodeError, json.JSONDecodeError, TaxPackError, TypeError):
            print(json.dumps({"status": "failed", "error_code": "invalid_tax_rule_pack"}, ensure_ascii=False))
            return 4
        print(json.dumps({
            "status": "completed",
            "revision": snapshot.revision,
            "report_schema_version": REPORT_SCHEMA_VERSION_V3,
            "snapshot_path": str(snapshot_path),
            "pdf_path": str(pdf_path),
            **evidence_bundle_delivery_payload(bundle),
        }, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
