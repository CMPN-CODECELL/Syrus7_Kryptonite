"""Saved example runs: export of a genuine run, read-only import at startup,
and /api/example serving exactly the recorded result."""

from __future__ import annotations

import json
import shutil

import pytest
from qf_api_helpers import PROJECT, default_config, submit, unique_seed, wait_status

from quantfolio.jobs.examples import export_example

REAL = sorted((PROJECT / "data" / "examples").glob("run-*"))


def test_no_example_is_explained(client):
    body = client.get("/api/example").json()
    assert body["run_id"] is None and "no saved example" in body["detail"].lower()


@pytest.mark.worker
def test_export_then_import_roundtrip(wclient, make_settings, app_client, data_dir):
    rid = submit(wclient, default_config(wclient, seed=unique_seed())).json()["run_id"]
    assert wait_status(wclient, rid)["status"] == "succeeded"
    result = wclient.get(f"/api/runs/{rid}/result").json()
    replay = submit(wclient, result["config"]).json()["run_id"]
    s = make_settings()
    with pytest.raises(ValueError):  # a cache replay is not a genuine computation
        export_example(s.db_path, s.runs_dir, replay, data_dir / "ex")
    with pytest.raises(ValueError):
        export_example(s.db_path, s.runs_dir, "../../etc", data_dir / "ex")
    wclient.close()
    out = export_example(s.db_path, s.runs_dir, rid, data_dir / "examples")
    assert sorted(p.name for p in out.iterdir()) == ["example.json", "final_counts.json", "result.json"]
    fresh = data_dir / "fresh"
    c = app_client(state_dir=fresh / "state", runs_dir=fresh / "runs", db_path=fresh / "state" / "q.sqlite3")  # imported read-only
    ex = c.get("/api/example").json()
    assert ex["run_id"] == rid
    assert c.get(f"/api/runs/{rid}/result").json() == result
    assert c.get(f"/api/runs/{rid}").json()["status"] == "succeeded"
    assert c.get(f"/api/runs/{rid}/counts.csv").status_code == 200


@pytest.mark.skipif(not REAL, reason="no genuine example recorded yet")
def test_committed_example_is_genuine_and_served(app_client, data_dir):
    src = REAL[-1]
    shutil.copytree(src, data_dir / "examples" / src.name)
    c = app_client()
    assert c.get("/api/example").json()["run_id"] == src.name
    res = c.get(f"/api/runs/{src.name}/result").json()
    stored = json.loads((src / "result.json").read_text(encoding="utf-8"))
    assert res == stored and stored["run_id"] == src.name
    assert stored["qaoa"]["primitive"].startswith("qiskit_aer")  # real simulator, not the fake pipeline
    meta = json.loads((src / "example.json").read_text(encoding="utf-8"))
    assert meta["config_hash"] == stored["config_hash"]
    counts = json.loads((src / "final_counts.json").read_text(encoding="utf-8"))
    assert sum(counts.values()) == stored["config"]["qaoa"]["final_shots"]
    assert "token" not in json.dumps(stored).lower() or "IBM_QUANTUM_TOKEN" not in json.dumps(stored)
