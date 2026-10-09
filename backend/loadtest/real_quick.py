"""Genuine end-to-end runs on the real pipeline (local Aer simulator only).

Starts an isolated server (QF_PIPELINE_IMPL=real), runs the default example
plus a few Quick-preset variants sequentially over HTTP + SSE, checks a cache
replay, records timings/resources to evidence/load/real_quick.json and
optionally exports the default run as the saved example.

    python loadtest/real_quick.py [--jobs 3] [--gap 0] [--out real_quick.json] [--export-example]

--gap N waits N seconds before each variant, so the pre-spawned job process has
finished loading its libraries (a user who pauses between runs); 0 = back to back.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from servectl import PROJECT, TMP, Server, prepare_data_dir

EVIDENCE = PROJECT / "evidence" / "load"


def run_job(h: httpx.Client, cfg: dict, label: str, sampler: Sampler) -> dict:
    t0 = time.monotonic()
    r = h.post("/api/runs", json=cfg)
    r.raise_for_status()
    body = r.json()
    rid = body["run_id"]
    first_iter = None
    n_events = 0
    with h.stream("GET", f"/api/runs/{rid}/events", timeout=httpx.Timeout(10, read=60)) as resp:
        for line in resp.iter_lines():
            if line.startswith("event:"):
                n_events += 1
                if first_iter is None and line == "event: iteration":
                    first_iter = time.monotonic() - t0
    st = h.get(f"/api/runs/{rid}").json()
    wall = time.monotonic() - t0
    out = {"label": label, "run_id": rid, "status": st["status"], "wall_s": round(wall, 2),
           "first_iteration_s": round(first_iter, 2) if first_iter else None, "sse_events": n_events,
           "cached_from": body.get("cached_from"), "evaluations": st["progress"].get("evaluations_done")}
    if st["status"] == "succeeded":
        res = h.get(f"/api/runs/{rid}/result").json()
        out["qubits"] = res["qaoa"]["n_qubits"] if "n_qubits" in res.get("qaoa", {}) else None
        out["solvers"] = {s["solver"]: {"status": s.get("status"), "objective": (s.get("portfolio") or {}).get("objective")}
                          for s in res["solvers"]}
        out["reports_ok"] = all(h.get(f"/api/runs/{rid}/{f}").status_code == 200
                                for f in ("report.html", "report.json", "report.csv", "counts.csv"))
    else:
        out["error"] = st.get("error")
    out["peak"] = sampler.take_peak()
    return out


class Sampler(threading.Thread):
    def __init__(self, server: Server) -> None:
        super().__init__(daemon=True)
        self.server, self.stop_flag = server, threading.Event()
        self.samples: list[dict] = []
        self._peak = {"cpu_pct": 0.0, "rss_mb": 0.0}

    def run(self) -> None:
        while not self.stop_flag.wait(0.5):
            s = self.server.resources()
            self.samples.append(s)
            self._peak = {k: max(self._peak[k], s[k]) for k in self._peak}

    def take_peak(self) -> dict:
        p, self._peak = self._peak, {"cpu_pct": 0.0, "rss_mb": 0.0}
        return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=3, help="Quick variants after the default example")
    ap.add_argument("--port", type=int, default=8121)
    ap.add_argument("--export-example", action="store_true")
    ap.add_argument("--gap", type=float, default=0.0, help="idle seconds before each variant")
    ap.add_argument("--out", default="real_quick.json", help="file name inside evidence/load")
    a = ap.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    data = prepare_data_dir(TMP / "load-real")
    t0 = time.monotonic()
    srv = Server(a.port, data, impl="real").start(timeout=240)
    boot_s = round(time.monotonic() - t0, 2)
    samp = Sampler(srv)
    samp.start()
    runs: list[dict] = []
    try:
        with httpx.Client(base_url=srv.base, timeout=60) as h:
            caps = h.get("/api/capabilities").json()
            default = h.get("/api/presets").json()["default_config"]
            runs.append(run_job(h, default, "default example (seed 7)", samp))
            runs.append(run_job(h, default, "cache replay of default", samp))
            for i in range(a.jobs):
                cfg = json.loads(json.dumps(default))
                cfg["qaoa"]["seed"] = 100 + i
                time.sleep(a.gap)
                runs.append(run_job(h, cfg, f"quick variant seed {100 + i}", samp))
    finally:
        samp.stop_flag.set()
        stop = srv.stop()
    fresh = [r["wall_s"] for r in runs if r["status"] == "succeeded" and not r["cached_from"]]
    report = {
        "machine": "i5-1135G7 4C/8T, 16 GB", "pipeline": "real (local Aer simulator; no IBM, no network)",
        "boot_to_ready_s": boot_s, "gap_s": a.gap, "runs": runs, "stop": stop,
        "fresh_wall_s": {"n": len(fresh), "median": statistics.median(fresh) if fresh else None, "max": max(fresh) if fresh else None},
        "max_qubits": caps.get("max_qubits"),
        "peak_tree": {"cpu_pct": max((s["cpu_pct"] for s in samp.samples), default=0),
                      "rss_mb": max((s["rss_mb"] for s in samp.samples), default=0)},
    }
    (EVIDENCE / Path(a.out).name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("boot_to_ready_s", "fresh_wall_s", "peak_tree", "stop")}, indent=2))
    for r in runs:
        print(r["label"], r["status"], r["wall_s"], r.get("cached_from"), r.get("error"))
    ok = all(r["status"] == "succeeded" for r in runs) and stop.get("graceful")
    if a.export_example and runs and runs[0]["status"] == "succeeded":
        sys.path.insert(0, str(PROJECT / "backend"))
        from quantfolio.jobs.examples import export_example

        out = export_example(data / "state" / "quantfolio.sqlite3", data / "runs", runs[0]["run_id"], PROJECT / "data" / "examples")
        print("example exported to", out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
