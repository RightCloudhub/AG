"""Keep reset and invite link credentials out of ordinary HTTP access logs."""

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def public_request_target(target: str) -> str:
    parts = urlsplit(target)
    path = re.sub(r"(/invites?/)[^/]+", r"\1[redacted]", parts.path)
    query = urlencode(
        [
            (key, "[redacted]" if key.casefold() in {"token", "password", "api_key"} else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit(("", "", path, query, ""))


class CredentialPathFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = public_request_target(str(args[2]))
            record.args = tuple(args)
        return True


def protect_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(filter_, CredentialPathFilter) for filter_ in logger.filters):
        logger.addFilter(CredentialPathFilter())
