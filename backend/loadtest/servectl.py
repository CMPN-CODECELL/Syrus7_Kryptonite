"""Start/stop an isolated QuantFolio server for benchmarks and load tests.

Only processes this helper started are ever stopped (graceful stop file
first, then the recorded process tree). Each run gets its own data dir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
TMP = PROJECT / ".tmp"


def prepare_data_dir(path: Path, with_datasets: bool = True) -> Path:
    """A fresh data dir; the real pipeline needs the cached price datasets."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    if with_datasets:
        shutil.copytree(PROJECT / "data" / "cache" / "datasets", path / "cache" / "datasets")
    return path


class Server:
    def __init__(self, port: int, data_dir: Path, impl: str = "fake", env: dict[str, str] | None = None) -> None:
        self.port, self.data_dir, self.impl = port, data_dir, impl
        self.base = f"http://127.0.0.1:{port}"
        self.extra_env = env or {}
        self.proc: subprocess.Popen[bytes] | None = None
        self.tree: list[psutil.Process] = []
        self.log_path = data_dir / "serve.out.log"

    def start(self, timeout: float = 120) -> Server:
        env = os.environ.copy()
        env.pop("IBM_QUANTUM_TOKEN", None)  # load tests never use IBM
        env.update({"QF_DATA_DIR": str(self.data_dir), "QF_PIPELINE_IMPL": self.impl, "PYTHONPATH": str(BACKEND),
                    "TEMP": str(TMP), "TMP": str(TMP), "QF_LOG_LEVEL": "WARNING"})
        env.update(self.extra_env)
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen([sys.executable, "-m", "quantfolio.serve", "--port", str(self.port)],
                                     cwd=str(BACKEND), env=env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited with {self.proc.returncode}; see {self.log_path}")
            try:
                if httpx.get(self.base + "/api/ready", timeout=2).json()["checks"].get("worker"):
                    self.refresh_tree()
                    return self
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError("server did not become ready")

    def refresh_tree(self) -> list[psutil.Process]:
        assert self.proc is not None
        try:
            root = psutil.Process(self.proc.pid)
            seen = {p.pid: p for p in self.tree if p.is_running()}  # keep objects: cpu_percent is per-object
            for p in [root, *root.children(recursive=True)]:
                seen.setdefault(p.pid, p)
            self.tree = list(seen.values())
        except psutil.NoSuchProcess:
            pass
        return self.tree

    def api_pid(self) -> int:
        return int(json.loads((self.data_dir / "state" / "api.pid").read_text(encoding="utf-8"))["pid"])

    def resources(self) -> dict[str, float]:
        """CPU % (sum over our tree, since last call) and RSS MB of our tree."""
        cpu = rss = 0.0
        for p in self.refresh_tree():
            try:
                cpu += p.cpu_percent(None)
                rss += p.memory_info().rss
            except psutil.Error:
                pass
        return {"cpu_pct": round(cpu, 1), "rss_mb": round(rss / 2**20, 1), "procs": len([p for p in self.tree if p.is_running()])}

    def stop(self, timeout: float = 30) -> dict[str, object]:
        """Graceful stop via the stop file; force-kill only our own tree if needed."""
        if self.proc is None:
            return {}
        self.refresh_tree()
        t0 = time.monotonic()
        (self.data_dir / "state").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "state" / "api.stop").write_text("stop", encoding="utf-8")
        graceful = True
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            graceful = False
        _, alive = psutil.wait_procs(self.tree, timeout=10)
        forced = []
        for p in alive:  # ours by construction (descendants of the process we spawned)
            try:
                p.kill()
                forced.append(p.pid)
            except psutil.Error:
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        self._log.close()
        return {"graceful": graceful and not forced, "stop_s": round(time.monotonic() - t0, 2), "orphans_killed": forced,
                "exit_code": self.proc.returncode}
