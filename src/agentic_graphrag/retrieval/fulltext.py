"""BM25 fulltext retrieval (FR-RT-03)."""

from __future__ import annotations

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.stores.interfaces import FulltextStore


class FulltextRetriever:
    def __init__(self, store: FulltextStore, top_k: int = 10) -> None:
        self.store = store
        self.top_k = top_k

    def search(self, query: str, top_k: int | None = None) -> list[Candidate]:
        k = top_k or self.top_k
        hits = self.store.search(query, top_k=k)
        out: list[Candidate] = []
        for rank, (chunk, score) in enumerate(hits):
            out.append(
                Candidate(
                    id=chunk.chunk_id,
                    source=CandidateSource.FULLTEXT,
                    content=chunk.text,
                    score=float(score),
                    structured={"doc_id": chunk.doc_id, "index": chunk.index},
                    citations=[
                        Citation(
                            doc_id=chunk.doc_id, chunk_id=chunk.chunk_id, span=chunk.text[:200]
                        )
                    ],
                    metadata={"rank": rank},
                )
            )
        return out
