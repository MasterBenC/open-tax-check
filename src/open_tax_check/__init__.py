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
