"""Job submission: validation, preflight, idempotency, cache replay, queueing."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any

from ..contracts import ExperimentConfig, JobStatus, RunConfig, RunSummary
from ..jobs.impl import canonical_json, experiment_cell_count, experiment_keys
from ..jobs.store import JobStore, QueueFull, StoreError
from ..settings import Settings
from . import core
from .errors import ApiError

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
EARLIEST = date(1990, 1, 1)


def job_status(store: JobStore, row: Any) -> JobStatus:
    return JobStatus.model_validate(store.status_dict(row))


def run_summary(store: JobStore, row: Any) -> RunSummary:
    return RunSummary.model_validate(store.summary_dict(row))


def validate_idempotency_key(key: str | None) -> str | None:
    if key is None:
        return None
    key = key.strip()
    if not IDEMPOTENCY_RE.fullmatch(key):
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key must be 1-128 characters of letters, digits, '-', '_', ':' or '.'.")
    return key


def validate_run_config(settings: Settings, cfg: RunConfig) -> None:
    """Checks beyond the schema: dataset id shape, date window, symbols."""
    if cfg.dataset_id is not None and not DATASET_ID_RE.fullmatch(cfg.dataset_id):
        raise ApiError(422, "invalid_dataset_id", "dataset_id must be an identifier returned by /api/data/status.")
    latest = date.today() + timedelta(days=1)
    d = cfg.dates
    for name in ("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end"):
        v = getattr(d, name)
        if v < EARLIEST or v > latest:
            raise ApiError(422, "invalid_dates", f"dates.{name} must be between {EARLIEST.isoformat()} and {latest.isoformat()}.",
                           [{"loc": ["dates", name], "msg": "out of range", "type": "value_error"}])
    known = core.known_symbols(settings, cfg.dataset_id)
    if known is not None:
        unknown = [s for s in cfg.symbols if s not in known]
        if unknown:
            raise ApiError(422, "unknown_symbols", f"Unknown symbol(s) for this dataset: {', '.join(unknown[:10])}",
                           [{"loc": ["symbols"], "msg": f"unknown symbol {s}", "type": "value_error"} for s in unknown[:50]])
    if cfg.mode == "lots" and cfg.constraints.lots is None:
        raise ApiError(422, "lots_required", "constraints.lots is required when mode is 'lots'.")
    sp = cfg.constraints.transaction_costs.starting_portfolio
    if sp.type == "holdings":
        bad = [s for s in sp.weights if s.upper() not in cfg.symbols]
        if bad:
            raise ApiError(422, "invalid_holdings", "Starting holdings must reference selected symbols only.")
        if any(w < 0 for w in sp.weights.values()) or sum(sp.weights.values()) > 1.0 + 1e-9:
            raise ApiError(422, "invalid_holdings", "Starting holdings weights must be non-negative and sum to at most 1.")


def _request_hash(kind: str, payload: dict[str, Any], force_fresh: bool) -> str:
    return hashlib.sha256(f"{kind}|{int(force_fresh)}|{canonical_json(payload)}".encode()).hexdigest()


def submit(
    *,
    settings: Settings,
    store: JobStore,
    kind: str,
    config: RunConfig | ExperimentConfig,
    idempotency_key: str | None,
    force_fresh: bool,
) -> tuple[JobStatus, int]:
    """Returns (status, http_code): 202 new job, 200 replay/duplicate/idempotent."""
    payload = config.model_dump(mode="json")
    request_hash = _request_hash(kind, payload, force_fresh)
    if idempotency_key:
        found = store.lookup_idempotency(idempotency_key)
        if found is not None:
            run_id, prev_hash = found
            if prev_hash != request_hash:
                raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request body.")
            row = store.get(run_id)
            if row is not None:
                return job_status(store, row), 200

    mod = core.pipeline(settings)
    if kind == "experiment":
        assert isinstance(config, ExperimentConfig)
        base = config.base
        validate_run_config(settings, base)
        cells = experiment_cell_count(config)
        if cells > config.max_cells:
            raise ApiError(422, "too_many_cells", f"This experiment needs up to {cells} runs; max_cells is {config.max_cells}.",
                           {"cells": cells, "max_cells": config.max_cells})
        report = core.call_pipeline(settings, "preflight", base)
        if not report.can_submit:
            raise ApiError(422, "preflight_failed", _preflight_message(report), report.model_dump(mode="json"))
        try:
            config_hash, key = experiment_keys(mod, config)
        except NotImplementedError as exc:
            raise core.core_unavailable("pipeline.cache_key") from exc
        timeout = int(min(settings.experiment_timeout_s, base.timeout_seconds * cells + 60))
        budget = report.evaluation_budget * cells
        summary = {"n_symbols": len(report.encoded_symbols) or len(base.symbols), "total_qubits": report.total_qubits, "n_cells": cells}
    else:
        assert isinstance(config, RunConfig)
        validate_run_config(settings, config)
        report = core.call_pipeline(settings, "preflight", config)
        if not report.can_submit:
            raise ApiError(422, "preflight_failed", _preflight_message(report), report.model_dump(mode="json"))
        config_hash, key = core.call_pipeline(settings, "cache_key", config)
        timeout = int(config.timeout_seconds)
        budget = report.evaluation_budget
        summary = {"n_symbols": len(report.encoded_symbols) or len(config.symbols), "total_qubits": report.total_qubits,
                   "depth": config.qaoa.depth, "optimizer": config.qaoa.optimizer}

    if not force_fresh:
        cached = store.find_cached(kind, key)
        if cached is not None and _result_exists(settings, cached):
            row = store.create_cached_replay(source=cached, config=payload, config_hash=config_hash, cache_key=key,
                                             idempotency_key=idempotency_key, request_hash=request_hash)
            return job_status(store, row), 200
    try:
        row, created = store.create_job(
            kind=kind, config=payload, config_hash=config_hash, cache_key=key, timeout_s=timeout, capacity=settings.queue_capacity,
            idempotency_key=idempotency_key, request_hash=request_hash, evaluation_budget=budget, summary=summary,
            dedupe_active=True,
        )
    except QueueFull as exc:
        retry = max(5, min(120, 10 * (exc.depth - exc.capacity + 1)))
        raise ApiError(429, "queue_full", f"The job queue is full ({exc.depth}/{exc.capacity}). Try again shortly.",
                       {"queue_depth": exc.depth, "capacity": exc.capacity}, headers={"Retry-After": str(retry)}) from exc
    except StoreError as exc:
        if str(exc) == "idempotency_key_reused":
            raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request body.") from exc
        raise
    return job_status(store, row), (202 if created else 200)


def _preflight_message(report: Any) -> str:
    errors = [i for i in report.issues if i.severity == "error"]
    if errors:
        return errors[0].message
    return "The configuration cannot be submitted (see preflight details)."


def _result_exists(settings: Settings, row: Any) -> bool:
    rp = row["result_path"]
    if not rp:
        return False
    p = (settings.runs_dir / rp).resolve()
    try:
        p.relative_to(settings.runs_dir.resolve())
    except ValueError:
        return False
    return p.is_file()
