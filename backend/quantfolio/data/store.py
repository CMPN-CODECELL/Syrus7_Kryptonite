"""Immutable, content-hashed price datasets.

A dataset is a wide table of daily prices (rows = trading dates, columns =
NSE symbols without the ``.NS`` suffix) plus metadata. Datasets are built
once from a raw snapshot (or an upload), written atomically to
``data/cache/datasets/<dataset_id>.parquet`` + ``.json`` and never modified;
the id embeds the first 12 hex chars of the content SHA-256.

Missing-data policy (see docs/DATA.md): prices are stored exactly as
received (NaN = no observation). Nothing is ever back-filled. At estimation
time gaps are forward-filled from the *past* for at most
``FFILL_LIMIT_DAYS`` trading days, and an asset without a valid price on
(or within ``MAX_START_LAG_DAYS`` trading days after) the training start, or
with less than ``MIN_TRAIN_COVERAGE`` training coverage, is dropped from the
run with an explicit issue.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..contracts import AssetInfo, DatasetInfo
from .corrections import PriceJump, apply_known_corrections, price_jumps
from .paths import atomic_write_bytes, atomic_write_text, datasets_dir, find_data_file
from .universe import SURVIVORSHIP_NOTE, Universe, load_universe

FFILL_LIMIT_DAYS = 5
MAX_START_LAG_DAYS = 5
MIN_TRAIN_COVERAGE = 0.95
STALE_AFTER_DAYS = 7
SYNTHETIC_ID = "synthetic-test-v1"

MISSING_DATA_POLICY = (
    "Prices stored as received; no back-filling with later data. At estimation time gaps are forward-filled "
    f"from the last past observation for at most {FFILL_LIMIT_DAYS} trading days. Assets without a price within "
    f"{MAX_START_LAG_DAYS} trading days of the training start, or with < {int(MIN_TRAIN_COVERAGE * 100)} % "
    "training coverage, are dropped with an explicit issue. Held-out metrics use buy-and-hold from the last "
    "close before the segment."
)

RAW_ADJ = "cache/raw/yahoo_adj_close.parquet"
RAW_CLOSE = "cache/raw/yahoo_close.parquet"
RAW_META = "cache/raw/yahoo_meta.json"


@dataclass
class Dataset:
    info: DatasetInfo
    prices: pd.DataFrame  # adjusted close, DatetimeIndex 'Date', float64, NaN = missing
    close: pd.DataFrame | None = None  # unadjusted close (indicative share counts), same shape
    issues: list[str] = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)  # symbol -> load-time correction applied
    _jumps: dict[str, list[PriceJump]] | None = field(default=None, repr=False)

    @property
    def symbols(self) -> list[str]:
        return list(self.prices.columns)

    @property
    def jumps(self) -> dict[str, list[PriceJump]]:
        """Large one-day moves left in the (corrected) prices, by symbol."""
        if self._jumps is None:
            self._jumps = price_jumps(self.prices)
        return self._jumps

    def price_for_shares(self, symbol: str, on: date) -> float | None:
        """Last unadjusted close on or before `on` (falls back to adjusted)."""
        frame = self.close if self.close is not None and symbol in self.close.columns else self.prices
        if symbol not in frame.columns:
            return None
        s = frame[symbol].loc[: pd.Timestamp(on)].dropna()
        return float(s.iloc[-1]) if len(s) else None


# --------------------------------------------------------------------------- #
# Hashing and quality checks
# --------------------------------------------------------------------------- #
def content_sha256(prices: pd.DataFrame, close: pd.DataFrame | None = None) -> str:
    """Exact, canonical content hash (column names, dates, float64 values;
    every NaN canonicalised) — independent of the parquet writer version."""
    h = hashlib.sha256()
    for name, frame in (("adj", prices), ("close", close)):
        if frame is None:
            continue
        h.update(f"{name}|cols:{','.join(frame.columns)}|".encode())
        days = frame.index.values.astype("datetime64[D]").astype(np.int64)
        h.update(days.tobytes())
        vals = np.ascontiguousarray(frame.to_numpy(dtype=np.float64))
        vals = np.where(np.isnan(vals), np.float64("nan"), vals)
        h.update(vals.tobytes())
    return h.hexdigest()


def quality_check(prices: pd.DataFrame, universe: Universe, *, strict: bool) -> list[str]:
    """Return human-readable issues. With strict=True, structural problems raise ValueError."""
    issues: list[str] = []

    def fail(msg: str) -> None:
        if strict:
            raise ValueError(msg)
        issues.append(msg)

    if prices.empty or prices.shape[1] == 0:
        fail("dataset is empty")
        return issues
    if prices.index.has_duplicates:
        fail(f"{int(prices.index.duplicated().sum())} duplicate dates")
    if not prices.index.is_monotonic_increasing:
        fail("dates are not in increasing order")
    if prices.columns.has_duplicates:
        fail("duplicate symbol columns")
    vals = prices.to_numpy(dtype=np.float64)
    if np.isinf(vals).any():
        fail(f"{int(np.isinf(vals).sum())} infinite prices")
    finite = vals[np.isfinite(vals)]
    if (finite <= 0).any():
        fail(f"{int((finite <= 0).sum())} non-positive prices")
    weekend = prices.index.dayofweek >= 5
    if weekend.any():
        issues.append(f"{int(weekend.sum())} weekend-dated rows present (special exchange sessions or misaligned calendar); kept as observed")
    unknown = [c for c in prices.columns if universe.get(c) is None]
    if unknown:
        issues.append(f"symbols not in universe: {', '.join(unknown[:10])}")
    missing = [s for s in universe.symbols if s not in prices.columns]
    if missing:
        issues.append(f"universe symbols with no data: {', '.join(missing[:10])}")
    for sym in prices.columns:
        s = prices[sym]
        fv, lv = s.first_valid_index(), s.last_valid_index()
        if fv is None:
            issues.append(f"{sym}: no valid prices")
            continue
        interior = int(s.loc[fv:lv].isna().sum())
        if fv > prices.index[0]:
            issues.append(f"{sym}: history starts {fv.date()} (later than dataset start {prices.index[0].date()})")
        if interior:
            issues.append(f"{sym}: {interior} interior missing price(s) (forward-filled up to {FFILL_LIMIT_DAYS} days at estimation)")
        if lv < prices.index[-1]:
            issues.append(f"{sym}: last price {lv.date()} before dataset end {prices.index[-1].date()}")
    return issues


def _staleness(last: date, today: date | None = None) -> tuple[bool, str | None]:
    today = today or date.today()
    lag = (today - last).days
    if lag > STALE_AFTER_DAYS:
        return True, f"last price date {last.isoformat()} is {lag} days before {today.isoformat()}"
    return False, None


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _frame_to_parquet_bytes(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_parquet(buf, engine="pyarrow", index=True)
    return buf.getvalue()


def _write_dataset(ds: Dataset) -> None:
    d = datasets_dir()
    did = ds.info.dataset_id
    atomic_write_bytes(d / f"{did}.parquet", _frame_to_parquet_bytes(ds.prices))
    if ds.close is not None:
        atomic_write_bytes(d / f"{did}.close.parquet", _frame_to_parquet_bytes(ds.close))
    meta = {"info": ds.info.model_dump(mode="json"), "issues": ds.issues}
    atomic_write_text(d / f"{did}.json", json.dumps(meta, indent=2, sort_keys=True))


def _read_dataset(did: str) -> Dataset | None:
    d = datasets_dir()
    meta_p, adj_p = d / f"{did}.json", d / f"{did}.parquet"
    if not (meta_p.is_file() and adj_p.is_file()):
        return None
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    info = DatasetInfo.model_validate(meta["info"])
    prices = pd.read_parquet(adj_p, engine="pyarrow").astype(np.float64)
    close_p = d / f"{did}.close.parquet"
    close = pd.read_parquet(close_p, engine="pyarrow").astype(np.float64) if close_p.is_file() else None
    if content_sha256(prices, close) != info.sha256:
        raise ValueError(f"dataset {did} failed its integrity check")
    return _corrected(Dataset(info=info, prices=prices, close=close, issues=list(meta.get("issues", []))))


def _corrected(ds: Dataset) -> Dataset:
    """Apply known source corrections after the integrity check (stored values are never changed)
    and list each correction and each remaining large one-day move in the dataset issues."""
    prices, close, applied = apply_known_corrections(ds.prices, ds.close)
    out = Dataset(info=ds.info, prices=prices, close=close, issues=list(ds.issues), corrections=applied)
    extra = [f"{sym}: corrected at load: {note}" for sym, note in applied.items()]
    extra += [f"{sym}: {j.describe()}" for sym, js in out.jumps.items() for j in js]
    if extra:
        out.issues = out.issues + extra
        out.info = ds.info.model_copy(update={"issues": list(ds.info.issues) + extra})
    return out


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [str(c).strip().upper().removesuffix(".NS") for c in out.columns]
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index)).normalize().astype("datetime64[ns]")
    out.index.name = "Date"
    out = out.astype(np.float64).sort_index()
    return out


def _ordered_columns(frame: pd.DataFrame, universe: Universe) -> pd.DataFrame:
    order = [s for s in universe.symbols if s in frame.columns] + [c for c in frame.columns if universe.get(c) is None]
    return frame[order]


def make_dataset(
    prices: pd.DataFrame,
    *,
    label: str,
    source: str,
    id_prefix: str,
    downloaded_at: datetime | None,
    close: pd.DataFrame | None = None,
    strict: bool = True,
    persist: bool = True,
) -> Dataset:
    universe = load_universe()
    prices = _ordered_columns(_normalise_frame(prices), universe)
    if close is not None:
        close = _normalise_frame(close).reindex(index=prices.index, columns=prices.columns)
    issues = quality_check(prices, universe, strict=strict)
    sha = content_sha256(prices, close)
    first, last = prices.index[0].date(), prices.index[-1].date()
    info = DatasetInfo(
        dataset_id=f"{id_prefix}-{sha[:12]}",
        label=label,  # type: ignore[arg-type]
        source=source,
        downloaded_at=downloaded_at,
        first_date=first,
        last_date=last,
        n_symbols=int(prices.shape[1]),
        sha256=sha,
        stale=False,
        stale_reason=None,
        survivorship_note=SURVIVORSHIP_NOTE,
        universe_source=universe.source,
        universe_as_of=universe.as_of,
        missing_data_policy=MISSING_DATA_POLICY,
        issues=issues,
    )
    ds = Dataset(info=info, prices=prices, close=close, issues=issues)
    if persist and _read_dataset(info.dataset_id) is None:
        _write_dataset(ds)
    return _corrected(ds)


# --------------------------------------------------------------------------- #
# Default dataset (built from the committed raw Yahoo snapshot)
# --------------------------------------------------------------------------- #
_lock = threading.Lock()
_cache: dict[str, Dataset] = {}


def _build_default_from_raw() -> Dataset:
    meta = json.loads(find_data_file(RAW_META).read_text(encoding="utf-8"))
    adj = pd.read_parquet(find_data_file(RAW_ADJ), engine="pyarrow")
    close_path = None
    try:
        close_path = find_data_file(RAW_CLOSE)
    except FileNotFoundError:
        pass
    close = pd.read_parquet(close_path, engine="pyarrow") if close_path else None
    downloaded = meta.get("downloaded_at_utc")
    dl = datetime.fromisoformat(downloaded) if downloaded else None
    source = f"{meta.get('provider', 'Yahoo Finance')} — daily adjusted close, NSE (.NS) tickers"
    return make_dataset(adj, label="downloaded historical", source=source, id_prefix="nifty50-yahoo", downloaded_at=dl, close=close, strict=False)


def _default_pointer() -> Path:
    return datasets_dir() / "default.txt"


def default_dataset_id() -> str:
    p = _default_pointer()
    if p.is_file():
        did = p.read_text(encoding="utf-8").strip()
        if did and (datasets_dir() / f"{did}.json").is_file():
            return did
    with _lock:
        ds = _build_default_from_raw()
        atomic_write_text(p, ds.info.dataset_id)
        _cache[ds.info.dataset_id] = ds
        return ds.info.dataset_id


def _with_dynamic_fields(ds: Dataset, today: date | None = None) -> Dataset:
    stale, reason = _staleness(ds.info.last_date, today)
    label = ds.info.label
    if stale and label == "downloaded historical":
        label = "stale cache"
    if stale == ds.info.stale and label == ds.info.label:
        return ds
    info = ds.info.model_copy(update={"stale": stale, "stale_reason": reason, "label": label})
    return Dataset(info=info, prices=ds.prices, close=ds.close, issues=ds.issues, corrections=ds.corrections, _jumps=ds._jumps)


def get_dataset(dataset_id: str | None = None) -> Dataset:
    """Load a dataset by id (None = default). Raises KeyError if unknown."""
    if dataset_id is None or dataset_id == "":
        dataset_id = default_dataset_id()
    if dataset_id == SYNTHETIC_ID:
        from .synthetic import synthetic_dataset

        return synthetic_dataset()
    if not _valid_id(dataset_id):
        raise KeyError("unknown dataset id")
    with _lock:
        ds = _cache.get(dataset_id)
        if ds is None:
            ds = _read_dataset(dataset_id)
            if ds is None:
                raise KeyError("unknown dataset id")
            _cache[dataset_id] = ds
    return _with_dynamic_fields(ds)


def _valid_id(did: str) -> bool:
    return 0 < len(did) <= 64 and all(c.isalnum() or c == "-" for c in did)


def list_dataset_infos() -> list[DatasetInfo]:
    out = []
    for p in sorted(datasets_dir().glob("*.json")):
        try:
            out.append(get_dataset(p.stem).info)
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# Interfaces used by the platform (B)
# --------------------------------------------------------------------------- #
def asset_infos(ds: Dataset, universe: Universe | None = None) -> list[AssetInfo]:
    universe = universe or load_universe()
    idx = ds.prices.index
    n_dates = len(idx)
    out: list[AssetInfo] = []
    for a in universe.assets:
        if a.symbol not in ds.prices.columns:
            out.append(AssetInfo(symbol=a.symbol, name=a.name, sector=a.sector, isin=a.isin, data_ok=False, data_note="not in this dataset"))
            continue
        s = ds.prices[a.symbol]
        valid = s.dropna()
        if valid.empty:
            out.append(AssetInfo(symbol=a.symbol, name=a.name, sector=a.sector, isin=a.isin, data_ok=False, data_note="no valid prices"))
            continue
        fv, lv = valid.index[0], valid.index[-1]
        start_lag = int((idx < fv).sum())
        interior = int(s.loc[fv:lv].isna().sum())
        notes = []
        if start_lag > MAX_START_LAG_DAYS:
            notes.append(f"history starts {fv.date().isoformat()}; not usable for training windows starting earlier")
        if interior:
            notes.append(f"{interior} missing day(s) inside history")
        price = ds.price_for_shares(a.symbol, lv.date())
        price_notes = [f"corrected: {ds.corrections[a.symbol]}"] if a.symbol in ds.corrections else []
        price_notes += [j.describe() for j in ds.jumps.get(a.symbol, [])]
        year = valid.loc[lv - pd.DateOffset(years=1) :]
        change = float(year.iloc[-1] / year.iloc[0] - 1.0) if year.index[0] <= lv - pd.DateOffset(days=358) else None
        if any(year.index[0].date() < j.on for j in ds.jumps.get(a.symbol, [])):
            change = None  # a 1-year change across an unadjusted event would mislead

        out.append(
            AssetInfo(
                symbol=a.symbol,
                name=a.name,
                sector=a.sector,
                isin=a.isin,
                first_date=fv.date(),
                last_date=lv.date(),
                n_obs=int(valid.size),
                coverage=round(valid.size / n_dates, 6) if n_dates else 0.0,
                # Sources store prices as float32 (2311.6001); the exchange quotes to the paisa.
                last_price_inr=round(price, 2) if price is not None else None,
                change_1y=round(change, 6) if change is not None else None,
                trend=_trend(year),
                data_ok=start_lag <= MAX_START_LAG_DAYS,
                data_note="; ".join(notes) or None,
                price_note="; ".join(price_notes) or None,
            )
        )
    return out


def _trend(s: pd.Series, points: int = 48) -> list[float]:
    """Evenly sampled closes ending at the last one (for a sparkline)."""
    if len(s) < 2:
        return []
    idx = np.unique(np.linspace(0, len(s) - 1, num=min(points, len(s))).round().astype(int))
    return [round(float(v), 2) for v in s.iloc[idx]]


def list_assets(dataset_id: str | None = None) -> tuple[list[AssetInfo], DatasetInfo]:
    ds = get_dataset(dataset_id)
    return asset_infos(ds), ds.info


def dataset_status() -> tuple[DatasetInfo, list[DatasetInfo]]:
    active = get_dataset(None).info
    available = list_dataset_infos()
    if not any(d.dataset_id == active.dataset_id for d in available):
        available.insert(0, active)
    return active, available


def refresh_from_yahoo(
    downloader: Callable[..., pd.DataFrame] | None = None,
    raw_dir: Path | None = None,
    *,
    start: str = "2019-01-01",
    end_exclusive: str | None = None,
) -> Dataset:
    """Download a fresh snapshot (Adj Close + Close) for the universe and build
    a new immutable dataset. `downloader(tickers, start, end)` must return a
    yfinance-style frame with column MultiIndex (field, ticker). Never called
    by the default test-suite (live test is opt-in with QF_LIVE_TESTS=1)."""
    universe = load_universe()
    tickers = [f"{s}.NS" for s in universe.symbols]
    end_exclusive = end_exclusive or (pd.Timestamp(date.today()) + pd.Timedelta(days=1)).date().isoformat()
    if downloader is None:
        import yfinance as yf  # optional, only for an explicit refresh

        def downloader(tk: list[str], s: str, e: str) -> pd.DataFrame:  # type: ignore[misc]
            return yf.download(tk, start=s, end=e, auto_adjust=False, progress=False, threads=False)

    raw = downloader(tickers, start, end_exclusive)
    if raw is None or raw.empty:
        raise ValueError("price provider returned no data")
    adj, close = raw["Adj Close"], raw["Close"] if "Close" in raw.columns.get_level_values(0) else None
    now = datetime.now(timezone.utc)
    if raw_dir is not None:
        raw_dir = Path(raw_dir)
        atomic_write_bytes(raw_dir / "yahoo_adj_close.parquet", _frame_to_parquet_bytes(adj))
        if close is not None:
            atomic_write_bytes(raw_dir / "yahoo_close.parquet", _frame_to_parquet_bytes(close))
        atomic_write_text(
            raw_dir / "yahoo_meta.json",
            json.dumps({"provider": "Yahoo Finance via yfinance", "downloaded_at_utc": now.isoformat(), "requested_start": start, "requested_end_exclusive": end_exclusive}, indent=2),
        )
    return make_dataset(adj, label="downloaded historical", source="Yahoo Finance via yfinance (refresh)", id_prefix="nifty50-yahoo", downloaded_at=now, close=close, strict=False, persist=raw_dir is not None)


def dataset_summary(ds: Dataset) -> dict[str, Any]:
    return {"dataset_id": ds.info.dataset_id, "sha256": ds.info.sha256, "label": ds.info.label, "rows": int(ds.prices.shape[0]), "symbols": ds.symbols}
