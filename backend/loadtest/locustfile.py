"""Locust users for the QuantFolio API.

Target: a LOCAL QuantFolio server only (started by run_load.py with the fake
pipeline). Nothing here talks to Yahoo, IBM or any third party.

  Browser   - read-heavy traffic (status, lists, results, reports, preflight)
  Submitter - submits a job, follows its SSE stream to the end, checks the result;
              a 429 from the bounded queue is expected backpressure, not a failure
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import random
import time

from locust import HttpUser, between, task

_seed = itertools.count(int(os.environ.get("QF_LOAD_SEED_BASE", "1000000")))
_FLOW_FILE = os.environ.get("QF_LOAD_FLOW_FILE")


def _record_flow(ms: float, ok: bool, status: str | None, n_events: int) -> None:
    """Whole-job timing (submit -> terminal). Kept out of locust's HTTP statistics."""
    if _FLOW_FILE:
        with open(_FLOW_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ms": round(ms, 1), "ok": ok, "status": status, "events": n_events}) + "\n")


class Browser(HttpUser):
    weight = 4
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.default = self.client.get("/api/presets", name="/api/presets").json()["default_config"]
        self.done: list[str] = []
        self.any: list[str] = []

    @task(3)
    def health(self) -> None:
        self.client.get("/api/health", name="/api/health")

    @task(2)
    def capabilities(self) -> None:
        self.client.get("/api/capabilities", name="/api/capabilities")

    @task(2)
    def assets(self) -> None:
        self.client.get("/api/assets", name="/api/assets")

    @task(3)
    def list_runs(self) -> None:
        r = self.client.get("/api/runs?limit=20", name="/api/runs")
        if r.status_code == 200:
            rows = r.json()
            self.any = [x["run_id"] for x in rows]
            self.done = [x["run_id"] for x in rows if x["status"] == "succeeded"]

    @task(4)
    def status(self) -> None:
        if self.any:
            self.client.get(f"/api/runs/{random.choice(self.any)}", name="/api/runs/{id}")

    @task(2)
    def result(self) -> None:
        if self.done:
            self.client.get(f"/api/runs/{random.choice(self.done)}/result", name="/api/runs/{id}/result")

    @task(1)
    def report(self) -> None:
        if self.done:
            self.client.get(f"/api/runs/{random.choice(self.done)}/report.html", name="/api/runs/{id}/report.html")

    @task(1)
    def snapshot(self) -> None:
        if self.any:
            self.client.get(f"/api/runs/{random.choice(self.any)}/events/snapshot", name="/api/runs/{id}/events/snapshot")

    @task(1)
    def preflight(self) -> None:
        self.client.post("/api/preflight", json=self.default, name="POST /api/preflight")


class Submitter(HttpUser):
    weight = 1
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.default = self.client.get("/api/presets", name="/api/presets").json()["default_config"]

    @task
    def submit_and_follow(self) -> None:
        cfg = copy.deepcopy(self.default)
        cfg["qaoa"]["seed"] = next(_seed)
        t0 = time.perf_counter()
        with self.client.post("/api/runs", json=cfg, name="POST /api/runs", catch_response=True) as r:
            if r.status_code == 429:
                r.request_meta["name"] = "POST /api/runs [429 queue full]"
                if not r.headers.get("Retry-After"):
                    r.failure("429 without Retry-After")
                else:
                    r.success()  # bounded queue doing its job
                time.sleep(min(float(r.headers.get("Retry-After", "1")), 3.0))
                return
            if r.status_code not in (200, 202):
                r.failure(f"unexpected {r.status_code}")
                return
            rid = r.json()["run_id"]
        n = 0
        with self.client.get(f"/api/runs/{rid}/events", stream=True, name="SSE connect /api/runs/{id}/events",
                             catch_response=True, timeout=120) as s:
            if s.status_code != 200:
                s.failure(f"SSE {s.status_code}")
                return
            for line in s.iter_lines():
                if line.startswith(b"event:"):
                    n += 1
            s.success()
        st = self.client.get(f"/api/runs/{rid}", name="/api/runs/{id}").json()
        _record_flow((time.perf_counter() - t0) * 1000, st.get("status") == "succeeded" and n >= 3, st.get("status"), n)
