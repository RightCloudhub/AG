"""Index version service (refactor §2.5).

A monotonic read-side version. Caches key on it; projectors / commit handlers
bump it and publish :class:`~agentic_graphrag.core.events.IndexUpdated`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class IndexVersion:
    """Monotonic index version — bump after graph / vector / fulltext updates."""

    version: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def current(self) -> int:
        with self._lock:
            return self.version

    def bump(self) -> int:
        with self._lock:
            self.version += 1
            return self.version
