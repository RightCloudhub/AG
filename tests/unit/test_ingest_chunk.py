from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.ingest import chunk_text, load_documents_from_dir


def test_chunk_text_overlap():
    text = "A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500
    chunks = chunk_text(text, chunk_size=400, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) <= 400 + 10 for c in chunks)  # allow slight boundary slack via strip


def test_load_sample_docs():
    docs = load_documents_from_dir(resolve_path("data/raw"))
    assert len(docs) >= 6
    assert all(d.content for d in docs)
