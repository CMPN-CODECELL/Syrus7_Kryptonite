"""Small filesystem helpers: atomic writes and safe run-directory resolution."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"^(run|exp)-\d{8}-[0-9a-f]{8}$")


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.fullmatch(run_id or ""))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same directory, fsync, then os.replace.

    Readers never observe a partially written file. On Windows os.replace can
    fail transiently while another process holds the target open, so retry a
    few times before giving up (the temp file is always removed).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        last_exc: Exception | None = None
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:  # pragma: no cover - Windows sharing violation
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
        assert last_exc is not None
        raise last_exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def _json_default(obj: Any) -> Any:
    item = getattr(obj, "item", None)
    if callable(item):
        return item()
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return tolist()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _finite(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    if isinstance(obj, dict):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def dumps(obj: Any) -> str:
    """Strict JSON (NaN/Infinity become null; numpy scalars converted)."""
    try:
        return json.dumps(obj, default=_json_default, allow_nan=False, ensure_ascii=False)
    except ValueError:
        return json.dumps(_finite(json.loads(json.dumps(obj, default=_json_default))), allow_nan=False, ensure_ascii=False)


def safe_rmtree(path: Path, root: Path) -> bool:
    """Remove `path` only if it resolves inside `root` (never follows outside)."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
    except OSError:
        return False
    if p == r or r not in p.parents:
        return False
    shutil.rmtree(p, ignore_errors=True)
    return not p.exists()
