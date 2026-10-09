"""Who is asking: no sign-in for visitors (a salted hash of the client address), OIDC for the internal sweep."""

from __future__ import annotations

import json

import pytest
from conftest import HOP, KID, SALT, SWEEP_AUDIENCE, SWEEP_SA, ip_of, make_token
from starlette.requests import Request

from quantfolio.cloud.auth import AuthError, ServiceAccountOidcVerifier
from quantfolio.cloud.client import client_identity, client_ip, client_key, valid_source


def _req(xff: str | None = None, peer: str | None = "203.0.113.9") -> Request:
    headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b"",
             "client": (peer, 5555) if peer else None}
    return Request(scope)


# --------------------------------------------------------------------------- client address -> key
@pytest.mark.parametrize(
    ("xff", "source", "expected"),
    [
        ("198.51.100.7", "first", "198.51.100.7"),
        ("198.51.100.7, 35.191.0.1", "first", "198.51.100.7"),  # Hosting/GFE append their own hops
        (" 198.51.100.7:4431 ,35.191.0.1", "first", "198.51.100.7"),  # port stripped
        ("1.2.3.4, 198.51.100.7, 35.191.0.1", "right:2", "198.51.100.7"),  # spoofed leading entry ignored
        ("198.51.100.7", "right:2", "198.51.100.7"),  # fewer entries than trusted hops: the first one
        ("198.51.100.7", "peer", "203.0.113.9"),  # header ignored
        ("2001:db8:aa:bb:1:2:3:4", "first", "2001:db8:aa:bb::/64"),  # IPv6 reduced to its /64
        ("[2001:db8:aa:bb::9]:443", "first", "2001:db8:aa:bb::/64"),
        ("::ffff:198.51.100.7", "first", "198.51.100.7"),  # IPv4-mapped
        ("not-an-ip, 35.191.0.1", "first", "203.0.113.9"),  # garbage falls back to the TCP peer
        ("", "first", "203.0.113.9"),
        (None, "first", "203.0.113.9"),
    ],
)
def test_client_ip(xff, source, expected):
    assert client_ip(_req(xff), source) == expected


def test_client_ip_without_any_usable_address_is_unknown():
    assert client_ip(_req(None, peer=None)) == "unknown"
    assert client_ip(_req("garbage", peer="testclient")) == "unknown"


def test_ipv6_privacy_addresses_of_one_network_share_a_key():
    a = client_identity(_req("2001:db8:1:2:aaaa::1"), SALT)
    b = client_identity(_req("2001:db8:1:2:bbbb::2"), SALT)
    c = client_identity(_req("2001:db8:1:3::1"), SALT)
    assert a.uid == b.uid != c.uid


def test_client_key_is_salted_and_never_the_address():
    k1 = client_key("198.51.100.7", SALT)
    assert k1 == client_key("198.51.100.7", SALT)
    assert k1.startswith("c_") and len(k1) == 34 and "." not in k1
    assert client_key("198.51.100.7", "another-salt-0123456789") != k1
    assert client_key("198.51.100.8", SALT) != k1
    ident = client_identity(_req("198.51.100.7"), SALT)
    assert ident.uid == k1 and ident.anonymous is True and ident.operator is False


@pytest.mark.parametrize(("source", "ok"), [("first", True), ("peer", True), ("right:1", True), ("right:9", True),
                                            ("right:0", False), ("right:10", False), ("last", False), ("", False)])
def test_ip_source_values(source, ok):
    assert valid_source(source) is ok


def test_ip_salt_is_required_and_never_printed(data_dir, monkeypatch):
    from quantfolio.cloud.config import load_cloud_config
    from quantfolio.settings import load_settings

    monkeypatch.setenv("QF_GCP_PROJECT", "p")
    monkeypatch.setenv("QF_BUCKET", "b")
    with pytest.raises(RuntimeError, match="QF_IP_SALT"):
        load_cloud_config(load_settings())
    monkeypatch.setenv("QF_IP_SALT", "short")
    with pytest.raises(RuntimeError, match="QF_IP_SALT"):
        load_cloud_config(load_settings())
    monkeypatch.setenv("QF_IP_SALT", "s3cret-salt-value-0123456789")
    monkeypatch.setenv("QF_CLIENT_IP_SOURCE", "bogus")
    with pytest.raises(RuntimeError, match="QF_CLIENT_IP_SOURCE"):
        load_cloud_config(load_settings())
    monkeypatch.setenv("QF_CLIENT_IP_SOURCE", "right:2")
    cfg = load_cloud_config(load_settings())
    assert cfg.client_ip_source == "right:2" and cfg.ip_salt == "s3cret-salt-value-0123456789"
    assert "s3cret" not in repr(cfg)


def test_requests_need_no_token_and_store_no_address(env):
    r = env.client.post("/api/runs", json=env.config(), headers=env.auth("u1"))
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    stored = json.dumps({p: env.db.get(p) for p in env.db.paths("")}, default=str)
    assert ip_of("u1") not in stored and env.key("u1") in stored
    assert "uid" not in r.json() and env.key("u1") not in env.client.get(f"/api/runs/{rid}").text
    assert env.key("u1") not in env.client.get("/api/runs").text
    # an Authorization header or a cookie changes nothing: the caller is still the address
    env.client.cookies.set("__session", "junk")
    again = env.client.get("/api/cloud/status", headers={**env.auth("u1"), "Authorization": "Bearer junk"})
    assert again.status_code == 200 and again.json()["quota"]["user_used"] == 1


def test_spoofed_leading_entries_do_not_change_the_client(env):

    env.submit("u1", env.config())
    spoofed = {"X-Forwarded-For": f"1.2.3.4, 5.6.7.8, {ip_of('u1')}, {HOP}"}
    st = env.client.get("/api/cloud/status", headers=spoofed).json()["quota"]
    assert st["user_used"] == 1 and st["user_active"] == 1
    r = env.client.post("/api/runs", json=env.config(), headers=spoofed)
    assert r.status_code == 429 and r.json()["error"]["code"] == "user_active_limit"


def test_cloud_status_says_there_is_no_auth(env):
    body = env.client.get("/api/cloud/status").json()
    assert body["auth"] == "none"
    for gone in ("signed_in", "anonymous", "operator", "uid"):
        assert gone not in body
    assert env.client.get("/api/auth/me").status_code == 404


# --------------------------------------------------------------------------- internal sweep (Cloud Scheduler OIDC)
def _sweep_verifier(signing, ok=True):
    def fetch(url):
        if not ok:
            raise OSError("offline")
        return {KID: signing.pem}, 3600.0

    return ServiceAccountOidcVerifier(SWEEP_AUDIENCE, SWEEP_SA, fetch=fetch)


def _sweep_token(signing, **kw):
    extra = {"email": SWEEP_SA, "email_verified": True, **kw.pop("extra", {})}
    return make_token(signing, "1234", aud=kw.pop("aud", SWEEP_AUDIENCE), iss=kw.pop("iss", "https://accounts.google.com"),
                      extra=extra, **kw)


def test_sweep_token_must_come_from_the_scheduler_service_account(signing):
    v = _sweep_verifier(signing)
    assert v.verify(_sweep_token(signing)) is True
    for extra, aud in (({"email": "someone@else.iam.gserviceaccount.com"}, SWEEP_AUDIENCE),
                       ({"email_verified": False}, SWEEP_AUDIENCE),
                       ({}, "https://other.example")):
        with pytest.raises(AuthError):
            v.verify(_sweep_token(signing, extra=extra, aud=aud))
    with pytest.raises(AuthError):  # a Firebase-style user token is not a scheduler token
        v.verify(make_token(signing, "user"))


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"exp_in": -3600}, "expired"),
        ({"iss": "https://securetoken.google.com/x"}, "invalid"),
        ({"kid": "unknown-key"}, "invalid"),
        ({"key": "other"}, "invalid"),
    ],
)
def test_rejected_sweep_tokens(signing, kwargs, reason):
    if kwargs.get("key") == "other":
        kwargs = {"key": signing.other}
    with pytest.raises(AuthError) as err:
        _sweep_verifier(signing).verify(_sweep_token(signing, **kwargs))
    assert err.value.reason == reason


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 9000, "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."])
def test_garbage_and_unsigned_tokens_are_invalid(signing, token):
    with pytest.raises(AuthError) as err:
        _sweep_verifier(signing).verify(token)
    assert err.value.reason == "invalid"


def test_hs256_token_signed_with_the_public_certificate_is_rejected(signing):
    import base64
    import hashlib
    import hmac

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": "attacker", "aud": SWEEP_AUDIENCE, "iss": "https://accounts.google.com",
                           "email": SWEEP_SA, "email_verified": True, "iat": 1, "exp": 9999999999}).encode())
    sig = b64(hmac.new(signing.pem.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError) as err:
        _sweep_verifier(signing).verify(f"{header}.{body}.{sig}")
    assert err.value.reason == "invalid"


def test_certificate_outage_is_unavailable_not_invalid(signing):
    with pytest.raises(AuthError) as err:
        _sweep_verifier(signing, ok=False).verify(_sweep_token(signing))
    assert err.value.reason == "unavailable"


def test_sweep_http_error_shapes(cloud_env, signing):
    env = cloud_env(sweep=True)
    c = env.client
    r = c.post("/api/internal/sweep")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json()["error"]["code"] == "unauthenticated" and r.json()["error"]["details"] == {"reason": "missing"}
    r = c.post("/api/internal/sweep", headers={"Authorization": f"Bearer {_sweep_token(signing, exp_in=-3600)}"})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "expired"}
    r = c.post("/api/internal/sweep", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "missing"}
    cold = cloud_env(sweep=True)  # no certificates cached yet, and Google cannot be reached
    cold.certs_ok = False
    r = cold.client.post("/api/internal/sweep", headers={"Authorization": f"Bearer {_sweep_token(signing)}"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "sweep_unavailable" and r.headers["retry-after"] == "15"
