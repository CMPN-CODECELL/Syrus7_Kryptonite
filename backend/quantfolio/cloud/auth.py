"""Service-to-service authentication for the cloud API. There is no end-user sign-in.

The only authenticated endpoint is the scheduled sweep (`POST /api/internal/sweep`):
Cloud Scheduler calls it with a Google-signed OIDC identity token of one service
account. A token is accepted only if its RS256 signature checks against Google's
published certificates and its audience, issuer and service-account email match.
Visitors are identified only by a salted hash of their network address
(`quantfolio.cloud.client`); no cookies or tokens are read for them.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ..api.errors import ApiError

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
_MAX_TOKEN_CHARS = 8192


@dataclass(frozen=True)
class Identity:
    """The caller of a public route: `uid` is the hashed client key (see `client.py`), never a raw address."""

    uid: str
    anonymous: bool = True
    operator: bool = False  # there are no operators in the public deployment


class AuthError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason  # "missing" | "expired" | "invalid" | "unavailable"


CertFetcher = Callable[[str], tuple[dict[str, str], float]]


def _fetch_certs(url: str) -> tuple[dict[str, str], float]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        cache_control = resp.headers.get("Cache-Control") or ""
    m = re.search(r"max-age=(\d+)", cache_control)
    max_age = float(m.group(1)) if m else 3600.0
    return {str(k): str(v) for k, v in body.items()}, max(300.0, min(max_age, 24 * 3600.0))


class _CertCache:
    def __init__(self, url: str, fetch: CertFetcher | None = None):
        self.url = url
        self._fetch = fetch or _fetch_certs
        self._certs: dict[str, str] = {}
        self._expires = 0.0
        self._next_miss_fetch = 0.0
        self._lock = threading.Lock()

    def get(self, kid: str) -> str | None:
        with self._lock:
            now = time.monotonic()
            stale = now >= self._expires
            unknown = kid not in self._certs and now >= self._next_miss_fetch
            if stale or unknown:
                self._next_miss_fetch = now + 60.0  # an unknown key id may refetch at most once a minute
                try:
                    self._certs, ttl = self._fetch(self.url)
                    self._expires = now + ttl
                except Exception as exc:
                    if kid not in self._certs:  # stale certificates are still fine for a known key id
                        raise AuthError("unavailable", f"certificate fetch failed: {type(exc).__name__}") from exc
            return self._certs.get(kid)


class JwtVerifier:
    """RS256 verification against x509 certificates published at a fixed Google URL."""

    def __init__(self, *, certs_url: str, audience: str, issuers: tuple[str, ...], fetch: CertFetcher | None = None):
        self.audience = audience
        self.issuers = issuers
        self._cache = _CertCache(certs_url, fetch)

    def verify(self, token: str) -> dict[str, Any]:
        import jwt  # PyJWT
        from cryptography import x509

        if not token or len(token) > _MAX_TOKEN_CHARS or token.count(".") != 2:
            raise AuthError("invalid", "malformed token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("invalid", "malformed token header") from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            raise AuthError("invalid", "unexpected token algorithm or key id")
        pem = self._cache.get(kid)
        if pem is None:
            raise AuthError("invalid", "unknown signing key")
        try:
            key = x509.load_pem_x509_certificate(pem.encode("ascii")).public_key()
            claims = jwt.decode(token, key=key, algorithms=["RS256"], audience=self.audience, leeway=10,  # type: ignore[arg-type]
                                options={"require": ["exp", "iat", "aud", "iss", "sub"]})
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("expired", "token expired") from exc
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthError("invalid", type(exc).__name__) from exc
        if claims.get("iss") not in self.issuers:
            raise AuthError("invalid", "wrong issuer")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthError("invalid", "missing subject")
        return claims


class ServiceAccountOidcVerifier:
    """Google-signed OIDC tokens (Cloud Scheduler) for the internal sweep endpoint."""

    def __init__(self, audience: str, email: str, fetch: CertFetcher | None = None):
        self.email = email
        self._jwt = JwtVerifier(certs_url=GOOGLE_CERTS_URL, audience=audience,
                                issuers=("https://accounts.google.com", "accounts.google.com"), fetch=fetch)

    def verify(self, token: str) -> bool:
        claims = self._jwt.verify(token)
        if claims.get("email") != self.email or claims.get("email_verified") is not True:
            raise AuthError("invalid", "unexpected caller")
        return True


def bearer_token(request: Request) -> str | None:
    raw = request.headers.get("authorization") or ""
    scheme, _, value = raw.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def caller_rejected(reason: str) -> ApiError:
    """401 for the internal sweep: the caller is not the scheduler's service account."""
    reason = reason if reason in ("missing", "expired", "invalid") else "invalid"
    return ApiError(401, "unauthenticated", "This internal endpoint accepts only the scheduler's identity token.",
                    {"reason": reason}, headers={"WWW-Authenticate": "Bearer"})


def caller_unverifiable() -> ApiError:
    """503 for the internal sweep when Google's certificates cannot be fetched; the scheduler retries."""
    return ApiError(503, "sweep_unavailable", "The caller could not be verified right now. Try again shortly.",
                    headers={"Retry-After": "15"})
