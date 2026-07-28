"""Behavioral tests for customer confirmation workflow snapshots."""

from datetime import date
import hashlib
import json
import unittest

from open_tax_check.schemas import to_dict
from open_tax_check.tax_models import (
    Confirmation,
    DateRange,
    EvaluationOutcome,
    FactRequirement,
    FactState,
    PackMaturity,
    RuleSource,
    TaxCheckRequest,
    TaxPack,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_workflow import (
    ConfirmationError,
    apply_confirmations,
    start_tax_health_check,
)


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
OCCURRED_AT = "2026-07-28T10:00:00+08:00"
SOURCE = RuleSource("Official source", "https://example.test/source", date(2026, 1, 1))


def tax_pack(tax_id: str, fact_key: str, evaluator_id: str) -> TaxPack:
    return TaxPack(
        pack_id=f"{tax_id}-cn-2026",
        tax_id=tax_id,
        jurisdiction="CN",
        effective_period=PERIOD,
        maturity=PackMaturity.SCREENING,
        requirements=(FactRequirement(fact_key, "choice", ("record",), 90, True, True),),
        local_parameter_keys=(),
        evaluator_id=evaluator_id,
        sources=(SOURCE,),
    )


class TaxWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.packs = (
            tax_pack("vat", "entity.vat_registration", "vat.screening"),
            tax_pack("enterprise_income_tax", "entity.eit_status", "eit.screening"),
        )
        self.request = TaxCheckRequest(
            PERIOD,
            "CN",
            input_hashes=("sha256:source-a",),
        )

    def test_start_creates_registry_ordered_snapshot_with_private_safe_audit(self) -> None:
        snapshot = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators={},
            occurred_at=OCCURRED_AT,
        )

        self.assertEqual([item.tax_id for item in snapshot.results], [item.tax_id for item in TAX_DEFINITIONS])
        self.assertEqual(len({item.tax_id for item in snapshot.results}), 20)
        self.assertEqual({item.fact_key for item in snapshot.questions}, {"entity.vat_registration", "entity.eit_status"})
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.audit_events[0].event_type, "check_started")
        self.assertEqual(snapshot.audit_events[0].occurred_at, OCCURRED_AT)
        serialized = json.dumps(to_dict(snapshot), ensure_ascii=False)
        self.assertIn("sha256:source-a", serialized)
        self.assertNotIn("customer/source/path.xlsx", serialized)
        self.assertNotIn("customer-source-contents", serialized)

    def test_confirmation_reruns_only_affected_tax_and_audits_digest(self) -> None:
        calls = {"vat": 0, "eit": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, ("vat_complete",), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, ("eit_complete",), ("source-002",))

        initial = start_tax_health_check(
            self.request, packs=self.packs,
            evaluators={"vat.screening": vat_evaluator, "eit.screening": eit_evaluator},
            occurred_at=OCCURRED_AT,
        )
        vat_question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        old_eit_result = next(result for result in initial.results if result.tax_id == "enterprise_income_tax")
        confirmation = Confirmation(
            vat_question.question_id, vat_question.fact_key, "general", "customer",
            "2026-07-28T10:05:00+08:00",
        )

        updated = apply_confirmations(
            initial, (confirmation,), packs=self.packs,
            evaluators={"vat.screening": vat_evaluator, "eit.screening": eit_evaluator},
        )

        self.assertEqual(calls, {"vat": 1, "eit": 0})
        self.assertEqual(next(result for result in updated.results if result.tax_id == "enterprise_income_tax"), old_eit_result)
        self.assertEqual(updated.request.facts[0].state, FactState.CONFIRMED)
        self.assertNotIn(vat_question.question_id, {question.question_id for question in updated.questions})
        self.assertEqual(updated.revision, 2)
        digest = hashlib.sha256(b'"general"').hexdigest()
        event = updated.audit_events[-1]
        self.assertEqual(event.fact_keys, ("entity.vat_registration",))
        self.assertEqual(event.confirmation_digest, digest)
        self.assertNotIn("general", json.dumps(to_dict(event), ensure_ascii=False))

    def test_invalid_confirmation_batch_does_not_apply_any_fact(self) -> None:
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={}, occurred_at=OCCURRED_AT)
        vat_question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        valid = Confirmation(vat_question.question_id, vat_question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        invalid = Confirmation("missing-question", "entity.eit_status", "yes", "customer", "2026-07-28T10:05:00+08:00")

        with self.assertRaisesRegex(ConfirmationError, "^unknown_question$"):
            apply_confirmations(initial, (valid, invalid), packs=self.packs, evaluators={})

        self.assertEqual(initial.request, self.request)

    def test_rejects_unknown_question_and_mismatched_fact_key(self) -> None:
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={}, occurred_at=OCCURRED_AT)
        question = initial.questions[0]
        unknown = Confirmation("missing-question", question.fact_key, "yes", "customer", "2026-07-28T10:05:00+08:00")
        mismatch = Confirmation(question.question_id, "other.fact", "yes", "customer", "2026-07-28T10:05:00+08:00")

        with self.assertRaisesRegex(ConfirmationError, "^unknown_question$"):
            apply_confirmations(initial, (unknown,), packs=self.packs, evaluators={})
        with self.assertRaisesRegex(ConfirmationError, "^confirmation_fact_mismatch$"):
            apply_confirmations(initial, (mismatch,), packs=self.packs, evaluators={})

    def test_confirmation_constructor_rejects_naive_timestamp_before_workflow_mutation(self) -> None:
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={}, occurred_at=OCCURRED_AT)
        question = initial.questions[0]

        with self.assertRaisesRegex(ValueError, "^invalid_timestamp$"):
            Confirmation(question.question_id, question.fact_key, "yes", "customer", "2026-07-28T10:05:00")
        self.assertEqual(initial.revision, 1)

    def test_repeated_identical_confirmation_is_idempotent(self) -> None:
        calls = {"vat": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={"vat.screening": vat_evaluator}, occurred_at=OCCURRED_AT)
        question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        confirmation = Confirmation(question.question_id, question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        confirmed = apply_confirmations(initial, (confirmation,), packs=self.packs, evaluators={"vat.screening": vat_evaluator})

        repeated = apply_confirmations(confirmed, (confirmation,), packs=self.packs, evaluators={"vat.screening": vat_evaluator})

        self.assertEqual(calls["vat"], 1)
        self.assertEqual(repeated, confirmed)

    def test_conflicting_confirmation_preserves_fact_and_retains_corrective_question(self) -> None:
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={}, occurred_at=OCCURRED_AT)
        question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        first = Confirmation(question.question_id, question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        confirmed = apply_confirmations(initial, (first,), packs=self.packs, evaluators={})
        conflict = Confirmation(question.question_id, question.fact_key, "small_scale", "customer", "2026-07-28T10:06:00+08:00")

        updated = apply_confirmations(confirmed, (conflict,), packs=self.packs, evaluators={})

        self.assertEqual(updated.request.facts[0].value, "general")
        vat_result = next(result for result in updated.results if result.tax_id == "vat")
        self.assertEqual(vat_result.status, TaxStatus.BLOCKED)
        self.assertEqual(vat_result.diagnostics, ("confirmation_conflict",))
        corrective = next(item for item in updated.questions if item.fact_key == "entity.vat_registration")
        self.assertIn("confirmation_conflict", corrective.reason_codes)
        self.assertEqual(corrective.answer_type, question.answer_type)
        self.assertEqual(corrective.evidence_types, question.evidence_types)
        self.assertEqual(corrective.priority, question.priority)
        self.assertEqual(corrective.blocking, question.blocking)
        self.assertEqual(updated.audit_events[-1].event_type, "confirmation_conflict")
        self.assertEqual(updated.revision, 3)

    def test_conflict_uses_stored_question_after_packs_change_and_replay_is_idempotent(self) -> None:
        calls = {"vat": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators={"vat.screening": vat_evaluator},
            occurred_at=OCCURRED_AT,
        )
        question = next(item for item in initial.questions if item.fact_key == "entity.vat_registration")
        first = Confirmation(question.question_id, question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        confirmed = apply_confirmations(
            initial, (first,), packs=self.packs, evaluators={"vat.screening": vat_evaluator}
        )
        conflict = Confirmation(question.question_id, question.fact_key, "small_scale", "customer", "2026-07-28T10:06:00+08:00")

        conflicted = apply_confirmations(
            confirmed, (conflict,), packs=(), evaluators={"vat.screening": vat_evaluator}
        )
        corrective = next(item for item in conflicted.questions if item.fact_key == question.fact_key)
        replayed = apply_confirmations(
            conflicted, (conflict,), packs=(), evaluators={"vat.screening": vat_evaluator}
        )

        self.assertEqual(corrective.answer_type, question.answer_type)
        self.assertEqual(corrective.evidence_types, question.evidence_types)
        self.assertEqual(corrective.priority, question.priority)
        self.assertEqual(corrective.blocking, question.blocking)
        self.assertEqual(calls, {"vat": 1})
        self.assertEqual(replayed, conflicted)
        self.assertEqual(len(replayed.audit_events), len(conflicted.audit_events))
        self.assertEqual(replayed.revision, conflicted.revision)


if __name__ == "__main__":
    unittest.main()
