"""Exact baseline: exhaustive enumeration of every structurally valid
portfolio (exactly K assets, or every lot vector meeting the budget), exact
constraint check and exact economic objective (shared evaluator formulas).

Proves optimality, and proves infeasibility when nothing passes.
"""

from __future__ import annotations

import time

import numpy as np

from ..portfolio import Problem, count_candidates, enumerate_candidates
from .common import ClassicalOutcome, argmin_deterministic, batch_objective

EXACT_LIMIT = 1_200_000  # candidate portfolios (C(20,10) = 184,756; 8 assets x 0..4 lots = 390,625)
MAX_EXACT_ASSETS = 20
FRONTIER_SAMPLE = 400


def pareto_points(ret: np.ndarray, vol: np.ndarray) -> np.ndarray:
    """Indices of portfolios not dominated in (higher return, lower vol)."""
    order = np.lexsort((-ret, vol))
    keep, best = [], -np.inf
    for i in order:
        if ret[i] > best + 1e-15:
            keep.append(i)
            best = ret[i]
    return np.array(keep, dtype=np.int64)


def solve_exact(p: Problem) -> ClassicalOutcome:
    t0 = time.perf_counter()
    n_cand = count_candidates(p)
    if (p.mode == "selection" and p.n > MAX_EXACT_ASSETS) or n_cand > EXACT_LIMIT:
        return ClassicalOutcome(
            "exact",
            "unsupported",
            status_detail=f"{n_cand:,} candidate portfolios exceed the exhaustive limit ({EXACT_LIMIT:,}; selection mode up to {MAX_EXACT_ASSETS} assets)",
            work={"candidates": n_cand},
        )
    X = enumerate_candidates(p, EXACT_LIMIT)
    assert X is not None
    mask = p.feasible_mask(X)
    obj = batch_objective(p, X)
    nfeas = int(mask.sum())
    rt = {"total": round(time.perf_counter() - t0, 4)}
    work = {"candidates": int(len(X)), "feasible": nfeas}
    if nfeas == 0:
        return ClassicalOutcome(
            "exact",
            "infeasible",
            status_detail=f"exhaustive check: none of the {len(X):,} candidate portfolios satisfies every constraint",
            runtime=rt,
            work=work,
        )
    bi = argmin_deterministic(obj, X, mask)
    W = p.enc.weights(X[mask].astype(np.float64))
    ret = W @ p.mu
    vol = np.sqrt(np.clip(np.einsum("ij,jk,ik->i", W, p.sigma, W), 0, None))
    fo = obj[mask]
    par = pareto_points(ret, vol)
    rng = np.random.default_rng(0)
    sample = rng.choice(len(ret), size=min(FRONTIER_SAMPLE, len(ret)), replace=False)
    par_set = set(par.tolist())
    pts = sorted(par_set | set(sample.tolist()))
    extra = {
        "f_opt": float(obj[bi]),
        "f_worst_feasible": float(fo.max()),
        "f_range": float(fo.max() - fo.min()),
        "f_worst_all_candidates": float(obj.max()),
        "discrete_points": [(float(ret[i]), float(vol[i]), float(fo[i]), i in par_set) for i in pts],
        "objective_values_feasible": fo,
    }
    return ClassicalOutcome(
        "exact",
        "ok",
        x=X[bi].astype(np.int64),
        runtime=rt,
        work=work,
        notes=[f"exhaustive enumeration of {len(X):,} candidates ({nfeas:,} feasible); optimality proven"],
        extra=extra,
    )
