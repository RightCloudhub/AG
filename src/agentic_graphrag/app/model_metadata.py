"""Record effective model identities without exposing provider credentials."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import PromptName
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.execution import current_execution

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def role_revision(svc: QueryService, role: ModelRole) -> str:
    target_id = svc.cfg.models.roles.get(role)
    if target_id:
        target = svc.cfg.models.catalog[target_id]
        payload: object = [
            target.model_dump(mode="json"),
            svc.cfg.models.providers[target.provider].model_dump(mode="json"),
        ]
    else:
        payload = [svc.cfg.llm.strong_model, svc.settings.llm_base_url]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def attach_model_metadata(svc: QueryService, chain: ReasoningChain) -> None:
    chain.metadata["model_profile_revision"] = svc.cfg.models.profile_revision
    chain.metadata["model_revisions"] = {role.value: role_revision(svc, role) for role in ModelRole}
    control = current_execution.get()
    llm = control.llm if control is not None else None
    usage = getattr(llm, "usage_by_role", None)
    if usage is not None:
        chain.metadata["model_usage"] = usage
    if chain.metadata.get("strategy") == "global":
        chain.metadata["prompt_versions"] = {
            name.value: hashlib.sha256(load_prompt(name).encode()).hexdigest()
            for name in (PromptName.GLOBAL_MAP, PromptName.GLOBAL_REDUCE, PromptName.GLOBAL_VERIFY)
        }
