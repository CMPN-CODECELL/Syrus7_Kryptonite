"""Market chart bars: /api/market/candles with an injected fake fetcher (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quantfolio.api import candles as candles_mod
from quantfolio.api.candles import CandleSeries, CandleStore
from quantfolio.api.live import IST
from quantfolio.contracts import Candle

OPEN = datetime(2026, 10, 9, 4, 30, tzinfo=UTC)  # Friday, 10:00 IST
SATURDAY = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
FIRST_BAR = datetime(2026, 10, 9, 9, 15, tzinfo=IST)


def bars(n: int = 3, start: datetime = FIRST_BAR, step_min: int = 5) -> list[Candle]:
    return [Candle(t=start + timedelta(minutes=step_min * i), o=100.0 + i, h=102.0 + i, l=99.0 + i, c=101.0 + i) for i in range(n)]


class FakeFetcher:
    def __init__(self, series: CandleSeries | None = None, fail: bool = False) -> None:
        self.series = series if series is not None else CandleSeries(candles=bars(), prev_close=98.5)
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def __call__(self, ysym: str, interval: str) -> CandleSeries | None:
        self.calls.append((ysym, interval))
        if self.fail:
            raise ConnectionError("unreachable")
        return self.series


def candle_client(app_client: Any, fetcher: Any, clock: Any = lambda: OPEN, live: bool = True) -> Any:
    c = app_client(live_quotes=live)
    s = c.app.state.qf
    s.candles = CandleStore(s.settings, fetcher=fetcher, symbols=lambda: ["RELIANCE", "TCS"], clock=clock)
    return c


def test_off_returns_no_candles_and_makes_no_call(app_client: Any) -> None:
    fetch = FakeFetcher()
    c = candle_client(app_client, fetch, live=False)
    r = c.get("/api/market/candles", params={"symbol": "^NSEI", "interval": "5m"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "status": "off", "symbol": "^NSEI", "label": "NIFTY 50", "interval": "5m",
        "as_of": None, "prev_close": None, "source": None, "updated_at": None, "candles": [],
    }  # fmt: skip
    assert fetch.calls == []


def test_default_store_is_off_in_tests(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> None:
        raise AssertionError("no network in tests")

    monkeypatch.setattr(candles_mod, "yahoo_candles", boom)
    body = client.get("/api/market/candles").json()  # defaults: NIFTY 50, 5m
    assert (body["status"], body["symbol"], body["interval"], body["candles"]) == ("off", "^NSEI", "5m", [])


def test_live_session_candles_and_cache(app_client: Any) -> None:
    fetch = FakeFetcher()
    c = candle_client(app_client, fetch)
    body = c.get("/api/market/candles", params={"symbol": "RELIANCE", "interval": "5m"}).json()
    assert body["status"] == "live"
    assert (body["symbol"], body["label"], body["interval"]) == ("RELIANCE", "RELIANCE", "5m")
    assert body["as_of"] == "2026-10-09" and body["prev_close"] == 98.5
    assert body["source"] == "Yahoo Finance" and body["updated_at"] is not None
    assert body["candles"][0] == {"t": "2026-10-09T09:15:00+05:30", "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0}
    assert [x["c"] for x in body["candles"]] == [101.0, 102.0, 103.0]
    for _ in range(3):  # many viewers, one upstream call
        c.get("/api/market/candles", params={"symbol": "RELIANCE", "interval": "5m"})
    assert fetch.calls == [("RELIANCE.NS", "5m")]
    c.get("/api/market/candles", params={"symbol": "RELIANCE", "interval": "15m"})  # cached per interval
    c.get("/api/market/candles", params={"symbol": "^BSESN"})
    assert fetch.calls == [("RELIANCE.NS", "5m"), ("RELIANCE.NS", "15m"), ("^BSESN", "5m")]


def test_delayed_index_and_closed_market(app_client: Any) -> None:
    c = candle_client(app_client, FakeFetcher())
    assert c.get("/api/market/candles", params={"symbol": "^BSESN"}).json()["status"] == "delayed"  # Yahoo's BSE feed lags
    closed = candle_client(app_client, FakeFetcher(), clock=lambda: SATURDAY)
    body = closed.get("/api/market/candles", params={"symbol": "^NSEI"}).json()
    assert body["status"] == "closed" and body["as_of"] == "2026-10-09" and len(body["candles"]) == 3
    old = CandleSeries(candles=bars(start=FIRST_BAR - timedelta(days=1)), prev_close=97.0)
    holiday = candle_client(app_client, FakeFetcher(old))  # the clock says open, but nothing traded today
    assert holiday.get("/api/market/candles").json()["status"] == "closed"


@pytest.mark.parametrize("fetch", [FakeFetcher(fail=True), FakeFetcher(CandleSeries(candles=[], prev_close=None)), lambda *_: None])
def test_yahoo_failure_is_offline_with_no_candles(app_client: Any, fetch: Any) -> None:
    c = candle_client(app_client, fetch)
    r = c.get("/api/market/candles", params={"symbol": "TCS", "interval": "15m"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "offline" and body["candles"] == []  # never a made-up series
    assert body["source"] is None and body["prev_close"] is None and body["as_of"] is None


def test_failed_refresh_keeps_the_last_real_candles(app_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetcher()
    c = candle_client(app_client, fetch)
    first = c.get("/api/market/candles").json()
    monkeypatch.setattr(candles_mod, "TTL_OPEN_S", 0.0)  # every request refreshes
    monkeypatch.setattr(candles_mod, "TTL_FAILED_S", 0.0)
    fetch.fail = True
    again = c.get("/api/market/candles").json()
    assert len(fetch.calls) == 2
    assert again["candles"] == first["candles"] and again["updated_at"] == first["updated_at"]
    monkeypatch.setattr(candles_mod, "STALE_OK_S", 0.0)
    assert c.get("/api/market/candles").json()["status"] == "offline"


def test_unknown_symbol_and_interval_use_the_error_envelope(app_client: Any) -> None:
    fetch = FakeFetcher()
    c = candle_client(app_client, fetch)
    r = c.get("/api/market/candles", params={"symbol": "AAPL"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_symbol"
    r = c.get("/api/market/candles", params={"symbol": "../etc/passwd"})
    assert r.status_code == 404
    r = c.get("/api/market/candles", params={"symbol": "RELIANCE", "interval": "1m"})
    assert r.status_code == 422 and "error" in r.json()
    assert fetch.calls == []


def test_yahoo_candles_keeps_the_last_session_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd
    import yfinance as yf

    def frame(index: Any, closes: list[float], sym: str) -> Any:
        cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], [sym]])
        rows = [[c - 1, c + 2, c - 2, c, 0] for c in closes]
        return pd.DataFrame(rows, index=index, columns=cols, dtype=float)

    asked: list[tuple[str, str]] = []

    def download(sym: str, period: str, interval: str, **_: Any) -> Any:
        asked.append((period, interval))
        if interval == "1d":
            return frame(pd.to_datetime(["2026-10-07", "2026-10-08", "2026-10-09"]), [90.0, 95.0, 101.0], sym)
        if period == "1d":
            return pd.DataFrame()  # nothing today yet: fall back to the last session there is
        idx = pd.DatetimeIndex(["2026-10-08 15:20", "2026-10-08 15:25", "2026-10-09 09:15", "2026-10-09 09:20"], tz="Asia/Kolkata")
        df = frame(idx, [94.0, 95.0, 100.0, 101.0], sym)
        df.iloc[3, 0] = float("nan")  # a bar with a missing value is dropped, not patched
        return df

    monkeypatch.setattr(yf, "download", download)
    s = candles_mod.yahoo_candles("TCS.NS", "5m")
    assert s is not None and asked == [("10d", "1d"), ("1d", "5m"), ("5d", "5m")]
    assert [c.model_dump(mode="json") for c in s.candles] == [{"t": "2026-10-09T09:15:00+05:30", "o": 99.0, "h": 102.0, "l": 98.0, "c": 100.0}]
    assert s.prev_close == 95.0  # the close of the session before the candles

    daily = candles_mod.yahoo_candles("TCS.NS", "1d")
    assert daily is not None and [c.c for c in daily.candles] == [90.0, 95.0, 101.0] and daily.prev_close == 95.0
    assert daily.candles[-1].t.isoformat() == "2026-10-09T00:00:00+05:30"

    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame())
    assert candles_mod.yahoo_candles("TCS.NS", "15m") is None


def test_openapi_lists_candles(client: Any) -> None:
    spec = client.get("/api/openapi.json").json()
    assert "/api/market/candles" in spec["paths"]
    assert {"Candle", "CandlesResponse"} <= set(spec["components"]["schemas"])
