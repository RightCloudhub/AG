"""Dependencies of the Web identity layer, injectable without a query engine."""

from dataclasses import dataclass

from fastapi import Request

from agentic_graphrag.api.auth import Principal
from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.mail import MailSender
from agentic_graphrag.webapp.security import unauthorized
from agentic_graphrag.webapp.settings import WebSettings


@dataclass(frozen=True)
class WebRuntime:
    database: Database
    settings: WebSettings
    secret: str
    mail: MailSender


def runtime(request: Request) -> WebRuntime:
    value = request.app.state.identity
    if not isinstance(value, WebRuntime):
        raise RuntimeError("Web identity is not initialized")
    return value


def principal(request: Request) -> Principal:
    value = getattr(request.state, "principal", None)
    if not isinstance(value, Principal):
        raise unauthorized()
    return value


def user_principal(request: Request) -> Principal:
    value = principal(request)
    if not value.session_id:
        raise unauthorized()
    return value
