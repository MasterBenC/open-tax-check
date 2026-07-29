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
    task_payload,
)

from .tax_reporting import (
    render_tax_health_report,
    tax_snapshot_payload,
    write_tax_health_report,
    write_tax_health_snapshot,
)
from .tax_io import tax_request_from_dict, tax_request_to_dict
