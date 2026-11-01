"""Inputs for tenant settings; the actor and tenant never come from request bodies."""

from pydantic import Field, FiniteFloat

from agentic_graphrag.api.rbac import Role
from agentic_graphrag.webapp.schemas import EmailInput, InputModel


class TenantInput(InputModel):
    name: str = Field(min_length=1, max_length=100)


class MemberInput(InputModel):
    role: Role


class InviteInput(EmailInput):
    role: Role = Role.READER


class KeyInput(TenantInput):
    role: Role = Role.READER
    expires_at: FiniteFloat | None = None
