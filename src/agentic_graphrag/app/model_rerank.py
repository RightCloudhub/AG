"""Adapt a profile's rerank role to the existing candidate fusion contract."""

from agentic_graphrag.config_rerank import RerankConfig
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.llm.capability_provider import CapabilityProvider
from agentic_graphrag.llm.role_provider import RoleLLMProvider
from agentic_graphrag.observability.degradation import ChannelHealth
from agentic_graphrag.retrieval.fusion import Reranker
from agentic_graphrag.retrieval.rerank_base import CrossEncoderReranker
from agentic_graphrag.retrieval.rerankers import build_reranker


class RoleReranker(CrossEncoderReranker):
    def __init__(
        self, client: CapabilityProvider, cfg: RerankConfig, *, health: ChannelHealth | None
    ) -> None:
        config = cfg.model_copy(update={"model": client.strong_model})
        super().__init__(config, health=health)
        self.client = client

    def _score(self, query: str, texts: list[str]) -> list[float]:
        self.stats.requests += 1
        return self.client.rerank(query, texts)


def build_model_reranker(
    cfg: RerankConfig, llm: LLMClient, *, health: ChannelHealth | None = None
) -> Reranker | None:
    if not isinstance(llm, RoleLLMProvider):
        return build_reranker(cfg, health=health)
    client = llm.clients.get(ModelRole.RERANK)
    if client is None:
        return None
    if not isinstance(client, CapabilityProvider):
        raise ValueError("Configured rerank model lacks a rerank adapter")
    return RoleReranker(client, cfg, health=health)
