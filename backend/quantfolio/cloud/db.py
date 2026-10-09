"""Tiny document-store seam over Firestore (Native mode), with an in-memory twin for tests.

Only what the cloud job store needs: point reads/writes, one-field queries and
serialisable transactions. Transaction bodies must do all their reads before
their first write (a Firestore rule the in-memory store enforces too) and must
be free of side effects, because Firestore re-runs them on contention.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from typing import Any, Protocol

Where = tuple[str, str, Any]  # (field, "==" | ">" | ">=" | "<" | "<=", value)


class Txn(Protocol):
    def get(self, path: str) -> dict[str, Any] | None: ...
    def set(self, path: str, data: dict[str, Any]) -> None: ...
    def delete(self, path: str) -> None: ...


class DocStore(Protocol):
    def get(self, path: str) -> dict[str, Any] | None: ...
    def set(self, path: str, data: dict[str, Any]) -> None: ...
    def delete(self, path: str) -> None: ...
    def query(self, collection: str, where: list[Where] | None = None, order_by: str | None = None,
              descending: bool = False, limit: int | None = None) -> list[dict[str, Any]]: ...
    def transaction(self, fn: Callable[[Txn], Any]) -> Any: ...
    def ping(self) -> bool: ...


class TransactionOrderError(RuntimeError):
    """A transaction read after writing (Firestore would reject it)."""


# --------------------------------------------------------------------------- in-memory (tests, local dry runs)


class _MemoryTxn:
    def __init__(self, docs: dict[str, dict[str, Any]]):
        self._docs = docs
        self.writes: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, path: str) -> dict[str, Any] | None:
        if self.writes:
            raise TransactionOrderError(f"read of {path} after a write in the same transaction")
        doc = self._docs.get(path)
        return copy.deepcopy(doc) if doc is not None else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        self.writes.append((path, copy.deepcopy(data)))

    def delete(self, path: str) -> None:
        self.writes.append((path, None))


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    ">": lambda a, b: a is not None and a > b,
    ">=": lambda a, b: a is not None and a >= b,
    "<": lambda a, b: a is not None and a < b,
    "<=": lambda a, b: a is not None and a <= b,
}


class MemoryDocStore:
    """Thread-safe, serialisable (one big lock) stand-in for Firestore."""

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.transactions = 0
        self.writes = 0

    def get(self, path: str) -> dict[str, Any] | None:
        with self._lock:
            doc = self._docs.get(path)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._docs[path] = copy.deepcopy(data)
            self.writes += 1

    def delete(self, path: str) -> None:
        with self._lock:
            self._docs.pop(path, None)

    def query(self, collection: str, where: list[Where] | None = None, order_by: str | None = None,
              descending: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        prefix = collection.rstrip("/") + "/"
        with self._lock:
            rows = [copy.deepcopy(d) for p, d in self._docs.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]
        for fld, op, value in where or []:
            rows = [r for r in rows if _OPS[op](r.get(fld), value)]
        if order_by:
            rows = [r for r in rows if r.get(order_by) is not None]
            rows.sort(key=lambda r: r[order_by], reverse=descending)
        return rows[:limit] if limit is not None else rows

    def transaction(self, fn: Callable[[Txn], Any]) -> Any:
        with self._lock:
            txn = _MemoryTxn(self._docs)
            out = fn(txn)
            for path, data in txn.writes:
                if data is None:
                    self._docs.pop(path, None)
                else:
                    self._docs[path] = data
                self.writes += 1
            self.transactions += 1
            return out

    def ping(self) -> bool:
        return True

    def paths(self, prefix: str = "") -> list[str]:
        with self._lock:
            return sorted(p for p in self._docs if p.startswith(prefix))


# --------------------------------------------------------------------------- Firestore


class _FirestoreTxn:
    def __init__(self, client: Any, txn: Any):
        self._client = client
        self._txn = txn

    def get(self, path: str) -> dict[str, Any] | None:
        snap = self._client.document(path).get(transaction=self._txn)
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        self._txn.set(self._client.document(path), data)

    def delete(self, path: str) -> None:
        self._txn.delete(self._client.document(path))


class FirestoreDocStore:
    def __init__(self, project: str, database: str = "(default)") -> None:
        from google.cloud import (
            firestore,  # lazy: not needed by the local app or the tests
        )

        self._fs = firestore
        self._client = firestore.Client(project=project, database=database)

    def get(self, path: str) -> dict[str, Any] | None:
        snap = self._client.document(path).get()
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict[str, Any]) -> None:
        self._client.document(path).set(data)

    def delete(self, path: str) -> None:
        self._client.document(path).delete()

    def query(self, collection: str, where: list[Where] | None = None, order_by: str | None = None,
              descending: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        q: Any = self._client.collection(collection)
        for fld, op, value in where or []:
            q = q.where(filter=FieldFilter(fld, op, value))
        if order_by:
            q = q.order_by(order_by, direction=self._fs.Query.DESCENDING if descending else self._fs.Query.ASCENDING)
        if limit is not None:
            q = q.limit(limit)
        return [snap.to_dict() for snap in q.stream()]

    def transaction(self, fn: Callable[[Txn], Any]) -> Any:
        txn = self._client.transaction(max_attempts=8)

        @self._fs.transactional
        def _run(t: Any) -> Any:
            return fn(_FirestoreTxn(self._client, t))

        return _run(txn)

    def ping(self) -> bool:
        self._client.document("control/compute").get()
        return True
