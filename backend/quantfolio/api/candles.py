"""Candlestick bars for the market chart (display only).

Like the price ticker, candles never enter a run: runs read only the stored
dataset. Every bar is a real Yahoo Finance bar; when Yahoo cannot be reached the
answer is an empty list with status ``offline``, never a made-up series.

* One blocking fetch per (symbol, interval), run in a worker thread with a
  bounded timeout and cached: about 30 s while NSE trades, longer when it is
  closed, so many viewers cost one upstream call.
* Intraday intervals show the latest trading session only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, get_args

from ..contracts import Candle, CandleInterval, CandlesResponse, LiveStatus
from ..settings import Settings
from .errors import ApiError
from .live import (
    DELAYED,
    INDICES,
    IST,
    SOURCE_YAHOO,
    in_session,
    universe_symbols,
    yahoo_symbol,
)

log = logging.getLogger("quantfolio.api.candles")

INTERVALS: tuple[str, ...] = get_args(CandleInterval)
DAILY_PERIOD = "3mo"  # how far back the 1d chart goes
TTL_OPEN_S = 30.0
TTL_CLOSED_S = 600.0
TTL_FAILED_S = 15.0  # retry Yahoo no faster than this
STALE_OK_S = 600.0  # after a failed refresh, keep serving the last real candles this long


@dataclass(frozen=True)
class CandleSeries:
    candles: list[Candle]  # oldest first; one trading session for intraday intervals
    prev_close: float | None


CandleFetcher = Callable[[str, str], CandleSeries | None]  # (Yahoo symbol, interval) -> series; blocking


def _frame(df: Any, ysym: str) -> Any:
    """yfinance returns (field, symbol) columns for a single symbol too; flatten them."""
    if df is None or df.empty:
        return None
    if getattr(df.columns, "nlevels", 1) > 1:
        if ysym not in df.columns.get_level_values(-1):
            return None
        df = df.xs(ysym, axis=1, level=-1)
    if not {"Open", "High", "Low", "Close"} <= set(df.columns):
        return None
    df = df[["Open", "High", "Low", "Close"]].dropna()
    return None if df.empty else df


def _ist(ts: Any) -> datetime:
    t = ts.to_pydatetime()
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


def yahoo_candles(ysym: str, interval: str, timeout: float = 8.0) -> CandleSeries | None:
    """Real OHLC bars for one Yahoo symbol, plus the close of the session before them."""
    import yfinance as yf

    def download(period: str, iv: str) -> Any:
        return _frame(yf.download(ysym, period=period, interval=iv, auto_adjust=False, progress=False, threads=False, timeout=timeout), ysym)

    if interval == "1d":
        bars = daily = download(DAILY_PERIOD, "1d")
    else:
        try:
            daily = download("10d", "1d")
        except Exception:  # noqa: BLE001 - the previous close is optional; the candles are not
            daily = None
        bars = download("1d", interval)
        if bars is None:  # nothing traded today yet (holiday, weekend, before the open): the last session there is
            bars = download("5d", interval)
        if bars is not None:
            last_day = _ist(bars.index[-1]).date()
            bars = bars[[_ist(ts).date() == last_day for ts in bars.index]]
    if bars is None or bars.empty:
        return None
    candles = [
        Candle(t=_ist(ts), o=round(float(o), 2), h=round(float(h), 2), l=round(float(lo), 2), c=round(float(c), 2))
        for ts, o, h, lo, c in zip(bars.index, bars["Open"], bars["High"], bars["Low"], bars["Close"], strict=True)
    ]
    prev: float | None = None
    if daily is not None:
        day = candles[-1].t.date()
        before = daily["Close"][[_ist(ts).date() < day for ts in daily.index]]
        if len(before):
            prev = round(float(before.iloc[-1]), 2)
    return CandleSeries(candles=candles, prev_close=prev)


@dataclass
class _Entry:
    series: CandleSeries | None
    at: datetime
    mono: float


class CandleStore:
    """Process-wide candle cache. Tests pass a fake `fetcher`, so they need no network."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: CandleFetcher | None = None,
        symbols: Callable[[], list[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
        fetch_timeout_s: float = 12.0,
    ) -> None:
        self.settings = settings
        self.enabled = settings.live_quotes
        self._fetcher: CandleFetcher = fetcher or yahoo_candles
        self._symbols_fn = symbols or (lambda: universe_symbols(settings))
        self._clock = clock or (lambda: datetime.now(UTC))
        self.fetch_timeout_s = fetch_timeout_s
        self._labels: dict[str, str] | None = None
        self._cache: dict[tuple[str, str], _Entry] = {}
        self._good: dict[tuple[str, str], _Entry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _label(self, symbol: str) -> str:
        if self._labels is None:
            syms = await asyncio.to_thread(self._symbols_fn)
            self._labels = dict(INDICES) | {s: s for s in syms if s}
        if symbol not in self._labels:
            raise ApiError(404, "unknown_symbol", "No chart for that symbol. Use a NIFTY 50 stock, ^NSEI or ^BSESN.")
        return self._labels[symbol]

    def _ttl(self, entry: _Entry) -> float:
        if entry.series is None:
            return TTL_FAILED_S
        return TTL_OPEN_S if in_session(self._clock()) else TTL_CLOSED_S

    async def _load(self, key: tuple[str, str]) -> _Entry:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # many viewers, one upstream call
            hit = self._cache.get(key)
            if hit is not None and time.monotonic() - hit.mono < self._ttl(hit):
                return hit
            try:
                series = await asyncio.wait_for(asyncio.to_thread(self._fetcher, yahoo_symbol(key[0]), key[1]), timeout=self.fetch_timeout_s)
            except Exception as exc:  # noqa: BLE001 - Yahoo/network failures of any kind mean "no candles"
                series = None
                log.debug("candles fetch failed (%s)", type(exc).__name__)
            if series is not None and not series.candles:
                series = None
            entry = _Entry(series=series, at=self._clock(), mono=time.monotonic())
            self._cache[key] = entry
            if series is not None:
                self._good[key] = entry
            return entry

    async def get(self, symbol: str, interval: str) -> CandlesResponse:
        label = await self._label(symbol)
        base: dict[str, Any] = {"symbol": symbol, "label": label, "interval": interval}
        if not self.enabled:
            return CandlesResponse(status="off", **base)
        key = (symbol, interval)
        entry = await self._load(key)
        if entry.series is None:
            good = self._good.get(key)
            if good is None or time.monotonic() - good.mono > STALE_OK_S:
                return CandlesResponse(status="offline", **base)
            entry = good  # a refresh failed: the last real candles, stamped with when they were fetched
        series = entry.series
        assert series is not None
        as_of = series.candles[-1].t.date()
        return CandlesResponse(
            status=self._status(symbol, as_of),
            as_of=as_of,
            prev_close=series.prev_close,
            source=SOURCE_YAHOO,
            updated_at=entry.at,
            candles=series.candles,
            **base,
        )

    def _status(self, symbol: str, as_of: date) -> LiveStatus:
        now = self._clock()
        if not in_session(now) or as_of != now.astimezone(IST).date():
            return "closed"
        return "delayed" if symbol in DELAYED else "live"
