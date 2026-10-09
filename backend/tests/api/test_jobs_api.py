"""Job lifecycle through the API with the real supervisor process and the fake
pipeline: success, idempotency, cache replays, saturation, cancel (queued,
running, races), timeouts, crashes, redaction and experiments."""

from __future__ import annotations

import json
import time

import pytest
from qf_api_helpers import TERMINAL, default_config, submit, unique_seed, wait_status

from quantfolio.contracts import ExperimentResult, JobStatus, RunResult

pytestmark = pytest.mark.worker


def _cfg(c, **qaoa):
    qaoa.setdefault("seed", unique_seed())
    return default_config(c, **qaoa)


def test_run_succeeds_with_progress_and_valid_result(wclient):
    cfg = _cfg(wclient)
    r = submit(wclient, cfg)
    assert r.status_code == 202
    rid = r.json()["run_id"]
    st = wait_status(wclient, rid)
    assert st["status"] == "succeeded", st
    JobStatus.model_validate(st)
    assert st["progress"]["evaluations_done"] == st["progress"]["evaluation_budget"] == cfg["qaoa"]["max_evaluations"]
    assert st["progress"]["stage"] == "done" and st["error"] is None
    res = wclient.get(f"/api/runs/{rid}/result")
    assert res.status_code == 200
    rr = RunResult.model_validate(res.json())
    assert rr.run_id == rid
    assert len(rr.qaoa.iterations) == cfg["qaoa"]["max_evaluations"]
    summary = [x for x in wclient.get("/api/runs").json() if x["run_id"] == rid][0]
    assert summary["status"] == "succeeded"


def test_idempotency_key_replay_and_conflict(client):
    cfg = _cfg(client)
    a = submit(client, cfg, key="key-0001")
    b = submit(client, cfg, key="key-0001")
    assert a.status_code == 202 and b.status_code == 200
    assert a.json()["run_id"] == b.json()["run_id"]
    cfg2 = dict(cfg, risk_aversion=cfg["risk_aversion"] + 1)
    c = submit(client, cfg2, key="key-0001")
    assert c.status_code == 409 and c.json()["error"]["code"] == "idempotency_key_reused"
    assert client.get("/api/runs").headers["x-total-count"] == "1"


def test_identical_active_submission_is_deduplicated(client):
    cfg = _cfg(client)
    a, b = submit(client, cfg), submit(client, cfg)
    assert (a.status_code, b.status_code) == (202, 200)
    assert a.json()["run_id"] == b.json()["run_id"]


def test_cache_replay_and_force_fresh(wclient):
    cfg = _cfg(wclient)
    rid = submit(wclient, cfg).json()["run_id"]
    assert wait_status(wclient, rid)["status"] == "succeeded"
    rep = submit(wclient, cfg)
    assert rep.status_code == 200
    body = rep.json()
    assert body["status"] == "succeeded" and body["cached_from"] == rid and body["run_id"] != rid
    assert body["cached_original_time"]
    assert wclient.get(f"/api/runs/{body['run_id']}/result").content == wclient.get(f"/api/runs/{rid}/result").content
    fresh = submit(wclient, cfg, force_fresh=True)
    assert fresh.status_code == 202 and fresh.json()["cached_from"] is None
    assert wait_status(wclient, fresh.json()["run_id"])["status"] == "succeeded"


def test_missing_cached_result_falls_back_to_fresh_run(wclient, make_settings):
    cfg = _cfg(wclient)
    rid = submit(wclient, cfg).json()["run_id"]
    assert wait_status(wclient, rid)["status"] == "succeeded"
    (make_settings().runs_dir / rid / "result.json").unlink()
    r = submit(wclient, cfg)
    assert r.status_code == 202 and r.json()["cached_from"] is None
    assert wclient.get(f"/api/runs/{rid}/result").status_code == 410


def test_queue_saturation_returns_429_with_retry_after(app_client):
    c = app_client(queue_capacity=2)  # no worker: jobs stay queued
    ids = [submit(c, _cfg(c)).json()["run_id"] for _ in range(2)]
    first_key = submit(c, _cfg(c), key="sat-key-1")
    assert first_key.status_code == 429
    err = first_key.json()["error"]
    assert err["code"] == "queue_full" and err["details"] == {"queue_depth": 2, "capacity": 2}
    assert 5 <= int(first_key.headers["retry-after"]) <= 120
    # cancelling frees a slot
    assert c.post(f"/api/runs/{ids[0]}/cancel").json()["status"] == "cancelled"
    assert submit(c, _cfg(c)).status_code == 202
    assert c.get("/api/capabilities").json()["queue_capacity"] == 2


def test_cancel_queued_and_terminal(client):
    rid = submit(client, _cfg(client)).json()["run_id"]
    r = client.post(f"/api/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    again = client.post(f"/api/runs/{rid}/cancel")
    assert again.status_code == 200 and again.json()["status"] == "cancelled"
    assert client.get(f"/api/runs/{rid}/result").json()["error"]["code"] == "result_not_available"
    assert client.post("/api/runs/run-20260101-00000000/cancel").status_code == 404


def test_cancel_running_job(app_client, monkeypatch):
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.1")
    c = app_client(start_worker=True)
    rid = submit(c, _cfg(c, max_evaluations=400)).json()["run_id"]
    wait_status(c, rid, {"running"})
    t0 = time.monotonic()
    r = c.post(f"/api/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] in ("running", "cancelled")
    st = wait_status(c, rid, timeout=20)
    assert st["status"] == "cancelled"
    assert time.monotonic() - t0 < 8
    assert st["progress"]["evaluations_done"] < 400
    assert c.get(f"/api/runs/{rid}/result").status_code == 409


def test_uncooperative_job_is_force_cancelled_after_grace(app_client):
    c = app_client(start_worker=True, cancel_grace_s=1.0)
    rid = submit(c, _cfg(c, seed=888)).json()["run_id"]
    deadline = time.monotonic() + 30
    while c.get(f"/api/runs/{rid}").json()["progress"]["stage"] != "optimising" and time.monotonic() < deadline:
        time.sleep(0.05)  # the hang starts inside the optimiser loop
    time.sleep(0.3)
    c.post(f"/api/runs/{rid}/cancel")
    st = wait_status(c, rid, timeout=20)
    assert st["status"] == "cancelled"
    events = c.get(f"/api/runs/{rid}/events/snapshot").json()
    msgs = [e["data"].get("progress", {}).get("message") for e in events if e["type"] == "status"]
    assert any("forcibly" in (m or "") for m in msgs), msgs


def test_cancel_race_never_leaves_inconsistent_state(wclient):
    rids = []
    for i in range(8):
        rid = submit(wclient, _cfg(wclient)).json()["run_id"]
        if i % 2 == 0:
            time.sleep(0.05 * i)
        wclient.post(f"/api/runs/{rid}/cancel")
        rids.append(rid)
    for rid in rids:
        st = wait_status(wclient, rid)
        assert st["status"] in ("cancelled", "succeeded"), st
        res = wclient.get(f"/api/runs/{rid}/result")
        assert res.status_code == (200 if st["status"] == "succeeded" else 409)
        statuses = [e["data"]["status"] for e in wclient.get(f"/api/runs/{rid}/events/snapshot").json() if e["type"] == "status"]
        assert sum(s in TERMINAL for s in statuses) == 1, statuses
    # the worker is still healthy afterwards
    rid = submit(wclient, _cfg(wclient)).json()["run_id"]
    assert wait_status(wclient, rid)["status"] == "succeeded"


@pytest.mark.slow
def test_hung_job_times_out(wclient):
    cfg = _cfg(wclient, seed=888)
    cfg["timeout_seconds"] = 10
    rid = submit(wclient, cfg).json()["run_id"]
    t0 = time.monotonic()
    st = wait_status(wclient, rid, timeout=40)
    assert st["status"] == "timed_out"
    assert st["error"]["code"] == "timeout"
    assert 9 <= time.monotonic() - t0 < 30
    assert wclient.get(f"/api/runs/{rid}/result").status_code == 409
    nxt = submit(wclient, _cfg(wclient)).json()["run_id"]
    assert wait_status(wclient, nxt)["status"] == "succeeded"


def test_crash_is_isolated_and_next_job_runs(wclient):
    crash = submit(wclient, _cfg(wclient, seed=666)).json()["run_id"]
    after = submit(wclient, _cfg(wclient)).json()["run_id"]  # queued behind the crashing job
    st = wait_status(wclient, crash)
    assert st["status"] == "failed" and st["error"]["code"] == "worker_crashed"
    assert "exit code 3" in st["error"]["message"]
    assert wait_status(wclient, after)["status"] == "succeeded"
    assert wclient.get("/api/health").status_code == 200
    assert not (wclient.app.state.qf.settings.runs_dir / crash / "result.json").exists()


def test_unexpected_exception_is_redacted(wclient, fake_token):
    rid = submit(wclient, _cfg(wclient, seed=777)).json()["run_id"]
    st = wait_status(wclient, rid)
    assert st["status"] == "failed" and st["error"]["code"] == "internal_error"
    blob = json.dumps(st) + json.dumps(wclient.get(f"/api/runs/{rid}/events/snapshot").json())
    for leak in ("sk-test-secret", "C:\\\\Users", "someone\\\\secret", fake_token):
        assert leak not in blob, leak


def test_pipeline_error_maps_to_its_code(wclient):
    rid = submit(wclient, _cfg(wclient, seed=999)).json()["run_id"]
    st = wait_status(wclient, rid)
    assert st["status"] == "failed" and st["error"]["code"] == "infeasible_config"
    assert "cannot be satisfied" in st["error"]["message"]


def test_experiment_runs_and_reports(wclient):
    base = _cfg(wclient, max_evaluations=5)
    exp = {"base": base, "depths": [1, 2], "optimizers": [], "init_strategies": [], "seeds": [3], "max_cells": 10}
    r = submit(wclient, exp, path="/api/experiments")
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    assert rid.startswith("exp-")
    st = wait_status(wclient, rid)
    assert st["status"] == "succeeded" and st["kind"] == "experiment"
    er = ExperimentResult.model_validate(wclient.get(f"/api/runs/{rid}/result").json())
    assert len(er.cells) == 2
    assert wclient.get(f"/api/runs/{rid}/report.csv").status_code == 200
    assert wclient.get(f"/api/runs/{rid}/report.html").status_code == 200
    assert wclient.get(f"/api/runs/{rid}/counts.csv").json()["error"]["code"] == "not_applicable"
    too_many = dict(exp, depths=[1, 2, 3, 4], seeds=[1, 2, 3], max_cells=5)
    assert submit(wclient, too_many, path="/api/experiments").json()["error"]["code"] == "too_many_cells"
