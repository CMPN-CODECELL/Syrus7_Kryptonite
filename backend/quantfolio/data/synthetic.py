"""Small deterministic SYNTHETIC price fixture for tests (label 'synthetic test').

Generated from a seeded multi-factor geometric random walk over business days.
It is never presented as market data; its label and source say so."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..contracts import DatasetInfo
from .store import MISSING_DATA_POLICY, SYNTHETIC_ID, Dataset, content_sha256, quality_check
from .universe import SURVIVORSHIP_NOTE, load_universe

SYNTHETIC_SYMBOLS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "RELIANCE",
    "ONGC", "SUNPHARMA", "CIPLA", "ITC", "HINDUNILVR", "LT",
]


def synthetic_prices(symbols: list[str] | None = None, seed: int = 20261008, start: str = "2021-01-01", end: str = "2026-10-07") -> pd.DataFrame:
    symbols = symbols or SYNTHETIC_SYMBOLS
    universe = load_universe()
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    n, t = len(symbols), len(dates)
    sectors = sorted({universe.sector_of(s) for s in symbols})
    sec_idx = np.array([sectors.index(universe.sector_of(s)) for s in symbols])
    market = rng.normal(0.0004, 0.009, t)
    sector_f = rng.normal(0.0, 0.006, (t, len(sectors)))
    drift = rng.uniform(-0.0002, 0.0008, n)
    beta = rng.uniform(0.6, 1.3, n)
    idio = rng.normal(0.0, 1.0, (t, n)) * rng.uniform(0.006, 0.016, n)
    rets = drift + market[:, None] * beta + sector_f[:, sec_idx] + idio
    rets[0] = 0.0
    prices = 100.0 * np.exp(np.cumsum(np.log1p(rets), axis=0)) * rng.uniform(0.5, 30.0, n)
    frame = pd.DataFrame(prices, index=pd.DatetimeIndex(dates, name="Date").astype("datetime64[ns]"), columns=symbols)
    return frame.round(6)


@lru_cache(maxsize=1)
def synthetic_dataset() -> Dataset:
    universe = load_universe()
    prices = synthetic_prices()
    issues = quality_check(prices, universe, strict=True)
    sha = content_sha256(prices, None)
    info = DatasetInfo(
        dataset_id=SYNTHETIC_ID,
        label="synthetic test",
        source="SYNTHETIC deterministic random walk (tests only, not market data)",
        downloaded_at=None,
        first_date=prices.index[0].date(),
        last_date=prices.index[-1].date(),
        n_symbols=prices.shape[1],
        sha256=sha,
        stale=False,
        survivorship_note=SURVIVORSHIP_NOTE,
        universe_source=universe.source,
        universe_as_of=universe.as_of,
        missing_data_policy=MISSING_DATA_POLICY,
        issues=issues,
    )
    return Dataset(info=info, prices=prices, close=None, issues=issues)
