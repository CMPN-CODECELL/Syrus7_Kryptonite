"""The cloud /api surface: shared public workspace, per-client quotas, kill switch, limits, dispatch and replays."""

from __future__ import annotations

import json
import shutil

import pytest
from conftest import PROJECT, SWEEP_AUDIENCE, SWEEP_SA, make_token
from qf_api_helpers import read_sse

GOOD_CSV = b"Date,INFY,TCS\r\n2024-01-01,100,200\r\n2024-01-02,101,201\r\n2024-01-03,102,199\r\n"
REAL_EXAMPLES = sorted((PROJECT / "data" / "examples").glob("run-*"))


def _err(r):
    return r.json()["error"]


def _upload(env, uid, data=GOOD_CSV, name="prices.csv"):
    return env.client.post("/api/data/upload", files={"file": (name, data, "text/csv")}, headers=env.auth(uid) if uid else {})


# --------------------------------------------------------------------------- public surface
def test_public_reads_need_no_token(env):
    c = env.client
    for path in ("/api/health", "/api/ready", "/api/presets", "/api/capabilities", "/api/example", "/api/ibm/status",
                 "/api/data/status", "/api/assets", "/api/cloud/status", "/api/runs", "/api/market/live",
                 "/api/market/candles"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text)
        assert r.headers["cache-control"] == "no-store", path
    assert c.post("/api/preflight", json=env.config()).status_code == 200
    assert c.get("/api/runs").json() == []
    ready = c.get("/api/ready").json()
    assert ready["ready"] is True and ready["checks"] == {"db": True, "worker": True, "dataset": True}
    assert "on demand" in ready["detail"]["worker"]


def test_debug_and_spa_routes_are_not_served(env):
    c = env.client
    for path in ("/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect", "/", "/index.html", "/assets/app.js"):
        assert c.get(path).status_code == 404, path
    assert _err(c.get("/api/nope"))["code"] == "not_found"


def test_cloud_status_shape(env):
    body = env.client.get("/api/cloud/status").json()
    assert body["mode"] == "cloud" and body["auth"] == "none" and body["compute_enabled"] is True
    assert body["quota"]["global_limit"] == 20 and body["quota"]["global_remaining"] == 20
    assert body["quota"]["user_limit"] == 5 and body["quota"]["user_active_limit"] == 1
    assert body["queue"] == {"depth": 0, "capacity": 6, "running": False}
    assert body["disabled_features"] == ["experiments", "research_preset", "ibm_check"]
    assert body["limits"]["max_qubits"] == 16 and body["limits"]["max_upload_bytes"] == 5 * 1024 * 1024


def test_removed_endpoints(env):
    assert env.client.get("/api/auth/me").status_code == 404
    assert env.client.post("/api/cloud/compute", json={"enabled": False}).status_code in (404, 405)


def test_default_config_is_within_the_public_limits(env):
    from quantfolio.cloud.routes import enforce_limits
    from quantfolio.contracts import RunConfig

    enforce_limits(env.cfg, RunConfig.model_validate(env.config()))


# --------------------------------------------------------------------------- submission and dispatch
def test_submit_persists_then_launches_one_worker(env):
    cfg = env.config()
    r = env.submit("u1", cfg)
    assert r.status_code == 202, r.text
    body = r.json()
    rid = body["run_id"]
    assert r.headers["location"] == f"/api/runs/{rid}" and body["status"] == "queued" and body["queue_position"] == 1
    assert env.launcher.calls == 1
    doc = env.db.get(f"runs/{rid}")
    assert doc["uid"] == env.key("u1") and doc["counted"] is True and json.loads(doc["config_json"])["qaoa"]["seed"] == cfg["qaoa"]["seed"]
    sched = env.db.get("control/scheduler")
    assert [e["run_id"] for e in sched["queue"]] == [rid] and sched["dispatch"]["last_execution"] == "fake-execution-1"
    # a second submission inside the launch window does not start a second worker
    r2 = env.submit("u2", env.config())
    assert r2.status_code == 202 and r2.json()["queue_position"] == 2
    assert env.launcher.calls == 1
    status = env.client.get("/api/cloud/status", headers=env.auth("u1")).json()
    assert status["queue"]["depth"] == 2 and status["quota"]["global_used"] == 2 and status["quota"]["user_used"] == 1
    assert status["quota"]["user_active"] == 1


def test_lost_launch_is_retried_by_later_requests(env):
    env.launcher.fail = True
    r = env.submit("u1", env.config())
    assert r.status_code == 202  # the run is durable even though the launch failed
    rid = r.json()["run_id"]
    assert env.launcher.calls == 0 and env.db.get("control/scheduler")["dispatch"]["last_kick_error"]
    env.launcher.fail = False
    env.status("u1", rid)
    assert env.launcher.calls == 0  # not hammered: the retry waits a few seconds
    env.clock.advance(20)
    env.status("u1", rid)
    assert env.launcher.calls == 1
    env.status("u1", rid)
    assert env.launcher.calls == 1


def test_submission_runs_end_to_end(env):
    cfg = env.config()
    rid = env.submit("u1", cfg).json()["run_id"]
    assert env.drain() == 1
    st = env.status("u1", rid)
    assert st["status"] == "succeeded" and st["progress"]["stage"] == "done" and st["error"] is None
    h = env.auth("u1")
    result = env.client.get(f"/api/runs/{rid}/result", headers=h)
    assert result.status_code == 200 and result.json()["run_id"] == rid
    assert env.client.get(f"/api/runs/{rid}/config", headers=h).json()["qaoa"]["seed"] == cfg["qaoa"]["seed"]
    for name, ctype in (("report.html", "text/html"), ("report.json", "application/json"), ("report.csv", "text/csv"),
                        ("counts.csv", "text/csv")):
        rep = env.client.get(f"/api/runs/{rid}/{name}", headers=h)
        assert rep.status_code == 200 and rep.headers["content-type"].startswith(ctype), (name, rep.text[:200])
    events = env.client.get(f"/api/runs/{rid}/events/snapshot", headers=h).json()
    types = [e["type"] for e in events]
    assert types[0] == "stage" and "iteration" in types and types[-1] == "result"
    assert [e["id"] for e in events] == sorted({e["id"] for e in events})
    listed = env.client.get("/api/runs", headers=h)
    assert [r["run_id"] for r in listed.json()] == [rid] and listed.headers["x-total-count"] == "1"
    # results live under a fenced prefix in the private bucket
    assert any(n.startswith(f"runs/{rid}/f1/") and n.endswith("result.json") for n in env.blobs.names())
    sched = env.db.get("control/scheduler")
    assert sched["queue"] == [] and sched["running"] is None and sched["lease"] is None


def test_sse_stream_replays_and_closes(env):
    rid = env.submit("u1", env.config()).json()["run_id"]
    env.drain()
    events = read_sse(env.client, rid, headers=env.auth("u1"), max_s=20)
    kinds = [e["event"] for e in events if "event" in e]
    assert kinds[-1] == "result" and "status" in kinds
    resumed = read_sse(env.client, rid, headers={**env.auth("u1"), "Last-Event-ID": events[-1]["id"]}, max_s=20)
    assert [e for e in resumed if "data" in e] == []
    others = read_sse(env.client, rid, headers=env.auth("u2"), max_s=20)  # runs are public
    assert [e["event"] for e in others if "event" in e][-1] == "result"


# --------------------------------------------------------------------------- shared workspace
def test_runs_are_public_and_only_the_submitter_may_cancel(env):
    rid = env.submit("u1", env.config()).json()["run_id"]
    assert env.client.post(f"/api/runs/{rid}/cancel", headers=env.auth("u2")).status_code == 403
    env.drain()
    paths = ["", "/config", "/result", "/events/snapshot", "/report.html", "/report.json", "/report.csv", "/counts.csv"]
    for p in paths:
        for headers in ({}, env.auth("u2"), env.auth("u1")):
            assert env.client.get(f"/api/runs/{rid}{p}", headers=headers).status_code == 200, (p, headers)
    for headers in ({}, env.auth("u2")):
        listed = env.client.get("/api/runs", headers=headers)
        assert [r["run_id"] for r in listed.json()] == [rid] and listed.headers["cache-control"] == "no-store"
    for bad in ("..%2F..%2Fetc", "run-20260101-zzzzzzzz", "x" * 200):
        assert env.client.get(f"/api/runs/{bad}", headers=env.auth("u1")).status_code == 404


def test_cancel_queued_run_refunds_the_quota(env):
    rid = env.submit("u1", env.config()).json()["run_id"]
    other = env.client.post(f"/api/runs/{rid}/cancel", headers=env.auth("u2"))
    assert other.status_code == 403 and _err(other)["code"] == "cancel_not_allowed" and _err(other)["message"]
    assert env.client.post(f"/api/runs/{rid}/cancel").status_code == 403
    assert env.status("u1", rid)["status"] == "queued"
    r = env.client.post(f"/api/runs/{rid}/cancel", headers=env.auth("u1"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["cancel_requested"] is True
    quota = env.client.get("/api/cloud/status", headers=env.auth("u1")).json()
    assert quota["quota"]["user_used"] == 0 and quota["quota"]["global_used"] == 0 and quota["queue"]["depth"] == 0
    assert env.drain() == 0  # nothing left to run
    again = env.client.post(f"/api/runs/{rid}/cancel", headers=env.auth("u1"))
    assert again.status_code == 200 and again.json()["status"] == "cancelled"
    assert env.client.get(f"/api/runs/{rid}/result", headers=env.auth("u1")).status_code == 409


# --------------------------------------------------------------------------- idempotency
def test_idempotency_is_scoped_to_the_client(env):
    cfg = env.config()
    first = env.submit("u1", cfg, key="key-0001")
    assert first.status_code == 202
    again = env.submit("u1", cfg, key="key-0001")
    assert again.status_code == 200 and again.json()["run_id"] == first.json()["run_id"]
    other_body = env.submit("u1", env.config(), key="key-0001")
    assert other_body.status_code == 409 and _err(other_body)["code"] == "idempotency_key_reused"
    # another client may use the same key: it is a different run
    theirs = env.submit("u2", cfg, key="key-0001")
    assert theirs.status_code == 202 and theirs.json()["run_id"] != first.json()["run_id"]
    assert len(env.db.paths("idempotency/")) == 2
    bad = env.submit("u1", cfg, key="bad key!")
    assert bad.status_code == 400 and _err(bad)["code"] == "invalid_idempotency_key"


def test_identical_active_submission_returns_the_same_run(env):
    cfg = env.config()
    first = env.submit("u1", cfg)
    twin = env.submit("u1", cfg)
    assert twin.status_code == 200 and twin.json()["run_id"] == first.json()["run_id"]
    assert env.client.get("/api/cloud/status", headers=env.auth("u1")).json()["quota"]["user_used"] == 1


# --------------------------------------------------------------------------- limits and quotas
def test_one_active_run_per_client(env):
    env.submit("u1", env.config())
    r = env.submit("u1", env.config())
    assert r.status_code == 429 and _err(r)["code"] == "user_active_limit" and _err(r)["details"]["limit"] == 1
    assert "network" in _err(r)["message"]


def test_per_client_daily_quota_defaults_to_five(env):
    for _ in range(5):
        assert env.submit("u1", env.config()).status_code == 202
        env.drain()
    r = env.submit("u1", env.config())
    assert r.status_code == 429 and _err(r)["code"] == "user_quota_exhausted"
    assert _err(r)["details"]["limit"] == 5 and _err(r)["details"]["used"] == 5
    assert "5 live runs" in _err(r)["message"] and "00:00 UTC" in _err(r)["message"]
    assert env.submit("u2", env.config()).status_code == 202


def test_user_daily_quota(cloud_env):
    env = cloud_env(user_daily_run_cap=2)
    for _ in range(2):
        assert env.submit("u1", env.config()).status_code == 202
        env.drain()
    r = env.submit("u1", env.config())
    assert r.status_code == 429 and _err(r)["code"] == "user_quota_exhausted"
    assert _err(r)["details"]["scope"] == "user" and _err(r)["details"]["resets_at"]
    assert env.submit("u2", env.config()).status_code == 202  # other users are unaffected
    env.drain()
    env.clock.advance(86400)  # next UTC day
    assert env.submit("u1", env.config()).status_code == 202


def test_global_daily_ceiling(cloud_env):
    env = cloud_env(daily_run_cap=3)
    for i in range(3):
        assert env.submit(f"u{i}", env.config()).status_code == 202
        env.drain()
    r = env.submit("u9", env.config())
    assert r.status_code == 429 and _err(r)["code"] == "daily_quota_exhausted" and _err(r)["details"]["scope"] == "global"
    assert env.submit("u8", env.config()).status_code == 429
    assert env.launcher.calls == 3 and len(env.db.paths("runs/")) > 0
    assert env.client.get("/api/cloud/status").json()["quota"]["global_remaining"] == 0


def test_queue_is_bounded(cloud_env):
    env = cloud_env(queue_capacity=2)
    assert env.submit("u1", env.config()).status_code == 202
    assert env.submit("u2", env.config()).status_code == 202
    r = env.submit("u3", env.config())
    assert r.status_code == 429 and _err(r)["code"] == "queue_full" and int(r.headers["retry-after"]) >= 5
    assert env.db.get("days/" + env.jobs.day_key())["total"] == 2


@pytest.mark.parametrize(
    ("field", "value", "limit"),
    [("depth", 4, "max_depth"), ("shots", 4096, "max_shots"), ("final_shots", 16384, "max_final_shots"),
     ("max_evaluations", 201, "max_evaluations")],
)
def test_requests_above_a_limit_are_rejected_not_reduced(env, field, value, limit):
    cfg = env.config(**{field: value})
    r = env.submit("u1", cfg)
    if r.status_code == 422 and _err(r)["code"] == "validation_error":
        pytest.skip(f"{field}={value} is outside the contract itself")
    assert r.status_code == 422 and _err(r)["code"] == "limit_exceeded", r.text
    assert _err(r)["details"] == {"limit": limit, "max": getattr(env.cfg.limits, limit), "requested": value}
    assert env.db.paths("runs/") == [] and env.launcher.calls == 0


def test_timeout_limit_applies_to_everyone(env):
    cfg = env.config()
    cfg["timeout_seconds"] = 600
    for uid in ("u1", "u2"):
        r = env.submit(uid, cfg)
        assert r.status_code == 422 and _err(r)["details"]["limit"] == "max_timeout_seconds"


def test_features_not_offered_publicly(env):
    exp = {"base": env.config(), "depths": [1, 2], "optimizers": [], "init_strategies": [], "seeds": [3], "max_cells": 4}
    r = env.client.post("/api/experiments", json=exp, headers=env.auth("u1"))
    assert r.status_code == 403 and _err(r) == {**_err(r), "code": "operator_only", "details": {"feature": "experiments"}}
    assert env.client.post("/api/experiments", json=exp).status_code == 403
    research = env.config()
    research["preset"] = "research"
    r = env.submit("u1", research)
    assert r.status_code == 403 and _err(r)["details"] == {"feature": "research_preset"}
    r = env.client.post("/api/ibm/check", headers=env.auth("u1"))
    assert r.status_code == 403 and _err(r)["details"] == {"feature": "ibm_check"}
    assert env.client.post("/api/ibm/check").status_code == 403
    assert env.db.paths("runs/") == []


# --------------------------------------------------------------------------- kill switch
def test_env_kill_switch_blocks_new_compute_only(cloud_env):
    env = cloud_env(compute_enabled=False)
    r = env.submit("u1", env.config())
    assert r.status_code == 503 and _err(r)["code"] == "compute_paused" and r.headers["retry-after"] == "300"
    assert env.launcher.calls == 0 and env.db.paths("runs/") == []
    status = env.client.get("/api/cloud/status").json()
    assert status["compute_enabled"] is False and status["compute_reason"]
    assert env.client.get("/api/presets").status_code == 200


def test_firestore_kill_switch_is_durable(env):
    """`control/compute` (written by scripts/gcp/pause-compute.ps1 -Firestore or the console) pauses new work."""
    queued = env.submit("u1", env.config()).json()["run_id"]
    env.jobs.set_compute(False, "budget alert", "ops")
    blocked = env.submit("u2", env.config())
    assert blocked.status_code == 503 and _err(blocked)["details"] == {"reason": "budget alert"}
    assert env.client.get("/api/cloud/status").json()["compute_enabled"] is False
    # a worker that starts while paused runs nothing and frees the lease
    assert env.drain() == 0
    assert env.status("u1", queued)["status"] == "queued"
    launches = env.launcher.calls
    env.clock.advance(120)
    env.status("u1", queued)
    assert env.launcher.calls == launches  # no worker is launched while paused
    env.jobs.set_compute(True, None, "ops")
    env.clock.advance(120)
    env.status("u1", queued)  # the next read (or the scheduled sweep) re-launches for the waiting run
    assert env.launcher.calls == launches + 1
    assert env.drain() == 1 and env.status("u1", queued)["status"] == "succeeded"


# --------------------------------------------------------------------------- reconciliation
def test_sweep_endpoint_requires_the_scheduler_identity(cloud_env):
    env = cloud_env(sweep=True)
    c = env.client
    assert c.post("/api/internal/sweep").status_code == 401
    assert c.post("/api/internal/sweep", headers=env.auth("u1")).status_code == 401  # an address is not enough
    user_token = {"Authorization": f"Bearer {env.token('u1')}"}
    assert c.post("/api/internal/sweep", headers=user_token).status_code == 401
    good = make_token(env.signing, "999", aud=SWEEP_AUDIENCE, iss="https://accounts.google.com",
                      extra={"email": SWEEP_SA, "email_verified": True})
    r = c.post("/api/internal/sweep", headers={"Authorization": f"Bearer {good}"})
    assert r.status_code == 200 and r.json() == {"reconciled": [], "kicked": False, "queue_depth": 0, "running": False}


def test_sweep_endpoint_is_absent_when_not_configured(env):
    assert env.client.post("/api/internal/sweep", headers=env.auth("u1")).status_code == 404


def test_sweep_fails_a_run_whose_worker_died_and_relaunches(cloud_env):
    env = cloud_env(sweep=True)
    dead = env.submit("u1", env.config()).json()["run_id"]
    waiting = env.submit("u2", env.config()).json()["run_id"]
    fence = env.jobs.acquire_lease("execution-dead")
    outcome, run = env.jobs.claim_next("execution-dead", fence, remaining_s=6000, fresh=True)
    assert outcome == "claimed" and run["run_id"] == dead
    env.clock.advance(env.cfg.lease_ttl_s + 5)  # the worker never heart-beats again
    good = make_token(env.signing, "999", aud=SWEEP_AUDIENCE, iss="https://accounts.google.com",
                      extra={"email": SWEEP_SA, "email_verified": True})
    launches = env.launcher.calls
    r = env.client.post("/api/internal/sweep", headers={"Authorization": f"Bearer {good}"})
    assert r.json()["reconciled"] == [dead] and r.json()["kicked"] is True and r.json()["queue_depth"] == 1
    assert env.launcher.calls == launches + 1
    st = env.status("u1", dead)
    assert st["status"] == "failed" and st["error"]["code"] == "worker_lost"
    # the dead worker comes back late: it can neither report progress nor publish
    assert env.jobs.record_stage(dead, fence, "optimizing", "late") is False
    assert env.jobs.finish(dead, fence, "succeeded", result_path="runs/x/f1/result.json") is False
    assert env.status("u1", dead)["status"] == "failed"
    assert env.drain("execution-2") == 1 and env.status("u2", waiting)["status"] == "succeeded"


def test_status_read_detects_an_orphaned_run(env):
    rid = env.submit("u1", env.config()).json()["run_id"]
    fence = env.jobs.acquire_lease("execution-dead")
    env.jobs.claim_next("execution-dead", fence, remaining_s=6000, fresh=True)
    assert env.status("u1", rid)["status"] == "running"
    env.clock.advance(env.cfg.lease_ttl_s + 1)
    st = env.status("u1", rid)
    assert st["status"] == "failed" and st["error"]["code"] == "worker_lost"
    assert env.client.get("/api/cloud/status").json()["queue"] == {"depth": 0, "capacity": 6, "running": False}


# --------------------------------------------------------------------------- stored-result replays
def test_repeat_of_a_finished_run_is_replayed_without_compute(env):
    cfg = env.config()
    first = env.submit("u1", cfg).json()["run_id"]
    env.drain()
    launches = env.launcher.calls
    again = env.submit("u1", cfg)
    assert again.status_code == 200 and again.json()["cached_from"] == first and again.json()["status"] == "succeeded"
    replay = again.json()["run_id"]
    h = env.auth("u1")
    assert env.client.get(f"/api/runs/{replay}/result", headers=h).json() == env.client.get(f"/api/runs/{first}/result", headers=h).json()
    assert env.client.get(f"/api/runs/{replay}/counts.csv", headers=h).status_code == 200
    quota = env.client.get("/api/cloud/status", headers=h).json()["quota"]
    assert quota["user_used"] == 1 and env.launcher.calls == launches
    # the workspace is shared: another client's identical request replays the same stored result
    theirs = env.submit("u2", cfg)
    assert theirs.status_code == 200 and theirs.json()["cached_from"] == first and env.launcher.calls == launches
    # force_fresh computes again
    fresh = env.submit("u1", cfg, force_fresh=True)
    assert fresh.status_code == 202


def test_replays_are_rate_limited(cloud_env):
    env = cloud_env(user_daily_replay_cap=1)
    cfg = env.config()
    env.submit("u1", cfg)
    env.drain()
    assert env.submit("u1", cfg).status_code == 200
    r = env.submit("u1", cfg)
    assert r.status_code == 429 and _err(r)["details"]["kind"] == "replay"


@pytest.mark.skipif(not REAL_EXAMPLES, reason="no bundled example in this checkout")
def test_bundled_example_is_public_and_read_only(cloud_env, data_dir):
    src = REAL_EXAMPLES[-1]
    shutil.copytree(src, data_dir / "examples" / src.name)
    env = cloud_env()
    c = env.client
    assert c.get("/api/example").json()["run_id"] == src.name
    for p in ("", "/config", "/result", "/events/snapshot", "/report.html", "/report.json", "/report.csv", "/counts.csv"):
        assert c.get(f"/api/runs/{src.name}{p}").status_code == 200, p
    assert [r["run_id"] for r in c.get("/api/runs").json()] == [src.name]
    before = c.get(f"/api/runs/{src.name}").json()
    assert before["is_example"] is True
    cancelled = c.post(f"/api/runs/{src.name}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "succeeded"
    assert env.db.paths("runs/") == []


# --------------------------------------------------------------------------- uploads
def test_uploads_stay_with_the_uploading_client(env):
    r = _upload(env, "u1")
    assert r.status_code == 200, r.text
    ds = r.json()["dataset_id"]
    assert ds.startswith("upload-")
    k1, k2 = env.key("u1"), env.key("u2")
    assert env.db.get(f"users/{k1}/datasets/{ds}")["uid"] == k1
    assert env.db.get(f"users/{k2}/datasets/{ds}") is None
    mine = env.client.get("/api/data/status", headers=env.auth("u1")).json()
    assert ds in [d["dataset_id"] for d in mine["available"]]
    for headers in ({}, env.auth("u2")):
        listed = env.client.get("/api/data/status", headers=headers).json()
        assert ds not in [d["dataset_id"] for d in listed["available"]]
        assert env.client.get(f"/api/assets?dataset_id={ds}", headers=headers).status_code == 404
    assert env.client.get(f"/api/assets?dataset_id={ds}", headers=env.auth("u1")).status_code == 200
    cfg = env.config()
    cfg["dataset_id"] = ds
    assert env.client.post("/api/preflight", json=cfg).status_code == 404
    assert env.client.post("/api/preflight", json=cfg, headers=env.auth("u2")).status_code == 404
    stolen = env.submit("u2", cfg)
    assert stolen.status_code == 404 and env.db.paths("runs/") == []
    assert env.client.get("/api/assets?dataset_id=..%2F..%2Fsecrets", headers=env.auth("u1")).status_code == 422


def test_upload_validation_and_quota(cloud_env):
    env = cloud_env(user_daily_upload_cap=1)
    assert _upload(env, "u1", name="prices.exe").status_code == 415
    assert _upload(env, "u1", data=b"").status_code == 422
    bad = _upload(env, "u1", data=b"Date,INFY,TCS\r\n2024-01-01,=1+1,200\r\n2024-01-02,101,201\r\n")
    assert bad.status_code == 422 and _err(bad)["code"] == "invalid_price"
    big = GOOD_CSV + b"2025-01-01,1,1\r\n" * (6 * 1024 * 1024 // 16)
    assert _upload(env, "u1", data=big).status_code == 413
    assert env.blobs.names() == []  # nothing rejected reached the bucket
    assert _upload(env, "u1").status_code == 200
    again = _upload(env, "u1", data=GOOD_CSV + b"2024-01-04,103,198\r\n")
    assert again.status_code == 429 and _err(again)["details"]["kind"] == "upload"
    assert _upload(env, "u2").status_code == 200  # same file, another user: allowed and separately owned


def test_uploaded_dataset_files_round_trip_through_the_bucket(env):
    from quantfolio.cloud import datasets
    from quantfolio.cloud.blobs import MemoryBlobStore

    s = env.settings
    d = datasets.local_dir(s)
    d.mkdir(parents=True, exist_ok=True)
    ds = "upload-abcdef012345"
    for suffix in datasets.SUFFIXES:
        (d / f"{ds}{suffix}").write_bytes(f"payload{suffix}".encode())
    blobs = MemoryBlobStore()
    objects = datasets.push(s, blobs, ds)
    assert objects == [f"datasets/{ds}/{ds}{x}" for x in datasets.SUFFIXES] and blobs.names() == sorted(objects)
    for suffix in datasets.SUFFIXES:  # a fresh instance has nothing on disk
        (d / f"{ds}{suffix}").unlink()
    assert datasets.ensure_local(s, blobs, ds, objects) is True
    assert (d / f"{ds}.close.parquet").read_bytes() == b"payload.close.parquet"
    for suffix in datasets.SUFFIXES:
        (d / f"{ds}{suffix}").unlink()
    assert datasets.ensure_local(s, MemoryBlobStore(), ds, objects) is False  # expired from the bucket
    # object names are confined to the dataset's own prefix
    blobs.put("datasets/other/evil.json", b"x")
    assert datasets.ensure_local(s, blobs, ds, ["datasets/other/evil.json"]) is False
    assert not (d / "evil.json").exists()
