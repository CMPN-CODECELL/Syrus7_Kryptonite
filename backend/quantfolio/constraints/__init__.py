"""Modular constraints. `build_constraints` is the only place that maps the
user-facing `ConstraintsConfig` onto constraint objects; solvers never look
at constraint types (they use the QUBO or `Problem.feasible_mask`)."""

from __future__ import annotations

from ..contracts import ConstraintsConfig
from .base import Constraint, IntegerForm
from .cardinality import Cardinality
from .costs import TransactionCostTerm
from .lots import LotBudget
from .sector import SectorCaps
from .target_return import TargetReturn

__all__ = [
    "Cardinality",
    "Constraint",
    "IntegerForm",
    "LotBudget",
    "SectorCaps",
    "TargetReturn",
    "TransactionCostTerm",
    "build_constraints",
    "SUPPORTED_BY_MODE",
]

SUPPORTED_BY_MODE: dict[str, list[str]] = {
    "selection": ["cardinality", "sector_caps(count|weight)", "target_return", "transaction_costs"],
    "lots": ["lot_budget", "lot_bounds", "sector_caps(weight)", "target_return", "transaction_costs"],
}


def build_constraints(cfg: ConstraintsConfig, mode: str) -> list[Constraint]:
    out: list[Constraint] = []
    if mode == "selection":
        if cfg.cardinality is not None:
            out.append(Cardinality(k=int(cfg.cardinality)))
    else:
        out.append(LotBudget())
    if cfg.sector_caps is not None and (cfg.sector_caps.default_max is not None or cfg.sector_caps.per_sector):
        sc = cfg.sector_caps
        out.append(SectorCaps(basis=sc.basis, default_max=sc.default_max, per_sector=dict(sc.per_sector)))
    if cfg.target_return is not None:
        out.append(TargetReturn(target=float(cfg.target_return)))
    out.append(TransactionCostTerm())
    return out
