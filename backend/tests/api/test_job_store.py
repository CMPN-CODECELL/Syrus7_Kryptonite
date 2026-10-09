"""Job store: state machine, events, idempotency, cache, restart reconciliation,
DB contention (threads and processes) and atomic writes."""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
from pathlib import Path

import pytest

from quantfolio.jobs.fsutil import atomic_write_bytes, is_valid_run_id, safe_rmtree
from quantfolio.jobs.store import ALLOWED_TRANSITIONS, TERMINAL_STATES, JobStore, QueueFull, StoreError


def _store(tmp: Path, **kw) -> JobStore:
    return JobStore(tmp / "state" / "q.sqlite3", **kw)


def _job(store: JobStore, **kw):
    args = dict(kind="run", config={"x": kw.pop("x", 1)}, config_hash=kw.pop("config_hash", os.urandom(4).hex()), cache_key=kw.pop("cache_key", "k"),
                timeout_s=30, capacity=kw.pop("capacity", 8), idempotency_key=kw.pop("idempotency_key", None),
                request_hash=kw.pop("request_hash", "h"), evaluation_budget=10, summary={"n_symbols": 2}, dedupe_active=kw.pop("dedupe", True))
    args.update(kw)
    return store.create_job(**args)


def test_sqlite_pragmas(data_dir):
    s = _store(data_dir)
    c = s._conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_run_ids(data_dir):
    s = _store(data_dir)
    row, created = _job(s)
    assert created and is_valid_run_id(row["run_id"]) and row["run_id"].startswith("run-")
    assert not is_valid_run_id("../run-20260101-deadbeef")


def test_state_machine_guards(data_dir):
    s = _store(data_dir)
    row, _ = _job(s)
    rid = row["run_id"]
    assert not s.transition(rid, "succeeded")  # queued -> succeeded is illegal
    assert s.claim_next(worker_pid=1)["run_id"] == rid
    assert s.get(rid)["status"] == "running"
    assert s.transition(rid, "succeeded", stage="done")
    for to in ["running", "failed", "cancelled", "queued", "timed_out"]:
        assert not s.transition(rid, to), to  # terminal states are final
    assert s.get(rid)["status"] == "succeeded"
    for frm, tos in ALLOWED_TRANSITIONS.items():
        if frm in TERMINAL_STATES:
            assert not tos


def test_first_terminal_transition_wins(data_dir):
    s = _store(data_dir)
    row, _ = _job(s)
    rid = row["run_id"]
    s.claim_next(worker_pid=1)
    results = []
    barrier = threading.Barrier(4)

    def go(state):
        st = JobStore(s.db_path)
        barrier.wait()
        results.append((state, st.transition(rid, state, error_code="x" if state != "succeeded" else None)))

    ts = [threading.Thread(target=go, args=(x,)) for x in ("succeeded", "failed", "cancelled", "timed_out")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(ok for _, ok in results) == 1
    winner = [st for st, ok in results if ok][0]
    assert s.get(rid)["status"] == winner
    status_events = [e for e in s.events_after(rid) if e["type"] == "status" and e["data"]["status"] in TERMINAL_STATES]
    assert len(status_events) == 1


def test_event_ids_monotonic_and_retention(data_dir):
    s = _store(data_dir, max_events_per_run=100)
    row, _ = _job(s)
    rid = row["run_id"]
    s.claim_next(worker_pid=1)
    for i in range(400):
        s.record_iterations(rid, "qaoa_optimising", [{"evaluation": i, "energy": float(i)}])
    s.transition(rid, "succeeded", stage="done")
    evs = s.events_after(rid, 0, 10_000)
    ids = [e["id"] for e in evs]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert len(evs) <= 100 + 10
    kinds = {e["type"] for e in evs}
    assert {"status", "stage", "result"} <= kinds  # never pruned
    assert evs[-1]["type"] in ("status", "result")
    after = s.events_after(rid, ids[5])
    assert all(e["id"] > ids[5] for e in after)


def test_queue_capacity(data_dir):
    s = _store(data_dir)
    for i in range(3):
        _job(s, capacity=3, x=i)
    with pytest.raises(QueueFull) as ei:
        _job(s, capacity=3, x=99)
    assert ei.value.depth == 3 and ei.value.capacity == 3
    # terminal jobs free capacity
    rid = s.claim_next(worker_pid=1)["run_id"]
    s.transition(rid, "failed", error_code="x", error_message="x")
    _job(s, capacity=3, x=100)


def test_idempotency_and_dedupe(data_dir):
    s = _store(data_dir)
    a, created = _job(s, idempotency_key="k1", request_hash="h1", config_hash="c1")
    assert created
    b, created = _job(s, idempotency_key="k1", request_hash="h1", config_hash="c1")
    assert not created and b["run_id"] == a["run_id"]
    with pytest.raises(StoreError):
        _job(s, idempotency_key="k1", request_hash="DIFFERENT", config_hash="c1")
    c, created = _job(s, config_hash="c1")  # same config while active -> same job
    assert not created and c["run_id"] == a["run_id"]


def test_cache_replay_is_marked_and_not_a_new_run(data_dir):
    s = _store(data_dir)
    row, _ = _job(s, cache_key="ck", config_hash="c")
    rid = row["run_id"]
    s.claim_next(worker_pid=1)
    s.transition(rid, "succeeded", stage="done", result_path=f"{rid}/result.json")
    src = s.find_cached("run", "ck")
    assert src["run_id"] == rid
    rep = s.create_cached_replay(source=src, config={"x": 1}, config_hash="c", cache_key="ck", idempotency_key=None, request_hash="h")
    assert rep["status"] == "succeeded" and rep["cached_from"] == rid
    assert rep["cached_original_time"] == src["finished_at"]
    assert rep["result_path"] == src["result_path"]
    assert rep["worker_pid"] is None and rep["evaluations_done"] in (0, None) or rep["cached_from"]
    # a replay is never itself a cache source and never claimable
    assert s.find_cached("run", "ck")["run_id"] == rid
    assert s.claim_next(worker_pid=1) is None


def test_cancel_paths(data_dir):
    s = _store(data_dir)
    q, _ = _job(s, x=1)
    assert s.request_cancel(q["run_id"]) == "cancelled"
    assert s.get(q["run_id"])["status"] == "cancelled"
    r, _ = _job(s, x=2)
    s.claim_next(worker_pid=1)
    assert s.request_cancel(r["run_id"]) == "requested"
    assert s.is_cancel_requested(r["run_id"])
    assert s.request_cancel("run-20260101-00000000") == "missing"
    s.transition(r["run_id"], "cancelled")
    assert s.request_cancel(r["run_id"]) == "terminal"


def test_restart_reconciliation_exactly_once(data_dir):
    s = _store(data_dir)
    a, _ = _job(s, x=1)
    b, _ = _job(s, x=2)
    s.claim_next(worker_pid=1)  # a running, b queued
    s2 = _store(data_dir)  # "after restart"
    assert s2.reconcile_interrupted() == [a["run_id"]]
    ra = s2.get(a["run_id"])
    assert ra["status"] == "failed" and ra["error_code"] == "interrupted_by_restart"
    assert s2.get(b["run_id"])["status"] == "queued"
    n_events = len(s2.events_after(a["run_id"]))
    assert s2.reconcile_interrupted() == []
    assert len(s2.events_after(a["run_id"])) == n_events
    errors = [e for e in s2.events_after(a["run_id"]) if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["data"]["code"] == "interrupted_by_restart"


def test_concurrent_threads_contention(data_dir):
    s = _store(data_dir)
    row, _ = _job(s)
    rid = row["run_id"]
    errors: list[BaseException] = []

    def writer(n):
        try:
            st = JobStore(s.db_path)
            for i in range(150):
                st.add_event(rid, "log", None, {"w": n, "i": i})
                if i % 10 == 0:
                    _job(st, x=f"{n}-{i}", capacity=10_000, dedupe=False)
                st.get(rid)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    ts = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    logs = [e for e in s.events_after(rid, 0, 100_000) if e["type"] == "log"]
    assert len(logs) == min(6 * 150, s.max_events_per_run)


def _proc_writer(db: str, rid: str, n: int, q) -> None:  # pragma: no cover - runs in a child process
    try:
        st = JobStore(Path(db))
        for i in range(100):
            st.add_event(rid, "log", None, {"p": n, "i": i})
        q.put("ok")
    except BaseException as exc:
        q.put(repr(exc))


def test_concurrent_processes_contention(data_dir):
    s = _store(data_dir)
    row, _ = _job(s)
    rid = row["run_id"]
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    ps = [ctx.Process(target=_proc_writer, args=(str(s.db_path), rid, n, q)) for n in range(3)]
    [p.start() for p in ps]
    for _ in range(100):
        s.add_event(rid, "log", None, {"p": "main"})
    [p.join(60) for p in ps]
    outs = [q.get(timeout=5) for _ in ps]
    assert outs == ["ok"] * 3, outs
    assert len([e for e in s.events_after(rid, 0, 100_000) if e["type"] == "log"]) == 400
    ids = [e["id"] for e in s.events_after(rid, 0, 100_000)]
    assert ids == sorted(set(ids))


def test_atomic_write_never_partial(data_dir):
    target = data_dir / "runs" / "x" / "result.json"
    a, b = b"A" * 400_000, b"B" * 300_000
    atomic_write_bytes(target, a)
    stop = threading.Event()
    bad: list[int] = []

    def reader():
        while not stop.is_set():
            try:
                data = target.read_bytes()
            except PermissionError:  # Windows: replace in progress
                continue
            if data not in (a, b):
                bad.append(len(data))

    t = threading.Thread(target=reader)
    t.start()
    for i in range(60):
        atomic_write_bytes(target, a if i % 2 else b)
    stop.set()
    t.join()
    assert not bad
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]


def test_safe_rmtree_refuses_outside_root(data_dir):
    root = data_dir / "runs"
    (root / "r1" / "tmp").mkdir(parents=True)
    outside = data_dir / "keep"
    outside.mkdir()
    assert not safe_rmtree(outside, root)
    assert not safe_rmtree(root / ".." / "keep", root)
    assert not safe_rmtree(root, root)
    assert outside.exists()
    assert safe_rmtree(root / "r1" / "tmp", root)
