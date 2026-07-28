"""Procedure-level evidence coverage without assurance conclusions."""

from __future__ import annotations

from dataclasses import dataclass


PROCEDURE_DEPENDENCIES = {
    "journal_quality": frozenset({"journal"}),
    "trial_balance_quality": frozenset({"trial_balance"}),
    "journal_tb_reconciliation": frozenset({"journal", "trial_balance"}),
    "journal_hard_rules": frozenset({"journal"}),
    "journal_semantic_screen": frozenset({"journal"}),
    "financial_statement_reconciliation": frozenset(
        {"balance_sheet", "income_statement"}
    ),
    "vat_return_reconciliation": frozenset({"vat_return"}),
    "cit_return_reconciliation": frozenset({"cit_return"}),
}


@dataclass(frozen=True, slots=True)
class ProcedureCoverage:
    """A procedure's execution state and roles responsible for that state."""

    procedure: str
    required_roles: tuple[str, ...]
    status: str
    affected_roles: tuple[str, ...]


def procedure_status(
    required_roles, evidence: dict[str, str], role_issues: dict[str, list[str]]
) -> tuple[str, tuple[str, ...]]:
    """Return the non-assurance state caused by this procedure's dependencies."""

    roles = tuple(required_roles)
    if not roles:
        return "not_applicable", ()
    missing = tuple(sorted(role for role in roles if evidence.get(role) == "missing"))
    if missing:
        return "not_performed_missing_evidence", missing
    unparsed = tuple(
        sorted(role for role in roles if evidence.get(role) == "present_unparsed")
    )
    if unparsed:
        return "present_unparsed", unparsed
    blocked = tuple(sorted(role for role in roles if role_issues.get(role)))
    return ("blocked", blocked) if blocked else ("performed", ())


def evaluate_procedure_coverage(
    evidence: dict[str, str],
    role_issues: dict[str, list[str]],
    dependencies: dict[str, frozenset[str]] = PROCEDURE_DEPENDENCIES,
) -> tuple[ProcedureCoverage, ...]:
    """Evaluate each procedure independently so unrelated procedures remain usable."""

    coverage = []
    for procedure, required_roles in dependencies.items():
        status, affected_roles = procedure_status(required_roles, evidence, role_issues)
        coverage.append(
            ProcedureCoverage(
                procedure=procedure,
                required_roles=tuple(sorted(required_roles)),
                status=status,
                affected_roles=affected_roles,
            )
        )
    return tuple(coverage)
