"""Greedy constructive baseline (optional).

Starts from an empty portfolio and adds one unit (an asset in selection mode,
one lot in lots mode) at a time, choosing the addition with the lowest
(constraint violation, economic objective), ties to the lower asset index.
Stops at K assets / L lots (or, with a cash remainder, when no addition
improves). No local search: this is the plain greedy heuristic.
"""

from __future__ import annotations

import time

import numpy as np

from ..portfolio import Problem
from .common import ClassicalOutcome, batch_objective


def solve_greedy(p: Problem) -> ClassicalOutcome:
    t0 = time.perf_counter()
    n = p.n
    u = np.zeros(n, dtype=np.int64)
    if p.mode == "selection":
        cap, per, target = 1, 1, int(p.enc.cardinality)
    else:
        cap, per, target = int(p.enc.max_lots), 1, int(p.enc.total_lots)
    steps = 0
    cur_key = None
    while int(u.sum()) < target:
        cands = [i for i in range(n) if u[i] + per <= cap]
        if not cands:
            break
        U = np.repeat(u[None, :], len(cands), axis=0)
        U[np.arange(len(cands)), cands] += per
        X = p.enc.bits_from_units_batch(U)
        viol = np.array([p.violation(x) for x in X])
        obj = batch_objective(p, X)
        j = int(np.lexsort((np.array(cands), obj, viol))[0])
        key = (float(viol[j]), float(obj[j]))
        if p.mode == "lots" and p.enc.allow_cash and cur_key is not None and key >= cur_key:
            break
        u, cur_key = U[j], key
        steps += 1
    x = p.enc.bits_from_units(u).astype(np.int64)
    rt = {"total": round(time.perf_counter() - t0, 4)}
    work = {"steps": steps, "units": u.tolist()}
    if not bool(p.feasible_mask(x)[0]):
        return ClassicalOutcome("greedy", "no_feasible_found", status_detail="greedy construction ended infeasible (not a proof of infeasibility)", runtime=rt, work=work)
    return ClassicalOutcome("greedy", "ok", x=x, runtime=rt, work=work, notes=[f"{steps} greedy additions"])
