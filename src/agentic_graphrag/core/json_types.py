"""Opaque JSON objects at transport, journal, metadata, and evaluation boundaries.

Domain operations use models and protocols. These objects intentionally retain
extension fields supplied by external tools, datasets, and audit producers.
"""

from typing import Any

type JsonObject = dict[str, Any]
