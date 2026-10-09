"""Target (training) expected-return floor: mu'w >= R.

The QUBO needs integer coefficients for an exact slack encoding, so the
constraint is quantised: with N = K (selection) or L (lots), unit weight 1/N,
and integer units n_i,

    sum_i (mu_i - s) n_i >= N (R - s)          (s = min mu if sum n_i = N is enforced, else 0)
    a_i = round((mu_i - s) / delta),  rhs = ceil(N (R - s) / delta)
    sum_i a_i n_i - slack = rhs,  slack in [0, lhs_max - rhs]  (bounded binary)

The shift s keeps coefficients non-negative and small (it is exact whenever the
structural equality holds, which every feasible portfolio satisfies). delta
is refined (halving the slack resolution each step) until the quantised and
exact constraints agree on every structurally feasible portfolio (checked by
enumeration when there are <= ENUM_LIMIT of them), or the slack-bit cap is
reached; otherwise a worst-case error bound delta/2 (return units) is reported.
The evaluator always checks the exact constraint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..contracts import Issue, Residual
from .base import Constraint, IntegerForm, at_least_one_unit

MAX_SLACK_BITS = 8
ENUM_LIMIT = 20_000
TARGET_ERROR_BOUND = 0.001  # 0.1 %/yr when exhaustive agreement cannot be checked
TOL = 1e-12


def extreme(p, values: np.ndarray, maximize: bool) -> float:
    """max/min of sum_i values_i n_i over the structural region (exact, greedy)."""
    v = np.asarray(values, dtype=np.float64)
    order = np.argsort(-v if maximize else v, kind="stable")
    if p.mode == "selection":
        return float(v[order[: int(p.enc.cardinality)]].sum())
    L, m, cash = int(p.enc.total_lots), int(p.enc.max_lots), bool(p.enc.allow_cash)
    rem, tot = L, 0.0
    for i in order:
        if rem <= 0:
            break
        if cash and ((maximize and v[i] <= 0) or (not maximize and v[i] >= 0)):
            break
        take = min(m, rem)
        tot += take * v[i]
        rem -= take
    return float(tot)


@dataclass
class _Quant:
    delta: float
    shift: float
    a: np.ndarray
    rhs: int
    lhs_min: int
    lhs_max: int
    bits: int
    disagreements: int | None
    checked: int | None
    error_bound: float  # return units


@dataclass
class TargetReturn(Constraint):
    target: float = 0.0
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.name = "target_return"
        self.kind = "inequality"

    def _N(self, p) -> int:
        return int(p.enc.cardinality) if p.mode == "selection" else int(p.enc.total_lots)

    def _structural_equality(self, p) -> bool:
        return p.mode == "selection" or not p.enc.allow_cash

    def status(self, p) -> str:
        """'infeasible' | 'nonbinding' | 'binding' (exact, on the structural region)."""
        N = self._N(p)
        best = extreme(p, p.mu, True) / N
        worst = extreme(p, p.mu, False) / N
        if best < self.target - TOL:
            return "infeasible"
        if worst >= self.target - TOL:
            return "nonbinding"
        return "binding"

    def quantise(self, p) -> _Quant | None:
        e = p.enc
        key = (p.mu.tobytes(), e.mode, e.cardinality, e.total_lots, e.max_lots, e.allow_cash, float(self.target))
        if key in self._cache:
            return self._cache[key]
        if self.status(p) != "binding":
            self._cache[key] = None
            return None
        from ..portfolio import enumerate_candidates

        N = self._N(p)
        shift = float(np.min(p.mu)) if self._structural_equality(p) else 0.0
        u = p.mu - shift
        target_u = N * (self.target - shift)
        max_u = extreme(p, u, True)
        span = max(max_u - target_u, abs(max_u) * 1e-6, 1e-9)
        X = enumerate_candidates(p, ENUM_LIMIT)
        units = p.enc.lots(X) if X is not None else None
        exact_ok = (units @ p.mu) / N >= self.target - TOL if units is not None else None
        best: _Quant | None = None
        for bits in range(1, MAX_SLACK_BITS + 1):
            delta = span / (2**bits - 1)
            a = np.rint(u / delta).astype(np.int64)
            rhs = int(math.ceil(target_u / delta - 1e-9))
            lhs_max = int(round(extreme(p, a, True)))
            lhs_min = int(round(extreme(p, a, False)))
            if lhs_max < rhs:
                continue
            dis = int(((units @ a >= rhs) != exact_ok).sum()) if units is not None else None
            q = _Quant(delta, shift, a, rhs, lhs_min, lhs_max, int(max(0, lhs_max - rhs)).bit_length(), dis, len(units) if units is not None else None, delta / 2.0)
            best = q
            if (dis == 0) or (dis is None and delta / 2.0 <= TARGET_ERROR_BOUND):
                break
        self._cache[key] = best
        return best

    def integer_forms(self, p) -> list[IntegerForm]:
        q = self.quantise(p)
        if q is None:
            return []
        coeffs: dict[int, int] = {}
        for k in range(p.enc.n_decision):
            c = int(q.a[p.enc.bit_asset[k]]) * int(p.enc.bit_coef[k])
            if c != 0:
                coeffs[k] = c
        if q.disagreements is not None:
            acc = f"quantised vs exact disagree on {q.disagreements} of {q.checked} structurally feasible portfolios"
        else:
            acc = f"worst-case quantisation error <= {q.error_bound:.4%} return"
        N = self._N(p)
        note = f"delta = {q.delta / N:.5f} return units per step, shift s = {q.shift:.4f}; {acc}"
        return [IntegerForm("target_return", coeffs, q.rhs, ">=", q.lhs_min, q.lhs_max, unit=f"{q.delta:.6g} return-units", note=note)]

    def residuals(self, p, x) -> list[Residual]:
        r = float(p.enc.weights(x) @ p.mu)
        return [
            Residual(
                constraint="target_return",
                value=r,
                limit=self.target,
                satisfied=r >= self.target - TOL,
                explanation=f"expected (training) return {r:.2%} vs floor {self.target:.2%}",
            )
        ]

    def feasible_mask(self, p, X) -> np.ndarray:
        return p.enc.weights(np.asarray(X)) @ p.mu >= self.target - TOL

    def violation(self, p, x) -> float:
        r = float(p.enc.weights(x) @ p.mu)
        return at_least_one_unit((self.target - r) / 0.01)

    def explanation(self, p) -> str:
        st = self.status(p)
        base = f"Expected annualised training return mu'w must be at least {self.target:.2%}."
        if st == "nonbinding":
            return base + " Every structurally feasible portfolio already meets it, so it is not encoded."
        if st == "infeasible":
            return base + " No portfolio can reach it."
        return base + " Encoded as an integer inequality with bounded binary slack after quantising mu (see binding note)."

    def preflight_issues(self, p) -> list[Issue]:
        st = self.status(p)
        N = self._N(p)
        best = extreme(p, p.mu, True) / N
        if st == "infeasible":
            return [
                Issue(
                    severity="error",
                    code="target_return_unreachable",
                    message=f"Target return {self.target:.2%} exceeds the best achievable training expected return {best:.2%} for these assets and {'K' if p.mode == 'selection' else 'lot'} settings.",
                    constraints=["target_return"],
                    suggestions=[f"Lower the target to at most {math.floor(best * 1e4) / 1e4:.2%}", "Add higher-return assets", "Change K / lot settings"],
                )
            ]
        if st == "nonbinding":
            return [Issue(severity="info", code="target_return_nonbinding", message="The target return is met by every candidate portfolio; it adds no qubits.", constraints=["target_return"])]
        q = self.quantise(p)
        if q is not None and q.disagreements:
            return [
                Issue(
                    severity="warning",
                    code="target_return_quantisation",
                    message=f"Quantised target-return encoding disagrees with the exact constraint on {q.disagreements} of {q.checked} portfolios; the exact check is applied to every result.",
                    constraints=["target_return"],
                )
            ]
        return []
