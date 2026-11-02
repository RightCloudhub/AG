from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.ingest import chunk_text, load_documents_from_dir
from agentic_graphrag.knowledge.tokenizer import get_tokenizer


def test_chunk_text_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_size=128, overlap=16)
    codec = get_tokenizer()
    assert len(chunks) >= 2
    assert all(codec.count(chunk) <= 128 for chunk in chunks)
    previous_body = chunks[0].removeprefix("Document\n\n")
    assert chunks[1].startswith("Document\n\n" + codec.suffix(previous_body, 16).strip())


def test_load_sample_docs():
    docs = load_documents_from_dir(resolve_path("data/raw"))
    assert len(docs) >= 6
    assert all(d.content for d in docs)
