"""Fixtures for platform (API/jobs/worker) tests.

Every test gets a private data directory under the project's .tmp folder and
runs against the deterministic fake pipeline unless it opts into the real
core. Tests that start the worker check afterwards that every process they
started has exited (no orphans), using only this test process's own tree.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import psutil
import pytest

BACKEND = Path(__file__).resolve().parents[2]
PROJECT = BACKEND.parent
TMP_ROOT = PROJECT / ".tmp" / "api-tests"
sys.path.insert(0, str(BACKEND))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qf_api_helpers import FAKE_TOKEN, wait_ready_worker  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "worker: starts the job supervisor process")
    config.addinivalue_line("markers", "slow: takes more than ~10 s")
    config.addinivalue_line("markers", "real: end-to-end with the real quantum core (skipped if unavailable)")
    config.addinivalue_line("markers", "process: runs the API as a separate OS process")


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="t-", dir=TMP_ROOT))
    monkeypatch.setenv("QF_DATA_DIR", str(d))
    monkeypatch.setenv("QF_PIPELINE_IMPL", "fake")
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.005")
    monkeypatch.setenv("QF_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("QF_FRONTEND_DIST", str(d / "no-dist"))
    monkeypatch.delenv("IBM_QUANTUM_TOKEN", raising=False)
    monkeypatch.delenv("IBM_QUANTUM_INSTANCE", raising=False)
    monkeypatch.delenv("QF_QUEUE_CAPACITY", raising=False)
    monkeypatch.delenv("QF_LIVE_QUOTES", raising=False)  # live prices stay off (no network) unless a test opts in
    from quantfolio.settings import reset_settings_cache

    reset_settings_cache()
    yield d
    reset_settings_cache()
    for _ in range(20):
        try:
            shutil.rmtree(d)
            break
        except OSError:
            time.sleep(0.25)


@pytest.fixture()
def make_settings(data_dir: Path) -> Callable[..., Any]:
    def _make(**overrides: Any) -> Any:
        from quantfolio.settings import load_settings

        return dataclasses.replace(load_settings(), **overrides)

    return _make


def _own_descendants() -> set[int]:
    try:
        return {p.pid for p in psutil.Process().children(recursive=True)}
    except psutil.Error:
        return set()


@pytest.fixture()
def no_orphans() -> Iterator[None]:
    before = _own_descendants()
    yield
    deadline = time.monotonic() + 15
    left: set[int] = set()
    while time.monotonic() < deadline:
        left = {pid for pid in _own_descendants() - before if psutil.pid_exists(pid)}
        if not left:
            break
        time.sleep(0.2)
    assert not left, f"processes started by this test are still alive: {sorted(left)}"


@pytest.fixture()
def app_client(make_settings: Callable[..., Any], no_orphans: None) -> Iterator[Callable[..., Any]]:
    """Factory: app_client(**settings_overrides) -> entered TestClient (closed at teardown)."""
    from fastapi.testclient import TestClient

    from quantfolio.api.app import create_app

    opened: list[Any] = []

    def _open(**overrides: Any) -> Any:
        overrides.setdefault("start_worker", False)
        c = TestClient(create_app(make_settings(**overrides)), raise_server_exceptions=False)
        c.__enter__()
        opened.append(c)
        return c

    yield _open
    for c in reversed(opened):
        c.__exit__(None, None, None)


@pytest.fixture()
def client(app_client: Callable[..., Any]) -> Any:
    """API without a worker (jobs stay queued)."""
    return app_client()


@pytest.fixture()
def wclient(app_client: Callable[..., Any]) -> Any:
    """API with the job supervisor running."""
    c = app_client(start_worker=True)
    wait_ready_worker(c)
    return c


@pytest.fixture()
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("IBM_QUANTUM_TOKEN", FAKE_TOKEN)
    from quantfolio.settings import reset_settings_cache

    reset_settings_cache()
    return FAKE_TOKEN
