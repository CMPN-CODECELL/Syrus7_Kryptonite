"""Job-store invariants without HTTP: the single global lease, fencing, reconciliation, dispatch throttle."""

from __future__ import annotations

import pytest
from conftest import PROJECT_ID, Clock

from quantfolio.api.errors import ApiError
from quantfolio.cloud.config import CloudConfig
from quantfolio.cloud.db import MemoryDocStore, TransactionOrderError
from quantfolio.cloud.jobstore import SCHED, CloudJobStore


def _store(**over):
    clock = Clock()
    cfg = CloudConfig(project=PROJECT_ID, bucket="b", **over)
    return CloudJobStore(MemoryDocStore(), cfg, clock=clock), clock


def _submit(jobs, uid="u1", h="h1", **kw):
    args = {"uid": uid, "operator": False, "kind": "run", "config": {"x": h}, "config_hash": h, "cache_key": f"ck-{h}",
            "timeout_s": 120, "idempotency_key": None, "request_hash": f"rq-{h}", "evaluation_budget": 30, "summary": {}}
    args.update(kw)
    return jobs.submit(**args)


def test_reads_after_writes_are_refused_by_the_memory_store():
    db = MemoryDocStore()

    def bad(t):
        t.set("a/1", {"x": 1})
        t.get("a/2")

    with pytest.raises(TransactionOrderError):
        db.transaction(bad)
    assert db.get("a/1") is None  # nothing committed


def test_only_one_lease_holder_and_fences_increase():
    jobs, clock = _store()
    f1 = jobs.acquire_lease("w1")
    assert f1 == 1 and jobs.acquire_lease("w2") is None
    assert jobs.renew_lease("w1", f1) is True and jobs.renew_lease("w2", f1) is False
    assert jobs.release_lease("w1", f1) is True
    f2 = jobs.acquire_lease("w2")
    assert f2 == 2
    assert jobs.renew_lease("w1", f1) is False  # the old holder is fenced out
    clock.advance(jobs.cfg.lease_ttl_s + 1)
    assert jobs.acquire_lease("w3") == 3  # an expired lease can be taken over


def test_claim_is_fifo_and_fenced():
    jobs, _ = _store()
    a, _ = _submit(jobs, "u1", "h1")
    b, _ = _submit(jobs, "u2", "h2")
    fence = jobs.acquire_lease("w1")
    outcome, run = jobs.claim_next("w1", fence, remaining_s=6000, fresh=True)
    assert outcome == "claimed" and run["run_id"] == a["run_id"] and run["fence"] == fence and run["execution"] == "w1"
    assert jobs.status_dict(jobs.get(b["run_id"]))["queue_position"] == 2
    assert jobs.claim_next("w2", fence, remaining_s=6000, fresh=True) == ("lost", None)
    assert jobs.record_stage(a["run_id"], fence + 1, "x", "wrong fence") is False
    assert jobs.is_cancel_requested(a["run_id"], fence + 1) is True  # a fenced-out worker is told to stop
    assert jobs.finish(a["run_id"], fence, "succeeded", result_path=f"runs/{a['run_id']}/f{fence}/result.json") is True
    assert jobs.finish(a["run_id"], fence, "failed") is False  # terminal states are final
    assert jobs.get(a["run_id"])["status"] == "succeeded"
    assert jobs.find_cached("run", "ck-h1")["run_id"] == a["run_id"]  # shared workspace: one stored result per request
    assert jobs.find_cached("run", "ck-h2") is None


def test_takeover_fails_the_dead_workers_run_and_blocks_its_writes():
    jobs, clock = _store()
    a, _ = _submit(jobs)
    f1 = jobs.acquire_lease("w1")
    jobs.claim_next("w1", f1, remaining_s=6000, fresh=True)
    clock.advance(jobs.cfg.lease_ttl_s + 1)
    f2 = jobs.acquire_lease("w2")
    assert f2 == f1 + 1
    run = jobs.get(a["run_id"])
    assert run["status"] == "failed" and run["error_code"] == "worker_lost"
    assert jobs.record_iterations(a["run_id"], f1, "optimizing", [{"evaluation": 3}]) is False
    assert jobs.add_event(a["run_id"], f1, "log", None, {"m": "late"}) is False
    assert jobs.finish(a["run_id"], f1, "succeeded") is False
    assert jobs.get(a["run_id"])["status"] == "failed"
    assert jobs.reconcile() == []  # nothing left to reconcile while w2 is alive
    assert jobs.claim_next("w2", f2, remaining_s=6000, fresh=True) == ("empty", None)
    assert jobs.db.get(SCHED)["lease"] is None


def test_reconcile_frees_the_slot_and_is_idempotent():
    jobs, clock = _store()
    a, _ = _submit(jobs)
    f = jobs.acquire_lease("w1")
    jobs.claim_next("w1", f, remaining_s=6000, fresh=True)
    assert jobs.reconcile() == []
    clock.advance(jobs.cfg.lease_ttl_s + 1)
    assert jobs.reconcile() == [a["run_id"]]
    assert jobs.reconcile() == []
    sched = jobs.db.get(SCHED)
    assert sched["running"] is None and sched["lease"] is None


def test_kick_throttle_retry_and_reset():
    jobs, clock = _store()
    assert jobs.decide_kick() is False  # empty queue
    _submit(jobs)
    assert jobs.decide_kick() is True and jobs.decide_kick() is False
    jobs.record_kick(error="boom")
    clock.advance(10)
    assert jobs.decide_kick() is False
    clock.advance(10)
    assert jobs.decide_kick() is True  # a failed launch is retried after ~15 s
    jobs.record_kick(execution="e1")
    f = jobs.acquire_lease("e1")
    assert jobs.decide_kick(force=True) is False  # a live worker drains the queue itself
    jobs.claim_next("e1", f, remaining_s=6000, fresh=True)
    _submit(jobs, "u2", "h2")
    jobs.finish(jobs.db.get(SCHED)["running"]["run_id"], f, "failed")
    assert jobs.claim_next("e1", f, remaining_s=6000, fresh=False)[0] == "claimed"


def test_handoff_when_the_next_run_does_not_fit():
    jobs, _ = _store()
    _submit(jobs)
    f = jobs.acquire_lease("w1")
    assert jobs.claim_next("w1", f, remaining_s=100, fresh=False) == ("handoff", None)
    assert jobs.db.get(SCHED)["lease"] is None and len(jobs.db.get(SCHED)["queue"]) == 1
    f2 = jobs.acquire_lease("w2")
    assert jobs.claim_next("w2", f2, remaining_s=100, fresh=True)[0] == "claimed"  # a fresh execution always takes one


def test_quota_and_compute_checks_happen_in_the_submit_transaction():
    jobs, _ = _store(daily_run_cap=1)
    jobs.set_compute(False, "maintenance", "op")
    with pytest.raises(ApiError) as err:
        _submit(jobs)
    assert err.value.code == "compute_paused"
    jobs.set_compute(True, None, "op")
    _submit(jobs)
    with pytest.raises(ApiError) as err:
        _submit(jobs, "u2", "h2")
    assert err.value.code == "daily_quota_exhausted"
    assert len([p for p in jobs.db.paths("runs/") if p.count("/") == 1]) == 1


def test_cancel_of_a_running_run_is_durable_and_fenced():
    jobs, _ = _store()
    a, _ = _submit(jobs)
    f = jobs.acquire_lease("w1")
    jobs.claim_next("w1", f, remaining_s=6000, fresh=True)
    assert jobs.is_cancel_requested(a["run_id"], f) is False
    assert jobs.request_cancel(a["run_id"], "u2") == ("missing", None)
    outcome, run = jobs.request_cancel(a["run_id"], "u1")
    assert outcome == "requested" and run["cancel_requested"] is True and run["status"] == "running"
    assert jobs.is_cancel_requested(a["run_id"], f) is True
    assert jobs.finish(a["run_id"], f, "cancelled") is True
    assert jobs.request_cancel(a["run_id"], "u1")[0] == "terminal"
