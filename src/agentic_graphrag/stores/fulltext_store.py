"""In-process BM25 fulltext store for POC (no Elasticsearch)."""

from __future__ import annotations

import math
import re
from collections import Counter
from uuid import uuid4

from agentic_graphrag.core.ports.stores import (
    STAGE_FULLTEXT,
    ChunkIdentity,
    ChunkKey,
    ChunkRecord,
)
from agentic_graphrag.core.tenancy import scoped_id, tenant_matches
from agentic_graphrag.stores.chunk_identity import admits, remove_below

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class _SimpleBM25:
    """BM25 with smoothed IDF so small corpora still produce positive scores."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / max(len(corpus), 1)
        self.doc_freqs: list[Counter[str]] = [Counter(doc) for doc in corpus]
        df: Counter[str] = Counter()
        for doc in corpus:
            for term in set(doc):
                df[term] += 1
        self.df = df
        self.n_docs = len(corpus)

    def _idf(self, term: str) -> float:
        # Robertson-Sparck Jones with +1 smoothing to avoid zero IDF on tiny corpora
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.n_docs - n + 0.5) / (n + 0.5))

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.n_docs
        for term in query:
            idf = self._idf(term)
            if idf <= 0:
                continue
            for i, freqs in enumerate(self.doc_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                dl = self.doc_len[i]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-9))
                scores[i] += idf * (tf * (self.k1 + 1)) / denom
        return scores


class BM25FulltextStore:
    def __init__(self) -> None:
        self.index_generation = uuid4().hex
        self._chunks: list[ChunkRecord] = []
        self._corpus_tokens: list[list[str]] = []
        self._bm25: _SimpleBM25 | None = None
        self._floors: dict[str, ChunkIdentity] = {}

    def index(self, chunks: list[ChunkRecord]) -> int:
        """Index chunks; an older identity never replaces a newer one (K-05)."""
        by_id = {scoped_id(c.tenant_id, c.chunk_id): c for c in self._chunks}
        for ch in chunks:
            key = scoped_id(ch.tenant_id, ch.chunk_id)
            if admits(by_id, self._floors, (key, ch), stage=STAGE_FULLTEXT):
                by_id[key] = ch
        self._chunks = list(by_id.values())
        self._corpus_tokens = [tokenize(c.text) for c in self._chunks]
        self._bm25 = _SimpleBM25(self._corpus_tokens) if self._corpus_tokens else None
        return len(chunks)

    def search(
        self, query: str, top_k: int = 10, *, tenant_id: str | None = None
    ) -> list[tuple[ChunkRecord, float]]:
        if not self._bm25 or not self._chunks:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        chunks = [ch for ch in self._chunks if tenant_matches(ch.tenant_id, tenant_id)]
        bm25 = self._bm25 if tenant_id is None else _SimpleBM25([tokenize(c.text) for c in chunks])
        scored = list(zip(chunks, bm25.get_scores(tokens), strict=True))
        ranked = sorted(scored, key=lambda row: row[1], reverse=True)[:top_k]
        return [(chunk, float(score)) for chunk, score in ranked if score > 0]

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        before = len(self._chunks)
        self._chunks = [
            c
            for c in self._chunks
            if not (c.doc_id == doc_id and tenant_matches(c.tenant_id, tenant_id))
        ]
        self.index([])
        return before - len(self._chunks)

    def delete_chunk(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int:
        by_id = {scoped_id(c.tenant_id, c.chunk_id): c for c in self._chunks}
        scoped = scoped_id(key.tenant_id, key.chunk_id)
        removed = remove_below(by_id, self._floors, scoped, below=below, stage=STAGE_FULLTEXT)
        if removed:
            self._chunks = list(by_id.values())
            self.index([])
        return removed

    def clear(self) -> None:
        self.index_generation = uuid4().hex
        self._floors.clear()
        self._chunks.clear()
        self._corpus_tokens.clear()
        self._bm25 = None

    def save(self, path: str) -> None:
        import json
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "text": c.text,
                "index": c.index,
                "metadata": c.metadata,
                "tenant_id": c.tenant_id,
                "revision": c.revision,
                "stages": c.stages,
            }
            for c in self._chunks
        ]
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: str) -> int:
        import json
        from pathlib import Path

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        chunks = [
            ChunkRecord(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                text=item["text"],
                index=item.get("index", 0),
                metadata=item.get("metadata") or {},
                # Pre-V2 records belong to the default tenant, never every tenant.
                tenant_id=str(
                    item.get("tenant_id") or (item.get("metadata") or {}).get("tenant_id") or ""
                ),
                revision=int(item.get("revision") or 1),
                stages=dict(item.get("stages") or {}),
            )
            for item in data
        ]
        self.clear()
        return self.index(chunks)
