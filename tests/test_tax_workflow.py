"""Behavioral tests for customer confirmation workflow snapshots."""

from dataclasses import replace
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
    TaxFact,
    TaxPack,
    TaxStatus,
)
from open_tax_check.tax_registry import TAX_DEFINITIONS
from open_tax_check.tax_workflow import (
    ConfirmationError,
    _confirmation_value_is_valid,
    apply_confirmations,
    start_tax_health_check,
)


PERIOD = DateRange(date(2026, 1, 1), date(2026, 1, 31))
OCCURRED_AT = "2026-07-28T10:00:00+08:00"
SOURCE = RuleSource("Official source", "https://example.test/source", date(2026, 1, 1))
SOURCE_HASH = "a" * 64


def tax_pack(
    tax_id: str,
    fact_key: str,
    evaluator_id: str,
    *,
    answer_type: str = "choice",
) -> TaxPack:
    return TaxPack(
        pack_id=f"{tax_id}-cn-2026",
        tax_id=tax_id,
        jurisdiction="CN",
        effective_period=PERIOD,
        maturity=PackMaturity.CALCULATION,
        requirements=(
            FactRequirement(fact_key, answer_type, ("record",), 90, True, True),
            FactRequirement("activity.screening_applies", "boolean", (), 1, False, False),
        ),
        local_parameter_keys=(),
        evaluator_id=evaluator_id,
        sources=(SOURCE,),
        rule_version="2026.1",
        fact_catalog_version="zh-CN.v1",
        applicability_fact_keys=("activity.screening_applies",),
    )


class TaxWorkflowTests(unittest.TestCase):
    def test_choice_confirmation_requires_declared_machine_value(self) -> None:
        self.assertTrue(_confirmation_value_is_valid("choice", "yes", ("yes", "no")))
        self.assertFalse(_confirmation_value_is_valid("choice", "maybe", ("yes", "no")))

    def setUp(self) -> None:
        self.packs = (
            tax_pack("vat", "entity.vat_registration", "vat.screening"),
            tax_pack("enterprise_income_tax", "entity.eit_status", "eit.screening"),
        )
        self.request = TaxCheckRequest(
            PERIOD,
            "CN",
            facts=(TaxFact("activity.screening_applies", True, FactState.OBSERVED, ("source-001",)),),
            input_hashes=(SOURCE_HASH,),
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
        self.assertIn(SOURCE_HASH, serialized)
        self.assertNotIn("customer/source/path.xlsx", serialized)
        self.assertNotIn("customer-source-contents", serialized)

    def test_confirmation_reruns_only_affected_tax_and_audits_digest(self) -> None:
        calls = {"vat": 0, "eit": 0}
        prompts = {}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, ("vat_complete",), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, ("eit_complete",), ("source-002",))

        initial = start_tax_health_check(
            self.request, packs=self.packs,
            evaluators={"vat.screening": vat_evaluator, "eit.screening": eit_evaluator},
            prompts=prompts,
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
            prompts=prompts,
        )

        self.assertEqual(calls, {"vat": 1, "eit": 0})
        self.assertEqual(next(result for result in updated.results if result.tax_id == "enterprise_income_tax"), old_eit_result)
        confirmed_fact = next(fact for fact in updated.request.facts if fact.key == vat_question.fact_key)
        self.assertEqual(confirmed_fact.state, FactState.CONFIRMED)
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

    def test_confirmation_values_match_each_supported_question_answer_type(self) -> None:
        cases = (
            ("text", "customer explanation"),
            ("choice", "general"),
            ("integer", 3),
            ("boolean", False),
            ("date", "2026-02-28"),
            ("decimal", "-1200.50"),
        )

        for answer_type, value in cases:
            with self.subTest(answer_type=answer_type):
                pack = tax_pack(
                    "vat",
                    f"entity.{answer_type}",
                    "vat.screening",
                    answer_type=answer_type,
                )
                initial = start_tax_health_check(
                    self.request,
                    packs=(pack,),
                    evaluators={},
                    occurred_at=OCCURRED_AT,
                )
                question = initial.questions[0]
                confirmation = Confirmation(
                    question.question_id,
                    question.fact_key,
                    value,
                    "customer",
                    "2026-07-28T10:05:00+08:00",
                )

                updated = apply_confirmations(
                    initial,
                    (confirmation,),
                    packs=(pack,),
                    evaluators={},
                )

                fact = next(
                    item
                    for item in updated.request.facts
                    if item.key == question.fact_key
                )
                self.assertEqual(fact.value, value)
                self.assertEqual(updated.revision, 2)

    def test_confirmation_rejects_values_that_mismatch_question_answer_type(self) -> None:
        cases = (
            ("text", ""),
            ("choice", 1),
            ("integer", True),
            ("boolean", "true"),
            ("date", "2026-02-30"),
            ("date", "20260228"),
            ("decimal", "not-a-decimal"),
            ("decimal", ""),
        )

        for answer_type, value in cases:
            with self.subTest(answer_type=answer_type, value=value):
                pack = tax_pack(
                    "vat",
                    f"entity.{answer_type}",
                    "vat.screening",
                    answer_type=answer_type,
                )
                initial = start_tax_health_check(
                    self.request,
                    packs=(pack,),
                    evaluators={},
                    occurred_at=OCCURRED_AT,
                )
                question = initial.questions[0]
                confirmation = Confirmation(
                    question.question_id,
                    question.fact_key,
                    value,
                    "customer",
                    "2026-07-28T10:05:00+08:00",
                )

                with self.assertRaisesRegex(
                    ConfirmationError,
                    "^invalid_confirmation_value$",
                ):
                    apply_confirmations(
                        initial,
                        (confirmation,),
                        packs=(pack,),
                        evaluators={},
                    )

                self.assertEqual(initial.revision, 1)

    def test_confirmation_rejects_unknown_question_answer_type(self) -> None:
        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators={},
            occurred_at=OCCURRED_AT,
        )
        original = initial.questions[0]
        unsupported = replace(original, answer_type="currency")
        hostile_snapshot = replace(
            initial,
            questions=(unsupported,),
            question_history=(unsupported,),
        )
        confirmation = Confirmation(
            unsupported.question_id,
            unsupported.fact_key,
            "12.00",
            "customer",
            "2026-07-28T10:05:00+08:00",
        )

        with self.assertRaisesRegex(
            ConfirmationError,
            "^invalid_confirmation_value$",
        ):
            apply_confirmations(
                hostile_snapshot,
                (confirmation,),
                packs=self.packs,
                evaluators={},
            )

    def test_invalid_confirmation_value_keeps_entire_batch_atomic(self) -> None:
        calls = {"vat": 0, "eit": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-002",))

        evaluators = {
            "vat.screening": vat_evaluator,
            "eit.screening": eit_evaluator,
        }
        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators=evaluators,
            occurred_at=OCCURRED_AT,
        )
        vat_question = next(
            item
            for item in initial.questions
            if item.fact_key == "entity.vat_registration"
        )
        eit_question = next(
            item
            for item in initial.questions
            if item.fact_key == "entity.eit_status"
        )
        valid = Confirmation(
            vat_question.question_id,
            vat_question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
        )
        invalid = Confirmation(
            eit_question.question_id,
            eit_question.fact_key,
            1,
            "customer",
            "2026-07-28T10:05:00+08:00",
        )

        with self.assertRaisesRegex(
            ConfirmationError,
            "^invalid_confirmation_value$",
        ):
            apply_confirmations(
                initial,
                (valid, invalid),
                packs=self.packs,
                evaluators=evaluators,
            )

        self.assertEqual(calls, {"vat": 0, "eit": 0})
        self.assertEqual(
            initial.request.facts,
            (TaxFact("activity.screening_applies", True, FactState.OBSERVED, ("source-001",)),),
        )
        self.assertEqual(initial.revision, 1)
        self.assertEqual(len(initial.audit_events), 1)

    def test_repeated_identical_confirmation_is_idempotent(self) -> None:
        calls = {"vat": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={"vat.screening": vat_evaluator}, occurred_at=OCCURRED_AT)
        question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        confirmation = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
            ("source-old",),
        )
        confirmed = apply_confirmations(initial, (confirmation,), packs=self.packs, evaluators={"vat.screening": vat_evaluator})

        repeated = apply_confirmations(confirmed, (confirmation,), packs=self.packs, evaluators={"vat.screening": vat_evaluator})

        self.assertEqual(calls["vat"], 1)
        self.assertEqual(repeated, confirmed)

    def test_same_value_with_supplemental_evidence_updates_fact_and_reruns(self) -> None:
        calls = {"vat": 0, "eit": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-002",))

        evaluators = {
            "vat.screening": vat_evaluator,
            "eit.screening": eit_evaluator,
        }
        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators=evaluators,
            occurred_at=OCCURRED_AT,
        )
        question = next(
            item
            for item in initial.questions
            if item.fact_key == "entity.vat_registration"
        )
        first = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
            ("source-old",),
        )
        confirmed = apply_confirmations(
            initial,
            (first,),
            packs=self.packs,
            evaluators=evaluators,
        )
        supplemental = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
            ("source-new",),
        )

        updated = apply_confirmations(
            confirmed,
            (supplemental,),
            packs=self.packs,
            evaluators=evaluators,
        )

        fact = next(
            item
            for item in updated.request.facts
            if item.key == question.fact_key
        )
        self.assertEqual(fact.evidence_refs, ("source-old", "source-new"))
        self.assertEqual(fact.confirmed_by, "customer")
        self.assertEqual(fact.confirmed_at, "2026-07-28T10:05:00+08:00")
        self.assertEqual(calls, {"vat": 2, "eit": 0})
        self.assertEqual(updated.audit_events[-1].event_type, "confirmation_updated")
        self.assertEqual(updated.revision, confirmed.revision + 1)

    def test_same_value_with_new_confirmation_metadata_updates_and_reruns(self) -> None:
        calls = {"vat": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        evaluators = {"vat.screening": vat_evaluator}
        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators=evaluators,
            occurred_at=OCCURRED_AT,
        )
        question = next(
            item
            for item in initial.questions
            if item.fact_key == "entity.vat_registration"
        )
        first = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
            ("source-old",),
        )
        confirmed = apply_confirmations(
            initial,
            (first,),
            packs=self.packs,
            evaluators=evaluators,
        )
        metadata_update = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "finance-director",
            "2026-07-28T10:07:00+08:00",
            ("source-old",),
        )

        updated = apply_confirmations(
            confirmed,
            (metadata_update,),
            packs=self.packs,
            evaluators=evaluators,
        )

        fact = next(
            item
            for item in updated.request.facts
            if item.key == question.fact_key
        )
        self.assertEqual(fact.evidence_refs, ("source-old",))
        self.assertEqual(fact.confirmed_by, "finance-director")
        self.assertEqual(fact.confirmed_at, "2026-07-28T10:07:00+08:00")
        self.assertEqual(calls, {"vat": 2})
        self.assertEqual(updated.audit_events[-1].event_type, "confirmation_updated")
        self.assertEqual(updated.revision, confirmed.revision + 1)

    def test_conflicting_confirmation_preserves_fact_and_retains_corrective_question(self) -> None:
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators={}, occurred_at=OCCURRED_AT)
        question = next(question for question in initial.questions if question.fact_key == "entity.vat_registration")
        first = Confirmation(question.question_id, question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        confirmed = apply_confirmations(initial, (first,), packs=self.packs, evaluators={})
        conflict = Confirmation(question.question_id, question.fact_key, "small_scale", "customer", "2026-07-28T10:06:00+08:00")

        updated = apply_confirmations(confirmed, (conflict,), packs=self.packs, evaluators={})

        confirmed_fact = next(fact for fact in updated.request.facts if fact.key == question.fact_key)
        self.assertEqual(confirmed_fact.value, "general")
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

    def test_preserved_value_reaffirms_active_conflict_and_clears_question(self) -> None:
        calls = {"vat": 0, "eit": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-002",))

        evaluators = {
            "vat.screening": vat_evaluator,
            "eit.screening": eit_evaluator,
        }
        initial = start_tax_health_check(
            self.request,
            packs=self.packs,
            evaluators=evaluators,
            occurred_at=OCCURRED_AT,
        )
        question = next(
            item
            for item in initial.questions
            if item.fact_key == "entity.vat_registration"
        )
        first = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "customer",
            "2026-07-28T10:05:00+08:00",
            ("source-old",),
        )
        confirmed = apply_confirmations(
            initial,
            (first,),
            packs=self.packs,
            evaluators=evaluators,
        )
        conflict = Confirmation(
            question.question_id,
            question.fact_key,
            "small_scale",
            "customer",
            "2026-07-28T10:06:00+08:00",
            ("source-rejected",),
        )
        conflicted = apply_confirmations(
            confirmed,
            (conflict,),
            packs=self.packs,
            evaluators=evaluators,
        )
        reaffirmation = Confirmation(
            question.question_id,
            question.fact_key,
            "general",
            "finance-director",
            "2026-07-28T10:07:00+08:00",
            ("source-new",),
        )

        reaffirmed = apply_confirmations(
            conflicted,
            (reaffirmation,),
            packs=self.packs,
            evaluators=evaluators,
        )

        fact = next(
            item
            for item in reaffirmed.request.facts
            if item.key == question.fact_key
        )
        self.assertEqual(fact.value, "general")
        self.assertEqual(fact.evidence_refs, ("source-old", "source-new"))
        self.assertEqual(fact.confirmed_by, "finance-director")
        self.assertEqual(fact.confirmed_at, "2026-07-28T10:07:00+08:00")
        self.assertEqual(calls, {"vat": 2, "eit": 0})
        vat_result = next(
            item for item in reaffirmed.results if item.tax_id == "vat"
        )
        self.assertEqual(vat_result.status, TaxStatus.CALCULATED)
        self.assertNotIn("confirmation_conflict", vat_result.diagnostics)
        self.assertNotIn(
            question.question_id,
            {item.question_id for item in reaffirmed.questions},
        )
        self.assertEqual(
            reaffirmed.audit_events[-1].event_type,
            "confirmation_reaffirmed",
        )
        self.assertEqual(reaffirmed.revision, conflicted.revision + 1)

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

    def test_replayed_conflict_in_a_mixed_batch_does_not_discard_new_confirmation(self) -> None:
        calls = {"vat": 0, "eit": 0}

        def vat_evaluator(context: object) -> EvaluationOutcome:
            calls["vat"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-001",))

        def eit_evaluator(context: object) -> EvaluationOutcome:
            calls["eit"] += 1
            return EvaluationOutcome(TaxStatus.CALCULATED, (), ("source-002",))

        evaluators = {"vat.screening": vat_evaluator, "eit.screening": eit_evaluator}
        initial = start_tax_health_check(self.request, packs=self.packs, evaluators=evaluators, occurred_at=OCCURRED_AT)
        vat_question = next(item for item in initial.questions if item.fact_key == "entity.vat_registration")
        eit_question = next(item for item in initial.questions if item.fact_key == "entity.eit_status")
        first = Confirmation(vat_question.question_id, vat_question.fact_key, "general", "customer", "2026-07-28T10:05:00+08:00")
        confirmed = apply_confirmations(initial, (first,), packs=self.packs, evaluators=evaluators)
        conflict = Confirmation(vat_question.question_id, vat_question.fact_key, "small_scale", "customer", "2026-07-28T10:06:00+08:00")
        conflicted = apply_confirmations(confirmed, (conflict,), packs=self.packs, evaluators=evaluators)
        eit_confirmation = Confirmation(eit_question.question_id, eit_question.fact_key, "standard", "customer", "2026-07-28T10:07:00+08:00")

        updated = apply_confirmations(
            conflicted, (conflict, eit_confirmation), packs=self.packs, evaluators=evaluators
        )

        self.assertEqual(calls, {"vat": 1, "eit": 1})
        self.assertEqual(updated.revision, conflicted.revision + 1)
        self.assertEqual(len(updated.audit_events), len(conflicted.audit_events) + 1)
        self.assertEqual(updated.audit_events[-1].fact_keys, ("entity.eit_status",))
        self.assertEqual(next(result for result in updated.results if result.tax_id == "vat").status, TaxStatus.BLOCKED)
        self.assertEqual(next(result for result in updated.results if result.tax_id == "enterprise_income_tax").status, TaxStatus.CALCULATED)


if __name__ == "__main__":
    unittest.main()
