"""Create seven-role providers from a pinned, tenant-validated model profile."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.config_models import ModelRoutingConfig
from agentic_graphrag.core.models import ModelProfile, ModelRole
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.capability_provider import CapabilityProvider
from agentic_graphrag.llm.model_catalog import (
    catalog_fingerprint,
    embedding_space,
    provider_secret,
    validate_bindings,
)
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.llm.provider_options import ProviderOptions
from agentic_graphrag.llm.role_provider import RoleLLMProvider
from agentic_graphrag.llm.role_state import role_circuits


def build_role_provider(
    *,
    cfg: AppConfig,
    settings: Settings,
    profile: ModelProfile | None = None,
    tenant_id: str = "default",
    kb_id: str = "default",
    budget: BudgetTracker | None = None,
    cache_dir: str | Path | None = None,
) -> RoleLLMProvider:
    config = cfg.models
    roles = profile.roles if profile else config.roles
    if profile and profile.tenant_id != tenant_id:
        raise ValueError("Model profile does not belong to this tenant")
    validate_bindings(config, roles, tenant_id=tenant_id)
    fingerprint = catalog_fingerprint(config, roles)
    if profile and profile.catalog_fingerprint != fingerprint:
        raise ValueError("Model catalogue changed; publish a tested profile revision")
    revision = f"{profile.id}:{profile.revision}" if profile else config.profile_revision
    namespace = json.dumps([tenant_id, kb_id, revision, fingerprint, config.scope_key])
    secrets = {key: value.get_secret_value() for key, value in settings.model_secrets.items()}
    secrets.setdefault("LLM_API_KEY", settings.llm_api_key)
    clients: dict[ModelRole, LLMProvider] = {}
    for role, target_id in roles.items():
        if target_id is not None:
            clients[role] = target_client(
                config,
                target_id,
                secrets=secrets,
                namespace=namespace,
                budget=budget,
                cache_dir=cache_dir,
            )
            clients[role].circuits = role_circuits(clients[role].options.cache_namespace)
    embedding_id = roles[ModelRole.EMBEDDING]
    assert embedding_id is not None
    return RoleLLMProvider(clients, embedding_model=embedding_space(config, embedding_id))


def target_client(
    config: ModelRoutingConfig,
    target_id: str,
    *,
    secrets: dict[str, str],
    namespace: str,
    budget: BudgetTracker | None = None,
    cache_dir: str | Path | None = None,
) -> CapabilityProvider:
    target = config.catalog[target_id]
    provider = config.providers[target.provider]
    caps = target.capabilities
    client = CapabilityProvider(
        api_key=provider_secret(provider, secrets),
        base_url=provider.base_url,
        strong_model=target.model,
        light_model=target.model,
        embedding_model=target.model,
        budget=budget,
        cache_dir=cache_dir,
        timeout_seconds=provider.timeout_seconds,
        options=ProviderOptions(
            auth_required=provider.auth_required,
            embedding_auth_required=provider.auth_required,
            cache_namespace=json.dumps([namespace, target_id, target.revision]),
            structured_output=caps.structured_output,
            embedding_dimensions=caps.embedding_dimensions,
            send_temperature=caps.temperature,
        ),
    )
    client.capabilities = caps
    return client
