"""The Cloud Run Job worker drains the queue with the real supervisor (spawned child, fake pipeline)."""

from __future__ import annotations

import threading
import time


def _wait(env, uid, rid, states, timeout=60.0):
    deadline = time.monotonic() + timeout
    st = {}
    while time.monotonic() < deadline:
        st = env.jobs.status_dict(env.jobs.get(rid))
        if st["status"] in states:
            return st
        time.sleep(0.1)
    raise AssertionError(f"{rid} did not reach {states}: {st}")


def test_failures_are_reported_and_the_queue_continues(env):
    rid_err = env.submit("u1", env.config(seed=999)).json()["run_id"]
    rid_exc = env.submit("u2", env.config(seed=777)).json()["run_id"]
    rid_ok = env.submit("u3", env.config()).json()["run_id"]
    assert env.drain() == 3
    err = env.status("u1", rid_err)
    assert err["status"] == "failed" and err["error"]["code"] == "infeasible_config"
    exc = env.status("u2", rid_exc)
    assert exc["status"] == "failed" and "sk-test" not in exc["error"]["message"]
    assert env.status("u3", rid_ok)["status"] == "succeeded"
    assert env.client.get(f"/api/runs/{rid_err}/result", headers=env.auth("u9")).status_code == 409
    sched = env.db.get("control/scheduler")
    assert sched["queue"] == [] and sched["running"] is None and sched["lease"] is None and sched["fence"] == 1
    # no local artifacts are left behind on the worker's disk
    assert not any((env.settings.runs_dir / r).exists() for r in (rid_err, rid_exc, rid_ok))


def test_a_second_worker_exits_while_the_lease_is_held(env):
    env.submit("u1", env.config())
    assert env.jobs.acquire_lease("someone-else") is not None
    assert env.drain("execution-2") == 0
    assert env.db.get("control/scheduler")["lease"]["holder"] == "someone-else"


def test_cancel_of_an_uncooperative_run_is_enforced(env):
    rid = env.submit("u1", env.config(seed=888)).json()["run_id"]
    t = threading.Thread(target=env.drain, kwargs={"cancel_grace_s": 0.5}, daemon=True)
    t.start()
    _wait(env, "u1", rid, {"running"})
    time.sleep(1.0)
    r = env.client.post(f"/api/runs/{rid}/cancel", headers=env.auth("u1"))
    assert r.status_code == 200 and r.json()["cancel_requested"] is True
    st = _wait(env, "u1", rid, {"cancelled", "failed", "timed_out", "succeeded"}, timeout=60)
    t.join(60)
    assert not t.is_alive()
    assert st["status"] == "cancelled"
    assert env.db.get("control/scheduler")["lease"] is None


def test_result_upload_failure_never_reports_success(env):
    rid = env.submit("u1", env.config()).json()["run_id"]
    env.blobs.fail_puts = True
    assert env.drain() == 1
    st = env.status("u1", rid)
    assert st["status"] == "failed" and st["error"]["code"] == "result_upload_failed"
    assert env.blobs.names() == []
    assert env.jobs.find_cached("run", env.jobs.get(rid)["cache_key"]) is None


def test_crash_of_the_child_is_a_failed_run(env):
    rid = env.submit("u1", env.config(seed=666)).json()["run_id"]
    assert env.drain() == 1
    st = env.status("u1", rid)
    assert st["status"] == "failed" and st["error"]["code"]
