"""Server-sent events: ordering, ids, heartbeat, resume via Last-Event-ID and
?after=, snapshot fallback, termination and iteration coalescing."""

from __future__ import annotations

import threading
import time

import pytest
from qf_api_helpers import TERMINAL, default_config, read_sse, submit, unique_seed, wait_status

pytestmark = pytest.mark.worker


def _done_run(c, **qaoa):
    qaoa.setdefault("seed", unique_seed())
    rid = submit(c, default_config(c, **qaoa)).json()["run_id"]
    assert wait_status(c, rid)["status"] == "succeeded"
    return rid


def test_stream_ids_types_and_termination(wclient):
    rid = submit(wclient, default_config(wclient, seed=unique_seed())).json()["run_id"]
    events = read_sse(wclient, rid)  # live: returns once the terminal status was sent
    ids = [int(e["id"]) for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(e["json"]["id"] == int(e["id"]) and e["json"]["type"] == e["event"] for e in events)
    types = [e["event"] for e in events]
    assert "status" in types[:3]  # creation emits the queued status (and stage) first
    assert {"stage", "iteration", "result"} <= set(types)
    terminal = [e for e in events if e["event"] == "status" and e["json"]["data"]["status"] in TERMINAL]
    assert len(terminal) == 1 and terminal[0]["json"]["data"]["status"] == "succeeded"
    assert types.index("result") > types.index("iteration")


def test_resume_with_last_event_id_and_after(wclient):
    rid = _done_run(wclient)
    full = read_sse(wclient, rid)
    ids = [int(e["id"]) for e in full]
    k = ids[len(ids) // 2]
    by_header = read_sse(wclient, rid, headers={"Last-Event-ID": str(k)})
    by_query = read_sse(wclient, rid, query=f"?after={k}")
    expected = [i for i in ids if i > k]
    assert [int(e["id"]) for e in by_header] == expected
    assert [int(e["id"]) for e in by_query] == expected
    # header and query: the later position wins
    both = read_sse(wclient, rid, headers={"Last-Event-ID": str(ids[1])}, query=f"?after={k}")
    assert [int(e["id"]) for e in both] == expected
    # after the end: closes immediately with nothing
    t0 = time.monotonic()
    assert read_sse(wclient, rid, headers={"Last-Event-ID": str(ids[-1])}) == []
    assert time.monotonic() - t0 < 5
    # malformed Last-Event-ID is ignored (full replay)
    assert [int(e["id"]) for e in read_sse(wclient, rid, headers={"Last-Event-ID": "abc"})] == ids


def test_snapshot_matches_stream(wclient):
    rid = _done_run(wclient)
    stream = read_sse(wclient, rid)
    snap = wclient.get(f"/api/runs/{rid}/events/snapshot").json()
    assert [e["id"] for e in snap] == [int(e["id"]) for e in stream]
    assert [e["data"] for e in snap] == [e["json"]["data"] for e in stream]
    page = wclient.get(f"/api/runs/{rid}/events/snapshot", params={"after": snap[2]["id"], "limit": 3}).json()
    assert [e["id"] for e in page] == [e["id"] for e in snap[3:6]]
    assert wclient.get(f"/api/runs/{rid}/events/snapshot", params={"limit": 0}).status_code == 422
    assert wclient.get(f"/api/runs/{rid}/events/snapshot", params={"after": -1}).status_code == 422


def test_unknown_run_stream_is_404(client):
    r = client.get("/api/runs/run-20260101-00000000/events")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_heartbeat_while_idle(app_client):
    c = app_client(sse_heartbeat_s=0.5)  # no worker: the job stays queued
    rid = submit(c, default_config(c, seed=unique_seed())).json()["run_id"]
    threading.Timer(2.0, lambda: c.post(f"/api/runs/{rid}/cancel")).start()
    events = read_sse(c, rid, max_s=20)
    beats = [e for e in events if e.get("event") == "heartbeat"]
    assert len(beats) >= 2, events
    assert beats[0]["json"] == {"type": "heartbeat", "data": {}}
    assert "id" not in beats[0]  # heartbeats never move the resume position
    assert events[-1]["event"] == "status" and events[-1]["json"]["data"]["status"] == "cancelled"


def test_iteration_events_are_coalesced_but_complete(app_client, monkeypatch):
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.01")
    c = app_client(start_worker=True)
    n = 200
    rid = submit(c, default_config(c, seed=unique_seed(), max_evaluations=n)).json()["run_id"]
    events = read_sse(c, rid)
    its = [e["json"] for e in events if e["event"] == "iteration"]
    points = [p["evaluation"] for e in its for p in e["data"]["points"]]
    assert points == list(range(1, n + 1))  # every point persisted, in order, once
    span = (time.mktime(time.strptime(its[-1]["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
            - time.mktime(time.strptime(its[0]["ts"][:19], "%Y-%m-%dT%H:%M:%S")))
    assert len(its) < n / 4
    assert len(its) <= 4 * (span + 1) + 3, (len(its), span)
    assert all(e["data"]["evaluation"] == e["data"]["points"][-1]["evaluation"] for e in its)


def test_client_disconnect_mid_stream_is_harmless(app_client, monkeypatch):
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.05")
    c = app_client(start_worker=True)
    rid = submit(c, default_config(c, seed=unique_seed(), max_evaluations=60)).json()["run_id"]
    for _ in range(3):
        with c.stream("GET", f"/api/runs/{rid}/events") as resp:
            for i, _line in enumerate(resp.iter_lines()):
                if i > 4:
                    break  # drop the connection early
    assert c.get("/api/health").status_code == 200
    assert wait_status(c, rid)["status"] == "succeeded"
    assert read_sse(c, rid)[-1]["event"] in ("status", "result")
