"""Tenant/run-scoped single-flight retrieval, with checkpointable successful hits."""

import json
from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock

from agentic_graphrag.agent.register_search import query_key
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.json_types import JsonObject

type QueryCache = dict[str, list[Candidate]]


def merge_cache(previous: QueryCache, incoming: QueryCache) -> QueryCache:
    return incoming | previous


def scoped_key(tenant_id: str, run_id: str, *, tool: str, args: JsonObject) -> str:
    return json.dumps([tenant_id, run_id, query_key(tool, args)], ensure_ascii=False)


class QueryDedup:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: dict[str, Future[list[Candidate]]] = {}

    def prime(self, values: QueryCache) -> None:
        with self._lock:
            for key, hits in values.items():
                if key not in self._pending:
                    future: Future[list[Candidate]] = Future()
                    future.set_result(hits)
                    self._pending[key] = future

    def retrieve(self, key: str, run: Callable[[], list[Candidate]]) -> list[Candidate]:
        future: Future[list[Candidate]]
        with self._lock:
            existing = self._pending.get(key)
            if existing is None:
                self._pending[key] = future = Future()
        if existing is not None:
            return [hit.model_copy(deep=True) for hit in existing.result()]
        try:
            hits = run()
            saved = [hit.model_copy(deep=True) for hit in hits]
        except BaseException as exc:
            # Cancellation must wake every waiter too, then propagate to the worker.
            # A failed tool remains retryable; a successful empty result is cached.
            with self._lock:
                self._pending.pop(key, None)
            future.set_exception(exc)
            raise
        future.set_result(saved)
        return hits
