"""Query planning and cache metadata must use the same scope as retrieval."""

from agentic_graphrag.agent.entity_mentions import lexicon_from_names
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.ports.stores import EntityRecord
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.retrieval.cache import RetrievalCache


def test_empty_explicit_lexicon_does_not_load_seed():
    assert lexicon_from_names([]) == []
    assert lexicon_from_names(None)


def test_cache_keys_do_not_alias_delimiters_or_unscoped_reads():
    cache = RetrievalCache()
    assert cache.retrieval_key("q", tenant_id=None) != cache.retrieval_key("q", tenant_id="default")
    assert cache.retrieval_key("q", "tool", tenant_id="a|b") != cache.retrieval_key(
        "b|q", "tool", tenant_id="a"
    )
    assert cache.answer_key("q", tenant_id="a|b", user_id="c") != cache.answer_key(
        "q", tenant_id="a", user_id="b|c"
    )


def test_query_builds_tenant_local_entity_lexicon(monkeypatch):
    from agentic_graphrag.app import query_run

    svc = QueryService.create_offline()
    svc.bundle.graph.upsert_entities(
        [
            EntityRecord("owned", "Alpha Internal", "Entity", tenant_id="alpha"),
            EntityRecord("private", "Beta Internal", "Entity", tenant_id="beta"),
        ]
    )
    seen = []

    def run(_svc, req, **kwargs):
        seen.extend(kwargs["executor"].known_entities)
        return ReasoningChain(question=req.question, answer="test")

    monkeypatch.setattr(query_run, "run_agent_with_timeout", run)
    query_run._invoke_agent(svc, QueryRequest(question="question"), tenant_id="alpha", user_id="u")
    assert seen == ["Alpha Internal"]
    svc.close()
