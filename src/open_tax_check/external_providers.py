"""Strict, replayable boundaries for optional external data providers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Protocol

from .document_extraction import (
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    AuthorizationRecord,
    DocumentExtraction,
    ExtractionIssue,
    document_extraction_from_dict,
)
from .document_providers import LocalDocumentSource, _safe_path


_MAX_PROVIDER_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_CLOUD_PAGE_BYTES = 20 * 1024 * 1024
_MAX_CLOUD_TOTAL_BYTES = 100 * 1024 * 1024
_MAX_CLOUD_PAGES = 500
_CREDENTIAL_FIELD = "api" + "_key"
_REPLAY_SCHEMA_VERSION = "provider-replay/v1"


class FengniaoTransport(Protocol):
    def discover(self, intent: str, **credentials: str) -> Mapping[str, object]: ...

    def call(
        self,
        tool: str,
        params: Mapping[str, str],
        **credentials: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class FengniaoQuery:
    search_term: str
    dimension_zh: str
    selected_legal_name: str

    def __post_init__(self) -> None:
        for value in (self.search_term, self.dimension_zh, self.selected_legal_name):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("invalid_fengniao_query")


@dataclass(frozen=True, slots=True)
class ExternalCompanyObservation:
    provider: str
    provider_version: str
    status: str
    observed_at: str
    entity_name: str | None
    dimension_zh: str
    confirmation_state: str
    facts: tuple[tuple[str, object], ...]
    issue_codes: tuple[str, ...]


def _provider_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_provider_payload")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("invalid_provider_payload") from None
    if len(encoded) > _MAX_PROVIDER_PAYLOAD_BYTES:
        raise ValueError("provider_payload_too_large")
    return payload


def _company_result(
    query: FengniaoQuery,
    observed_at: str,
    *,
    status: str,
    entity_name: str | None = None,
    facts: tuple[tuple[str, object], ...] = (),
    issues: tuple[str, ...] = (),
) -> ExternalCompanyObservation:
    return ExternalCompanyObservation(
        provider="fengniao",
        provider_version="replay-v1",
        status=status,
        observed_at=observed_at,
        entity_name=entity_name,
        dimension_zh=query.dimension_zh,
        confirmation_state="external_observation",
        facts=facts,
        issue_codes=issues,
    )


class FengniaoProvider:
    """Execute the discover -> entity resolution -> entid lookup contract."""

    def __init__(self, transport: FengniaoTransport):
        self._transport = transport

    def lookup(
        self,
        query: FengniaoQuery,
        observed_at: str,
        **credentials: str | None,
    ) -> ExternalCompanyObservation:
        customer_key = credentials.get(_CREDENTIAL_FIELD)
        if not isinstance(customer_key, str) or not customer_key.strip():
            return _company_result(
                query,
                observed_at,
                status="not_performed",
                issues=("missing_customer_api_key",),
            )
        try:
            discovery = _provider_payload(
                self._transport.discover(
                    query.dimension_zh,
                    **{_CREDENTIAL_FIELD: customer_key},
                )
            )
            tools = discovery.get("tools")
            if not isinstance(tools, list) or len(tools) != 1:
                return _company_result(
                    query,
                    observed_at,
                    status="not_performed",
                    issues=("dimension_tool_not_found",),
                )
            tool = tools[0]
            tool_name = tool.get("name") if isinstance(tool, Mapping) else None
            if not isinstance(tool_name, str) or not tool_name or tool_name == "biz_fuzzy_search":
                raise ValueError("invalid_provider_payload")

            search = _provider_payload(
                self._transport.call(
                    "biz_fuzzy_search",
                    {"key": query.search_term},
                    **{_CREDENTIAL_FIELD: customer_key},
                )
            )
            candidates = search.get("data")
            if not isinstance(candidates, list):
                raise ValueError("invalid_provider_payload")
            exact = [
                candidate
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and candidate.get("entname") == query.selected_legal_name
                and isinstance(candidate.get("entid"), str)
                and candidate.get("entid")
            ]
            if len(exact) != 1:
                return _company_result(
                    query,
                    observed_at,
                    status="needs_entity_selection",
                    issues=(
                        "selected_entity_not_found"
                        if not exact
                        else "selected_entity_ambiguous",
                    ),
                )
            entid = exact[0]["entid"]
            response = _provider_payload(
                self._transport.call(
                    tool_name,
                    {"entid": entid},
                    **{_CREDENTIAL_FIELD: customer_key},
                )
            )
            data = response.get("data")
            if not isinstance(data, Mapping):
                raise ValueError("invalid_provider_payload")
            if data.get("entname") != query.selected_legal_name:
                return _company_result(
                    query,
                    observed_at,
                    status="failed",
                    issues=("provider_entity_mismatch",),
                )
            facts = tuple(
                sorted(
                    (
                        (key, value)
                        for key, value in data.items()
                        if key not in {"entid", "entname"}
                        and isinstance(key, str)
                        and isinstance(value, (str, int, bool, float, type(None)))
                    ),
                    key=lambda item: item[0],
                )
            )
            return _company_result(
                query,
                observed_at,
                status="completed",
                entity_name=query.selected_legal_name,
                facts=facts,
            )
        except Exception:
            return _company_result(
                query,
                observed_at,
                status="failed",
                issues=("external_provider_unavailable",),
            )


def _load_replay_fixture(path: Path, provider: str) -> Mapping[str, object]:
    """Load a bounded, repository-controlled provider replay fixture."""

    if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
        raise ValueError("invalid_provider_replay_fixture")
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_provider_replay_fixture") from None
    payload = _provider_payload(payload)
    if (
        payload.get("schema_version") != _REPLAY_SCHEMA_VERSION
        or payload.get("provider") != provider
    ):
        raise ValueError("invalid_provider_replay_fixture")
    return payload


class FengniaoReplayTransport:
    """Replay the Fengniao discover/search/detail contract without a network call."""

    def __init__(self, payload: Mapping[str, object]):
        self._payload = _provider_payload(payload)
        self._calls: list[tuple] = []
        self._call_index = 0

    @classmethod
    def from_path(cls, path: Path) -> "FengniaoReplayTransport":
        return cls(_load_replay_fixture(path, "fengniao"))

    @property
    def complete(self) -> bool:
        calls = self._payload.get("calls")
        return isinstance(calls, list) and self._call_index == len(calls)

    @property
    def calls(self) -> tuple[tuple, ...]:
        return tuple(self._calls)

    def discover(self, intent: str, **credentials: str) -> Mapping[str, object]:
        if intent != self._payload.get("discover_intent"):
            raise ValueError("replay_intent_mismatch")
        response = self._payload.get("discovery")
        if not isinstance(response, Mapping):
            raise ValueError("invalid_provider_replay_fixture")
        self._calls.append(("discover", intent))
        return response

    def call(
        self,
        tool: str,
        params: Mapping[str, str],
        **credentials: str,
    ) -> Mapping[str, object]:
        calls = self._payload.get("calls")
        if not isinstance(calls, list) or self._call_index >= len(calls):
            raise ValueError("replay_call_unexpected")
        expected = calls[self._call_index]
        if not isinstance(expected, Mapping) or expected.get("tool") != tool:
            raise ValueError("replay_call_mismatch")
        expected_params = expected.get("params")
        if not isinstance(expected_params, Mapping) or dict(expected_params) != dict(params):
            raise ValueError("replay_call_mismatch")
        response = expected.get("response")
        if not isinstance(response, Mapping):
            raise ValueError("invalid_provider_replay_fixture")
        self._call_index += 1
        self._calls.append(("call", tool, tuple(sorted(params.items()))))
        return response


@dataclass(frozen=True, slots=True)
class CloudOcrRequest:
    provider: str
    provider_version: str
    source_id: str
    media_type: str
    transfer_scope: str
    page_numbers: tuple[int, ...]
    payloads: tuple[tuple[int, bytes], ...]
    authorized_subject: str
    authorized_at: str
    cache_retention: str


class CloudOcrTransport(Protocol):
    def extract(self, request: CloudOcrRequest) -> Mapping[str, object]: ...


def _cloud_result(
    source: LocalDocumentSource,
    authorization: AuthorizationRecord,
    *,
    status: str,
    issue_code: str,
) -> DocumentExtraction:
    return DocumentExtraction(
        schema_version=DOCUMENT_EXTRACTION_SCHEMA_VERSION,
        document_id=f"cloud-{source.source_id}",
        source_id=source.source_id,
        filename=source.path.name,
        media_type=source.media_type,
        provider="cloud",
        provider_version=authorization.provider_version,
        executed_at=authorization.authorized_at,
        authorization=authorization,
        status=status,
        pages=(),
        blocks=(),
        tables=(),
        issues=(ExtractionIssue(issue_code, "info" if status == "not_performed" else "error", issue_code),),
        source_refs=(source.source_id,),
    )


def _authorized_pages(
    source: LocalDocumentSource,
    authorization: AuthorizationRecord,
) -> tuple[int, ...] | None:
    prefix = f"source:{source.source_id};pages:"
    if authorization.transfer_scope != "rendered_pages" or not authorization.scope.startswith(prefix):
        return None
    raw_pages = authorization.scope[len(prefix) :]
    try:
        pages = tuple(sorted({int(value) for value in raw_pages.split(",")}))
    except ValueError:
        return None
    if not pages or len(pages) > _MAX_CLOUD_PAGES or pages[0] < 1:
        return None
    return pages


class CloudOcrProvider:
    """Send only explicitly authorized page payloads and validate recorded responses."""

    def __init__(self, transport: CloudOcrTransport):
        self._transport = transport

    def extract(
        self,
        source: LocalDocumentSource,
        *,
        authorization: AuthorizationRecord,
        page_payloads: Mapping[int, bytes],
    ) -> DocumentExtraction:
        if authorization.provider != "cloud" or not authorization.subject.strip():
            return _cloud_result(
                source,
                authorization,
                status="not_performed",
                issue_code="cloud_authorization_invalid",
            )
        if authorization.revoked:
            return _cloud_result(
                source,
                authorization,
                status="not_performed",
                issue_code="cloud_authorization_revoked",
            )
        if not _safe_path(source):
            return _cloud_result(
                source,
                authorization,
                status="failed",
                issue_code="unsafe_source_path",
            )
        pages = _authorized_pages(source, authorization)
        if pages is None or set(page_payloads) != set(pages):
            return _cloud_result(
                source,
                authorization,
                status="not_performed",
                issue_code="cloud_transfer_scope_mismatch",
            )
        payloads: list[tuple[int, bytes]] = []
        total = 0
        for page in pages:
            payload = page_payloads[page]
            if not isinstance(payload, bytes) or not payload or len(payload) > _MAX_CLOUD_PAGE_BYTES:
                return _cloud_result(
                    source,
                    authorization,
                    status="not_performed",
                    issue_code="cloud_payload_invalid",
                )
            total += len(payload)
            if total > _MAX_CLOUD_TOTAL_BYTES:
                return _cloud_result(
                    source,
                    authorization,
                    status="not_performed",
                    issue_code="cloud_payload_limit_exceeded",
                )
            payloads.append((page, payload))
        request = CloudOcrRequest(
            provider="cloud",
            provider_version=authorization.provider_version,
            source_id=source.source_id,
            media_type=source.media_type,
            transfer_scope=authorization.transfer_scope,
            page_numbers=pages,
            payloads=tuple(payloads),
            authorized_subject=authorization.subject,
            authorized_at=authorization.authorized_at,
            cache_retention=authorization.cache_retention,
        )
        try:
            result = document_extraction_from_dict(
                _provider_payload(self._transport.extract(request))
            )
            result_pages = {page.page_number for page in result.pages}
            referenced_pages = result_pages | {block.page_number for block in result.blocks} | {
                table.page_number for table in result.tables
            }
            if (
                result.provider != "cloud"
                or result.provider_version != authorization.provider_version
                or result.authorization != authorization
                or result.media_type != source.media_type
                or not referenced_pages.issubset(set(pages))
            ):
                raise ValueError("invalid_provider_payload")
        except Exception:
            return _cloud_result(
                source,
                authorization,
                status="failed",
                issue_code="cloud_provider_unavailable",
            )
        return DocumentExtraction(
            schema_version=result.schema_version,
            document_id=result.document_id,
            source_id=source.source_id,
            filename=source.path.name,
            media_type=source.media_type,
            provider="cloud",
            provider_version=result.provider_version,
            executed_at=result.executed_at,
            authorization=authorization,
            status=result.status,
            pages=result.pages,
            blocks=result.blocks,
            tables=result.tables,
            issues=result.issues,
            source_refs=(source.source_id,),
        )


class CloudOcrReplayTransport:
    """Replay an authorized cloud OCR response while checking transferred pages."""

    def __init__(self, payload: Mapping[str, object]):
        self._payload = _provider_payload(payload)
        self._calls: list[tuple[str, tuple[int, ...]]] = []

    @classmethod
    def from_path(cls, path: Path) -> "CloudOcrReplayTransport":
        return cls(_load_replay_fixture(path, "cloud"))

    @property
    def calls(self) -> tuple[tuple[str, tuple[int, ...]], ...]:
        return tuple(self._calls)

    def extract(self, request: CloudOcrRequest) -> Mapping[str, object]:
        expected = self._payload.get("expected_request")
        if not isinstance(expected, Mapping):
            raise ValueError("invalid_provider_replay_fixture")
        if (
            expected.get("source_id") != request.source_id
            or expected.get("media_type") != request.media_type
            or expected.get("transfer_scope") != request.transfer_scope
            or tuple(expected.get("page_numbers", ())) != request.page_numbers
        ):
            raise ValueError("replay_request_mismatch")
        expected_hashes = expected.get("payload_sha256")
        actual_hashes = {
            str(page): hashlib.sha256(payload).hexdigest()
            for page, payload in request.payloads
        }
        if not isinstance(expected_hashes, Mapping) or dict(expected_hashes) != actual_hashes:
            raise ValueError("replay_request_mismatch")
        response = self._payload.get("response")
        if not isinstance(response, Mapping):
            raise ValueError("invalid_provider_replay_fixture")
        self._calls.append((request.source_id, request.page_numbers))
        return response
