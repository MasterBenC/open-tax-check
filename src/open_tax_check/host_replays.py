"""Executable, host-neutral installation and contract replay checks.

Host message conclusions are validated separately in :mod:`host_contract`.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
import shutil
from typing import Mapping, Sequence


HOST_NAMES = (
    "Codex",
    "Hermes",
    "WorkBuddy",
    "扣子",
    "Qoder",
    "Claude Code",
    "OpenClaw",
)

_HOST_SLUGS = {
    "Codex": "codex",
    "Hermes": "hermes",
    "WorkBuddy": "workbuddy",
    "扣子": "coze",
    "Qoder": "qoder",
    "Claude Code": "claude-code",
    "OpenClaw": "openclaw",
}
_HOST_TARGETS = {
    "Codex": ".codex/skills/open-tax-check",
    "Hermes": ".hermes/skills/open-tax-check",
    "WorkBuddy": ".workbuddy/skills/open-tax-check",
    "扣子": ".coze/skills/open-tax-check",
    "Qoder": ".qoder/skills/open-tax-check",
    "Claude Code": ".claude/skills/open-tax-check",
    "OpenClaw": ".openclaw/skills/open-tax-check",
}
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "question_id",
        "fact_key",
        "tax_ids",
        "question_zh",
        "help_zh",
        "evidence_help_zh",
        "evidence_types",
        "answer_type",
        "choices",
        "priority",
        "blocking",
    }
)
_CHOICE_FIELDS = frozenset({"value", "label_zh"})
_DELIVERY_FIELDS = frozenset(
    {
        "evidence_bundle_status",
        "evidence_bundle_path",
        "evidence_bundle_sha256",
        "includes_evidence_files",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class HostReplayResult:
    host: str
    slug: str
    install_target: str
    discovery_marker: str
    layout_status: str
    marker_status: str
    runtime_validation: str
    task_status: str
    delivery_status: str
    issue_codes: tuple[str, ...] = ()


def _safe_tree_copy(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("invalid_skill_package")
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError("skill_package_link")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError("install_target_exists")
    shutil.copytree(source, target, symlinks=False)


def validate_task_payload(payload: Mapping[str, object]) -> str | None:
    if not isinstance(payload, Mapping) or set(payload) != _TASK_FIELDS:
        return "unknown_or_missing_task_field"
    for key in ("task_id", "question_id", "fact_key", "question_zh", "help_zh", "evidence_help_zh", "answer_type"):
        if not isinstance(payload[key], str) or not payload[key]:
            return "invalid_task_field"
    if not isinstance(payload["tax_ids"], list) or not all(isinstance(value, str) and value for value in payload["tax_ids"]):
        return "invalid_task_field"
    if not isinstance(payload["evidence_types"], list) or not all(isinstance(value, str) and value for value in payload["evidence_types"]):
        return "invalid_task_field"
    if not isinstance(payload["choices"], list):
        return "invalid_task_field"
    for choice in payload["choices"]:
        if not isinstance(choice, Mapping) or set(choice) != _CHOICE_FIELDS:
            return "invalid_task_field"
        if not all(isinstance(choice[key], str) and choice[key] for key in _CHOICE_FIELDS):
            return "invalid_task_field"
    if isinstance(payload["priority"], bool) or not isinstance(payload["priority"], int):
        return "invalid_task_field"
    if not isinstance(payload["blocking"], bool):
        return "invalid_task_field"
    return None


def validate_delivery_payload(payload: Mapping[str, object]) -> str | None:
    if not isinstance(payload, Mapping) or set(payload) != _DELIVERY_FIELDS:
        return "invalid_delivery_payload"
    status = payload["evidence_bundle_status"]
    if status not in {None, "verified"}:
        return "invalid_delivery_payload"
    if not isinstance(payload["includes_evidence_files"], bool):
        return "invalid_delivery_payload"
    path = payload["evidence_bundle_path"]
    digest = payload["evidence_bundle_sha256"]
    if status is None:
        if path is not None or digest is not None or payload["includes_evidence_files"]:
            return "invalid_delivery_payload"
    elif not isinstance(path, str) or not path or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return "invalid_delivery_payload"
    return None


def _replay_one(
    package_root: Path,
    staging_root: Path,
    host: str,
    task_payloads: Sequence[Mapping[str, object]],
    delivery: Mapping[str, object],
) -> HostReplayResult:
    slug = _HOST_SLUGS[host]
    install_target = _HOST_TARGETS[host]
    discovery_marker = f"{install_target}/SKILL.md"
    target = staging_root / Path(install_target)
    try:
        _safe_tree_copy(package_root, target)
    except (OSError, ValueError):
        return HostReplayResult(
            host, slug, install_target, discovery_marker,
            "failed", "not_run", "pending", "not_run", "not_run",
            ("layout_validation_failed",),
        )
    required = (target / "SKILL.md", target / "references" / "host-integrations.md")
    if not all(path.is_file() for path in required):
        return HostReplayResult(
            host, slug, install_target, discovery_marker,
            "layout_validated", "failed", "pending", "not_run", "not_run",
            ("marker_validation_failed",),
        )
    task_issues = tuple(
        issue
        for payload in task_payloads
        for issue in ([validate_task_payload(payload)] if validate_task_payload(payload) else [])
    )
    task_status = "accepted" if not task_issues else "rejected"
    delivery_issue = validate_delivery_payload(delivery)
    delivery_status = "accepted" if delivery_issue is None else "rejected"
    issues = task_issues + ((delivery_issue,) if delivery_issue else ())
    return HostReplayResult(
        host,
        slug,
        install_target,
        discovery_marker,
        "layout_validated",
        "marker_validated",
        "pending",
        task_status,
        delivery_status,
        issues,
    )


def install_and_replay_all(
    package_root: Path,
    staging_root: Path,
    *,
    task_payloads: Sequence[Mapping[str, object]],
    delivery: Mapping[str, object],
) -> tuple[HostReplayResult, ...]:
    """Validate host-specific target layouts and replay host-neutral envelopes."""

    return tuple(
        _replay_one(package_root, staging_root, host, task_payloads, delivery)
        for host in HOST_NAMES
    )


def host_replay_manifest_hash(package_root: Path) -> str:
    """Return a deterministic digest for the installed skill tree."""

    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file() and not path.is_symlink():
            digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()
