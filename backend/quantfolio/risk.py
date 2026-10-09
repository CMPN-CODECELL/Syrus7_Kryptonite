"""Return/risk estimation on the TRAINING segment and held-out metrics.

Return definition: simple (arithmetic) daily returns r_t = P_t / P_{t-1} - 1
on adjusted closes. Simple returns aggregate linearly across assets
(r_p = sum_i w_i r_i), which is what the portfolio objective w'mu, w'Sigma w
assumes; log returns do not. Annualisation: mu = 252 * mean(r),
Sigma = 252 * Cov(r) (arithmetic, i.i.d. scaling).

Covariance: Ledoit-Wolf shrinkage (sklearn) towards a scaled identity, then
an eigenvalue floor if needed (PSD correction is recorded, never silent).

Leakage guards: every training statistic is computed from
``prices.loc[:train_end]`` only; validation/test prices are touched solely by
`period_metrics` for reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .contracts import DateSplit, EstimationInfo, PeriodMetrics, SegmentInfo
from .data.store import FFILL_LIMIT_DAYS, MAX_START_LAG_DAYS, MIN_TRAIN_COVERAGE

ANNUALISATION = 252
RETURN_TYPE = "simple daily returns on adjusted close (P_t/P_{t-1} - 1)"
MIN_TRAIN_OBS = 60
MIN_HELDOUT_OBS = 20
PSD_FLOOR = 1e-10


@dataclass
class Estimate:
    symbols: list[str]
    mu: np.ndarray  # annualised expected simple return
    sigma: np.ndarray  # annualised covariance (PSD)
    n_obs: int
    start: date
    end: date
    shrinkage: float | None
    psd_corrected: bool
    min_eig_before: float
    min_eig_after: float
    vol: np.ndarray = field(init=False)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.vol = np.sqrt(np.clip(np.diag(self.sigma), 0.0, None))

    def subset(self, symbols: list[str]) -> "Estimate":
        idx = [self.symbols.index(s) for s in symbols]
        out = Estimate(
            symbols=list(symbols),
            mu=self.mu[idx].copy(),
            sigma=self.sigma[np.ix_(idx, idx)].copy(),
            n_obs=self.n_obs,
            start=self.start,
            end=self.end,
            shrinkage=self.shrinkage,
            psd_corrected=self.psd_corrected,
            min_eig_before=self.min_eig_before,
            min_eig_after=self.min_eig_after,
            notes=list(self.notes),
        )
        return out


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


def training_window(prices: pd.DataFrame, split: DateSplit) -> pd.DataFrame:
    """Training-segment prices, forward-filled from the past only (limit FFILL_LIMIT_DAYS).
    Nothing after train_end is ever read."""
    past = prices.loc[: _ts(split.train_end)]
    filled = past.ffill(limit=FFILL_LIMIT_DAYS)
    win = filled.loc[_ts(split.train_start):]
    if len(win) and win.index.max() > _ts(split.train_end):  # pragma: no cover - guard
        raise AssertionError("training window leaked past train_end")
    return win


def usable_symbols(prices: pd.DataFrame, symbols: list[str], split: DateSplit) -> tuple[list[str], dict[str, str]]:
    """Split requested symbols into usable/dropped based on training coverage."""
    raw = prices.loc[_ts(split.train_start) : _ts(split.train_end)]
    n = len(raw)
    usable, dropped = [], {}
    for s in symbols:
        if s not in prices.columns:
            dropped[s] = "not present in the dataset"
            continue
        col = raw[s]
        if n == 0:
            dropped[s] = "no dataset dates in the training window"
            continue
        fv = col.first_valid_index()
        if fv is None:
            dropped[s] = "no prices in the training window"
            continue
        lag = int((raw.index < fv).sum())
        cov = float(col.notna().sum()) / n
        if lag > MAX_START_LAG_DAYS:
            dropped[s] = f"first price {fv.date().isoformat()} is {lag} trading days after the training start"
        elif cov < MIN_TRAIN_COVERAGE:
            dropped[s] = f"training coverage {cov:.0%} < {MIN_TRAIN_COVERAGE:.0%}"
        else:
            usable.append(s)
    return usable, dropped


def training_returns(prices: pd.DataFrame, symbols: list[str], split: DateSplit) -> pd.DataFrame:
    win = training_window(prices[symbols], split)
    rets = win.pct_change(fill_method=None).iloc[1:]
    rets = rets.dropna(how="any")  # complete cases only (late starts within the allowed lag)
    return rets


def estimate(prices: pd.DataFrame, symbols: list[str], split: DateSplit) -> Estimate:
    """Ledoit-Wolf covariance and mean returns from TRAINING data only."""
    from sklearn.covariance import LedoitWolf

    rets = training_returns(prices, symbols, split)
    if len(rets) < MIN_TRAIN_OBS:
        raise ValueError(f"only {len(rets)} complete training return observations (need >= {MIN_TRAIN_OBS})")
    r = rets.to_numpy(dtype=np.float64)
    mu = r.mean(axis=0) * ANNUALISATION
    lw = LedoitWolf(assume_centered=False).fit(r)
    sigma = np.asarray(lw.covariance_, dtype=np.float64) * ANNUALISATION
    sigma = 0.5 * (sigma + sigma.T)
    sigma, corrected, before, after = psd_correct(sigma)
    notes = []
    zero_var = [s for s, v in zip(symbols, np.diag(sigma)) if v < 1e-12]
    if zero_var:
        notes.append(f"near-zero variance in training data: {', '.join(zero_var)}")
    return Estimate(
        symbols=list(symbols),
        mu=mu,
        sigma=sigma,
        n_obs=len(rets),
        start=rets.index[0].date(),
        end=rets.index[-1].date(),
        shrinkage=float(lw.shrinkage_),
        psd_corrected=corrected,
        min_eig_before=before,
        min_eig_after=after,
        notes=notes,
    )


def psd_correct(sigma: np.ndarray, floor: float = PSD_FLOOR) -> tuple[np.ndarray, bool, float, float]:
    vals, vecs = np.linalg.eigh(sigma)
    before = float(vals.min())
    if before >= floor:
        return sigma, False, before, before
    vals = np.clip(vals, floor, None)
    fixed = (vecs * vals) @ vecs.T
    fixed = 0.5 * (fixed + fixed.T)
    return fixed, True, before, float(np.linalg.eigvalsh(fixed).min())


def segment_info(prices: pd.DataFrame, start: date, end: date) -> SegmentInfo:
    n = int(((prices.index >= _ts(start)) & (prices.index <= _ts(end))).sum())
    return SegmentInfo(start=start, end=end, n_obs=n)


def estimation_info(est: Estimate, prices: pd.DataFrame, split: DateSplit) -> EstimationInfo:
    notes = list(est.notes)
    notes.append(f"training returns used: {est.n_obs} complete daily observations {est.start} to {est.end}")
    return EstimationInfo(
        return_type=RETURN_TYPE,
        annualisation_days=ANNUALISATION,
        covariance_method="Ledoit-Wolf shrinkage (sklearn.covariance.LedoitWolf) of daily simple returns x 252",
        shrinkage_intensity=est.shrinkage,
        psd_corrected=est.psd_corrected,
        min_eigenvalue_before=est.min_eig_before,
        min_eigenvalue_after=est.min_eig_after,
        train=SegmentInfo(start=split.train_start, end=split.train_end, n_obs=est.n_obs),
        validation=segment_info(prices, split.validation_start, split.validation_end),
        test=segment_info(prices, split.test_start, split.test_end),
        notes=notes,
    )


def period_metrics(
    prices: pd.DataFrame,
    weights: dict[str, float],
    start: date,
    end: date,
    *,
    risk_free_rate: float,
    cash_weight: float = 0.0,
    cost_fraction: float = 0.0,
) -> PeriodMetrics:
    """Buy-and-hold metrics over [start, end] from the last close before `start`.
    Uses only prices up to `end`; gaps forward-filled from the past. Cash earns 0."""
    syms = [s for s, w in weights.items() if w > 0]
    if not syms and cash_weight <= 0:
        return PeriodMetrics(start=start, end=end, insufficient=True, note="empty portfolio")
    upto = prices.loc[: _ts(end), syms].ffill() if syms else prices.loc[: _ts(end)].iloc[:, :0]
    in_seg = upto.index >= _ts(start)
    first = int(np.argmax(in_seg)) if in_seg.any() else None
    if first is None:
        return PeriodMetrics(start=start, end=end, insufficient=True, note="no prices in this segment (beyond dataset end)")
    base_pos = first - 1 if first > 0 else first
    seg = upto.iloc[base_pos:]
    n_obs = len(seg) - 1
    seg_start, seg_end = upto.index[first].date(), seg.index[-1].date()
    if n_obs < MIN_HELDOUT_OBS:
        return PeriodMetrics(start=seg_start, end=seg_end, n_obs=max(n_obs, 0), insufficient=True, note=f"fewer than {MIN_HELDOUT_OBS} daily observations")
    if syms:
        base = seg.iloc[0]
        if base.isna().any():
            return PeriodMetrics(start=seg_start, end=seg_end, n_obs=n_obs, insufficient=True, note="missing base price for a holding")
        rel = seg / base
        w = np.array([weights[s] for s in syms])
        value = cash_weight + rel.to_numpy() @ w
    else:
        value = np.full(len(seg), cash_weight)
    value = np.asarray(value, dtype=np.float64)
    total = float(value[-1] / value[0] - 1.0)
    daily = value[1:] / value[:-1] - 1.0
    ann_ret = float((1.0 + total) ** (ANNUALISATION / n_obs) - 1.0) if total > -1 else -1.0
    vol = float(np.std(daily, ddof=1) * np.sqrt(ANNUALISATION))
    sharpe = None
    note = None
    if vol > 1e-12:
        sharpe = float((ann_ret - risk_free_rate) / vol)
    else:
        note = "zero volatility: Sharpe undefined"
    dd = float(np.min(value / np.maximum.accumulate(value) - 1.0))
    net = float((1.0 - cost_fraction) * (1.0 + total) - 1.0)
    return PeriodMetrics(
        start=seg_start,
        end=seg_end,
        n_obs=n_obs,
        total_return=total,
        annualised_return=ann_ret,
        annualised_volatility=vol,
        sharpe=sharpe,
        max_drawdown=dd,
        net_total_return=net,
        insufficient=False,
        note=note,
    )
