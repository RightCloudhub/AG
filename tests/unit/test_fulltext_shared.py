"""BM25 tenant persistence + cross-process sharing (ARCH_OPTIMIZATION_V2 O-3)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentic_graphrag.bootstrap.profile import CAPABILITY_MATRIX, Profile
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.stores.factory import FulltextBackend, create_fulltext_store
from agentic_graphrag.stores.fulltext_shared import SharedFileBM25Store
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore


def _chunk(cid: str, text: str, tenant: str = "") -> ChunkRecord:
    return ChunkRecord(chunk_id=cid, doc_id="d", text=text, index=0, tenant_id=tenant)


def test_snapshot_keeps_tenant_isolation(tmp_path: Path) -> None:
    store = BM25FulltextStore()
    store.index([_chunk("a", "apex holdings secret", "t1"), _chunk("b", "apex public", "t2")])
    path = tmp_path / "bm25.json"
    store.save(str(path))
    reloaded = BM25FulltextStore()
    reloaded.load(str(path))
    hits = reloaded.search("apex", tenant_id="t2")
    assert [c.chunk_id for c, _ in hits] == ["b"]  # pre-V2: "a" leaked to t2


def test_legacy_snapshot_without_tenant_stays_global(tmp_path: Path) -> None:
    path = tmp_path / "bm25.json"
    legacy = [{"chunk_id": "x", "doc_id": "d", "text": "apex", "index": 0}]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = BM25FulltextStore()
    store.load(str(path))
    assert store.search("apex", tenant_id="any")[0][0].tenant_id == ""


def _bump(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_worker_writes_are_visible_to_api(tmp_path: Path) -> None:
    path = tmp_path / "bm25.json"
    api = SharedFileBM25Store(path)
    worker = SharedFileBM25Store(path)
    assert api.search("novatech") == []
    worker.index([_chunk("w1", "NovaTech Industries makes servers")])
    _bump(path)  # coarse-mtime filesystems: make the change observable
    assert [c.chunk_id for c, _ in api.search("novatech")] == ["w1"]
    api.index([_chunk("a1", "Apex Holdings")])
    _bump(path)
    ids = {c.chunk_id for c, _ in worker.search("novatech apex")}
    assert ids == {"w1", "a1"}  # writer merged onto the newest snapshot


def test_corrupt_snapshot_keeps_last_good_state(tmp_path: Path) -> None:
    path = tmp_path / "bm25.json"
    store = SharedFileBM25Store(path)
    store.index([_chunk("a", "apex")])
    path.write_text("{not json", encoding="utf-8")
    _bump(path)
    assert [c.chunk_id for c, _ in store.search("apex")] == ["a"]


def test_clear_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "bm25.json"
    store = SharedFileBM25Store(path)
    store.index([_chunk("a", "apex")])
    snap = tmp_path / "copy.json"
    store.save(str(snap))
    store.clear()
    assert store.search("apex") == []
    assert store.load(str(snap)) == 1
    assert SharedFileBM25Store(path).search("apex")


def test_factory_and_capabilities(tmp_path: Path) -> None:
    assert isinstance(create_fulltext_store(), BM25FulltextStore)
    shared = create_fulltext_store("shared_file", path=tmp_path / "s.json")
    assert isinstance(shared, SharedFileBM25Store)
    with pytest.raises(ValueError):
        create_fulltext_store(FulltextBackend.SHARED_FILE)
    assert CAPABILITY_MATRIX[Profile.LIVE].fulltext is FulltextBackend.SHARED_FILE
    assert CAPABILITY_MATRIX[Profile.HYBRID].fulltext is FulltextBackend.SHARED_FILE
    assert CAPABILITY_MATRIX[Profile.OFFLINE].fulltext is FulltextBackend.PROCESS
