"""Resolve one knowledge base's model choices without changing process globals."""

import json

from agentic_graphrag.app.model_profiles import ModelProfiles
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.llm.model_catalog import embedding_space, validate_bindings


def effective_model_config(
    cfg: AppConfig,
    settings: Settings,
    *,
    tenant_id: str,
    kb_id: str,
    profile_id: str | None = None,
    profile_revision: int | None = None,
) -> AppConfig:
    effective = cfg.model_copy(deep=True)
    effective.models.scope_key = json.dumps([tenant_id, kb_id])
    if profile_id:
        profiles = ModelProfiles(cfg, settings)
        profile = (
            profiles.revision(profile_id, tenant_id=tenant_id, revision=profile_revision)
            if profile_revision is not None
            else profiles.active(profile_id, tenant_id=tenant_id)
        )
        effective.models.roles = dict(profile.roles)
        effective.models.profile_revision = f"{profile.id}:{profile.revision}"
    if effective.models.roles:
        validate_bindings(effective.models, effective.models.roles, tenant_id=tenant_id)
        target_id = effective.models.roles[ModelRole.EMBEDDING]
        assert target_id is not None
        effective.llm.embedding_model = embedding_space(effective.models, target_id)
    return effective
