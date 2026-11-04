"""Content-addressed JSON replay cache for LLM completions and embeddings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from agentic_graphrag.llm.responses import JSON_OBJECT


def directory(path: str | Path | None) -> Path | None:
    if not path:
        return None
    result = Path(path)
    result.mkdir(parents=True, exist_ok=True)
    return result


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
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(value, ensure_ascii=False))
            handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
