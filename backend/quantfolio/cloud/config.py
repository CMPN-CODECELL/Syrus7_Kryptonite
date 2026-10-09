"""Environment-driven configuration for the cloud deployment (names only; no secrets live here)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..settings import Settings, _env_bool, _env_int
from .client import valid_source


@dataclass(frozen=True)
class PublicLimits:
    """Largest workload anyone may request. Requests above a limit are rejected, never reduced."""

    max_timeout_seconds: int = 300
    max_depth: int = 3
    max_evaluations: int = 200
    max_shots: int = 2048
    max_final_shots: int = 8192
    max_noise_trials: int = 3
    max_qubits: int = 16
    max_sa_sweeps: int = 20_000
    max_sa_restarts: int = 20

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class CloudConfig:
    project: str
    bucket: str
    region: str = "asia-south1"
    worker_job: str = "quantfolio-worker"
    database: str = "(default)"
    daily_run_cap: int = 20  # live (computed) runs per UTC day, everyone together
    # "user" below means one client network address (hashed; see client.py): there is no sign-in.
    user_daily_run_cap: int = 5
    user_active_cap: int = 1  # queued + running runs one client may have
    queue_capacity: int = 6  # queued + running, everyone together
    daily_replay_cap: int = 300  # stored-result replays per UTC day (no compute, but they create documents)
    user_daily_replay_cap: int = 30
    daily_upload_cap: int = 30
    user_daily_upload_cap: int = 3
    compute_enabled: bool = True  # env kill switch; the Firestore document control/compute is the instant one
    lease_ttl_s: float = 90.0
    lease_renew_s: float = 15.0
    worker_budget_s: float = 6600.0  # the worker stops claiming when a job would not fit in what is left
    claim_margin_s: float = 120.0
    kick_min_interval_s: float = 60.0
    poke_min_interval_s: float = 10.0
    events_stream_max_s: float = 45.0
    retention_days: int = 30
    history_cap: int = 200
    sweep_sa: str | None = None  # service account allowed to call POST /api/internal/sweep
    sweep_audience: str | None = None
    limits: PublicLimits = field(default_factory=PublicLimits)
    ip_salt: str = field(default="", repr=False)  # HMAC key for client addresses (Secret Manager); never logged
    client_ip_source: str = "right:2"  # which X-Forwarded-For entry is the visitor: right:N (N trusted hops) | first | peer


def load_cloud_config(settings: Settings) -> CloudConfig:
    env = os.environ
    project = (env.get("QF_GCP_PROJECT") or env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    bucket = (env.get("QF_BUCKET") or "").strip()
    if not project or not bucket:
        raise RuntimeError("QF_GCP_PROJECT and QF_BUCKET must be set for the cloud entrypoints.")
    salt = env.get("QF_IP_SALT") or ""
    if len(salt.strip()) < 16:
        raise RuntimeError("QF_IP_SALT (at least 16 characters, from Secret Manager) must be set for the cloud entrypoints.")
    source = (env.get("QF_CLIENT_IP_SOURCE") or "right:2").strip()
    if not valid_source(source):
        raise RuntimeError("QF_CLIENT_IP_SOURCE must be first, peer or right:N (N = 1..9).")
    return CloudConfig(
        project=project,
        bucket=bucket,
        region=(env.get("QF_GCP_REGION") or "asia-south1").strip(),
        worker_job=(env.get("QF_WORKER_JOB") or "quantfolio-worker").strip(),
        database=(env.get("QF_FIRESTORE_DB") or "(default)").strip(),
        daily_run_cap=_env_int("QF_DAILY_RUN_CAP", 20, 0, 1000),
        user_daily_run_cap=_env_int("QF_USER_DAILY_RUN_CAP", 5, 0, 1000),
        user_active_cap=_env_int("QF_USER_ACTIVE_CAP", 1, 1, 16),
        queue_capacity=min(settings.queue_capacity, _env_int("QF_QUEUE_CAPACITY", 6, 1, 64)),
        compute_enabled=_env_bool("QF_COMPUTE_ENABLED", True),
        worker_budget_s=float(_env_int("QF_WORKER_BUDGET_S", 6600, 60, 24 * 3600)),
        retention_days=_env_int("QF_RETENTION_DAYS", 30, 1, 3650),
        sweep_sa=(env.get("QF_SWEEP_SA") or "").strip() or None,
        sweep_audience=(env.get("QF_SWEEP_AUDIENCE") or "").strip() or None,
        ip_salt=salt.strip(),
        client_ip_source=source,
    )
