"""Public façade tests for model-independent tax-health checks."""

from datetime import date
import json
import unittest
from unittest.mock import patch

from open_tax_check.tax_health import continue_tax_health_check, run_tax_health_check
from open_tax_check.tax_models import (
    AuditEvent,
    Confirmation,
    DateRange,
    EvaluationOutcome,
    FactRequirement,
    FactState,
    PackMaturity,
    Question,
    RuleSource,
    TaxCheckRequest,
    TaxCheckSnapshot,
    TaxFact,
    TaxPack,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_reporting import render_tax_health_report, tax_snapshot_payload
from open_tax_check.tax_screening import GENERIC_SCREENING_EVALUATOR_ID


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
OCCURRED_AT = "2026-07-28T10:00:00+08:00"
SOURCE = RuleSource("国家税务总局", "https://www.chinatax.gov.cn/example", date(2026, 1, 1))


def vat_pack() -> TaxPack:
    return TaxPack(
        "vat-cn-2026", "vat", "CN", PERIOD, PackMaturity.CALCULATION,
        (
            FactRequirement("entity.vat_registration", "choice", ("营业执照",), 90, True, True),
            FactRequirement("activity.screening_applies", "boolean", (), 1, False, False),
        ),
        (), "vat.screening", (SOURCE,),
        "2026.1", "zh-CN.v1", ("activity.screening_applies",),
    )


def calculated(context: object) -> EvaluationOutcome:
    return EvaluationOutcome(TaxStatus.CALCULATED, ("vat_complete",), ("source-001",))


def generic_vat_pack() -> TaxPack:
    requirement = FactRequirement(
        "activity.has_vat_taxable_transaction",
        "boolean",
        ("customer_confirmation",),
        100,
        True,
        True,
    )
    return TaxPack(
        "vat-cn-2026-screening-v1",
        "vat",
        "CN",
        PERIOD,
        PackMaturity.SCREENING,
        (requirement,),
        (),
        GENERIC_SCREENING_EVALUATOR_ID,
        (SOURCE,),
        "2026.1",
        "zh-CN.v1",
        (requirement.fact_key,),
    )


def payload_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(payload_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(payload_keys(item) for item in value))
    return set()


class TaxHealthFacadeTests(unittest.TestCase):
    def test_invalid_builtin_fact_catalog_has_stable_customer_safe_error(self) -> None:
        with patch(
            "open_tax_check.tax_health.load_builtin_fact_catalog",
            side_effect=ValueError("unexpected details"),
        ):
            with self.assertRaisesRegex(ValueError, "^invalid_fact_catalog$"):
                run_tax_health_check(TaxCheckRequest(PERIOD, "CN"), occurred_at=OCCURRED_AT)

    def test_custom_generic_pack_without_evaluator_mapping_remains_unavailable(self) -> None:
        request = TaxCheckRequest(
            PERIOD,
            "CN",
            (
                TaxFact(
                    "activity.has_vat_taxable_transaction",
                    False,
                    FactState.CONFIRMED,
                    confirmed_by="customer",
                    confirmed_at=OCCURRED_AT,
                ),
            ),
        )

        snapshot = run_tax_health_check(
            request,
            packs=(generic_vat_pack(),),
            occurred_at=OCCURRED_AT,
        )

        vat = next(result for result in snapshot.results if result.tax_id == "vat")
        self.assertEqual(vat.status, TaxStatus.BLOCKED)
        self.assertEqual(vat.diagnostics, ("rule_evaluator_unavailable",))

    def test_explicit_empty_evaluator_mapping_does_not_inject_builtin_generic(self) -> None:
        request = TaxCheckRequest(
            PERIOD,
            "CN",
            (
                TaxFact(
                    "activity.has_vat_taxable_transaction",
                    False,
                    FactState.CONFIRMED,
                    confirmed_by="customer",
                    confirmed_at=OCCURRED_AT,
                ),
            ),
        )

        snapshot = run_tax_health_check(
            request,
            packs=(generic_vat_pack(),),
            evaluators={},
            occurred_at=OCCURRED_AT,
        )

        vat = next(result for result in snapshot.results if result.tax_id == "vat")
        self.assertEqual(vat.status, TaxStatus.BLOCKED)
        self.assertEqual(vat.diagnostics, ("rule_evaluator_unavailable",))

    def test_payload_excludes_confirmation_and_question_history_but_preserves_active_safety_metadata(self) -> None:
        active_question = Question(
            "active-question", "entity.active_fact", ("vat",), ("missing_fact",),
            "choice", ("record",), 90, True,
        )
        historical_question = Question(
            "history-question", "entity.historical_fact", ("vat",), ("missing_fact",),
            "choice", ("record",), 80, True,
        )
        snapshot = TaxCheckSnapshot(
            request=TaxCheckRequest(
                PERIOD,
                "CN",
                facts=(TaxFact(
                    "entity.confirmed_fact", "hostile-confirmation-value", FactState.CONFIRMED,
                    confirmed_by="hostile-confirmed-by",
                    confirmed_at="2026-07-28T10:05:00+08:00",
                ),),
                input_hashes=("a" * 64,),
            ),
            results=(),
            questions=(active_question,),
            audit_events=(AuditEvent(
                "fact_confirmed", "2026-07-28T10:05:00+08:00",
                confirmation_digest="safe-confirmation-digest",
            ),),
            revision=2,
            question_history=(historical_question,),
        )

        payload = tax_snapshot_payload(snapshot)
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(set(payload), {"schema_version", "revision", "request", "results", "questions", "audit_events"})
        self.assertEqual(set(payload["request"]), {"period", "jurisdiction", "input_hashes"})
        self.assertEqual(payload["request"]["input_hashes"], ["a" * 64])
        self.assertEqual(
            set(payload["audit_events"][0]),
            {"event_type", "tax_ids", "fact_keys", "confirmation_digest"},
        )
        self.assertIn("active-question", serialized)
        self.assertIn("safe-confirmation-digest", serialized)
        for prohibited in (
            "facts", "question_history", "hostile-confirmation-value",
            "hostile-confirmed-by", "2026-07-28T10:05:00+08:00", "history-question", "occurred_at",
        ):
            self.assertNotIn(prohibited, payload_keys(payload))
            self.assertNotIn(prohibited, serialized)

    def test_default_facade_returns_all_taxes_with_prototype_excluded(self) -> None:
        snapshot = run_tax_health_check(
            TaxCheckRequest(PERIOD, "CN"), occurred_at=OCCURRED_AT
        )

        self.assertEqual([result.tax_id for result in snapshot.results], [item.tax_id for item in TAX_DEFINITIONS])
        vat = next(result for result in snapshot.results if result.tax_id == "vat")
        self.assertEqual(vat.status, TaxStatus.NEEDS_EVIDENCE)
        self.assertEqual(vat.diagnostics, ())
        payload = tax_snapshot_payload(snapshot)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIsInstance(payload, dict)
        self.assertIn("status_label_zh", payload["results"][0])
        self.assertIsInstance(payload["results"][0]["status_label_zh"], str)
        self.assertTrue(payload_keys(payload).isdisjoint({"model", "token", "prompt", "hidden_reasoning"}))

    def test_continue_facade_applies_confirmation_and_updates_report(self) -> None:
        request = TaxCheckRequest(
            PERIOD, "CN", (TaxFact("activity.screening_applies", True, FactState.OBSERVED, ("source-001",)),)
        )
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
