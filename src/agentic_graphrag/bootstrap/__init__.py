"""Composition root: profiles, capability matrix, and wiring (refactor §0-C).

Drivers (API, CLI, workers, eval) resolve one :class:`RuntimeProfile` and ask
this package for wired components instead of assembling backends themselves.
"""

from agentic_graphrag.bootstrap.profile import (
    CAPABILITY_MATRIX,
    Capabilities,
    LLMMode,
    Profile,
    ProfileError,
    RuntimeProfile,
    missing_credentials,
    resolve_profile_name,
    resolve_runtime_profile,
)
from agentic_graphrag.bootstrap.stores import build_stores

__all__ = [
    "CAPABILITY_MATRIX",
    "Capabilities",
    "LLMMode",
    "Profile",
    "ProfileError",
    "RuntimeProfile",
    "build_stores",
    "missing_credentials",
    "resolve_profile_name",
    "resolve_runtime_profile",
]
