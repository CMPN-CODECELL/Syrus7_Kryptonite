"""Long-lived job supervisor (one per data directory).

Started by the API (`python -m quantfolio.jobs.worker --parent-pid <api pid>`)
or by hand. It:

* holds an exclusive lock file so two supervisors can never run jobs from the
  same database (which would mean duplicate execution);
* reconciles on start: jobs left `running` by a dead worker become `failed`
  (`interrupted_by_restart`) exactly once; `queued` jobs stay queued;
* claims queued jobs one at a time with an atomic conditional UPDATE;
* runs each job in its own spawned child process (pre-spawned while idle so
  heavy imports are already done), relays progress into the DB with
  iteration events coalesced to <= QF_ITERATION_FLUSH_HZ per second, enforces
  cancellation (cooperative, then hard kill after a grace period) and the
  wall-clock timeout (hard kill -> `timed_out`), and maps a dead child to
  `failed` (`worker_crashed`);
* exits when its parent (the API) disappears or a stop is requested, killing
  only processes it started.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from ..logging_utils import configure_logging, run_id_var, safe_message
from ..settings import Settings, get_settings
from .child import child_main
from .fsutil import atomic_write_text, safe_rmtree
from .store import JobStore, iso

log = logging.getLogger("quantfolio.worker")

STOP_META_KEY = "worker_stop_requested"
LOCK_NAME = "worker.lock"
PID_NAME = "worker.pid"


class AlreadyRunning(RuntimeError):
    pass


class SingleInstanceLock:
    """Exclusive, crash-safe lock: the OS releases it when the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - POSIX
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunning(str(exc)) from exc
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


class ChildHandle:
    def __init__(self, ctx: Any, settings: Settings):
        self.parent_conn, child_conn = ctx.Pipe(duplex=True)
        self.cancel_event = ctx.Event()
        me = psutil.Process()
        self.proc = ctx.Process(
            target=child_main,
            args=(child_conn, self.cancel_event, me.pid, me.create_time(), settings.pipeline_impl, settings.log_level),
            name="quantfolio-job",
        )
        self.proc.start()
        child_conn.close()
        self.ready = False
        self.spawned_at = time.monotonic()

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    def alive(self) -> bool:
        return self.proc.is_alive()

    def kill_tree(self, timeout: float = 5.0) -> None:
        """Hard-stop this child and anything it started (all ours)."""
        pid = self.proc.pid
        procs: list[psutil.Process] = []
        if pid is not None:
            try:
                parent = psutil.Process(pid)
                procs = parent.children(recursive=True) + [parent]
            except psutil.Error:
                procs = []
        for p in procs:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=timeout)
        self.proc.join(timeout=timeout)
        self.close()

    def close(self) -> None:
        try:
            self.parent_conn.close()
        except OSError:
            pass
        try:
            self.proc.close()
        except ValueError:
            pass  # still running (only after a failed kill)


class Supervisor:
    def __init__(self, settings: Settings, parent_pid: int | None = None, once: bool = False):
        self.s = settings
        self.store = JobStore(settings.db_path, settings.max_events_per_run)
        self.parent_pid = parent_pid
        self.parent_create: float | None = None
        if parent_pid:
            try:
                self.parent_create = psutil.Process(parent_pid).create_time()
            except psutil.Error:
                self.parent_create = None
        self.once = once
        self.ctx = mp.get_context("spawn")
        self.lock = SingleInstanceLock(settings.state_dir / LOCK_NAME)
        self.pid = os.getpid()
        self.started_at = iso()
        self.warm: ChildHandle | None = None
        self.current_run: str | None = None
        self.current_child: ChildHandle | None = None
        self._stop = False
        self._last_beat = 0.0
        self.jobs_done = 0

    # ------------------------------------------------------------------ #
    def request_stop(self, *_: Any) -> None:
        self._stop = True

    def should_stop(self) -> bool:
        if self._stop:
            return True
        if self.parent_pid:
            try:
                p = psutil.Process(self.parent_pid)
                if not p.is_running() or (self.parent_create and abs(p.create_time() - self.parent_create) > 1.0):
                    log.warning("parent process gone; stopping")
                    self._stop = True
            except psutil.Error:
                log.warning("parent process gone; stopping")
                self._stop = True
        if not self._stop:
            flag = self.store.get_meta(STOP_META_KEY)
            if flag and flag in (str(self.pid), "*"):
                log.info("stop requested via database flag")
                self._stop = True
        return self._stop

    def beat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_beat < 1.0:
            return
        self._last_beat = now
        child = self.current_child or self.warm
        info: dict[str, Any] = {"jobs_done": self.jobs_done, "impl": self.s.pipeline_impl, "warm_ready": bool(self.warm and self.warm.ready)}
        try:
            info["rss_mb"] = round(psutil.Process().memory_info().rss / 2**20, 1)
            if child and child.pid:
                info["child_rss_mb"] = round(psutil.Process(child.pid).memory_info().rss / 2**20, 1)
        except psutil.Error:
            pass
        try:
            self.store.heartbeat(self.pid, self.started_at, self.current_run, child.pid if child else None, info)
        except Exception:
            log.warning("heartbeat write failed", exc_info=True)

    def _spawn_warm(self) -> None:
        if self.warm is None and self.s.warm_worker and not self._stop:
            try:
                self.warm = ChildHandle(self.ctx, self.s)
            except Exception:
                log.error("could not spawn warm child", exc_info=True)
                self.warm = None

    def _poll_warm(self) -> None:
        w = self.warm
        if w is None:
            return
        if not w.alive():
            log.warning("warm child exited early (code %s); respawning", w.proc.exitcode)
            w.close()
            self.warm = None
            return
        try:
            while w.parent_conn.poll():
                msg = w.parent_conn.recv()
                if msg and msg[0] == "ready":
                    w.ready = True
        except (EOFError, OSError):
            w.kill_tree()
            self.warm = None

    # ------------------------------------------------------------------ #
    def run_forever(self) -> int:
        try:
            self.lock.acquire()
        except AlreadyRunning:
            log.error("another worker already holds the lock; exiting")
            return 3
        try:
            atomic_write_text(self.s.state_dir / PID_NAME, json.dumps({"pid": self.pid, "create_time": psutil.Process().create_time(),
                                                                         "parent_pid": self.parent_pid}))
            if self.store.get_meta(STOP_META_KEY) in (str(self.pid), "*"):
                self.store.set_meta(STOP_META_KEY, None)
            reconciled = self.store.reconcile_interrupted()
            if reconciled:
                log.warning("reconciled interrupted jobs", extra={"run_ids": reconciled})
            self._cleanup_stale_tmp()
            self.beat(force=True)
            log.info("worker started", extra={"pid": self.pid, "impl": self.s.pipeline_impl})
            while not self.should_stop():
                self._spawn_warm()
                self._poll_warm()
                self.beat()
                job = self.store.claim_next(self.pid)
                if job is None:
                    time.sleep(0.2)
                    continue
                self._execute(job)
                self.jobs_done += 1
                if self.once:
                    break
            return 0
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        if self.warm is not None:
            try:
                self.warm.parent_conn.send(("exit",))
            except OSError:
                pass
            self.warm.proc.join(timeout=2)
            if self.warm.proc.is_alive():
                self.warm.kill_tree()
            else:
                self.warm.close()
            self.warm = None
        try:
            self.store.clear_heartbeat(self.pid)
        except Exception:
            pass
        try:
            pid_file = self.s.state_dir / PID_NAME
            if pid_file.is_file() and json.loads(pid_file.read_text()).get("pid") == self.pid:
                pid_file.unlink()
        except (OSError, ValueError):
            pass
        self.lock.release()
        log.info("worker stopped", extra={"pid": self.pid})

    def _cleanup_stale_tmp(self) -> None:
        runs = self.s.runs_dir
        if not runs.is_dir():
            return
        for d in runs.iterdir():
            tmp = d / "tmp"
            if tmp.is_dir():
                safe_rmtree(tmp, runs)

    # ------------------------------------------------------------------ #
    def _execute(self, job: Any) -> None:
        run_id = job["run_id"]
        run_id_var.set(run_id)
        self.current_run = run_id
        artifacts = self.s.runs_dir / run_id
        artifacts.mkdir(parents=True, exist_ok=True)
        timeout_s = float(job["timeout_s"])
        t_start = time.monotonic()
        try:
            child = self.warm if (self.warm is not None and self.warm.alive()) else None
            self.warm = None
            if child is None:
                child = ChildHandle(self.ctx, self.s)
            self.current_child = child
            child.cancel_event.clear()
            child.parent_conn.send(("job", {"run_id": run_id, "kind": job["kind"], "config": json.loads(job["config_json"]),
                                            "artifacts_dir": str(artifacts)}))
            self.beat(force=True)
            outcome = self._supervise(job, child, t_start, timeout_s)
        except Exception as exc:  # supervisor-side failure: never leave the job running
            log.error("supervisor error", exc_info=True)
            outcome = ("failed", {"code": "worker_error", "message": safe_message(f"{type(exc).__name__}: {exc}")})
            if self.current_child is not None:
                self.current_child.kill_tree()
        finally:
            self.current_child = None
        self._finish(job, artifacts, outcome)
        self.current_run = None
        run_id_var.set(None)
        self.beat(force=True)

    def _supervise(self, job: Any, child: ChildHandle, t_start: float, timeout_s: float) -> tuple[str, dict[str, Any]]:
        run_id = job["run_id"]
        flush_interval = 1.0 / max(0.5, self.s.iteration_flush_hz)
        pending: list[dict[str, Any]] = []
        last_flush = 0.0
        last_cancel_check = 0.0
        cancel_deadline: float | None = None
        stage: str | None = "loading_data"
        log_events = 0
        conn = child.parent_conn

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            if pending and (force or time.monotonic() - last_flush >= flush_interval):
                self.store.record_iterations(run_id, stage, list(pending))
                pending.clear()
                last_flush = time.monotonic()

        while True:
            # drain messages
            try:
                got = conn.poll(0.05)
                while got:
                    msg = conn.recv()
                    kind = msg[0]
                    if kind == "iter":
                        pending.append(msg[1])
                    elif kind == "stage":
                        flush(force=True)
                        stage = msg[1]
                        self.store.record_stage(run_id, msg[1], msg[2], msg[3] if len(msg) > 3 else None)
                    elif kind == "log":
                        if log_events < 200:
                            log_events += 1
                            self.store.add_event(run_id, "log", stage, {"level": msg[1], "message": safe_message(msg[2])})
                    elif kind == "ready":
                        child.ready = True
                    elif kind == "done":
                        flush(force=True)
                        child.proc.join(timeout=10)
                        if child.proc.is_alive():
                            child.kill_tree()
                        else:
                            child.close()
                        return msg[1], msg[2]
                    got = conn.poll(0)
            except (EOFError, OSError):
                flush(force=True)
                child.proc.join(timeout=2)
                code = child.proc.exitcode
                child.kill_tree()
                if cancel_deadline is not None:
                    return "cancelled", {}
                return "failed", {"code": "worker_crashed",
                                  "message": f"The job process exited unexpectedly (exit code {code}). Partial output was discarded."}

            flush()
            now = time.monotonic()
            self.beat()

            if not child.alive():
                # drain anything sent right before exit, then classify
                try:
                    while conn.poll(0):
                        msg = conn.recv()
                        if msg[0] == "iter":
                            pending.append(msg[1])
                        elif msg[0] == "done":
                            flush(force=True)
                            child.close()
                            return msg[1], msg[2]
                except (EOFError, OSError):
                    pass
                flush(force=True)
                code = child.proc.exitcode
                child.kill_tree()
                if cancel_deadline is not None:
                    return "cancelled", {}
                return "failed", {"code": "worker_crashed",
                                  "message": f"The job process exited unexpectedly (exit code {code}). Partial output was discarded."}

            if now - t_start > timeout_s:
                flush(force=True)
                child.kill_tree()
                return "timed_out", {"code": "timeout",
                                     "message": f"Stopped after exceeding the wall-clock limit of {int(timeout_s)} s."}

            if now - last_cancel_check >= 0.2:
                last_cancel_check = now
                if cancel_deadline is None and self.store.is_cancel_requested(run_id):
                    child.cancel_event.set()
                    cancel_deadline = now + self.s.cancel_grace_s
                    log.info("cancellation signalled to child")
                if cancel_deadline is None and self.should_stop():
                    child.cancel_event.set()
                    child.kill_tree()
                    flush(force=True)
                    return "failed", {"code": "interrupted_by_shutdown",
                                      "message": "The server was stopped while this job was running. It was not re-run."}
            if cancel_deadline is not None and now > cancel_deadline:
                flush(force=True)
                child.kill_tree()
                log.info("child did not stop within grace period; killed")
                return "cancelled", {"forced": True}

    def _finish(self, job: Any, artifacts: Path, outcome: tuple[str, dict[str, Any]]) -> None:
        run_id = job["run_id"]
        state, payload = outcome
        safe_rmtree(artifacts / "tmp", self.s.runs_dir)
        if state == "succeeded":
            result_file = artifacts / payload.get("result_file", "result.json")
            if not result_file.is_file():
                state, payload = "failed", {"code": "result_missing", "message": "The job finished but its result file is missing."}
            else:
                ok = self.store.transition(
                    run_id, "succeeded", stage="done", progress_message="Completed",
                    summary_json=json.dumps(payload.get("summary") or {}), result_path=f"{run_id}/{result_file.name}",
                )
                if not ok:
                    log.warning("job was no longer running when it succeeded")
                return
        if state == "cancelled":
            msg = "Cancelled by request" + (" (stopped forcibly after the grace period)" if payload.get("forced") else "")
            if not self.store.transition(run_id, "cancelled", progress_message=msg):
                log.warning("cancel transition did not apply")
            self._discard_partial(artifacts)
            return
        if state == "timed_out":
            self.store.transition(run_id, "timed_out", error_code=payload["code"], error_message=payload["message"])
            self._discard_partial(artifacts)
            return
        code = str(payload.get("code") or "failed")[:64]
        message = safe_message(payload.get("message") or "The job failed.")
        details = payload.get("details")
        if details:
            try:
                self.store.add_event(run_id, "log", None, {"level": "error", "message": message, "details": details})
            except Exception:
                pass
        self.store.transition(run_id, "failed", error_code=code, error_message=message)
        self._discard_partial(artifacts)

    def _discard_partial(self, artifacts: Path) -> None:
        # Keep the directory (it may hold diagnostics) but never a half result.
        partial = artifacts / "result.json"
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QuantFolio job supervisor")
    ap.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits")
    ap.add_argument("--once", action="store_true", help="run at most one job then exit (tests)")
    args = ap.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.data_dir / "logs" / "worker.log")
    sup = Supervisor(settings, parent_pid=args.parent_pid, once=args.once)
    signal.signal(signal.SIGINT, sup.request_stop)
    signal.signal(signal.SIGTERM, sup.request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, sup.request_stop)
    return sup.run_forever()


if __name__ == "__main__":
    sys.exit(main())
