"""Job child process: runs exactly one pipeline call, then exits.

Spawned (never forked) by the supervisor, optionally ahead of time so heavy
imports are already done when a job arrives ("warm" child). One process per
job means no state leaks between runs and a crash or hard kill only affects
the job it was running.

Protocol over a duplex multiprocessing Pipe (child -> supervisor):
    ("ready", pid)                      imports done, waiting for a job
    ("stage", stage, message, data)
    ("iter", point)
    ("log", level, message)
    ("done", state, payload)            state in succeeded|failed|cancelled
Supervisor -> child: ("job", job_dict) or ("exit",).
Cancellation is a shared multiprocessing Event checked by sink.check_cancelled().
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("quantfolio.child")


def _watch_parent(parent_pid: int, parent_create_time: float) -> None:
    """Exit if the supervisor disappears, so a killed supervisor never leaves
    a computing orphan behind."""
    import psutil

    while True:
        time.sleep(0.5)
        try:
            p = psutil.Process(parent_pid)
            if abs(p.create_time() - parent_create_time) > 1.0 or not p.is_running():
                os._exit(70)
        except psutil.Error:
            os._exit(70)


class PipeSink:
    def __init__(self, conn: Any, cancel_event: Any):
        self._conn = conn
        self._cancel = cancel_event
        self._lock = threading.Lock()

    def _send(self, msg: tuple[Any, ...]) -> None:
        try:
            with self._lock:
                self._conn.send(msg)
        except (BrokenPipeError, EOFError, OSError):
            os._exit(71)  # supervisor gone

    def stage(self, stage: str, message: str = "", **data: Any) -> None:
        self._send(("stage", str(stage), str(message)[:300], _plain(data)))

    def iteration(self, point: dict[str, Any]) -> None:
        self._send(("iter", _plain(point)))

    def log(self, level: str, message: str, **data: Any) -> None:
        self._send(("log", str(level), str(message)[:500]))

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            from ..pipeline import Cancelled

            raise Cancelled()


def _plain(obj: Any) -> Any:
    """Make values picklable/JSON-friendly (numpy scalars -> python)."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(obj)


def child_main(conn: Any, cancel_event: Any, parent_pid: int, parent_create_time: float, impl: str, log_level: str) -> None:
    # Ctrl+C belongs to the supervisor; the child is stopped via the pipe/event.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    threading.Thread(target=_watch_parent, args=(parent_pid, parent_create_time), daemon=True, name="parent-watch").start()

    from ..logging_utils import configure_logging, run_id_var

    configure_logging(log_level)
    from .impl import pipeline_module

    mod = pipeline_module(impl)
    warm = getattr(mod, "warmup", None)
    if callable(warm):
        try:
            warm()
        except Exception:  # warm-up is an optimisation only
            log.warning("pipeline warmup failed", exc_info=True)
    conn.send(("ready", os.getpid()))
    try:
        msg = conn.recv()
    except (EOFError, OSError):
        return
    if not msg or msg[0] != "job":
        return
    job = msg[1]
    run_id_var.set(job["run_id"])
    _run_job(conn, cancel_event, job, mod)
    try:
        conn.close()
    except OSError:
        pass


def _run_job(conn: Any, cancel_event: Any, job: dict[str, Any], mod: Any) -> None:
    from ..contracts import ExperimentConfig, RunConfig
    from ..logging_utils import safe_message
    from ..pipeline import Cancelled, PipelineError
    from .fsutil import atomic_write_text, dumps
    from .impl import run_summary

    run_id = job["run_id"]
    kind = job["kind"]
    artifacts = Path(job["artifacts_dir"])
    tmp = artifacts / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(tmp)
    tempfile.tempdir = str(tmp)
    sink = PipeSink(conn, cancel_event)
    try:
        if kind == "experiment":
            cfg = ExperimentConfig.model_validate(job["config"])
            result = mod.run_experiment(cfg, run_id, sink, str(artifacts))
        else:
            cfg = RunConfig.model_validate(job["config"])
            result = mod.run_optimisation(cfg, run_id, sink, str(artifacts))
        if getattr(result, "run_id", run_id) != run_id:
            result = result.model_copy(update={"run_id": run_id})
        data = result.model_dump(mode="json")
        atomic_write_text(artifacts / "result.json", dumps(data))
        summary = run_summary(kind, result)
        sink._send(("done", "succeeded", {"summary": _plain(summary), "result_file": "result.json"}))
    except Cancelled:
        sink._send(("done", "cancelled", {}))
    except PipelineError as exc:
        sink._send(("done", "failed", {"code": str(exc.code)[:64], "message": safe_message(exc.message),
                                       "details": _plain(exc.details) if exc.details else None}))
    except MemoryError:
        sink._send(("done", "failed", {"code": "out_of_memory", "message": "The job ran out of memory. Reduce assets, depth or shots."}))
    except BaseException as exc:  # noqa: BLE001 - everything else is an internal error, redacted
        log.error("job raised", exc_info=True)
        sink._send(("done", "failed", {"code": "internal_error", "message": safe_message(f"{type(exc).__name__}: {exc}")}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        sys.stdout.flush()
