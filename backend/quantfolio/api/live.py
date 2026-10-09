"""Live NSE prices for the price ticker (display only).

Live prices never enter a run: runs read only the stored dataset. One upstream
feed per process, started when the first viewer arrives and stopped
`live_idle_s` after the last one leaves. All Yahoo Finance traffic stays on the
server; browsers read `/api/market/live` and `/api/market/stream`.

* Snapshot: one batched daily download (last price + previous close), run in a
  worker thread with a bounded timeout, so prices show at once (also when the
  market is closed).
* Ticks: Yahoo's streamer websocket (about one tick per second per symbol while
  NSE trades). The service owns the reconnect loop (exponential backoff).
* Fallback: if Yahoo cannot be reached, the stored dataset's last close is
  served with status ``offline`` and is never labelled live.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol

from ..contracts import LiveQuote, LiveQuotesResponse, LiveStatus
from ..settings import Settings

log = logging.getLogger("quantfolio.api.live")

IST = timezone(timedelta(hours=5, minutes=30), "IST")
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)
INDICES: tuple[tuple[str, str], ...] = (("^NSEI", "NIFTY 50"), ("^BSESN", "SENSEX"))
DELAYED = frozenset({"^BSESN"})  # Yahoo's BSE feed runs about 15 minutes behind
SOURCE_YAHOO = "Yahoo Finance"
SOURCE_STORED = "Stored dataset"
STREAM_URL = "wss://streamer.finance.yahoo.com/?version=2"
REGULAR_MARKET = 1  # PricingData.market_hours


def in_session(now: datetime) -> bool:
    """NSE regular session clock: 09:15-15:30 IST, Monday to Friday (exchange holidays are not known here)."""
    t = now.astimezone(IST)
    if t.weekday() >= 5:
        return False
    minutes = t.hour * 60 + t.minute
    return SESSION_OPEN[0] * 60 + SESSION_OPEN[1] <= minutes < SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]


def yahoo_symbol(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


def app_symbol(ysym: str) -> str:
    return ysym.removesuffix(".NS")


# --------------------------------------------------------------------------- #
# Upstream adapters (injectable; tests pass fakes, so no network)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tick:
    symbol: str  # Yahoo symbol, e.g. "RELIANCE.NS" or "^NSEI"
    price: float
    change: float | None = None
    at: datetime | None = None
    market_hours: int = REGULAR_MARKET


@dataclass(frozen=True)
class SeedQuote:
    price: float
    prev_close: float | None
    day: date  # trading day of `price`


class Upstream(Protocol):
    async def run(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        """Stream ticks until the connection ends (return) or fails (raise)."""


Seeder = Callable[[list[str]], dict[str, SeedQuote]]  # blocking; runs in a worker thread
StoredSource = Callable[[list[str]], tuple[dict[str, tuple[float, float | None]], date | None]]  # blocking


def decode_tick(raw: str | bytes) -> Tick | None:
    """One streamer frame -> Tick. Malformed frames are dropped."""
    from google.protobuf.message import DecodeError
    from yfinance.pricing_pb2 import PricingData

    try:
        payload = json.loads(raw)
        msg = PricingData()
        msg.ParseFromString(base64.b64decode(payload["message"]))
        if not msg.id or not (msg.price > 0):
            return None
        at = datetime.fromtimestamp(msg.time / 1000, UTC) if msg.time > 0 else None
        return Tick(symbol=msg.id, price=float(msg.price), change=float(msg.change), at=at, market_hours=int(msg.market_hours))
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, DecodeError):
        return None


class YahooStream:
    """Yahoo Finance streamer websocket. Returns on a clean close; the service reconnects."""

    def __init__(self, url: str = STREAM_URL, resubscribe_s: float = 15.0, silent_s: float = 60.0) -> None:
        self.url = url
        self.resubscribe_s = resubscribe_s
        self.silent_s = silent_s

    async def run(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        from websockets.asyncio.client import connect

        sub = json.dumps({"subscribe": symbols})
        async with connect(self.url, open_timeout=10, close_timeout=2, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
            await ws.send(sub)
            last_sub = last_msg = time.monotonic()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.resubscribe_s)  # recv() is cancellation-safe
                except TimeoutError:
                    raw = None
                now = time.monotonic()
                if now - last_sub >= self.resubscribe_s:  # Yahoo drops subscriptions that are not renewed
                    await ws.send(sub)
                    last_sub = now
                if raw is None:
                    if now - last_msg >= self.silent_s and in_session(datetime.now(UTC)):
                        return  # silent while NSE trades: reconnect
                    continue
                last_msg = now
                tick = decode_tick(raw)
                if tick is not None:
                    on_tick(tick)


def yahoo_snapshot(symbols: list[str], timeout: float = 8.0) -> dict[str, SeedQuote]:
    """Last price and previous close per Yahoo symbol (one batched daily download)."""
    import yfinance as yf

    df = yf.download(symbols, period="5d", interval="1d", auto_adjust=False, progress=False, threads=True, timeout=timeout)
    if df is None or df.empty or "Close" not in df.columns.get_level_values(0):
        return {}
    close = df["Close"]
    out: dict[str, SeedQuote] = {}
    for sym in symbols:
        if sym not in close.columns:
            continue
        s = close[sym].dropna()
        if s.empty:
            continue
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        out[sym] = SeedQuote(price=float(s.iloc[-1]), prev_close=prev, day=s.index[-1].date())
    return out


def stored_closes(settings: Settings, symbols: list[str]) -> tuple[dict[str, tuple[float, float | None]], date | None]:
    """Last two stored closes per app symbol from the active dataset (offline fallback; no network)."""
    from . import core

    if settings.pipeline_impl == "fake":
        return {}, core.dataset_status(settings)[0].last_date
    from ..data import get_dataset

    ds = get_dataset(None)
    frame = ds.close if ds.close is not None else ds.prices
    out: dict[str, tuple[float, float | None]] = {}
    last: date | None = None
    for sym in symbols:
        if sym not in frame.columns:
            continue
        s = frame[sym].dropna()
        if s.empty:
            continue
        out[sym] = (float(s.iloc[-1]), float(s.iloc[-2]) if len(s) > 1 else None)
        d = s.index[-1].date()
        last = d if last is None or d > last else last
    return out, last or ds.info.last_date


def universe_symbols(settings: Settings) -> list[str]:
    """The NIFTY 50 universe in the order /api/assets lists it."""
    from . import core

    try:
        assets, _ = core.list_assets(settings)
        syms = [a.symbol for a in assets]
    except Exception:  # noqa: BLE001 - any core failure falls back to the shipped universe
        syms = []
    return syms or [r["symbol"] for r in core.universe_rows()]


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
@dataclass
class _Quote:
    ysym: str
    symbol: str
    label: str
    kind: str
    delayed: bool
    price: float | None = None
    prev_close: float | None = None
    at: datetime | None = None
    seq: int = 0
    tick_mono: float | None = None

    def model(self) -> LiveQuote:
        change = pct = None
        if self.price is not None and self.prev_close:
            change = self.price - self.prev_close
            pct = change / self.prev_close * 100.0
        return LiveQuote(
            symbol=self.symbol,
            label=self.label,
            kind=self.kind,  # type: ignore[arg-type]
            price=_r(self.price),
            change=_r(change),
            change_pct=_r(pct),
            prev_close=_r(self.prev_close),
            at=self.at,
            delayed=self.delayed,
        )


def _r(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else round(float(v), 2)


class LiveQuotes:
    """Process-wide live price feed with lazy start and idle stop."""

    def __init__(
        self,
        settings: Settings,
        *,
        upstream: Upstream | None = None,
        seeder: Seeder | None = None,
        stored: StoredSource | None = None,
        symbols: Callable[[], list[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
        seed_timeout_s: float = 12.0,
        live_window_s: float = 30.0,
        connect_grace_s: float = 15.0,
    ) -> None:
        self.settings = settings
        self.enabled = settings.live_quotes
        self.idle_s = settings.live_idle_s
        self._upstream: Upstream = upstream or YahooStream()
        self._seeder: Seeder = seeder or yahoo_snapshot
        self._stored_fn: StoredSource = stored or (lambda syms: stored_closes(settings, syms))
        self._symbols_fn = symbols or (lambda: universe_symbols(settings))
        self._clock = clock or (lambda: datetime.now(UTC))
        self.seed_timeout_s = seed_timeout_s
        self.live_window_s = live_window_s
        self.connect_grace_s = connect_grace_s

        self._symbols: list[str] | None = None
        self._quotes: dict[str, _Quote] = {}
        self._stored: tuple[list[LiveQuote], date | None] | None = None
        self._seq = 0
        self._updated_at: datetime | None = None
        self._session_day: date | None = None
        self._last_tick: float | None = None  # monotonic time of the last regular-session NSE tick
        self._tick_day: date | None = None
        self._seed_ok: bool | None = None
        self._started_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._viewers = 0
        self._last_touch = 0.0
        self._closed = False

    # ----- lifecycle --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def viewers(self) -> int:
        return self._viewers

    def touch(self) -> None:
        """A viewer is here: start the feed if needed and push back the idle stop."""
        self._last_touch = time.monotonic()
        if not self.enabled or self._closed or self.running:
            return
        self._ready = asyncio.Event()
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name="qf-live-quotes")

    def attach(self) -> None:
        self._viewers += 1
        self.touch()

    def detach(self) -> None:
        self._viewers = max(0, self._viewers - 1)
        self._last_touch = time.monotonic()

    async def aclose(self) -> None:
        self._closed = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def wait_ready(self, timeout: float) -> None:
        if not self.running:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def _run(self) -> None:
        ws_task: asyncio.Task[None] | None = None
        try:
            if self._symbols is None:
                syms = await asyncio.to_thread(self._symbols_fn)
                self._init_quotes(syms)
            await self._seed()
            self._ready.set()
            ws_task = asyncio.create_task(self._ws_loop(), name="qf-live-ws")
            last_seed = time.monotonic()
            while True:
                await asyncio.sleep(1.0)
                now = time.monotonic()
                if self._viewers == 0 and now - self._last_touch >= self.idle_s:
                    log.info("live quotes idle; stopping feed")
                    return
                if now - last_seed >= self._reseed_interval(now):
                    await self._seed()
                    last_seed = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive; never take the API down
            log.exception("live quotes feed stopped unexpectedly")
        finally:
            self._ready.set()
            if ws_task is not None:
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await ws_task

    def _init_quotes(self, syms: Iterable[str]) -> None:
        self._symbols = list(dict.fromkeys(s for s in syms if s))
        quotes: dict[str, _Quote] = {}
        for ysym, label in INDICES:
            quotes[ysym] = _Quote(ysym=ysym, symbol=ysym, label=label, kind="index", delayed=ysym in DELAYED)
        for sym in self._symbols:
            ysym = yahoo_symbol(sym)
            quotes[ysym] = _Quote(ysym=ysym, symbol=sym, label=sym, kind="stock", delayed=ysym in DELAYED)
        self._quotes = quotes

    def _reseed_interval(self, now: float) -> float:
        if self._seed_ok is not True:
            return 30.0  # Yahoo unreachable so far: retry, quietly
        if in_session(self._clock()):
            ticking = self._last_tick is not None and now - self._last_tick <= self.live_window_s
            return 600.0 if ticking else 20.0  # no ticks: keep prices fresh from snapshots
        return 600.0

    async def _seed(self) -> None:
        try:
            snap = await asyncio.wait_for(asyncio.to_thread(self._seeder, list(self._quotes)), timeout=self.seed_timeout_s)
        except Exception as exc:  # noqa: BLE001 - Yahoo/network failures of any kind mean "no snapshot"
            snap = None
            (log.warning if self._seed_ok is None else log.debug)("live quotes snapshot failed (%s)", type(exc).__name__)
        if not snap:
            if self._seed_ok is None:
                self._seed_ok = False
            if self._seed_ok is False:
                await self._load_stored()
            return
        now = time.monotonic()
        for ysym, sq in snap.items():
            q = self._quotes.get(ysym)
            if q is None or not (sq.price > 0):
                continue
            fresh_tick = q.tick_mono is not None and now - q.tick_mono < 60.0  # ticks beat a daily snapshot
            changed = False
            if not fresh_tick and q.price != sq.price:
                q.price, q.at, changed = sq.price, None, True
            if (q.prev_close is None or not fresh_tick) and sq.prev_close is not None and q.prev_close != sq.prev_close:
                q.prev_close, changed = sq.prev_close, True
            if changed:
                self._seq += 1
                q.seq = self._seq
                self._updated_at = self._clock()
            if self._session_day is None or sq.day > self._session_day:
                self._session_day = sq.day
        self._seed_ok = True

    async def _ws_loop(self) -> None:
        backoff = 1.0
        syms = list(self._quotes)
        while True:
            started = time.monotonic()
            try:
                await self._upstream.run(syms, self._on_tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect after any upstream failure
                log.debug("live quotes stream dropped: %s", type(exc).__name__)
            if time.monotonic() - started > 60.0:
                backoff = 1.0
            await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
            backoff = min(60.0, backoff * 2)

    def _on_tick(self, tick: Tick) -> None:
        q = self._quotes.get(tick.symbol)
        if q is None or not (tick.price > 0):
            return
        now = time.monotonic()
        if tick.change is not None and tick.price - tick.change > 0:
            q.prev_close = tick.price - tick.change
        q.price = tick.price
        q.at = tick.at or self._clock()
        q.tick_mono = now
        self._seq += 1
        q.seq = self._seq
        self._updated_at = self._clock()
        if tick.market_hours == REGULAR_MARKET and tick.symbol not in DELAYED:
            self._last_tick = now
            self._tick_day = (tick.at or self._clock()).astimezone(IST).date()

    async def _load_stored(self) -> tuple[list[LiveQuote], date | None]:
        if self._stored is None:
            syms = self._symbols if self._symbols is not None else await asyncio.to_thread(self._symbols_fn)
            try:
                closes, as_of = await asyncio.to_thread(self._stored_fn, list(syms))
            except Exception:  # noqa: BLE001 - no stored prices is a valid answer
                closes, as_of = {}, None
            quotes = []
            for sym in syms:
                if sym not in closes:
                    continue
                price, prev = closes[sym]
                q = _Quote(ysym=yahoo_symbol(sym), symbol=sym, label=sym, kind="stock", delayed=False, price=price, prev_close=prev)
                quotes.append(q.model())
            self._stored = (quotes, as_of)
        return self._stored

    # ----- snapshots ----------------------------------------------------------
    def has_yahoo(self) -> bool:
        return any(q.price is not None for q in self._quotes.values())

    def status(self) -> LiveStatus:
        if not self.enabled:
            return "off"
        if not self.has_yahoo():
            return "offline" if self._seed_ok is False else "connecting"
        now = self._clock()
        if not in_session(now):
            return "closed"
        mono = time.monotonic()
        if self._last_tick is not None and mono - self._last_tick <= self.live_window_s:
            return "live"
        if self._started_at is not None and mono - self._started_at < self.connect_grace_s:
            return "connecting"
        today = now.astimezone(IST).date()
        if self._session_day != today and self._tick_day != today:
            return "closed"  # an exchange holiday, or before the first trade of the day
        return "delayed"

    @property
    def seq(self) -> int:
        return self._seq

    async def snapshot(self, since: int | None = None) -> LiveQuotesResponse:
        """Full snapshot, or only quotes changed after `since` (a previous `seq`)."""
        status = self.status()
        now = self._clock()
        market_open = in_session(now)
        if status in ("off", "offline"):
            quotes, as_of = await self._load_stored()
            note = "Live prices are off. Showing the last stored close." if status == "off" else "Yahoo Finance is unreachable. Showing the last stored close."
            return LiveQuotesResponse(status=status, source=SOURCE_STORED, updated_at=None, market_open=market_open, as_of=as_of,
                                      note=note, seq=self._seq, quotes=list(quotes))
        quotes = [q.model() for q in self._quotes.values() if q.price is not None and (since is None or q.seq > since)]
        note = {"delayed": "No live ticks right now. Prices refresh every 20 seconds.",
                "closed": "NSE is closed. Showing the latest prices."}.get(status)
        return LiveQuotesResponse(status=status, source=SOURCE_YAHOO, updated_at=self._updated_at, market_open=market_open,
                                  as_of=self._session_day, note=note, seq=self._seq, quotes=quotes)
