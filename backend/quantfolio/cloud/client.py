"""Who is asking, without a login: a salted hash of the visitor's network address.

The public deployment has no sign-in. Per-client limits (runs per day, one active
run, uploads, replays) are keyed on the client IP, taken from `X-Forwarded-For`.
A client can put anything at the left of that header, so only entries appended
by Google's infrastructure are trusted: by default the second entry from the
right (`right:2`; the visitor as seen by Firebase Hosting, followed by the hop
that Google's front end appends; verified on the deployment, see
docs/GCP_DEPLOYMENT.md). The address itself is never
stored or logged: it is reduced to a keyed hash (HMAC-SHA256 with a salt from
Secret Manager), and IPv6 addresses are first truncated to their /64 network so
one household cannot rotate through its own prefix.

This is abuse damping, not identity. A shared network (office, mobile carrier)
shares one allowance, and the hard protections are elsewhere: the global daily
ceiling, the single worker lease, the queue bound, the kill switch and the
workload limits.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re

from fastapi import Request

from .auth import Identity

_SOURCE_RE = re.compile(r"^(first|peer|right:([1-9]))$")
UNKNOWN = "unknown"


def valid_source(source: str) -> bool:
    return bool(_SOURCE_RE.fullmatch(source))


def _normalise(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().strip('"')
    if value.startswith("[") and "]" in value:  # "[v6]:port"
        value = value[1:value.index("]")]
    elif value.count(":") == 1:  # "v4:port"
        value = value.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False).network_address) + "/64"
    return str(ip)


def client_ip(request: Request, source: str = "right:2") -> str:
    """The visitor's address (IPv6 as its /64), or "unknown".

    `right:N` the N-th entry from the right (N trusted proxies append their peer); if the
              header has fewer entries, its first entry;
    `first`   the first entry (spoofable; only for a proxy that overwrites the header);
    `peer`    the TCP peer, ignoring the header.
    """
    peer = request.client.host if request.client else None
    if source != "peer":
        entries = [e for e in (request.headers.get("x-forwarded-for") or "").split(",") if e.strip()]
        pick: str | None = None
        if entries:
            if source.startswith("right:"):
                n = int(source.split(":", 1)[1])
                pick = entries[-n] if len(entries) >= n else entries[0]
            else:
                pick = entries[0]
        found = _normalise(pick)
        if found:
            return found
    return _normalise(peer) or UNKNOWN


def client_key(ip: str, salt: str) -> str:
    """Stable, non-reversible id for one address: `c_` + 32 hex characters of HMAC-SHA256(salt, ip)."""
    return "c_" + hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def client_identity(request: Request, salt: str, source: str = "right:2") -> Identity:
    return Identity(uid=client_key(client_ip(request, source), salt), anonymous=True)


__all__ = ["UNKNOWN", "client_identity", "client_ip", "client_key", "valid_source"]
