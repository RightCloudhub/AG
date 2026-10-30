"""The live test harness validates requests and reports without real network calls."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentic_graphrag.config import AppConfig, Settings, resolve_path

_SCRIPT = resolve_path("scripts/online_eval.py")
_spec = importlib.util.spec_from_file_location("online_eval", _SCRIPT)
assert _spec and _spec.loader
online_eval = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = online_eval
_spec.loader.exec_module(online_eval)
_CLIENT = httpx.Client


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        LLM_API_KEY="offline-test-credential",
        LLM_BASE_URL="https://offline.test/v1",
        LLM_STRONG_MODEL="test-model",
    )


def mock_chat(monkeypatch: pytest.MonkeyPatch, *, status: int = 200) -> list[httpx.Request]:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer offline-test-credential"
        payload = json.loads(request.content)
        content = {"ok": True, "answer": "4"}
        if len(payload["messages"]) > 1:
            content = {
                "sub_questions": [],
                "entities": [],
                "claims": [],
                "answer": "Cannot answer using available evidence",
                "status": "no_answer",
                "action": "give_up",
                "scope": "global",
                "rationale": "Evidence is insufficient",
                "evidence_ids": [],
                "sub_answered": False,
                "global_answered": False,
            }
        return httpx.Response(
            status,
            json={
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    def client(**kwargs: Any) -> httpx.Client:
        return _CLIENT(**kwargs, transport=httpx.MockTransport(handle), trust_env=False)

    monkeypatch.setattr(httpx, "Client", client)
    return requests


def test_config_uses_one_model_and_keeps_shared_config_unchanged(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    cfg = AppConfig()
    monkeypatch.setattr(online_eval, "Settings", lambda **kwargs: settings)
    monkeypatch.setattr(online_eval, "get_config", lambda: cfg)
    defaults = online_eval.TestDefaults(
        base_url="https://default.test/v1",
        limit=5,
        cases="unused",
        seed_triples="unused",
        prompt="unused",
    )
    selected, configured = online_eval.configure(
        online_eval.parse_args(["--model", "chosen"]), defaults
    )
    assert selected.llm_base_url == "https://offline.test/v1"
    assert configured.llm.strong_model == configured.llm.light_model == "chosen"
    assert cfg.llm.strong_model == "gpt-4.1"


def test_default_endpoint_requires_only_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None, LLM_API_KEY="offline-test-credential", LLM_STRONG_MODEL="test-model"
    )
    monkeypatch.setattr(online_eval, "Settings", lambda **kwargs: settings)
    defaults = online_eval.TestDefaults(
        base_url="https://gateway.test/v1",
        limit=5,
        cases="unused",
        seed_triples="unused",
        prompt="unused",
    )
    selected, _cfg = online_eval.configure(online_eval.parse_args([]), defaults)
    assert selected.llm_base_url == "https://gateway.test/v1"


@pytest.mark.parametrize("status,expected_calls,passed", [(200, 2, True), (401, 1, False)])
def test_preflight_uses_actual_provider_and_checks_json(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    status: int,
    expected_calls: int,
    passed: bool,
) -> None:
    requests = mock_chat(monkeypatch, status=status)
    cfg = AppConfig()
    cfg.llm.strong_model = cfg.llm.light_model = "test-model"
    result = online_eval.preflight(
        settings, cfg, resolve_path("configs/prompts/online_llm_test.md")
    )
    assert result["passed"] is passed
    assert len(requests) == expected_calls
    assert all(json.loads(request.content)["model"] == "test-model" for request in requests)
    if passed:
        assert "response_format" not in json.loads(requests[0].content)
        assert json.loads(requests[1].content)["response_format"] == {"type": "json_object"}
        assert result["usage"]["total_tokens"] == 20
    else:
        assert result["usage"]["llm_calls"] == 0
        assert "401" in result["checks"][0]["error"]


def test_invalid_json_response_fails_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProvider:
        def complete(self, *args: Any, **kwargs: Any) -> str:
            return '{"ok":true,"answer":"5"}'

    monkeypatch.setattr(online_eval, "build_llm_provider", lambda **kwargs: FakeProvider())
    result = online_eval.preflight(
        Settings(_env_file=None), AppConfig(), resolve_path("configs/prompts/online_llm_test.md")
    )
    assert result["passed"] is False
    assert result["checks"][0]["passed"] is True
    assert result["checks"][1]["passed"] is False


def test_sampling_spans_categories_and_zero_means_all(tmp_path: Path) -> None:
    source = tmp_path / "cases.jsonl"
    cases = [
        {"id": str(index), "question": "Who?", "gold_answer": "Elena", "hops": hops}
        for index, hops in enumerate([2, 2, 2, 3, 3, 0])
    ]
    source.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    sampled = online_eval.sample_cases(source, 3)
    assert {case["hops"] for case in sampled} == {0, 2, 3}
    assert len(online_eval.sample_cases(source, 0)) == 6


@pytest.mark.parametrize("online,status", [(False, 200), (True, 200), (True, 401)])
def test_project_batch_isolates_state_and_never_calls_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: Settings,
    online: bool,
    status: int,
) -> None:
    requests = mock_chat(monkeypatch, status=status)
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "existing-runtime"))
    cases = online_eval.sample_cases(resolve_path("evals/datasets/poc_cases.jsonl"), 1)
    cfg = AppConfig()
    cfg.llm.max_retries = 0
    result = online_eval.run_batch(
        cases,
        settings=settings,
        cfg=cfg,
        seed=resolve_path("data/processed/seed_triples.jsonl"),
        out=tmp_path,
        online=online,
    )
    assert result["accuracy"]["total"] == 1
    assert not (tmp_path / "existing-runtime").exists()
    assert os.environ["AGR_RUNTIME_DIR"] == str(tmp_path / "existing-runtime")
    if not online:
        assert requests == []
        assert result["llm_calls"] == 0
        assert result["accuracy"]["correct"] == 1
    elif status == 200:
        assert requests
        assert result["llm_calls"] > 0
        assert result["passed"] is True
    else:
        assert requests
        assert result["llm_calls"] == 0
        assert result["passed"] is False


def test_report_redacts_credentials(tmp_path: Path, settings: Settings) -> None:
    summary = {
        "model": "test-model",
        "base_url": "https://offline.test/v1",
        "passed": False,
        "error": settings.llm_api_key,
    }
    online_eval.write_report(tmp_path, summary, settings.llm_api_key)
    assert settings.llm_api_key not in (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert "[REDACTED]" in (tmp_path / "summary.json").read_text(encoding="utf-8")
