"""Data-directory resolution and atomic file writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..settings import PROJECT_ROOT, get_settings


def data_dir() -> Path:
    return get_settings().data_dir


def find_data_file(rel: str) -> Path:
    """Resolve a read-only shipped data file: the configured data dir first,
    then the repository's data/ folder (tests may point QF_DATA_DIR elsewhere)."""
    for base in (data_dir(), PROJECT_ROOT / "data"):
        p = base / rel
        if p.is_file():
            return p
    raise FileNotFoundError(rel)


def datasets_dir() -> Path:
    d = data_dir() / "cache" / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))
