"""Refactor §0-C / §4.4: profiles + capability matrix, no silent fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_graphrag.bootstrap import (
    CAPABILITY_MATRIX,
    LLMMode,
    Profile,
    ProfileError,
    build_stores,
    missing_credentials,
    resolve_profile_name,
    resolve_runtime_profile,
)
from agentic_graphrag.stores.factory import GraphBackend, VectorBackend, create_live_bundle


def test_default_profile_is_offline() -> None:
    assert resolve_profile_name({}) is Profile.OFFLINE


def test_legacy_live_flag_maps_to_live() -> None:
    assert resolve_profile_name({"AGR_USE_LIVE_STORES": "1"}) is Profile.LIVE


def test_explicit_profile_wins_and_unknown_rejected() -> None:
    assert resolve_profile_name({"AGR_PROFILE": "hybrid", "AGR_USE_LIVE_STORES": "1"}) is (
        Profile.HYBRID
    )
    with pytest.raises(ProfileError):
        resolve_profile_name({"AGR_PROFILE": "turbo"})


def test_matrix_declares_hybrid_explicitly() -> None:
    hybrid = CAPABILITY_MATRIX[Profile.HYBRID]
    assert hybrid.graph is GraphBackend.NEO4J and hybrid.vector is VectorBackend.MEMORY
    live = CAPABILITY_MATRIX[Profile.LIVE]
    assert live.graph is GraphBackend.NEO4J and live.vector is VectorBackend.QDRANT


def test_eval_profile_disables_cache_and_is_deterministic() -> None:
    rp = resolve_runtime_profile({"AGR_PROFILE": "eval"})
    assert rp.llm_mode is LLMMode.OFF
    assert not rp.caps.answer_cache and rp.caps.deterministic


def test_profile_rejects_llm_mode_outside_matrix(monkeypatch) -> None:
    from dataclasses import replace

    from agentic_graphrag.bootstrap import profile as prof

    no_llm = replace(prof.CAPABILITY_MATRIX[Profile.EVAL], llm_modes=frozenset({LLMMode.OFF}))
    monkeypatch.setitem(prof.CAPABILITY_MATRIX, Profile.EVAL, no_llm)
    with pytest.raises(ProfileError):
        resolve_runtime_profile({"AGR_PROFILE": "eval", "AGR_ALLOW_LLM": "1"}, llm_api_key="k")


def test_llm_mode_needs_flag_and_real_key() -> None:
    env = {"AGR_ALLOW_LLM": "1"}
    assert resolve_runtime_profile(env, llm_api_key="").llm_mode is LLMMode.OFF
    assert resolve_runtime_profile(env, llm_api_key="your-key").llm_mode is LLMMode.OFF
    assert resolve_runtime_profile(env, llm_api_key="sk-x").allow_llm


def test_missing_credentials_per_profile() -> None:
    settings = MagicMock(neo4j_uri="", neo4j_user="u", neo4j_password="p", qdrant_url="")
    settings.qdrant_collection = "c"
    live = resolve_runtime_profile({"AGR_PROFILE": "live"})
    assert set(missing_credentials(live, settings)) == {"NEO4J_URI", "QDRANT_URL"}
    hybrid = resolve_runtime_profile({"AGR_PROFILE": "hybrid"})
    assert missing_credentials(hybrid, settings) == ["NEO4J_URI"]
    assert missing_credentials(resolve_runtime_profile({}), settings) == []


class _BrokenNeo4j:
    def __init__(self, *a, **k) -> None:
        pass

    def ping(self) -> None:
        raise ConnectionError("neo4j down")


def test_live_profile_fails_fast_instead_of_falling_back() -> None:
    rp = resolve_runtime_profile({"AGR_PROFILE": "live"})
    with patch("agentic_graphrag.stores.neo4j_store.Neo4jGraphStore", _BrokenNeo4j):
        with pytest.raises(ProfileError, match="could not open its stores"):
            build_stores(rp)


def test_live_bundle_rejects_implicit_fallback_flag() -> None:
    with pytest.raises(ValueError, match="fallback was removed"):
        create_live_bundle(allow_memory_graph_fallback=True)


def test_offline_profile_builds_memory_bundle() -> None:
    bundle = build_stores(resolve_runtime_profile({}))
    assert bundle.graph_backend is GraphBackend.MEMORY
    assert bundle.vector_backend is VectorBackend.MEMORY
    bundle.close()
