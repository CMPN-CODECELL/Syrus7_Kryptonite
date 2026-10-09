"""Pure-ASGI middleware: request ids + access log + security headers, and a
request-body guard (size limits, strict JSON without NaN/Infinity)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable

from ..logging_utils import request_id_var

log = logging.getLogger("quantfolio.api.access")

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_RID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_QUIET_PATHS = ("/api/health", "/api/ready")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


def _error_bytes(code: str, message: str, rid: str | None) -> bytes:
    body: dict[str, Any] = {"code": code, "message": message}
    if rid:
        body["request_id"] = rid
    return json.dumps({"error": body}).encode()


async def send_error(send: Send, status: int, code: str, message: str, rid: str | None, extra: list[tuple[bytes, bytes]] | None = None) -> None:
    payload = _error_bytes(code, message, rid)
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    if rid:
        headers.append((b"x-request-id", rid.encode()))
    headers += extra or []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, csp_spa: str):
        self.app = app
        self.csp_spa = csp_spa.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                incoming = v.decode("latin-1")
                break
        rid = incoming if incoming and _RID_RE.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        path = scope.get("path", "")
        t0 = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                if b"x-request-id" not in names:
                    headers.append((b"x-request-id", rid.encode()))
                for k, v in SECURITY_HEADERS:
                    if k not in names:
                        headers.append((k, v))
                if not path.startswith("/api") and b"content-security-policy" not in names:
                    headers.append((b"content-security-policy", self.csp_spa))
                if path.startswith("/api") and b"cache-control" not in names:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dur_ms = round((time.perf_counter() - t0) * 1000, 2)
            level = logging.DEBUG if (path in _QUIET_PATHS or path.endswith("/events/snapshot")) else logging.INFO
            if log.isEnabledFor(level):
                log.log(level, "request", extra={"method": scope.get("method"), "path": path[:200], "status": status_holder["status"],
                                                  "duration_ms": dur_ms})
            request_id_var.reset(token)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


class BodyGuardMiddleware:
    """Rejects oversized bodies (413) before the app reads them, and JSON
    bodies that are not strict JSON (NaN/Infinity, too deep) with 422."""

    def __init__(self, app: ASGIApp, max_body: int, max_upload: int, upload_paths: tuple[str, ...]):
        self.app = app
        self.max_body = max_body
        self.max_upload = max_upload
        self.upload_paths = upload_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = self.max_upload + 64 * 1024 if path in self.upload_paths else self.max_body
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        rid = request_id_var.get()
        cl = headers.get(b"content-length")
        if cl is not None:
            try:
                if int(cl) > limit:
                    await send_error(send, 413, "payload_too_large", f"Request body exceeds the limit of {limit} bytes.", rid)
                    return
            except ValueError:
                await send_error(send, 400, "bad_request", "Invalid Content-Length header.", rid)
                return
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > limit:
                await send_error(send, 413, "payload_too_large", f"Request body exceeds the limit of {limit} bytes.", rid)
                return
            chunks.append(body)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        ctype = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
        if ctype == b"application/json" and body:
            try:
                parsed = json.loads(body, parse_constant=_reject_constant)
                _check_depth(parsed, 0)
            except ValueError as exc:
                msg = str(exc) if "non-finite" in str(exc) or "nesting" in str(exc) else "Body is not valid JSON."
                await send_error(send, 422, "invalid_json", msg, rid)
                return
            except RecursionError:
                await send_error(send, 422, "invalid_json", "JSON nesting is too deep.", rid)
                return

        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _check_depth(obj: Any, depth: int) -> None:
    if depth > 32:
        raise ValueError("JSON nesting is too deep")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_depth(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _check_depth(v, depth + 1)
