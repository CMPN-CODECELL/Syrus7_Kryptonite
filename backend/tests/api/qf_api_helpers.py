"""Helpers shared by the platform tests (imported by the test modules)."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
PROJECT = BACKEND.parent
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
FAKE_TOKEN = "sk-test-secret-value-0123456789"


def wait_ready_worker(c: Any, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = c.get("/api/ready").json()
        if r["checks"].get("worker"):
            return
        time.sleep(0.2)
    raise AssertionError("worker did not become ready")


def wait_status(c: Any, run_id: str, states: set[str] | None = None, timeout: float = 60) -> dict[str, Any]:
    states = states or TERMINAL
    deadline = time.monotonic() + timeout
    st: dict[str, Any] = {}
    while time.monotonic() < deadline:
        st = c.get(f"/api/runs/{run_id}").json()
        if st["status"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {states}; last: {st}")


def default_config(c: Any, **qaoa: Any) -> dict[str, Any]:
    cfg = c.get("/api/presets").json()["default_config"]
    if qaoa:
        cfg["qaoa"].update(qaoa)
    return cfg


def unique_seed() -> int:
    return 10_000 + uuid.uuid4().int % 1_000_000


def submit(c: Any, cfg: dict[str, Any], key: str | None = None, force_fresh: bool = False, path: str = "/api/runs") -> Any:
    headers = {"Idempotency-Key": key} if key else {}
    return c.post(path + ("?force_fresh=true" if force_fresh else ""), json=cfg, headers=headers)


def read_sse(c: Any, run_id: str, headers: dict[str, str] | None = None, query: str = "", max_s: float = 60) -> list[dict[str, Any]]:
    """Read a whole SSE stream (it closes after the terminal status event)."""
    events: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    t0 = time.monotonic()
    with c.stream("GET", f"/api/runs/{run_id}/events{query}", headers=headers or {}) as resp:
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if time.monotonic() - t0 > max_s:
                break
            if line == "":
                if cur:
                    if "data" in cur:
                        cur["json"] = json.loads(cur["data"])
                    events.append(cur)
                cur = {}
                continue
            if line.startswith(":"):
                continue
            k, _, v = line.partition(":")
            cur[k] = v[1:] if v.startswith(" ") else v
    return events


def venv_python() -> str:
    return sys.executable


def project_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(extra)
    env["PYTHONPATH"] = str(BACKEND)
    return env
