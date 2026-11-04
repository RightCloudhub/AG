"""Real provider boundary exercised with deterministic HTTP transports."""

import json
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from agentic_graphrag.bootstrap.profile import resolve_runtime_profile
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.config_models import ModelRoutingConfig, ModelTarget, ProviderConfig
from agentic_graphrag.core.models import ModelCapabilities, ModelProfile, ModelRole
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.capability_tests import test_profile as probe_profile
from agentic_graphrag.llm.http_transport import ProviderHttpPool
from agentic_graphrag.llm.model_catalog import catalog_fingerprint, embedding_space
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.llm.provider_options import ProviderOptions
from agentic_graphrag.llm.responses import CompletionRefused, CompletionTruncated, EmptyCompletion
from agentic_graphrag.llm.role_factory import build_role_provider
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.llm.structured import complete_structured


@pytest.fixture
def models():
    provider = ProviderConfig(
        base_url="http://localhost:11434/v1", auth_required=False, allowed_tenants=["alpha"]
    )
    targets = {
        role.value: ModelTarget(
            provider="local",
            model=f"model-{role}",
            capabilities=ModelCapabilities(
                completion=role not in {ModelRole.EMBEDDING, ModelRole.RERANK},
                embedding=role == ModelRole.EMBEDDING,
                embedding_dimensions=2 if role == ModelRole.EMBEDDING else None,
                rerank=role == ModelRole.RERANK,
                structured_output="json_schema",
            ),
        )
        for role in ModelRole
    }
    roles = {role: role.value for role in ModelRole}
    cfg = AppConfig(
        models=ModelRoutingConfig(providers={"local": provider}, catalog=targets, roles=roles)
    )
    profile = ModelProfile(
        id="balanced",
        tenant_id="alpha",
        name="Balanced",
        roles=roles,
        catalog_fingerprint=catalog_fingerprint(cfg.models, roles),
    )
    return cfg, Settings.model_construct(llm_api_key="", model_secrets={}), profile


@pytest.fixture
def http_probe(monkeypatch):
    requests = []
    clients = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("embeddings"):
            count = len(body["input"]) if isinstance(body["input"], list) else 1
            data = {
                "data": [
                    {"index": i, "embedding": [float(i), 1.0]} for i in reversed(range(count))
                ],
                "usage": {"total_tokens": 4},
            }
        elif request.url.path.endswith("rerank"):
            data = {
                "results": [
                    {"index": 1, "relevance_score": 0.1},
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        else:
            data = {
                "choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        return httpx.Response(200, json=data)

    def client(_self, base_url, _timeout):
        result = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))
        clients.append(result)
        return result

    monkeypatch.setattr(ProviderHttpPool, "client", client)
    yield requests
    for client in clients:
        client.close()


def test_seven_roles_are_independent_and_share_request_budget(models, http_probe):
    cfg, settings, profile = models
    budget = BudgetTracker()
    llm = build_role_provider(
        cfg=cfg,
        settings=settings,
        profile=profile,
        tenant_id="alpha",
        kb_id="contracts",
        budget=budget,
    )
    for role in set(ModelRole) - {ModelRole.EMBEDDING, ModelRole.RERANK}:
        for_role(llm, role).complete([Message(role="user", content="probe")])
    assert llm.embed_many(["one", "two"]) == [[0.0, 1.0], [1.0, 1.0]]
    assert llm.clients[ModelRole.RERANK].rerank("one", ["one", "two"]) == [0.9, 0.1]
    assert {json.loads(req.content)["model"] for req in http_probe} == {
        f"model-{role}" for role in ModelRole
    }
    assert all("Authorization" not in req.headers for req in http_probe)
    assert budget.llm_calls == 5 and budget.embed_calls == 2
    assert llm.for_verifier().strong_model == "model-verifier"
    assert llm.embedding_model == embedding_space(cfg.models, "embedding")
    with pytest.raises(ValueError, match="Embedding model"):
        llm.embed_for_model("one", "model-embedding")
    llm.close()


def test_role_profiles_do_not_change_legacy_clients():
    legacy = MockLLMProvider()
    assert for_role(legacy, ModelRole.WRITER) is legacy
    assert not resolve_runtime_profile({"AGR_ALLOW_LLM": "1"}, llm_api_key="").allow_llm
    assert resolve_runtime_profile(
        {"AGR_ALLOW_LLM": "1"}, llm_api_key="", llm_auth_required=False
    ).allow_llm
    assert not resolve_runtime_profile({}, llm_auth_required=False).allow_llm


def test_catalog_identity_rejects_cross_tenant_and_unreviewed_deploy_change(models):
    cfg, settings, profile = models
    with pytest.raises(ValueError, match="tenant"):
        build_role_provider(cfg=cfg, settings=settings, profile=profile, tenant_id="beta")
    cfg.models.catalog["researcher"] = cfg.models.catalog["researcher"].model_copy(
        update={"revision": "2"}
    )
    with pytest.raises(ValueError, match="catalogue changed"):
        build_role_provider(cfg=cfg, settings=settings, profile=profile, tenant_id="alpha")


def test_probe_uses_real_requests_and_does_not_just_list_models(models, http_probe):
    cfg, settings, profile = models
    report = probe_profile(profile, cfg, settings)
    assert report.passed
    assert len(http_probe) == 12
    assert all(not str(request.url).endswith("/models") for request in http_probe)
    assert report.catalog_fingerprint == profile.catalog_fingerprint
    assert sum(row.tokens for row in report.checks) == 54
    assert all(json.loads(req.content).get("max_tokens", 64) == 64 for req in http_probe)


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "embedding": [1, 2]}, {"index": 0, "embedding": [1, 2]}],
        [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [1]}],
        [{"index": 0, "embedding": [float("nan"), 2]}, {"index": 1, "embedding": [1, 2]}],
    ],
)
def test_embedding_batch_rejects_invalid_order_dimensions_or_values(rows, monkeypatch):
    llm = LLMProvider("key", options=ProviderOptions(embedding_dimensions=2))
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"data": rows})
    with pytest.raises(ValueError):
        llm.embed_many(["one", "two"])


def test_chat_cache_isolated_by_endpoint_and_scope(tmp_path, monkeypatch):
    calls = []

    def post(self, *_a, **_k):
        calls.append(self.base_url)
        return {"choices": [{"message": {"content": str(len(calls))}}]}

    monkeypatch.setattr(LLMProvider, "_post", post)
    message = [Message(role="user", content="same question")]
    a = LLMProvider("key", "http://a.test/v1", cache_dir=tmp_path)
    b = LLMProvider("key", "http://b.test/v1", cache_dir=tmp_path)
    c = LLMProvider(
        "key",
        "http://a.test/v1",
        cache_dir=tmp_path,
        options=ProviderOptions(cache_namespace="other-tenant"),
    )
    assert a.complete(message) == a.complete(message) == "1"
    assert b.complete(message) == "2"
    assert c.complete(message) == "3"


@pytest.mark.parametrize(
    "choice,error",
    [
        ({"message": {"refusal": "declined", "content": None}}, CompletionRefused),
        ({"message": {"content": "{"}, "finish_reason": "length"}, CompletionTruncated),
        ({"message": {"content": ""}}, EmptyCompletion),
    ],
)
def test_refusal_truncation_and_empty_are_not_json_repair_retries(choice, error, monkeypatch):
    calls = []
    llm = LLMProvider("key", budget=BudgetTracker())

    def post(*_a, **_k):
        calls.append(True)
        return {"choices": [choice], "usage": {"prompt_tokens": 4}}

    monkeypatch.setattr(llm, "_post", post)

    class Reply(BaseModel):
        value: str

    with pytest.raises(error):
        complete_structured(llm, [Message(role="user", content="q")], Reply)
    assert len(calls) == 1
    assert llm.budget.total_tokens == 4


def test_text_mode_and_unsupported_temperature_are_not_sent(http_probe):
    llm = LLMProvider(
        "",
        "http://localhost/v1",
        options=ProviderOptions(
            auth_required=False,
            send_temperature=False,
            structured_output="text",
        ),
    )

    class Reply(BaseModel):
        status: str

    assert complete_structured(llm, [Message(role="user", content="q")], Reply).status == "ok"
    body = json.loads(http_probe[0].content)
    assert "temperature" not in body and "response_format" not in body
    llm.options = replace(llm.options, structured_output="json_schema")
    complete_structured(llm, [Message(role="user", content="q")], Reply)
    schema = json.loads(http_probe[-1].content)["response_format"]["json_schema"]
    assert schema["strict"] and schema["schema"]["required"] == ["status"]
