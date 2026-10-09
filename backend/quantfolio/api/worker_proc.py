"""Start/monitor/stop the job supervisor process from the API process.

Only processes started here are ever stopped here (the Popen handle and its
descendants, found through psutil)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections import deque

import psutil

from ..jobs.store import JobStore
from ..jobs.worker import STOP_META_KEY
from ..settings import BACKEND_DIR, Settings

log = logging.getLogger("quantfolio.api.worker")


class WorkerProcess:
    def __init__(self, settings: Settings, store: JobStore):
        self.s = settings
        self.store = store
        self.proc: subprocess.Popen[bytes] | None = None
        self.restarts: deque[float] = deque(maxlen=10)
        self.stopping = False
        self.gave_up = False

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def start(self) -> None:
        env = os.environ.copy()
        env.pop("IBM_QUANTUM_TOKEN", None)  # least privilege: jobs never talk to IBM
        env["PYTHONPATH"] = str(BACKEND_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # console Ctrl+C goes to the API, which stops us cleanly
        self.store.set_meta(STOP_META_KEY, None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "quantfolio.jobs.worker", "--parent-pid", str(os.getpid())],
            cwd=str(BACKEND_DIR),
            env=env,
            creationflags=flags,
        )
        log.info("worker process started", extra={"pid": self.proc.pid})

    def check(self) -> None:
        """Restart the supervisor if it died unexpectedly (bounded retries)."""
        if self.stopping or self.proc is None or self.proc.poll() is None or self.gave_up:
            return
        code = self.proc.returncode
        now = time.monotonic()
        recent = [t for t in self.restarts if now - t < 300]
        if code == 3:
            log.error("worker exited: another worker holds the lock for this data directory")
            self.gave_up = True
            return
        if len(recent) >= 5:
            log.error("worker keeps exiting; not restarting again", extra={"exit_code": code})
            self.gave_up = True
            return
        log.warning("worker exited unexpectedly; restarting", extra={"exit_code": code})
        self.restarts.append(now)
        self.start()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def tree_pids(self) -> list[int]:
        if self.proc is None:
            return []
        try:
            p = psutil.Process(self.proc.pid)
            return [p.pid] + [c.pid for c in p.children(recursive=True)]
        except psutil.Error:
            return []

    def stop(self, timeout: float = 10.0) -> None:
        self.stopping = True
        if self.proc is None:
            return
        try:
            root = psutil.Process(self.proc.pid)
            tree = [root] + root.children(recursive=True)
        except psutil.Error:
            tree = []
        try:
            self.store.set_meta(STOP_META_KEY, "*")
        except Exception:
            log.warning("could not write stop flag", exc_info=True)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("worker did not stop in time; killing its process tree")
        # Kill anything of ours still alive (the tree captured above + late children).
        try:
            tree += psutil.Process(self.proc.pid).children(recursive=True)
        except psutil.Error:
            pass
        alive = []
        for p in tree:
            try:
                if p.is_running():
                    p.kill()
                    alive.append(p)
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        log.info("worker process stopped")
