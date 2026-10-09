"""Cloud versions of the /api routes that touch runs or uploads.

These routes are placed in front of the local router, so every path defined
here wins; everything else (health, presets, capabilities, example, IBM status,
`/api/market/*`) falls through to the unchanged local handlers and stays public.

Rules
-----
* There is no sign-in. No cookies or `Authorization` headers are read, except on the
  internal sweep, which takes the scheduler's Google-signed OIDC token.
* Runs are public in one shared workspace: anyone may list and read any run.
* Per-client limits (runs per day, active runs, uploads) are keyed on a salted hash
  of the client address (`client.py`). Only the client that submitted a run may
  cancel it; uploaded datasets are usable and listed only for their uploader.
* Experiments, the research preset and the IBM access check are not offered
  publicly (403 `operator_only`).
* Workload limits are enforced here, on the server. A request above a limit is
  rejected with 422 `limit_exceeded`; it is never silently reduced.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import anyio
from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..api import core, service
from ..api import routes as base
from ..api.errors import ApiError
from ..api.routes import st
from ..contracts import (
    AssetsResponse,
    DatasetInfo,
    DataStatusResponse,
    ExperimentConfig,
    IbmStatus,
    JobStatus,
    PreflightReport,
    ReadyResponse,
    RunConfig,
    RunEvent,
    RunSummary,
)
from ..jobs.fsutil import is_valid_run_id
from ..jobs.impl import experiment_cell_count, experiment_keys
from ..logging_utils import safe_message
from ..reports import render_counts_csv, render_csv, render_html, render_json
from . import datasets, dispatch
from .auth import (
    AuthError,
    Identity,
    bearer_token,
    caller_rejected,
    caller_unverifiable,
)
from .blobs import BlobStore
from .client import client_identity
from .config import CloudConfig
from .jobstore import ACTIVE_STATES, TERMINAL_STATES, CloudJobStore
from .launcher import JobLauncher

log = logging.getLogger("quantfolio.cloud.api")

router = APIRouter(prefix="/api")

LOCAL_PREFIX = "local:"  # result_path of a replay of the bundled example (files inside the image)
NOT_PUBLIC = ("experiments", "research_preset", "ibm_check")
_NOT_FOUND = "No run with that id."
_GONE = "The stored result for this run is no longer available."


@dataclass
class CloudState:
    cfg: CloudConfig
    jobs: CloudJobStore
    blobs: BlobStore
    launcher: JobLauncher
    sweep_verifier: Any = None  # .verify(token) -> True, or None when the sweep endpoint is disabled
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_poke: float = 0.0
    snap: dict[str, Any] | None = None
    snap_at: float = 0.0
    ready: tuple[bool, str] | None = None
    ready_at: float = 0.0


def cs(request: Request) -> CloudState:
    return request.app.state.qf_cloud


# --------------------------------------------------------------------------- #
# the caller: no sign-in, a salted hash of the client address
# --------------------------------------------------------------------------- #
def caller(request: Request) -> Identity:
    cfg = cs(request).cfg
    return client_identity(request, cfg.ip_salt, cfg.client_ip_source)


def _not_public(feature: str) -> None:
    raise ApiError(403, "operator_only", "This feature is not available in the public deployment.", {"feature": feature})


# --------------------------------------------------------------------------- #
# dispatch (outbox): launch after the commit, re-launch from any later request
# --------------------------------------------------------------------------- #
def kick(c: CloudState, *, force: bool = False) -> bool:
    """Start one worker execution if the queue has work and no worker is alive. Never raises."""
    return dispatch.kick(c.jobs, c.launcher, force=force)


def poke(c: CloudState) -> list[str]:
    """Cheap, throttled self-healing on status reads: fail runs of a dead worker, re-launch a lost dispatch."""
    now = time.monotonic()
    with c.lock:
        if now - c.last_poke < c.cfg.poke_min_interval_s:
            return []
        c.last_poke = now
    orphaned: list[str] = []
    try:
        orphaned = c.jobs.reconcile()
        if orphaned:
            log.warning("reconciled runs of a lost worker", extra={"run_ids": orphaned})
    except Exception:
        log.warning("reconcile failed", exc_info=True)
    kick(c)
    return orphaned


def _invalidate(c: CloudState) -> None:
    with c.lock:
        c.snap = None


def _snapshot(c: CloudState) -> dict[str, Any]:
    now = time.monotonic()
    with c.lock:
        if c.snap is not None and now - c.snap_at < 3.0:
            return c.snap
    snap = c.jobs.snapshot()
    with c.lock:
        c.snap, c.snap_at = snap, now
    return snap


# --------------------------------------------------------------------------- #
# health / status
# --------------------------------------------------------------------------- #
@router.get("/ready", response_model=ReadyResponse)
def ready(request: Request) -> ReadyResponse:
    s, c = st(request), cs(request)
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {}
    now = time.monotonic()
    with c.lock:
        cached = c.ready if (c.ready is not None and now - c.ready_at < 10.0) else None
    if cached is None:
        try:
            c.jobs.db.ping()
            cached = (True, "Firestore reachable")
        except Exception as exc:  # noqa: BLE001
            cached = (False, safe_message(type(exc).__name__))
        with c.lock:
            c.ready, c.ready_at = cached, now
    checks["db"], detail["db"] = cached
    checks["worker"] = True
    detail["worker"] = "on demand (one Cloud Run Job execution at a time)"
    try:
        active, _ = core.dataset_status(s.settings)
        checks["dataset"] = True
        detail["dataset"] = f"{active.label}: {active.first_date} to {active.last_date}"
    except ApiError as exc:
        checks["dataset"] = False
        detail["dataset"] = exc.message
    except Exception as exc:  # noqa: BLE001
        checks["dataset"] = False
        detail["dataset"] = safe_message(f"{type(exc).__name__}: {exc}", 200)
    return ReadyResponse(ready=all(checks.values()), checks=checks, detail=detail)


@router.get("/cloud/status")
def cloud_status(request: Request, response: Response) -> dict[str, Any]:
    s, c = st(request), cs(request)
    view = c.jobs.overview(caller(request).uid, _snapshot(c))
    response.headers["Cache-Control"] = "no-store"
    return {
        "mode": "cloud",
        "auth": "none",
        "compute_enabled": view["compute_enabled"],
        "compute_reason": view["compute_reason"],
        "quota": view["quota"],
        "queue": view["queue"],
        "limits": {**c.cfg.limits.as_dict(), "max_upload_bytes": s.settings.max_upload_bytes,
                   "user_datasets_per_day": c.cfg.user_daily_upload_cap},
        "disabled_features": list(NOT_PUBLIC),
    }


@router.post("/internal/sweep", include_in_schema=False)
def internal_sweep(request: Request) -> dict[str, Any]:
    """Scheduled reconciliation. Callable only with a Google-signed OIDC token of the scheduler service account."""
    c = cs(request)
    token = bearer_token(request)
    if c.sweep_verifier is None:
        raise ApiError(404, "not_found", "Unknown API endpoint.")
    if token is None:
        raise caller_rejected("missing")
    try:
        c.sweep_verifier.verify(token)
    except AuthError as exc:
        if exc.reason == "unavailable":
            raise caller_unverifiable() from exc
        raise caller_rejected(exc.reason) from exc
    orphaned = c.jobs.reconcile()
    kicked = kick(c)
    snap = c.jobs.snapshot()
    if orphaned:
        log.warning("sweep reconciled runs of a lost worker", extra={"run_ids": orphaned})
    return {"reconciled": orphaned, "kicked": kicked, "queue_depth": len(snap["sched"].get("queue") or []),
            "running": bool(snap["sched"].get("running"))}


# --------------------------------------------------------------------------- #
# datasets
# --------------------------------------------------------------------------- #
def _check_dataset_id(dataset_id: str | None) -> None:
    if dataset_id is not None and not service.DATASET_ID_RE.fullmatch(dataset_id):
        raise ApiError(422, "invalid_dataset_id", "dataset_id must be an identifier returned by /api/data/status.")


def _dataset_gate(request: Request, dataset_id: str | None, ident: Identity | None = None) -> None:
    """An uploaded dataset is usable only by the client that uploaded it, and its files must be on this instance."""
    _check_dataset_id(dataset_id)
    if not datasets.is_upload_id(dataset_id):
        return
    assert dataset_id is not None
    s, c = st(request), cs(request)
    ident = ident or caller(request)
    owned = c.jobs.owned_dataset(ident.uid, dataset_id)
    if owned is None:
        raise ApiError(404, "not_found", "No dataset with that id.")
    if not datasets.ensure_local(s.settings, c.blobs, dataset_id, owned.get("objects")):
        raise ApiError(410, "dataset_missing", "This uploaded dataset has expired. Upload the file again.")


@router.get("/assets", response_model=AssetsResponse)
def assets(request: Request, dataset_id: str | None = Query(default=None, max_length=64)) -> AssetsResponse:
    _dataset_gate(request, dataset_id)
    return base.assets(request, dataset_id)


@router.post("/preflight", response_model=PreflightReport)
def preflight(request: Request, config: RunConfig) -> PreflightReport:
    _dataset_gate(request, config.dataset_id)
    return base.preflight(request, config)


@router.get("/data/status", response_model=DataStatusResponse)
def data_status(request: Request) -> DataStatusResponse:
    s, c = st(request), cs(request)
    ident = caller(request)
    active, available = core.dataset_status(s.settings)
    # Uploaded datasets that happen to be on this instance's disk belong to someone: never list them from disk.
    shared = [d for d in available if not datasets.is_upload_id(d.dataset_id)]
    mine: list[DatasetInfo] = []
    for doc in c.jobs.owned_datasets(ident.uid):
        try:
            mine.append(DatasetInfo.model_validate(json.loads(doc.get("info_json") or "{}")))
        except ValueError:
            continue
    return DataStatusResponse(active=active, available=[*shared, *mine])


@router.post("/data/upload", response_model=DatasetInfo)
def data_upload(request: Request, file: UploadFile = File(...)) -> DatasetInfo:  # noqa: B008
    s, c = st(request), cs(request)
    ident = caller(request)
    c.jobs.check_upload_quota(ident.uid, ident.operator)
    raw_name = file.filename or "upload.csv"
    name = base._UPLOAD_NAME_RE.sub("_", raw_name.replace("\\", "/").rsplit("/", 1)[-1])[:100] or "upload.csv"
    if not name.lower().endswith(".csv"):
        raise ApiError(415, "unsupported_file_type", "Upload a .csv file (Date column plus one adjusted-close column per symbol).")
    limit = s.settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "payload_too_large", f"The file exceeds the {limit // (1024 * 1024)} MB limit.")
    if not data.strip():
        raise ApiError(422, "empty_file", "The uploaded file is empty.")
    info = core.ingest_upload(s.settings, data, name)  # validates the CSV shape; raises 422 with the reason
    objects = datasets.push(s.settings, c.blobs, info.dataset_id)
    c.jobs.register_dataset(ident.uid, ident.operator, info.model_dump(mode="json"), objects)
    return info


# --------------------------------------------------------------------------- #
# submission
# --------------------------------------------------------------------------- #
def _limit(name: str, maximum: int, requested: int) -> None:
    if requested > maximum:
        raise ApiError(422, "limit_exceeded", f"{name.removeprefix('max_').replace('_', ' ')} {requested} is above the limit of "
                       f"{maximum} for this deployment. Lower it and submit again.",
                       {"limit": name, "max": maximum, "requested": requested})


def enforce_limits(cfg: CloudConfig, config: RunConfig) -> None:
    lim = cfg.limits
    _limit("max_depth", lim.max_depth, config.qaoa.depth)
    _limit("max_shots", lim.max_shots, config.qaoa.shots)
    _limit("max_final_shots", lim.max_final_shots, config.qaoa.final_shots)
    _limit("max_evaluations", lim.max_evaluations, config.qaoa.max_evaluations)
    _limit("max_timeout_seconds", lim.max_timeout_seconds, config.timeout_seconds)
    if config.baselines.simulated_annealing:
        _limit("max_sa_sweeps", lim.max_sa_sweeps, config.baselines.sa_sweeps)
        _limit("max_sa_restarts", lim.max_sa_restarts, config.baselines.sa_restarts)
    if config.noise_comparison:
        _limit("max_noise_trials", lim.max_noise_trials, config.noise_trials)


def _status(c: CloudState, run: dict[str, Any]) -> JobStatus:
    return JobStatus.model_validate(c.jobs.status_dict(run))


def _example_source(row: Any) -> dict[str, Any]:
    return {"kind": row["kind"], "run_id": row["run_id"], "finished_at": row["finished_at"], "timeout_s": row["timeout_s"],
            "summary_json": row["summary_json"], "result_path": f"{LOCAL_PREFIX}{row['result_path']}", "counts_path": None,
            "evaluations_done": row["evaluations_done"], "evaluation_budget": row["evaluation_budget"]}


def _submit(request: Request, response: Response, kind: str, config: RunConfig | ExperimentConfig, idem: str | None,
            force_fresh: bool) -> JobStatus:
    s, c = st(request), cs(request)
    settings = s.settings
    ident = caller(request)
    key = service.validate_idempotency_key(idem)
    run_cfg = config.base if isinstance(config, ExperimentConfig) else config
    if kind == "experiment":
        _not_public("experiments")
    if run_cfg.preset == "research":
        _not_public("research_preset")
    enforce_limits(c.cfg, run_cfg)

    payload = config.model_dump(mode="json")
    request_hash = service._request_hash(kind, payload, force_fresh)
    if key:
        found = c.jobs.lookup_idempotency(ident.uid, key)
        if found is not None:
            run_id, prev_hash = found
            if prev_hash != request_hash:
                raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request body.")
            existing = c.jobs.get_owned(run_id, ident.uid)
            if existing is not None:
                response.status_code = 200
                response.headers["Location"] = f"/api/runs/{run_id}"
                return _status(c, existing)

    _dataset_gate(request, run_cfg.dataset_id, ident)
    mod = core.pipeline(settings)
    service.validate_run_config(settings, run_cfg)
    report = core.call_pipeline(settings, "preflight", run_cfg)
    if not report.can_submit:
        raise ApiError(422, "preflight_failed", service._preflight_message(report), report.model_dump(mode="json"))
    _limit("max_qubits", c.cfg.limits.max_qubits, int(report.total_qubits))
    if isinstance(config, ExperimentConfig):
        cells = experiment_cell_count(config)
        if cells > config.max_cells:
            raise ApiError(422, "too_many_cells", f"This experiment needs up to {cells} runs; max_cells is {config.max_cells}.",
                           {"cells": cells, "max_cells": config.max_cells})
        try:
            config_hash, cache_key = experiment_keys(mod, config)
        except NotImplementedError as exc:
            raise core.core_unavailable("pipeline.cache_key") from exc
        timeout = int(min(settings.experiment_timeout_s, run_cfg.timeout_seconds * cells + 60))
        budget = report.evaluation_budget * cells
        summary: dict[str, Any] = {"n_symbols": len(report.encoded_symbols) or len(run_cfg.symbols),
                                   "total_qubits": report.total_qubits, "n_cells": cells}
    else:
        config_hash, cache_key = core.call_pipeline(settings, "cache_key", config)
        timeout = int(config.timeout_seconds)
        budget = report.evaluation_budget
        summary = {"n_symbols": len(report.encoded_symbols) or len(config.symbols), "total_qubits": report.total_qubits,
                   "depth": config.qaoa.depth, "optimizer": config.qaoa.optimizer}

    if not force_fresh:
        source: dict[str, Any] | None = c.jobs.find_cached(kind, cache_key)  # shared workspace
        if source is not None and not (source.get("result_path") and c.blobs.exists(source["result_path"])):
            source = None
        if source is None:  # the bundled example is a stored result everyone may replay
            row = s.store.find_cached(kind, cache_key)
            if row is not None and row["is_example"] and service._result_exists(settings, row):
                source = _example_source(row)
        if source is not None:
            run = c.jobs.create_replay(uid=ident.uid, operator=ident.operator, source=source, config=payload, config_hash=config_hash,
                                       cache_key=cache_key, idempotency_key=key, request_hash=request_hash)
            response.status_code = 200
            response.headers["Location"] = f"/api/runs/{run['run_id']}"
            return _status(c, run)

    run, created = c.jobs.submit(uid=ident.uid, operator=ident.operator, kind=kind, config=payload, config_hash=config_hash,
                                 cache_key=cache_key, timeout_s=timeout, idempotency_key=key, request_hash=request_hash,
                                 evaluation_budget=budget, summary=summary)
    _invalidate(c)
    if run.get("status") == "queued":
        kick(c)  # the run is already durable; a lost launch is retried by later requests and the scheduled sweep
    response.status_code = 202 if created else 200
    response.headers["Location"] = f"/api/runs/{run['run_id']}"
    return _status(c, run)


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


# --------------------------------------------------------------------------- #
# reading runs
# --------------------------------------------------------------------------- #
def _when(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    request: Request,
    response: Response,
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"] | None = None,
    kind: Literal["run", "experiment"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> list[dict[str, Any]]:
    s, c = st(request), cs(request)
    rows, _ = s.store.list_jobs(limit=200)
    items = [s.store.summary_dict(r) for r in rows if r["is_example"]]
    recent = c.jobs.list_recent(200)
    if any(r.get("status") in ACTIVE_STATES for r in recent):
        poke(c)
    items += [c.jobs.summary_dict(r) for r in recent]
    items = [i for i in items if (status is None or i["status"] == status) and (kind is None or i["kind"] == kind)]
    items.sort(key=lambda i: _when(i["created_at"]), reverse=True)
    response.headers["X-Total-Count"] = str(len(items))
    return items[offset: offset + limit]


def _resolve(request: Request, run_id: str) -> tuple[bool, Any]:
    """(is_example, row_or_run). Every run is public in the shared workspace."""
    if not is_valid_run_id(run_id):
        raise ApiError(404, "not_found", _NOT_FOUND)
    row = st(request).store.get(run_id)
    if row is not None and row["is_example"]:
        return True, row
    run = cs(request).jobs.get(run_id)
    if run is None:
        raise ApiError(404, "not_found", _NOT_FOUND)
    return False, run


@router.get("/runs/{run_id}", response_model=JobStatus)
def get_run(request: Request, run_id: str) -> JobStatus:
    example, run = _resolve(request, run_id)
    if example:
        return base.get_run(request, run_id)
    c = cs(request)
    if run["status"] in ACTIVE_STATES and (run_id in poke(c)):
        run = c.jobs.get(run_id) or run
    return _status(c, run)


@router.post("/runs/{run_id}/cancel", response_model=JobStatus)
def cancel_run(request: Request, run_id: str) -> JobStatus:
    example, _ = _resolve(request, run_id)
    if example:
        return base.get_run(request, run_id)
    c = cs(request)
    outcome, run = c.jobs.request_cancel(run_id, caller(request).uid)
    if outcome == "missing" or run is None:  # it exists (resolved above) but another client submitted it
        raise ApiError(403, "cancel_not_allowed", "Only the visitor who started this run can cancel it.")
    _invalidate(c)
    return _status(c, run)


@router.get("/runs/{run_id}/config")
def get_run_config(request: Request, run_id: str) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.get_run_config(request, run_id)
    return Response(content=run["config_json"], media_type="application/json")


def _local_result(request: Request, rel: str) -> Any:
    root = st(request).settings.runs_dir.resolve()
    p = (root / rel).resolve()
    if root not in p.parents or not p.is_file():
        raise ApiError(410, "result_missing", _GONE)
    return p


def _result_bytes(request: Request, run: dict[str, Any]) -> bytes:
    if run["status"] != "succeeded":
        raise ApiError(409, "result_not_available", f"The run is {run['status']}; a result exists only for succeeded runs.",
                       {"status": run["status"]})
    rp = run.get("result_path")
    if not rp:
        raise ApiError(410, "result_missing", _GONE)
    if rp.startswith(LOCAL_PREFIX):
        return _local_result(request, rp[len(LOCAL_PREFIX):]).read_bytes()
    data = cs(request).blobs.get(rp)
    if data is None:
        raise ApiError(410, "result_missing", _GONE)
    return data


def _counts_bytes(request: Request, run: dict[str, Any]) -> bytes | None:
    rp = run.get("result_path") or ""
    if rp.startswith(LOCAL_PREFIX):
        f = _local_result(request, rp[len(LOCAL_PREFIX):]).parent / "final_counts.json"
        return f.read_bytes() if f.is_file() else None
    cp = run.get("counts_path")
    return cs(request).blobs.get(cp) if cp else None


def parse_counts(raw: bytes | None) -> dict[str, int] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("counts"), dict):
        data = data["counts"]
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items()
            if isinstance(k, str) and re.fullmatch(r"[01]{1,64}", k) and isinstance(v, int) and not isinstance(v, bool) and v >= 0}


@router.get("/runs/{run_id}/result")
def get_result(request: Request, run_id: str) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.get_result(request, run_id)
    return Response(content=_result_bytes(request, run), media_type="application/json")


@router.get("/runs/{run_id}/events/snapshot", response_model=list[RunEvent])
def events_snapshot(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0, lt=2**62),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    example, _ = _resolve(request, run_id)
    if example:
        return base.events_snapshot(request, run_id, after, limit)
    return cs(request).jobs.events_after(run_id, after, limit)


@router.get("/runs/{run_id}/events")
async def events_stream(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0, lt=2**62),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Progress as Server-Sent Events. Each stream is short (the browser reconnects with Last-Event-ID), so a
    request never outlives the hosting proxy's limit. Polling `GET /api/runs/{id}` is equivalent."""
    example, _ = await anyio.to_thread.run_sync(_resolve, request, run_id)
    if example:
        return await base.events_stream(request, run_id, after, last_event_id)
    s, c = st(request), cs(request)
    start = max(after, base._parse_last_event_id(last_event_id))
    deadline = time.monotonic() + c.cfg.events_stream_max_s

    async def gen():  # type: ignore[no-untyped-def]
        last = start
        interval = 1.0
        terminal = False
        yield ServerSentEvent(comment="stream open", retry=2000)
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            events = await anyio.to_thread.run_sync(c.jobs.events_after, run_id, last, 500)
            for ev in events:
                last = ev["id"]
                if ev["type"] == "status" and ev["data"].get("status") in TERMINAL_STATES:
                    terminal = True
                yield ServerSentEvent(data=json.dumps(ev, separators=(",", ":"), ensure_ascii=False), id=str(ev["id"]), event=ev["type"])
            if len(events) == 500:
                continue
            if terminal:
                return
            if events:
                interval = 1.0
            else:
                run = await anyio.to_thread.run_sync(c.jobs.get, run_id)
                if run is None or run["status"] in TERMINAL_STATES:
                    # resumed after the end: drain once more, then close
                    rest = await anyio.to_thread.run_sync(c.jobs.events_after, run_id, last, 500)
                    for ev in rest:
                        yield ServerSentEvent(data=json.dumps(ev, separators=(",", ":"), ensure_ascii=False), id=str(ev["id"]),
                                              event=ev["type"])
                    return
                await anyio.to_thread.run_sync(poke, c)
                interval = min(3.0, interval * 1.5)
            await anyio.sleep(interval)

    return EventSourceResponse(
        gen(),
        ping=s.settings.sse_heartbeat_s,
        ping_message_factory=base._heartbeat,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        send_timeout=30,
    )


# --------------------------------------------------------------------------- #
# reports
# --------------------------------------------------------------------------- #
def _report_inputs(request: Request, run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(_result_bytes(request, run).decode("utf-8"))
    return result, cs(request).jobs.status_dict(run)


@router.get("/runs/{run_id}/report.html")
def report_html(request: Request, run_id: str, download: bool = Query(default=False)) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.report_html(request, run_id, download)
    result, job = _report_inputs(request, run)
    counts_available = bool(run.get("counts_path")) or _counts_bytes(request, run) is not None
    body = render_html(result, job, counts_available=counts_available)
    return base._download(body, "text/html; charset=utf-8", f"quantfolio-{run_id}-report.html", inline=not download, csp=base._REPORT_CSP)


@router.get("/runs/{run_id}/report.json")
def report_json(request: Request, run_id: str) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.report_json(request, run_id)
    result, job = _report_inputs(request, run)
    return base._download(render_json(result, job), "application/json", f"quantfolio-{run_id}-report.json")


@router.get("/runs/{run_id}/report.csv")
def report_csv(request: Request, run_id: str) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.report_csv(request, run_id)
    result, _ = _report_inputs(request, run)
    return base._download(render_csv(result), "text/csv; charset=utf-8", f"quantfolio-{run_id}-report.csv")


@router.get("/runs/{run_id}/counts.csv")
def counts_csv(request: Request, run_id: str) -> Response:
    example, run = _resolve(request, run_id)
    if example:
        return base.counts_csv(request, run_id)
    result, _ = _report_inputs(request, run)
    if "cells" in result:
        raise ApiError(404, "not_applicable", "Experiments have per-cell results; raw counts are available for single runs only.")
    if not result.get("qaoa"):
        raise ApiError(404, "no_counts", "This run has no QAOA sampling batch (the quantum stage did not run).")
    counts = parse_counts(_counts_bytes(request, run))
    return base._download(render_counts_csv(counts, result), "text/csv; charset=utf-8", f"quantfolio-{run_id}-counts.csv")


# --------------------------------------------------------------------------- #
# IBM access check (network call with the server's token): not offered publicly
# --------------------------------------------------------------------------- #
@router.post("/ibm/check", response_model=IbmStatus)
def ibm_check(request: Request) -> IbmStatus:
    _not_public("ibm_check")
    return base.ibm_check(request)  # pragma: no cover - unreachable


def install(app: Any, state: CloudState) -> None:
    """Put the cloud routes in front of the local ones and attach the cloud state."""
    app.state.qf_cloud = state
    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added


__all__ = ["CloudState", "JSONResponse", "caller", "cs", "enforce_limits", "install", "kick", "parse_counts", "poke", "router"]
