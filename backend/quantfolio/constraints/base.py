"""Constraint interface.

Every constraint is a self-contained class: it validates portfolios exactly
(`residuals`, `feasible_mask`), describes itself (`explanation`), reports
preflight conflicts (`preflight_issues`) and exposes an *integer* linear form
over decision bits (`integer_forms`) that `qubo.py` turns into a penalty with
bounded slack. Adding a constraint never touches a solver: solvers only see
the QUBO (QAOA), or the shared `Problem.feasible_mask/violation/residuals`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

from ..contracts import ConstraintEncodingInfo, Issue, Residual
from ..portfolio import bounded_coefficients

if TYPE_CHECKING:  # pragma: no cover
    from ..portfolio import Problem

Sense = Literal["==", "<=", ">=", "at_most_one"]


@dataclass
class IntegerForm:
    """sum_j a_j * y_j  (sense)  rhs, with integer a_j over decision variables.

    lhs_min/lhs_max bound the left-hand side over the structurally feasible
    region (e.g. exactly K selected). They size the slack range, so every
    feasible point has a representable slack value and nothing is wasted."""

    name: str
    coeffs: dict[int, int]
    rhs: int
    sense: Sense
    lhs_min: int
    lhs_max: int
    unit: str = ""
    note: str = ""

    def slack_max(self) -> int:
        if self.sense == "<=":
            return max(0, self.rhs - self.lhs_min)
        if self.sense == ">=":
            return max(0, self.lhs_max - self.rhs)
        return 0

    def slack_coefficients(self) -> list[int]:
        return bounded_coefficients(self.slack_max())

    def lhs(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        idx = np.fromiter(self.coeffs.keys(), dtype=np.int64)
        a = np.fromiter(self.coeffs.values(), dtype=np.int64)
        return X[:, idx].astype(np.int64) @ a

    def satisfied(self, X: np.ndarray) -> np.ndarray:
        v = self.lhs(X)
        if self.sense == "==":
            return v == self.rhs
        if self.sense == "<=":
            return v <= self.rhs
        if self.sense == ">=":
            return v >= self.rhs
        return v <= 1


@dataclass
class Constraint(ABC):
    name: str = ""
    kind: str = ""
    notes: list[str] = field(default_factory=list)

    @abstractmethod
    def integer_forms(self, p: "Problem") -> list[IntegerForm]: ...

    @abstractmethod
    def residuals(self, p: "Problem", x: np.ndarray) -> list[Residual]: ...

    @abstractmethod
    def feasible_mask(self, p: "Problem", X: np.ndarray) -> np.ndarray: ...

    @abstractmethod
    def violation(self, p: "Problem", x: np.ndarray) -> float:
        """Violation in integer-like units: 0 iff satisfied, >= 1 otherwise."""

    @abstractmethod
    def explanation(self, p: "Problem") -> str: ...

    def preflight_issues(self, p: "Problem") -> list[Issue]:
        return []

    def is_objective_term(self) -> bool:
        return False

    def encoding_info(self, p: "Problem", penalty: float | None = None) -> ConstraintEncodingInfo:
        forms = self.integer_forms(p)
        slack = sum(len(f.slack_coefficients()) for f in forms)
        notes = [f.note for f in forms if f.note] + self.notes
        return ConstraintEncodingInfo(
            name=self.name,
            kind=self.kind,
            active=bool(forms) or self.is_objective_term(),
            explanation=self.explanation(p),
            slack_bits=slack,
            penalty_weight=penalty if forms else None,
            binding_note="; ".join(dict.fromkeys(notes)) or None,
        )


def at_least_one_unit(value: float) -> float:
    """Map a positive natural-unit violation to >= 1 (0 stays 0)."""
    return 0.0 if value <= 0 else 1.0 + float(value)
