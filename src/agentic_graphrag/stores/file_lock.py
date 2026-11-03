"""Reentrant thread/process lock for state shared on a local volume."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


class FileMutex:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.thread = RLock()
        self.depth = 0

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self.thread:
            if self.depth or self.path is None:
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                self.depth += 1
                try:
                    yield
                finally:
                    self.depth -= 1
                    fcntl.flock(handle, fcntl.LOCK_UN)
