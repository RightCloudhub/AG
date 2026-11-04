"""Envelope validation and transport settings shared by both clients."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from ag_client.errors import APIError, ProtocolError
from ag_client.requests import QueryRequest, SearchRequest

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_READ_RETRIES = 3
RETRY_STATUSES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class ClientSettings:
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    kb_id: str = "default"
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    read_retries: int = 1

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not include credentials, query or fragment")
        if not 0 <= self.read_retries <= MAX_READ_RETRIES:
            raise ValueError(f"read_retries must be between 0 and {MAX_READ_RETRIES}")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        QueryRequest(question="validate", kb_id=self.kb_id)

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "ag-client/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def path_identifier(value: str) -> str:
    if not value.strip() or len(value) > 2048:
        raise ValueError("identifier must be nonempty and at most 2048 characters")
    return quote(value, safe="")


def read_envelope(response: httpx.Response) -> tuple[Any, dict[str, Any]]:
    try:
        envelope = response.json()
    except ValueError as exc:
        if response.is_error:
            raise APIError(
                "HTTP_ERROR", "HTTP request failed", status_code=response.status_code
            ) from exc
        raise ProtocolError("Response is not a JSON envelope") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("success"), bool):
        raise ProtocolError("Response is not an AG envelope")
    meta = envelope.get("meta", {})
    if not isinstance(meta, dict):
        raise ProtocolError("Invalid envelope metadata")
    if not envelope["success"] or response.is_error:
        raise envelope_error(envelope, status_code=response.status_code, meta=meta)
    if envelope.get("data") is None:
        raise ProtocolError("Successful envelope has no data")
    return envelope["data"], meta


def envelope_error(envelope: dict[str, Any], *, status_code: int, meta: dict[str, Any]) -> APIError:
    error = envelope.get("error") or {}
    if not isinstance(error, dict):
        return APIError("HTTP_ERROR", "HTTP request failed", status_code=status_code)
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    request_id = meta.get("request_id") or details.get("request_id")
    return APIError(
        str(error.get("code") or "HTTP_ERROR"),
        str(error.get("message") or "HTTP request failed"),
        status_code=status_code,
        request_id=str(request_id) if request_id else None,
        details=details,
    )


def parse_model[T: BaseModel](kind: type[T], data: Any) -> T:
    try:
        return kind.model_validate(data)
    except ValidationError as exc:
        raise ProtocolError(f"Invalid {kind.__name__} response") from exc


def query_body(
    request: QueryRequest | str,
    *,
    default_kb: str,
    kb_id: str | None,
    audit_required: bool | None,
) -> dict[str, Any]:
    value = (
        QueryRequest(question=request, kb_id=default_kb) if isinstance(request, str) else request
    )
    data = value.model_dump(exclude_none=True)
    if kb_id is not None:
        data["kb_id"] = kb_id
    if audit_required is not None:
        data["audit_required"] = audit_required
    return QueryRequest.model_validate(data).model_dump(exclude_none=True)


def search_body(
    request: SearchRequest | str,
    *,
    default_kb: str,
    kb_id: str | None,
    top_k: int | None,
    audit_required: bool | None,
) -> dict[str, Any]:
    value = SearchRequest(query=request, kb_id=default_kb) if isinstance(request, str) else request
    data = value.model_dump(exclude_none=True)
    for key, override in {"kb_id": kb_id, "top_k": top_k, "audit_required": audit_required}.items():
        if override is not None:
            data[key] = override
    return SearchRequest.model_validate(data).model_dump(exclude_none=True)


def page_params(*, page: int, limit: int) -> dict[str, str]:
    if page < 1 or not 1 <= limit <= 100:
        raise ValueError("page must be positive and limit between 1 and 100")
    return {"page": str(page), "limit": str(limit)}


def evidence_params(*, kb_id: str, offset: int, limit: int) -> dict[str, str]:
    if offset < 0 or not 1 <= limit <= 16_384:
        raise ValueError("offset must be nonnegative and limit between 1 and 16384")
    return {"kb_id": kb_id, "offset": str(offset), "limit": str(limit)}
