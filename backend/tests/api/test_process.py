"""The production entrypoint as a real OS process: PID file, port-in-use exit
code, a job over real HTTP + SSE, and a clean stop via the stop file with no
orphaned processes."""

from __future__ import annotations

import json
import socket
import subprocess
import time

import httpx
import psutil
import pytest
from qf_api_helpers import BACKEND, project_env, venv_python

pytestmark = [pytest.mark.process, pytest.mark.worker]


def _free_port() -> int:
    for port in range(8150, 8300):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")


def test_serve_lifecycle(data_dir, no_orphans):
    port = _free_port()
    env = project_env({"QF_DATA_DIR": str(data_dir), "QF_PIPELINE_IMPL": "fake", "QF_FAKE_STEP_S": "0.005"})
    cmd = [venv_python(), "-m", "quantfolio.serve", "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=str(BACKEND), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(base + "/api/ready", timeout=2).json()["checks"]["worker"]:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        else:
            raise AssertionError("server did not become ready")
        pid_file = data_dir / "state" / "api.pid"
        info = json.loads(pid_file.read_text(encoding="utf-8"))
        # a Windows venv python.exe is a launcher; the server is it or its direct child
        ours = {proc.pid} | {p.pid for p in psutil.Process(proc.pid).children()}
        assert info["pid"] in ours and info["port"] == port
        assert abs(psutil.Process(info["pid"]).create_time() - info["create_time"]) < 1

        dup = subprocess.run(cmd, cwd=str(BACKEND), env=env, capture_output=True, timeout=60)
        assert dup.returncode == 4 and b"already in use" in dup.stderr
        assert json.loads(pid_file.read_text(encoding="utf-8"))["pid"] == info["pid"]  # not clobbered

        with httpx.Client(base_url=base, timeout=30) as h:
            assert h.get("/api/health").headers.get("server") is None
            cfg = h.get("/api/presets").json()["default_config"]
            rid = h.post("/api/runs", json=cfg).json()["run_id"]
            with h.stream("GET", f"/api/runs/{rid}/events") as resp:
                lines = [ln for ln in resp.iter_lines() if ln.startswith("event:")]
            assert lines[-1] in ("event: status", "event: result")
            assert h.get(f"/api/runs/{rid}").json()["status"] == "succeeded"
            assert h.get(f"/api/runs/{rid}/report.html").status_code == 200

        tree = [psutil.Process(proc.pid)] + psutil.Process(proc.pid).children(recursive=True)
        assert len(tree) >= 2  # API + supervisor (+ warm child)
        (data_dir / "state" / "api.stop").write_text("stop", encoding="utf-8")
        assert proc.wait(timeout=30) == 0
        _, alive = psutil.wait_procs(tree, timeout=15)
        assert not alive
        assert not pid_file.exists() and not (data_dir / "state" / "api.stop").exists()
        out = proc.stdout.read().decode(errors="replace")
        assert f"http://127.0.0.1:{port}" in out
    finally:
        if proc.poll() is None:  # only our own process tree
            for p in psutil.Process(proc.pid).children(recursive=True) + [psutil.Process(proc.pid)]:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        proc.stdout.close()
