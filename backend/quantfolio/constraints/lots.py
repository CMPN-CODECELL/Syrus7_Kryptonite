"""Lots mode: per-asset lot bounds (by encoding) and the lot budget."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import Issue, Residual
from .base import Constraint, IntegerForm


@dataclass
class LotBudget(Constraint):
    def __post_init__(self) -> None:
        self.name = "lot_budget"
        self.kind = "equality_or_inequality"

    def _cfg(self, p) -> tuple[int, int, bool]:
        return int(p.enc.total_lots), int(p.enc.max_lots), bool(p.enc.allow_cash)

    def integer_forms(self, p) -> list[IntegerForm]:
        L, m, cash = self._cfg(p)
        coeffs = {k: int(p.enc.bit_coef[k]) for k in range(p.enc.n_decision)}
        if not cash:
            return [IntegerForm("lot_budget", coeffs, L, "==", 0, p.n * m, unit="lots", note=f"sum of lots = {L}")]
        if p.n * m <= L:
            return []  # never binding
        return [IntegerForm("lot_budget", coeffs, L, "<=", 0, p.n * m, unit="lots", note=f"sum of lots <= {L} (remainder held as cash)")]

    def residuals(self, p, x) -> list[Residual]:
        L, m, cash = self._cfg(p)
        units = p.enc.lots(x)
        tot = int(units.sum())
        ok = tot <= L if cash else tot == L
        out = [
            Residual(
                constraint="lot_budget",
                value=float(tot),
                limit=float(L),
                satisfied=ok,
                explanation=f"{tot} of {L} lots allocated ({'<=' if cash else '='} {L} required)",
            )
        ]
        over = int((units > m).sum())
        out.append(
            Residual(
                constraint="lot_bounds",
                value=float(units.max(initial=0)),
                limit=float(m),
                satisfied=over == 0 and int(units.min(initial=0)) >= 0,
                explanation=f"max lots in one asset {int(units.max(initial=0))} (bound {m}, guaranteed by the bounded encoding)",
            )
        )
        return out

    def feasible_mask(self, p, X) -> np.ndarray:
        L, m, cash = self._cfg(p)
        units = p.enc.lots(np.asarray(X))
        tot = units.sum(axis=1)
        ok = tot <= L if cash else tot == L
        return ok & (units <= m).all(axis=1)

    def violation(self, p, x) -> float:
        L, _, cash = self._cfg(p)
        tot = int(p.enc.lots(x).sum())
        return float(max(0, tot - L) if cash else abs(tot - L))

    def explanation(self, p) -> str:
        L, m, cash = self._cfg(p)
        from ..portfolio import bounded_coefficients

        coefs = bounded_coefficients(m)
        budget = f"sum n_i <= {L} with slack (cash remainder allowed)" if cash else f"sum n_i = {L} (fully invested)"
        return (
            f"Each asset holds n_i in [0, {m}] lots of 1/{L} of the budget, encoded with bits of value {coefs} "
            f"(bounded encoding: cannot exceed {m}). Budget: {budget}."
        )

    def preflight_issues(self, p) -> list[Issue]:
        L, m, cash = self._cfg(p)
        if not cash and p.n * m < L:
            return [
                Issue(
                    severity="error",
                    code="lot_budget_infeasible",
                    message=f"{p.n} assets x {m} lots = {p.n * m} < {L} total lots: the budget cannot be fully invested.",
                    constraints=["lot_budget"],
                    suggestions=["Raise max lots per asset", "Add assets", "Lower total lots", "Allow a cash remainder"],
                )
            ]
        return []
