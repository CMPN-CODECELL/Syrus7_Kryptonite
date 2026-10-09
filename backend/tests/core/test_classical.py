"""Classical baselines against brute force; rounding/repair validated by the shared evaluator."""

from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import all_bits, make_config, problem_of

from quantfolio.classical import compute_gap, solve_annealing, solve_exact, solve_greedy, solve_relaxation_rounding
from quantfolio.portfolio import evaluate
from quantfolio.qubo import build_qubo

CASES = ["selection_count_cap", "selection_weight_cap", "lots_budget_eq", "lots_cash_remainder"]


def _brute(p):
    X = all_bits(p.enc.n_decision)
    X = X[p.feasible_mask(X)]
    f = p.objective(p.enc.weights(X))
    return float(f.min()), float(f.max())


@pytest.mark.parametrize("name", CASES)
def test_exact_matches_brute_force(cases, name):
    p = problem_of(cases[name])
    f_opt, f_worst = _brute(p)
    ex = solve_exact(p)
    assert ex.status == "ok"
    assert abs(ex.extra["f_opt"] - f_opt) <= 1e-12
    assert abs(ex.extra["f_worst_feasible"] - f_worst) <= 1e-12
    pe = evaluate(p, ex.x)
    assert pe.feasibility.feasible and abs(pe.objective - f_opt) <= 1e-12


@pytest.mark.parametrize("name", CASES)
def test_heuristics_feasible_and_bounded_by_exact(cases, name):
    p = problem_of(cases[name])
    f_opt, _ = _brute(p)
    q = build_qubo(p)
    rr = solve_relaxation_rounding(p)
    sa = solve_annealing(p, sweeps=200, restarts=4, seed=1, penalty=q.penalty.weight)
    gr = solve_greedy(p)
    if rr.status == "ok":
        assert rr.extra["lower_bound"] <= f_opt + 1e-7  # continuous relaxation is a valid lower bound
    for out in (rr, sa, gr):
        assert out.status in ("ok", "no_feasible_found")
        if out.status == "ok":
            pe = evaluate(p, out.x)  # independent recomputation
            assert pe.feasibility.feasible, out.solver
            assert pe.objective >= f_opt - 1e-12
        else:
            assert out.x is None
    assert sa.status == "ok" and rr.status == "ok"


def test_infeasible_target_is_proven_by_exact_and_relaxation():
    p = problem_of(make_config(K=3, target=0.01))
    p_bad = problem_of(make_config(K=3, target=0.01))
    from quantfolio.constraints import TargetReturn

    tr = next(c for c in p_bad.constraints if isinstance(c, TargetReturn))
    tr.target = 4.0  # beyond every portfolio's expected return
    assert solve_exact(p_bad).status == "infeasible"
    assert solve_relaxation_rounding(p_bad).status == "infeasible"
    assert solve_exact(p).status == "ok"


def test_gap_normalisation_is_safe():
    g = compute_gap(-0.10, -0.12, "exact_optimum", "exact", 0.04)
    assert math.isclose(g.absolute, 0.02) and math.isclose(g.relative, 0.5)
    g0 = compute_gap(0.0, 0.0, "exact_optimum", "exact", 0.0)  # degenerate range and zero reference
    assert g0.absolute == 0.0 and math.isfinite(g0.relative)
    gn = compute_gap(0.3, 0.0, "best_known", "simulated_annealing", None)
    assert math.isfinite(gn.relative) and "unavailable" in gn.normalisation


def test_relaxation_rounding_repairs_to_feasible():
    p = problem_of(make_config(K=3, sector_caps=None, target=None))
    out = solve_relaxation_rounding(p)
    assert out.status == "ok"
    assert len(out.work["relaxed_weights"]) == p.n
    assert sum(out.work["rounded_units"]) == 3
    assert evaluate(p, out.x).feasibility.feasible
    assert np.isclose(sum(out.work["relaxed_weights"]), 1.0, atol=1e-5)
