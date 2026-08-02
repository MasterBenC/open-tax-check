"""Install and replay the common host-Agent contract for all supported hosts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from open_tax_check.host_replays import install_and_replay_all


def _run(skill_dir: Path, fixture: Path, staging: Path) -> dict[str, object]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "host-replay/v1":
        raise ValueError("invalid_host_replay_fixture")
    task_payloads = payload.get("task_payloads")
    delivery = payload.get("delivery")
    if not isinstance(task_payloads, list) or not isinstance(delivery, dict):
        raise ValueError("invalid_host_replay_fixture")
    results = install_and_replay_all(
        skill_dir,
        staging,
        task_payloads=tuple(task_payloads),
        delivery=delivery,
    )
    hosts = [
        {
            "host": result.host,
            "slug": result.slug,
            "install_target": result.install_target,
            "discovery_marker": result.discovery_marker,
            "layout_status": result.layout_status,
            "marker_status": result.marker_status,
            "runtime_validation": result.runtime_validation,
            "task_status": result.task_status,
            "delivery_status": result.delivery_status,
            "issue_codes": list(result.issue_codes),
        }
        for result in results
    ]
    status = "contract_replay_verified" if all(
        host["layout_status"] == "layout_validated"
        and host["marker_status"] == "marker_validated"
        and host["runtime_validation"] == "pending"
        and host["task_status"] == "accepted"
        and host["delivery_status"] == "accepted"
        and not host["issue_codes"]
        for host in hosts
    ) else "failed"
    return {"status": status, "hosts": hosts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the seven-host OpenTaxCheck contract")
    parser.add_argument("--skill-dir", type=Path, default=ROOT / "skills" / "open-tax-check")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "host-replays" / "common-contract.json",
    )
    parser.add_argument("--staging", type=Path)
    args = parser.parse_args()
    try:
        if args.staging is not None:
            staging = args.staging
            if staging.exists() and any(staging.iterdir()):
                raise ValueError("staging_directory_must_be_empty")
            staging.mkdir(parents=True, exist_ok=True)
            result = _run(args.skill_dir, args.fixture, staging)
        else:
            with tempfile.TemporaryDirectory() as temporary:
                result = _run(args.skill_dir, args.fixture, Path(temporary) / "staging")
    except Exception:
        result = {"status": "failed", "error": "host_replay_failed"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "contract_replay_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
