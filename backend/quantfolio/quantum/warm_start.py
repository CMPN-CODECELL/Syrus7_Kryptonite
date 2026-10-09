"""Parameter initialisation strategies.

Parameter vector layout everywhere: [gamma_1..gamma_p, beta_1..beta_p].

random      gamma_l ~ U(0, pi), beta_l ~ U(0, pi/2), seeded.
warm_interp INTERP (Zhou et al., PRX 10, 021067, 2020): start at p = 1 from
            the linear-ramp (discretised annealing) point, optimise, then
            interpolate the optimum to depth p+1 and repeat until depth p-1;
            the depth-p optimisation starts from the interpolation of the
            depth-(p-1) optimum. Evaluations spent at shallower depths are
            'warm_start_preparation' evaluations and are reported separately
            from the depth-p evaluation budget.
"""

from __future__ import annotations

import numpy as np

RAMP_DT = 0.75


def random_init(depth: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(int(seed))
    g = rng.uniform(0.0, np.pi, depth)
    b = rng.uniform(0.0, np.pi / 2.0, depth)
    return np.concatenate([g, b])


def linear_ramp(depth: int, dt: float = RAMP_DT) -> np.ndarray:
    t = (np.arange(1, depth + 1) - 0.5) / depth
    return np.concatenate([t * dt, (1.0 - t) * dt])


def interp(params: np.ndarray, depth: int) -> np.ndarray:
    """INTERP: depth-p optimum -> depth-(p+1) initial point."""
    g, b = np.asarray(params[:depth]), np.asarray(params[depth:])

    def up(v: np.ndarray) -> np.ndarray:
        p = len(v)
        ext = np.concatenate([[0.0], v, [0.0]])  # [v]_0 = [v]_{p+1} = 0
        i = np.arange(1, p + 2)
        return (i - 1) / p * ext[i - 1] + (p - i + 1) / p * ext[i]

    return np.concatenate([up(g), up(b)])


def preparation_budget(max_evaluations: int, depth: int) -> int:
    """Per-level evaluation budget for the depths 1..p-1 (half the main budget in total)."""
    if depth <= 1:
        return 0
    return max(5, (max_evaluations // 2) // (depth - 1))


DESCRIPTIONS = {
    "random": "uniform random gamma in [0, pi), beta in [0, pi/2), seeded",
    "warm_interp": "INTERP warm start: linear-ramp p=1 start, optimise each shallower depth and interpolate (Zhou et al. 2020)",
}
