"""Continuous relaxation (CVXPY + CLARABEL) followed by rounding and repair.

Relaxation of the discrete feasible set (a superset of it):
  * 0 <= w_i <= 1/K (selection) or m/L (lots); sum w = 1 (or <= 1 with cash)
  * sector caps as weight caps: (unit limit) x (unit weight), which every
    discrete portfolio satisfies
  * target return mu'w >= R
Objective: q w'Sigma w - mu'w + (bps/1e4) * ||w - w0||_1 + outside-holding
liquidation; the fixed per-trade fee is non-convex and dropped, so (fees >= 0)
the optimal value is a valid LOWER BOUND on the discrete optimum, and
relaxation infeasibility proves the discrete problem infeasible.

Rounding: selection -> the K largest weights (ties: lower index); lots ->
largest-remainder rounding of w*L under the per-asset maximum. Then the shared
best-improvement local search repairs violations and improves the objective
with structure-preserving moves (swap / lot move).
"""

from __future__ import annotations

import time

import numpy as np

from ..portfolio import Problem
from .common import ClassicalOutcome, local_search


def relaxed_feasible_set(p: Problem, w, *, with_target: bool = True) -> list:
    """CVXPY constraints describing the relaxation (shared with frontier.py)."""
    import cvxpy as cp

    from ..constraints import SectorCaps, TargetReturn

    if p.mode == "selection":
        ub, budget_eq = 1.0 / float(p.enc.cardinality), True
    else:
        ub, budget_eq = float(p.enc.max_lots) / float(p.enc.total_lots), not p.enc.allow_cash
    cons = [w >= 0, w <= ub, (cp.sum(w) == 1) if budget_eq else (cp.sum(w) <= 1)]
    for c in p.constraints:
        if isinstance(c, SectorCaps):
            if p.mode == "lots" and c.basis == "count":
                continue
            for sec, cap in c.caps(p).items():
                members = [i for i, s in enumerate(p.sectors) if s == sec]
                cons.append(cp.sum(w[members]) <= c.unit_limit(p, cap) * p.enc.unit_weight() + 1e-12)
        elif isinstance(c, TargetReturn) and with_target:
            cons.append(p.mu @ w >= c.target)
    return cons


def solve_relaxation(p: Problem):
    """Returns (status, w or None, value or None, seconds, raw solver status)."""
    import cvxpy as cp

    t0 = time.perf_counter()
    w = cp.Variable(p.n)
    sigma = cp.psd_wrap(0.5 * (p.sigma + p.sigma.T))
    obj = p.risk_aversion * cp.quad_form(w, sigma) - p.mu @ w + p.outside_cost()
    if p.prop_rate > 0:
        obj = obj + p.prop_rate * cp.norm1(w - p.w0)
    prob = cp.Problem(cp.Minimize(obj), relaxed_feasible_set(p, w))
    try:
        prob.solve(solver=cp.CLARABEL)
    except cp.error.SolverError as exc:  # pragma: no cover - solver failure path
        return "failed", None, None, time.perf_counter() - t0, str(exc)[:200]
    dt = time.perf_counter() - t0
    st = prob.status
    if st in (cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE):
        return "infeasible", None, None, dt, st
    if w.value is None:
        return "failed", None, None, dt, st
    return "ok", np.clip(np.asarray(w.value, dtype=np.float64), 0, None), float(prob.value), dt, st


def round_weights(p: Problem, w: np.ndarray) -> np.ndarray:
    """Integer units per asset from relaxed weights (deterministic)."""
    n = p.n
    if p.mode == "selection":
        K = int(p.enc.cardinality)
        order = np.lexsort((np.arange(n), -np.round(w, 12)))
        u = np.zeros(n, dtype=np.int64)
        u[order[:K]] = 1
        return u
    m, L = int(p.enc.max_lots), int(p.enc.total_lots)
    scaled = w * L
    u = np.minimum(np.floor(scaled + 1e-9).astype(np.int64), m)
    target = L if not p.enc.allow_cash else min(L, int(round(float(scaled.sum()))))
    frac = scaled - u
    for i in np.lexsort((np.arange(n), -np.round(frac, 12))):
        if u.sum() >= target:
            break
        if u[i] < m:
            u[i] += 1
    while u.sum() < target:  # remaining lots on any asset with room
        room = np.nonzero(u < m)[0]
        if not len(room):
            break
        u[room[0]] += 1
    return u


def solve_relaxation_rounding(p: Problem) -> ClassicalOutcome:
    t0 = time.perf_counter()
    status, w, val, dt, raw = solve_relaxation(p)
    work = {"solver": "CLARABEL", "cvxpy_status": raw, "relaxation_seconds": round(dt, 4)}
    if status == "infeasible":
        return ClassicalOutcome(
            "relaxation_rounding",
            "infeasible",
            status_detail="continuous relaxation is infeasible, which proves the discrete problem is infeasible (the relaxation contains every discrete portfolio)",
            runtime={"total": round(time.perf_counter() - t0, 4)},
            work=work,
        )
    if status != "ok":
        return ClassicalOutcome("relaxation_rounding", "failed", status_detail=f"relaxation solver did not return a solution ({raw})", runtime={"total": round(time.perf_counter() - t0, 4)}, work=work)
    u0 = round_weights(p, w)
    x0 = p.enc.bits_from_units(u0)
    work.update(
        {
            "relaxed_objective_lower_bound": val,
            "relaxed_weights": [round(float(v), 6) for v in w],
            "rounded_units": u0.tolist(),
            "rounded_feasible": bool(p.feasible_mask(x0)[0]),
            "rounded_objective": float(p.objective(p.enc.weights(x0))),
        }
    )
    u, rounds = local_search(p, u0)
    x = p.enc.bits_from_units(u).astype(np.int64)
    work["repair_rounds"] = rounds
    feasible = bool(p.feasible_mask(x)[0])
    rt = {"relaxation": round(dt, 4), "total": round(time.perf_counter() - t0, 4)}
    notes = [
        f"relaxed optimum {val:.6g} is a lower bound on the discrete optimum (fixed per-trade fees dropped)",
        f"rounded portfolio {'feasible' if work['rounded_feasible'] else 'infeasible'} before repair; {rounds} local-search rounds",
    ]
    if not feasible:
        return ClassicalOutcome(
            "relaxation_rounding",
            "no_feasible_found",
            x=None,
            status_detail="rounding + local repair did not reach a feasible portfolio (not a proof of infeasibility)",
            runtime=rt,
            work=work,
            notes=notes,
        )
    return ClassicalOutcome("relaxation_rounding", "ok", x=x, runtime=rt, work=work, notes=notes, extra={"lower_bound": val})
