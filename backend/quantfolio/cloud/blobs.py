"""Private object storage seam (Cloud Storage) with an in-memory twin for tests."""

from __future__ import annotations

import threading
from typing import Any, Protocol


class BlobStore(Protocol):
    def put(self, name: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, name: str) -> bytes | None: ...
    def exists(self, name: str) -> bool: ...


class MemoryBlobStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self.fail_puts = False

    def put(self, name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if self.fail_puts:
            raise OSError("simulated upload failure")
        with self._lock:
            self._objects[name] = bytes(data)

    def get(self, name: str) -> bytes | None:
        with self._lock:
            return self._objects.get(name)

    def exists(self, name: str) -> bool:
        with self._lock:
            return name in self._objects

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._objects)


class GcsBlobStore:
    """Objects in one private bucket (uniform bucket-level access, public access prevention enforced)."""

    def __init__(self, project: str, bucket: str) -> None:
        from google.cloud import storage  # lazy

        self._bucket: Any = storage.Client(project=project).bucket(bucket)

    def put(self, name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._bucket.blob(name).upload_from_string(data, content_type=content_type, timeout=60)

    def get(self, name: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(name).download_as_bytes(timeout=60)
        except NotFound:
            return None

    def exists(self, name: str) -> bool:
        return bool(self._bucket.blob(name).exists(timeout=30))
