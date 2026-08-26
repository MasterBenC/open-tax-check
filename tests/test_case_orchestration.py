from datetime import date
import hashlib
import json
import queue
import threading
import unittest
from unittest.mock import patch

from open_tax_check.case import init_case
from open_tax_check import cli
import open_tax_check.case_orchestration as case_orchestration
from open_tax_check.host_contract import validate_customer_handoff
from open_tax_check.case_orchestration import (
    CaseStatus,
    apply_case_confirmations,
    build_case_customer_handoff,
    build_case_customer_tasks,
    case_status_payload,
    load_case_snapshot,
    run_case_self_check,
    confirm_cross_check_explanation,
)
from open_tax_check.tax_cross_checks import (
    CrossCheckContext,
    CrossCheckInputBinding,
    CrossCheckStatus,
    run_tax_cross_checks,
    tax_cross_check_payload,
)
from test_tax_cross_checks import difference_run, statement, tax_return
from open_tax_check.structured_document_providers import (
    persist_financial_statement,
    persist_tax_return,
)
from open_tax_check.structured_documents import (
    financial_statement_to_dict,
    tax_return_to_dict,
)
from open_tax_check.tax_models import Confirmation, DateRange, FactState, TaxCheckRequest, TaxFact


OCCURRED_AT = "2026-07-30T10:00:00+08:00"


def _request() -> TaxCheckRequest:
    return TaxCheckRequest(
        DateRange(date(2026, 1, 1), date(2026, 12, 31)),
        "CN",
        facts=(TaxFact("activity.has_vat_taxable_transaction", True, FactState.OBSERVED, ("source-001",)),),
        input_hashes=("a" * 64,),
    )


def _bind_source(case_dir, source_id="source-001", filename="source.pdf", content=b"trusted source"):
    source_path = case_dir / "input" / filename
    source_path.write_bytes(content)
    manifest = {
        "schema_version": "excel-intake/v1",
        "sources": [{
            "schema_version": "excel-intake/v1",
            "source_id": source_id,
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "extension": source_path.suffix,
            "status": "present_unparsed",
        }],
        "issues": [],
    }
    (case_dir / "work" / "input_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_cross_check_documents(case_dir, *, quarterly_sales="275.00"):
    from dataclasses import replace

    source_id = "source-1"

    def bind(document):
        lines = tuple(
            replace(
                line,
                source_anchor=replace(
                    line.source_anchor,
                    source_id=source_id,
                    page_number=None,
                    sheet_name="Sheet1",
                    cell="A1",
                ),
            )
            for line in document.lines
        )
        return replace(
            document,
            source_id=source_id,
            entity_name="\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8",
            lines=lines,
        )

    financial = bind(statement())
    persist_financial_statement(
        case_dir, source_id, financial_statement_to_dict(financial)
    )
    for index, (period_start, period_end) in enumerate(
        (
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"),
            ("2025-10-01", "2025-12-31"),
        ),
        start=1,
    ):
        vat = bind(
            tax_return(
                "vat",
                {"vat.taxable_sales": quarterly_sales},
                return_id=f"vat-q{index}",
                period_start=period_start,
                period_end=period_end,
            )
        )
        persist_tax_return(case_dir, source_id, tax_return_to_dict(vat))


def _with_input_binding(case_dir, run):
    from open_tax_check.pipeline import _cross_check_input_binding

    return __import__("dataclasses").replace(
        run, input_binding=_cross_check_input_binding(case_dir / "work")
    )


class CaseOrchestrationTests(unittest.TestCase):
  @staticmethod
  def _scenario_confirmation(task, **overrides):
    payload = {
      "confirmation_kind": "scenario",
      "question_id": task["question_id"],
      "fact_key": task["fact_key"],
      "value": True,
      "confirmed_by": "customer",
      "confirmed_at": "2026-08-26T18:00:00+08:00",
      "evidence_refs": [f"customer-confirmation:{task['question_id']}"],
      "scenario_revision": task["scenario_revision"],
      "question_digest": task["question_digest"],
    }
    payload.update(overrides)
    return payload

  def test_case_tasks_uses_remaining_queue_for_bound_scenario_questions(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")

      tasks = build_case_customer_tasks(case_dir)

      self.assertEqual(len(tasks), 3)
      self.assertTrue(all(isinstance(item, dict) for item in tasks))
      self.assertEqual(
        [item["fact_key"] for item in tasks],
        [
          "has_real_estate_activity",
          "has_taxable_contracts",
          "has_employees_or_shareholder_flows",
        ],
      )
      for item in tasks:
        self.assertEqual(item["confirmation_kind"], "scenario")
        self.assertEqual(item["answer_type"], "boolean")
        self.assertEqual(item["scenario_revision"], 0)
        self.assertRegex(item["question_digest"], r"^[0-9a-f]{64}$")

  def test_scenario_confirmation_batch_is_atomic_revisioned_and_idempotent(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      scenario_tasks = build_case_customer_tasks(case_dir)
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      pointer = case_dir / "work" / "delivery.json"
      pointer.write_text('{"revision": 1}', encoding="utf-8")
      batch = tuple(self._scenario_confirmation(task) for task in scenario_tasks[:2])
      apply_confirmations = getattr(
        case_orchestration, "apply_scenario_confirmations", None
      )
      self.assertTrue(callable(apply_confirmations))

      first = apply_confirmations(case_dir, batch)
      replay = apply_confirmations(case_dir, batch)

      facts = json.loads(
        (case_dir / "work/scenario-facts.json").read_text(encoding="utf-8")
      )
      snapshot = load_case_snapshot(case_dir)
      self.assertEqual(first["revision"], 2)
      self.assertEqual(replay["revision"], 2)
      self.assertEqual(facts["schema_version"], "tax-scenario-facts/v2")
      self.assertEqual(facts["revision"], 1)
      self.assertEqual(set(facts["facts"]), {item["fact_key"] for item in batch})
      self.assertEqual(snapshot.revision, 2)
      events = [
        event for event in snapshot.audit_events
        if event.event_type == "scenario_confirmation"
      ]
      self.assertEqual(len(events), 1)
      self.assertEqual(set(events[0].fact_keys), {item["fact_key"] for item in batch})
      self.assertFalse(pointer.exists())

  def test_concurrent_scenario_confirmations_do_not_lose_an_update(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      tasks = build_case_customer_tasks(case_dir)
      first_payload = self._scenario_confirmation(tasks[0])
      second_payload = self._scenario_confirmation(tasks[1])
      first_at_write = threading.Event()
      release_first_write = threading.Event()
      second_started = threading.Barrier(2)
      outcomes = queue.Queue()
      original_write = case_orchestration._write_json

      def controlled_write(path, payload):
        if (
          path.name == "scenario-facts.json"
          and first_payload["fact_key"] in payload["facts"]
        ):
          first_at_write.set()
          if not release_first_write.wait(timeout=5):
            raise AssertionError("first scenario confirmation was not released")
        original_write(path, payload)

      def apply(payload, *, wait_for_start=False):
        if wait_for_start:
          second_started.wait(timeout=5)
        try:
          result = case_orchestration.apply_scenario_confirmations(case_dir, (payload,))
          outcomes.put(("ok", payload["fact_key"], result))
        except Exception as error:
          outcomes.put(("error", payload["fact_key"], str(error)))

      with patch.object(case_orchestration, "_write_json", side_effect=controlled_write):
        first = threading.Thread(target=apply, args=(first_payload,))
        first.start()
        self.assertTrue(first_at_write.wait(timeout=5))

        second = threading.Thread(
          target=apply,
          args=(second_payload,),
          kwargs={"wait_for_start": True},
        )
        second.start()
        second_started.wait(timeout=5)
        second.join(timeout=5)
        self.assertFalse(second.is_alive())
        release_first_write.set()
        first.join(timeout=5)
        self.assertFalse(first.is_alive())

      observed = [outcomes.get_nowait(), outcomes.get_nowait()]
      successes = [item for item in observed if item[0] == "ok"]
      errors = [item for item in observed if item[0] == "error"]
      facts = json.loads(
        (case_dir / "work/scenario-facts.json").read_text(encoding="utf-8")
      )

      self.assertEqual(len(successes), 1)
      self.assertEqual(len(errors), 1)
      self.assertIn(
        errors[0][2],
        {"scenario_confirmation_locked", "scenario_confirmation_stale"},
      )
      self.assertEqual(facts["revision"], 1)
      self.assertEqual(set(facts["facts"]), {successes[0][1]})
      self.assertFalse((case_dir / ".scenario-confirmation.lock").exists())

  def test_scenario_confirmation_removes_lock_when_token_write_fails(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]

      with patch.object(case_orchestration.os, "write", side_effect=OSError("disk full")):
        with self.assertRaisesRegex(OSError, "disk full"):
          case_orchestration.apply_scenario_confirmations(
            case_dir, (self._scenario_confirmation(task),)
          )

      self.assertFalse((case_dir / ".scenario-confirmation.lock").exists())

  def test_scenario_confirmation_does_not_remove_lock_with_replaced_token(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]
      lock_path = case_dir / ".scenario-confirmation.lock"

      def replace_lock_token(*_args):
        lock_path.write_text("replacement-owner-token", encoding="ascii")
        raise ValueError("forced_failure")

      with patch.object(
        case_orchestration,
        "_apply_scenario_confirmations_locked",
        side_effect=replace_lock_token,
      ):
        with self.assertRaisesRegex(ValueError, "forced_failure"):
          case_orchestration.apply_scenario_confirmations(
            case_dir, (self._scenario_confirmation(task),)
          )

      self.assertEqual(
        lock_path.read_text(encoding="ascii"),
        "replacement-owner-token",
      )

  def _invoke_scenario_cli(self, case_dir, input_path, payload):
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with patch.object(
      cli.sys,
      "argv",
      ["opentaxcheck", "case-confirm", "--case", str(case_dir), "--input", str(input_path)],
    ), patch("builtins.print") as print_output:
      code = cli.main()
    return code, json.loads(print_output.call_args.args[0])

  def test_cli_applies_all_scenario_confirmation_batch(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(task)],
      )

      self.assertEqual(code, 0)
      self.assertEqual(output["status"], "scenario_confirmed")
      self.assertEqual(output["scenario_revision"], 1)

  def test_cli_rejects_non_human_scenario_confirmers(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]

      for confirmed_by in ("ai", "agent", "model", "host", "host:codex", "", "human:"):
        with self.subTest(confirmed_by=confirmed_by):
          code, output = self._invoke_scenario_cli(
            case_dir,
            root / "scenario-confirmations.json",
            [self._scenario_confirmation(task, confirmed_by=confirmed_by)],
          )
          self.assertEqual(code, 4)
          self.assertEqual(output["error_code"], "invalid_scenario_confirmer")

  def test_cli_rejects_scenario_confirmation_without_evidence(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(task, evidence_refs=[])],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "confirmation_evidence_required")

  def test_cli_rejects_stale_scenario_revision(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      tasks = build_case_customer_tasks(case_dir)
      case_orchestration.apply_scenario_confirmations(
        case_dir, (self._scenario_confirmation(tasks[0]),)
      )

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(tasks[1])],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "scenario_confirmation_stale")

  def test_cli_rejects_conflicting_scenario_confirmation(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]
      case_orchestration.apply_scenario_confirmations(
        case_dir, (self._scenario_confirmation(task),)
      )

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(task, value=False, scenario_revision=1)],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "scenario_confirmation_conflict")

  def test_cli_rejects_wrong_scenario_question_digest(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(task, question_digest="0" * 64)],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "scenario_question_digest_mismatch")

  def test_cli_exposes_retryable_scenario_confirmation_lock(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      task = build_case_customer_tasks(case_dir)[0]
      (case_dir / ".scenario-confirmation.lock").write_text("active", encoding="ascii")

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "scenario-confirmations.json",
        [self._scenario_confirmation(task)],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "scenario_confirmation_locked")

  def test_cli_rejects_mixed_standard_and_scenario_confirmation_batch(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      scenario_task = build_case_customer_tasks(case_dir)[0]
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      standard_task = next(
        item for item in build_case_customer_tasks(case_dir)
        if not isinstance(item, dict)
      )
      standard = {
        "question_id": standard_task.question_id,
        "fact_key": standard_task.fact_key,
        "value": self._answer_for(standard_task),
        "confirmed_by": "customer",
        "confirmed_at": "2026-08-26T18:00:00+08:00",
        "evidence_refs": [f"customer-confirmation:{standard_task.question_id}"],
      }

      code, output = self._invoke_scenario_cli(
        case_dir,
        root / "mixed-confirmations.json",
        [standard, self._scenario_confirmation(scenario_task)],
      )

      self.assertEqual(code, 4)
      self.assertEqual(output["error_code"], "mixed_confirmation_batch")

  def test_cross_check_confirmation_is_incremental_append_only_and_idempotent(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      _write_cross_check_documents(case_dir)
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work" / "input_manifest.json").read_bytes()
      ).hexdigest()
      run_case_self_check(
        case_dir,
        TaxCheckRequest(
          DateRange(date(2025, 1, 1), date(2025, 12, 31)),
          "CN",
          input_hashes=(manifest_sha256,),
        ),
        occurred_at="2026-08-05T09:00:00+08:00",
      )
      run = __import__("dataclasses").replace(
        difference_run(), source_manifest_sha256=manifest_sha256
      )
      run = _with_input_binding(case_dir, run)
      (case_dir / "work" / "tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run), ensure_ascii=False), encoding="utf-8"
      )
      delivery_pointer = case_dir / "work" / "delivery.json"
      delivery_pointer.write_text('{"revision": 1}', encoding="utf-8")
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": manifest_sha256,
        "category": "timing_difference",
        "explanation_zh": "该差异为已核验的申报与账务确认期间差异。",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [{
          "amount": "100.00",
          "label_zh": "申报确认期间差异",
          "evidence_refs": ["source-1"],
        }],
      }

      revised = confirm_cross_check_explanation(case_dir, payload)
      repeated = confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(revised.results[1].resolution_status, "closed")
      self.assertEqual(revised.audit_events[:len(run.audit_events)], run.audit_events)
      self.assertEqual(repeated, revised)
      self.assertEqual(len(repeated.audit_events), len(revised.audit_events))
      self.assertFalse(delivery_pointer.exists())
      self.assertEqual(load_case_snapshot(case_dir).revision, 2)
      self.assertEqual(
        load_case_snapshot(case_dir).audit_events[-1].event_type,
        "cross_check_confirmation",
      )

      conflicting = {**payload, "explanation_zh": "另一项互相冲突的客户说明。"}
      with self.assertRaisesRegex(ValueError, "cross_check_confirmation_conflict"):
        confirm_cross_check_explanation(case_dir, conflicting)

  def test_cross_check_confirmation_rejects_stale_manifest_binding_without_writing(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      run = difference_run()
      cross_path = case_dir / "work" / "tax-cross-checks.json"
      original = json.dumps(tax_cross_check_payload(run), ensure_ascii=False)
      cross_path.write_text(original, encoding="utf-8")
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": "a" * 64,
        "category": "timing_difference",
        "explanation_zh": "该差异为客户确认的期间差异。",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [],
      }

      with self.assertRaisesRegex(ValueError, "cross_check_confirmation_binding_mismatch"):
        confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(cross_path.read_text(encoding="utf-8"), original)

  def test_cross_check_confirmation_rejects_changed_original_rerun_inputs(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      _write_cross_check_documents(case_dir)
      journal = case_dir / "work/normalized_journal.jsonl"
      journal.write_text('{"account_code":"6001"}\n', encoding="utf-8")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      structured = {
        path.relative_to(case_dir / "work").as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((case_dir / "work/structured-documents").rglob("*"))
        if path.is_file()
      }
      run = __import__("dataclasses").replace(
        difference_run(),
        source_manifest_sha256=manifest_sha256,
      )
      run = _with_input_binding(case_dir, run)
      cross_path = case_dir / "work/tax-cross-checks.json"
      original = json.dumps(tax_cross_check_payload(run), ensure_ascii=False)
      cross_path.write_text(original, encoding="utf-8")
      journal.write_text('{"account_code":"6002"}\n', encoding="utf-8")
      (case_dir / "work/normalized_journal.sha256").write_text(
        hashlib.sha256(journal.read_bytes()).hexdigest(), encoding="utf-8"
      )
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": manifest_sha256,
        "category": "timing_difference",
        "explanation_zh": "该差异已经人工核对确认。",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [],
      }

      with self.assertRaisesRegex(ValueError, "cross_check_rerun_inputs_invalid"):
        confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(cross_path.read_text(encoding="utf-8"), original)
      journal.write_text('{"account_code":"6001"}\n', encoding="utf-8")
      (case_dir / "work/normalized_journal.sha256").write_text(
        hashlib.sha256(journal.read_bytes()).hexdigest(), encoding="utf-8"
      )
      first_structured_path = case_dir / "work" / next(iter(structured))
      first_structured_path.unlink()

      with self.assertRaisesRegex(ValueError, "cross_check_rerun_inputs_invalid"):
        confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(cross_path.read_text(encoding="utf-8"), original)

  def test_cross_check_confirmation_requires_input_binding_and_preserves_file(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = __import__("dataclasses").replace(
        difference_run(), source_manifest_sha256=manifest_sha256
      )
      cross_path = case_dir / "work/tax-cross-checks.json"
      original = json.dumps(tax_cross_check_payload(run), ensure_ascii=False)
      cross_path.write_text(original, encoding="utf-8")
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": manifest_sha256,
        "category": "timing_difference",
        "explanation_zh": "该差异已经人工核对确认。",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [],
      }

      with self.assertRaisesRegex(ValueError, "cross_check_rerun_inputs_invalid"):
        confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(cross_path.read_text(encoding="utf-8"), original)

  def test_cross_check_confirmation_rejects_journal_added_after_absent_binding(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = __import__("dataclasses").replace(
        difference_run(),
        source_manifest_sha256=manifest_sha256,
        input_binding=CrossCheckInputBinding(None, ()),
      )
      cross_path = case_dir / "work/tax-cross-checks.json"
      original = json.dumps(tax_cross_check_payload(run), ensure_ascii=False)
      cross_path.write_text(original, encoding="utf-8")
      (case_dir / "work/normalized_journal.jsonl").write_text("{}\n", encoding="utf-8")
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": manifest_sha256,
        "category": "timing_difference",
        "explanation_zh": "该差异已经人工核对确认。",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [],
      }

      with self.assertRaisesRegex(ValueError, "cross_check_rerun_inputs_invalid"):
        confirm_cross_check_explanation(case_dir, payload)

      self.assertEqual(cross_path.read_text(encoding="utf-8"), original)

  def test_pending_text_explanation_can_be_completed_only_with_consistent_bridge(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      _write_cross_check_documents(case_dir)
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = __import__("dataclasses").replace(
        difference_run(), source_manifest_sha256=manifest_sha256
      )
      run = _with_input_binding(case_dir, run)
      (case_dir / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run)), encoding="utf-8"
      )
      payload = {
        "question_id": run.questions[0].question_id,
        "check_id": run.questions[0].check_id,
        "source_manifest_sha256": manifest_sha256,
        "category": "timing_difference",
        "explanation_zh": "\u5df2\u7531\u4eba\u5de5\u6838\u5bf9\u671f\u95f4\u5dee\u5f02\u3002",
        "confirmed_by": "human:Chen Li",
        "confirmed_at": "2026-08-05T10:00:00+08:00",
        "evidence_refs": ["source-1"],
        "bridge": [],
      }

      pending = confirm_cross_check_explanation(case_dir, payload)
      pending_question = next(
        item for item in pending.questions if item.check_id == payload["check_id"]
      )
      closed = confirm_cross_check_explanation(
        case_dir,
        {
          **payload,
          "question_id": pending_question.question_id,
          "bridge": [{
            "amount": "100.00",
            "label_zh": "\u7533\u62a5\u671f\u95f4\u5dee\u5f02",
            "evidence_refs": ["source-1"],
          }],
        },
      )

      self.assertEqual(pending.results[1].resolution_status, "explained_pending_verification")
      self.assertEqual(closed.results[1].resolution_status, "closed")
      with self.assertRaisesRegex(ValueError, "cross_check_confirmation_conflict"):
        confirm_cross_check_explanation(
          case_dir, {**payload, "explanation_zh": "\u51b2\u7a81\u7684\u65b0\u8bf4\u660e\u3002"}
        )

  def test_case_tasks_exposes_bounded_cross_check_questions_without_private_snapshot(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = run_tax_cross_checks(
        (),
        None,
        (),
        (),
        CrossCheckContext(
          "2025-01-01", "2025-12-31",
          "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "CNY",
        ),
        manifest_sha256,
      )
      (case_dir / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run)), encoding="utf-8"
      )

      tasks = build_case_customer_tasks(case_dir)

      self.assertLessEqual(len(tasks), 3)
      self.assertTrue(tasks)
      self.assertTrue(all(isinstance(item, dict) and "check_id" in item for item in tasks))

  def test_case_status_includes_cross_check_tasks_when_health_snapshot_has_none(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = run_tax_cross_checks(
        (), None, (), (),
        CrossCheckContext(
          "2025-01-01", "2025-12-31",
          "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "CNY",
        ),
        manifest_sha256,
      )
      (case_dir / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run)), encoding="utf-8"
      )

      status = case_status_payload(case_dir)

      self.assertEqual(status["status"], CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION.value)
      self.assertEqual(status["pending_task_count"], 3)
      self.assertTrue(status["required_evidence_types"])
      self.assertTrue(status["affected_tax_ids"])

  def test_missing_source_confirmation_reruns_affected_check_and_regenerates_queue(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "2025-01-01", "2025-12-31")
      _bind_source(case_dir, source_id="source-1")
      manifest_sha256 = hashlib.sha256(
        (case_dir / "work/input_manifest.json").read_bytes()
      ).hexdigest()
      run = run_tax_cross_checks(
        (), None, (), (),
        CrossCheckContext(
          "2025-01-01", "2025-12-31",
          "\u865a\u6784\u79d1\u6280\u6709\u9650\u516c\u53f8", "CNY",
        ),
        manifest_sha256,
      )
      target_question = next(
        item for item in run.questions if item.check_id == "FS-VAT-SALES-001"
      )
      _write_cross_check_documents(case_dir, quarterly_sales="250.00")
      run = _with_input_binding(case_dir, run)
      (case_dir / "work/tax-cross-checks.json").write_text(
        json.dumps(tax_cross_check_payload(run)), encoding="utf-8"
      )

      revised = confirm_cross_check_explanation(
        case_dir,
        {
          "question_id": target_question.question_id,
          "check_id": target_question.check_id,
          "source_manifest_sha256": manifest_sha256,
          "category": "amended_return",
          "explanation_zh": "\u5df2\u8865\u5145\u5b8c\u6574\u7533\u62a5\u8d44\u6599\u5e76\u7531\u4eba\u5de5\u786e\u8ba4\u3002",
          "confirmed_by": "human:Chen Li",
          "confirmed_at": "2026-08-05T10:00:00+08:00",
          "evidence_refs": ["source-1"],
          "bridge": [],
        },
      )

      target = next(item for item in revised.results if item.check_id == target_question.check_id)
      self.assertIs(target.status, CrossCheckStatus.MATCHED)
      self.assertNotIn(target_question.check_id, {item.check_id for item in revised.questions})
      self.assertEqual(revised.results[0], run.results[0])

  def test_package_facade_exposes_case_orchestration_contract(self):
    import open_tax_check

    self.assertIs(open_tax_check.run_case_self_check, run_case_self_check)
    self.assertIs(open_tax_check.apply_case_confirmations, apply_case_confirmations)
    self.assertIs(open_tax_check.case_status_payload, case_status_payload)

  @staticmethod
  def _answer_for(task):
    if task.answer_type == "boolean":
      return False
    if task.answer_type == "choice":
      return task.choices[0].value
    if task.answer_type == "integer":
      return 0
    if task.answer_type == "date":
      return "2026-01-01"
    if task.answer_type == "decimal":
      return "0.00"
    return "客户确认"

  def test_cli_exposes_resumable_self_check_and_status(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      request_path = root / "request.json"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      request_path.write_text(json.dumps({
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "jurisdiction": "CN",
        "facts": [],
        "input_hashes": [],
      }), encoding="utf-8")
      with patch.object(cli.sys, "argv", ["opentaxcheck", "case-self-check", "--case", str(case_dir), "--request", str(request_path)]):
        self.assertEqual(cli.main(), 0)
      with patch.object(cli.sys, "argv", ["opentaxcheck", "case-status", "--case", str(case_dir)]):
        self.assertEqual(cli.main(), 0)

  def test_first_case_run_persists_customer_tasks_and_waiting_status(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")

      payload = run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)

      self.assertEqual(payload["status"], CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION.value)
      self.assertEqual(payload["next_action"], "answer_customer_tasks")
      self.assertTrue(payload["status_label_zh"])
      self.assertTrue(payload["customer_message_zh"])
      self.assertTrue(payload["required_evidence_types"])
      self.assertTrue(payload["affected_tax_ids"])
      self.assertTrue((case_dir / "work" / "tax-health-snapshot.private.json").is_file())
      tasks_path = case_dir / "work" / "customer-tasks.json"
      self.assertTrue(tasks_path.is_file())
      tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
      self.assertEqual(tasks["schema_version"], "tax-customer-tasks/v1")
      self.assertTrue(tasks["tasks"])
      self.assertTrue(all("question_zh" in item for item in tasks["tasks"]))
      self.assertTrue(all(item["evidence_types"] for item in tasks["tasks"]))
      self.assertTrue(all(item["tax_ids"] for item in tasks["tasks"]))
      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_pending_case_handoff_never_claims_completion(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)

      payload = build_case_customer_handoff(case_dir)

      self.assertEqual(payload["title_zh"], "第一轮账务初筛已完成")
      self.assertEqual(payload["pending_task_count"], len(build_case_customer_tasks(case_dir)))
      self.assertIn("不代表应补税额、欠税金额或最终申报金额", payload["amount_disclaimer_zh"])
      self.assertIsNone(validate_customer_handoff(payload))
      self.assertTrue((case_dir / "output" / "税务健康检查交付说明.json").is_file())

  def test_explicit_limitations_are_part_of_the_single_persisted_snapshot_contract(self):
    from open_tax_check import tax_models

    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      limitation = tax_models.Limitation(
        "special_tax_path_not_covered",
        "零税率、免税及不动产特殊增值税路径未纳入本次确定性计算。",
      )

      run_case_self_check(
        case_dir,
        _request(),
        occurred_at=OCCURRED_AT,
        limitations=(limitation,),
      )

      private = json.loads(
        (case_dir / "work/tax-health-snapshot.private.json").read_text(encoding="utf-8")
      )
      machine = json.loads(
        (case_dir / "work/tax-health-snapshot.json").read_text(encoding="utf-8")
      )
      report = (case_dir / "output/税务健康检查报告.md").read_text(encoding="utf-8")
      self.assertEqual(private["limitations"], machine["limitations"])
      persisted_limitations = load_case_snapshot(case_dir).limitations
      self.assertIn(limitation, persisted_limitations)
      self.assertIn("pending_customer_evidence", {item.code for item in persisted_limitations})
      self.assertIn("unresolved_tax_scope", {item.code for item in persisted_limitations})
      self.assertIn(limitation.message_zh, report)

  def test_confirmation_updates_case_snapshot_revision_and_keeps_unrelated_results(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      _bind_source(case_dir)
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id,
        task.fact_key,
        self._answer_for(task),
        "customer",
        "2026-07-30T10:05:00+08:00",
        ("source-001",),
      )

      payload = apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(payload["revision"], 2)
      self.assertIn(payload["status"], {
        CaseStatus.COMPLETED_WITH_LIMITATIONS.value,
        CaseStatus.WAITING_FOR_CUSTOMER_CONFIRMATION.value,
      })
      self.assertEqual(json.loads((case_dir / "work" / "customer-tasks.json").read_text(encoding="utf-8"))["revision"], 2)
      self.assertEqual(load_case_snapshot(case_dir).revision, 2)

  def test_case_confirmation_accepts_matching_customer_statement_reference(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00",
        (f"customer-confirmation:{task.question_id}",),
      )

      payload = apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(payload["revision"], 2)

  def test_case_confirmation_rejects_unknown_manifest_source(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      _bind_source(case_dir)
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00", ("unknown-source",),
      )

      with self.assertRaisesRegex(ValueError, "^unverified_confirmation_evidence$"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_case_confirmation_rejects_changed_manifest_source_bytes(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      _bind_source(case_dir)
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      source_path = case_dir / "input" / "source.pdf"
      source_path.write_bytes(b"changed source")
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00", ("source-001",),
      )

      with self.assertRaisesRegex(ValueError, "^unverified_confirmation_evidence$"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_case_confirmation_rejects_mismatched_customer_statement_reference(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00",
        ("customer-confirmation:question-0000000000000000",),
      )

      with self.assertRaisesRegex(ValueError, "^unverified_confirmation_evidence$"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_case_confirmation_rejects_unsafe_manifest_source(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      outside = root / "outside.pdf"
      outside.write_bytes(b"outside")
      manifest = {
        "sources": [{
          "source_id": "source-001",
          "filename": "../outside.pdf",
          "sha256": hashlib.sha256(b"outside").hexdigest(),
          "status": "accepted",
        }],
      }
      (case_dir / "work" / "input_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
      )
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00", ("source-001",),
      )

      with self.assertRaisesRegex(ValueError, "^unverified_confirmation_evidence$"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_cli_applies_confirmation_batch_without_accepting_host_status(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      confirmation_path = root / "confirmation.json"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation_path.write_text(json.dumps([{
        "question_id": task.question_id,
        "fact_key": task.fact_key,
        "value": self._answer_for(task),
        "confirmed_by": "customer",
        "confirmed_at": "2026-07-30T10:05:00+08:00",
        "evidence_refs": ["source-001"],
        "status": "completed",
      }]), encoding="utf-8")
      with patch.object(cli.sys, "argv", ["opentaxcheck", "case-confirm", "--case", str(case_dir), "--input", str(confirmation_path)]):
        self.assertEqual(cli.main(), 4)
      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_cli_reports_unverified_confirmation_evidence(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      root = __import__("pathlib").Path(temp_dir)
      case_dir = root / "case"
      confirmation_path = root / "confirmation.json"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation_path.write_text(json.dumps([{
        "question_id": task.question_id,
        "fact_key": task.fact_key,
        "value": self._answer_for(task),
        "confirmed_by": "customer",
        "confirmed_at": "2026-07-30T10:05:00+08:00",
        "evidence_refs": ["unknown-source"],
      }]), encoding="utf-8")

      with patch.object(
        cli.sys, "argv",
        ["opentaxcheck", "case-confirm", "--case", str(case_dir), "--input", str(confirmation_path)],
      ), patch("builtins.print") as print_output:
        self.assertEqual(cli.main(), 4)

      self.assertEqual(
        json.loads(print_output.call_args.args[0]),
        {"status": "failed", "error_code": "unverified_confirmation_evidence"},
      )

  def test_private_snapshot_rejects_invalid_fact_entries_instead_of_dropping_them(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      private_path = case_dir / "work" / "tax-health-snapshot.private.json"
      payload = json.loads(private_path.read_text(encoding="utf-8"))
      payload["request"]["facts"].append("invalid")
      private_path.write_text(json.dumps(payload), encoding="utf-8")
      with self.assertRaisesRegex(ValueError, "invalid_case_snapshot"):
        load_case_snapshot(case_dir)

  def test_self_check_refuses_to_bypass_existing_data_quality_block(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      state_path = case_dir / "state.json"
      state = json.loads(state_path.read_text(encoding="utf-8"))
      state["status"] = "blocked_data_quality"
      state["next_action"] = "repair_source_data"
      state_path.write_text(json.dumps(state), encoding="utf-8")

      with self.assertRaisesRegex(ValueError, "blocked_data_quality"):
        run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)

      self.assertFalse((case_dir / "output" / "税务健康检查报告.md").exists())
      self.assertFalse((case_dir / "work" / "tax-health-snapshot.private.json").exists())

  def test_self_check_requires_request_period_to_match_case_scope(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2025-01-01", "2025-12-31")

      with self.assertRaisesRegex(ValueError, "case_period_mismatch"):
        run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)

      self.assertFalse((case_dir / "output" / "税务健康检查报告.md").exists())

  def test_confirmation_cannot_republish_after_data_quality_becomes_blocked(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      state_path = case_dir / "state.json"
      state = json.loads(state_path.read_text(encoding="utf-8"))
      state["status"] = "blocked_data_quality"
      state_path.write_text(json.dumps(state), encoding="utf-8")
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00", ("source-001",),
      )

      with self.assertRaisesRegex(ValueError, "blocked_data_quality"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_initial_case_request_rejects_host_supplied_confirmed_fact(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      request = TaxCheckRequest(
        DateRange(date(2026, 1, 1), date(2026, 12, 31)), "CN",
        facts=(TaxFact("activity.has_vat_taxable_transaction", True, FactState.CONFIRMED, ("source-001",), "host_ai", OCCURRED_AT),),
      )

      with self.assertRaisesRegex(ValueError, "initial_confirmed_fact_forbidden"):
        run_case_self_check(case_dir, request, occurred_at=OCCURRED_AT)

  def test_case_confirmation_requires_evidence_reference(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      task = build_case_customer_tasks(case_dir)[0]
      confirmation = Confirmation(
        task.question_id, task.fact_key, self._answer_for(task), "customer",
        "2026-07-30T10:05:00+08:00",
      )

      with self.assertRaisesRegex(ValueError, "confirmation_evidence_required"):
        apply_case_confirmations(case_dir, (confirmation,))

      self.assertEqual(load_case_snapshot(case_dir).revision, 1)

  def test_case_status_gives_later_data_quality_block_precedence_over_snapshot(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
      state_path = case_dir / "state.json"
      state = json.loads(state_path.read_text(encoding="utf-8"))
      state["status"] = "blocked_data_quality"
      state_path.write_text(json.dumps(state), encoding="utf-8")

      payload = case_status_payload(case_dir)

      self.assertEqual(payload["status"], CaseStatus.BLOCKED_DATA_QUALITY.value)
      self.assertEqual(payload["next_action"], "repair_source_data")

  def test_private_snapshot_rejects_malformed_choices_and_audit_events(self):
    for field, mutation in (
      ("questions", lambda payload: payload["questions"][0].update({"choices": ["invalid"]})),
      ("audit_events", lambda payload: payload["audit_events"].append("invalid")),
    ):
      with self.subTest(field=field), __import__("tempfile").TemporaryDirectory() as temp_dir:
        case_dir = __import__("pathlib").Path(temp_dir) / "case"
        init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
        run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)
        private_path = case_dir / "work" / "tax-health-snapshot.private.json"
        payload = json.loads(private_path.read_text(encoding="utf-8"))
        mutation(payload)
        private_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "invalid_case_snapshot"):
          load_case_snapshot(case_dir)

  def test_repeated_initial_run_is_idempotent_and_cannot_replace_request(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司", "2026-01-01", "2026-12-31")
      first = run_case_self_check(case_dir, _request(), occurred_at=OCCURRED_AT)

      repeated = run_case_self_check(case_dir, _request(), occurred_at="2026-07-30T11:00:00+08:00")

      self.assertEqual(repeated, first)
      self.assertEqual(load_case_snapshot(case_dir).revision, 1)
      changed = TaxCheckRequest(
        _request().period, "CN-11", facts=_request().facts, input_hashes=_request().input_hashes,
      )
      with self.assertRaisesRegex(ValueError, "case_snapshot_exists"):
        run_case_self_check(case_dir, changed, occurred_at=OCCURRED_AT)

  def test_case_status_payload_reports_waiting_for_materials_before_first_snapshot(self):
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
      case_dir = __import__("pathlib").Path(temp_dir) / "case"
      init_case(case_dir, "虚构科技有限公司")
      payload = case_status_payload(case_dir)
      self.assertEqual(payload["status"], CaseStatus.WAITING_FOR_MATERIALS.value)
      self.assertEqual(payload["next_action"], "add_source_files")
      self.assertEqual(payload["pending_task_count"], 0)
      self.assertTrue(payload["status_label_zh"])
      self.assertEqual(payload["required_evidence_types"], ["case_source_files"])
