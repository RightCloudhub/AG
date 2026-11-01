"""Storage ownership rules, independent of Web identities and authentication."""

import json

DEFAULT_TENANT = "default"


def tenant_matches(owner: str | None, requested: str | None) -> bool:
    """Unscoped tools see all records; legacy blank owners belong to default."""
    return requested is None or (owner or DEFAULT_TENANT) == (requested or DEFAULT_TENANT)


def scoped_id(tenant_id: str, record_id: str) -> str:
    """Unambiguous identity even when either input contains delimiters."""
    return json.dumps([tenant_id or DEFAULT_TENANT, record_id], ensure_ascii=True)
