"""Fixtures for quantum-core tests.

Every test uses the deterministic SYNTHETIC dataset (label 'synthetic test'),
tiny universes and small shot/evaluation budgets, so the suite needs no
network, no IBM account and no downloaded market data.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[2]
PROJECT = BACKEND.parent
TMP_ROOT = PROJECT / ".tmp" / "core-tests"
sys.path.insert(0, str(BACKEND))

from quantfolio.contracts import (  # noqa: E402
    BaselineSettings,
    ConstraintsConfig,
    LotConfig,
    QaoaSettings,
    RunConfig,
    SectorCaps,
    StartingPortfolio,
    TransactionCosts,
)
from quantfolio.data.store import SYNTHETIC_ID  # noqa: E402
from quantfolio.presets import DEFAULT_DATES  # noqa: E402

SYM6 = ["HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "ITC"]  # 3 Financial Services, 2 IT, 1 FMCG
TINY_QAOA = QaoaSettings(depth=1, optimizer="COBYLA", init_strategy="random", shots=64, final_shots=256, max_evaluations=6, seed=3)
FAST_BASELINES = BaselineSettings(exact=True, relaxation_rounding=True, simulated_annealing=True, greedy=True, sa_sweeps=60, sa_restarts=2)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: takes more than ~10 s")


@pytest.fixture()
def tmp_dir() -> Iterator[Path]:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="t-", dir=TMP_ROOT))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make_config(
    symbols: list[str] | None = None,
    *,
    K: int | None = 3,
    mode: str = "selection",
    sector_caps: SectorCaps | None = None,
    target: float | None = None,
    lots: LotConfig | None = None,
    holdings: dict[str, float] | None = None,
    bps: float = 25.0,
    fixed_inr: float = 20.0,
    costs: bool = True,
    qaoa: QaoaSettings | None = None,
    **kw: Any,
) -> RunConfig:
    sp = StartingPortfolio(type="holdings", weights=holdings) if holdings else StartingPortfolio(type="cash")
    return RunConfig(
        symbols=list(symbols or SYM6),
        mode=mode,  # type: ignore[arg-type]
        risk_aversion=2.0,
        budget_inr=1_000_000,
        dates=DEFAULT_DATES,
        constraints=ConstraintsConfig(
            cardinality=K if mode == "selection" else None,
            sector_caps=sector_caps,
            target_return=target,
            lots=lots,
            transaction_costs=TransactionCosts(enabled=costs, proportional_bps=bps, fixed_inr_per_trade=fixed_inr, starting_portfolio=sp),
        ),
        qaoa=qaoa or TINY_QAOA,
        baselines=FAST_BASELINES,
        dataset_id=SYNTHETIC_ID,
        timeout_seconds=120,
        **kw,
    )


def problem_of(cfg: RunConfig):
    from quantfolio.assemble import assemble

    a = assemble(cfg)
    assert a.problem is not None, [i.message for i in a.errors]
    return a.problem


def all_bits(n: int) -> np.ndarray:
    return np.array(list(itertools.product([0, 1], repeat=n)), dtype=np.int64)


def median_feasible_return(cfg: RunConfig) -> float:
    """A target return that binds: the median expected return over portfolios
    that satisfy every other constraint."""
    p = problem_of(cfg)
    X = all_bits(p.enc.n_decision)
    X = X[p.feasible_mask(X)]
    rets = p.enc.weights(X) @ p.mu
    return float(np.round(np.median(rets), 6))


# Small problems covering every constraint and both encodings.
def case_configs() -> dict[str, RunConfig]:
    sel = make_config(SYM6, K=3, sector_caps=SectorCaps(basis="count", per_sector={"Financial Services": 1}), holdings={"HDFCBANK": 0.5, "TCS": 0.5})
    sel_w = make_config(SYM6, K=4, sector_caps=SectorCaps(basis="weight", per_sector={"Financial Services": 0.5}))
    lots_eq = make_config(SYM6[:4], mode="lots", lots=LotConfig(total_lots=4, max_lots_per_asset=2), sector_caps=SectorCaps(basis="weight", default_max=0.5), holdings={"SBIN": 0.25})
    lots_cash = make_config(["HDFCBANK", "ICICIBANK", "ITC"], mode="lots", lots=LotConfig(total_lots=4, max_lots_per_asset=3, allow_cash_remainder=True), sector_caps=SectorCaps(basis="weight", per_sector={"Financial Services": 0.5}))
    out = {"selection_count_cap": sel, "selection_weight_cap": sel_w, "lots_budget_eq": lots_eq, "lots_cash_remainder": lots_cash}
    # add a binding target return to every case
    for k, c in list(out.items()):
        t = median_feasible_return(c)
        out[k] = c.model_copy(update={"constraints": c.constraints.model_copy(update={"target_return": t})})
    return out


@pytest.fixture(scope="session")
def cases() -> dict[str, RunConfig]:
    return case_configs()
