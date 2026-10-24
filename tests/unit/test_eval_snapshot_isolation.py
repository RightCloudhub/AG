"""A deterministic eval must not consume an unrelated local indexing snapshot."""

from pathlib import Path

from agentic_graphrag.bootstrap import build_stores, resolve_runtime_profile
from agentic_graphrag.config import AppConfig
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore


def test_eval_profile_ignores_local_fulltext_snapshot(tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg.paths.indexes_dir = str(tmp_path)
    snapshot = BM25FulltextStore()
    snapshot.index([ChunkRecord(chunk_id="noise", doc_id="unrelated", text="phantom", index=0)])
    snapshot.save(str(tmp_path / "bm25.json"))
    bundle = build_stores(resolve_runtime_profile({"AGR_PROFILE": "eval"}), cfg=cfg)
    try:
        assert bundle.fulltext.search("phantom") == []
    finally:
        bundle.close()
