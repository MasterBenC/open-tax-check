"""Professional-review gate for formal reports."""

from __future__ import annotations


REPORTABLE_STATUSES = {"suspected", "confirmed", "insufficient_data"}


def select_reportable_findings(findings: list[dict]) -> list[dict]:
    return [
        finding
        for finding in findings
        if finding.get("review_status") == "reviewed"
        and finding.get("status") in REPORTABLE_STATUSES
    ]

