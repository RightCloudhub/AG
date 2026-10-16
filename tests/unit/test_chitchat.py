"""Friendly replies for greetings and capability questions."""

from __future__ import annotations

from agentic_graphrag.agent.chitchat import ChitchatKind, match_chitchat, try_chitchat_answer
from agentic_graphrag.agent.executor import Executor, ExecutorConfig, ExecutorDeps
from agentic_graphrag.agent.loop import run_query
from agentic_graphrag.generation.trace import QueryStatus
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.factory import create_offline_bundle


def test_match_greetings() -> None:
    for q in ("你好", "你好！", "Hello", "hi", "早上好", "Hey!"):
        assert match_chitchat(q) == ChitchatKind.GREETING, q


def test_match_capability() -> None:
    for q in (
        "你可以回答什么？",
        "你能做什么",
        "What can you answer?",
        "what can you do",
        "help",
        "你是谁",
    ):
        assert match_chitchat(q) == ChitchatKind.CAPABILITY, q


def test_not_chitchat_for_real_questions() -> None:
    for q in (
        "Who is the CEO of Apex Holdings?",
        "Apex Holdings 的 CEO 是谁",
        "What is the parent company of NovaTech?",
    ):
        assert match_chitchat(q) is None, q


def test_try_chitchat_answer_content() -> None:
    chain = try_chitchat_answer("你好")
    assert chain is not None
    assert chain.status == QueryStatus.ANSWERED
    assert chain.route == "chitchat"
    assert "AgenticGraphRAG" in chain.answer

    cap = try_chitchat_answer("你可以回答什么？")
    assert cap is not None
    assert "多跳" in cap.answer or "CEO" in cap.answer


def test_run_query_short_circuits_chitchat() -> None:
    bundle = create_offline_bundle(load_bm25=False, load_embeddings=False)
    executor = Executor(
        graph=GraphRetriever(bundle.graph),
        deps=ExecutorDeps(fulltext=FulltextRetriever(bundle.fulltext)),
        config=ExecutorConfig(parallel=False),
    )
    chain = run_query("hello", executor, None, allow_llm=False)
    assert chain.route == "chitchat"
    assert chain.status == QueryStatus.ANSWERED
    assert "无法基于现有知识" not in chain.answer
    bundle.close()
