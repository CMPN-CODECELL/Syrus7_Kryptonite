"""Exact cardinality (selection mode): sum_i x_i = K."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import Issue, Residual
from .base import Constraint, IntegerForm


@dataclass
class Cardinality(Constraint):
    k: int = 0

    def __post_init__(self) -> None:
        self.name = "cardinality"
        self.kind = "equality"

    def integer_forms(self, p) -> list[IntegerForm]:
        return [IntegerForm("cardinality", {i: 1 for i in range(p.n)}, self.k, "==", 0, p.n, unit="assets")]

    def residuals(self, p, x) -> list[Residual]:
        cnt = int(np.asarray(x)[: p.n].sum())
        return [
            Residual(
                constraint="cardinality",
                value=float(cnt),
                limit=float(self.k),
                satisfied=cnt == self.k,
                explanation=f"{cnt} assets selected; exactly {self.k} required",
            )
        ]

    def feasible_mask(self, p, X) -> np.ndarray:
        return np.asarray(X)[:, : p.n].sum(axis=1) == self.k

    def violation(self, p, x) -> float:
        return float(abs(int(np.asarray(x)[: p.n].sum()) - self.k))

    def explanation(self, p) -> str:
        return (
            f"Hold exactly K = {self.k} of the {p.n} encoded assets, each at weight 1/{self.k}. "
            f"Penalty P*(sum x - {self.k})^2 is zero only at exactly {self.k} selections (no slack bits)."
        )

    def preflight_issues(self, p) -> list[Issue]:
        if self.k > p.n:
            return [
                Issue(
                    severity="error",
                    code="cardinality_exceeds_universe",
                    message=f"Cardinality K = {self.k} exceeds the {p.n} encoded assets.",
                    constraints=["cardinality"],
                    suggestions=[f"Lower K to at most {p.n}", "Add more assets"],
                )
            ]
        if self.k == p.n:
            return [Issue(severity="info", code="cardinality_trivial", message="K equals the number of assets: every asset is held.", constraints=["cardinality"])]
        return []
