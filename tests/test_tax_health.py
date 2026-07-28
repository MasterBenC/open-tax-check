"""Public façade tests for model-independent tax-health checks."""

from datetime import date
import json
import unittest

from open_tax_check.tax_health import continue_tax_health_check, run_tax_health_check
from open_tax_check.tax_models import (
    Confirmation,
    DateRange,
    EvaluationOutcome,
    FactRequirement,
    PackMaturity,
    RuleSource,
    TaxCheckRequest,
    TaxPack,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_reporting import render_tax_health_report, tax_snapshot_payload


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
OCCURRED_AT = "2026-07-28T10:00:00+08:00"
SOURCE = RuleSource("国家税务总局", "https://www.chinatax.gov.cn/example", date(2026, 1, 1))


def vat_pack() -> TaxPack:
    return TaxPack(
        "vat-cn-2026", "vat", "CN", PERIOD, PackMaturity.SCREENING,
        (FactRequirement("entity.vat_registration", "choice", ("营业执照",), 90, True, True),),
        (), "vat.screening", (SOURCE,),
    )


def calculated(context: object) -> EvaluationOutcome:
    return EvaluationOutcome(TaxStatus.CALCULATED, ("vat_complete",), ("source-001",))


def payload_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(payload_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(payload_keys(item) for item in value))
    return set()


class TaxHealthFacadeTests(unittest.TestCase):
    def test_default_facade_returns_all_taxes_with_prototype_excluded(self) -> None:
        snapshot = run_tax_health_check(
            TaxCheckRequest(PERIOD, "CN"), occurred_at=OCCURRED_AT
        )

        self.assertEqual([result.tax_id for result in snapshot.results], [item.tax_id for item in TAX_DEFINITIONS])
        vat = next(result for result in snapshot.results if result.tax_id == "vat")
        self.assertEqual(vat.status, TaxStatus.BLOCKED)
        self.assertEqual(vat.diagnostics, ("rule_pack_unavailable",))
        payload = tax_snapshot_payload(snapshot)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIsInstance(payload, dict)
        self.assertIn("rule_pack_unavailable", serialized)
        self.assertTrue(payload_keys(payload).isdisjoint({"model", "token", "prompt", "hidden_reasoning"}))

    def test_continue_facade_applies_confirmation_and_updates_report(self) -> None:
        request = TaxCheckRequest(PERIOD, "CN")
        initial = run_tax_health_check(
            request, packs=(vat_pack(),), evaluators={"vat.screening": calculated}, occurred_at=OCCURRED_AT
        )
        question = next(item for item in initial.questions if item.fact_key == "entity.vat_registration")
        confirmation = Confirmation(
            question.question_id, question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00"
        )

        updated = continue_tax_health_check(
            initial, (confirmation,), packs=(vat_pack(),), evaluators={"vat.screening": calculated}
        )

        self.assertEqual(updated.revision, 2)
        self.assertEqual(next(result for result in updated.results if result.tax_id == "vat").status, TaxStatus.CALCULATED)
        self.assertIn("修订版本: 2", render_tax_health_report(updated))


if __name__ == "__main__":
    unittest.main()
