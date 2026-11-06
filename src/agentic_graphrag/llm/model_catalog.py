"""Validate role bindings against deployment-owned, tenant-visible capabilities."""

from __future__ import annotations

import hashlib
import json

from agentic_graphrag.config_models import ModelRoutingConfig, ModelTarget, ProviderConfig
from agentic_graphrag.core.models import ModelRole

CHAT_ROLES = frozenset(
    {
        ModelRole.TRIAGE,
        ModelRole.RESEARCHER,
        ModelRole.VERIFIER,
        ModelRole.WRITER,
        ModelRole.EXTRACTOR,
    }
)
REQUIRED_ROLES = CHAT_ROLES | {ModelRole.EMBEDDING}


def visible_targets(config: ModelRoutingConfig, tenant_id: str) -> dict[str, ModelTarget]:
    return {
        key: target
        for key, target in config.catalog.items()
        if tenant_id in config.providers[target.provider].allowed_tenants
        or "*" in config.providers[target.provider].allowed_tenants
    }


def validate_bindings(
    config: ModelRoutingConfig, roles: dict[ModelRole, str | None], *, tenant_id: str
) -> None:
    visible = visible_targets(config, tenant_id)
    for role in ModelRole:
        key = roles.get(role)
        if key is None:
            if role in REQUIRED_ROLES:
                raise ValueError(f"Model role {role.value} requires a model")
            continue
        if key not in visible:
            raise ValueError("Model is not registered for this tenant")
        caps = visible[key].capabilities
        required = "completion" if role in CHAT_ROLES else role.value
        if not getattr(caps, required):
            raise ValueError(f"Model lacks the {required} capability for {role.value}")
        if role == ModelRole.EMBEDDING and caps.embedding_dimensions is None:
            raise ValueError("Embedding models must declare dimensions before creating an index")


def catalog_fingerprint(config: ModelRoutingConfig, roles: dict[ModelRole, str | None]) -> str:
    resolved = {}
    for key in sorted({value for value in roles.values() if value is not None}):
        target = config.catalog[key]
        resolved[key] = {
            "target": target.model_dump(mode="json"),
            "provider": config.providers[target.provider].model_dump(mode="json"),
        }
    raw = json.dumps(resolved, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def public_catalog(config: ModelRoutingConfig, tenant_id: str) -> list[dict[str, object]]:
    return [
        {
            "id": key,
            "provider": target.provider,
            "model": target.model,
            "revision": target.revision,
            "capabilities": target.capabilities.model_dump(mode="json"),
        }
        for key, target in visible_targets(config, tenant_id).items()
    ]


def provider_secret(provider: ProviderConfig, secrets: dict[str, str]) -> str:
    value = secrets.get(provider.secret_ref or "", "")
    if provider.auth_required and not value:
        raise ValueError("Configured provider credential is unavailable")
    return value


def embedding_space(config: ModelRoutingConfig, target_id: str) -> str:
    target = config.catalog[target_id]
    raw = json.dumps(
        {
            "endpoint": config.providers[target.provider].base_url,
            "model": target.model,
            "revision": target.revision,
            "dimensions": target.capabilities.embedding_dimensions,
            "normalization": "none",
        },
        sort_keys=True,
    )
    return "agemb:" + hashlib.sha256(raw.encode()).hexdigest()
