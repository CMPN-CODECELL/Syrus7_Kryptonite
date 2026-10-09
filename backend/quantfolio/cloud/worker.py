"""Cloud Run Job entrypoint: `python -m quantfolio.cloud.worker`.

One execution = one task = one process that

1. takes the single global lease (or exits at once if another worker holds it);
2. drains the queue one run at a time, re-using the local `Supervisor` for the
   actual execution (spawned child process, cooperative cancellation with a
   hard kill after the grace period, wall-clock timeout, crash detection);
3. heart-beats the lease while it works, and releases it in the same
   transaction that finds the queue empty.

Every write is fenced with the lease token: a worker that lost its lease can
neither report progress nor publish a result. Results go to
`runs/<run_id>/f<fence>/` in the private bucket, so two executions can never
write the same object. A run is never re-executed automatically.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

from ..jobs.fsutil import safe_rmtree
from ..jobs.worker import Supervisor
from ..logging_utils import configure_logging, safe_message
from ..settings import Settings, get_settings
from . import datasets, dispatch
from .blobs import BlobStore
from .config import CloudConfig, load_cloud_config
from .jobstore import CloudJobStore
from .launcher import JobLauncher

log = logging.getLogger("quantfolio.cloud.worker")

_CANCEL_POLL_S = 2.0


def _retry(fn: Callable[[], Any], what: str, attempts: int = 4) -> Any:
    delay = 1.0
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            log.warning("%s failed; retrying", what, exc_info=True)
            time.sleep(delay)
            delay *= 2


class FencedStore:
    """The slice of the job-store interface the Supervisor uses, bound to one lease (holder + fencing token)."""

    def __init__(self, settings: Settings, cfg: CloudConfig, jobs: CloudJobStore, blobs: BlobStore, holder: str, fence: int,
                 on_lost: Callable[[], None]) -> None:
        self.s, self.cfg, self.jobs, self.blobs = settings, cfg, jobs, blobs
        self.holder, self.fence = holder, fence
        self.on_lost = on_lost
        self.lost = False
        self._last_renew = time.monotonic()
        self._cancel_at = 0.0
        self._cancel = False

    # --- supervisor liveness -> lease heartbeat
    def get_meta(self, key: str) -> None:
        return None

    def heartbeat(self, pid: int, started_at: str, current_run: str | None, child_pid: int | None, info: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_renew < self.cfg.lease_renew_s:
            return
        try:
            ok = self.jobs.renew_lease(self.holder, self.fence)
        except Exception:
            self._last_renew = now - self.cfg.lease_renew_s + 3.0  # try again in a few seconds
            log.warning("lease renewal failed", exc_info=True)
            return
        self._last_renew = now
        if not ok and not self.lost:
            self.lost = True
            log.error("lease lost; stopping without publishing", extra={"fence": self.fence})
            self.on_lost()

    def clear_heartbeat(self, pid: int) -> None:
        return None

    def close(self) -> None:
        return None

    # --- progress (best effort: a failed progress write never fails the run)
    def is_cancel_requested(self, run_id: str) -> bool:
        now = time.monotonic()
        if now - self._cancel_at >= _CANCEL_POLL_S:
            self._cancel_at = now
            try:
                self._cancel = self.jobs.is_cancel_requested(run_id, self.fence)
            except Exception:
                log.warning("cancel check failed", exc_info=True)
        return self._cancel

    def reset_cancel(self) -> None:
        self._cancel, self._cancel_at = False, 0.0

    def record_stage(self, run_id: str, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        try:
            self.jobs.record_stage(run_id, self.fence, stage, message, data)
        except Exception:
            log.warning("stage write failed", exc_info=True)

    def record_iterations(self, run_id: str, stage: str | None, points: list[dict[str, Any]]) -> None:
        try:
            self.jobs.record_iterations(run_id, self.fence, stage, points)
        except Exception:
            log.warning("iteration write failed", exc_info=True)

    def add_event(self, run_id: str, type_: str, stage: str | None, data: dict[str, Any]) -> None:
        try:
            self.jobs.add_event(run_id, self.fence, type_, stage, data)
        except Exception:
            log.warning("event write failed", exc_info=True)

    # --- outcome
    def transition(self, run_id: str, to_state: str, **fields: Any) -> bool:
        if to_state != "succeeded":
            return bool(_retry(lambda: self.jobs.finish(run_id, self.fence, to_state, **fields), "finish"))
        current = _retry(lambda: self.jobs.get(run_id), "read run")
        if current is None or current.get("status") != "running" or current.get("fence") != self.fence:
            return False  # reconciled or taken over meanwhile: publish nothing
        artifacts = self.s.runs_dir / run_id
        name = str(fields.pop("result_path", f"{run_id}/result.json")).rsplit("/", 1)[-1]
        prefix = f"runs/{run_id}/f{self.fence}"
        counts_path: str | None = None
        try:
            _retry(lambda: self.blobs.put(f"{prefix}/{name}", (artifacts / name).read_bytes(), "application/json"), "result upload", 3)
            counts = artifacts / "final_counts.json"
            if counts.is_file():
                counts_path = f"{prefix}/final_counts.json"
                _retry(lambda: self.blobs.put(str(counts_path), counts.read_bytes(), "application/json"), "counts upload", 3)
        except Exception as exc:
            log.exception("result upload failed")
            message = safe_message(f"The run finished but its result could not be stored ({type(exc).__name__}). Submit it again.")
            return bool(_retry(lambda: self.jobs.finish(run_id, self.fence, "failed", error_code="result_upload_failed",
                                                        error_message=message), "finish"))
        return bool(_retry(lambda: self.jobs.finish(run_id, self.fence, "succeeded", result_path=f"{prefix}/{name}",
                                                    counts_path=counts_path, **fields), "finish"))


class CloudSupervisor(Supervisor):
    """The local supervisor with its SQLite store swapped for the fenced Firestore adapter."""

    def __init__(self, settings: Settings, store: FencedStore) -> None:
        super().__init__(settings)
        self.store.close()
        self.store = store  # type: ignore[assignment]


def _dataset_id(config: dict[str, Any]) -> str | None:
    base = config.get("base") if isinstance(config.get("base"), dict) else config
    value = base.get("dataset_id")
    return value if isinstance(value, str) else None


def drain(settings: Settings, cfg: CloudConfig, jobs: CloudJobStore, blobs: BlobStore, launcher: JobLauncher, holder: str,
          *, install_signals: bool = False) -> int:
    """Take the lease and run queued jobs until the queue is empty. Returns the number of runs executed."""
    for d in (settings.data_dir, settings.state_dir, settings.runs_dir):
        d.mkdir(parents=True, exist_ok=True)
    fence = _retry(lambda: jobs.acquire_lease(holder), "acquire lease")
    if fence is None:
        log.info("another worker holds the lease; nothing to do", extra={"holder": holder})
        return 0
    started = time.monotonic()
    sup: CloudSupervisor | None = None
    reason = "stopped"
    try:
        store = FencedStore(settings, cfg, jobs, blobs, holder, fence, on_lost=lambda: sup.request_stop() if sup else None)
        sup = CloudSupervisor(settings, store)
        if install_signals:
            signal.signal(signal.SIGTERM, sup.request_stop)
            signal.signal(signal.SIGINT, sup.request_stop)
        log.info("worker started", extra={"holder": holder, "fence": fence, "impl": settings.pipeline_impl})
        fresh = True
        while not sup._stop:
            remaining = cfg.worker_budget_s - (time.monotonic() - started)
            reason, run = _retry(lambda: jobs.claim_next(holder, fence, remaining_s=remaining, fresh=fresh), "claim")  # noqa: B023
            if run is None:
                break
            fresh = False
            reason = "stopped"
            run_id = run["run_id"]
            store.reset_cancel()
            dataset_id = _dataset_id(json.loads(run["config_json"]))
            if datasets.is_upload_id(dataset_id):
                assert dataset_id is not None
                owned = jobs.owned_dataset(run["uid"], dataset_id)
                if owned is None or not datasets.ensure_local(settings, blobs, dataset_id, owned.get("objects")):
                    store.transition(run_id, "failed", error_code="dataset_missing",
                                     error_message="The uploaded dataset for this run is no longer stored. Upload it again.")
                    continue
            sup._execute(run)
            sup.jobs_done += 1
            safe_rmtree(settings.runs_dir / run_id, settings.runs_dir)
        if reason == "stopped" and not store.lost:
            # stopped between jobs (SIGTERM) or after an interrupted job: free the lease so the next launch can start at once
            try:
                jobs.release_lease(holder, fence)
            except Exception:
                log.warning("lease release failed (it expires on its own)", exc_info=True)
        log.info("worker finished", extra={"holder": holder, "reason": reason, "jobs_done": sup.jobs_done})
        return sup.jobs_done
    finally:
        if sup is not None:
            sup._shutdown()
        if reason == "handoff":
            # the next run would not fit in this execution's time limit: start a fresh execution for it
            dispatch.kick(jobs, launcher, force=True)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    cfg = load_cloud_config(settings)
    from .blobs import GcsBlobStore
    from .db import FirestoreDocStore
    from .launcher import CloudRunJobLauncher

    jobs = CloudJobStore(FirestoreDocStore(cfg.project, cfg.database), cfg, max_events_per_run=settings.max_events_per_run)
    holder = os.environ.get("CLOUD_RUN_EXECUTION") or f"manual-{os.getpid()}-{secrets.token_hex(3)}"
    drain(settings, cfg, jobs, GcsBlobStore(cfg.project, cfg.bucket), CloudRunJobLauncher(cfg.project, cfg.region, cfg.worker_job),
          holder, install_signals=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
