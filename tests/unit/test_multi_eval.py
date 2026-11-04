"""Evaluation preserves failed rows, measured usage and configuration isolation."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "eval_multi_agent.py"
    spec = importlib.util.spec_from_file_location("eval_multi_agent", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_measured_tokens_unknown_prices_and_errors(runner):
    rows = [
        {
            "status": "answered",
            "gold": "Elena Varga",
            "prediction": "Elena Varga",
            "latency_ms": 10,
            "wall_latency_ms": 15,
            "cost": {"tokens": 150, "prompt_tokens": 100, "completion_tokens": 50},
            "metadata": {
                "budget_ledger": [
                    {"kind": "allocation", "role": "researcher", "token_limit": 1000},
                    {
                        "kind": "usage",
                        "role": "researcher",
                        "calls": 1,
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                    },
                ]
            },
        },
        {"status": "error", "error": "TimeoutError", "gold": "Elena Varga", "latency_ms": 100},
    ]
    result = runner.summarize(rows, "multi-3", offline=False)
    assert (result["correct"], result["total"], result["errors"]) == (1, 2, 1)
    assert result["cost_tokens_total"] == result["prompt_tokens"] + result["completion_tokens"]
    assert result["cost_tokens_total"] == 150
    assert result["cost_usd"] is None and result["tokens_complete"] is False
    assert result["roles"]["researcher"]["calls"] == 1
    assert result["status_counts"] == {"answered": 1, "error": 1}
    assert runner.summarize([], "offline", offline=True)["cost_usd"] == 0


def test_response_cache_never_substitutes_for_measured_requests(runner, monkeypatch, tmp_path):
    budget = BudgetTracker(max_llm_calls=10, max_tokens=10000)
    provider = LLMProvider("test", budget=budget, cache_dir=tmp_path)
    calls = []

    def post(*_args, **_kwargs):
        calls.append(True)
        return {
            "choices": [{"message": {"content": "fresh"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 2},
        }

    monkeypatch.setattr(provider, "_post", post)
    messages = [Message(role="user", content="hello")]
    provider.complete(messages)  # Pre-existing cache, as in a developer's worktree.
    paths = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert len(calls) == 1 and paths
    with runner.uncached_responses():
        provider.complete(messages)
        provider.complete(messages)
    assert len(calls) == 3 and budget.prompt_tokens == 60
    assert all(path.read_bytes() == data for path, data in paths.items())
    provider.complete(messages)  # Normal cache semantics restored after the run.
    assert len(calls) == 3


@pytest.mark.parametrize("startup_failure", [False, True])
def test_failure_stops_and_restores_configuration(runner, monkeypatch, tmp_path, startup_failure):
    original = AgentConfig()
    cfg = SimpleNamespace(agent=original)
    args = SimpleNamespace(no_llm=False, seed_triples=None, enable_triage=False, out=tmp_path)
    closed = []
    app = SimpleNamespace(allow_llm=True, close=lambda: closed.append(True))

    def build(**_kwargs):
        if startup_failure:
            raise RuntimeError("startup failed")
        return app

    monkeypatch.setattr(runner, "get_config", lambda: cfg)
    monkeypatch.setattr(runner, "build_eval_app", build)
    monkeypatch.setattr(
        runner, "run_case_row_via_app", lambda *_a, **_k: {"status": "error", "error": "timeout"}
    )
    message = "startup failed" if startup_failure else "three consecutive query errors"
    with pytest.raises(RuntimeError, match=message):
        runner.run_one(args, [{}] * 5, name="multi-3", parallel=3, seed=11)
    assert cfg.agent is original
    if not startup_failure:
        rows = [
            json.loads(line)
            for line in (tmp_path / "multi-3-seed-11.jsonl").read_text().splitlines()
        ]
        assert len(rows) == 3 and all(row["latency_ms"] >= 0 for row in rows)
        assert closed == [True]
