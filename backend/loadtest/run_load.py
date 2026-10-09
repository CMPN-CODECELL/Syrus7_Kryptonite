"""Bounded load / stress run against an isolated local QuantFolio server.

    python loadtest/run_load.py            # ~10 minutes
    python loadtest/run_load.py --smoke    # ~1 minute

Stages (fake pipeline, own port + data dir, only our own processes are stopped):
  ramp 1 / 10 / 25 / 50 users, a soak, then failure scenarios: queue saturation
  (429), SSE fan-out, cancel storm, dropped SSE connections, missing cached result,
  worker kill + restart. The mocked provider outage runs in-process (outage_mock.py).
Outputs: evidence/load/<stage>_stats.csv (locust), summary.json, summary.md
(a smoke pass writes to evidence/load/smoke/ instead).
Never targets Yahoo, IBM or any third party.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import csv
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from servectl import BACKEND, PROJECT, TMP, Server, prepare_data_dir

EVIDENCE = PROJECT / "evidence" / "load"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
_seed = iter(range(50_000_000, 60_000_000))


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1)


class Monitor(threading.Thread):
    """Samples CPU/RSS of OUR server tree once a second."""

    def __init__(self, srv: Server) -> None:
        super().__init__(daemon=True)
        self.srv, self.stop_flag, self.rows = srv, threading.Event(), []
        self.label = "boot"

    def run(self) -> None:
        self.srv.resources()  # prime cpu counters
        while not self.stop_flag.wait(1.0):
            r = self.srv.resources()
            r.update(t=round(time.time(), 1), stage=self.label, avail_mb=round(psutil.virtual_memory().available / 2**20))
            self.rows.append(r)

    def summary(self, label: str) -> dict:
        rows = [r for r in self.rows if r["stage"] == label]
        if not rows:
            return {}
        return {"cpu_pct_avg": round(statistics.mean(r["cpu_pct"] for r in rows), 1), "cpu_pct_max": max(r["cpu_pct"] for r in rows),
                "rss_mb_start": rows[0]["rss_mb"], "rss_mb_end": rows[-1]["rss_mb"], "rss_mb_max": max(r["rss_mb"] for r in rows),
                "procs_max": max(r["procs"] for r in rows), "system_avail_mb_min": min(r["avail_mb"] for r in rows)}


def total(h: httpx.Client, status: str) -> int:
    return int(h.get(f"/api/runs?status={status}&limit=1").headers["X-Total-Count"])


def wait_idle(h: httpx.Client, timeout: float = 120) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if total(h, "queued") + total(h, "running") == 0:
            break
        time.sleep(0.3)
    return round(time.monotonic() - t0, 1)


def wait_terminal(h: httpx.Client, rid: str, timeout: float = 120) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        st = h.get(f"/api/runs/{rid}").json()
        if st["status"] in TERMINAL:
            return st
        time.sleep(0.15)
    return st


def cfg_with(default: dict, **qaoa) -> dict:
    c = copy.deepcopy(default)
    c["qaoa"]["seed"] = next(_seed)
    c["qaoa"].update(qaoa)
    return c


# ------------------------------------------------------------------ locust
def locust_stage(srv: Server, mon: Monitor, h: httpx.Client, name: str, users: int, rate: int, seconds: int, seed_base: int) -> dict:
    mon.label = name
    before = {s: total(h, s) for s in ("succeeded", "failed", "cancelled", "timed_out")}
    import os

    flow_file = EVIDENCE / f"{name}_jobflows.jsonl"
    flow_file.unlink(missing_ok=True)
    env = dict(os.environ, QF_LOAD_SEED_BASE=str(seed_base), QF_LOAD_FLOW_FILE=str(flow_file), TEMP=str(TMP), TMP=str(TMP))
    cmd = [sys.executable, "-m", "locust", "-f", str(Path(__file__).with_name("locustfile.py")), "--headless", "-u", str(users),
           "-r", str(rate), "-t", f"{seconds}s", "--host", srv.base, "--csv", str(EVIDENCE / name), "--only-summary",
           "--stop-timeout", "15", "--exit-code-on-error", "0"]
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=str(BACKEND), env=env, capture_output=True, timeout=seconds + 180)
    wall = time.monotonic() - t0
    (EVIDENCE / f"{name}_locust.txt").write_bytes(p.stdout + p.stderr)
    drain = wait_idle(h)
    after = {s: total(h, s) for s in before}
    rows = {}
    with open(EVIDENCE / f"{name}_stats.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[r["Name"]] = {"n": int(r["Request Count"]), "fail": int(r["Failure Count"]), "p50": float(r["50%"] or 0),
                               "p95": float(r["95%"] or 0), "p99": float(r["99%"] or 0), "max": float(r["Max Response Time"] or 0),
                               "rps": round(float(r["Requests/s"] or 0), 2)}
    done = {s: after[s] - before[s] for s in before}
    flows = [json.loads(ln) for ln in flow_file.read_text(encoding="utf-8").splitlines()] if flow_file.exists() else []
    fms = [f["ms"] for f in flows if f["ok"]]
    job_flow = {"n": len(flows), "failed": sum(not f["ok"] for f in flows), "p50_ms": pct(fms, 0.5), "p95_ms": pct(fms, 0.95),
                "max_ms": pct(fms, 1.0)}
    out = {"users": users, "job_flow": job_flow, "seconds": seconds, "locust_exit": p.returncode, "aggregate": rows.get("Aggregated"), "by_name": rows,
           "jobs": done, "jobs_per_min": round(done["succeeded"] / (wall / 60), 1), "drain_s": drain, "resources": mon.summary(name)}
    agg = out["aggregate"] or {}
    print(f"[{name}] users={users} req={agg.get('n')} fail={agg.get('fail')} p50={agg.get('p50')} p95={agg.get('p95')} "
          f"p99={agg.get('p99')} rps={agg.get('rps')} jobs={done} flow={job_flow} res={out['resources']}", flush=True)
    return out


# --------------------------------------------------------------- scenarios
def sc_saturation(h: httpx.Client, default: dict, n: int) -> dict:
    """Burst of submissions: the queue is bounded, the rest get 429 + Retry-After."""
    wait_idle(h)
    codes, retry, accepted = [], [], []
    t0 = time.monotonic()
    for _ in range(n):
        r = h.post("/api/runs", json=cfg_with(default, max_evaluations=60))
        codes.append(r.status_code)
        if r.status_code == 429:
            retry.append(r.headers.get("Retry-After"))
            assert r.json()["error"]["code"] == "queue_full"
        else:
            accepted.append(r.json()["run_id"])
    burst_s = time.monotonic() - t0
    finals = [wait_terminal(h, rid)["status"] for rid in accepted]
    return {"submitted": n, "accepted": len(accepted), "rejected_429": codes.count(429), "other_codes": sorted(set(codes) - {202, 429}),
            "retry_after_present": all(retry) and bool(retry), "retry_after_values": sorted(set(retry)), "burst_s": round(burst_s, 2),
            "accepted_all_succeeded": all(s == "succeeded" for s in finals), "drain_s": wait_idle(h),
            "ok": codes.count(429) > 0 and all(retry) and all(s == "succeeded" for s in finals) and not (set(codes) - {202, 429})}


def _sse_ids(base: str, rid: str, drop_after: int | None = None) -> tuple[list[int], float | None]:
    ids, first = [], None
    with httpx.Client(base_url=base, timeout=httpx.Timeout(10, read=60)) as c:
        t0 = time.monotonic()  # after the client is built: client start-up is not server latency
        with c.stream("GET", f"/api/runs/{rid}/events") as resp:
            for line in resp.iter_lines():
                if line.startswith("id:"):
                    ids.append(int(line[3:]))
                    if first is None:
                        first = (time.monotonic() - t0) * 1000
                    if drop_after and len(ids) >= drop_after:
                        break  # leaving the block closes the socket mid-stream
    return ids, first


def sc_sse_fanout(srv: Server, h: httpx.Client, default: dict, clients: int) -> dict:
    """Many SSE clients on one running job: all get the same complete, ordered stream."""
    wait_idle(h)
    rid = h.post("/api/runs", json=cfg_with(default, max_evaluations=250)).json()["run_id"]
    lat: list[float] = []
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(clients) as ex:
        res = list(ex.map(lambda _: _sse_ids(srv.base, rid), range(clients)))
        health = []
        for _ in range(20):
            t = time.perf_counter()
            h.get("/api/health")
            health.append((time.perf_counter() - t) * 1000)
    st = wait_terminal(h, rid)
    seqs = [r[0] for r in res]
    lat = [r[1] for r in res if r[1] is not None]
    ref = [e["id"] for e in h.get(f"/api/runs/{rid}/events/snapshot?limit=2000").json()]
    same = all(s == seqs[0] for s in seqs)
    return {"clients": clients, "job_status": st["status"], "events_per_client": len(seqs[0]), "all_identical": same,
            "matches_persisted_snapshot": seqs[0] == ref, "strictly_increasing": all(a < b for a, b in zip(seqs[0], seqs[0][1:])),
            "first_event_ms_p50": pct(lat, 0.5), "first_event_ms_p95": pct(lat, 0.95), "wall_s": round(time.monotonic() - t0, 1),
            "ok": same and seqs[0] == ref and st["status"] == "succeeded"}


def sc_cancel_storm(h: httpx.Client, default: dict, jobs: int, dup: int) -> dict:
    """Fill the queue with long jobs, then cancel everything concurrently (with duplicates)."""
    wait_idle(h)
    rids = [h.post("/api/runs", json=cfg_with(default, max_evaluations=1500)).json()["run_id"] for _ in range(jobs)]
    time.sleep(1.0)  # let the first one start iterating
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(16) as ex:
        codes = list(ex.map(lambda rid: h.post(f"/api/runs/{rid}/cancel").status_code, rids * dup))
    finals = [wait_terminal(h, rid, 60)["status"] for rid in rids]
    took = time.monotonic() - t0
    return {"jobs": jobs, "cancel_calls": len(codes), "codes": {str(c): codes.count(c) for c in sorted(set(codes))},
            "final_statuses": {s: finals.count(s) for s in sorted(set(finals))}, "all_terminal_s": round(took, 2),
            "ok": all(s == "cancelled" for s in finals) and all(c < 500 for c in codes)}


def sc_dropped(srv: Server, h: httpx.Client, default: dict, clients: int) -> dict:
    """SSE clients vanish mid-stream; the job and the API must be unaffected and threads must not leak."""
    wait_idle(h)
    api = psutil.Process(srv.api_pid())
    threads_before = api.num_threads()
    rid = h.post("/api/runs", json=cfg_with(default, max_evaluations=200)).json()["run_id"]
    for _ in range(3):
        with cf.ThreadPoolExecutor(clients) as ex:
            list(ex.map(lambda _: _sse_ids(srv.base, rid, drop_after=2), range(clients)))
    st = wait_terminal(h, rid)
    lat = []
    for _ in range(30):
        t = time.perf_counter()
        h.get("/api/health")
        lat.append((time.perf_counter() - t) * 1000)
    time.sleep(3)
    threads_after = api.num_threads()
    full, _ = _sse_ids(srv.base, rid)  # a late client still gets the whole persisted stream
    return {"dropped_connections": clients * 3, "job_status": st["status"], "health_ms_p95_after": pct(lat, 0.95),
            "api_threads_before": threads_before, "api_threads_after": threads_after, "replay_events_after": len(full),
            "ok": st["status"] == "succeeded" and threads_after <= threads_before + 8 and len(full) > 3}


def sc_missing_cache(srv: Server, h: httpx.Client, default: dict) -> dict:
    """The cached result file disappears: 410 for the old run, and a re-submit computes fresh instead of replaying."""
    wait_idle(h)
    cfg = cfg_with(default)
    rid = h.post("/api/runs", json=cfg).json()["run_id"]
    wait_terminal(h, rid)
    replay = h.post("/api/runs", json=cfg)
    (srv.data_dir / "runs" / rid / "result.json").unlink()  # inside OUR isolated data dir
    gone = h.get(f"/api/runs/{rid}/result")
    rep = h.get(f"/api/runs/{rid}/report.html")
    again = h.post("/api/runs", json=cfg)
    st = wait_terminal(h, again.json()["run_id"])
    return {"replay_before_delete": {"code": replay.status_code, "cached_from": replay.json().get("cached_from")},
            "result_after_delete": gone.status_code, "report_after_delete": rep.status_code,
            "resubmit": {"code": again.status_code, "cached_from": again.json().get("cached_from"), "status": st["status"]},
            "ok": replay.json().get("cached_from") == rid and gone.status_code == 410 and again.status_code == 202
            and again.json().get("cached_from") is None and st["status"] == "succeeded"}


def _supervisors(api: psutil.Process) -> list[psutil.Process]:
    out = []
    for p in api.children(recursive=True):
        try:
            if "quantfolio.jobs.worker" in " ".join(p.cmdline()):
                out.append(p)
        except psutil.Error:  # a job child that exited meanwhile
            pass
    return out


def sc_worker_kill(srv: Server, h: httpx.Client, default: dict) -> dict:
    """Kill OUR supervisor mid-job: the monitor restarts it, the job fails honestly, nothing is orphaned."""
    wait_idle(h)
    api = psutil.Process(srv.api_pid())
    sup = _supervisors(api)
    if not sup:
        return {"ok": False, "detail": "supervisor process not found"}
    sup_p = sup[0]
    rid = h.post("/api/runs", json=cfg_with(default, max_evaluations=1500)).json()["run_id"]
    queued = h.post("/api/runs", json=cfg_with(default)).json()["run_id"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30 and h.get(f"/api/runs/{rid}").json()["status"] != "running":
        time.sleep(0.1)
    time.sleep(0.5)
    old_tree = [sup_p, *sup_p.children(recursive=True)]
    t_kill = time.monotonic()
    sup_p.kill()
    not_ready = None
    back = None
    while time.monotonic() - t_kill < 60:
        try:
            ready = h.get("/api/ready").json()["checks"].get("worker")
        except httpx.HTTPError:
            ready = False
        new = [p for p in _supervisors(api) if p.pid != sup_p.pid]
        if ready and new:
            back = round(time.monotonic() - t_kill, 2)
            break
        not_ready = True
        time.sleep(0.1)
    st = wait_terminal(h, rid, 60)
    st_q = wait_terminal(h, queued, 90)
    _, alive = psutil.wait_procs(old_tree, timeout=15)
    return {"killed_supervisor_pid": sup_p.pid, "worker_ready_again_s": back, "saw_not_ready": bool(not_ready),
            "running_job": {"status": st["status"], "code": (st.get("error") or {}).get("code")},
            "queued_job_after_restart": st_q["status"], "orphans_of_old_tree": [p.pid for p in alive],
            "ok": back is not None and st["status"] == "failed" and (st.get("error") or {}).get("code") == "interrupted_by_restart"
            and st_q["status"] == "succeeded" and not alive}


def sec(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f}"


def write_md(s: dict) -> None:
    lines = ["| Stage | Users | Requests | Failures | p50 ms | p95 ms | p99 ms | req/s | 429s | Jobs done | Jobs/min | Job p50 / p95 s | CPU avg / max % | RSS max MB |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, st in s["stages"].items():
        a, r, jf = st["aggregate"] or {}, st["resources"], st["job_flow"]
        lines.append(f"| {name} | {st['users']} | {a.get('n')} | {a.get('fail')} | {a.get('p50')} | {a.get('p95')} | {a.get('p99')} | "
                     f"{a.get('rps')} | {(st['by_name'].get('POST /api/runs [429 queue full]') or {}).get('n', 0)} | {st['jobs']['succeeded']} | "
                     f"{st['jobs_per_min']} | {sec(jf['p50_ms'])} / {sec(jf['p95_ms'])} | {r.get('cpu_pct_avg')} / {r.get('cpu_pct_max')} | "
                     f"{r.get('rss_mb_max')} |")
    lines += ["", "| Scenario | OK | Key numbers |", "|---|---|---|"]
    for name, sc in s["scenarios"].items():
        key = {k: v for k, v in sc.items() if k != "ok"}
        lines.append(f"| {name} | {'yes' if sc.get('ok') else 'NO'} | `{json.dumps(key)}` |")
    (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--port", type=int, default=8130)
    a = ap.parse_args()
    if a.smoke:  # a smoke pass must never overwrite the stage files of a full run
        global EVIDENCE
        EVIDENCE = EVIDENCE / "smoke"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    ramp_s, soak_s = (8, 15) if a.smoke else (40, 180)
    stages = [("ramp-01", 1, 1), ("ramp-10", 10, 5)] if a.smoke else [("ramp-01", 1, 1), ("ramp-10", 10, 5), ("ramp-25", 25, 10), ("ramp-50", 50, 25)]
    n = (12, 8, 4, 8) if a.smoke else (30, 40, 8, 40)  # burst, sse clients, cancel jobs, dropped clients

    data = prepare_data_dir(TMP / "load-fake", with_datasets=False)
    srv = Server(a.port, data, impl="fake", env={"QF_FAKE_STEP_S": "0.02"}).start()
    mon = Monitor(srv)
    mon.start()
    summary: dict = {"machine": "i5-1135G7 4C/8T, 16 GB RAM, Windows 11", "pipeline": "fake (deterministic, ~0.02 s per evaluation)",
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "smoke": a.smoke, "stages": {}, "scenarios": {}}
    try:
        with httpx.Client(base_url=srv.base, timeout=30) as h:
            default = h.get("/api/presets").json()["default_config"]
            summary["queue_capacity"] = h.get("/api/capabilities").json().get("queue_capacity")
            for i, (name, users, rate) in enumerate(stages):
                summary["stages"][name] = locust_stage(srv, mon, h, name, users, rate, ramp_s, 1_000_000 * (i + 1))
            summary["stages"]["soak-10"] = locust_stage(srv, mon, h, "soak-10", 10, 5, soak_s, 9_000_000)
            for name, fn in [
                ("queue_saturation_429", lambda: sc_saturation(h, default, n[0])),
                ("sse_fanout", lambda: sc_sse_fanout(srv, h, default, n[1])),
                ("cancel_storm", lambda: sc_cancel_storm(h, default, n[2], 3)),
                ("dropped_sse_connections", lambda: sc_dropped(srv, h, default, n[3])),
                ("missing_cached_result", lambda: sc_missing_cache(srv, h, default)),
                ("worker_kill_restart", lambda: sc_worker_kill(srv, h, default)),
            ]:
                mon.label = name
                try:
                    summary["scenarios"][name] = fn()
                except Exception as exc:  # record, keep going
                    summary["scenarios"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                print(f"[{name}] {json.dumps(summary['scenarios'][name])}", flush=True)
            summary["final"] = {s: total(h, s) for s in ("succeeded", "failed", "cancelled", "timed_out", "queued", "running")}
            summary["db_mb"] = round((data / "state" / "quantfolio.sqlite3").stat().st_size / 2**20, 2)
    finally:
        mon.stop_flag.set()
        tree = list(srv.refresh_tree())
        summary["stop"] = srv.stop()
        _, alive = psutil.wait_procs(tree, timeout=5)
        summary["orphans_after_stop"] = [p.pid for p in alive]
    out = subprocess.run([sys.executable, str(Path(__file__).with_name("outage_mock.py"))], cwd=str(BACKEND), capture_output=True, timeout=180)
    try:
        summary["scenarios"]["provider_outage_mocked"] = json.loads(out.stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        summary["scenarios"]["provider_outage_mocked"] = {"ok": False, "error": out.stderr.decode(errors="replace")[-400:]}
    print(f"[provider_outage_mocked] {json.dumps(summary['scenarios']['provider_outage_mocked'])}", flush=True)
    with open(EVIDENCE / "resources.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t", "stage", "cpu_pct", "rss_mb", "procs", "avail_mb"])
        w.writeheader()
        w.writerows(mon.rows)
    suffix = "_smoke" if a.smoke else ""
    (EVIDENCE / f"summary{suffix}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not a.smoke:
        write_md(summary)
    bad = [k for k, v in summary["scenarios"].items() if not v.get("ok")]
    fails = sum((st["aggregate"] or {}).get("fail", 0) + st["job_flow"]["failed"] for st in summary["stages"].values())
    print(json.dumps({"scenario_failures": bad, "locust_failures": fails, "stop": summary["stop"], "orphans": summary["orphans_after_stop"]}))
    return 0 if not bad and not fails and not summary["orphans_after_stop"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
