"""Password and opaque-token primitives; no Web credentials enter the core."""

from __future__ import annotations

import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from agentic_graphrag.app.errors import ApiError

_PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_DUMMY_HASH = _PASSWORDS.hash(secrets.token_urlsafe(32))
_EMAIL = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")


def field_error(field: str, code: str) -> ApiError:
    return ApiError(
        "INVALID_INPUT",
        "Check the highlighted fields",
        status_code=422,
        details={"field_errors": {field: code}},
    )


def normalize_email(email: str) -> str:
    value = email.strip().casefold()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise field_error("email", "invalid_email")
    return value


def validate_password(password: str) -> None:
    kinds = sum(
        bool(re.search(pattern, password)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]")
    )
    if not 12 <= len(password) <= 128 or (kinds < 3 and len(password) < 20):
        raise field_error("password", "weak_password")


def hash_password(password: str) -> str:
    validate_password(password)
    return _PASSWORDS.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        matches = _PASSWORDS.verify(encoded or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return bool(encoded and matches)


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


def unauthorized() -> ApiError:
    return ApiError("UNAUTHORIZED", "Sign in to continue", status_code=401)
