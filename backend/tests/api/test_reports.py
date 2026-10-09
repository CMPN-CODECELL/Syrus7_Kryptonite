"""Reports: exported numbers equal the stored result, provenance and the
disclaimer are present, HTML is escaped, CSV is formula-safe, nothing leaks
credentials, and the SPA static server cannot be used for traversal."""

from __future__ import annotations

import csv
import io
import json
import math

import pytest
from qf_api_helpers import default_config, submit, unique_seed, wait_status

from quantfolio.contracts import DISCLAIMER
from quantfolio.reports import csv_cell, render_csv, render_html


def _rows(body: bytes) -> list[dict[str, str]]:
    assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    text = body.decode("utf-8-sig")
    assert "\r\n" in text
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture()
def done(wclient):
    rid = submit(wclient, default_config(wclient, seed=unique_seed())).json()["run_id"]
    assert wait_status(wclient, rid)["status"] == "succeeded"
    return wclient, rid, wclient.get(f"/api/runs/{rid}/result").json()


@pytest.mark.worker
def test_json_report_equals_stored_result(done):
    c, rid, result = done
    r = c.get(f"/api/runs/{rid}/report.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="quantfolio-{rid}-report.json"'
    rep = r.json()
    assert rep["result"] == result
    meta = rep["report"]
    assert meta["disclaimer"] == DISCLAIMER
    assert meta["job"]["run_id"] == rid and meta["job"]["status"] == "succeeded"
    assert meta["git_commit"] and meta["report_generated_at"] and meta["report_schema_version"]


@pytest.mark.worker
def test_csv_numbers_equal_stored_result(done):
    c, rid, result = done
    r = c.get(f"/api/runs/{rid}/report.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = _rows(r.content)
    expected_rows = sum(max(1, len((s.get("portfolio") or {}).get("holdings") or [])) for s in result["solvers"])
    assert len(rows) == expected_rows
    for s in result["solvers"]:
        p = s["portfolio"]
        mine = [x for x in rows if x["solver"] == s["solver"]]
        for row, h in zip(mine, p["holdings"]):
            assert float(row["objective"]) == p["objective"]  # exact: repr round-trips
            assert float(row["expected_return"]) == p["expected_return"]
            assert float(row["volatility"]) == p["volatility"]
            assert row["symbol"] == h["symbol"]
            assert float(row["weight"]) == h["weight"]
            assert float(row["amount_inr"]) == h["amount_inr"]
            assert row["bitstring"] == s["bitstring"]
            assert row["run_id"] == rid


@pytest.mark.worker
def test_counts_csv_matches_artifact_and_shots(done, make_settings):
    c, rid, result = done
    r = c.get(f"/api/runs/{rid}/counts.csv")
    assert r.status_code == 200
    rows = _rows(r.content)
    shots = result["qaoa"]["final_batch"]["shots"]
    assert sum(int(x["count"]) for x in rows) == shots
    stored = json.loads((make_settings().runs_dir / rid / "final_counts.json").read_text(encoding="utf-8"))
    assert {x["bitstring_qiskit_order"]: int(x["count"]) for x in rows} == stored
    assert all(math.isclose(float(x["probability"]), int(x["count"]) / shots) for x in rows)
    counts = [int(x["count"]) for x in rows]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.worker
def test_html_report_has_provenance_disclaimer_and_csp(done):
    c, rid, result = done
    r = c.get(f"/api/runs/{rid}/report.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert "attachment" not in r.headers.get("content-disposition", "inline")
    page = r.text
    assert DISCLAIMER in page and rid in page
    assert result["config_hash"] in page and result["implementation_version"] in page
    assert "<script" not in page.lower()
    dl = c.get(f"/api/runs/{rid}/report.html?download=1")
    assert dl.headers["content-disposition"].startswith("attachment")
    # cached replay is labelled as such
    rep = submit(c, result["config"]).json()
    assert rep["cached_from"] == rid
    assert "replay" in c.get(f"/api/runs/{rep['run_id']}/report.html").text.lower()


def test_reports_require_a_succeeded_run(client):
    rid = submit(client, default_config(client, seed=unique_seed())).json()["run_id"]  # stays queued (no worker)
    for f in ("result", "report.html", "report.json", "report.csv", "counts.csv"):
        r = client.get(f"/api/runs/{rid}/{f}")
        assert r.status_code == 409 and r.json()["error"]["code"] == "result_not_available", f
    for bad in ("run-2026", "..%2F..%2Fsecrets", "run-20260101-0000000G"):
        assert client.get(f"/api/runs/{bad}/report.html").status_code == 404


@pytest.mark.parametrize("value,expected", [
    ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
    ("+1+1", "'+1+1"), ("-2+3", "'-2+3"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("|cmd", "'|cmd"),
    ("INFY", "INFY"), (None, ""), (True, "true"), (3, "3"), (-0.25, "-0.25"), (float("nan"), ""), (float("inf"), ""),
])
def test_csv_cell_neutralises_formulas(value, expected):
    assert csv_cell(value) == expected


@pytest.mark.worker
def test_hostile_strings_are_escaped_and_neutralised(done):
    _, rid, result = done
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    result = json.loads(json.dumps(result))
    result["warnings"].append(evil)
    result["sectors"] = {k: "=cmd|' /C calc'!A0" for k in result["sectors"]}
    for s in result["solvers"]:
        for h in s["portfolio"]["holdings"]:
            h["sector"] = "=cmd|' /C calc'!A0"
    page = render_html(result, {"run_id": rid, "status": "succeeded"}, True).decode("utf-8")
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    rows = _rows(render_csv(result))
    assert rows and all(r["sector"].startswith("'=") for r in rows)


@pytest.mark.worker
def test_no_credentials_in_reports_logs_or_db(app_client, fake_token, make_settings):
    c = app_client(start_worker=True)
    from qf_api_helpers import wait_ready_worker

    wait_ready_worker(c)
    rid = submit(c, default_config(c, seed=unique_seed())).json()["run_id"]
    assert wait_status(c, rid)["status"] == "succeeded"
    bad = submit(c, default_config(c, seed=777)).json()["run_id"]
    wait_status(c, bad)
    blobs = [c.get(f"/api/runs/{rid}/{f}").content for f in ("result", "report.html", "report.json", "report.csv", "counts.csv")]
    blobs += [c.get(p).content for p in ("/api/capabilities", "/api/ibm/status", f"/api/runs/{bad}", f"/api/runs/{bad}/events/snapshot")]
    c.close()
    s = make_settings()
    unreadable = []
    for f in list(s.data_dir.rglob("*")):
        if f.is_file():
            try:
                blobs.append(f.read_bytes())
            except PermissionError:  # e.g. the worker lock file while its handle closes
                unreadable.append(f.name)
    assert not [n for n in unreadable if n.endswith((".sqlite3", ".json", ".log", ".csv", ".html"))], unreadable
    assert len(blobs) > 8
    for b in blobs:
        assert fake_token.encode() not in b
    assert c.app.state.qf.settings.ibm_token() == fake_token  # it was configured, just never exported


# ---------------------------------------------------------------- SPA ---- #
@pytest.fixture()
def spa(app_client, data_dir):
    dist = data_dir / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>QuantFolio</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (data_dir / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return app_client(frontend_dist=dist)


def test_spa_serves_index_assets_and_client_routes(spa):
    r = spa.get("/")
    assert r.status_code == 200 and "QuantFolio" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert "default-src" in r.headers["content-security-policy"]
    assert spa.get("/runs/run-20260101-00000000").text == r.text  # client-side route
    a = spa.get("/assets/app-abc123.js")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    assert spa.get("/assets/missing-0000.js").status_code == 404
    assert spa.get("/favicon.svg").status_code == 200
    assert spa.get("/api/nope").json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("path", ["/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt",
                                  "/..\\secret.txt", "/assets/%2e%2e%5c%2e%2e%5csecret.txt", "/C:%5cWindows%5cwin.ini", "/.env"])
def test_spa_traversal_never_escapes_dist(spa, path):
    r = spa.get(path)
    assert "TOP-SECRET" not in r.text
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "QuantFolio" in r.text  # falls back to the app shell


def test_spa_missing_build_is_explained(client):
    r = client.get("/")
    assert r.status_code == 503 and "frontend" in r.text.lower()
