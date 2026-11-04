"""Bounded HTTP connections shared by a provider's requests, without POST retries."""

from __future__ import annotations

import threading
from typing import Any

import httpx

from agentic_graphrag.llm.responses import JSON_OBJECT


class ProviderHttpPool:
    def __init__(self) -> None:
        self._clients: dict[tuple[str, float], httpx.Client] = {}
        self._lock = threading.Lock()

    def client(self, base_url: str, timeout_seconds: float) -> httpx.Client:
        key = (base_url, timeout_seconds)
        with self._lock:
            if key not in self._clients:
                timeout = httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))
                self._clients[key] = httpx.Client(
                    base_url=base_url, timeout=timeout, follow_redirects=False
                )
            return self._clients[key]

    def post(
        self, path: str, payload: dict[str, Any], *, base_url: str, api_key: str, timeout: float
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = self.client(base_url, timeout).post(path, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        if path == "/rerank" and isinstance(data, list):
            data = {"results": data}
        return JSON_OBJECT.validate_python(data)

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
