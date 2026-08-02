from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

from open_tax_check.document_extraction import AuthorizationRecord
from open_tax_check.document_providers import LocalDocumentSource
from open_tax_check.external_providers import (
    CloudOcrProvider,
    CloudOcrReplayTransport,
    FengniaoProvider,
    FengniaoQuery,
    FengniaoReplayTransport,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "provider-replays"


class RecordedFengniaoTransport:
    def __init__(self):
        self.calls = []

    def discover(self, intent, *, api_key):
        self.calls.append(("discover", intent, api_key))
        return {"tools": [{"name": "biz_basic_info"}]}

    def call(self, tool, params, *, api_key):
        self.calls.append(("call", tool, params, api_key))
        if tool == "biz_fuzzy_search":
            return {
                "code": 20000,
                "data": [
                    {
                        "entid": "internal-only-id",
                        "entname": "虚构科技有限公司",
                    }
                ],
            }
        return {
            "code": 20000,
            "data": {
                "entid": "internal-only-id",
                "entname": "虚构科技有限公司",
                "credit_code": "91310000TEST000001",
            },
        }


def test_fengniao_replay_discovers_then_resolves_entity_and_hides_entid():
    transport = RecordedFengniaoTransport()

    result = FengniaoProvider(transport).lookup(
        FengniaoQuery("虚构科技", "企业基本信息", "虚构科技有限公司"),
        **{"api" + "_key": "customer-key"},
        observed_at="2026-07-31T10:00:00+08:00",
    )

    assert result.status == "completed"
    assert result.entity_name == "虚构科技有限公司"
    assert result.confirmation_state == "external_observation"
    assert result.facts == (("credit_code", "91310000TEST000001"),)
    assert "internal-only-id" not in repr(result)
    assert [call[0:2] for call in transport.calls] == [
        ("discover", "企业基本信息"),
        ("call", "biz_fuzzy_search"),
        ("call", "biz_basic_info"),
    ]


def test_fengniao_formal_fixture_replay_uses_the_provider_contract():
    transport = FengniaoReplayTransport.from_path(
        FIXTURE_ROOT / "fengniao-basic-info.json"
    )

    result = FengniaoProvider(transport).lookup(
        FengniaoQuery("虚构科技", "企业基本信息", "虚构科技有限公司"),
        observed_at="2026-07-31T10:00:00+08:00",
        **{"api" + "_key": "fixture-key"},
    )

    assert result.status == "completed"
    assert result.facts == (("credit_code", "91310000TEST000001"),)
    assert transport.calls == (
        ("discover", "企业基本信息"),
        ("call", "biz_fuzzy_search", (("key", "虚构科技"),)),
        ("call", "biz_basic_info", (("entid", "fixture-entid"),)),
    )


def test_fengniao_without_customer_key_does_not_call_transport():
    transport = RecordedFengniaoTransport()

    result = FengniaoProvider(transport).lookup(
        FengniaoQuery("虚构科技", "企业基本信息", "虚构科技有限公司"),
        **{"api" + "_key": None},
        observed_at="2026-07-31T10:00:00+08:00",
    )

    assert result.status == "not_performed"
    assert result.issue_codes == ("missing_customer_api_key",)
    assert transport.calls == []


def test_fengniao_requires_exact_selected_legal_name():
    transport = RecordedFengniaoTransport()

    result = FengniaoProvider(transport).lookup(
        FengniaoQuery("虚构科技", "企业基本信息", "另一家公司"),
        **{"api" + "_key": "customer-key"},
        observed_at="2026-07-31T10:00:00+08:00",
    )

    assert result.status == "needs_entity_selection"
    assert result.issue_codes == ("selected_entity_not_found",)
    assert len(transport.calls) == 2


class RecordedCloudTransport:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def extract(self, request):
        self.requests.append(request)
        return self.payload


def _cloud_authorization(*, revoked=False):
    return AuthorizationRecord(
        provider="cloud",
        provider_version="recorded-v1",
        transfer_scope="rendered_pages",
        subject="customer-owner",
        authorized_at="2026-07-31T10:00:00+08:00",
        scope="source:src-1;pages:2",
        cache_retention="none",
        revoked=revoked,
    )


def _cloud_payload(authorization):
    return {
        "schema_version": "document-extraction/v1.1",
        "document_id": "cloud-doc-1",
        "source_id": "forged-source",
        "filename": "forged.pdf",
        "media_type": "application/pdf",
        "provider": "cloud",
        "provider_version": "recorded-v1",
        "executed_at": "2026-07-31T10:01:00+08:00",
        "authorization": asdict(authorization),
        "status": "completed",
        "pages": [
            {"page_number": 2, "width": 100, "height": 100, "unit": "pt", "rotation": 0}
        ],
        "blocks": [],
        "tables": [],
        "issues": [],
        "source_refs": ["forged-source"],
    }


def test_cloud_ocr_sends_only_explicitly_authorized_pages_and_rebinds_source(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "scan.pdf"
    path.write_bytes(b"not-sent-whole-file")
    source = LocalDocumentSource("src-1", path, "application/pdf", input_dir)
    authorization = _cloud_authorization()
    transport = RecordedCloudTransport(_cloud_payload(authorization))

    result = CloudOcrProvider(transport).extract(
        source,
        authorization=authorization,
        page_payloads={2: b"rendered-page-two"},
    )

    assert result.status == "completed"
    assert result.source_id == "src-1"
    assert result.filename == "scan.pdf"
    assert result.source_refs == ("src-1",)
    request = transport.requests[0]
    assert request.page_numbers == (2,)
    assert request.payloads == ((2, b"rendered-page-two"),)
    assert b"not-sent-whole-file" not in tuple(payload for _, payload in request.payloads)


def test_cloud_ocr_formal_fixture_replay_checks_page_scope_and_rebinds_source(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "scan.pdf"
    path.write_bytes(b"not-sent-whole-file")
    source = LocalDocumentSource("src-1", path, "application/pdf", input_dir)
    authorization = _cloud_authorization()
    transport = CloudOcrReplayTransport.from_path(
        FIXTURE_ROOT / "cloud-ocr-page-2.json"
    )

    result = CloudOcrProvider(transport).extract(
        source,
        authorization=authorization,
        page_payloads={2: b"rendered-page-two"},
    )

    assert result.status == "completed"
    assert result.source_id == "src-1"
    assert result.filename == "scan.pdf"
    assert result.source_refs == ("src-1",)
    assert transport.calls == (("src-1", (2,)),)


def test_provider_replay_command_runs_committed_fixtures():
    script = Path(__file__).parents[1] / "scripts" / "replay_external_providers.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--fixture-dir", str(FIXTURE_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "status": "completed",
        "fengniao": {
            "status": "completed",
            "issue_codes": [],
            "replay_complete": True,
        },
        "cloud": {
            "status": "completed",
            "issue_codes": [],
        },
    }


def test_cloud_ocr_revoked_authorization_never_calls_transport(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "scan.pdf"
    path.write_bytes(b"private")
    source = LocalDocumentSource("src-1", path, "application/pdf", input_dir)
    authorization = _cloud_authorization(revoked=True)
    transport = RecordedCloudTransport(_cloud_payload(authorization))

    result = CloudOcrProvider(transport).extract(
        source,
        authorization=authorization,
        page_payloads={2: b"rendered-page-two"},
    )

    assert result.status == "not_performed"
    assert result.issues[0].code == "cloud_authorization_revoked"
    assert transport.requests == []


def test_cloud_ocr_rejects_payload_outside_authorized_page_scope(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = input_dir / "scan.pdf"
    path.write_bytes(b"private")
    source = LocalDocumentSource("src-1", path, "application/pdf", input_dir)
    authorization = _cloud_authorization()
    transport = RecordedCloudTransport(_cloud_payload(authorization))

    result = CloudOcrProvider(transport).extract(
        source,
        authorization=authorization,
        page_payloads={1: b"wrong-page"},
    )

    assert result.status == "not_performed"
    assert result.issues[0].code == "cloud_transfer_scope_mismatch"
    assert transport.requests == []
