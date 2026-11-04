"""Immutable, content-addressed original upload bytes per tenant (K-02).

References are opaque ``(tenant, sha256)`` pairs; the on-disk layout is never
exposed through the API. Writes are atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{64}$")
TENANT_DIGEST_CHARS = 24


def tenant_dir_name(tenant_id: str) -> str:
    return hashlib.sha256((tenant_id or "default").encode("utf-8")).hexdigest()[
        :TENANT_DIGEST_CHARS
    ]


@dataclass(frozen=True)
class StoredBlob:
    tenant_dir: str
    sha256: str
    mtime: float
    path: Path


class SourceArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, tenant_id: str, sha256: str) -> Path:
        if not _SHA.match(sha256):
            raise ValueError("Invalid source artifact reference")
        return self.root / tenant_dir_name(tenant_id) / sha256

    def put(self, tenant_id: str, data: bytes) -> str:
        sha256 = hashlib.sha256(data).hexdigest()
        path = self._path(tenant_id, sha256)
        if path.exists():
            os.utime(path)  # refresh age so pruning keeps a freshly referenced blob
            return sha256
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{sha256}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return sha256

    def read(self, tenant_id: str, sha256: str) -> bytes:
        data = self._path(tenant_id, sha256).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("Source artifact failed its integrity check")
        return data

    def exists(self, tenant_id: str, sha256: str) -> bool:
        return self._path(tenant_id, sha256).exists()

    def blobs(self) -> Iterator[StoredBlob]:
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*/*")):
            if path.is_file() and _SHA.match(path.name):
                yield StoredBlob(path.parent.name, path.name, path.stat().st_mtime, path)
