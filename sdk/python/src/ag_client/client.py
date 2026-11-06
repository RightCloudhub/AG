"""Synchronous API client. Only safe GET requests may be retried."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, overload

import httpx

from ag_client.errors import ProtocolError, StreamInterrupted, TransportError
from ag_client.evidence import EvidenceResult, QueryAudit, SearchAudit, SearchResult
from ag_client.models import KnowledgeBase, KnowledgeBasePage, QueryResult
from ag_client.requests import QueryRequest, SearchRequest
from ag_client.stream import SSEDecoder, StreamEvent, StreamState
from ag_client.transport import (
    RETRY_STATUSES,
    ClientSettings,
    evidence_params,
    page_params,
    parse_model,
    path_identifier,
    query_body,
    read_envelope,
    search_body,
)


class Client:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        api_key: str | None = None,
        kb_id: str = "default",
        timeout: float = 120.0,
        read_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = ClientSettings(base_url, api_key, kb_id, timeout, read_retries)
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def query(
        self,
        request: QueryRequest | str,
        *,
        kb_id: str | None = None,
        audit_required: bool | None = None,
    ) -> QueryResult:
        body = query_body(
            request, default_kb=self._settings.kb_id, kb_id=kb_id, audit_required=audit_required
        )
        data, _ = self._request("POST", "/v1/query", body=body)
        return parse_model(QueryResult, data)

    def search(
        self,
        request: SearchRequest | str,
        *,
        kb_id: str | None = None,
        top_k: int | None = None,
        audit_required: bool | None = None,
    ) -> SearchResult:
        body = search_body(
            request,
            default_kb=self._settings.kb_id,
            kb_id=kb_id,
            top_k=top_k,
            audit_required=audit_required,
        )
        data, _ = self._request("POST", "/v1/search", body=body)
        return parse_model(SearchResult, data)

    def get_evidence(
        self,
        evidence_id: str,
        *,
        kb_id: str | None = None,
        offset: int = 0,
        limit: int = 4096,
    ) -> EvidenceResult:
        params = evidence_params(kb_id=kb_id or self._settings.kb_id, offset=offset, limit=limit)
        data, _ = self._request(
            "GET", f"/v1/evidence/{path_identifier(evidence_id)}", params=params
        )
        return parse_model(EvidenceResult, data)

    @overload
    def get_audit(
        self,
        audit_id: str,
        *,
        kind: Literal["query"] = "query",
        kb_id: str | None = None,
    ) -> QueryAudit: ...

    @overload
    def get_audit(
        self,
        audit_id: str,
        *,
        kind: Literal["search"],
        kb_id: str | None = None,
    ) -> SearchAudit: ...

    def get_audit(
        self,
        audit_id: str,
        *,
        kind: Literal["query", "search"] = "query",
        kb_id: str | None = None,
    ) -> QueryAudit | SearchAudit:
        if kind not in {"query", "search"}:
            raise ValueError("audit kind must be query or search")
        category = "queries" if kind == "query" else "searches"
        data, _ = self._request(
            "GET",
            f"/v1/audit/{category}/{path_identifier(audit_id)}",
            params={"kb_id": kb_id or self._settings.kb_id},
        )
        if kind == "query":
            return parse_model(QueryAudit, data)
        return parse_model(SearchAudit, data)

    def list_knowledge_bases(self, *, page: int = 1, limit: int = 50) -> KnowledgeBasePage:
        data, meta = self._request(
            "GET", "/v1/knowledge-bases", params=page_params(page=page, limit=limit)
        )
        return parse_model(
            KnowledgeBasePage,
            {"items": data, "total": meta.get("total"), "page": page, "limit": limit},
        )

    def iter_knowledge_bases(self, *, page_size: int = 50) -> Iterator[KnowledgeBase]:
        page, seen = 1, set[str]()
        while True:
            result = self.list_knowledge_bases(page=page, limit=page_size)
            fresh = [kb for kb in result.items if kb.kb_id not in seen]
            if result.items and not fresh:
                raise ProtocolError("Knowledge-base pagination did not advance")
            for kb in fresh:
                seen.add(kb.kb_id)
                yield kb
            if len(result.items) < page_size or (
                result.total is not None and len(seen) >= result.total
            ):
                return
            page += 1

    @contextmanager
    def stream(
        self,
        request: QueryRequest | str,
        *,
        kb_id: str | None = None,
        audit_required: bool | None = None,
    ) -> Iterator[Iterator[StreamEvent]]:
        body = query_body(
            request, default_kb=self._settings.kb_id, kb_id=kb_id, audit_required=audit_required
        )
        headers = {**self._settings.headers(), "Accept": "text/event-stream"}
        try:
            with self._http.stream(
                "POST",
                self._settings.url("/v1/query/stream"),
                json=body,
                headers=headers,
            ) as response:
                if response.is_error:
                    response.read()
                    read_envelope(response)
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ProtocolError("Query stream is not an SSE response")
                yield self._events(response)
        except httpx.TransportError as exc:
            raise StreamInterrupted() from exc

    def _events(self, response: httpx.Response) -> Iterator[StreamEvent]:
        decoder = SSEDecoder()
        state = StreamState(query_id=response.headers.get("x-query-id"))
        try:
            for line in response.iter_lines():
                event = decoder.feed(line)
                if event is None:
                    continue
                state.accept(event)
                yield event
                if state.terminal:
                    return
            state.finish()
        except httpx.TransportError as exc:
            raise StreamInterrupted(query_id=state.query_id) from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        retries = self._settings.read_retries if method == "GET" else 0
        for attempt in range(retries + 1):
            try:
                response = self._http.request(
                    method,
                    self._settings.url(path),
                    json=body,
                    params=params,
                    headers=self._settings.headers(),
                )
                if response.status_code in RETRY_STATUSES and attempt < retries:
                    response.close()
                    continue
                return read_envelope(response)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise TransportError("AG request failed; no response was received") from exc
        raise AssertionError("unreachable")
