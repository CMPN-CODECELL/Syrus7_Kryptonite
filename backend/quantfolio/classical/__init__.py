"""Classical baselines. Every solver returns decision bits only; the shared
evaluator (`portfolio.evaluate`) recomputes and validates the portfolio."""

from .annealing import solve_annealing
from .common import ClassicalOutcome, compute_gap
from .exact import MAX_EXACT_ASSETS, solve_exact
from .greedy import solve_greedy
from .relaxation import solve_relaxation_rounding

__all__ = ["ClassicalOutcome", "MAX_EXACT_ASSETS", "compute_gap", "solve_annealing", "solve_exact", "solve_greedy", "solve_relaxation_rounding"]
