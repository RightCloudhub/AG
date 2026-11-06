"""Scoped persisted advice is separate from authoritative human review decisions."""

import sqlite3
import threading
from pathlib import Path

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.review_advice import ReviewAdvice, ReviewCalibration


class SQLiteReviewAdviceStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS review_advice "
            "(tenant TEXT, kb TEXT, id TEXT, data TEXT, PRIMARY KEY(tenant,kb,id));"
            "CREATE TABLE IF NOT EXISTS review_calibration "
            "(tenant TEXT, kb TEXT, data TEXT, PRIMARY KEY(tenant,kb));"
        )

    def close(self) -> None:
        self._db.close()

    def save(self, advice: ReviewAdvice) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO review_advice VALUES (?,?,?,?)",
                (
                    advice.scope.tenant_id,
                    advice.scope.kb_id,
                    advice.review_id,
                    advice.model_dump_json(),
                ),
            )

    def get(self, scope: KnowledgeScope, review_id: str) -> ReviewAdvice | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM review_advice WHERE tenant=? AND kb=? AND id=?",
                (scope.tenant_id, scope.kb_id, review_id),
            ).fetchone()
        return ReviewAdvice.model_validate_json(row[0]) if row else None

    def save_calibration(self, calibration: ReviewCalibration) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO review_calibration VALUES (?,?,?)",
                (
                    calibration.scope.tenant_id,
                    calibration.scope.kb_id,
                    calibration.model_dump_json(),
                ),
            )

    def get_calibration(self, scope: KnowledgeScope) -> ReviewCalibration | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM review_calibration WHERE tenant=? AND kb=?",
                (scope.tenant_id, scope.kb_id),
            ).fetchone()
        return ReviewCalibration.model_validate_json(row[0]) if row else None
