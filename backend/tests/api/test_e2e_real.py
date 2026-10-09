"""End-to-end through the API with the real quantum core (local Aer simulator
only; no network, no IBM). Skipped when the core or cached data is missing."""

from __future__ import annotations

import json
import shutil

import pytest
from qf_api_helpers import PROJECT, submit, wait_ready_worker, wait_status

pytestmark = [pytest.mark.real, pytest.mark.worker, pytest.mark.slow]

DATASETS = PROJECT / "data" / "cache" / "datasets"


@pytest.fixture()
def real(app_client, data_dir, monkeypatch):
    try:
        from quantfolio import pipeline, presets

        presets.default_example_config()
        if not hasattr(pipeline, "run_optimisation"):
            pytest.skip("real pipeline not available")
    except (ImportError, NotImplementedError) as exc:
        pytest.skip(f"real pipeline not available: {exc}")
    if not (DATASETS / "default.txt").is_file():
        pytest.skip("no cached dataset")
    shutil.copytree(DATASETS, data_dir / "cache" / "datasets")
    monkeypatch.setenv("QF_PIPELINE_IMPL", "real")
    c = app_client(pipeline_impl="real", start_worker=True)
    wait_ready_worker(c, timeout=180)
    return c


def test_default_example_runs_for_real(real, make_settings):
    c = real
    cfg = c.get("/api/presets").json()["default_config"]
    assert c.post("/api/preflight", json=cfg).json()["ok"] is True
    rid = submit(c, cfg).json()["run_id"]
    with c.stream("GET", f"/api/runs/{rid}/events") as resp:
        types = [ln[7:] for ln in resp.iter_lines() if ln.startswith("event: ")]
    assert "iteration" in types and types[-1] in ("status", "result")
    st = wait_status(c, rid, timeout=240)
    assert st["status"] == "succeeded", st
    res = c.get(f"/api/runs/{rid}/result").json()
    assert res["qaoa"]["primitive"].startswith("qiskit_aer")
    objs = {s["solver"]: s["portfolio"]["objective"] for s in res["solvers"] if s.get("portfolio")}
    assert {"qaoa", "exact"} <= objs.keys() and objs["exact"] <= objs["qaoa"] + 1e-12
    assert c.get(f"/api/runs/{rid}/report.json").json()["result"] == res
    counts = json.loads((make_settings().runs_dir / rid / "final_counts.json").read_text(encoding="utf-8"))
    assert sum(counts.values()) == cfg["qaoa"]["final_shots"]
    snap = c.get(f"/api/runs/{rid}/events/snapshot").json()
    pts = [p for e in snap if e["type"] == "iteration" for p in e["data"]["points"]]
    assert len(pts) == res["qaoa"]["evaluations_used"]  # every optimiser evaluation persisted


def test_real_run_can_be_cancelled(real):
    c = real
    cfg = c.get("/api/presets").json()["default_config"]
    cfg["qaoa"]["max_evaluations"] = 400
    cfg["qaoa"]["seed"] = 4242
    rid = submit(c, cfg).json()["run_id"]
    wait_status(c, rid, {"running"}, timeout=120)
    for _ in range(600):
        if c.get(f"/api/runs/{rid}").json()["progress"].get("evaluations_done", 0) > 0:
            break
        import time

        time.sleep(0.1)
    assert c.post(f"/api/runs/{rid}/cancel").status_code in (200, 202)
    st = wait_status(c, rid, timeout=60)
    assert st["status"] == "cancelled"
