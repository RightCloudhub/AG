"""In-process BM25 fulltext store for POC (no Elasticsearch)."""

from __future__ import annotations

import math
import re
from collections import Counter

from agentic_graphrag.stores.interfaces import ChunkRecord

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
        self._chunks: list[ChunkRecord] = []
        self._corpus_tokens: list[list[str]] = []
        self._bm25: _SimpleBM25 | None = None

    def index(self, chunks: list[ChunkRecord]) -> int:
        by_id = {c.chunk_id: c for c in self._chunks}
        for ch in chunks:
            by_id[ch.chunk_id] = ch
        self._chunks = list(by_id.values())
        self._corpus_tokens = [tokenize(c.text) for c in self._chunks]
        self._bm25 = _SimpleBM25(self._corpus_tokens) if self._corpus_tokens else None
        return len(chunks)

    def search(self, query: str, top_k: int = 10) -> list[tuple[ChunkRecord, float]]:
        if not self._bm25 or not self._chunks:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self._chunks[i], float(s)) for i, s in ranked if s > 0]

    def clear(self) -> None:
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
            )
            for item in data
        ]
        return self.index(chunks)
