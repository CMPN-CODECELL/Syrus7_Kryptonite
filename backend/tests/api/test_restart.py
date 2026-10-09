"""Restart semantics: interrupted jobs are failed exactly once and never
silently re-run; a killed supervisor is restarted; shutdown never leaves a
job running; only one supervisor may own a data directory."""

from __future__ import annotations

import subprocess
import time

import psutil
import pytest
from qf_api_helpers import BACKEND, default_config, project_env, submit, unique_seed, venv_python, wait_ready_worker, wait_status

pytestmark = pytest.mark.worker


def test_running_job_is_failed_on_restart_and_queued_job_runs(app_client):
    c1 = app_client()  # API only
    a = submit(c1, default_config(c1, seed=unique_seed())).json()["run_id"]
    b = submit(c1, default_config(c1, seed=unique_seed())).json()["run_id"]
    assert c1.app.state.qf.store.claim_next(worker_pid=999_999)["run_id"] == a  # a dead worker had claimed it
    c1.__exit__(None, None, None)
    c2 = app_client(start_worker=True)
    wait_ready_worker(c2)
    sa = wait_status(c2, a)
    assert sa["status"] == "failed" and sa["error"]["code"] == "interrupted_by_restart"
    assert wait_status(c2, b)["status"] == "succeeded"
    errors = [e for e in c2.get(f"/api/runs/{a}/events/snapshot").json() if e["type"] == "error"]
    assert len(errors) == 1


def test_killed_supervisor_is_restarted(app_client, monkeypatch):
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.1")
    c = app_client(start_worker=True)
    wait_ready_worker(c)
    rid = submit(c, default_config(c, seed=unique_seed(), max_evaluations=500)).json()["run_id"]
    wait_status(c, rid, {"running"})
    w = c.app.state.qf.worker
    old_pid = w.pid
    tree = [psutil.Process(p) for p in w.tree_pids()]
    assert len(tree) >= 2  # supervisor + job child
    psutil.Process(old_pid).kill()  # crash only the supervisor (ours); its child must notice and exit
    st = wait_status(c, rid, timeout=40)
    assert st["status"] == "failed" and st["error"]["code"] == "interrupted_by_restart"
    assert w.pid != old_pid and w.alive()
    gone, alive = psutil.wait_procs(tree, timeout=15)
    assert not alive, f"orphaned job processes: {[p.pid for p in alive]}"
    nxt = submit(c, default_config(c, seed=unique_seed(), max_evaluations=5)).json()["run_id"]
    assert wait_status(c, nxt, timeout=60)["status"] == "succeeded"


def test_shutdown_with_running_job_fails_it_cleanly(app_client, monkeypatch, make_settings):
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.1")
    c = app_client(start_worker=True)
    wait_ready_worker(c)
    rid = submit(c, default_config(c, seed=unique_seed(), max_evaluations=500)).json()["run_id"]
    wait_status(c, rid, {"running"})
    tree = [psutil.Process(p) for p in c.app.state.qf.worker.tree_pids()]
    t0 = time.monotonic()
    c.__exit__(None, None, None)
    assert time.monotonic() - t0 < 20
    _, alive = psutil.wait_procs(tree, timeout=10)
    assert not alive
    from quantfolio.jobs.store import JobStore

    row = JobStore(make_settings().db_path).get(rid)
    assert row["status"] == "failed" and row["error_code"] == "interrupted_by_shutdown"


def test_second_supervisor_is_refused(wclient, make_settings):
    s = make_settings()
    env = project_env({"QF_DATA_DIR": str(s.data_dir), "QF_PIPELINE_IMPL": "fake"})
    p = subprocess.run([venv_python(), "-m", "quantfolio.jobs.worker"], cwd=str(BACKEND), env=env, capture_output=True, timeout=60)
    assert p.returncode == 3
    assert wclient.get("/api/ready").json()["checks"]["worker"] is True
