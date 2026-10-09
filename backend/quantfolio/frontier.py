"""Efficient-frontier points, three distinct kinds (never mixed in one curve):

* continuous_relaxation — min w'Sigma w s.t. mu'w >= r over the RELAXED feasible
  set (box 0..1/K or m/L, budget, sector caps as weight caps; no target-return
  floor, no costs), r swept between the relaxed minimum-variance return and the
  maximum attainable return. This is a reference curve, not an achievable set
  of discrete portfolios.
* discrete_feasible — actual feasible discrete portfolios from exhaustive
  enumeration (all Pareto-efficient ones plus a fixed-seed sample).
* solver — the portfolio each solver returned.
"""

from __future__ import annotations

import numpy as np

from .contracts import FrontierPoint
from .portfolio import Problem

N_POINTS = 25
CONTINUOUS_LABEL = "relaxed continuous frontier (no costs, no target floor)"


def continuous_frontier(p: Problem, n_points: int = N_POINTS) -> list[FrontierPoint]:
    import cvxpy as cp

    from .classical.relaxation import relaxed_feasible_set

    w = cp.Variable(p.n)
    base = relaxed_feasible_set(p, w, with_target=False)
    sigma = cp.psd_wrap(0.5 * (p.sigma + p.sigma.T))
    lo = cp.Problem(cp.Minimize(cp.quad_form(w, sigma)), base)
    lo.solve(solver=cp.CLARABEL)
    if w.value is None:
        return []
    r_min = float(p.mu @ w.value)
    hi = cp.Problem(cp.Maximize(p.mu @ w), base)
    hi.solve(solver=cp.CLARABEL)
    if w.value is None:
        return []
    r_max = float(p.mu @ w.value)
    r = cp.Parameter()
    prob = cp.Problem(cp.Minimize(cp.quad_form(w, sigma)), base + [p.mu @ w >= r])
    out: list[FrontierPoint] = []
    for target in np.linspace(r_min, r_max, n_points):
        r.value = float(target) - 1e-10
        try:
            prob.solve(solver=cp.CLARABEL)
        except cp.error.SolverError:  # pragma: no cover
            continue
        if w.value is None:
            continue
        wv = np.asarray(w.value)
        out.append(
            FrontierPoint(
                expected_return=float(p.mu @ wv),
                volatility=float(np.sqrt(max(float(wv @ p.sigma @ wv), 0.0))),
                kind="continuous_relaxation",
                label=CONTINUOUS_LABEL,
            )
        )
    return out


def discrete_points(exact_extra: dict) -> list[FrontierPoint]:
    pts = exact_extra.get("discrete_points") or []
    return [
        FrontierPoint(expected_return=r, volatility=v, kind="discrete_feasible", objective=f, label="pareto" if par else None)
        for (r, v, f, par) in pts
    ]


def solver_point(name: str, expected_return: float, volatility: float, objective: float) -> FrontierPoint:
    return FrontierPoint(expected_return=expected_return, volatility=volatility, kind="solver", label=name, objective=objective)
