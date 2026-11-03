"""Content-addressed JSON replay cache for LLM completions and embeddings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agentic_graphrag.llm.responses import JSON_OBJECT


def cache_key(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_cache(cache_dir: Path | None, key: str) -> dict[str, Any] | None:
    if not cache_dir:
        return None
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    return JSON_OBJECT.validate_json(path.read_text(encoding="utf-8"))


def write_cache(cache_dir: Path | None, key: str, value: dict[str, Any]) -> None:
    if not cache_dir:
        return
    path = cache_dir / f"{key}.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
