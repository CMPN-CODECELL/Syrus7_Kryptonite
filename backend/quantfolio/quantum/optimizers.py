"""Classical outer-loop optimisers under a HARD evaluation budget.

Both optimisers minimise f(theta) = estimator(QUBO energies) * s, where s is
the circuit Hamiltonian scale (so step sizes are problem independent). The
returned parameters are those of the lowest estimate actually evaluated
(documented 'result_rule'); the budget is never exceeded: the wrapped
objective raises `BudgetExhausted` before evaluation number budget+1.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


class BudgetExhausted(Exception):
    pass


class DeadlineReached(Exception):
    pass


@dataclass
class Tracked:
    fun: Callable[[np.ndarray], float]
    budget: int
    deadline: float | None = None  # time.monotonic() value
    n: int = 0
    best_x: np.ndarray | None = None
    best_f: float = float("inf")
    history: list[float] = field(default_factory=list)
    stopped: str | None = None

    def __call__(self, x: np.ndarray) -> float:
        if self.n >= self.budget:
            self.stopped = "budget"
            raise BudgetExhausted
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.stopped = "deadline"
            raise DeadlineReached
        x = np.asarray(x, dtype=np.float64).copy()
        f = float(self.fun(x))
        self.n += 1
        self.history.append(f)
        if f < self.best_f:
            self.best_f, self.best_x = f, x
        return f


RESULT_RULE = "parameters of the lowest objective estimate evaluated during optimisation"


def cobyla(tr: Tracked, x0: np.ndarray, *, rhobeg: float = 0.3, tol: float = 1e-4) -> dict:
    from scipy.optimize import minimize

    settings = {"method": "COBYLA (scipy.optimize.minimize)", "rhobeg": rhobeg, "tol": tol, "maxfev": tr.budget, "result_rule": RESULT_RULE}
    try:
        res = minimize(tr, np.asarray(x0, dtype=np.float64), method="COBYLA", options={"maxiter": tr.budget, "rhobeg": rhobeg, "tol": tol})
        settings["scipy_message"] = str(res.message)[:200]
    except (BudgetExhausted, DeadlineReached):
        settings["scipy_message"] = f"stopped by {tr.stopped}"
    return settings


def spsa(
    tr: Tracked,
    x0: np.ndarray,
    *,
    seed: int,
    c0: float = 0.15,
    target_step: float = 0.2,
    alpha: float = 0.602,
    gamma: float = 0.101,
) -> dict:
    """SPSA (Spall 1998) with standard gain exponents. 2 evaluations per
    iteration. The learning-rate numerator a is calibrated from the FIRST
    iteration's own gradient estimate so that the first update has magnitude
    `target_step` (no extra evaluations)."""
    rng = np.random.default_rng(int(seed))
    iters = tr.budget // 2
    A = 0.1 * iters
    theta = np.asarray(x0, dtype=np.float64).copy()
    a = None
    settings = {
        "method": "SPSA (own implementation)",
        "iterations": iters,
        "c0": c0,
        "alpha": alpha,
        "gamma": gamma,
        "A": A,
        "target_first_step": target_step,
        "perturbation": "Rademacher +-1",
        "seed": int(seed),
        "result_rule": RESULT_RULE,
    }
    try:
        for k in range(iters):
            ck = c0 / (k + 1) ** gamma
            delta = rng.choice([-1.0, 1.0], size=theta.shape)
            fp = tr(theta + ck * delta)
            fm = tr(theta - ck * delta)
            g = (fp - fm) / (2.0 * ck) * delta
            if a is None:
                gm = float(np.mean(np.abs(g)))
                a = target_step * (A + 1) ** alpha / gm if gm > 1e-12 else target_step * (A + 1) ** alpha
                settings["a_calibrated"] = a
            ak = a / (k + 1 + A) ** alpha
            theta = theta - ak * g
    except (BudgetExhausted, DeadlineReached):
        pass
    settings["final_iterate"] = [float(v) for v in theta]
    return settings
