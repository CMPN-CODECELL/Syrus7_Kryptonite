"""Live price ticker: /api/market/live and /api/market/stream with an injected fake upstream (no network)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, date, datetime
from typing import Any

import pytest

from quantfolio.api.live import LiveQuotes, SeedQuote, Tick, decode_tick, in_session

TRADING_DAY = date(2026, 10, 9)  # a Friday
OPEN = datetime(2026, 10, 9, 4, 30, tzinfo=UTC)  # 10:00 IST
SATURDAY = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def seed(_: list[str]) -> dict[str, SeedQuote]:
    return {
        "^NSEI": SeedQuote(22400.0, 22300.0, TRADING_DAY),
        "^BSESN": SeedQuote(72000.0, 71000.0, TRADING_DAY),
        "RELIANCE.NS": SeedQuote(99.0, 98.0, TRADING_DAY),
        "TCS.NS": SeedQuote(200.0, 190.0, TRADING_DAY),
    }


def no_yahoo(_: list[str]) -> dict[str, SeedQuote]:
    raise ConnectionError("unreachable")


def stored(syms: list[str]) -> tuple[dict[str, tuple[float, float | None]], date | None]:
    return {"RELIANCE": (97.5, 96.0), "TCS": (180.0, None)}, date(2026, 10, 8)


class FakeUpstream:
    """Ticks RELIANCE and NIFTY every `every` seconds; optionally fails the first `fail` connections."""

    def __init__(self, every: float = 0.1, fail: int = 0) -> None:
        self.every = every
        self.fail = fail
        self.calls = 0
        self.symbols: list[str] = []

    async def run(self, symbols: list[str], on_tick: Any) -> None:
        self.calls += 1
        self.symbols = symbols
        if self.calls <= self.fail:
            raise ConnectionError("dropped")
        price = 100.0
        while True:
            price += 0.5
            now = datetime.now(UTC)
            on_tick(Tick("RELIANCE.NS", price, change=price - 98.0, at=now))
            on_tick(Tick("^NSEI", 22400.0 + price, change=100.0 + price, at=now))
            on_tick(Tick("NOT.A.SYMBOL", 1.0))
            await asyncio.sleep(self.every)


def service(settings: Any, **kw: Any) -> LiveQuotes:
    kw.setdefault("upstream", FakeUpstream())
    kw.setdefault("seeder", seed)
    kw.setdefault("stored", stored)
    kw.setdefault("symbols", lambda: ["RELIANCE", "TCS"])
    kw.setdefault("clock", lambda: OPEN)
    return LiveQuotes(settings, **kw)


def live_client(app_client: Any, **kw: Any) -> Any:
    c = app_client(live_quotes=True, live_stream_max_s=kw.pop("max_s", 2.5))
    c.app.state.qf.live = service(c.app.state.qf.settings, **kw)
    return c


def frames(body: str) -> list[dict[str, Any]]:
    out, cur = [], {}
    for line in body.splitlines():
        if line == "":
            if cur:
                out.append(cur)
            cur = {}
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(":")
        cur[k] = v.removeprefix(" ")
    if cur:
        out.append(cur)
    return out


def wait_status(c: Any, want: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = c.get("/api/market/live").json()
        if body["status"] == want:
            return body
        time.sleep(0.1)
    raise AssertionError(f"status stayed {body.get('status')!r}, wanted {want!r}")


# --------------------------------------------------------------------------- #
def test_off_by_default_in_tests_serves_stored_closes_without_network(client: Any) -> None:
    r = client.get("/api/market/live")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["status"] == "off"
    assert body["source"] == "Stored dataset"
    assert body["as_of"] == "2026-10-08"
    assert body["quotes"] == []  # the fake pipeline has no prices
    assert client.app.state.qf.live.running is False  # nothing started, nothing fetched

    s = client.get("/api/market/stream")  # one snapshot, then the stream ends (the client stops listening)
    assert s.status_code == 200 and s.headers["content-type"].startswith("text/event-stream")
    fr = frames(s.text)
    assert [f.get("event") for f in fr] == ["snapshot"]
    assert fr[0]["retry"] == "600000"
    assert json.loads(fr[0]["data"])["status"] == "off"


def test_settings_default_live_on_for_real_pipeline(monkeypatch: pytest.MonkeyPatch, data_dir: Any) -> None:
    from quantfolio.settings import load_settings

    assert load_settings().live_quotes is False  # fake pipeline
    monkeypatch.setenv("QF_PIPELINE_IMPL", "real")
    assert load_settings().live_quotes is True
    monkeypatch.setenv("QF_LIVE_QUOTES", "0")
    assert load_settings().live_quotes is False


def test_snapshot_then_live_ticks(app_client: Any) -> None:
    c = live_client(app_client)
    body = wait_status(c, "live")
    assert body["source"] == "Yahoo Finance"
    assert body["market_open"] is True
    assert body["as_of"] == "2026-10-09"
    q = {x["symbol"]: x for x in body["quotes"]}
    assert [x["symbol"] for x in body["quotes"]] == ["^NSEI", "^BSESN", "RELIANCE", "TCS"]  # indices first
    assert q["^NSEI"]["label"] == "NIFTY 50" and q["^NSEI"]["kind"] == "index"
    assert q["^BSESN"]["label"] == "SENSEX" and q["^BSESN"]["delayed"] is True
    rel = q["RELIANCE"]
    assert rel["kind"] == "stock" and rel["label"] == "RELIANCE" and rel["delayed"] is False
    assert rel["price"] > 100 and rel["prev_close"] == 98.0 and rel["at"] is not None
    assert rel["change"] == pytest.approx(rel["price"] - 98.0, abs=0.01)
    assert rel["change_pct"] == pytest.approx((rel["price"] - 98.0) / 98.0 * 100, abs=0.01)
    tcs = q["TCS"]  # no ticks: snapshot price
    assert tcs["price"] == 200.0 and tcs["change"] == 10.0 and tcs["change_pct"] == 5.26 and tcs["at"] is None
    up = c.app.state.qf.live._upstream
    assert up.symbols == ["^NSEI", "^BSESN", "RELIANCE.NS", "TCS.NS"]
    first = rel["price"]
    time.sleep(0.4)
    later = {x["symbol"]: x for x in c.get("/api/market/live").json()["quotes"]}
    assert later["RELIANCE"]["price"] > first


def test_stream_sends_snapshot_then_deltas_at_most_once_per_second(app_client: Any) -> None:
    c = live_client(app_client, max_s=3.2)
    t0 = time.monotonic()
    r = c.get("/api/market/stream")
    took = time.monotonic() - t0
    assert r.status_code == 200
    fr = [f for f in frames(r.text) if f.get("event") in ("snapshot", "delta")]
    assert fr[0]["event"] == "snapshot" and fr[0]["retry"] == "3000"
    assert all(f["event"] == "delta" for f in fr[1:])
    assert 2 <= len(fr) <= 5  # about one frame a second over ~3 s, never one per tick
    assert took < 8
    deltas = [json.loads(f["data"]) for f in fr[1:]]
    assert any(d["status"] == "live" for d in deltas)
    for d in deltas:  # only quotes that changed: the ticking ones, never the static TCS snapshot
        assert {q["symbol"] for q in d["quotes"]} <= {"RELIANCE", "^NSEI"}
    seqs = [json.loads(f["data"])["seq"] for f in fr]
    assert seqs == sorted(seqs)
    assert c.app.state.qf.live.viewers == 0  # the stream detached when it ended


def test_closed_outside_session_even_with_ticks(app_client: Any) -> None:
    c = live_client(app_client, clock=lambda: SATURDAY)
    body = wait_status(c, "closed")
    assert body["market_open"] is False
    assert body["note"]
    assert any(q["symbol"] == "RELIANCE" for q in body["quotes"])


def test_offline_falls_back_to_stored_closes_never_live(app_client: Any) -> None:
    c = live_client(app_client, seeder=no_yahoo, upstream=FakeUpstream(fail=10**6))
    body = wait_status(c, "offline")
    assert body["source"] == "Stored dataset"
    assert body["as_of"] == "2026-10-08"
    assert "unreachable" in body["note"].lower()
    q = {x["symbol"]: x for x in body["quotes"]}
    assert set(q) == {"RELIANCE", "TCS"}
    assert q["RELIANCE"]["price"] == 97.5 and q["RELIANCE"]["change"] == 1.5 and q["RELIANCE"]["at"] is None
    assert q["TCS"]["change"] is None


def test_delayed_and_holiday_status(make_settings: Any) -> None:
    s = make_settings(live_quotes=True)

    async def main() -> tuple[str, str]:
        quiet = FakeUpstream(fail=10**6)  # websocket never delivers
        a = service(s, upstream=quiet, connect_grace_s=0.0)
        a.touch()
        await a.wait_ready(3)
        delayed = a.status()  # market open, snapshots only
        await a.aclose()

        old = {k: SeedQuote(v.price, v.prev_close, date(2026, 10, 8)) for k, v in seed([]).items()}
        b = service(s, upstream=FakeUpstream(fail=10**6), seeder=lambda _: old, connect_grace_s=0.0)
        b.touch()
        await b.wait_ready(3)
        holiday = b.status()  # clock says open, but nothing traded today
        await b.aclose()
        return delayed, holiday

    assert asyncio.run(main()) == ("delayed", "closed")


def test_lazy_start_idle_stop_and_restart(make_settings: Any) -> None:
    s = make_settings(live_quotes=True, live_idle_s=1.0)

    async def main() -> None:
        live = service(s)
        assert not live.running
        live.attach()
        assert live.running and live.viewers == 1
        await live.wait_ready(3)
        await asyncio.sleep(1.5)
        assert live.running  # a viewer is attached
        live.detach()
        for _ in range(40):
            if not live.running:
                break
            await asyncio.sleep(0.1)
        assert not live.running  # stopped about idle_s after the last viewer left
        live.touch()
        assert live.running
        await live.aclose()
        assert not live.running
        live.touch()
        assert not live.running  # closed for good (app shutdown)

    asyncio.run(main())


def test_stream_reconnects_after_upstream_failure(make_settings: Any) -> None:
    s = make_settings(live_quotes=True)

    async def main() -> int:
        up = FakeUpstream(fail=1)
        live = service(s, upstream=up)
        live.touch()
        await live.wait_ready(3)
        for _ in range(60):
            if live.status() == "live":
                break
            await asyncio.sleep(0.1)
        status = live.status()
        await live.aclose()
        assert status == "live"
        return up.calls

    assert asyncio.run(main()) >= 2


def test_decode_tick() -> None:
    from yfinance.pricing_pb2 import PricingData

    msg = PricingData(id="RELIANCE.NS", price=1172.1, time=1791534616000, change=-5.9, market_hours=1)
    raw = json.dumps({"type": "pricing", "message": base64.b64encode(msg.SerializeToString()).decode()})
    t = decode_tick(raw)
    assert t is not None
    assert t.symbol == "RELIANCE.NS" and t.price == pytest.approx(1172.1, abs=1e-3) and t.change == pytest.approx(-5.9, abs=1e-3)
    assert t.market_hours == 1 and t.at is not None and t.at.year == 2026
    assert decode_tick("not json") is None
    assert decode_tick(json.dumps({"message": "!!!"})) is None
    assert decode_tick(json.dumps({"message": base64.b64encode(PricingData(id="X").SerializeToString()).decode()})) is None


@pytest.mark.parametrize(
    ("utc", "open_"),
    [
        (datetime(2026, 10, 9, 3, 44, tzinfo=UTC), False),  # 09:14 IST
        (datetime(2026, 10, 9, 3, 45, tzinfo=UTC), True),  # 09:15 IST
        (datetime(2026, 10, 9, 9, 59, tzinfo=UTC), True),  # 15:29 IST
        (datetime(2026, 10, 9, 10, 0, tzinfo=UTC), False),  # 15:30 IST
        (SATURDAY, False),
    ],
)
def test_session_clock(utc: datetime, open_: bool) -> None:
    assert in_session(utc) is open_


def test_openapi_lists_market_endpoints(client: Any) -> None:
    spec = client.get("/api/openapi.json").json()
    assert "/api/market/live" in spec["paths"] and "/api/market/stream" in spec["paths"]
    assert {"LiveQuote", "LiveQuotesResponse"} <= set(spec["components"]["schemas"])
