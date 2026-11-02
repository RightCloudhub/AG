"""Token budgets, hierarchy, and indivisible Markdown structures."""

from pathlib import Path

import pytest

from agentic_graphrag.config_knowledge import KnowledgeConfig
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.chunk_splitting import ChunkingError
from agentic_graphrag.knowledge.chunking import chunk_markdown
from agentic_graphrag.knowledge.ingest import chunk_document, chunk_text
from agentic_graphrag.knowledge.tokenizer import get_tokenizer


@pytest.mark.parametrize(
    "sentence",
    [
        "The research team publishes annual results. Revenue grows! What changes? Read more; ",
        "研究团队公布年度成果。收入持续增长！发生了什么变化？请继续阅读；",
    ],
)
def test_multilingual_token_budget_includes_breadcrumb(sentence: str) -> None:
    doc = DocumentRecord(
        "stable-id",
        "Annual report",
        "# Research\n## Findings\n\n" + sentence * 200,
        tenant_id="acme",
    )
    chunks = chunk_document(doc, chunk_size=128, overlap=16)
    codec = get_tokenizer()
    assert len(chunks) > 2
    for index, chunk in enumerate(chunks):
        assert codec.count(chunk.text) <= 128
        assert chunk.metadata["token_count"] == codec.count(chunk.text)
        assert chunk.chunk_id == f"stable-id:{index}"
        assert chunk.doc_id == "stable-id" and chunk.tenant_id == "acme"
        assert chunk.metadata["heading_path"] == ["Research", "Findings"]
        assert chunk.metadata["breadcrumb"] == "Annual report > Research > Findings"
        assert chunk.text.startswith(chunk.metadata["breadcrumb"] + "\n\n")


def test_heading_hierarchy_and_setext_headings() -> None:
    text = (
        "Preamble.\n\n# First\nFirst body.\n\n### Deep\nDeep body.\n\n"
        "## Sibling\nSibling body.\n\nSecond\n======\nSecond body."
    )
    chunks = chunk_markdown(text, title="Doc", chunk_size=64, overlap=8)
    assert [chunk.heading_path for chunk in chunks] == [
        (),
        ("First",),
        ("First", "Deep"),
        ("First", "Sibling"),
        ("Second",),
    ]
    assert all(chunk.breadcrumb and chunk.text.startswith(chunk.breadcrumb) for chunk in chunks)
    assert all("First body" not in c.text for c in chunks[2:])


def test_large_table_splits_only_between_rows_and_repeats_header() -> None:
    header = "| Item | Description |\n| --- | --- |"
    rows = [f"| item-{i} | revenue {i} \\| value |" for i in range(40)]
    chunks = chunk_markdown(
        "# Results\n\n" + header + "\n" + "\n".join(rows), title="Doc", chunk_size=80, overlap=16
    )
    assert len(chunks) > 1
    observed = []
    for chunk in chunks:
        body = chunk.text.removeprefix(chunk.breadcrumb + "\n\n")
        assert body.startswith(header + "\n")
        observed.extend(body.splitlines()[2:])
        assert chunk.token_count <= 80
    assert observed == rows


def test_small_table_remains_whole() -> None:
    table = "| Name | Value |\n| --- | --- |\n| Alpha | 12 |\n| Beta | 24 |"
    chunks = chunk_markdown("Before.\n\n" + table + "\n\nAfter.", chunk_size=128)
    assert len(chunks) == 1 and table in chunks[0].text


def test_embedded_html_tables_are_normalized_before_row_chunking() -> None:
    table = "<table><tr><th>Year</th><th>Revenue</th></tr>"
    table += "".join(f"<tr><td>{year}</td><td>{year * 10}</td></tr>" for year in range(2000, 2020))
    table += "</table>"
    chunks = chunk_markdown("# Report\n\n" + table, chunk_size=64, overlap=8)
    header = "| Year | Revenue |\n| --- | --- |"
    assert len(chunks) > 1
    assert all(chunk.text.split("\n\n", 1)[1].startswith(header) for chunk in chunks)
    assert all(chunk.token_count <= 64 for chunk in chunks)


def test_html_table_example_inside_a_fence_is_not_converted() -> None:
    code = "```html\n<table><tr><td>example</td></tr></table>\n```"
    assert code in chunk_markdown(code, chunk_size=64, overlap=8)[0].text


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_code_is_intact_and_its_headings_are_not_sections(fence: str) -> None:
    code = f"{fence}python\n# fake heading\n\ndef compute():\n    return 42\n{fence}"
    text = (
        "# Real\n\n"
        + "A paragraph of prose. " * 50
        + "\n\n"
        + code
        + "\n\n"
        + "After the code. " * 50
    )
    chunks = chunk_markdown(text, title="Guide", chunk_size=64, overlap=8)
    assert sum(code in chunk.text for chunk in chunks) == 1
    assert all(chunk.heading_path == ("Real",) for chunk in chunks)
    assert all(chunk.token_count <= 64 for chunk in chunks)


def test_indented_code_and_list_items_are_preserved() -> None:
    code = "    first = 1\n    second = 2"
    rows = [f"- List item {i} with its complete detail." for i in range(40)]
    chunks = chunk_markdown(code + "\n\n" + "\n".join(rows), chunk_size=64, overlap=8)
    assert sum(code in chunk.text for chunk in chunks) == 1
    assert all(sum(row in chunk.text for chunk in chunks) == 1 for row in rows)
    assert all(chunk.token_count <= 64 for chunk in chunks)


@pytest.mark.parametrize(
    "text",
    [
        "```\n" + "a code line\n" * 100 + "```",
        "| Name | Value |\n| --- | --- |\n| oversized | " + "value " * 100 + " |",
    ],
)
def test_oversized_atomic_structures_fail_without_splitting(text: str) -> None:
    with pytest.raises(ChunkingError, match="increase"):
        chunk_markdown(text, chunk_size=32, overlap=4)


def test_unicode_token_fallback_does_not_drop_or_replace_characters() -> None:
    text = "龘😀𠀀数据🧬" * 40
    chunks = chunk_markdown(text, title="Doc", chunk_size=32, overlap=0)
    bodies = [chunk.text.removeprefix(chunk.breadcrumb + "\n\n") for chunk in chunks]
    assert "".join(body.replace("\n\n", "") for body in bodies) == text
    assert all(chunk.token_count <= 32 for chunk in chunks)
    assert all("\ufffd" not in chunk.text for chunk in chunks)


def test_bundled_tokenizer_never_needs_network_or_cache(monkeypatch, tmp_path: Path) -> None:
    import requests

    def no_network(*args, **kwargs):
        raise AssertionError("Tokenizer attempted a download")

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(requests, "get", no_network)
    get_tokenizer.cache_clear()
    codec = get_tokenizer()
    assert codec.encoder.encode_ordinary("hello world") == [15339, 1917]
    assert codec.count("<|endoftext|>") > 1
    assert not (tmp_path / "absent").exists()


def test_local_tokenizer_definition(tmp_path: Path) -> None:
    import base64
    import json

    (tmp_path / "bytes.tiktoken").write_text(
        "\n".join(f"{base64.b64encode(bytes([i])).decode()} {i}" for i in range(256)),
        encoding="ascii",
    )
    definition = tmp_path / "bytes.json"
    definition.write_text(
        json.dumps({"name": "bytes", "pat_str": "(?s).+", "ranks_path": "bytes.tiktoken"}),
        encoding="utf-8",
    )
    chunks = chunk_markdown(
        "你好 world. " * 20, chunk_size=48, overlap=4, tokenizer=str(definition)
    )
    assert all(chunk.tokenizer == "bytes" and chunk.token_count <= 48 for chunk in chunks)


def test_empty_heading_only_and_invalid_budgets() -> None:
    assert chunk_text(" \n\n") == []
    assert chunk_markdown("# Heading", title="Doc")[0].text == "Doc > Heading"
    with pytest.raises(ValueError, match="positive"):
        chunk_text("text", chunk_size=0)
    with pytest.raises(ChunkingError, match="Breadcrumb"):
        chunk_markdown("body", title="A very long title " * 10, chunk_size=8)
    with pytest.raises(ValueError, match="smaller"):
        KnowledgeConfig(chunk_size_tokens=32, chunk_overlap_tokens=32)
    with pytest.raises(ValueError, match="chunk_size_chars"):
        KnowledgeConfig.model_validate({"chunk_size_chars": 1200})
