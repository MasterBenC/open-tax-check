"""OpenTaxCheck deterministic accounting and tax-health-check core."""

__version__ = "0.1.0"

from .document_extraction import (
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    LEGACY_DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    AuthorizationRecord,
    DocumentExtraction,
    DocumentPage,
    ExtractionIssue,
    TableCell,
    TableRegion,
    TextBlock,
    document_extraction_from_dict,
    document_extraction_to_dict,
)

from .agent_orchestration import (
    AgentQuestionTask,
    build_agent_question_tasks,
    confirmation_from_task,
    evidence_bundle_delivery_payload,
    task_payload,
)

from .case_orchestration import (
    CASE_SCHEMA_VERSION,
    TASKS_SCHEMA_VERSION,
    CaseStatus,
    apply_case_confirmations,
    build_case_customer_tasks,
    case_status_payload,
    load_case_snapshot,
    run_case_self_check,
)

from .evidence_bundle import (
    BundleArtifact,
    EvidenceBundleError,
    EvidenceBundleResult,
    EvidenceManifest,
    EvidenceManifestItem,
    VerifiedEvidenceItem,
    VerifiedEvidenceSet,
)

from .tax_reporting import (
    PdfExportUnavailable,
    render_tax_health_report,
    tax_snapshot_payload,
    write_tax_health_report,
    write_tax_health_report_docx,
    write_tax_health_report_pdf,
    write_tax_health_snapshot,
)
from .tax_io import tax_request_from_dict, tax_request_to_dict
from .tax_calculation import TaxCalculationRequest, TaxCalculationResult, calculate_tax_amount
from .tax_models import (
    CalculationRecord,
    CalculationSpec,
    StatutoryBracketEntry,
    StatutoryBracketTable,
    StatutoryRateRange,
)
from .tax_registry import load_builtin_parameterized_calculation_packs
from .evidence import EvidenceVerification, verify_evidence_file
from .source_paths import resolve_safe_source
from .structured_documents import (
    FinancialStatement,
    FinancialStatementLine,
    SourceAnchor,
    StructuredIssue,
    TaxReturn,
    TaxReturnLine,
    financial_statement_from_dict,
    financial_statement_to_dict,
    tax_return_from_dict,
    tax_return_to_dict,
)
from .structured_document_providers import (
    StructuredDocumentImportError,
    StructuredDocumentSource,
    import_financial_statement,
    import_tax_return,
)
from .template_documents import (
    TemplateIssue,
    TemplateParseResult,
    financial_statement_template,
    tax_return_template,
)
from .structured_reconciliation import (
    DocumentReconciliationIssue,
    DocumentReconciliationResult,
    reconcile_structured_documents,
)
