"""Launching the worker: the second half of the outbox.

A run is durable (persisted, queued, counted) before anything here is called.
`kick` asks Firestore, in a transaction, whether a launch is due - there is
queued work, no live worker lease, compute is enabled, and nobody launched in
the last minute - and only then starts one execution of the worker job. A
launch that fails or is lost is simply attempted again by the next status
read, the next submission or the scheduled sweep; a duplicate launch is
harmless because the second worker cannot take the lease and exits.
"""

from __future__ import annotations

import logging

from ..logging_utils import safe_message
from .jobstore import CloudJobStore
from .launcher import JobLauncher

log = logging.getLogger("quantfolio.cloud.dispatch")


def kick(jobs: CloudJobStore, launcher: JobLauncher, *, force: bool = False) -> bool:
    """Start one worker execution if one is due. Never raises. Returns True if an execution was launched."""
    try:
        if not jobs.decide_kick(force=force):
            return False
    except Exception:
        log.warning("dispatch decision failed", exc_info=True)
        return False
    try:
        execution = launcher.run()
    except Exception as exc:  # noqa: BLE001 - any launch failure is retried later
        log.warning("worker launch failed", extra={"error": safe_message(f"{type(exc).__name__}: {exc}", 300)})
        try:
            jobs.record_kick(error=safe_message(f"{type(exc).__name__}: {exc}", 300))
        except Exception:
            log.warning("could not record the failed launch", exc_info=True)
        return False
    try:
        jobs.record_kick(execution=execution)
    except Exception:
        log.warning("could not record the launch", exc_info=True)
    log.info("worker execution launched", extra={"execution": execution})
    return True
