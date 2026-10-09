"""Simulated annealing baseline (vectorised over independent restarts).

State: integer units per asset (0/1 in selection mode, lots in lots mode).
Moves keep the structural constraint exactly: swap a held and a non-held
asset (selection) or move one lot between assets / add or remove one lot
when a cash remainder is allowed (lots). Other constraints (sector caps,
target return) enter as an indicator penalty lambda * [infeasible] with
lambda = the QUBO penalty weight (>= objective range), so the penalised
minimum is the constrained optimum. Energy = exact economic objective.

Temperature: geometric from T0 (median |delta f| of random moves, so ~60 %
of uphill moves are accepted at the start) down to T0 * 1e-3.
"""

from __future__ import annotations

import time

import numpy as np

from ..portfolio import Problem
from .common import ClassicalOutcome, local_search

T_END_RATIO = 1e-3


def _random_start(p: Problem, rng: np.random.Generator, R: int) -> np.ndarray:
    n = p.n
    U = np.zeros((R, n), dtype=np.int64)
    if p.mode == "selection":
        K = int(p.enc.cardinality)
        for r in range(R):
            U[r, rng.choice(n, size=K, replace=False)] = 1
        return U
    m, L = int(p.enc.max_lots), int(p.enc.total_lots)
    target = L if not p.enc.allow_cash else min(L, n * m)
    for r in range(R):
        left = target
        while left > 0:
            free = np.nonzero(U[r] < m)[0]
            if not len(free):
                break
            U[r, rng.choice(free)] += 1
            left -= 1
    return U


def _energy(p: Problem, U: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = p.enc.bits_from_units_batch(U)
    W = p.enc.weights(X.astype(np.float64))
    f = np.asarray(p.objective(W), dtype=np.float64)
    feas = p.feasible_mask(X)
    return f + lam * (~feas), f, feas


def _propose(p: Problem, U: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    R, n = U.shape
    V = U.copy()
    rows = np.arange(R)
    if p.mode == "selection":
        on = U > 0
        # pick a random held and a random non-held asset per chain
        ki = np.argmax(rng.random((R, n)) * on, axis=1)
        kj = np.argmax(rng.random((R, n)) * (~on), axis=1)
        ok = on[rows, ki] & ~on[rows, kj]
        V[rows[ok], ki[ok]] = 0
        V[rows[ok], kj[ok]] = 1
        return V
    m, L = int(p.enc.max_lots), int(p.enc.total_lots)
    move = rng.random(R)
    has = U > 0
    room = U < m
    i = np.argmax(rng.random((R, n)) * has, axis=1)
    j = np.argmax(rng.random((R, n)) * room, axis=1)
    if p.enc.allow_cash:
        tot = U.sum(axis=1)
        add = (move < 0.25) & room[rows, j] & (tot < L)
        rem = (move >= 0.25) & (move < 0.5) & has[rows, i]
        mv = (move >= 0.5) & has[rows, i] & room[rows, j] & (i != j)
        V[rows[add], j[add]] += 1
        V[rows[rem], i[rem]] -= 1
    else:
        mv = has[rows, i] & room[rows, j] & (i != j)
    V[rows[mv], i[mv]] -= 1
    V[rows[mv], j[mv]] += 1
    return V


def solve_annealing(p: Problem, *, sweeps: int, restarts: int, seed: int, penalty: float, deadline: float | None = None) -> ClassicalOutcome:
    t0 = time.perf_counter()
    rng = np.random.default_rng(int(seed))
    R = int(restarts)
    U = _random_start(p, rng, R)
    E, F, feas = _energy(p, U, penalty)
    # temperature calibration from random moves (not counted as sweeps)
    probe = _propose(p, U, rng)
    Ep, _, _ = _energy(p, probe, 0.0)
    dF = np.abs(Ep - F)
    dF = dF[dF > 1e-15]
    T0 = float(np.median(dF)) / np.log(1 / 0.6) if len(dF) else 1e-3
    T0 = max(T0, 1e-9)
    steps = int(sweeps) * max(1, p.n)
    ratios = T_END_RATIO ** (np.arange(steps) / max(1, steps - 1))
    best_f = np.where(feas, F, np.inf)
    best_U = U.copy()
    accepted = 0
    stopped = None
    for k in range(steps):
        if deadline is not None and (k & 255) == 0 and time.monotonic() > deadline:
            stopped = "deadline"
            break
        V = _propose(p, U, rng)
        Ev, Fv, fv = _energy(p, V, penalty)
        T = T0 * ratios[k]
        acc = (Ev <= E) | (rng.random(R) < np.exp(-np.clip((Ev - E) / T, 0, 700)))
        U[acc], E[acc], F[acc], feas[acc] = V[acc], Ev[acc], Fv[acc], fv[acc]
        accepted += int(acc.sum())
        better = feas & (F < best_f - 1e-15)
        best_f[better] = F[better]
        best_U[better] = U[better]
    work = {"sweeps": int(sweeps), "restarts": R, "moves": steps * R, "accepted": accepted, "T0": T0, "T_end": T0 * T_END_RATIO, "penalty_lambda": penalty, "seed": int(seed)}
    # deterministic polish of the best chain (counts as SA output, recorded)
    found = np.isfinite(best_f)
    if not found.any():
        rt = {"total": round(time.perf_counter() - t0, 4)}
        return ClassicalOutcome(
            "simulated_annealing",
            "time_limited" if stopped else "no_feasible_found",
            status_detail="no feasible portfolio visited by any chain (not a proof of infeasibility)",
            runtime=rt,
            work=work,
        )
    r = int(np.argmin(best_f))
    u, rounds = local_search(p, best_U[r])
    work["polish_rounds"] = rounds
    x = p.enc.bits_from_units(u).astype(np.int64)
    rt = {"total": round(time.perf_counter() - t0, 4)}
    status = "time_limited" if stopped else "ok"
    return ClassicalOutcome(
        "simulated_annealing",
        status,
        x=x,
        status_detail="stopped early by the run time limit" if stopped else None,
        runtime=rt,
        work=work,
        notes=[f"{R} chains x {steps} moves, geometric cooling T0={T0:.3g} -> {T0 * T_END_RATIO:.3g}; best chain polished by best-improvement local search"],
    )
