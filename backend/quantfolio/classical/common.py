"""Shared pieces for the classical baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..contracts import GapInfo
from ..portfolio import Problem


@dataclass
class ClassicalOutcome:
    solver: str
    status: str
    x: np.ndarray | None = None  # decision bits
    status_detail: str | None = None
    runtime: dict[str, float] = field(default_factory=dict)
    work: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def batch_objective(p: Problem, X: np.ndarray) -> np.ndarray:
    """Exact economic objective for many decision vectors."""
    return np.asarray(p.objective(p.enc.weights(np.asarray(X, dtype=np.float64))), dtype=np.float64)


def argmin_deterministic(obj: np.ndarray, X: np.ndarray, mask: np.ndarray | None = None) -> int | None:
    """Index of the lowest objective (ties -> lexicographically smallest bits)."""
    idx = np.arange(len(obj)) if mask is None else np.nonzero(mask)[0]
    if not len(idx):
        return None
    best = obj[idx].min()
    tied = idx[np.isclose(obj[idx], best, rtol=0, atol=1e-13)]
    if len(tied) == 1:
        return int(tied[0])
    keys = ["".join(map(str, X[i].astype(int))) for i in tied]
    return int(tied[int(np.argmin(np.array(keys)))])


def neighbours(p: Problem, units: np.ndarray) -> list[np.ndarray]:
    """Moves that keep the structural constraint: swap (selection) or move one
    lot between assets / add or remove a lot when cash is allowed (lots)."""
    out = []
    n = p.n
    if p.mode == "selection":
        on, off = np.nonzero(units > 0)[0], np.nonzero(units == 0)[0]
        for i in on:
            for j in off:
                u = units.copy()
                u[i], u[j] = 0, 1
                out.append(u)
        return out
    m, L = int(p.enc.max_lots), int(p.enc.total_lots)
    for i in range(n):
        for j in range(n):
            if i != j and units[i] > 0 and units[j] < m:
                u = units.copy()
                u[i] -= 1
                u[j] += 1
                out.append(u)
    if p.enc.allow_cash:
        tot = int(units.sum())
        for i in range(n):
            if units[i] > 0:
                u = units.copy()
                u[i] -= 1
                out.append(u)
            if units[i] < m and tot < L:
                u = units.copy()
                u[i] += 1
                out.append(u)
    return out


def local_search(p: Problem, units: np.ndarray, max_rounds: int = 200) -> tuple[np.ndarray, int]:
    """Best-improvement descent on (infeasible?, violation, objective):
    first restores feasibility (if possible), then improves the objective while
    staying feasible. Returns (units, rounds)."""

    def key(u: np.ndarray) -> tuple[float, float]:
        x = p.enc.bits_from_units(u)
        v = p.violation(x)
        return (v, float(p.objective(p.enc.weights(x))))

    cur = units.copy()
    ck = key(cur)
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        nb = neighbours(p, cur)
        if not nb:
            break
        X = p.enc.bits_from_units_batch(np.array(nb))
        viol = np.array([p.violation(x) for x in X])
        obj = batch_objective(p, X)
        order = np.lexsort((obj, viol))
        j = int(order[0])
        cand = (float(viol[j]), float(obj[j]))
        if cand < (ck[0], ck[1] - 1e-15):
            cur, ck = nb[j], cand
        else:
            break
    return cur, rounds


def compute_gap(f: float, f_ref: float, ref_kind: str, ref_solver: str, f_range: float | None) -> GapInfo:
    absolute = float(f - f_ref)
    if f_range is not None and f_range > 1e-12:
        rel, norm = absolute / f_range, "fraction of the feasible objective range (worst - best feasible, exact enumeration)"
    else:
        denom = max(abs(f_ref), 1e-9)
        rel, norm = absolute / denom, "relative to |reference objective| (feasible range unavailable)"
    return GapInfo(reference=ref_kind, reference_solver=ref_solver, absolute=absolute, relative=float(rel), normalisation=norm)  # type: ignore[arg-type]
