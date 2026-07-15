import unittest

from open_tax_check.reporting import select_reportable_findings


class ReportingGateTests(unittest.TestCase):
    def test_only_reviewed_findings_enter_formal_report(self):
        findings = [
            {"finding_id": "F1", "status": "candidate", "review_status": "unreviewed"},
            {"finding_id": "F2", "status": "suspected", "review_status": "reviewed"},
            {"finding_id": "F3", "status": "closed", "review_status": "reviewed"},
        ]

        reportable = select_reportable_findings(findings)

        self.assertEqual([item["finding_id"] for item in reportable], ["F2"])


if __name__ == "__main__":
    unittest.main()
