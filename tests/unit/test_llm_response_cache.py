"""The LLM replay cache split out of the coverage-omitted live provider."""

from agentic_graphrag.llm.response_cache import cache_key, read_cache, write_cache


def test_cache_key_is_order_independent_and_kind_scoped():
    assert cache_key("chat", {"a": 1, "b": 2}) == cache_key("chat", {"b": 2, "a": 1})
    assert cache_key("chat", {"a": 1}) != cache_key("embed", {"a": 1})


def test_round_trip_and_disabled_cache(tmp_path):
    key = cache_key("embed", {"input": "文本"})
    assert read_cache(tmp_path, key) is None
    write_cache(tmp_path, key, {"embedding": [0.5, -1.0]})
    assert read_cache(tmp_path, key) == {"embedding": [0.5, -1.0]}
    write_cache(None, key, {"ignored": True})
    assert read_cache(None, key) is None
