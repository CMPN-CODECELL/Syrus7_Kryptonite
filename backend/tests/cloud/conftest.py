"""Fixtures for the cloud adapters.

Nothing here talks to Google: Firestore, Cloud Storage and the Cloud Run Admin
API are replaced by the in-memory twins from `quantfolio.cloud`. There is no
sign-in: a test "user" is a client address sent as `X-Forwarded-For` (as
Firebase Hosting does). The scheduler's OIDC tokens are real RS256 JWTs signed
with a throwaway key whose certificate is served by a fake certificate fetcher.
The quantum core is the deterministic fake pipeline, as in the platform tests.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

BACKEND = Path(__file__).resolve().parents[2]
PROJECT = BACKEND.parent
TMP_ROOT = PROJECT / ".tmp" / "cloud-tests"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests" / "api"))  # qf_api_helpers

PROJECT_ID = "demo-quantfolio"
KID = "test-key-1"
SALT = "test-salt-0123456789"
HOP = "130.211.0.7"  # stands in for the address Google's front end appends
SWEEP_SA = "qf-scheduler@demo-quantfolio.iam.gserviceaccount.com"
SWEEP_AUDIENCE = "https://quantfolio-api.example.run.app"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "worker: starts a job child process")


@pytest.fixture(scope="session")
def signing() -> SimpleNamespace:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test signer")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30)).sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return SimpleNamespace(key=key, other=other, pem=pem)


def make_token(signing: SimpleNamespace, uid: str, *, anonymous: bool = True, aud: str = PROJECT_ID, iss: str | None = None,
               exp_in: int = 3600, key: Any = None, kid: str = KID, extra: dict[str, Any] | None = None) -> str:
    import jwt

    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": iss or f"https://securetoken.google.com/{aud}", "aud": aud, "sub": uid, "user_id": uid,
        "iat": now - 60 if exp_in > 0 else now + exp_in - 3600, "auth_time": now - 60, "exp": now + exp_in,
        "firebase": {"sign_in_provider": "anonymous" if anonymous else "google.com", "identities": {}},
    }
    claims.update(extra or {})
    return jwt.encode(claims, key or signing.key, algorithm="RS256", headers={"kid": kid})


_IPS: dict[str, str] = {}


def ip_of(uid: str) -> str:
    """A distinct TEST-NET-2 address for each test user name."""
    if uid not in _IPS:
        n = len(_IPS) + 1
        _IPS[uid] = f"198.51.{100 + n // 250}.{n % 250 + 1}"
    return _IPS[uid]


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="c-", dir=TMP_ROOT))
    monkeypatch.setenv("QF_DATA_DIR", str(d))
    monkeypatch.setenv("QF_PIPELINE_IMPL", "fake")
    monkeypatch.setenv("QF_FAKE_STEP_S", "0.005")
    monkeypatch.setenv("QF_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("QF_FRONTEND_DIST", str(d / "no-dist"))
    monkeypatch.setenv("QF_WARM_WORKER", "false")
    for name in ("IBM_QUANTUM_TOKEN", "IBM_QUANTUM_INSTANCE", "QF_QUEUE_CAPACITY", "QF_LIVE_QUOTES", "QF_STATE_DIR", "QF_RUNS_DIR",
                 "QF_GCP_PROJECT", "QF_BUCKET", "QF_IP_SALT", "QF_CLIENT_IP_SOURCE", "QF_COMPUTE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from quantfolio.settings import reset_settings_cache

    reset_settings_cache()
    yield d
    reset_settings_cache()
    for _ in range(20):
        try:
            shutil.rmtree(d)
            break
        except OSError:
            time.sleep(0.25)


def _own_descendants() -> set[int]:
    try:
        return {p.pid for p in psutil.Process().children(recursive=True)}
    except psutil.Error:
        return set()


@pytest.fixture()
def no_orphans() -> Iterator[None]:
    before = _own_descendants()
    yield
    deadline = time.monotonic() + 15
    left: set[int] = set()
    while time.monotonic() < deadline:
        left = {pid for pid in _own_descendants() - before if psutil.pid_exists(pid)}
        if not left:
            break
        time.sleep(0.2)
    assert not left, f"processes started by this test are still alive: {sorted(left)}"


class Clock:
    """A clock tests can move forward (lease expiry, kick throttle, day roll-over)."""

    def __init__(self) -> None:
        self.offset = dt.timedelta()

    def __call__(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc) + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += dt.timedelta(seconds=seconds)


class Env:
    def __init__(self, signing: SimpleNamespace, settings: Any, **cfg_overrides: Any) -> None:
        from quantfolio.cloud.auth import ServiceAccountOidcVerifier
        from quantfolio.cloud.blobs import MemoryBlobStore
        from quantfolio.cloud.config import CloudConfig
        from quantfolio.cloud.db import MemoryDocStore
        from quantfolio.cloud.jobstore import CloudJobStore
        from quantfolio.cloud.launcher import FakeLauncher
        from quantfolio.cloud.routes import CloudState

        self.signing = signing
        self.settings = settings
        self.certs_ok = True
        self.fetches = 0
        sweep = cfg_overrides.pop("sweep", False)
        base: dict[str, Any] = {"project": PROJECT_ID, "bucket": "test-bucket", "ip_salt": SALT,
                                "poke_min_interval_s": 0.0}
        if sweep:
            base.update(sweep_sa=SWEEP_SA, sweep_audience=SWEEP_AUDIENCE)
        base.update(cfg_overrides)
        self.cfg = CloudConfig(**base)
        self.clock = Clock()
        self.db = MemoryDocStore()
        self.blobs = MemoryBlobStore()
        self.launcher = FakeLauncher()
        self.jobs = CloudJobStore(self.db, self.cfg, clock=self.clock)
        self.state = CloudState(
            cfg=self.cfg, jobs=self.jobs, blobs=self.blobs, launcher=self.launcher,
            sweep_verifier=ServiceAccountOidcVerifier(SWEEP_AUDIENCE, SWEEP_SA, fetch=self._fetch) if sweep else None,
        )
        self.client: Any = None

    def _fetch(self, url: str) -> tuple[dict[str, str], float]:
        self.fetches += 1
        if not self.certs_ok:
            raise OSError("no network")
        return {KID: self.signing.pem}, 3600.0

    def token(self, uid: str, **kw: Any) -> str:
        return make_token(self.signing, uid, **kw)

    def auth(self, uid: str) -> dict[str, str]:
        """Request headers of the visitor `uid` as they reach Cloud Run behind Firebase Hosting:
        the visitor's address (one test address per name), then the hop Google's front end appends."""
        return {"X-Forwarded-For": f"{ip_of(uid)}, {HOP}"}

    def key(self, uid: str) -> str:
        """The stored client key of the visitor `uid`."""
        from quantfolio.cloud.client import client_key

        return client_key(ip_of(uid), self.cfg.ip_salt)

    def submit(self, uid: str, cfg: dict[str, Any], *, key: str | None = None, force_fresh: bool = False, path: str = "/api/runs") -> Any:
        headers = self.auth(uid)
        if key:
            headers["Idempotency-Key"] = key
        return self.client.post(path + ("?force_fresh=true" if force_fresh else ""), json=cfg, headers=headers)

    def config(self, **qaoa: Any) -> dict[str, Any]:
        from qf_api_helpers import unique_seed

        cfg = self.client.get("/api/presets").json()["default_config"]
        cfg["qaoa"]["seed"] = unique_seed()
        cfg["qaoa"].update(qaoa)
        return cfg

    def drain(self, holder: str = "execution-1", **settings_overrides: Any) -> int:
        from quantfolio.cloud.worker import drain

        settings = dataclasses.replace(self.settings, **settings_overrides) if settings_overrides else self.settings
        return drain(settings, self.cfg, self.jobs, self.blobs, self.launcher, holder)

    def status(self, uid: str, run_id: str) -> dict[str, Any]:
        return self.client.get(f"/api/runs/{run_id}", headers=self.auth(uid)).json()


@pytest.fixture()
def cloud_env(data_dir: Path, signing: SimpleNamespace, no_orphans: None) -> Iterator[Callable[..., Env]]:
    """Factory: cloud_env(**CloudConfig overrides) -> Env with an entered TestClient."""
    from fastapi.testclient import TestClient

    from quantfolio.cloud.app import create_cloud_app
    from quantfolio.settings import load_settings

    opened: list[Any] = []

    def _open(**overrides: Any) -> Env:
        settings = dataclasses.replace(load_settings(), start_worker=False, warm_worker=False)
        env = Env(signing, settings, **overrides)
        env.client = TestClient(create_cloud_app(settings, env.state), raise_server_exceptions=False)
        env.client.__enter__()
        opened.append(env.client)
        return env

    yield _open
    for c in reversed(opened):
        c.__exit__(None, None, None)


@pytest.fixture()
def env(cloud_env: Callable[..., Env]) -> Env:
    return cloud_env()
