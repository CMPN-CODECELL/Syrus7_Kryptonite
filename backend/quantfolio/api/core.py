"""Adapter between the API and the quantum-core workstream's modules
(`quantfolio.data`, `quantfolio.presets`, `quantfolio.pipeline`).

With `QF_PIPELINE_IMPL=fake` it serves deterministic stand-ins (universe CSV +
synthetic dataset) so the platform can be tested without the core. With the
real implementation a missing core function becomes a clean 503, never a 500.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from ..contracts import (
    AssetInfo,
    BaselineSettings,
    ConstraintsConfig,
    DatasetInfo,
    DateSplit,
    Preset,
    QaoaSettings,
    RunConfig,
)
from ..jobs.impl import pipeline_module
from ..settings import PROJECT_ROOT, Settings
from .errors import ApiError

log = logging.getLogger("quantfolio.api.core")

UNIVERSE_DIR = PROJECT_ROOT / "data" / "universe"


def core_unavailable(what: str) -> ApiError:
    return ApiError(503, "core_unavailable", f"The quantum core is not available yet ({what}).")


def _call(fn_owner: str, fn_name: str, *args: Any, **kwargs: Any) -> Any:
    import importlib

    try:
        mod = importlib.import_module(f"quantfolio.{fn_owner}")
        fn = getattr(mod, fn_name)
    except (ImportError, AttributeError) as exc:
        raise core_unavailable(f"{fn_owner}.{fn_name}") from exc
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        raise core_unavailable(f"{fn_owner}.{fn_name}") from exc


def pipeline(settings: Settings) -> ModuleType:
    return pipeline_module(settings.pipeline_impl)


def call_pipeline(settings: Settings, fn_name: str, *args: Any) -> Any:
    from ..pipeline import PipelineError

    mod = pipeline(settings)
    try:
        return getattr(mod, fn_name)(*args)
    except NotImplementedError as exc:
        raise core_unavailable(f"pipeline.{fn_name}") from exc
    except PipelineError as exc:
        from ..logging_utils import safe_message

        raise ApiError(422, str(exc.code)[:64], safe_message(exc.message), exc.details or None) from exc


# --------------------------------------------------------------------------- #
# Universe (shipped CSV) — used for symbol validation and by the fake core
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def universe_rows() -> tuple[dict[str, str], ...]:
    files = sorted(UNIVERSE_DIR.glob("*.csv"))
    if not files:
        return ()
    rows = []
    with open(files[-1], newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            sym = (r.get("Symbol") or "").strip().upper()
            if sym:
                rows.append({"symbol": sym, "name": (r.get("Company Name") or "").strip(), "sector": (r.get("Industry") or "").strip(),
                             "isin": (r.get("ISIN Code") or "").strip()})
    return tuple(rows)


def universe_symbols() -> frozenset[str]:
    return frozenset(r["symbol"] for r in universe_rows())


def _fake_dataset() -> DatasetInfo:
    return DatasetInfo(
        dataset_id="fake-synthetic",
        label="synthetic test",
        source="deterministic fake pipeline (platform tests only)",
        first_date=date(2019, 1, 1),
        last_date=date(2026, 10, 8),
        n_symbols=len(universe_rows()),
        sha256="0" * 64,
        survivorship_note="Synthetic data; no survivorship semantics.",
        universe_source="NSE NIFTY 50 constituents CSV (shipped)",
        missing_data_policy="none (synthetic)",
    )


def _fake_default_config() -> RunConfig:
    return RunConfig(
        symbols=["HDFCBANK", "ICICIBANK", "INFY", "TCS", "RELIANCE", "ITC"],
        dates=DateSplit(train_start=date(2019, 1, 1), train_end=date(2023, 12, 31), validation_start=date(2024, 1, 1),
                        validation_end=date(2024, 12, 31), test_start=date(2025, 1, 1), test_end=date(2026, 10, 8)),
        constraints=ConstraintsConfig(cardinality=3),
        preset="quick",
    )


# --------------------------------------------------------------------------- #
# Public adapter API
# --------------------------------------------------------------------------- #
def list_assets(settings: Settings, dataset_id: str | None = None) -> tuple[list[AssetInfo], DatasetInfo]:
    if settings.pipeline_impl == "fake":
        assets = [AssetInfo(symbol=r["symbol"], name=r["name"], sector=r["sector"], isin=r["isin"], n_obs=1925, coverage=1.0,
                            first_date=date(2019, 1, 1), last_date=date(2026, 10, 8)) for r in universe_rows()]
        return assets, _fake_dataset()
    assets, ds = _call("data", "list_assets", dataset_id)
    return list(assets), ds


def dataset_status(settings: Settings) -> tuple[DatasetInfo, list[DatasetInfo]]:
    if settings.pipeline_impl == "fake":
        ds = _fake_dataset()
        return ds, [ds]
    active, available = _call("data", "dataset_status")
    return active, list(available)


def ingest_upload(settings: Settings, data: bytes, filename: str) -> DatasetInfo:
    from ..pipeline import PipelineError

    if settings.pipeline_impl == "fake":
        return _fake_ingest(data, filename)
    try:
        return _call("data", "ingest_upload", data, filename)
    except PipelineError as exc:
        from ..logging_utils import safe_message

        raise ApiError(422, str(exc.code)[:64], safe_message(exc.message), exc.details or None) from exc


def _fake_ingest(data: bytes, filename: str) -> DatasetInfo:
    """Minimal structural validation for the fake core (tests only)."""
    import hashlib

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(422, "invalid_encoding", "The file must be UTF-8 encoded CSV.") from exc
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3 or len(rows[0]) < 2:
        raise ApiError(422, "invalid_csv", "Expected a header row (Date + one column per symbol) and at least two data rows.")
    header = [h.strip() for h in rows[0]]
    if header[0].lower() != "date":
        raise ApiError(422, "invalid_csv", "The first column must be 'Date'.")
    syms = header[1:]
    for s in syms:
        if not s or len(s) > 20 or not all(c.isalnum() or c in "-&_" for c in s.upper()):
            raise ApiError(422, "invalid_symbol", "Column names must be ticker symbols (letters, digits, - & _).")
    dates = []
    for r in rows[1:]:
        if not r:
            continue
        try:
            dates.append(date.fromisoformat(r[0].strip()))
        except ValueError as exc:
            raise ApiError(422, "invalid_date", "Dates must be ISO formatted (YYYY-MM-DD).") from exc
        for cell in r[1:]:
            c = cell.strip()
            if c == "":
                continue
            try:
                v = float(c)
            except ValueError as exc:
                raise ApiError(422, "invalid_price", "Prices must be numbers.") from exc
            if not (v == v) or v in (float("inf"), float("-inf")) or v <= 0:
                raise ApiError(422, "invalid_price", "Prices must be finite positive numbers.")
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ApiError(422, "invalid_date", "Dates must be strictly increasing.")
    sha = hashlib.sha256(data).hexdigest()
    return DatasetInfo(dataset_id=f"upload-{sha[:12]}", label="uploaded", source="user upload (fake core)", first_date=dates[0],
                       last_date=dates[-1], n_symbols=len(syms), sha256=sha, survivorship_note="User supplied.",
                       universe_source="user upload", missing_data_policy="fake core: not applied")


def presets(settings: Settings) -> tuple[list[Preset], RunConfig]:
    if settings.pipeline_impl == "fake":
        quick = Preset(name="quick", title="Quick", description="Small, fast demonstration run.", max_assets=6,
                       qaoa=QaoaSettings(depth=1, max_evaluations=30), baselines=BaselineSettings(), timeout_seconds=120)
        return [quick], _fake_default_config()
    items = _call("presets", "list_presets")
    default = _call("presets", "default_example_config")
    return list(items), default


def capabilities(settings: Settings) -> dict[str, Any]:
    if settings.pipeline_impl == "fake":
        from ..jobs.fake_pipeline import FAKE_MAX_QUBITS

        core: dict[str, Any] = {"max_qubits": FAKE_MAX_QUBITS, "max_qubits_noise": 12, "max_exact_assets": 20, "depths": [1, 2, 3, 4, 5],
                                "optimizers": ["COBYLA", "SPSA"], "init_strategies": {"random": "random", "warm_interp": "warm"},
                                "fake_backends": [], "modes": {"selection": ["cardinality"], "lots": ["budget"]}, "limits": {}}
    else:
        core = dict(_call("presets", "core_capabilities"))
    limits = dict(core.get("limits") or {})
    limits.update({"max_upload_bytes": settings.max_upload_bytes, "max_body_bytes": settings.max_body_bytes,
                   "experiment_timeout_s": settings.experiment_timeout_s})
    core["limits"] = limits
    core["queue_capacity"] = settings.queue_capacity
    core["ibm_configured"] = settings.ibm_configured
    return core


def known_symbols(settings: Settings, dataset_id: str | None) -> frozenset[str] | None:
    """Symbols valid for a dataset, or None if unknown (preflight then decides)."""
    if dataset_id is None and settings.pipeline_impl == "fake":
        return universe_symbols() or None
    try:
        assets, _ = list_assets(settings, dataset_id)
        return frozenset(a.symbol for a in assets)
    except ApiError:
        if dataset_id is None:
            return universe_symbols() or None
        return None
    except Exception:
        log.warning("symbol lookup failed", exc_info=True)
        return None


def example_dir(settings: Settings) -> Path:
    return settings.examples_dir
