"""Public errors contain stable codes, never HTTP request credentials."""

from typing import Any


class AGError(Exception):
    """Base class for client failures."""


class APIError(AGError):
    """An HTTP or business failure returned by AG."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.details = details or {}


class ProtocolError(AGError):
    """The server returned an invalid envelope, result, or event."""


class TransportError(AGError):
    """A request failed before a valid response could be read."""


class StreamInterrupted(TransportError):
    """A query stream ended without a terminal answer/error. POST is not retried."""

    def __init__(self, *, query_id: str | None = None) -> None:
        super().__init__("Query stream interrupted; the query was not resubmitted")
        self.query_id = query_id
