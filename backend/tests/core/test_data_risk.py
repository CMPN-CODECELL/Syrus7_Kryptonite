"""Data layer and risk estimation: returns, annualisation, split isolation, PSD, missing data, uploads."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from conftest import SYM6, make_config

from quantfolio.assemble import assemble
from quantfolio.data.store import SYNTHETIC_ID, content_sha256, get_dataset
from quantfolio.data.upload import parse_price_csv, safe_display_name
from quantfolio.pipeline import PipelineError
from quantfolio.presets import DEFAULT_DATES
from quantfolio.risk import ANNUALISATION, estimate, period_metrics, psd_correct, training_returns, usable_symbols

SYMS = ["HDFCBANK", "TCS", "ITC"]
TRAIN_END = pd.Timestamp(DEFAULT_DATES.train_end)


@pytest.fixture(scope="module")
def prices() -> pd.DataFrame:
    return get_dataset(SYNTHETIC_ID).prices


def test_synthetic_fixture_is_labelled():
    ds = get_dataset(SYNTHETIC_ID)
    assert ds.info.label == "synthetic test" and "SYNTHETIC" in ds.info.source
    assert ds.info.sha256 == content_sha256(ds.prices, None)
    a = assemble(make_config())
    assert "synthetic_data" in {i.code for i in a.issues}


def test_simple_returns_annualised_by_252(prices):
    est = estimate(prices, SYMS, DEFAULT_DATES)
    win = prices.loc[pd.Timestamp(DEFAULT_DATES.train_start) : TRAIN_END, SYMS]
    r = (win / win.shift(1) - 1).iloc[1:].to_numpy()
    assert ANNUALISATION == 252
    np.testing.assert_allclose(est.mu, r.mean(axis=0) * 252, rtol=1e-12)
    assert not np.allclose(est.mu, np.log1p(r).mean(axis=0) * 252, rtol=1e-6)  # simple, not log, returns
    assert est.n_obs == len(r) and est.start >= DEFAULT_DATES.train_start and est.end <= DEFAULT_DATES.train_end
    # Ledoit-Wolf: (1 - d) S + d * tr(S)/n * I on daily returns, then x 252
    S = np.cov(r.T, bias=True)
    lw = (1 - est.shrinkage) * S + est.shrinkage * np.trace(S) / len(SYMS) * np.eye(len(SYMS))
    np.testing.assert_allclose(est.sigma, lw * 252, rtol=1e-9)
    np.testing.assert_allclose(est.vol, np.sqrt(np.diag(est.sigma)))


def test_covariance_symmetric_psd(prices):
    est = estimate(prices, SYM6, DEFAULT_DATES)
    assert np.array_equal(est.sigma, est.sigma.T)
    assert np.linalg.eigvalsh(est.sigma).min() > 0
    bad = np.array([[1.0, 2.0], [2.0, 1.0]])  # eigenvalues 3, -1
    fixed, corrected, before, after = psd_correct(bad)
    assert corrected and before < 0 < after
    assert np.array_equal(fixed, fixed.T) and np.linalg.eigvalsh(fixed).min() > 0


def test_training_estimates_ignore_everything_after_train_end(prices):
    base = estimate(prices, SYM6, DEFAULT_DATES)
    changed = prices.copy()
    rng = np.random.default_rng(1)
    after = changed.index > TRAIN_END
    changed.loc[after] = changed.loc[after] * rng.uniform(0.2, 5.0, size=(int(after.sum()), changed.shape[1]))
    est = estimate(changed, SYM6, DEFAULT_DATES)
    assert est.mu.tobytes() == base.mu.tobytes() and est.sigma.tobytes() == base.sigma.tobytes()
    assert training_returns(changed, SYM6, DEFAULT_DATES).index.max() <= TRAIN_END
    # the boundary is inclusive: the price ON train_end does matter
    edge = prices.copy()
    edge.loc[edge.index[edge.index <= TRAIN_END][-1], "TCS"] *= 1.5
    assert estimate(edge, SYM6, DEFAULT_DATES).mu.tobytes() != base.mu.tobytes()


def test_missing_data_policy(prices):
    base = estimate(prices, SYMS, DEFAULT_DATES)
    idx = prices.index[(prices.index >= pd.Timestamp(DEFAULT_DATES.train_start)) & (prices.index <= TRAIN_END)]

    short_gap = prices.copy()
    short_gap.loc[idx[100:103], "TCS"] = np.nan  # 3 days: forward-filled from the past
    assert estimate(short_gap, SYMS, DEFAULT_DATES).n_obs == base.n_obs

    long_gap = prices.copy()
    long_gap.loc[idx[100:108], "TCS"] = np.nan  # 8 days: only 5 are filled, the rest are dropped (complete cases)
    assert estimate(long_gap, SYMS, DEFAULT_DATES).n_obs == base.n_obs - 4

    late = prices.copy()
    late.loc[idx[:10], "ITC"] = np.nan  # listed 10 trading days after the training start
    usable, dropped = usable_symbols(late, SYMS, DEFAULT_DATES)
    assert usable == ["HDFCBANK", "TCS"] and "trading days after the training start" in dropped["ITC"]

    sparse = prices.copy()
    sparse.loc[idx[5::8], "ITC"] = np.nan  # 12.5 % missing < 95 % coverage
    usable, dropped = usable_symbols(sparse, SYMS, DEFAULT_DATES)
    assert "ITC" not in usable and "coverage" in dropped["ITC"]

    usable, dropped = usable_symbols(prices, SYMS + ["NOPE"], DEFAULT_DATES)
    assert dropped == {"NOPE": "not present in the dataset"}


def test_too_short_training_window_is_rejected(prices):
    from quantfolio.contracts import DateSplit

    short = DateSplit(train_start=date(2024, 11, 1), train_end=date(2024, 12, 31), validation_start=date(2025, 1, 1), validation_end=date(2025, 9, 30), test_start=date(2025, 10, 1), test_end=date(2026, 10, 7))
    with pytest.raises(ValueError, match="training return observations"):
        estimate(prices, SYMS, short)
    a = assemble(make_config().model_copy(update={"dates": short}))
    assert a.problem is None and "insufficient_training_data" in {i.code for i in a.errors}


def test_held_out_metrics_use_only_their_window(prices):
    w = {"HDFCBANK": 0.5, "TCS": 0.5}
    d = DEFAULT_DATES
    kw = dict(risk_free_rate=0.065, cash_weight=0.0, cost_fraction=0.01)
    m = period_metrics(prices, w, d.validation_start, d.validation_end, **kw)
    upto = prices.loc[: pd.Timestamp(d.validation_end), list(w)]
    first = int(np.argmax(upto.index >= pd.Timestamp(d.validation_start)))
    seg = upto.iloc[first - 1 :]  # bought at the last close before the segment, then held
    growth = (seg / seg.iloc[0]).to_numpy() @ np.array([0.5, 0.5])
    assert abs(m.total_return - (growth[-1] - 1)) < 1e-12
    assert abs(m.net_total_return - (0.99 * growth[-1] - 1)) < 1e-12  # one-off cost charged once
    assert m.start >= d.validation_start and m.end <= d.validation_end and m.n_obs == len(seg) - 1
    # prices outside [entry close, validation_end] cannot change the validation metrics
    other = prices.copy()
    outside = (other.index > pd.Timestamp(d.validation_end)) | (other.index < seg.index[0])
    other.loc[outside] = other.loc[outside] * 3.0
    assert period_metrics(other, w, d.validation_start, d.validation_end, **kw) == m
    far = period_metrics(prices, w, date(2030, 1, 1), date(2030, 6, 1), **kw)
    assert far.insufficient and far.total_return is None


def _csv(rows: int = 70, **override: str) -> bytes:
    d0 = date(2024, 1, 1)
    lines = ["Date,TCS,INFY"]
    for i in range(rows):
        lines.append(f"{(d0 + timedelta(days=i)).isoformat()},{100 + i * 0.5:.2f},{200 - i * 0.25:.2f}")
    for k, v in override.items():
        lines[int(k[1:])] = v
    return ("\n".join(lines) + "\n").encode()


def test_upload_accepts_plain_price_csv():
    frame, issues = parse_price_csv(_csv())
    assert list(frame.columns) == ["TCS", "INFY"] and len(frame) == 70 and frame.index.is_monotonic_increasing
    assert frame.dtypes.eq("float64").all() and issues == []
    assert safe_display_name("C:\\tmp\\..\\prices.csv") == "prices.csv"  # only the base name survives


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00\x01binary",
        "Date,TCS,INFY\n2024-01-01,1,2\n".encode("utf-16"),
        b"Day,TCS,INFY\n2024-01-01,1,2\n",
        _csv(rows=10),  # too few rows
        _csv(r5="2024-01-05,=1+1,5"),  # formula injection
        _csv(r5="2024-01-05,@SUM(A1),5"),
        _csv(r5="2024-01-05,NaN,5"),
        _csv(r5="2024-01-05,inf,5"),
        _csv(r5="2024-01-05,-3,5"),
        _csv(r5="2024-01-05,0,5"),
        _csv(r5="2024-01-05,1e99,5"),
        _csv(r5="2024-13-45,1,5"),
        _csv(r5="05/01/2024,1,5"),
        _csv(r5="2024-01-04,1,5"),  # duplicate date
        _csv(r5="2024-01-05,1"),  # ragged row
        _csv(r5="2999-01-05,1,5"),  # future date
        _csv(r0="Date,TCS,TCS"),
        _csv(r0="Date,TCS,../../etc/passwd"),
        _csv(r0="Date,NOTASYMBOL,ALSONOT"),
    ],
)
def test_upload_rejects_malformed_input(payload):
    with pytest.raises(PipelineError) as e:
        parse_price_csv(payload)
    assert e.value.code == "invalid_upload"


@pytest.mark.parametrize("name", ["prices.txt", "prices.csv.exe", "..csv", "a;rm -rf.csv", "<script>.csv", ""])
def test_upload_rejects_unsafe_file_names(name):
    with pytest.raises(PipelineError):
        safe_display_name(name)
