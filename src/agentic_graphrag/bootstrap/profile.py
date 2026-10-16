"""Runtime profiles and the capability matrix (refactor §0-C / §4.4).

A *profile* names one legal combination of backends. Startup resolves exactly
one profile, validates it against the capability matrix and the environment,
and refuses to start on an invalid combination. There is no implicit fallback:
"Neo4j graph + in-memory vectors" is only allowed when it is declared as the
``hybrid`` profile.

Resolution order:

1. ``AGR_PROFILE`` (``offline`` | ``live`` | ``hybrid`` | ``eval``)
2. legacy flags: ``AGR_USE_LIVE_STORES=1`` → ``live``; otherwise ``offline``

``AGR_ALLOW_LLM`` stays orthogonal for profiles whose matrix allows a live LLM.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from agentic_graphrag.stores.factory import GraphBackend, VectorBackend

_TRUTHY = {"1", "true", "yes", "on"}


class Profile(StrEnum):
    OFFLINE = "offline"
    LIVE = "live"
    HYBRID = "hybrid"
    EVAL = "eval"


class LLMMode(StrEnum):
    OFF = "off"  # MockLLM / rule-based only
    LIVE = "live"  # real provider (requires a key)


class ProfileError(RuntimeError):
    """Raised when a profile is unknown or the environment cannot satisfy it."""


@dataclass(frozen=True)
class Capabilities:
    """What a profile wires. Every field is an explicit decision, never a fallback."""

    graph: GraphBackend
    vector: VectorBackend
    llm_modes: frozenset[LLMMode]
    answer_cache: bool
    retrieval_cache: bool
    load_seed_graph: bool
    deterministic: bool = False


CAPABILITY_MATRIX: Mapping[Profile, Capabilities] = {
    Profile.OFFLINE: Capabilities(
        graph=GraphBackend.MEMORY,
        vector=VectorBackend.MEMORY,
        llm_modes=frozenset({LLMMode.OFF, LLMMode.LIVE}),
        answer_cache=True,
        retrieval_cache=True,
        load_seed_graph=True,
    ),
    Profile.LIVE: Capabilities(
        graph=GraphBackend.NEO4J,
        vector=VectorBackend.QDRANT,
        llm_modes=frozenset({LLMMode.OFF, LLMMode.LIVE}),
        answer_cache=True,
        retrieval_cache=True,
        load_seed_graph=False,
    ),
    Profile.HYBRID: Capabilities(
        graph=GraphBackend.NEO4J,
        vector=VectorBackend.MEMORY,
        llm_modes=frozenset({LLMMode.OFF, LLMMode.LIVE}),
        answer_cache=True,
        retrieval_cache=True,
        load_seed_graph=False,
    ),
    # Eval: production wiring, caches off, seed graph; a live LLM is allowed but
    # replays from the provider's on-disk response cache (record/replay).
    Profile.EVAL: Capabilities(
        graph=GraphBackend.MEMORY,
        vector=VectorBackend.MEMORY,
        llm_modes=frozenset({LLMMode.OFF, LLMMode.LIVE}),
        answer_cache=False,
        retrieval_cache=False,
        load_seed_graph=True,
        deterministic=True,
    ),
}


@dataclass(frozen=True)
class RuntimeProfile:
    """A resolved, validated profile plus the LLM mode chosen for this process."""

    profile: Profile
    caps: Capabilities
    llm_mode: LLMMode

    @property
    def allow_llm(self) -> bool:
        return self.llm_mode is LLMMode.LIVE


def _flag(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _TRUTHY


def resolve_profile_name(env: Mapping[str, str] | None = None) -> Profile:
    env = os.environ if env is None else env
    raw = str(env.get("AGR_PROFILE", "")).strip().lower()
    if raw:
        try:
            return Profile(raw)
        except ValueError as exc:
            allowed = ", ".join(p.value for p in Profile)
            raise ProfileError(f"AGR_PROFILE={raw!r} is not one of: {allowed}") from exc
    return Profile.LIVE if _flag(env, "AGR_USE_LIVE_STORES") else Profile.OFFLINE


def _llm_key_usable(key: str | None) -> bool:
    key = (key or "").strip()
    return bool(key) and "your-key" not in key


def resolve_runtime_profile(
    env: Mapping[str, str] | None = None,
    *,
    llm_api_key: str | None = None,
) -> RuntimeProfile:
    """Resolve the profile and LLM mode; validate against the capability matrix."""
    env = os.environ if env is None else env
    profile = resolve_profile_name(env)
    caps = CAPABILITY_MATRIX[profile]
    wants_llm = _flag(env, "AGR_ALLOW_LLM")
    if wants_llm and LLMMode.LIVE not in caps.llm_modes:
        raise ProfileError(
            f"profile {profile.value!r} does not allow a live LLM (AGR_ALLOW_LLM=1); "
            "use record/replay fixtures instead"
        )
    mode = LLMMode.LIVE if wants_llm and _llm_key_usable(llm_api_key) else LLMMode.OFF
    return RuntimeProfile(profile=profile, caps=caps, llm_mode=mode)


def missing_credentials(rp: RuntimeProfile, settings: object) -> list[str]:
    """Env vars the profile needs but the settings leave empty."""
    required: dict[str, object] = {}
    if rp.caps.graph is GraphBackend.NEO4J:
        required |= {
            "NEO4J_URI": getattr(settings, "neo4j_uri", ""),
            "NEO4J_USER": getattr(settings, "neo4j_user", ""),
            "NEO4J_PASSWORD": getattr(settings, "neo4j_password", ""),
        }
    if rp.caps.vector is VectorBackend.QDRANT:
        required |= {
            "QDRANT_URL": getattr(settings, "qdrant_url", ""),
            "QDRANT_COLLECTION": getattr(settings, "qdrant_collection", ""),
        }
    return [name for name, value in required.items() if not str(value or "").strip()]
