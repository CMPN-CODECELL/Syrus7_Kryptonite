"""FastAPI application factory.

Middleware (outer -> inner): request context/security headers -> CORS ->
trusted hosts -> gzip -> body guard -> routes. The job supervisor is started as a
child process in the lifespan and stopped (with only its own process tree)
on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..jobs.fsutil import atomic_write_bytes, is_valid_run_id
from ..jobs.store import JobStore
from ..settings import Settings, get_settings
from .errors import install_error_handlers
from .live import LiveQuotes
from .middleware import BodyGuardMiddleware, RequestContextMiddleware
from .routes import AppState, router
from .worker_proc import WorkerProcess

log = logging.getLogger("quantfolio.api")

DEFAULT_SPA_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "connect-src 'self'; worker-src 'self' blob:; font-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; object-src 'none'; form-action 'self'"
)
EXAMPLE_FILES = ("result.json", "final_counts.json")


def import_examples(settings: Settings, store: JobStore) -> str | None:
    """Register saved example runs (data/examples/<run_id>/) as read-only jobs.

    Returns a note when no example is available."""
    root = settings.examples_dir
    if not root.is_dir():
        return "No saved example run is available yet."
    imported = 0
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not is_valid_run_id(d.name):
            continue
        meta_p, result_p = d / "example.json", d / "result.json"
        if not meta_p.is_file() or not result_p.is_file():
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            target = settings.runs_dir / d.name
            target.mkdir(parents=True, exist_ok=True)
            for name in EXAMPLE_FILES:
                src = d / name
                if src.is_file() and not (target / name).is_file():
                    atomic_write_bytes(target / name, src.read_bytes())
            store.import_example(
                run_id=d.name, kind=meta.get("kind", "run"), config=meta["config"], config_hash=meta["config_hash"],
                cache_key=meta.get("cache_key"), created_at=meta["created_at"], finished_at=meta["finished_at"],
                label=meta.get("label") or "Saved example run", summary=meta.get("summary") or {},
                result_path=f"{d.name}/result.json", evaluations_done=int(meta.get("evaluations_done") or 0),
                evaluation_budget=meta.get("evaluation_budget"),
            )
            imported += 1
        except Exception:
            log.warning("could not import example run", extra={"example": d.name}, exc_info=True)
    return None if imported else "No saved example run is available yet."


def _cleanup_upload_tmp(settings: Settings) -> None:
    tmp = settings.state_dir / "tmp"
    if tmp.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for d in (s.data_dir, s.state_dir, s.runs_dir):
            d.mkdir(parents=True, exist_ok=True)
        _cleanup_upload_tmp(s)
        store = JobStore(s.db_path, s.max_events_per_run)
        state = AppState(settings=s, store=store, live=LiveQuotes(s))  # live prices start with the first viewer
        app.state.qf = state
        state.example_note = await anyio.to_thread.run_sync(import_examples, s, store)
        monitor: asyncio.Task[None] | None = None
        if s.start_worker:
            state.worker = WorkerProcess(s, store)
            await anyio.to_thread.run_sync(state.worker.start)

            async def watch() -> None:
                while True:
                    await asyncio.sleep(2.0)
                    try:
                        await anyio.to_thread.run_sync(state.worker.check)
                    except Exception:
                        log.warning("worker check failed", exc_info=True)

            monitor = asyncio.create_task(watch())
        log.info("api started", extra={"version": __version__, "settings": s.public_dict(), "pid": os.getpid()})
        try:
            yield
        finally:
            if monitor is not None:
                monitor.cancel()
            if state.live is not None:
                await state.live.aclose()
            if state.worker is not None:
                await anyio.to_thread.run_sync(state.worker.stop)
            store.close()
            log.info("api stopped")

    app = FastAPI(
        title="QuantFolio API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.include_router(router)
    _mount_spa(app, s)

    # add_middleware wraps: the last one added is the outermost.
    app.add_middleware(BodyGuardMiddleware, max_body=s.max_body_bytes, max_upload=s.max_upload_bytes, upload_paths=("/api/data/upload",))
    # Compress the built web app and JSON; Starlette never compresses text/event-stream, so SSE stays unbuffered.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    extra_hosts = [h.strip() for h in (os.environ.get("QF_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    hosts = sorted({"127.0.0.1", "localhost", "testserver", s.host, *extra_hosts} - {"0.0.0.0", ""})
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(s.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["X-Total-Count", "Retry-After", "X-Request-ID", "Location"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware, csp_spa=os.environ.get("QF_SPA_CSP") or DEFAULT_SPA_CSP)
    return app


_NOT_BUILT = """<!doctype html><html lang=en><head><meta charset=utf-8><title>QuantFolio</title></head>
<body style="font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px">
<h1>QuantFolio API is running</h1><p>The web interface has not been built yet. Run
<code>scripts\\setup.ps1</code> (or <code>npm run build</code> in <code>frontend</code>), then restart with
<code>scripts\\start.ps1</code>.</p><p>API docs: <a href="/api/docs">/api/docs</a></p></body></html>"""


def _mount_spa(app: FastAPI, s: Settings) -> None:
    dist = s.frontend_dist

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        root = dist.resolve()
        index = root / "index.html"
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT, status_code=503)
        if path:
            # Only plain relative paths inside dist; no traversal, no drive letters, no hidden files.
            parts = path.replace("\\", "/").split("/")
            if any(p in ("", ".", "..") or p.startswith(".") or ":" in p for p in parts):
                return _index(index)
            candidate = (root / Path(*parts)).resolve()
            if root in candidate.parents and candidate.is_file():
                immutable = parts[0] == "assets"
                return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
            if "." in parts[-1] and parts[0] == "assets":
                return Response(status_code=404)
        return _index(index)


def _index(index: Path) -> Response:
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _app_from_env() -> FastAPI:  # pragma: no cover - uvicorn --factory entry
    return create_app()


__all__: list[Any] = ["create_app", "import_examples", "DEFAULT_SPA_CSP"]
