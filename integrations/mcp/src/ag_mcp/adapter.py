"""Tool execution is a thin, cancellable SDK call with required audit receipts."""

import json
import logging
from typing import Any

from ag_client import AGError, APIError, AsyncClient, QueryRequest, SearchRequest
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ValidationError

from ag_mcp.contracts import (
    AuditInput,
    AuditResult,
    EvidenceInput,
    ListInput,
    QueryInput,
    SearchInput,
)

logger = logging.getLogger(__name__)


class ToolAdapter:
    def __init__(self, client: AsyncClient, *, kb_id: str = "default") -> None:
        self.client = client
        self.kb_id = kb_id

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | CallToolResult:
        try:
            result = await self._dispatch(name, arguments)
            return result.model_dump(mode="json")
        except ValidationError:
            return tool_error("INVALID_INPUT", "Tool arguments do not match the declared schema")
        except APIError as exc:
            return tool_error(exc.code, exc.message, details=exc.details)
        except AGError:
            return tool_error(
                "AG_UNAVAILABLE", "AG did not return a valid response; the request was not replayed"
            )
        except Exception as exc:
            # Never return raw exception strings: HTTP errors may contain connection details.
            logger.error("MCP tool failed: %s", type(exc).__name__)
            return tool_error("INTERNAL_ERROR", "Tool execution failed")

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> BaseModel:
        if name == "list_knowledge_bases":
            args = ListInput.model_validate(arguments)
            return await self.client.list_knowledge_bases(page=args.page, limit=args.limit)
        if name == "query":
            return await self._query(QueryInput.model_validate(arguments))
        if name == "search":
            return await self._search(SearchInput.model_validate(arguments))
        if name == "get_evidence":
            return await self._evidence(EvidenceInput.model_validate(arguments))
        if name == "get_audit":
            return await self._audit(AuditInput.model_validate(arguments))
        raise APIError("UNKNOWN_TOOL", "Unknown AG tool")

    async def _query(self, args: QueryInput) -> BaseModel:
        request = QueryRequest(
            question=args.question,
            kb_id=args.kb_id or self.kb_id,
            strategy=args.strategy,
            doc_ids=args.doc_ids,
            max_hops=args.max_hops,
            timeout_ms=args.timeout_ms,
            audit_required=True,
        )
        result = await self.client.query(request)
        require_audit(result.audit_status, result.query_id)
        return result

    async def _search(self, args: SearchInput) -> BaseModel:
        request = SearchRequest(
            query=args.query,
            kb_id=args.kb_id or self.kb_id,
            doc_ids=args.doc_ids,
            top_k=args.top_k,
            audit_required=True,
        )
        result = await self.client.search(request)
        require_audit(result.audit_status, result.search_id)
        return result

    async def _evidence(self, args: EvidenceInput) -> BaseModel:
        return await self.client.get_evidence(
            args.evidence_id,
            kb_id=args.kb_id or self.kb_id,
            offset=args.offset,
            limit=args.limit,
        )

    async def _audit(self, args: AuditInput) -> BaseModel:
        record = await self.client.get_audit(
            args.audit_id,
            kind=args.kind,
            kb_id=args.kb_id or self.kb_id,
        )
        return AuditResult(kind=args.kind, record=record)


def require_audit(status: str | None, audit_id: str) -> None:
    if status != "persisted":
        raise APIError(
            "AUDIT_NOT_PERSISTED",
            "AG did not confirm durable audit persistence",
            details={"audit_id": audit_id, "audit_status": status},
        )


def tool_error(code: str, message: str, *, details: dict[str, Any] | None = None) -> CallToolResult:
    payload = {"error": {"code": code, "message": message, "details": details or {}}}
    return CallToolResult(
        isError=True,
        structuredContent=payload,
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
    )
