"""One authenticated JSON POST to an OpenAI-compatible endpoint."""

from __future__ import annotations

from typing import Any

import httpx

from agentic_graphrag.llm.http_transport import ProviderHttpPool
from agentic_graphrag.llm.responses import JSON_OBJECT

# Connect must fail fast: SSL hang otherwise dominates Fast Path P95.
CONNECT_TIMEOUT_SECONDS = 3.0
WRITE_TIMEOUT_SECONDS = 30.0


def post_json(
    path: str,
    payload: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    pool: ProviderHttpPool | None = None,
) -> dict[str, Any]:
    if pool is not None:
        return pool.post(path, payload, base_url=base_url, api_key=api_key, timeout=timeout_seconds)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    limit = float(timeout_seconds)
    connect = min(CONNECT_TIMEOUT_SECONDS, limit)
    timeout = httpx.Timeout(
        connect=connect, read=limit, write=min(WRITE_TIMEOUT_SECONDS, limit), pool=connect
    )
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        resp = client.post(path, headers=headers, json=payload)
        resp.raise_for_status()
        return JSON_OBJECT.validate_python(resp.json())
