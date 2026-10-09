"""Optional IBM path: status never touches the network or quota; check is
mocked (tests never contact IBM); submissions need explicit confirmation."""

from __future__ import annotations

import time

import pytest

import quantfolio.ibm as ibm


class _Boom:
    def __init__(self, *a, **k):
        raise AssertionError("QiskitRuntimeService must not be constructed")


@pytest.fixture()
def no_service(monkeypatch):
    import qiskit_ibm_runtime

    monkeypatch.setattr(qiskit_ibm_runtime, "QiskitRuntimeService", _Boom)


def test_status_unconfigured_makes_no_call(client, no_service):
    r = client.get("/api/ibm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False and body["checked"] is False and body["backends"] == []


def test_status_configured_never_calls_or_leaks(app_client, fake_token, no_service):
    c = app_client()
    body = c.get("/api/ibm/status").json()
    assert body["configured"] is True and body["checked"] is False
    assert fake_token not in c.get("/api/ibm/status").text
    assert c.get("/api/capabilities").json()["ibm_configured"] is True


def test_check_without_token_is_400(client, no_service):
    r = client.post("/api/ibm/check")
    assert r.status_code == 400 and r.json()["error"]["code"] == "ibm_not_configured"


def test_check_with_mocked_service(app_client, fake_token, monkeypatch):
    seen = {}

    class _B:
        def __init__(self, name):
            self.name = name

    class _Svc:
        def __init__(self, **kw):
            seen.update(kw)

        def backends(self, operational, simulator):
            assert operational is True and simulator is False
            return [_B("ibm_b"), _B("ibm_a")]

        def run(self, *a, **k):  # pragma: no cover
            raise AssertionError("no jobs may be submitted by a check")

    monkeypatch.setattr(ibm, "_service", lambda s: _Svc(channel=ibm.CHANNEL, token=s.ibm_token()))
    c = app_client()
    r = c.post("/api/ibm/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] is True and body["backends"] == ["ibm_a", "ibm_b"]
    assert seen["channel"] == "ibm_quantum_platform" and seen["token"] == fake_token
    assert fake_token not in r.text


def test_check_failure_is_redacted(app_client, fake_token, monkeypatch):
    def bad(_s):
        raise RuntimeError(f"401 Unauthorized for token={fake_token} at C:\\Users\\me\\.qiskit\\qiskit-ibm.json")

    monkeypatch.setattr(ibm, "_service", bad)
    r = app_client().post("/api/ibm/check")
    assert r.status_code == 502 and r.json()["error"]["code"] == "ibm_check_failed"
    assert fake_token not in r.text and "C:\\\\Users" not in r.text


def test_check_timeout(app_client, fake_token, monkeypatch):
    monkeypatch.setattr(ibm, "CHECK_TIMEOUT_S", 0.3)
    monkeypatch.setattr(ibm, "_service", lambda s: time.sleep(2))
    r = app_client().post("/api/ibm/check")
    assert r.status_code == 504 and r.json()["error"]["code"] == "ibm_timeout"


def test_hardware_submission_requires_confirmation(make_settings):
    with pytest.raises(PermissionError):
        ibm.submit_counts_job(make_settings(), object(), backend_name=None, shots=100, confirm=False)
    assert ibm.main(["submit", "--run-id", "run-20260101-00000000"]) == 2


def test_worker_never_receives_the_token(app_client, fake_token):
    import psutil

    c = app_client(start_worker=True)
    from qf_api_helpers import wait_ready_worker

    wait_ready_worker(c)
    w = c.app.state.qf.worker
    env = psutil.Process(w.pid).environ()
    assert "IBM_QUANTUM_TOKEN" not in env
