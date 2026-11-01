"""Injectable mail delivery; development messages go only to the mail logger."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from agentic_graphrag.webapp.settings import WebSettings


class MailSender(Protocol):
    def send(self, recipient: str, *, subject: str, body: str) -> None: ...


class LogMailSender:
    def send(self, recipient: str, *, subject: str, body: str) -> None:
        logging.getLogger("agr.webapp.mail").info(
            "Development email to=%s subject=%s\n%s", recipient, subject, body
        )


class SmtpMailSender:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings

    def send(self, recipient: str, *, subject: str, body: str) -> None:
        config = self.settings
        message = EmailMessage()
        message["From"] = config.mail_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as server:
            server.starttls()
            if config.smtp_user:
                server.login(config.smtp_user, config.smtp_password.get_secret_value())
            server.send_message(message)


def create_mail_sender(settings: WebSettings) -> MailSender:
    return SmtpMailSender(settings) if settings.mail_backend == "smtp" else LogMailSender()
