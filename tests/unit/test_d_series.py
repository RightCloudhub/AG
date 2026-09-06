"""D4 / D6 / D9: lexical reranker, heading-aware chunking, ablation knob."""

from __future__ import annotations

from agentic_graphrag.knowledge.ingest import chunk_text
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource
from agentic_graphrag.retrieval.fusion import IdentityReranker, LexicalReranker


def _cand(cid: str, content: str) -> Candidate:
    return Candidate(id=cid, source=CandidateSource.FUSION, content=content)


class TestD4LexicalReranker:
    def test_boosts_query_overlap(self) -> None:
        cands = [_cand("a", "quarterly revenue report"), _cand("b", "CEO of Apex Holdings")]
        reranked = LexicalReranker().rerank("Who is the CEO of Apex Holdings?", cands)
        assert reranked[0].id == "b"

    def test_stable_on_ties(self) -> None:
        cands = [_cand("a", "alpha"), _cand("b", "alpha"), _cand("c", "alpha")]
        reranked = LexicalReranker().rerank("alpha", cands)
        assert [c.id for c in reranked] == ["a", "b", "c"]

    def test_identity_default_unchanged(self) -> None:
        cands = [_cand("a", "x"), _cand("b", "CEO of Apex Holdings")]
        assert IdentityReranker().rerank("CEO of Apex Holdings", cands) == cands

    def test_config_maps_identity_to_none_behaviour(self) -> None:
        from agentic_graphrag.api.service_helpers import build_reranker

        assert type(build_reranker("lexical")).__name__ == "LexicalReranker"
        assert type(build_reranker("identity")).__name__ == "IdentityReranker"
        assert type(build_reranker(None)).__name__ == "IdentityReranker"


class TestD6HeadingChunking:
    def test_chunks_never_span_headings(self) -> None:
        text = "# Alpha\n" + "alpha body. " * 10 + "\n\n# Beta\n" + "beta body. " * 10
        chunks = chunk_text(text, chunk_size=200, overlap=0)
        assert len(chunks) >= 2
        assert any(c.startswith("# Alpha") for c in chunks)
        assert any(c.startswith("# Beta") for c in chunks)
        for c in chunks:
            body = c.removeprefix("# Alpha").removeprefix("# Beta")
            assert ("alpha body" not in body) or ("beta body" not in c)

    def test_heading_lines_are_kept(self) -> None:
        chunks = chunk_text("# Title\nbody text here", chunk_size=1200, overlap=150)
        assert chunks == ["# Title\nbody text here"]

    def test_headingless_text_unchanged(self) -> None:
        text = "A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500
        chunks = chunk_text(text, chunk_size=400, overlap=50)
        assert len(chunks) >= 2
        assert all(len(c) <= 400 + 10 for c in chunks)

    def test_large_section_falls_back_to_window(self) -> None:
        text = "# Big\n" + ("word " * 400)
        chunks = chunk_text(text, chunk_size=300, overlap=50)
        assert len(chunks) >= 2
        assert all(len(c) <= 300 + 10 for c in chunks)


class TestD9GraphToolKnob:
    def test_config_defaults_to_enabled(self) -> None:
        from agentic_graphrag.agent.executor import ExecutorConfig

        assert ExecutorConfig().enable_graph_tools is True

    def test_heuristic_drops_graph_specs_when_disabled(self) -> None:
        from agentic_graphrag.agent import executor_plan
        from agentic_graphrag.agent.executor import ExecutorConfig

        class FakeExecutor:
            config = ExecutorConfig(enable_graph_tools=False)

            def resolve_entities(self, _q, _hint):
                return ["Apex Holdings"]

            llm = None

        specs = executor_plan.build_heuristic(FakeExecutor(), "path from Apex to NovaTech", [])
        assert all(not s.tool.startswith("graph_") for s in specs)

    def test_heuristic_keeps_graph_specs_by_default(self) -> None:
        from agentic_graphrag.agent import executor_plan
        from agentic_graphrag.agent.executor import ExecutorConfig

        class FakeExecutor:
            config = ExecutorConfig()

            def resolve_entities(self, _q, _hint):
                return ["Apex Holdings"]

            llm = None

        specs = executor_plan.build_heuristic(FakeExecutor(), "path from Apex to NovaTech", [])
        assert any(s.tool.startswith("graph_") for s in specs)
