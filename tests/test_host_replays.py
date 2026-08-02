import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from open_tax_check.host_replays import (
    HOST_NAMES,
    install_and_replay_all,
    validate_delivery_payload,
    validate_task_payload,
)


def _package_root(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Tax health check\n", encoding="utf-8")
    (root / "references" / "host-integrations.md").write_text("contract\n", encoding="utf-8")
    return root


def task_payload():
    return {
        "task_id": "agent-task-q1",
        "question_id": "q1",
        "fact_key": "entity.vat_registration",
        "tax_ids": ["vat"],
        "question_zh": "请选择增值税纳税人类型。",
        "help_zh": "按客户确认资料选择。",
        "evidence_help_zh": "请提供税务登记或申报依据。",
        "evidence_types": ["tax_return"],
        "answer_type": "choice",
        "choices": [{"value": "general", "label_zh": "一般纳税人"}],
        "priority": 10,
        "blocking": True,
    }


def delivery_payload():
    return {
        "evidence_bundle_status": None,
        "evidence_bundle_path": None,
        "evidence_bundle_sha256": None,
        "includes_evidence_files": False,
    }


def test_all_supported_hosts_validate_distinct_target_layouts_without_claiming_runtime_execution(tmp_path):
    package_root = _package_root(tmp_path)
    result = install_and_replay_all(
        package_root,
        tmp_path / "staging",
        task_payloads=(task_payload(),),
        delivery=delivery_payload(),
    )

    assert tuple(item.host for item in result) == HOST_NAMES
    assert all(item.layout_status == "layout_validated" for item in result)
    assert all(item.marker_status == "marker_validated" for item in result)
    assert all(item.runtime_validation == "pending" for item in result)
    assert all(item.task_status == "accepted" for item in result)
    assert all(item.delivery_status == "accepted" for item in result)
    assert len({item.install_target for item in result}) == 7
    assert len({item.discovery_marker for item in result}) == 7
    assert all((tmp_path / "staging" / item.discovery_marker).is_file() for item in result)


def test_task_contract_rejects_host_status_or_unknown_fields():
    invalid = task_payload() | {"status": "confirmed"}

    assert validate_task_payload(invalid) == "unknown_or_missing_task_field"


def test_delivery_contract_rejects_unverified_evidence_bundle():
    invalid = delivery_payload() | {
        "evidence_bundle_status": "verified",
        "evidence_bundle_sha256": "not-a-digest",
    }

    assert validate_delivery_payload(invalid) == "invalid_delivery_payload"


def test_replay_rejects_missing_skill_discovery_file(tmp_path):
    package_root = _package_root(tmp_path)
    (package_root / "SKILL.md").unlink()

    result = install_and_replay_all(
        package_root,
        tmp_path / "staging",
        task_payloads=(task_payload(),),
        delivery=delivery_payload(),
    )

    assert all(item.layout_status == "layout_validated" for item in result)
    assert all(item.marker_status == "failed" for item in result)


def test_host_replay_command_runs_the_committed_contract_fixture():
    script = Path(__file__).parents[1] / "scripts" / "replay_host_integrations.py"
    fixture = Path(__file__).parent / "fixtures" / "host-replays" / "common-contract.json"
    completed = subprocess.run(
        [sys.executable, str(script), "--fixture", str(fixture)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "cp1252:strict"},
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "contract_replay_verified"
    assert payload["hosts"] == [
        {
            "host": host,
            "slug": slug,
            "install_target": target,
            "discovery_marker": f"{target}/SKILL.md",
            "layout_status": "layout_validated",
            "marker_status": "marker_validated",
            "runtime_validation": "pending",
            "task_status": "accepted",
            "delivery_status": "accepted",
            "issue_codes": [],
        }
        for host, slug, target in (
            ("Codex", "codex", ".codex/skills/open-tax-check"),
            ("Hermes", "hermes", ".hermes/skills/open-tax-check"),
            ("WorkBuddy", "workbuddy", ".workbuddy/skills/open-tax-check"),
            ("扣子", "coze", ".coze/skills/open-tax-check"),
            ("Qoder", "qoder", ".qoder/skills/open-tax-check"),
            ("Claude Code", "claude-code", ".claude/skills/open-tax-check"),
            ("OpenClaw", "openclaw", ".openclaw/skills/open-tax-check"),
        )
    ]
