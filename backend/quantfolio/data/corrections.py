"""Known source-data errors and large one-day price moves.

Stored datasets stay exactly as received (their content hash covers the raw
values). Corrections are applied when a dataset is loaded, only where the
source is provably inconsistent and the factor is known, and every applied
correction is listed in the dataset issues and in the run cache key.

A correction is applied only if the step it repairs is present: the price
ratio across ``first_adjusted`` must be within ``STEP_TOLERANCE`` of
``1 / factor``. A refreshed download that is already consistent is left alone.

Large one-day moves that are real events (a demerger, for example) are not
"corrected": they are reported with an explanation so a user can see why
returns across that date look the way they do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

JUMP_THRESHOLD = 0.30  # one-day moves larger than this are reported
STEP_TOLERANCE = 0.10


@dataclass(frozen=True)
class Correction:
    symbol: str
    first_adjusted: date  # first row the source already adjusts
    factor: float  # rows before `first_adjusted` are divided by this
    reason: str


KNOWN_CORRECTIONS: tuple[Correction, ...] = (
    Correction(
        symbol="TRENT",
        first_adjusted=date(2026, 1, 1),
        factor=1.5,
        reason=(
            "1:2 bonus issue (record date 2026-06-04). Yahoo Finance divides only 2026-01-01 to 2026-06-02 by 1.5, "
            "which leaves an artificial -33 % step on 2026-01-01; earlier prices are divided by 1.5 to match"
        ),
    ),
)

# Real events behind a large one-day move. Keyed by (symbol, ISO date).
KNOWN_EVENTS: dict[tuple[str, str], str] = {
    ("TMPV", "2025-10-14"): (
        "demerger of Tata Motors' commercial-vehicle business: holders also received TMCV shares, which this price "
        "series does not include, so returns across this date understate what a holder kept"
    ),
}


def apply_known_corrections(
    prices: pd.DataFrame, close: pd.DataFrame | None
) -> tuple[pd.DataFrame, pd.DataFrame | None, dict[str, str]]:
    """Return corrected copies of (prices, close) and {symbol: note} for each correction applied."""
    applied: dict[str, str] = {}
    out_p, out_c = prices, close
    for c in KNOWN_CORRECTIONS:
        if c.symbol not in prices.columns:
            continue
        s = prices[c.symbol].dropna()
        cut = pd.Timestamp(c.first_adjusted)
        before, after = s.loc[s.index < cut], s.loc[s.index >= cut]
        if before.empty or after.empty:
            continue
        step = float(after.iloc[0] / before.iloc[-1])
        if abs(step * c.factor - 1.0) > STEP_TOLERANCE:
            continue
        if out_p is prices:
            out_p = prices.copy()
            out_c = close.copy() if close is not None else None
        mask = out_p.index < cut
        out_p.loc[mask, c.symbol] = out_p.loc[mask, c.symbol] / c.factor
        if out_c is not None and c.symbol in out_c.columns:
            out_c.loc[mask, c.symbol] = out_c.loc[mask, c.symbol] / c.factor
        applied[c.symbol] = f"prices before {c.first_adjusted.isoformat()} divided by {c.factor:g} ({c.reason})"
    return out_p, out_c, applied


@dataclass(frozen=True)
class PriceJump:
    symbol: str
    on: date
    change: float  # fractional one-day change, e.g. -0.40

    def describe(self) -> str:
        pct = f"{self.change * 100:+.0f} %".replace("-", "−")
        why = KNOWN_EVENTS.get((self.symbol, self.on.isoformat()))
        tail = why if why else "possibly a split, bonus or demerger the data source did not adjust; returns across this date may be distorted"
        return f"one-day price move of {pct} on {self.on.isoformat()}: {tail}"


def price_jumps(prices: pd.DataFrame, threshold: float = JUMP_THRESHOLD) -> dict[str, list[PriceJump]]:
    """One-day moves larger than `threshold` (gaps forward-filled up to 5 days, as at estimation)."""
    out: dict[str, list[PriceJump]] = {}
    for sym in prices.columns:
        s = prices[sym].ffill(limit=5)
        r = s / s.shift(1) - 1.0
        hits = r[np.abs(r) > threshold].dropna()
        if len(hits):
            out[sym] = [PriceJump(sym, ts.date(), float(v)) for ts, v in hits.items()]
    return out
