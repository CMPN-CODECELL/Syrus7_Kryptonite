"""Event delivery lag and cancellation latency against an isolated local server (fake pipeline).

    python loadtest/sse_latency.py

For 1 / 10 / 40 concurrent SSE clients on one running job it records, per event,
client receive time minus the event's own server timestamp (same machine clock).
Clients are built before timing starts, so client start-up is not counted.
Output: evidence/load/sse_latency.json. Never targets a third party.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_load import EVIDENCE, cfg_with, pct, wait_idle, wait_terminal
from servectl import TMP, Server, prepare_data_dir


def follow(c: httpx.Client, rid: str, go: threading.Event) -> tuple[list[float], float | None]:
    """Returns (lag in ms for every event, ms from request to the first event)."""
    lags: list[float] = []
    first = None
    go.wait()
    t0 = time.monotonic()
    with c.stream("GET", f"/api/runs/{rid}/events") as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            now = time.time()
            if first is None:
                first = (time.monotonic() - t0) * 1000
            try:
                ts = datetime.fromisoformat(json.loads(line[5:])["ts"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError):
                continue
            lags.append((now - ts) * 1000)
    return lags, first


def fanout(srv: Server, h: httpx.Client, default: dict, clients: int) -> dict:
    wait_idle(h)
    pool = [httpx.Client(base_url=srv.base, timeout=httpx.Timeout(10, read=60)) for _ in range(clients)]
    go = threading.Event()
    try:
        with cf.ThreadPoolExecutor(clients) as ex:
            rid = h.post("/api/runs", json=cfg_with(default, max_evaluations=250)).json()["run_id"]
            futs = [ex.submit(follow, c, rid, go) for c in pool]
            go.set()
            res = [f.result() for f in futs]
    finally:
        for c in pool:
            c.close()
    lags = [x for r in res for x in r[0]]
    first = [r[1] for r in res if r[1] is not None]
    return {"clients": clients, "job_status": wait_terminal(h, rid)["status"], "events_received": len(lags),
            "lag_ms_p50": pct(lags, 0.5), "lag_ms_p95": pct(lags, 0.95), "lag_ms_max": round(max(lags), 1),
            "first_event_ms_p50": pct(first, 0.5), "first_event_ms_p95": pct(first, 0.95)}


def cancel_latency(h: httpx.Client, default: dict, n: int) -> dict:
    """Running job: time for the cancel call to return, and until the job is terminal."""
    ack, done = [], []
    for _ in range(n):
        wait_idle(h)
        rid = h.post("/api/runs", json=cfg_with(default, max_evaluations=400)).json()["run_id"]
        while h.get(f"/api/runs/{rid}").json()["status"] == "queued":
            time.sleep(0.05)
        t0 = time.perf_counter()
        code = h.post(f"/api/runs/{rid}/cancel").status_code
        ack.append((time.perf_counter() - t0) * 1000)
        st = wait_terminal(h, rid, timeout=30)
        done.append((time.perf_counter() - t0) * 1000)
        assert code == 200 and st["status"] == "cancelled", (code, st["status"])
    return {"n": n, "ack_ms_p50": pct(ack, 0.5), "ack_ms_max": round(max(ack), 1),
            "cancelled_ms_p50": pct(done, 0.5), "cancelled_ms_max": round(max(done), 1), "poll_interval_ms": 150}


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    srv = Server(8131, prepare_data_dir(TMP / "load-sse", with_datasets=False), impl="fake", env={"QF_FAKE_STEP_S": "0.02"}).start()
    out: dict = {"pipeline": "fake (~0.02 s per evaluation)", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "fanout": []}
    try:
        with httpx.Client(base_url=srv.base, timeout=30) as h:
            default = h.get("/api/presets").json()["default_config"]
            for n in (1, 10, 40):
                out["fanout"].append(fanout(srv, h, default, n))
                print(out["fanout"][-1], flush=True)
            out["cancel_running"] = cancel_latency(h, default, 10)
            print(out["cancel_running"], flush=True)
    finally:
        out["stop"] = srv.stop()
    (EVIDENCE / "sse_latency.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(out["stop"])
    return 0 if all(f["job_status"] == "succeeded" for f in out["fanout"]) and not out["stop"].get("orphans_killed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
