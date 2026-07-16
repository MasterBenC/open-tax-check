import unittest
from copy import deepcopy

from open_tax_check.reporting import select_reportable_findings


class ReportingGateTests(unittest.TestCase):
    def test_only_reviewed_and_approved_reportable_statuses_enter_formal_report(self):
        findings = [
            {
                "finding_id": "F1",
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "approved",
            },
            {
                "finding_id": "F2",
                "status": "confirmed",
                "review_status": "reviewed",
                "report_status": "approved",
            },
            {
                "finding_id": "F3",
                "status": "insufficient_data",
                "review_status": "reviewed",
                "report_status": "approved",
            },
            {
                "finding_id": "F4",
                "status": "suspected",
                "review_status": "reviewed",
            },
            {
                "finding_id": "F5",
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "draft",
            },
            {
                "finding_id": "F6",
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "rejected",
            },
            {
                "finding_id": "F7",
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "other",
            },
            {
                "finding_id": "F8",
                "status": "suspected",
                "review_status": "unreviewed",
                "report_status": "approved",
            },
            {
                "finding_id": "F9",
                "status": "candidate",
                "review_status": "reviewed",
                "report_status": "approved",
            },
            {
                "finding_id": "F10",
                "status": "closed",
                "review_status": "reviewed",
                "report_status": "approved",
            },
        ]

        reportable = select_reportable_findings(findings)

        self.assertEqual(
            [item["finding_id"] for item in reportable], ["F1", "F2", "F3"]
        )

    def test_selecting_reportable_findings_does_not_mutate_input(self):
        findings = [
            {
                "finding_id": "F1",
                "status": "suspected",
                "review_status": "reviewed",
                "report_status": "approved",
                "evidence": {"source_rows": [1, 2]},
            },
            {
                "finding_id": "F2",
                "status": "candidate",
                "review_status": "unreviewed",
                "report_status": "draft",
            },
        ]
        original = deepcopy(findings)

        select_reportable_findings(findings)

        self.assertEqual(findings, original)


if __name__ == "__main__":
    unittest.main()
