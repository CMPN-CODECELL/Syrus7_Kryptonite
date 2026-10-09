"""HTTP routes under /api (see docs/CONTRACT.md).

Blocking work (SQLite, file reads, core calls) runs in sync handlers, which
FastAPI executes in its thread pool; only the SSE stream is async.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from .. import __version__
from ..contracts import (
    AssetsResponse,
    CandleInterval,
    CandlesResponse,
    Capabilities,
    DatasetInfo,
    DataStatusResponse,
    ExampleResponse,
    ExperimentConfig,
    HealthResponse,
    IbmStatus,
    JobStatus,
    LiveQuotesResponse,
    PreflightReport,
    PresetsResponse,
    ReadyResponse,
    RunConfig,
    RunEvent,
    RunSummary,
)
from ..jobs.fsutil import is_valid_run_id
from ..jobs.store import TERMINAL_STATES, JobStore
from ..logging_utils import safe_message
from ..reports import render_counts_csv, render_csv, render_html, render_json
from ..settings import Settings
from . import core, service
from .errors import ApiError

log = logging.getLogger("quantfolio.api")

router = APIRouter(prefix="/api")

_UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
_REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"


@dataclass
class AppState:
    settings: Settings
    store: JobStore
    worker: Any = None  # WorkerProcess | None
    started_at: float = field(default_factory=time.time)
    example_note: str | None = None
    live: Any = None  # live.LiveQuotes (display-only price ticker)
    candles: Any = None  # candles.CandleStore (display-only chart bars; made on first use)


def st(request: Request) -> AppState:
    return request.app.state.qf


def _job_or_404(store: JobStore, run_id: str) -> Any:
    if not is_valid_run_id(run_id):
        raise ApiError(404, "not_found", "No run with that id.")
    row = store.get(run_id)
    if row is None:
        raise ApiError(404, "not_found", "No run with that id.")
    return row


# --------------------------------------------------------------------------- #
# health / readiness / metadata
# --------------------------------------------------------------------------- #
@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=ReadyResponse)
def ready(request: Request) -> ReadyResponse:
    s = st(request)
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {}
    try:
        s.store.counts_by_status()
        checks["db"] = True
    except Exception as exc:  # pragma: no cover - db failure
        checks["db"] = False
        detail["db"] = safe_message(type(exc).__name__)
    hb = None
    try:
        hb = s.store.get_heartbeat()
    except Exception:
        pass
    if hb is not None:
        from datetime import datetime

        from ..jobs.store import utcnow

        age = (utcnow() - datetime.fromisoformat(hb["beat_at"])).total_seconds()
        checks["worker"] = age < 5.0
        detail["worker"] = f"heartbeat {age:.1f}s ago" + ("" if checks["worker"] else " (stale)")
    else:
        checks["worker"] = False
        detail["worker"] = "worker not started" if not s.settings.start_worker else "waiting for first worker heartbeat"
    try:
        active, _ = core.dataset_status(s.settings)
        checks["dataset"] = True
        detail["dataset"] = f"{active.label}: {active.first_date} to {active.last_date}"
    except ApiError as exc:
        checks["dataset"] = False
        detail["dataset"] = exc.message
    except Exception as exc:
        checks["dataset"] = False
        detail["dataset"] = safe_message(f"{type(exc).__name__}: {exc}", 200)
    return ReadyResponse(ready=all(checks.values()), checks=checks, detail=detail)


@router.get("/assets", response_model=AssetsResponse)
def assets(request: Request, dataset_id: str | None = Query(default=None, max_length=64)) -> AssetsResponse:
    if dataset_id is not None and not service.DATASET_ID_RE.fullmatch(dataset_id):
        raise ApiError(422, "invalid_dataset_id", "dataset_id must be an identifier returned by /api/data/status.")
    items, ds = core.list_assets(st(request).settings, dataset_id)
    return AssetsResponse(dataset=ds, assets=items)


@router.get("/data/status", response_model=DataStatusResponse)
def data_status(request: Request) -> DataStatusResponse:
    active, available = core.dataset_status(st(request).settings)
    return DataStatusResponse(active=active, available=available)


@router.post("/data/upload", response_model=DatasetInfo)
def data_upload(request: Request, file: UploadFile = File(...)) -> DatasetInfo:
    s = st(request).settings
    raw_name = file.filename or "upload.csv"
    name = _UPLOAD_NAME_RE.sub("_", raw_name.replace("\\", "/").rsplit("/", 1)[-1])[:100] or "upload.csv"
    if not name.lower().endswith(".csv"):
        raise ApiError(415, "unsupported_file_type", "Upload a .csv file (Date column plus one adjusted-close column per symbol).")
    data = file.file.read(s.max_upload_bytes + 1)
    if len(data) > s.max_upload_bytes:
        raise ApiError(413, "payload_too_large", f"The file exceeds the {s.max_upload_bytes // (1024 * 1024)} MB limit.")
    if not data.strip():
        raise ApiError(422, "empty_file", "The uploaded file is empty.")
    return core.ingest_upload(s, data, name)


@router.get("/presets", response_model=PresetsResponse)
def presets(request: Request) -> PresetsResponse:
    items, default = core.presets(st(request).settings)
    return PresetsResponse(presets=items, default_config=default)


@router.get("/capabilities", response_model=Capabilities)
def capabilities(request: Request) -> Capabilities:
    return Capabilities.model_validate(core.capabilities(st(request).settings))


@router.post("/preflight", response_model=PreflightReport)
def preflight(request: Request, config: RunConfig) -> PreflightReport:
    s = st(request).settings
    if config.dataset_id is not None and not service.DATASET_ID_RE.fullmatch(config.dataset_id):
        raise ApiError(422, "invalid_dataset_id", "dataset_id must be an identifier returned by /api/data/status.")
    return core.call_pipeline(s, "preflight", config)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def _submit(request: Request, response: Response, kind: str, config: Any, idem: str | None, force_fresh: bool) -> JobStatus:
    s = st(request)
    key = service.validate_idempotency_key(idem)
    status, code = service.submit(settings=s.settings, store=s.store, kind=kind, config=config, idempotency_key=key, force_fresh=force_fresh)
    response.status_code = code
    response.headers["Location"] = f"/api/runs/{status.run_id}"
    return status


@router.post("/runs", response_model=JobStatus, status_code=202)
def create_run(
    request: Request,
    response: Response,
    config: RunConfig,
    force_fresh: bool = Query(default=False),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobStatus:
    return _submit(request, response, "run", config, idempotency_key, force_fresh)


@router.post("/experiments", response_model=JobStatus, status_code=202)
def create_experiment(
    request: Request,
    response: Response,
    config: ExperimentConfig,
    force_fresh: bool = Query(default=False),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobStatus:
    return _submit(request, response, "experiment", config, idempotency_key, force_fresh)


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    request: Request,
    response: Response,
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"] | None = None,
    kind: Literal["run", "experiment"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> list[RunSummary]:
    s = st(request)
    rows, total = s.store.list_jobs(status=status, kind=kind, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [service.run_summary(s.store, r) for r in rows]


@router.get("/runs/{run_id}", response_model=JobStatus)
def get_run(request: Request, run_id: str) -> JobStatus:
    s = st(request)
    return service.job_status(s.store, _job_or_404(s.store, run_id))


@router.post("/runs/{run_id}/cancel", response_model=JobStatus)
def cancel_run(request: Request, run_id: str) -> JobStatus:
    s = st(request)
    _job_or_404(s.store, run_id)
    outcome = s.store.request_cancel(run_id)
    if outcome == "missing":  # pragma: no cover - deleted between calls
        raise ApiError(404, "not_found", "No run with that id.")
    return service.job_status(s.store, s.store.get(run_id))


@router.get("/runs/{run_id}/config")
def get_run_config(request: Request, run_id: str) -> Response:
    """The submitted configuration (RunConfig, or ExperimentConfig for exp-*), for retry/edit."""
    s = st(request)
    row = _job_or_404(s.store, run_id)
    return Response(content=row["config_json"], media_type="application/json")


def _result_file(s: AppState, row: Any) -> Path:
    if row["status"] != "succeeded":
        raise ApiError(409, "result_not_available", f"The run is {row['status']}; a result exists only for succeeded runs.",
                       {"status": row["status"]})
    rp = row["result_path"]
    if not rp:
        raise ApiError(410, "result_missing", "The stored result for this run is no longer available.")
    root = s.settings.runs_dir.resolve()
    p = (root / rp).resolve()
    if root not in p.parents or not p.is_file():
        raise ApiError(410, "result_missing", "The stored result for this run is no longer available.")
    return p


@router.get("/runs/{run_id}/result")
def get_result(request: Request, run_id: str) -> Response:
    s = st(request)
    row = _job_or_404(s.store, run_id)
    data = _result_file(s, row).read_bytes()
    return Response(content=data, media_type="application/json")


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def _parse_last_event_id(value: str | None) -> int:
    if not value:
        return 0
    try:
        v = int(value.strip())
    except ValueError:
        return 0
    return v if 0 <= v < 2**62 else 0


@router.get("/runs/{run_id}/events/snapshot", response_model=list[RunEvent])
def events_snapshot(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0, lt=2**62),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    s = st(request)
    _job_or_404(s.store, run_id)
    return s.store.events_after(run_id, after, limit)


def _heartbeat() -> ServerSentEvent:
    return ServerSentEvent(event="heartbeat", data=json.dumps({"type": "heartbeat", "data": {}}))


@router.get("/runs/{run_id}/events")
async def events_stream(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0, lt=2**62),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    s = st(request)
    await anyio.to_thread.run_sync(_job_or_404, s.store, run_id)
    start = max(after, _parse_last_event_id(last_event_id))
    poll = s.settings.sse_poll_s

    async def gen():  # type: ignore[no-untyped-def]
        last = start
        interval = poll
        terminal = False  # a terminal status event was streamed
        finished = False  # the job row is terminal: drain what is left, then close
        checked = 0.0
        while True:
            if await request.is_disconnected():
                return
            events = await anyio.to_thread.run_sync(s.store.events_after, run_id, last, 500)
            for ev in events:
                last = ev["id"]
                if ev["type"] == "status" and ev["data"].get("status") in TERMINAL_STATES:
                    terminal = True
                yield ServerSentEvent(data=json.dumps(ev, separators=(",", ":"), ensure_ascii=False), id=str(ev["id"]), event=ev["type"])
            if len(events) == 500:
                continue
            if events:
                interval = poll
            elif terminal or finished:
                return
            else:
                now = time.monotonic()
                if now - checked >= 1.0:
                    # e.g. a resume after the end of the stream: no terminal event will arrive again.
                    checked = now
                    row = await anyio.to_thread.run_sync(s.store.get, run_id)
                    if row is None or row["status"] in TERMINAL_STATES:
                        finished = True
                        continue
                interval = min(1.0, interval * 1.5)
            await anyio.sleep(interval)

    return EventSourceResponse(
        gen(),
        ping=s.settings.sse_heartbeat_s,
        ping_message_factory=_heartbeat,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        send_timeout=30,
    )


# --------------------------------------------------------------------------- #
# reports
# --------------------------------------------------------------------------- #
def _load_for_report(s: AppState, run_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    row = _job_or_404(s.store, run_id)
    p = _result_file(s, row)
    result = json.loads(p.read_text(encoding="utf-8"))
    job = s.store.status_dict(row)
    return result, job, p


def _download(content: bytes, media_type: str, filename: str, inline: bool = False, csp: str | None = None) -> Response:
    headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"'}
    if csp:
        headers["Content-Security-Policy"] = csp
    return Response(content=content, media_type=media_type, headers=headers)


def _read_counts(p: Path) -> dict[str, int] | None:
    f = p.parent / "final_counts.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("counts"), dict):
        data = data["counts"]
    if not isinstance(data, dict):
        return None
    out: dict[str, int] = {}
    for k, v in data.items():
        if isinstance(k, str) and re.fullmatch(r"[01]{1,64}", k) and isinstance(v, int) and v >= 0:
            out[k] = v
    return out


@router.get("/runs/{run_id}/report.html")
def report_html(request: Request, run_id: str, download: bool = Query(default=False)) -> Response:
    s = st(request)
    result, job, p = _load_for_report(s, run_id)
    body = render_html(result, job, counts_available=(p.parent / "final_counts.json").is_file())
    return _download(body, "text/html; charset=utf-8", f"quantfolio-{run_id}-report.html", inline=not download, csp=_REPORT_CSP)


@router.get("/runs/{run_id}/report.json")
def report_json(request: Request, run_id: str) -> Response:
    s = st(request)
    result, job, _ = _load_for_report(s, run_id)
    return _download(render_json(result, job), "application/json", f"quantfolio-{run_id}-report.json")


@router.get("/runs/{run_id}/report.csv")
def report_csv(request: Request, run_id: str) -> Response:
    s = st(request)
    result, _, _ = _load_for_report(s, run_id)
    return _download(render_csv(result), "text/csv; charset=utf-8", f"quantfolio-{run_id}-report.csv")


@router.get("/runs/{run_id}/counts.csv")
def counts_csv(request: Request, run_id: str) -> Response:
    s = st(request)
    result, _, p = _load_for_report(s, run_id)
    if "cells" in result:
        raise ApiError(404, "not_applicable", "Experiments have per-cell results; raw counts are available for single runs only.")
    if not result.get("qaoa"):
        raise ApiError(404, "no_counts", "This run has no QAOA sampling batch (the quantum stage did not run).")
    return _download(render_counts_csv(_read_counts(p), result), "text/csv; charset=utf-8", f"quantfolio-{run_id}-counts.csv")


# --------------------------------------------------------------------------- #
# example run / IBM
# --------------------------------------------------------------------------- #
@router.get("/example", response_model=ExampleResponse)
def example(request: Request) -> ExampleResponse:
    s = st(request)
    row = s.store.example_job()
    if row is None:
        return ExampleResponse(run_id=None, detail=s.example_note or "No saved example run is available yet.")
    return ExampleResponse(run_id=row["run_id"], label=row["label"], recorded_at=row["finished_at"],
                           detail="Genuine run recorded earlier with the default example configuration; served from storage.")


@router.get("/ibm/status", response_model=IbmStatus)
def ibm_status(request: Request) -> IbmStatus:
    from .. import ibm

    return ibm.local_status(st(request).settings)


@router.post("/ibm/check", response_model=IbmStatus)
def ibm_check(request: Request) -> IbmStatus:
    """Explicit, user-initiated access check: lists backends (no jobs, no quota)."""
    from .. import ibm

    return ibm.check_access(st(request).settings)


# --------------------------------------------------------------------------- #
# live market prices (display only; never used by a run)
# --------------------------------------------------------------------------- #
@router.get("/market/live", response_model=LiveQuotesResponse)
async def market_live(request: Request, response: Response) -> LiveQuotesResponse:
    """Current NSE prices. The first call starts the feed and waits briefly for its first snapshot."""
    live = st(request).live
    live.touch()
    await live.wait_ready(timeout=5.0)
    response.headers["Cache-Control"] = "no-store"
    return await live.snapshot()


@router.get("/market/candles", response_model=CandlesResponse)
async def market_candles(request: Request, response: Response, symbol: str = Query("^NSEI", max_length=32), interval: CandleInterval = "5m") -> CandlesResponse:
    """Real price bars for the market chart: the latest session for 5m and 15m, about three months for 1d.
    An empty list with status `off` or `offline` when there is nothing real to show."""
    s = st(request)
    if s.candles is None:
        from .candles import CandleStore

        s.candles = CandleStore(s.settings)
    response.headers["Cache-Control"] = "no-store"
    return await s.candles.get(symbol, interval)


@router.get("/market/stream")
async def market_stream(request: Request) -> EventSourceResponse:
    """Live prices as Server-Sent Events: one `snapshot` frame, then `delta` frames with only the changed
    quotes, at most once per second. Each stream ends after `QF_LIVE_STREAM_MAX_S`; the browser reconnects."""
    s = st(request)
    live = s.live
    max_s = s.settings.live_stream_max_s

    def frame(snap: LiveQuotesResponse, event: str, retry: int | None = None) -> ServerSentEvent:
        return ServerSentEvent(data=snap.model_dump_json(), event=event, retry=retry)

    async def gen():  # type: ignore[no-untyped-def]
        if not live.enabled:
            yield frame(await live.snapshot(), "snapshot", retry=600_000)
            return
        live.attach()
        try:
            deadline = time.monotonic() + max_s
            await live.wait_ready(timeout=5.0)  # first frame carries prices when Yahoo answers in time
            snap = await live.snapshot()
            sent_seq, sent_status, sent_source = snap.seq, snap.status, snap.source
            yield frame(snap, "snapshot", retry=3000)
            while time.monotonic() < deadline:
                await anyio.sleep(1.0)
                if await request.is_disconnected():
                    return
                live.touch()
                if live.seq == sent_seq and live.status() == sent_status:
                    continue
                snap = await live.snapshot(since=sent_seq)
                event = "delta" if snap.source == sent_source else "snapshot"
                if event == "snapshot":
                    snap = await live.snapshot()
                sent_seq, sent_status, sent_source = snap.seq, snap.status, snap.source
                yield frame(snap, event)
        finally:
            live.detach()

    return EventSourceResponse(
        gen(),
        ping=s.settings.sse_heartbeat_s,
        ping_message_factory=_heartbeat,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        send_timeout=30,
    )


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(request: Request, rest: str) -> JSONResponse:
    """Unknown /api paths get a JSON 404; known paths with the wrong method a 405."""
    from starlette.routing import Match

    allowed: set[str] = set()
    for route in router.routes:
        if getattr(route, "endpoint", None) is api_not_found:
            continue
        match, _ = route.matches({**request.scope, "method": "__none__"})
        if match == Match.PARTIAL:
            allowed |= set(getattr(route, "methods", None) or ())
    if allowed:
        raise ApiError(405, "method_not_allowed", "Method not allowed for this endpoint.", headers={"Allow": ", ".join(sorted(allowed))})
    raise ApiError(404, "not_found", "Unknown API endpoint.")
