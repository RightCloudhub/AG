"""Deterministic HTTP fixtures; no server, credentials or network are required."""

import json

import httpx
import pytest


@pytest.fixture
def answer() -> dict:
    return {
        "query_id": "query-1",
        "question": "Contract risks?",
        "answer": "Uncapped liability.",
        "status": "answered",
        "route": "agentic",
        "claims": [{"text": "Uncapped liability."}],
        "metadata": {"audit_status": "persisted", "strategy": "global"},
        "cost": {"tokens": 12},
    }


@pytest.fixture
def evidence() -> dict:
    return {
        "evidence_id": "ev-1",
        "content": "Uncapped liability.",
        "status": "available",
        "source_refs": [{"doc_id": "contract-1", "doc_version": 2, "span_start": 0}],
        "offset": 0,
        "total_chars": 100,
    }


@pytest.fixture
def search_result() -> dict:
    return {
        "search_id": "search-1",
        "kb_id": "contracts",
        "audit_status": "persisted",
        "candidates": [
            {"id": "ev-1", "content": "Uncapped liability.", "source": "fulltext_chunk"}
        ],
        "snapshot": {"scope": {"tenant_id": "t", "kb_id": "contracts"}, "generation": "2"},
    }


def envelope(data, **meta) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data, "error": None, "meta": meta})


def event(name: str, data: dict, *, event_id: str = "") -> bytes:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {name}\ndata: {json.dumps(data)}\n\n".encode()
