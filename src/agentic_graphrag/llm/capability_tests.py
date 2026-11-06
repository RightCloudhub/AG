"""Small bounded requests validate configured capabilities without business text."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel

from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.enums import MessageRole
from agentic_graphrag.core.models import CapabilityCheck, ModelProfile, ModelProfileTest, ModelRole
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.capability_provider import CapabilityProvider
from agentic_graphrag.llm.model_catalog import catalog_fingerprint, validate_bindings
from agentic_graphrag.llm.role_factory import target_client
from agentic_graphrag.llm.structured import complete_structured

TEST_TIMEOUT_SECONDS = 5.0
TEST_OUTPUT_TOKENS = 64
TEST_MAX_CALLS = 16
TEST_MAX_TOKENS = 4096


class _ProbeReply(BaseModel):
    status: Literal["ok"]


def test_profile(profile: ModelProfile, cfg: AppConfig, settings: Settings) -> ModelProfileTest:
    validate_bindings(cfg.models, profile.roles, tenant_id=profile.tenant_id)
    report = ModelProfileTest(
        profile_id=profile.id,
        revision=profile.revision,
        catalog_fingerprint=catalog_fingerprint(cfg.models, profile.roles),
        tested_at=datetime.now(UTC).isoformat(),
    )
    budget = BudgetTracker(max_llm_calls=TEST_MAX_CALLS, max_tokens=TEST_MAX_TOKENS)
    secrets = {key: value.get_secret_value() for key, value in settings.model_secrets.items()}
    secrets.setdefault("LLM_API_KEY", settings.llm_api_key)
    for role, target_id in profile.roles.items():
        if target_id is None:
            report.checks.append(
                CapabilityCheck(role=role, model_id="", capability=role, status="disabled")
            )
            continue
        try:
            client = target_client(
                cfg.models, target_id, secrets=secrets, namespace="capability-test", budget=budget
            )
        except ValueError:
            report.checks.append(
                CapabilityCheck(
                    role=role,
                    model_id=target_id,
                    capability="configuration",
                    status="failed",
                    error_code="configuration",
                )
            )
            continue
        try:
            client.timeout_seconds = min(client.timeout_seconds, TEST_TIMEOUT_SECONDS)
            client.options = replace(client.options, max_output_tokens=TEST_OUTPUT_TOKENS)
            report.checks.extend(_run_checks(client, role=role, target_id=target_id))
        finally:
            client.close()
    return report


def _run_checks(
    client: CapabilityProvider, *, role: ModelRole, target_id: str
) -> list[CapabilityCheck]:
    capabilities = (
        [role.value]
        if role in {ModelRole.EMBEDDING, ModelRole.RERANK}
        else [
            "completion",
            client.options.structured_output,
        ]
    )
    rows = []
    for capability in capabilities:
        before = client.budget.total_tokens if client.budget else 0
        started = time.monotonic()
        error = ""
        try:
            _probe(client, capability)
        except Exception as exc:
            error = _error_code(exc)
        tokens = client.budget.total_tokens - before if client.budget else 0
        rows.append(
            CapabilityCheck(
                role=role,
                model_id=target_id,
                capability=capability,
                status="failed" if error else "passed",
                error_code=error,
                latency_ms=round((time.monotonic() - started) * 1000),
                tokens=tokens,
            )
        )
    return rows


def _probe(client: CapabilityProvider, capability: str) -> None:
    if capability == "embedding":
        client.embed_many(["A small green tree.", "A blue cup."])
    elif capability == "rerank":
        client.rerank("green tree", ["A small green tree.", "A blue cup."])
    elif capability == "completion":
        client.complete([Message(role=MessageRole.USER, content="Reply with ok.")])
    else:
        complete_structured(
            client,
            [
                Message(role=MessageRole.USER, content='Reply with JSON: {"status":"ok"}'),
            ],
            _ProbeReply,
            max_retries=0,
        )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return {401: "authentication", 403: "authentication", 429: "rate_limited"}.get(
            exc.response.status_code, "upstream_error"
        )
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "invalid_response"
    return "capability_unavailable"
