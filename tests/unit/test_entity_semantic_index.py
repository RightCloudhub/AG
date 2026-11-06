"""Entity vectors, aliases and mentions never share chunk namespaces or knowledge scopes."""

import pytest

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.entities import SemanticEntity
from agentic_graphrag.stores.entity_index import SQLiteEntityIndex


def entity(scope, *, entity_id="e1", vector=(1.0, 0.0)):
    return SemanticEntity(
        scope=scope,
        entity_id=entity_id,
        name="Alex",
        entity_type="Person",
        description="One particular Alex",
        aliases=("A.",),
        vector=vector,
    )


def source(scope):
    return SourceRef(scope=scope, doc_id="d", doc_version=1, chunk_id="c", content_sha256="a" * 64)


def test_two_tenants_two_knowledge_bases_and_model_spaces(tmp_path):
    index = SQLiteEntityIndex(tmp_path / "entity.sqlite3")
    scopes = [KnowledgeScope(tenant_id=t, kb_id=k) for t in ["t1", "t2"] for k in ["a", "b"]]
    for scope in scopes:
        index.upsert([entity(scope)], space="model:v1")
    for scope in scopes:
        matches = index.search([1, 0], scope=scope, space="model:v1")
        assert len(matches) == 1 and matches[0].entity.scope == scope
        assert index.aliases("A.", scope=scope, space="model:v1")[0].scope == scope
    assert index.search([1, 0], scope=scopes[0], space="model:v2") == []
    index.close()


def test_mentions_and_confirmed_aliases_survive_restart(tmp_path):
    path = tmp_path / "entity.sqlite3"
    scope = KnowledgeScope(tenant_id="t", kb_id="k")
    index = SQLiteEntityIndex(path)
    index.upsert([entity(scope)], space="v1")
    index.confirm_alias("  AL  ", "e1", scope=scope, space="v1")
    index.bind_mention("m1", "e1", source=source(scope), space="v1")
    index.close()
    restarted = SQLiteEntityIndex(path)
    assert restarted.confirmed_entity("al", scope=scope, space="v1") == "e1"
    assert restarted.mention("m1", scope=scope, space="v1") == "e1"
    restarted.upsert([entity(scope, entity_id="e2")], space="v1")
    restarted.confirm_alias("al", "e2", scope=scope, space="v1")
    assert restarted.confirmed_entity("al", scope=scope, space="v1") is None
    with pytest.raises(ValueError, match="explicit review"):
        restarted.bind_mention("m1", "e2", source=source(scope), space="v1")
    restarted.close()


def test_vector_dimension_and_finite_value_gates(tmp_path):
    index = SQLiteEntityIndex(tmp_path / "entity.sqlite3")
    scope = KnowledgeScope()
    index.upsert([entity(scope)], space="v1")
    with pytest.raises(ValueError, match="dimensions"):
        index.upsert([entity(scope, vector=(1.0,))], space="v1")
    with pytest.raises(ValueError, match="dimensions"):
        index.search([1], scope=scope, space="v1")
    with pytest.raises(ValueError):
        index.search([float("nan"), 0], scope=scope, space="v1")
    with pytest.raises(ValueError):
        index.upsert([entity(scope)], space="")
    with pytest.raises(KeyError):
        index.confirm_alias("x", "missing", scope=scope, space="v1")
    assert index.search([0, 0], scope=scope, space="v1")[0].score == 0
    index.close()
