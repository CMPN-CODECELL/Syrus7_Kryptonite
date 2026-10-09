"""Environment-driven settings for the API, job store and worker.

Values come from process environment, optionally seeded from a project-root
`.env` file (python-dotenv). Secrets (IBM token) are read here but never
logged, serialised or returned by the API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

_SECRET_ENV = ("IBM_QUANTUM_TOKEN",)


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except Exception:  # pragma: no cover - dotenv is a declared dependency
            pass


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, val))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    if val != val:  # NaN
        return default
    return max(lo, min(hi, val))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = PROJECT_ROOT / "data"
    state_dir: Path = PROJECT_ROOT / "data" / "state"
    runs_dir: Path = PROJECT_ROOT / "data" / "runs"
    examples_dir: Path = PROJECT_ROOT / "data" / "examples"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    db_path: Path = PROJECT_ROOT / "data" / "state" / "quantfolio.sqlite3"
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    queue_capacity: int = 8  # queued + running jobs accepted at once
    max_events_per_run: int = 3000
    iteration_flush_hz: float = 4.0
    cancel_grace_s: float = 3.0
    max_upload_bytes: int = 5 * 1024 * 1024
    max_body_bytes: int = 256 * 1024
    experiment_timeout_s: int = 3600
    start_worker: bool = True
    warm_worker: bool = True
    pipeline_impl: str = "real"  # "real" | "fake" (fake = deterministic test pipeline)
    sse_heartbeat_s: float = 10.0
    sse_poll_s: float = 0.2
    # Live NSE quotes for the price ticker (display only; never enter a run). Off in the fake pipeline and tests.
    live_quotes: bool = False
    live_idle_s: float = 60.0  # stop the upstream feed this long after the last viewer leaves
    live_stream_max_s: float = 900.0  # end each SSE stream after this long; the browser reconnects
    log_level: str = "INFO"
    ibm_instance: str | None = None
    _ibm_token: str | None = field(default=None, repr=False)

    @property
    def ibm_configured(self) -> bool:
        return bool(self._ibm_token)

    def ibm_token(self) -> str | None:
        """Accessor so the token never appears in repr()/asdict()-style dumps."""
        return self._ibm_token

    def public_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "queue_capacity": self.queue_capacity,
            "max_events_per_run": self.max_events_per_run,
            "iteration_flush_hz": self.iteration_flush_hz,
            "cancel_grace_s": self.cancel_grace_s,
            "max_upload_bytes": self.max_upload_bytes,
            "pipeline_impl": self.pipeline_impl,
            "warm_worker": self.warm_worker,
            "live_quotes": self.live_quotes,
            "ibm_configured": self.ibm_configured,
        }


def load_settings() -> Settings:
    _load_dotenv()
    data_dir = Path(os.environ.get("QF_DATA_DIR") or (PROJECT_ROOT / "data")).resolve()
    state_dir = Path(os.environ.get("QF_STATE_DIR") or (data_dir / "state")).resolve()
    runs_dir = Path(os.environ.get("QF_RUNS_DIR") or (data_dir / "runs")).resolve()
    origins_raw = os.environ.get("QF_CORS_ORIGINS")
    origins = (
        tuple(o.strip() for o in origins_raw.split(",") if o.strip())
        if origins_raw
        else ("http://127.0.0.1:5173", "http://localhost:5173")
    )
    impl = (os.environ.get("QF_PIPELINE_IMPL") or "real").strip().lower()
    if impl not in {"real", "fake"}:
        impl = "real"
    host = (os.environ.get("QF_API_HOST") or "127.0.0.1").strip()
    return Settings(
        host=host,
        port=_env_int("QF_API_PORT", 8000, 1, 65535),
        data_dir=data_dir,
        state_dir=state_dir,
        runs_dir=runs_dir,
        examples_dir=data_dir / "examples",
        frontend_dist=Path(os.environ.get("QF_FRONTEND_DIST") or (PROJECT_ROOT / "frontend" / "dist")).resolve(),
        db_path=state_dir / "quantfolio.sqlite3",
        cors_origins=origins,
        queue_capacity=_env_int("QF_QUEUE_CAPACITY", 8, 1, 64),
        max_events_per_run=_env_int("QF_MAX_EVENTS_PER_RUN", 3000, 100, 100_000),
        iteration_flush_hz=_env_float("QF_ITERATION_FLUSH_HZ", 4.0, 0.5, 50.0),
        cancel_grace_s=_env_float("QF_CANCEL_GRACE_S", 3.0, 0.1, 30.0),
        max_upload_bytes=_env_int("QF_MAX_UPLOAD_BYTES", 5 * 1024 * 1024, 1024, 50 * 1024 * 1024),
        experiment_timeout_s=_env_int("QF_EXPERIMENT_TIMEOUT_S", 3600, 30, 6 * 3600),
        start_worker=_env_bool("QF_START_WORKER", True),
        warm_worker=_env_bool("QF_WARM_WORKER", True),
        pipeline_impl=impl,
        sse_heartbeat_s=_env_float("QF_SSE_HEARTBEAT_S", 10.0, 0.5, 60.0),
        sse_poll_s=_env_float("QF_SSE_POLL_S", 0.2, 0.02, 2.0),
        live_quotes=_env_bool("QF_LIVE_QUOTES", impl != "fake"),
        live_idle_s=_env_float("QF_LIVE_IDLE_S", 60.0, 1.0, 3600.0),
        live_stream_max_s=_env_float("QF_LIVE_STREAM_MAX_S", 900.0, 1.0, 24 * 3600.0),
        log_level=(os.environ.get("QF_LOG_LEVEL") or "INFO").upper(),
        ibm_instance=os.environ.get("IBM_QUANTUM_INSTANCE") or None,
        _ibm_token=(os.environ.get("IBM_QUANTUM_TOKEN") or "").strip() or None,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def secret_values() -> list[str]:
    """Current secret values (for log/report redaction). Never log the result."""
    vals = []
    for name in _SECRET_ENV:
        v = os.environ.get(name)
        if v and len(v.strip()) >= 4:
            vals.append(v.strip())
    return vals
