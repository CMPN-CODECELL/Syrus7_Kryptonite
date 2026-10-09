"""Uniform error envelope: every non-2xx /api JSON body is
`{"error": {"code", "message", "details"?, "request_id"?}}`.

Messages are safe to show: no stack traces, filesystem paths, secrets or
echoed request bodies.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..logging_utils import request_id_var, safe_message

log = logging.getLogger("quantfolio.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def envelope(status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    rid = request_id_var.get()
    if rid:
        body["request_id"] = rid
    return JSONResponse({"error": body}, status_code=status, headers=headers)


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "too_many_requests",
    503: "unavailable",
}


def _clean_validation(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in errors[:50]:
        loc = [p if isinstance(p, int) else str(p)[:64] for p in e.get("loc", ())]
        msg = str(e.get("msg", "invalid value"))
        # Pydantic prefixes custom validator messages with "Value error, ".
        msg = msg.replace("Value error, ", "")
        out.append({"loc": loc, "msg": safe_message(msg, 200), "type": str(e.get("type", "value_error"))[:64]})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _clean_validation(list(exc.errors()))
        first = details[0] if details else None
        msg = "Request validation failed"
        if first:
            where = ".".join(str(p) for p in first["loc"] if p != "body")
            msg = f"{where}: {first['msg']}" if where else first["msg"]
        return envelope(422, "validation_error", msg, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        detail = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return envelope(exc.status_code, code, safe_message(detail, 200), headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", exc_info=exc)
        return envelope(500, "internal_error", "Internal server error. The request id identifies the log entry.")
