"""Shared offline fixtures for the document-operations tests (no network, no keys)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from agentic_graphrag.core.ports.document_ops import ChunkPatch
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.document_commands import DocumentCommands
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.ingest_worker import IngestWorker, IngestWorkerConfig
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.vector_store import InMemoryVectorStore

FACT = re.compile(r"(\w+) PRODUCES (\w+)")
CONTENT = (
    "# Alpha\n\nAcme PRODUCES Widget and Beta PRODUCES Gadget.\n\n# Beta\n\nBeta PRODUCES Gadget.\n"
)


class FixtureExtractor(GraphIngestor):
    """Deterministic extractor: every ``X PRODUCES Y`` sentence is one triple."""

    def _extract_chunk(self, chunk, result):  # noqa: ANN001 — test double
        return [
            Triple(
                head=EntityMention(name=head, type="Company"),
                relation="PRODUCES",
                tail=EntityMention(name=tail, type="Product"),
                confidence=0.99,
            )
            for head, tail in FACT.findall(chunk.text)
        ]


@dataclass
class FixtureEmbedder:
    calls: list[list[str]] = field(default_factory=list)
    hook: object = None

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if callable(self.hook):
            self.hook()
        return [_vector(t) for t in texts]


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 + 0.01 for b in digest[:8]]


@dataclass
class Env:
    docs: FileDocStore
    tasks: IngestTaskStore
    vector: InMemoryVectorStore
    fulltext: BM25FulltextStore
    graph: InMemoryGraphStore
    embedder: FixtureEmbedder
    worker: IngestWorker
    commands: DocumentCommands

    def new_worker(self, worker_id: str, **config: object) -> IngestWorker:
        return build_worker(self, worker_id, **config)

    def upload(self, content: str = CONTENT, *, name: str = "guide.md") -> str:
        row = self.worker.pipeline.lifecycle.accept(
            DocumentRecord("", name, content, tenant_id="alpha", source_uri=f"upload://{name}")
        )
        self.tasks.create("alpha", [row])
        self.worker.run_once()
        return row["doc_id"]

    def patch(self, doc_id: str, chunk: int, *, key: str, **changes: object):
        current = self.docs.get(doc_id, tenant_id="alpha").chunks[chunk]
        patch = ChunkPatch(
            "alpha",
            doc_id,
            current.chunk_id,
            int(changes.pop("expected_revision", current.revision)),
            **changes,
        )
        return self.commands.patch_chunk(patch, key=key, respond=respond)

    def relations(self) -> dict[tuple[str, str], list[str]]:
        return {
            (r.head_name, r.tail_name): sorted(s["chunk_id"] for s in r.sources)
            for r in self.graph.list_relations()
        }

    def search(self, text: str) -> list[str]:
        return [c.text for c, _ in self.fulltext.search(text, tenant_id="alpha")]


def respond(chunk, op):  # noqa: ANN001
    return {"chunk_id": chunk.chunk_id, "revision": chunk.revision, "task_id": op.task_id}


def build_worker(env: Env, worker_id: str, **config: object) -> IngestWorker:
    settings = {"embedding_model": "fixture", "retry_base_seconds": 0, **config}
    return IngestWorker(
        doc_store=env.docs,
        vector_store=env.vector,
        fulltext_store=env.fulltext,
        task_store=env.tasks,
        embed_many=env.embedder,
        graph_ingestor=FixtureExtractor(GraphIngestDeps(graph=env.graph)),
        config=IngestWorkerConfig(**settings),
        worker_id=worker_id,
    )


def make_env(tmp_path, **config: object) -> Env:
    docs = FileDocStore(tmp_path / "docs")
    tasks = IngestTaskStore(tmp_path / "tasks.jsonl")
    env = Env(
        docs,
        tasks,
        InMemoryVectorStore(),
        BM25FulltextStore(),
        InMemoryGraphStore(),
        FixtureEmbedder(),
        None,  # type: ignore[arg-type]
        DocumentCommands(docs, tasks),
    )
    env.worker = build_worker(env, "worker-main", **config)
    return env
