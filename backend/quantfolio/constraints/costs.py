"""Transaction-cost objective term (not a feasibility constraint)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import Issue, Residual
from .base import Constraint, IntegerForm


@dataclass
class TransactionCostTerm(Constraint):
    def __post_init__(self) -> None:
        self.name = "transaction_costs"
        self.kind = "objective_term"

    def is_objective_term(self) -> bool:
        return True

    def integer_forms(self, p) -> list[IntegerForm]:
        return []

    def residuals(self, p, x) -> list[Residual]:
        return []

    def feasible_mask(self, p, X) -> np.ndarray:
        return np.ones(len(np.atleast_2d(X)), dtype=bool)

    def violation(self, p, x) -> float:
        return 0.0

    def explanation(self, p) -> str:
        if not p.costs_enabled:
            return "Transaction costs disabled."
        start = "a cash start (every holding is a purchase)" if p.start_type == "cash" else "the disclosed starting holdings"
        txt = (
            f"One-off cost from {start}: {p.cost_bps:g} bps of traded weight |w - w0| plus "
            f"INR {p.fixed_inr:,.0f} per traded asset (= {p.fixed_rate:.4%} of budget), charged against the one-year expected return."
        )
        if p.w0_outside:
            txt += f" Starting holdings outside the encoded set are sold: {', '.join(sorted(p.w0_outside))}."
        if p.cost_is_constant():
            txt += " With this start and structure the cost is the same for every feasible portfolio, so it does not change the selection."
        return txt

    def preflight_issues(self, p) -> list[Issue]:
        if p.costs_enabled and p.cost_is_constant() and (p.cost_bps > 0 or p.fixed_inr > 0):
            return [
                Issue(
                    severity="info",
                    code="transaction_cost_constant",
                    message="With a cash starting portfolio and a fixed number of holdings, the transaction cost is identical for every feasible portfolio: it shifts the objective but is not a selection trade-off.",
                    constraints=["transaction_costs"],
                    suggestions=["Use a disclosed holdings starting portfolio to make costs a real trade-off"],
                )
            ]
        return []
