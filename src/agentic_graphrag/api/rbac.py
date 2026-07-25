"""RBAC primitives for API key governance (ENT-04).

Extends the existing auth layer with role-based access control:
  - Role enum: admin > operator > reader
  - Key format: ``AGR_API_KEYS=tenant:key:role`` (backward-compat with ``tenant:key``)
  - ``require_role()`` FastAPI dependency for route-level enforcement
  - ``KeyInfo`` with optional expiry for future YAML-based key config

Wire into ``AuthRateLimitMiddleware`` by replacing ``parse_api_keys`` with
``parse_api_keys_with_roles`` and attaching ``role`` to ``Principal``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import Request

from agentic_graphrag.api.errors import ApiError

log = logging.getLogger(__name__)

# ── error code ──────────────────────────────────────────────────────────

FORBIDDEN = "FORBIDDEN"

# ── role hierarchy ──────────────────────────────────────────────────────


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    READER = "reader"


_VALID_ROLES: frozenset[str] = frozenset(r.value for r in Role)


# ── key metadata ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KeyInfo:
    """Extended key metadata — env var parsing fills tenant_id + role;
    the YAML config path can additionally set expiry and notes."""

    tenant_id: str
    role: Role = Role.READER
    expires_at: float | None = None
    note: str = ""


# ── parsing ─────────────────────────────────────────────────────────────


def parse_api_keys_with_roles(
    raw: str | None = None,
) -> dict[str, KeyInfo]:
    """Parse ``AGR_API_KEYS`` into ``{api_key: KeyInfo}``.

    Supported segment formats (comma-separated):
      ``tenant:key:role``  -> KeyInfo(tenant, Role(role))
      ``tenant:key``       -> KeyInfo(tenant, Role.READER)   # backward compat
      ``key``              -> KeyInfo("default", Role.READER)

    Invalid roles are logged and silently defaulted to ``reader``.
    """
    text = raw if raw is not None else os.environ.get("AGR_API_KEYS", "")
    out: dict[str, KeyInfo] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        key_info = _parse_single_entry(part)
        if key_info is not None:
            api_key, info = key_info
            out[api_key] = info
    return out


def _parse_single_entry(segment: str) -> tuple[str, KeyInfo] | None:
    """Parse one ``tenant:key[:role]`` segment."""
    pieces = segment.split(":")
    if len(pieces) == 1:
        # bare key
        return pieces[0].strip(), KeyInfo(tenant_id="default")
    if len(pieces) == 2:
        tenant, key = pieces[0].strip(), pieces[1].strip()
        return key, KeyInfo(tenant_id=tenant or "default")
    if len(pieces) >= 3:
        tenant = pieces[0].strip() or "default"
        key = pieces[1].strip()
        role_raw = pieces[2].strip().lower()
        role = _safe_role(role_raw)
        return key, KeyInfo(tenant_id=tenant, role=role)
    return None  # pragma: no cover


def _safe_role(value: str) -> Role:
    """Convert a string to Role, defaulting to READER on bad input."""
    if value in _VALID_ROLES:
        return Role(value)
    log.warning("Unknown role %r in AGR_API_KEYS; defaulting to reader", value)
    return Role.READER


# ── flat dict for backward-compat bridge ────────────────────────────────


def keys_to_tenant_map(keys: dict[str, KeyInfo]) -> dict[str, str]:
    """Downgrade ``KeyInfo`` map to ``{api_key: tenant_id}`` for the
    existing ``AuthRateLimitMiddleware`` until it is migrated."""
    return {k: info.tenant_id for k, info in keys.items()}


# ── role-based route guard ──────────────────────────────────────────────


def require_role(*allowed_roles: Role) -> Callable:
    """FastAPI ``Depends()`` factory that enforces role membership.

    Usage::

        @router.get("/admin/keys", dependencies=[Depends(require_role(Role.ADMIN))])
        def admin_keys(): ...

        @router.post("/docs", dependencies=[Depends(require_role(Role.ADMIN, Role.OPERATOR))])
        def upload(): ...

    Raises ``ApiError(FORBIDDEN, ...)`` when the principal's role is not in
    *allowed_roles*.  Falls back to ``Role.READER`` when the principal
    object does not carry a ``role`` attribute (backward compat with the
    pre-RBAC ``Principal``).
    """
    allowed = frozenset(allowed_roles)

    def _checker(request: Request) -> None:
        # When auth is not required (dev/test), skip RBAC — matches "default open".
        if not _is_auth_required():
            return
        principal = getattr(request.state, "principal", None)
        role = _extract_role(principal)
        if role not in allowed:
            raise ApiError(
                FORBIDDEN,
                f"Insufficient permissions: requires {_fmt_roles(allowed)}",
                status_code=403,
                details={"required": sorted(r.value for r in allowed), "actual": role.value},
            )

    return _checker


def _extract_role(principal: Any) -> Role:
    """Get the role from a principal, tolerating pre-RBAC objects."""
    role = getattr(principal, "role", None)
    if isinstance(role, Role):
        return role
    if isinstance(role, str):
        return _safe_role(role)
    return Role.READER


def _is_auth_required() -> bool:
    return os.getenv("AGR_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}


def _fmt_roles(roles: frozenset[Role]) -> str:
    return " | ".join(sorted(r.value for r in roles))


# ── key expiry ──────────────────────────────────────────────────────────


def check_key_expiry(key_info: KeyInfo) -> bool:
    """Return ``True`` if the key is valid (not expired).

    Keys without an ``expires_at`` are always valid.
    """
    if key_info.expires_at is None:
        return True
    return time.time() < key_info.expires_at


# ── optional YAML key config ────────────────────────────────────────────


@dataclass
class KeyRegistry:
    """In-memory registry combining env-var keys with optional YAML config.

    The YAML file (``configs/api_keys.yaml``) can set per-key expiry and notes::

        keys:
          - tenant: acme
            key: sk-acme-123
            role: operator
            expires_at: 1735689600
            note: "Q4 trial key"
    """

    _keys: dict[str, KeyInfo] = field(default_factory=dict)

    def load_from_env(self, raw: str | None = None) -> None:
        self._keys.update(parse_api_keys_with_roles(raw))

    def load_from_yaml(self, path: str) -> None:
        """Merge keys from a YAML config file if it exists."""
        import yaml  # deferred: not needed on the offline path

        try:
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            return
        for entry in data.get("keys", []):
            api_key = str(entry.get("key", "")).strip()
            if not api_key:
                continue
            self._keys[api_key] = KeyInfo(
                tenant_id=str(entry.get("tenant", "default")),
                role=_safe_role(str(entry.get("role", "reader"))),
                expires_at=_optional_float(entry.get("expires_at")),
                note=str(entry.get("note", "")),
            )

    def get(self, api_key: str) -> KeyInfo | None:
        info = self._keys.get(api_key)
        if info is None:
            return None
        if not check_key_expiry(info):
            return None
        return info

    def tenant_map(self) -> dict[str, str]:
        return keys_to_tenant_map(self._keys)

    @property
    def keys(self) -> dict[str, KeyInfo]:
        return dict(self._keys)


def _optional_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
