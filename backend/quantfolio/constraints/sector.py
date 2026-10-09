"""Sector caps by count (selection mode) or by weight (both modes)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..contracts import Issue, Residual
from .base import Constraint, IntegerForm

EPS = 1e-9


@dataclass
class SectorCaps(Constraint):
    basis: str = "count"  # count | weight
    default_max: float | None = None
    per_sector: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = "sector_caps"
        self.kind = "inequality"

    # -------- helpers --------
    def caps(self, p) -> dict[str, float]:
        out: dict[str, float] = {}
        for sec in dict.fromkeys(p.sectors):
            cap = self.per_sector.get(sec, self.default_max)
            if cap is not None:
                out[sec] = float(cap)
        return out

    def _members(self, p, sec: str) -> list[int]:
        return [i for i, s in enumerate(p.sectors) if s == sec]

    def unit_limit(self, p, cap: float) -> int:
        """Cap expressed in integer encoding units (assets or lots)."""
        if p.mode == "selection":
            return math.floor(cap + EPS) if self.basis == "count" else math.floor(cap * p.enc.cardinality + EPS)
        return math.floor(cap * p.enc.total_lots + EPS)

    def _value(self, p, x: np.ndarray, members: list[int]) -> float:
        if self.basis == "count":
            return float(sum(1 for i in members if p.enc.lots(x)[i] > 0))
        return float(p.enc.weights(x)[members].sum())

    def _structural_max(self, p, members: list[int]) -> int:
        if p.mode == "selection":
            return min(len(members), int(p.enc.cardinality))
        return min(len(members) * int(p.enc.max_lots), int(p.enc.total_lots))

    # -------- interface --------
    def integer_forms(self, p) -> list[IntegerForm]:
        if p.mode == "lots" and self.basis == "count":
            return []  # unsupported; reported by preflight
        forms = []
        for sec, cap in self.caps(p).items():
            members = self._members(p, sec)
            limit = self.unit_limit(p, cap)
            smax = self._structural_max(p, members)
            name = f"sector_cap:{sec}"
            if limit >= smax:
                continue  # never binding
            coeffs: dict[int, int] = {}
            for i in members:
                for k in p.enc.bits_of(i):
                    coeffs[k] = int(p.enc.bit_coef[k])
            unit = "assets" if p.mode == "selection" else "lots"
            if p.mode == "selection" and limit == 1:
                forms.append(IntegerForm(name, coeffs, 1, "at_most_one", 0, smax, unit=unit, note=f"{sec}: at most one asset, pairwise penalty x_i x_j (no slack)"))
            else:
                forms.append(IntegerForm(name, coeffs, limit, "<=", 0, smax, unit=unit, note=f"{sec}: <= {limit} {unit}"))
        return forms

    def residuals(self, p, x) -> list[Residual]:
        out = []
        for sec, cap in self.caps(p).items():
            members = self._members(p, sec)
            v = self._value(p, x, members)
            what = ("asset" if v == 1 else "assets") if self.basis == "count" else "weight"
            out.append(
                Residual(
                    constraint=f"sector_cap:{sec}",
                    value=v,
                    limit=cap,
                    satisfied=v <= cap + EPS,
                    explanation=f"{sec}: {v:g} {what} held, cap {cap:g}",
                )
            )
        return out

    def feasible_mask(self, p, X) -> np.ndarray:
        X = np.asarray(X)
        mask = np.ones(len(X), dtype=bool)
        units = p.enc.lots(X)
        W = p.enc.weights(X)
        for sec, cap in self.caps(p).items():
            m = self._members(p, sec)
            v = (units[:, m] > 0).sum(axis=1) if self.basis == "count" else W[:, m].sum(axis=1)
            mask &= v <= cap + EPS
        return mask

    def violation(self, p, x) -> float:
        tot = 0.0
        units = p.enc.lots(x)
        for sec, cap in self.caps(p).items():
            m = self._members(p, sec)
            if self.basis == "count":
                over = (units[m] > 0).sum() - math.floor(cap + EPS)
            else:
                over = int(units[m].sum()) - self.unit_limit(p, cap)
            tot += max(0, over)
        return float(tot)

    def explanation(self, p) -> str:
        caps = self.caps(p)
        if not caps:
            return "No sector caps set."
        parts = ", ".join(f"{s} <= {c:g}" for s, c in caps.items())
        basis = "number of assets" if self.basis == "count" else "portfolio weight"
        enc = (
            "Caps of 1 asset use pairwise penalties; larger caps use P*(sum - cap + slack)^2 with bounded binary slack."
            if p.mode == "selection"
            else "Weight caps become integer lot limits floor(cap*L) with bounded slack."
        )
        return f"Sector caps on {basis}: {parts}. {enc}"

    def preflight_issues(self, p) -> list[Issue]:
        issues: list[Issue] = []
        known = set(p.sectors)
        unknown = [s for s in self.per_sector if s not in known]
        if unknown:
            issues.append(Issue(severity="warning", code="sector_cap_unknown_sector", message=f"Caps for sectors not among the encoded assets are ignored: {', '.join(unknown)}.", constraints=["sector_caps"]))
        if p.mode == "lots" and self.basis == "count":
            issues.append(
                Issue(
                    severity="error",
                    code="unsupported_constraint_for_mode",
                    message="Count-based sector caps are not supported in lots mode (the held/not-held indicator is not quadratic in lot bits).",
                    constraints=["sector_caps"],
                    suggestions=["Use basis 'weight' in lots mode", "Switch to selection mode"],
                )
            )
            return issues
        caps = self.caps(p)
        room = 0
        for sec in dict.fromkeys(p.sectors):
            members = self._members(p, sec)
            smax = self._structural_max(p, members)
            room += min(smax, self.unit_limit(p, caps[sec])) if sec in caps else smax
        need = int(p.enc.cardinality) if p.mode == "selection" else (0 if p.enc.allow_cash else int(p.enc.total_lots))
        if room < need:
            unit = "assets" if p.mode == "selection" else "lots"
            issues.append(
                Issue(
                    severity="error",
                    code="sector_caps_infeasible",
                    message=f"Sector caps allow at most {room} {unit} in total but {need} are required.",
                    constraints=["sector_caps", "cardinality" if p.mode == "selection" else "lot_budget"],
                    suggestions=["Raise the sector caps", "Add assets from other sectors", f"Lower {'K' if p.mode == 'selection' else 'total lots'}"],
                )
            )
        if self.basis == "weight" and p.mode == "selection":
            for sec, cap in caps.items():
                if 0 < cap * p.enc.cardinality < 1 - EPS:
                    issues.append(Issue(severity="info", code="sector_cap_excludes_sector", message=f"Weight cap {cap:g} on {sec} is below 1/K, so no {sec} asset can be held.", constraints=["sector_caps"]))
        return issues
