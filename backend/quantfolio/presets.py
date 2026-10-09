"""Presets, the default example configuration and core capabilities/limits.

Preset timings are MEASURED, not promised: `scripts/experiments.py
--calibrate-presets` runs each preset on the default example several times and
writes `presets_calibration.json` next to this file (median / p95 wall time,
machine note). Until a calibration file exists the measured fields are None.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import (
    BaselineSettings,
    ConstraintsConfig,
    DateSplit,
    Preset,
    QaoaSettings,
    RunConfig,
    SectorCaps,
    StartingPortfolio,
    TransactionCosts,
)

CALIBRATION_FILE = Path(__file__).with_name("presets_calibration.json")

# --------------------------------------------------------------------------- #
# Resource limits (benchmarked; see docs/MATH.md "Resource limits" and
# evidence/experiments/qubit_benchmark.json)
# --------------------------------------------------------------------------- #
MAX_QUBITS = 24  # ideal statevector: 2^24 amplitudes x 16 B = 256 MiB per state
MAX_QUBITS_NOISE = 14  # noisy trajectories: one statevector simulation per shot
MAX_EXACT_ASSETS = 20
MAX_ENCODED_ASSETS = 20
MEMORY_LIMIT_MB = 1500.0
DEPTHS = [1, 2, 3, 4, 5]

DEFAULT_DATES = DateSplit(
    train_start=date(2022, 1, 3),
    train_end=date(2024, 12, 31),
    validation_start=date(2025, 1, 1),
    validation_end=date(2025, 9, 30),
    test_start=date(2025, 10, 1),
    test_end=date(2026, 10, 7),
)

DEFAULT_SYMBOLS = ["HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "ITC", "HINDUNILVR", "BHARTIARTL"]
DEFAULT_HOLDINGS = {"HDFCBANK": 0.30, "TCS": 0.30, "HINDUNILVR": 0.20, "INFY": 0.20}

_PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "title": "Quick",
        "description": "Depth-1 QAOA, COBYLA, small shot counts; for interactive exploration (target <= 30 s on the default example).",
        "max_assets": 10,
        "qaoa": QaoaSettings(depth=1, optimizer="COBYLA", init_strategy="random", shots=256, final_shots=1024, max_evaluations=25, seed=7),
        "baselines": BaselineSettings(exact=True, relaxation_rounding=True, simulated_annealing=True, greedy=False, sa_sweeps=300, sa_restarts=8),
        "timeout_seconds": 90,
    },
    "balanced": {
        "title": "Balanced",
        "description": "Depth-2 QAOA with INTERP warm start, more shots and evaluations.",
        "max_assets": 14,
        "qaoa": QaoaSettings(depth=2, optimizer="COBYLA", init_strategy="warm_interp", shots=512, final_shots=2048, max_evaluations=60, seed=7),
        "baselines": BaselineSettings(exact=True, relaxation_rounding=True, simulated_annealing=True, greedy=True, sa_sweeps=1000, sa_restarts=16),
        "timeout_seconds": 300,
    },
    "research": {
        "title": "Research",
        "description": "Depth-3 QAOA, INTERP warm start, large shot counts and budget; slow.",
        "max_assets": 16,
        "qaoa": QaoaSettings(depth=3, optimizer="COBYLA", init_strategy="warm_interp", shots=1024, final_shots=4096, max_evaluations=150, seed=7),
        "baselines": BaselineSettings(exact=True, relaxation_rounding=True, simulated_annealing=True, greedy=True, sa_sweeps=3000, sa_restarts=32),
        "timeout_seconds": 1200,
    },
}


@lru_cache(maxsize=1)
def _calibration() -> dict[str, Any]:
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_presets() -> list[Preset]:
    cal = _calibration().get("presets", {})
    out = []
    for name, spec in _PRESETS.items():
        c = cal.get(name) or {}
        note = None
        if c:
            note = f"median {c['median_s']:.1f} s, p95 {c['p95_s']:.1f} s over {c['repeats']} runs of the default example ({c.get('machine', 'this machine')}, {c.get('measured_at', '')})"
        out.append(
            Preset(
                name=name,  # type: ignore[arg-type]
                title=spec["title"],
                description=spec["description"],
                max_assets=spec["max_assets"],
                qaoa=spec["qaoa"],
                baselines=spec["baselines"],
                timeout_seconds=spec["timeout_seconds"],
                measured_median_s=c.get("median_s"),
                measured_note=note or "not yet measured on this machine",
            )
        )
    return out


def get_preset(name: str) -> Preset:
    for p in list_presets():
        if p.name == name:
            return p
    raise KeyError(name)


def apply_preset(cfg: RunConfig, name: str) -> RunConfig:
    p = get_preset(name)
    qa = p.qaoa.model_copy(update={"noise": cfg.qaoa.noise, "fake_backend": cfg.qaoa.fake_backend, "penalty_scale": cfg.qaoa.penalty_scale, "seed": cfg.qaoa.seed})
    return cfg.model_copy(update={"qaoa": qa, "baselines": p.baselines, "timeout_seconds": p.timeout_seconds, "preset": name})


def default_example_config() -> RunConfig:
    """~8 NIFTY 50 assets, K = 4, a sector cap, a binding target return,
    transaction costs from existing holdings, depth-1 COBYLA, fixed seed."""
    quick = _PRESETS["quick"]
    return RunConfig(
        symbols=list(DEFAULT_SYMBOLS),
        mode="selection",
        risk_aversion=2.0,
        budget_inr=1_000_000,
        risk_free_rate=0.065,
        dates=DEFAULT_DATES,
        constraints=ConstraintsConfig(
            cardinality=4,
            sector_caps=SectorCaps(basis="count", default_max=2, per_sector={"Financial Services": 1}),
            target_return=0.15,
            transaction_costs=TransactionCosts(
                enabled=True,
                proportional_bps=25.0,
                fixed_inr_per_trade=20.0,
                starting_portfolio=StartingPortfolio(type="holdings", weights=dict(DEFAULT_HOLDINGS)),
            ),
        ),
        qaoa=quick["qaoa"],
        baselines=quick["baselines"],
        preset="quick",
        noise_comparison=False,
        timeout_seconds=quick["timeout_seconds"],
    )


def core_capabilities() -> dict[str, Any]:
    from .constraints import SUPPORTED_BY_MODE
    from .quantum.noise import FAKE_BACKENDS
    from .quantum.warm_start import DESCRIPTIONS

    cal = _calibration()
    return {
        "max_qubits": MAX_QUBITS,
        "max_qubits_noise": MAX_QUBITS_NOISE,
        "max_exact_assets": MAX_EXACT_ASSETS,
        "depths": list(DEPTHS),
        "optimizers": ["COBYLA", "SPSA"],
        "init_strategies": dict(DESCRIPTIONS),
        "fake_backends": list(FAKE_BACKENDS),
        "modes": {k: list(v) for k, v in SUPPORTED_BY_MODE.items()},
        "limits": {
            "max_encoded_assets": MAX_ENCODED_ASSETS,
            "memory_limit_mb": MEMORY_LIMIT_MB,
            "max_shots": 8192,
            "max_final_shots": 16384,
            "max_evaluations": 2000,
            "qubit_caps_basis": cal.get("qubit_caps_basis", "statevector memory 16*2^n bytes; noisy runs simulate each shot (trajectories)"),
        },
    }
