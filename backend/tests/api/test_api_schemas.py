"""Response schemas, error envelopes and request validation (no worker)."""

from __future__ import annotations

import json

import pytest
from qf_api_helpers import default_config, submit

from quantfolio.contracts import (
    AssetsResponse,
    Capabilities,
    DataStatusResponse,
    ErrorEnvelope,
    ExampleResponse,
    HealthResponse,
    IbmStatus,
    JobStatus,
    PreflightReport,
    PresetsResponse,
    ReadyResponse,
    RunSummary,
)


@pytest.mark.parametrize(
    "path,model",
    [
        ("/api/health", HealthResponse),
        ("/api/ready", ReadyResponse),
        ("/api/assets", AssetsResponse),
        ("/api/data/status", DataStatusResponse),
        ("/api/presets", PresetsResponse),
        ("/api/capabilities", Capabilities),
        ("/api/example", ExampleResponse),
        ("/api/ibm/status", IbmStatus),
    ],
)
def test_get_endpoints_match_contract(client, path, model):
    r = client.get(path)
    assert r.status_code == 200, r.text
    model.model_validate(r.json())
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert len(r.headers["x-request-id"]) >= 8


def test_capabilities_reports_platform_limits(client):
    cap = client.get("/api/capabilities").json()
    assert cap["queue_capacity"] == 8
    assert cap["ibm_configured"] is False
    assert cap["limits"]["max_upload_bytes"] == 5 * 1024 * 1024


def test_preflight_and_submit_shapes(client):
    cfg = default_config(client)
    pf = client.post("/api/preflight", json=cfg)
    assert pf.status_code == 200
    PreflightReport.model_validate(pf.json())
    r = submit(client, cfg)
    assert r.status_code == 202
    st = JobStatus.model_validate(r.json())
    assert st.status == "queued" and st.queue_position == 1
    assert r.headers["location"] == f"/api/runs/{st.run_id}"
    lst = client.get("/api/runs")
    assert lst.headers["x-total-count"] == "1"
    [RunSummary.model_validate(x) for x in lst.json()]
    assert client.get(f"/api/runs/{st.run_id}").json()["run_id"] == st.run_id


def test_list_filters_and_paging_validation(client):
    assert client.get("/api/runs?status=bogus").status_code == 422
    assert client.get("/api/runs?limit=0").status_code == 422
    assert client.get("/api/runs?limit=201").status_code == 422
    assert client.get("/api/runs?offset=-1").status_code == 422
    r = client.get("/api/runs?status=queued&kind=run&limit=5&offset=0")
    assert r.status_code == 200 and r.headers["x-total-count"] == "0"


def _assert_envelope(r, status, code=None):
    assert r.status_code == status, r.text
    body = r.json()
    ErrorEnvelope.model_validate(body)
    if code:
        assert body["error"]["code"] == code, body
    assert body["error"]["request_id"] == r.headers["x-request-id"]
    text = r.text.lower()
    assert "traceback" not in text and "e:\\" not in text and "site-packages" not in text
    return body["error"]


def test_error_envelopes(client):
    _assert_envelope(client.get("/api/does-not-exist"), 404, "not_found")
    _assert_envelope(client.get("/api/runs/run-20260101-00000000"), 404, "not_found")
    _assert_envelope(client.get("/api/runs/not-a-run-id"), 404, "not_found")
    _assert_envelope(client.post("/api/health"), 405)
    _assert_envelope(client.post("/api/runs", content=b"{", headers={"content-type": "application/json"}), 422, "invalid_json")


def test_validation_details_are_loc_msg_lists(client):
    cfg = default_config(client)
    cfg["risk_aversion"] = -1
    err = _assert_envelope(submit(client, cfg), 422, "validation_error")
    assert isinstance(err["details"], list) and err["details"]
    assert {"loc", "msg"} <= set(err["details"][0])
    assert "risk_aversion" in err["message"]


def test_unknown_fields_rejected(client):
    cfg = default_config(client)
    cfg["shell"] = "rm -rf /"
    err = _assert_envelope(submit(client, cfg), 422, "validation_error")
    assert "rm -rf" not in json.dumps(err)  # inputs are not echoed back


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_rejected(client, token):
    cfg = default_config(client)
    raw = json.dumps(cfg).replace('"risk_aversion": 1.0', f'"risk_aversion": {token}')
    assert token in raw
    _assert_envelope(client.post("/api/runs", content=raw.encode(), headers={"content-type": "application/json"}), 422, "invalid_json")
    _assert_envelope(client.post("/api/preflight", content=raw.encode(), headers={"content-type": "application/json"}), 422, "invalid_json")


def test_deeply_nested_json_rejected(client):
    raw = "[" * 200 + "]" * 200
    _assert_envelope(client.post("/api/runs", content=raw.encode(), headers={"content-type": "application/json"}), 422, "invalid_json")


def test_oversized_body_rejected(client):
    big = b'{"symbols": ["' + b"A" * (300 * 1024) + b'"]}'
    _assert_envelope(client.post("/api/runs", content=big, headers={"content-type": "application/json"}), 413, "payload_too_large")
    # Lying Content-Length is caught while streaming as well.
    def gen():
        for _ in range(40):
            yield b"x" * 10_000

    _assert_envelope(client.post("/api/preflight", content=gen(), headers={"content-type": "application/json"}), 413, "payload_too_large")


@pytest.mark.parametrize(
    "symbols,code",
    [
        (["INFY", "infy"], "validation_error"),  # duplicate after normalisation
        (["INFY", "TCS;DROP"], "validation_error"),
        (["INFY", "../etc/passwd"], "validation_error"),
        (["INFY", "NOTAREALCO"], "unknown_symbols"),
        (["INFY"], "validation_error"),  # min 2
    ],
)
def test_symbol_validation(client, symbols, code):
    cfg = default_config(client)
    cfg["symbols"] = symbols
    _assert_envelope(submit(client, cfg), 422, code)


def test_symbols_are_normalised(client):
    cfg = default_config(client)
    cfg["symbols"] = [" infy ", "tcs", "Reliance"]
    r = submit(client, cfg)
    assert r.status_code == 202, r.text


@pytest.mark.parametrize("dataset_id", ["../../secrets", "C:\\Windows\\system32", "http://evil.example/x.csv", "a b", "x" * 65])
def test_paths_and_urls_never_accepted(client, dataset_id):
    cfg = default_config(client)
    cfg["dataset_id"] = dataset_id
    r = submit(client, cfg)
    assert r.status_code == 422
    assert client.get("/api/assets", params={"dataset_id": dataset_id}).status_code == 422
    assert client.post("/api/preflight", json=cfg).status_code == 422


def test_dates_out_of_range(client):
    cfg = default_config(client)
    cfg["dates"]["test_end"] = "2999-01-01"
    _assert_envelope(submit(client, cfg), 422, "invalid_dates")


def test_idempotency_key_format(client):
    cfg = default_config(client)
    _assert_envelope(submit(client, cfg, key="bad key with spaces"), 400, "invalid_idempotency_key")
    _assert_envelope(submit(client, cfg, key="x" * 129), 400, "invalid_idempotency_key")


def test_preflight_failure_is_422_with_report(client):
    cfg = default_config(client)
    cfg["symbols"] = sorted({a["symbol"] for a in client.get("/api/assets").json()["assets"]})[:30]  # 30 qubits > fake max 20
    err = _assert_envelope(submit(client, cfg), 422, "preflight_failed")
    PreflightReport.model_validate(err["details"])


def test_trusted_hosts_only(client):
    r = client.get("/api/health", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_cors_is_narrow(client):
    ok = client.options("/api/runs", headers={"origin": "http://127.0.0.1:5173", "access-control-request-method": "POST",
                                              "access-control-request-headers": "content-type,idempotency-key"})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    bad = client.get("/api/health", headers={"origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    nope = client.options("/api/runs", headers={"origin": "http://127.0.0.1:5173", "access-control-request-method": "DELETE"})
    assert nope.status_code == 400


def test_request_id_propagation(client):
    r = client.get("/api/health", headers={"x-request-id": "abcdef12-3456"})
    assert r.headers["x-request-id"] == "abcdef12-3456"
    r = client.get("/api/health", headers={"x-request-id": "<script>"})
    assert r.headers["x-request-id"] != "<script>"


def test_openapi_lists_contract_endpoints(client):
    paths = set(client.get("/api/openapi.json").json()["paths"])
    for p in ["/api/health", "/api/ready", "/api/assets", "/api/data/status", "/api/data/upload", "/api/presets", "/api/capabilities",
              "/api/preflight", "/api/runs", "/api/runs/{run_id}", "/api/runs/{run_id}/result", "/api/runs/{run_id}/cancel",
              "/api/runs/{run_id}/events", "/api/runs/{run_id}/events/snapshot", "/api/runs/{run_id}/report.html",
              "/api/runs/{run_id}/report.json", "/api/runs/{run_id}/report.csv", "/api/runs/{run_id}/counts.csv", "/api/experiments",
              "/api/example", "/api/ibm/status"]:
        assert p in paths, p
