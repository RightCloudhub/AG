"""Background polling thread shared by ingest workers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from agentic_graphrag.observability.degradation import (
    REASON_UNEXPECTED,
    ChannelStatus,
    record_degradation,
)

logger = logging.getLogger("agentic_graphrag.knowledge.ingest_worker")


class PollingThread:
    poll_interval_seconds: float = 5.0

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> Sequence[Any]:
        raise NotImplementedError

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ingest-worker")
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        logger.info("IngestWorker started")
        while not self._stop.is_set():
            try:
                results = self.run_once()
                if results:
                    logger.info(
                        "Ingested %d docs: %s",
                        len(results),
                        ", ".join(getattr(r, "doc_id", "") for r in results),
                    )
            except Exception as exc:  # noqa: BLE001
                # The loop must survive, but never silently: that hid a dead worker (O-9).
                record_degradation("ingest", REASON_UNEXPECTED, exc, severity=ChannelStatus.FAILED)
            self._stop.wait(timeout=self.poll_interval_seconds)
        logger.info("IngestWorker stopped")
