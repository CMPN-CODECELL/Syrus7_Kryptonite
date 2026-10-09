"""RunConfig -> dataset, training estimates, screening and the shared Problem.

Used by both `pipeline.preflight` (fast, no qiskit) and `run_optimisation`,
so the preflight report describes exactly the problem that will be solved.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .constraints import Constraint, build_constraints
from .contracts import Issue, RunConfig
from .data.store import Dataset, get_dataset
from .data.universe import load_universe
from .portfolio import Encoding, Problem, count_candidates, enumerate_candidates
from .risk import Estimate, estimate, usable_symbols
from .screening import screen

JOINT_CHECK_LIMIT = 20_000
PROVEN_INFEASIBLE = {
    "cardinality_exceeds_universe",
    "sector_caps_infeasible",
    "target_return_unreachable",
    "lot_budget_infeasible",
    "constraints_jointly_infeasible",
}
RESOURCE = {"too_many_qubits", "noise_too_many_qubits", "memory_limit"}

_est_lock = threading.Lock()
_est_cache: "OrderedDict[tuple, Estimate]" = OrderedDict()
_EST_CACHE_MAX = 32


@dataclass
class Assembled:
    config: RunConfig
    dataset: Dataset | None
    requested: list[str]
    usable: list[str] = field(default_factory=list)
    dropped: dict[str, str] = field(default_factory=dict)
    encoded: list[str] = field(default_factory=list)
    screened_out: list[str] = field(default_factory=list)
    screening: dict[str, Any] = field(default_factory=dict)
    estimate: Estimate | None = None
    problem: Problem | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def conflict_kind(self) -> str | None:
        errs = self.errors
        if not errs:
            return None
        if any(e.code in PROVEN_INFEASIBLE for e in errs):
            return "proven_infeasible"
        if all(e.code in RESOURCE for e in errs):
            return "resource_limit"
        return "preflight_conflict"


def _issue(sev: str, code: str, msg: str, **kw: Any) -> Issue:
    return Issue(severity=sev, code=code, message=msg, **kw)  # type: ignore[arg-type]


def cached_estimate(ds: Dataset, symbols: list[str], cfg: RunConfig) -> Estimate:
    d = cfg.dates
    key = (ds.info.dataset_id, ds.info.sha256, tuple(symbols), d.train_start, d.train_end)
    with _est_lock:
        hit = _est_cache.get(key)
        if hit is not None:
            _est_cache.move_to_end(key)
            return hit
    est = estimate(ds.prices, symbols, d)
    with _est_lock:
        _est_cache[key] = est
        while len(_est_cache) > _EST_CACHE_MAX:
            _est_cache.popitem(last=False)
    return est


def assemble(cfg: RunConfig, *, dataset: Dataset | None = None) -> Assembled:
    a = Assembled(config=cfg, dataset=None, requested=list(cfg.symbols))
    # ---------------- data ----------------
    if dataset is None:
        try:
            dataset = get_dataset(cfg.dataset_id)
        except KeyError:
            a.issues.append(_issue("error", "unknown_dataset", "The selected dataset does not exist on the server."))
            return a
    a.dataset = dataset
    ds = dataset
    if ds.info.stale:
        a.issues.append(_issue("warning", "stale_data", f"Dataset is a stale cache: {ds.info.stale_reason}"))
    if ds.info.label == "synthetic test":
        a.issues.append(_issue("warning", "synthetic_data", "Synthetic test data: results say nothing about real markets."))
    d = cfg.dates
    first, last = ds.info.first_date, ds.info.last_date
    if d.train_start < first or d.train_end > last:
        a.issues.append(
            _issue("error", "dates_outside_dataset", f"Training window {d.train_start}..{d.train_end} is outside the dataset range {first}..{last}.", suggestions=[f"Use dates within {first}..{last}"])
        )
        return a
    if d.validation_start > last:
        a.issues.append(_issue("warning", "validation_outside_dataset", f"Validation window starts after the last dataset date {last}; validation metrics will be unavailable."))
    if d.test_end > last:
        a.issues.append(_issue("info", "test_truncated", f"Test window ends after the last dataset date {last}; test metrics use data to {last}."))

    universe = load_universe()
    unknown = [s for s in cfg.symbols if s not in ds.prices.columns]
    if unknown:
        a.issues.append(_issue("error", "unknown_symbols", f"Not in the dataset: {', '.join(unknown)}.", suggestions=["Choose symbols from the asset list"]))
        return a
    usable, dropped = usable_symbols(ds.prices, list(cfg.symbols), d)
    a.usable, a.dropped = usable, dropped
    for s, why in dropped.items():
        a.issues.append(_issue("warning", "insufficient_history", f"{s} excluded: {why}.", suggestions=["Move the training start later", "Choose another asset"]))
    if len(usable) < 2:
        a.issues.append(_issue("error", "too_few_assets", "Fewer than two assets have enough training history."))
        return a

    # ---------------- estimation + screening (training data only) ----------------
    try:
        est_all = cached_estimate(ds, usable, cfg)
    except ValueError as exc:
        a.issues.append(_issue("error", "insufficient_training_data", str(exc), suggestions=["Use a longer training window"]))
        return a
    kept, out, report = screen(est_all, cfg.screening, cfg.risk_free_rate)
    a.encoded, a.screened_out, a.screening = kept, out, report
    est = est_all.subset(kept)
    a.estimate = est
    for note in est.notes:
        a.issues.append(_issue("warning", "estimation_note", note))

    # ---------------- encoding ----------------
    c = cfg.constraints
    n = len(kept)
    if cfg.mode == "selection":
        if c.cardinality is None:
            a.issues.append(_issue("error", "cardinality_required", "Selection mode needs a cardinality K (number of assets held).", constraints=["cardinality"], suggestions=["Set K, e.g. half the encoded assets"]))
            return a
        if c.lots is not None:
            a.issues.append(_issue("info", "lots_ignored", "Lot settings are ignored in selection mode."))
        if c.cardinality > n:
            a.issues.append(
                _issue("error", "cardinality_exceeds_universe", f"Cardinality K = {c.cardinality} exceeds the {n} encoded assets.", constraints=["cardinality"], suggestions=[f"Lower K to at most {n}", "Add more assets"])
            )
            return a
        enc = Encoding(mode="selection", n_assets=n, cardinality=int(c.cardinality))
    else:
        if c.lots is None:
            a.issues.append(_issue("error", "lots_required", "Lots mode needs lot settings (total lots, max lots per asset).", constraints=["lot_budget"]))
            return a
        if c.cardinality is not None:
            a.issues.append(
                _issue("error", "unsupported_constraint_for_mode", "Cardinality is not supported in lots mode (it would need held/not-held indicator bits).", constraints=["cardinality"], suggestions=["Remove K", "Switch to selection mode"])
            )
        enc = Encoding(mode="lots", n_assets=n, total_lots=c.lots.total_lots, max_lots=c.lots.max_lots_per_asset, allow_cash=c.lots.allow_cash_remainder)

    # ---------------- starting portfolio / costs ----------------
    tc = c.transaction_costs
    sp = tc.starting_portfolio
    w0 = np.zeros(n)
    outside: dict[str, float] = {}
    if sp.type == "holdings":
        bad = [s for s in sp.weights if s not in universe.symbols]
        neg = [s for s, w in sp.weights.items() if w < 0]
        tot = float(sum(sp.weights.values()))
        if bad:
            a.issues.append(_issue("error", "unknown_holding", f"Starting holdings reference unknown symbols: {', '.join(bad)}.", constraints=["transaction_costs"]))
        if neg:
            a.issues.append(_issue("error", "negative_holding", "Starting holding weights must be non-negative.", constraints=["transaction_costs"]))
        if tot > 1 + 1e-6:
            a.issues.append(_issue("error", "holdings_exceed_budget", f"Starting holding weights sum to {tot:.4f} > 1.", constraints=["transaction_costs"]))
        for s, w in sp.weights.items():
            if s in kept:
                w0[kept.index(s)] = float(w)
            elif w > 0:
                outside[s] = float(w)
        if outside:
            a.issues.append(
                _issue("info", "holdings_outside_encoding", f"Starting holdings not among the encoded assets are sold (constant cost): {', '.join(sorted(outside))}.", constraints=["transaction_costs"])
            )
    prices = {s: ds.price_for_shares(s, d.train_end) for s in kept}
    p = Problem(
        symbols=kept,
        sectors=[universe.sector_of(s) for s in kept],
        mu=est.mu,
        sigma=est.sigma,
        risk_aversion=float(cfg.risk_aversion),
        enc=enc,
        budget_inr=float(cfg.budget_inr),
        risk_free_rate=float(cfg.risk_free_rate),
        costs_enabled=bool(tc.enabled),
        cost_bps=float(tc.proportional_bps),
        fixed_inr=float(tc.fixed_inr_per_trade),
        start_type=sp.type,
        w0=w0,
        w0_outside=outside,
        prices={k: v for k, v in prices.items() if v is not None},
        train_end=d.train_end,
    )
    p.constraints = build_constraints(c, cfg.mode)
    a.problem = p
    for con in p.constraints:
        for iss in con.preflight_issues(p):
            if not any(i.code == iss.code and i.message == iss.message for i in a.issues):
                a.issues.append(iss)
    if cfg.qaoa.penalty_scale < 1:
        a.issues.append(_issue("warning", "penalty_scale_below_one", f"penalty_scale {cfg.qaoa.penalty_scale:g} < 1: infeasible bitstrings may have lower QUBO energy than the optimum."))

    # joint feasibility (exact, cheap when the structural region is small)
    if not a.errors and count_candidates(p) <= JOINT_CHECK_LIMIT:
        X = enumerate_candidates(p, JOINT_CHECK_LIMIT)
        if X is not None:
            nfeas = int(p.feasible_mask(X).sum())
            a.screening["feasible_portfolios"] = nfeas
            a.screening["structural_candidates"] = int(len(X))
            if nfeas == 0:
                a.issues.append(
                    _issue(
                        "error",
                        "constraints_jointly_infeasible",
                        f"None of the {len(X)} candidate portfolios satisfies all constraints together (checked exhaustively).",
                        constraints=[con.name for con in p.constraints if not con.is_objective_term()],
                        suggestions=["Relax the target return", "Raise sector caps", "Change K / lots", "Add assets"],
                    )
                )
    return a

