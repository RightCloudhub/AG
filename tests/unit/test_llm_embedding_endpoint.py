"""Chat vs embedding endpoint split (LLM_EMBEDDING_BASE_URL)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_graphrag.config import (
    AppConfig,
    Settings,
    resolve_chat_base_url,
    resolve_embedding_api_key,
    resolve_embedding_base_url,
)
from agentic_graphrag.llm.provider import LLMProvider


def test_resolve_embedding_falls_back_to_chat():
    # model_construct: ignore .env so local keys do not leak into unit tests
    s = Settings.model_construct(
        llm_api_key="k1",
        llm_base_url="https://chat.example/v1",
        llm_embedding_base_url=None,
        llm_embedding_api_key=None,
    )
    assert resolve_chat_base_url(s) == "https://chat.example/v1"
    assert resolve_embedding_base_url(s, AppConfig()) == "https://chat.example/v1"
    assert resolve_embedding_api_key(s, AppConfig()) == "k1"


def test_resolve_embedding_separate_endpoint():
    s = Settings.model_construct(
        llm_api_key="chat-key",
        llm_base_url="https://chat.example/v1",
        llm_embedding_base_url="https://embed.example/v1",
        llm_embedding_api_key="embed-key",
        llm_embedding_model="bge-m3",
    )
    cfg = AppConfig()
    assert resolve_embedding_base_url(s, cfg) == "https://embed.example/v1"
    assert resolve_embedding_api_key(s, cfg) == "embed-key"


def test_provider_embed_uses_embedding_base_url():
    llm = LLMProvider(
        api_key="chat-key",
        base_url="https://chat.example/v1",
        embedding_base_url="https://embed.example/v1",
        embedding_api_key="embed-key",
        embedding_model="text-embedding-3-small",
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"embedding": [0.1, 0.2, 0.3]}],
        "usage": {"total_tokens": 3},
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    client_path = "agentic_graphrag.llm.provider.httpx.Client"
    with patch(client_path, return_value=mock_client) as client_cls:
        vec = llm.embed("hello")

    assert vec == [0.1, 0.2, 0.3]
    client_cls.assert_called()
    # base_url kw to Client must be embedding endpoint
    kwargs = client_cls.call_args.kwargs
    assert kwargs.get("base_url") == "https://embed.example/v1"
    post_kwargs = mock_client.post.call_args
    assert post_kwargs.args[0] == "/embeddings"
    headers = post_kwargs.kwargs["headers"]
    assert headers["Authorization"] == "Bearer embed-key"


def test_provider_complete_uses_chat_base_url():
    llm = LLMProvider(
        api_key="chat-key",
        base_url="https://chat.example/v1",
        embedding_base_url="https://embed.example/v1",
        embedding_api_key="embed-key",
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    from agentic_graphrag.llm.provider import Message

    client_path = "agentic_graphrag.llm.provider.httpx.Client"
    with patch(client_path, return_value=mock_client) as client_cls:
        out = llm.complete([Message(role="user", content="hi")])

    assert out == "ok"
    assert client_cls.call_args.kwargs.get("base_url") == "https://chat.example/v1"
    assert mock_client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer chat-key"
