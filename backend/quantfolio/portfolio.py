"""Shared portfolio model and the independent evaluator.

One representation serves every solver:

* selection mode — decision bit x_i per asset, equal weight among the
  selected: w_i = x_i / K with exact cardinality sum(x) = K.
* lots mode — n_i lots per asset in [0, m] encoded with bounded binary
  bits (coefficients 1, 2, 4, ..., last chosen so the maximum is exactly m);
  w_i = n_i / L. Budget sum(n) = L, or sum(n) <= L with a cash remainder.

Economic objective (minimised, annual units, decimals):

    f(w) = q * w' Sigma w  -  mu' w  +  C(w)
    C(w) = (bps/1e4) * sum_i |w_i - w0_i|  +  (F / budget) * #{i : w_i != w0_i}
           + liquidation of start holdings outside the encoded set (constant)

C is a ONE-OFF cost charged against a one-year expected-return horizon, so
it is commensurate with the annual mu'w term (documented in docs/MATH.md).

`evaluate` recomputes everything from the decision bits only — weights,
objective, costs, constraint residuals, held-out metrics — and never trusts
numbers reported by a solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import TYPE_CHECKING

import numpy as np

from .contracts import Feasibility, Holding, PortfolioEval, Residual

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

    from .constraints.base import Constraint

TOL = 1e-9


def bounded_coefficients(max_value: int) -> list[int]:
    """Bounded binary encoding of an integer in [0, max_value]: 1, 2, 4, ...,
    with the last coefficient reduced so the sum is exactly max_value. Every
    integer in the range is representable and nothing above it is."""
    if max_value <= 0:
        return []
    b = int(max_value).bit_length()
    coefs = [1 << k for k in range(b - 1)]
    coefs.append(max_value - (sum(coefs)))
    return coefs


def canonical_bits(value: int, coefs: list[int]) -> list[int]:
    """Canonical bit pattern for `value` under `bounded_coefficients`: use the
    (reduced) last coefficient if value >= it, write the rest in binary."""
    if value < 0 or value > sum(coefs):
        raise ValueError("value out of range for encoding")
    bits = [0] * len(coefs)
    if not coefs:
        return bits
    if len(coefs) == 1:
        bits[0] = int(value)
        return bits
    rem = int(value)
    if rem >= coefs[-1]:
        bits[-1] = 1
        rem -= coefs[-1]
    for k in range(len(coefs) - 1):
        bits[k] = (rem >> k) & 1
    assert sum(c * b for c, b in zip(coefs, bits)) == value
    return bits


@dataclass
class Encoding:
    mode: str  # selection | lots
    n_assets: int
    cardinality: int | None = None
    total_lots: int | None = None
    max_lots: int | None = None
    allow_cash: bool = False
    bit_asset: np.ndarray = field(init=False)
    bit_coef: np.ndarray = field(init=False)
    A: np.ndarray = field(init=False)  # weights = A @ x

    def __post_init__(self) -> None:
        if self.mode == "selection":
            if not self.cardinality:
                raise ValueError("selection mode needs a cardinality K")
            self.bit_asset = np.arange(self.n_assets)
            self.bit_coef = np.ones(self.n_assets, dtype=np.int64)
            denom = float(self.cardinality)
        else:
            coefs = bounded_coefficients(int(self.max_lots or 0))
            self.bit_asset = np.repeat(np.arange(self.n_assets), len(coefs))
            self.bit_coef = np.tile(np.array(coefs, dtype=np.int64), self.n_assets)
            denom = float(self.total_lots or 1)
        n_dec = len(self.bit_asset)
        self.A = np.zeros((self.n_assets, n_dec))
        self.A[self.bit_asset, np.arange(n_dec)] = self.bit_coef / denom

    @property
    def n_decision(self) -> int:
        return int(len(self.bit_asset))

    @property
    def bits_per_asset(self) -> int:
        return self.n_decision // self.n_assets

    def bits_of(self, i: int) -> list[int]:
        return [int(k) for k in np.nonzero(self.bit_asset == i)[0]]

    def lots(self, x: np.ndarray) -> np.ndarray:
        """Integer units per asset: lots (lots mode) or 0/1 (selection)."""
        x = np.asarray(x)
        out = np.zeros(x.shape[:-1] + (self.n_assets,), dtype=np.int64)
        for i in range(self.n_assets):
            ks = self.bits_of(i)
            out[..., i] = x[..., ks] @ self.bit_coef[ks]
        return out

    def weights(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(x, dtype=np.float64) @ self.A.T

    def bits_from_units(self, units: np.ndarray) -> np.ndarray:
        """Canonical decision bits for integer units per asset."""
        x = np.zeros(self.n_decision, dtype=np.int8)
        if self.mode == "selection":
            x[:] = np.asarray(units, dtype=np.int8)
            return x
        return self.bits_from_units_batch(np.asarray(units)[None, :])[0]

    def bits_from_units_batch(self, units: np.ndarray) -> np.ndarray:
        units = np.asarray(units, dtype=np.int64)
        if self.mode == "selection":
            return units.astype(np.int8)
        coefs = bounded_coefficients(int(self.max_lots or 0))
        table = np.array([canonical_bits(v, coefs) for v in range(int(self.max_lots) + 1)], dtype=np.int8)
        return table[units].reshape(len(units), self.n_decision)  # asset-major, matches bit_asset

    def unit_weight(self) -> float:
        return 1.0 / float(self.cardinality if self.mode == "selection" else self.total_lots)


@dataclass
class Problem:
    symbols: list[str]
    sectors: list[str]
    mu: np.ndarray
    sigma: np.ndarray
    risk_aversion: float
    enc: Encoding
    budget_inr: float
    risk_free_rate: float
    costs_enabled: bool
    cost_bps: float
    fixed_inr: float
    start_type: str
    w0: np.ndarray  # starting weight per encoded asset
    w0_outside: dict[str, float]  # starting holdings not in the encoded set (sold)
    prices: dict[str, float]  # indicative price per symbol at train_end (unadjusted close)
    train_end: date | None = None
    constraints: list["Constraint"] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.symbols)

    @property
    def mode(self) -> str:
        return self.enc.mode

    # ---------------- costs ----------------
    @property
    def prop_rate(self) -> float:
        return self.cost_bps / 1e4 if self.costs_enabled else 0.0

    @property
    def fixed_rate(self) -> float:
        return self.fixed_inr / self.budget_inr if self.costs_enabled else 0.0

    def outside_cost(self) -> float:
        if not self.costs_enabled:
            return 0.0
        held = [v for v in self.w0_outside.values() if v > TOL]
        return self.prop_rate * sum(held) + self.fixed_rate * len(held)

    def asset_cost(self, i: int, w: np.ndarray | float) -> np.ndarray | float:
        d = np.abs(np.asarray(w, dtype=np.float64) - self.w0[i])
        return self.prop_rate * d + self.fixed_rate * (d > TOL)

    def cost(self, w: np.ndarray) -> np.ndarray | float:
        """One-off transaction cost as a fraction of budget; w shape (..., n)."""
        w = np.asarray(w, dtype=np.float64)
        d = np.abs(w - self.w0)
        c = self.prop_rate * d.sum(axis=-1) + self.fixed_rate * (d > TOL).sum(axis=-1)
        return c + self.outside_cost()

    def objective(self, w: np.ndarray) -> np.ndarray | float:
        w = np.asarray(w, dtype=np.float64)
        var = np.einsum("...i,ij,...j->...", w, self.sigma, w)
        return self.risk_aversion * var - w @ self.mu + self.cost(w)

    def cost_is_constant(self) -> bool:
        """True when C(w) cannot differ between feasible portfolios (no trade-off)."""
        if not self.costs_enabled or (self.cost_bps == 0 and self.fixed_inr == 0):
            return True
        if np.any(self.w0 > TOL):
            return False
        if self.mode == "selection":
            return True  # cash start, fixed K: turnover 1 and K trades always
        return (not self.enc.allow_cash) and self.fixed_inr == 0

    # ---------------- feasibility ----------------
    def residuals(self, x: np.ndarray) -> list[Residual]:
        x = np.asarray(x, dtype=np.int64)
        out: list[Residual] = []
        for c in self.constraints:
            out.extend(c.residuals(self, x))
        return out

    def feasible_mask(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=np.int64))
        mask = np.ones(len(X), dtype=bool)
        for c in self.constraints:
            mask &= c.feasible_mask(self, X)
        return mask

    def violation(self, x: np.ndarray) -> float:
        return float(sum(c.violation(self, np.asarray(x, dtype=np.int64)) for c in self.constraints))


# --------------------------------------------------------------------------- #
# Objective as a quadratic pseudo-Boolean function of the decision bits
# --------------------------------------------------------------------------- #
@dataclass
class QuadForm:
    const: float
    lin: np.ndarray  # (n,)
    quad: np.ndarray  # (n, n) strictly upper triangular
    max_cost_fit_error: float = 0.0

    def energy(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        return self.const + X @ self.lin + np.einsum("ij,ij->i", X @ self.quad, X)


def _mobius_quadratic(values: dict[tuple[int, ...], float], nbits: int) -> tuple[dict[tuple[int, ...], float], float]:
    """Exact multilinear (Moebius) coefficients of a function on `nbits` bits;
    if any term of order >= 3 is non-zero, return the least-squares quadratic fit
    and its max absolute error instead."""
    subsets = [s for r in range(nbits + 1) for s in combinations(range(nbits), r)]
    coef: dict[tuple[int, ...], float] = {}
    for s in subsets:
        acc = 0.0
        for r in range(len(s) + 1):
            for t in combinations(s, r):
                acc += (-1) ** (len(s) - len(t)) * values[t]
        coef[s] = acc
    if all(abs(v) < 1e-14 for s, v in coef.items() if len(s) >= 3):
        return {s: v for s, v in coef.items() if len(s) <= 2}, 0.0
    low = [s for s in subsets if len(s) <= 2]
    rows = []
    rhs = []
    for t in subsets:  # all assignments, identified by their set of ones
        rows.append([1.0 if set(s) <= set(t) else 0.0 for s in low])
        rhs.append(values[t])
    sol, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    err = float(np.max(np.abs(np.array(rows) @ sol - np.array(rhs))))
    return dict(zip(low, sol)), err


def objective_quadform(p: Problem) -> QuadForm:
    """f(w(x)) as const + lin·x + x'Ux. Exact for selection mode and for lots
    with <= 2 bits per asset; otherwise the per-asset cost term is the best
    quadratic least-squares fit (error reported; the evaluator stays exact)."""
    enc = p.enc
    n_dec = enc.n_decision
    M = p.risk_aversion * (enc.A.T @ p.sigma @ enc.A)
    lin = np.diag(M).copy() - enc.A.T @ p.mu
    quad = np.triu(2.0 * M, k=1)
    const = p.outside_cost()
    max_err = 0.0
    for i in range(p.n):
        ks = enc.bits_of(i)
        coefs = enc.bit_coef[ks]
        nb = len(ks)
        vals: dict[tuple[int, ...], float] = {}
        for r in range(nb + 1):
            for t in combinations(range(nb), r):
                units = int(sum(coefs[j] for j in t))
                vals[t] = float(p.asset_cost(i, units * enc.unit_weight()))
        terms, err = _mobius_quadratic(vals, nb)
        max_err = max(max_err, err)
        for s, v in terms.items():
            if len(s) == 0:
                const += v
            elif len(s) == 1:
                lin[ks[s[0]]] += v
            else:
                a, b = sorted((ks[s[0]], ks[s[1]]))
                quad[a, b] += v
    assert quad.shape == (n_dec, n_dec)
    return QuadForm(const=float(const), lin=lin, quad=quad, max_cost_fit_error=max_err)


# --------------------------------------------------------------------------- #
# Evaluator
# --------------------------------------------------------------------------- #
def decision_string(x: np.ndarray) -> str:
    """Decision bits as text, char i == decision variable i (asset order)."""
    return "".join("1" if int(v) else "0" for v in np.asarray(x).ravel())


def parse_decision_string(s: str) -> np.ndarray:
    return np.array([1 if ch == "1" else 0 for ch in s], dtype=np.int8)


def evaluate(
    p: Problem,
    x: np.ndarray,
    *,
    prices: "pd.DataFrame | None" = None,
    validation: tuple[date, date] | None = None,
    test: tuple[date, date] | None = None,
) -> PortfolioEval:
    """Independent recomputation of a portfolio from its decision bits."""
    from .risk import period_metrics

    x = np.asarray(x, dtype=np.int64)
    if x.shape != (p.enc.n_decision,):
        raise ValueError("decision vector has the wrong length")
    w = p.enc.weights(x)
    units = p.enc.lots(x)
    invested = float(w.sum())
    cash_w = max(0.0, 1.0 - invested)
    var = float(w @ p.sigma @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    exp_ret = float(w @ p.mu)
    cost = float(p.cost(w))
    obj = float(p.objective(w))
    holdings: list[Holding] = []
    remainder = 0.0
    sector_w: dict[str, float] = {}
    for i in np.nonzero(w > TOL)[0]:
        sym = p.symbols[i]
        amount = float(w[i] * p.budget_inr)
        price = p.prices.get(sym)
        shares = int(np.floor(amount / price)) if price and price > 0 else None
        if shares is not None:
            remainder += amount - shares * price
        holdings.append(
            Holding(
                symbol=sym,
                sector=p.sectors[i],
                weight=float(w[i]),
                amount_inr=round(amount, 2),
                indicative_shares=shares,
                price_inr=round(price, 4) if price else None,
                lots=int(units[i]) if p.mode == "lots" else None,
            )
        )
        sector_w[p.sectors[i]] = sector_w.get(p.sectors[i], 0.0) + float(w[i])
    residuals = p.residuals(x)
    feasible = all(r.satisfied for r in residuals)
    sharpe = (exp_ret - p.risk_free_rate * invested) / vol if vol > 1e-12 else None
    val_m = test_m = None
    if prices is not None:
        wd = {p.symbols[i]: float(w[i]) for i in range(p.n) if w[i] > TOL}
        if validation:
            val_m = period_metrics(prices, wd, validation[0], validation[1], risk_free_rate=p.risk_free_rate, cash_weight=cash_w, cost_fraction=cost)
        if test:
            test_m = period_metrics(prices, wd, test[0], test[1], risk_free_rate=p.risk_free_rate, cash_weight=cash_w, cost_fraction=cost)
    return PortfolioEval(
        holdings=holdings,
        cash_weight=cash_w,
        cash_inr=round(cash_w * p.budget_inr + remainder, 2),
        expected_return=exp_ret,
        variance=var,
        volatility=vol,
        cost_fraction=cost,
        cost_inr=round(cost * p.budget_inr, 2),
        net_expected_return=exp_ret - cost,
        objective=obj,
        sharpe_estimate=sharpe,
        feasibility=Feasibility(feasible=feasible, residuals=residuals),
        validation=val_m,
        test=test_m,
        sector_weights=sector_w,
    )


# --------------------------------------------------------------------------- #
# Enumeration helpers (exact baseline, frontier, penalty derivation, tests)
# --------------------------------------------------------------------------- #
def count_candidates(p: Problem) -> int:
    from math import comb

    if p.mode == "selection":
        return comb(p.n, int(p.enc.cardinality))
    return int((p.enc.max_lots + 1) ** p.n)


def enumerate_candidates(p: Problem, limit: int) -> np.ndarray | None:
    """All decision vectors respecting the structural equality (cardinality /
    lot budget) — in canonical encoding — or None if more than `limit`."""
    if count_candidates(p) > limit:
        return None
    if p.mode == "selection":
        K = int(p.enc.cardinality)
        combos = np.array(list(combinations(range(p.n), K)), dtype=np.int64)
        X = np.zeros((len(combos), p.n), dtype=np.int8)
        if len(combos):
            X[np.repeat(np.arange(len(combos)), K), combos.ravel()] = 1
        return X
    m, L = int(p.enc.max_lots), int(p.enc.total_lots)
    grids = np.stack(np.meshgrid(*[np.arange(m + 1)] * p.n, indexing="ij"), axis=-1).reshape(-1, p.n)
    tot = grids.sum(axis=1)
    keep = tot <= L if p.enc.allow_cash else tot == L
    return p.enc.bits_from_units_batch(grids[keep])
