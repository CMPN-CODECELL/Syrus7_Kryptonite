"""Select the pipeline implementation (real quantum core or the test fake)."""

from __future__ import annotations

import hashlib
import json
from types import ModuleType
from typing import Any

from ..contracts import ExperimentConfig, RunConfig


def pipeline_module(impl: str) -> ModuleType:
    if impl == "fake":
        from . import fake_pipeline

        return fake_pipeline
    from .. import pipeline

    return pipeline


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def experiment_keys(mod: ModuleType, config: ExperimentConfig) -> tuple[str, str]:
    """(config_hash, cache_key) for an experiment: the base run's keys (which
    include dataset hash, seeds and implementation version) combined with the
    sweep definition."""
    base_hash, base_key = mod.cache_key(config.base)
    sweep = config.model_dump(mode="json")
    sweep.pop("base", None)
    sweep_s = canonical_json(sweep)
    return (
        hashlib.sha256(f"exp|{base_hash}|{sweep_s}".encode()).hexdigest(),
        hashlib.sha256(f"exp|{base_key}|{sweep_s}".encode()).hexdigest(),
    )


def experiment_cell_count(config: ExperimentConfig) -> int:
    """Upper bound on the number of QAOA runs an experiment will execute."""
    seeds = max(1, len(config.seeds))
    if config.design == "full_factorial":
        n = max(1, len(config.depths)) * max(1, len(config.optimizers)) * max(1, len(config.init_strategies))
        n *= max(1, len(config.universe_sizes))
        return n * seeds
    n = len(config.depths) + len(config.optimizers) + len(config.init_strategies) + len(config.universe_sizes)
    return max(1, n) * seeds


def run_summary(kind: str, result: Any) -> dict[str, Any]:
    """Small index row stored with the job (for /runs listings)."""
    if kind == "experiment":
        cfg: ExperimentConfig = result.config
        objs = [c.best_objective for c in result.cells if c.best_objective is not None]
        return {
            "n_symbols": len(cfg.base.symbols),
            "depth": None,
            "optimizer": None,
            "total_qubits": max((c.total_qubits for c in result.cells), default=None),
            "qaoa_status": None,
            "best_objective": min(objs) if objs else None,
            "n_cells": len(result.cells),
        }
    rcfg: RunConfig = result.config
    qaoa = next((s for s in result.solvers if s.solver == "qaoa"), None)
    return {
        "n_symbols": len(result.encoded_symbols) or len(rcfg.symbols),
        "total_qubits": result.qubo.n_variables if result.qubo else None,
        "depth": rcfg.qaoa.depth,
        "optimizer": rcfg.qaoa.optimizer,
        "qaoa_status": qaoa.status if qaoa else None,
        "best_objective": qaoa.portfolio.objective if qaoa and qaoa.portfolio else None,
    }
