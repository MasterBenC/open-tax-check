"""Run the committed offline provider contract replays."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from open_tax_check.document_extraction import AuthorizationRecord
from open_tax_check.document_providers import LocalDocumentSource
from open_tax_check.external_providers import (
    CloudOcrProvider,
    CloudOcrReplayTransport,
    FengniaoProvider,
    FengniaoQuery,
    FengniaoReplayTransport,
)


def _run(fixture_dir: Path) -> dict[str, object]:
    fengniao_fixture = fixture_dir / "fengniao-basic-info.json"
    fengniao_payload = json.loads(fengniao_fixture.read_text(encoding="utf-8"))
    query_payload = fengniao_payload["query"]
    query = FengniaoQuery(
        query_payload["search_term"],
        query_payload["dimension_zh"],
        query_payload["selected_legal_name"],
    )
    fengniao_transport = FengniaoReplayTransport.from_path(fengniao_fixture)
    fengniao = FengniaoProvider(fengniao_transport).lookup(
        query,
        observed_at="2026-07-31T10:00:00+08:00",
        **{"api" + "_key": "fixture-key"},
    )

    cloud_fixture = fixture_dir / "cloud-ocr-page-2.json"
    cloud_payload = json.loads(cloud_fixture.read_text(encoding="utf-8"))
    authorization = AuthorizationRecord(
        **cloud_payload["response"]["authorization"]
    )
    with tempfile.TemporaryDirectory() as temporary:
        input_dir = Path(temporary) / "input"
        input_dir.mkdir()
        source_path = input_dir / "scan.pdf"
        source_path.write_bytes(b"fictional-provider-replay-source")
        source = LocalDocumentSource("src-1", source_path, "application/pdf", input_dir)
        cloud_transport = CloudOcrReplayTransport.from_path(cloud_fixture)
        cloud = CloudOcrProvider(cloud_transport).extract(
            source,
            authorization=authorization,
            page_payloads={2: b"rendered-page-two"},
        )

    payload = {
        "status": "completed"
        if fengniao.status == "completed" and cloud.status == "completed"
        else "failed",
        "fengniao": {
            "status": fengniao.status,
            "issue_codes": list(fengniao.issue_codes),
            "replay_complete": fengniao_transport.complete,
        },
        "cloud": {
            "status": cloud.status,
            "issue_codes": [issue.code for issue in cloud.issues],
        },
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay offline provider contracts")
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "provider-replays",
    )
    args = parser.parse_args()
    try:
        result = _run(args.fixture_dir)
    except Exception:
        result = {"status": "failed", "error": "provider_replay_failed"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
