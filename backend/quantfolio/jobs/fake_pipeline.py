"""Deterministic fake pipeline for platform tests and load tests ONLY.

Selected with `QF_PIPELINE_IMPL=fake`. It exercises the job mechanics
(progress events, cancellation, timeouts, crashes, artifacts) without qiskit.
Every result it produces is labelled `synthetic test` and carries a warning;
it is never used for anything shown as a genuine run.

Behaviour switches (by `config.qaoa.seed`):
    666 -> hard crash of the child process (os._exit)
    777 -> unexpected exception whose text contains a fake secret and a path
    888 -> uncooperative hang (never calls check_cancelled)
    999 -> PipelineError("infeasible_config", ...)
Step time per evaluation: env `QF_FAKE_STEP_S` (default 0.02 s).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts import (
    CircuitStats,
    DatasetInfo,
    EstimationInfo,
    ExperimentCell,
    ExperimentConfig,
    ExperimentResult,
    Feasibility,
    FinalBatch,
    FrontierPoint,
    Holding,
    Issue,
    PenaltyInfo,
    PeriodMetrics,
    PortfolioEval,
    PreflightReport,
    QaoaDetails,
    QuboInfo,
    Residual,
    RunConfig,
    RunResult,
    SampleRow,
    SegmentInfo,
    SolverResult,
    VariableInfo,
)
from ..pipeline import Cancelled, PipelineError, ProgressSink  # noqa: F401  (re-exported for tests)

FAKE_VERSION = "fake-pipeline-1"
FAKE_MAX_QUBITS = 20
CRASH_SEED, EXC_SEED, HANG_SEED, PIPELINE_ERROR_SEED = 666, 777, 888, 999


def _step_s() -> float:
    try:
        return max(0.0, float(os.environ.get("QF_FAKE_STEP_S", "0.02")))
    except ValueError:
        return 0.02


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _qubits(config: RunConfig) -> tuple[int, int]:
    n = len(config.symbols)
    if config.mode == "lots" and config.constraints.lots is not None:
        bits = max(1, int(config.constraints.lots.max_lots_per_asset).bit_length())
        return n * bits, 0
    return n, 0


def preflight(config: RunConfig) -> PreflightReport:
    decision, slack = _qubits(config)
    total = decision + slack
    issues: list[Issue] = []
    can_submit = total <= FAKE_MAX_QUBITS
    if not can_submit:
        issues.append(
            Issue(severity="error", code="too_many_qubits", message=f"{total} qubits exceeds the limit of {FAKE_MAX_QUBITS}",
                  suggestions=["Reduce the number of assets"])
        )
    k = config.constraints.cardinality
    if k is not None and k > len(config.symbols):
        can_submit = False
        issues.append(Issue(severity="error", code="cardinality_exceeds_universe",
                            message="Cardinality is larger than the number of assets", constraints=["cardinality"]))
    budget = config.qaoa.max_evaluations
    return PreflightReport(
        ok=can_submit and not issues,
        can_submit=can_submit,
        conflict_kind=None if can_submit else ("resource_limit" if total > FAKE_MAX_QUBITS else "preflight_conflict"),
        requested_symbols=list(config.symbols),
        encoded_symbols=list(config.symbols),
        portfolio_size=k,
        decision_bits=decision,
        slack_bits=slack,
        total_qubits=total,
        depth=config.qaoa.depth,
        shots=config.qaoa.shots,
        final_shots=config.qaoa.final_shots,
        evaluation_budget=budget,
        timeout_seconds=config.timeout_seconds,
        memory_estimate_mb=round((2**total) * 16 / 1e6, 3),
        estimated_runtime_s=round(budget * _step_s(), 3),
        max_qubits=FAKE_MAX_QUBITS,
        issues=issues,
        dataset_id="fake-synthetic",
        dataset_label="synthetic test",
    )


def cache_key(config: RunConfig) -> tuple[str, str]:
    cfg = config.model_dump(mode="json")
    config_hash = hashlib.sha256(_canonical(cfg).encode()).hexdigest()
    key = hashlib.sha256(_canonical({"cfg": cfg, "impl": FAKE_VERSION, "data": "fake-synthetic"}).encode()).hexdigest()
    return config_hash, key


def _dataset(n: int) -> DatasetInfo:
    return DatasetInfo(
        dataset_id="fake-synthetic",
        label="synthetic test",
        source="deterministic fake pipeline (platform tests only)",
        first_date=date(2019, 1, 1),
        last_date=date(2026, 10, 8),
        n_symbols=n,
        sha256="0" * 64,
        survivorship_note="Synthetic data; no survivorship semantics.",
        universe_source="test fixture",
        missing_data_policy="none (synthetic)",
    )


def _iterate(config: RunConfig, sink: ProgressSink, rng: random.Random, t0: float, offset: int = 0) -> list[dict[str, Any]]:
    seed = config.qaoa.seed
    points: list[dict[str, Any]] = []
    best: float | None = None
    step = _step_s()
    for i in range(1, config.qaoa.max_evaluations + 1):
        if seed == HANG_SEED:
            while True:  # uncooperative: ignores cancellation, only a hard kill stops it
                time.sleep(0.05)
        sink.check_cancelled()
        if step:
            time.sleep(step)
        energy = -1.0 + math.exp(-i / 8.0) + rng.uniform(-0.02, 0.02)
        obj = -0.1 - 0.05 * (1 - math.exp(-i / 6.0))
        best = obj if best is None else min(best, obj)
        point = {
            "evaluation": offset + i,
            "energy": round(energy, 9),
            "best_feasible_objective": round(best, 9),
            "feasible_fraction": round(min(1.0, 0.2 + i / (config.qaoa.max_evaluations + 1)), 6),
            "elapsed_s": round(time.perf_counter() - t0, 6),
            "phase": "optimisation",
        }
        points.append(point)
        sink.iteration(point)
        if seed == CRASH_SEED and i == max(1, config.qaoa.max_evaluations // 2):
            os._exit(3)
        if seed == EXC_SEED and i == 2:
            secret = os.environ.get("IBM_QUANTUM_TOKEN", "sk-test-secret-value")
            raise RuntimeError(f"boom while reading C:\\Users\\someone\\secret\\file.csv token={secret}")
    return points


def _portfolio(config: RunConfig, rng: random.Random) -> tuple[str, PortfolioEval, dict[str, str]]:
    n = len(config.symbols)
    k = config.constraints.cardinality or max(1, n // 2)
    k = min(k, n)
    mu = [rng.uniform(0.05, 0.25) for _ in range(n)]
    order = sorted(range(n), key=lambda i: -mu[i])[:k]
    sel = sorted(order)
    bitstring = "".join("1" if i in sel else "0" for i in range(n))
    sectors = {s: ("Sector A" if i % 2 == 0 else "Sector B") for i, s in enumerate(config.symbols)}
    w = 1.0 / k
    holdings = [
        Holding(symbol=config.symbols[i], sector=sectors[config.symbols[i]], weight=w, amount_inr=config.budget_inr * w,
                indicative_shares=int(config.budget_inr * w // 1000), price_inr=1000.0)
        for i in sel
    ]
    er = sum(mu[i] for i in sel) * w
    var = 0.04 / k + 0.01
    cost = config.constraints.transaction_costs.proportional_bps / 1e4 if config.constraints.transaction_costs.enabled else 0.0
    sw: dict[str, float] = {}
    for h in holdings:
        sw[h.sector] = sw.get(h.sector, 0.0) + h.weight
    feas = Feasibility(
        feasible=True,
        residuals=[Residual(constraint="cardinality", value=float(k), limit=float(k), satisfied=True, explanation=f"holds exactly {k} assets")],
    )
    pm = lambda s, e: PeriodMetrics(start=s, end=e, n_obs=100, total_return=0.05, annualised_return=0.12,  # noqa: E731
                                    annualised_volatility=0.2, sharpe=0.3, max_drawdown=-0.1)
    port = PortfolioEval(
        holdings=holdings,
        cash_weight=0.0,
        cash_inr=0.0,
        expected_return=er,
        variance=var,
        volatility=math.sqrt(var),
        cost_fraction=cost,
        cost_inr=cost * config.budget_inr,
        net_expected_return=er - cost,
        objective=config.risk_aversion * var - er + cost,
        sharpe_estimate=(er - config.risk_free_rate) / math.sqrt(var),
        feasibility=feas,
        validation=pm(config.dates.validation_start, config.dates.validation_end),
        test=pm(config.dates.test_start, config.dates.test_end),
        sector_weights=sw,
    )
    return bitstring, port, sectors


def run_optimisation(config: RunConfig, run_id: str, sink: ProgressSink, artifacts_dir: str) -> RunResult:
    t0 = time.perf_counter()
    rng = random.Random(config.qaoa.seed)
    if config.qaoa.seed == PIPELINE_ERROR_SEED:
        sink.stage("loading_data", "Loading synthetic data")
        raise PipelineError("infeasible_config", "The constraints cannot be satisfied together (fake pipeline test case).")
    for st, msg in (("loading_data", "Loading synthetic data"), ("estimating", "Estimating"), ("building_qubo", "Building QUBO"),
                    ("transpiling", "Transpiling")):
        sink.check_cancelled()
        sink.stage(st, msg)
    sink.stage("optimising", f"Optimising ({config.qaoa.max_evaluations} evaluations)")
    points = _iterate(config, sink, rng, t0)
    sink.stage("final_sampling", "Final sampling")
    n = len(config.symbols)
    bitstring, port, sectors = _portfolio(config, rng)
    shots = config.qaoa.final_shots
    counts: dict[str, int] = {}
    remaining = shots
    full_best = bitstring[::-1]  # Qiskit order: qubit 0 rightmost
    counts[full_best] = shots // 4
    remaining -= counts[full_best]
    while remaining > 0:
        b = "".join(rng.choice("01") for _ in range(n))
        c = min(remaining, rng.randint(1, max(1, shots // 20)))
        counts[b] = counts.get(b, 0) + c
        remaining -= c
    counts_json = json.dumps(dict(sorted(counts.items(), key=lambda kv: -kv[1])), sort_keys=False)
    Path(artifacts_dir, "final_counts.json").write_text(counts_json, encoding="utf-8")
    counts_sha = hashlib.sha256(counts_json.encode()).hexdigest()
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    rows = [
        SampleRow(bitstring=b, count=c, probability=c / shots, decision_bits=b[::-1], feasible=b[::-1].count("1") == bitstring.count("1"),
                  objective=port.objective if b == full_best else None, qubo_energy=-0.5 if b == full_best else 0.1)
        for b, c in top
    ]
    sink.stage("baselines", "Classical baselines")
    sink.check_cancelled()
    config_hash, key = cache_key(config)
    p = config.qaoa.depth
    variables = [VariableInfo(index=i, name=f"x_{i}", kind="decision", asset=s) for i, s in enumerate(config.symbols)]
    qaoa = QaoaDetails(
        depth=p,
        optimizer=config.qaoa.optimizer,
        optimizer_settings={"maxiter": config.qaoa.max_evaluations},
        init_strategy=config.qaoa.init_strategy,
        init_description="fake initial parameters",
        estimator=config.qaoa.estimator,
        cvar_alpha=config.qaoa.cvar_alpha if config.qaoa.estimator == "cvar" else None,
        primitive="fake",
        simulator_method="none (fake pipeline)",
        noise="ideal",
        circuit=CircuitStats(n_qubits=n, depth=4 * p + 2, size=n * (3 * p + 1), gate_counts={"rzz": p * n, "rx": p * n, "h": n},
                             two_qubit_gates=p * n, basis_gates=["rzz", "rx", "h"], transpile_seconds=0.0, optimization_level=1),
        initial_parameters=[0.1] * (2 * p),
        optimal_parameters=[round(0.3 + 0.01 * i, 6) for i in range(2 * p)],
        parameter_order=[f"gamma_{i}" for i in range(p)] + [f"beta_{i}" for i in range(p)],
        evaluations_used=len(points),
        evaluation_budget=config.qaoa.max_evaluations,
        iterations=points,
        final_batch=FinalBatch(shots=shots, seed=config.qaoa.seed, distinct_bitstrings=len(counts), feasible_probability=0.25,
                               selection_policy="lowest objective among feasible samples", best_bitstring=full_best,
                               top_samples=rows, counts_sha256=counts_sha),
        seeds={"optimizer": config.qaoa.seed, "simulator": config.qaoa.seed, "transpiler": config.qaoa.seed},
    )
    solvers = [
        SolverResult(solver="qaoa", status="ok", bitstring=bitstring, portfolio=port, runtime_s={"total": time.perf_counter() - t0},
                     work={"evaluations": len(points)}),
        SolverResult(solver="exact", status="ok", bitstring=bitstring, portfolio=port, runtime_s={"total": 0.001},
                     work={"enumerated": 2**n}),
    ]
    sink.stage("reporting", "Assembling result")
    seg = lambda s, e: SegmentInfo(start=s, end=e, n_obs=100)  # noqa: E731
    d = config.dates
    return RunResult(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        implementation_version=FAKE_VERSION,
        config=config,
        config_hash=config_hash,
        cache_key=key,
        dataset=_dataset(n),
        estimation=EstimationInfo(return_type="log", annualisation_days=252, covariance_method="sample (synthetic)",
                                  psd_corrected=False, min_eigenvalue_before=0.01, min_eigenvalue_after=0.01,
                                  train=seg(d.train_start, d.train_end), validation=seg(d.validation_start, d.validation_end),
                                  test=seg(d.test_start, d.test_end)),
        encoded_symbols=list(config.symbols),
        sectors=sectors,
        screening={"rule": "none"},
        qubo=QuboInfo(n_variables=n, n_decision=n, n_slack=0, offset=0.0, ising_offset=0.0, n_linear_terms=n,
                      n_quadratic_terms=n * (n - 1) // 2, max_abs_coefficient=1.0, scale=1.0,
                      penalties=[PenaltyInfo(constraint="cardinality", weight=2.0, derivation="fake")], variables=variables,
                      objective_definition="q*w'Σw - μ'w + cost (synthetic)"),
        qaoa=qaoa,
        solvers=solvers,
        frontier=[FrontierPoint(expected_return=0.1 + 0.01 * i, volatility=0.15 + 0.01 * i, kind="continuous_relaxation") for i in range(5)],
        timings_s={"total": time.perf_counter() - t0},
        software={"python": platform.python_version(), "pipeline": FAKE_VERSION, "platform": sys.platform},
        warnings=["FAKE PIPELINE: synthetic numbers for platform tests only."],
    )


def run_experiment(config: ExperimentConfig, run_id: str, sink: ProgressSink, artifacts_dir: str) -> ExperimentResult:
    t0 = time.perf_counter()
    base = config.base
    cells: list[ExperimentCell] = []
    sink.stage("experiment", "Running fake experiment cells")
    offset = 0
    for depth in config.depths:
        for seed in config.seeds:
            sink.check_cancelled()
            cfg = base.model_copy(update={"qaoa": base.qaoa.model_copy(update={"depth": depth, "seed": seed})})
            pts = _iterate(cfg, sink, random.Random(seed), t0, offset)
            offset += len(pts)
            cells.append(ExperimentCell(factor="depth", depth=depth, optimizer=base.qaoa.optimizer, init_strategy=base.qaoa.init_strategy,
                                        seed=seed, n_assets=len(base.symbols), total_qubits=len(base.symbols), status="ok",
                                        feasible_probability=0.25, best_objective=-0.15, exact_objective=-0.16, gap_abs=0.01,
                                        approximation_ratio=0.94, evaluations=len(pts), runtime_s=time.perf_counter() - t0))
    return ExperimentResult(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        implementation_version=FAKE_VERSION,
        config=config,
        dataset=_dataset(len(base.symbols)),
        cells=cells,
        summary=[{"factor": "depth", "level": d, "mean_gap": 0.01} for d in config.depths],
        selected_configuration={"depth": config.depths[0]},
        selection_rule="lowest mean validation gap (fake)",
        timings_s={"total": time.perf_counter() - t0},
        software={"python": platform.python_version(), "pipeline": FAKE_VERSION},
        warnings=["FAKE PIPELINE: synthetic numbers for platform tests only."],
    )
