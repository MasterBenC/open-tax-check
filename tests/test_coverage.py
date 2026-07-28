import unittest

from open_tax_check.coverage import (
    PROCEDURE_DEPENDENCIES,
    evaluate_procedure_coverage,
)


def baseline_evidence(*, journal=False, trial_balance=False):
    return {
        role: "present_parsed"
        for role, present in {
            "journal": journal,
            "trial_balance": trial_balance,
        }.items()
        if present
    }


def status_of(coverage, procedure):
    return next(item.status for item in coverage if item.procedure == procedure)


class ProcedureCoverageTests(unittest.TestCase):
    def test_trial_balance_failure_does_not_block_journal_only_screen(self):
        coverage = evaluate_procedure_coverage(
            evidence=baseline_evidence(journal=True, trial_balance=True),
            role_issues={"trial_balance": ["identity_mismatch"]},
            dependencies={
                "journal_semantic_screen": {"journal"},
                "journal_tb_reconciliation": {"journal", "trial_balance"},
            },
        )

        self.assertEqual(status_of(coverage, "journal_semantic_screen"), "performed")
        self.assertEqual(status_of(coverage, "journal_tb_reconciliation"), "blocked")

    def test_statement_present_but_unparsed_is_not_performed(self):
        coverage = evaluate_procedure_coverage(
            evidence={"balance_sheet": "present_unparsed"}, role_issues={}
        )

        self.assertEqual(
            status_of(coverage, "financial_statement_reconciliation"),
            "present_unparsed",
        )

    def test_missing_role_precedes_unparsed_and_blocked_dependencies(self):
        coverage = evaluate_procedure_coverage(
            evidence={"journal": "present_unparsed", "trial_balance": "missing"},
            role_issues={"journal": ["invalid_date"]},
            dependencies={"journal_tb_reconciliation": {"journal", "trial_balance"}},
        )

        item = coverage[0]
        self.assertEqual(item.status, "not_performed_missing_evidence")
        self.assertEqual(item.affected_roles, ("trial_balance",))

    def test_unparsed_role_precedes_blocking_issue(self):
        coverage = evaluate_procedure_coverage(
            evidence={"journal": "present_unparsed"},
            role_issues={"journal": ["invalid_date"]},
            dependencies={"journal_semantic_screen": {"journal"}},
        )

        item = coverage[0]
        self.assertEqual(item.status, "present_unparsed")
        self.assertEqual(item.affected_roles, ("journal",))

    def test_empty_dependency_set_is_not_applicable(self):
        coverage = evaluate_procedure_coverage(
            evidence={}, role_issues={}, dependencies={"reserved_procedure": frozenset()}
        )

        item = coverage[0]
        self.assertEqual(item.status, "not_applicable")
        self.assertEqual(item.affected_roles, ())

    def test_default_coverage_exposes_only_non_assurance_statuses(self):
        coverage = evaluate_procedure_coverage(evidence={}, role_issues={})

        statuses = {item.status for item in coverage}
        self.assertTrue(statuses <= {
            "performed",
            "blocked",
            "not_performed_missing_evidence",
            "present_unparsed",
            "not_applicable",
        })
        self.assertNotIn("no_risk", statuses)
        self.assertNotIn("clear", statuses)
        self.assertEqual(
            {item.procedure for item in coverage}, set(PROCEDURE_DEPENDENCIES)
        )


if __name__ == "__main__":
    unittest.main()
