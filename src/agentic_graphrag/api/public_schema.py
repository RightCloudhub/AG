"""Describe the middleware-owned authentication and stable v1 error contract."""

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agentic_graphrag.api.envelope import ErrorBody, MetaBody

CONTRACT_VERSION = "1.1.0"
ERROR_STATUSES = (400, 401, 403, 404, 409, 422, 429, 500, 503, 504)
SCOPED_PREFIXES = (
    "/v1/evidence/",
    "/v1/audit/",
    "/v1/documents",
    "/v1/graph/",
    "/v1/review-queue",
    "/v1/review-calibration",
    "/v1/ingest-tasks",
    "/v1/feedback",
)


class ErrorEnvelope(BaseModel):
    success: Literal[False] = False
    data: None = None
    error: ErrorBody
    meta: MetaBody = Field(default_factory=MetaBody)


def install_contract(app: FastAPI) -> None:
    original = app.openapi

    def schema() -> dict[str, Any]:
        document = original()
        document["info"]["x-contract-version"] = CONTRACT_VERSION
        document["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Tenant-bound API key. Optional only in explicit offline mode.",
            }
        }
        for path, methods in document["paths"].items():
            if path.startswith("/v1/"):
                _describe_operations(path, methods)
        return document

    app.__dict__["openapi"] = schema


def _describe_operations(path: str, methods: dict[str, Any]) -> None:
    for method, operation in methods.items():
        if method not in {"get", "post", "patch", "put", "delete"}:
            continue
        operation["security"] = [{"BearerAuth": []}]
        if path.startswith(SCOPED_PREFIXES):
            parameters = operation.setdefault("parameters", [])
            if not any(param.get("name") == "kb_id" for param in parameters):
                parameters.append(
                    {
                        "name": "kb_id",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "default": "default"},
                        "description": "One knowledge base within the authenticated tenant.",
                    }
                )
        if path == "/v1/query/stream":
            operation["x-sse-schema"] = "sse-v1.schema.json"
            operation["x-terminal-events"] = ["answer", "error"]
            operation["x-replay-supported"] = False
            operation["responses"]["200"]["content"].pop("application/json", None)
