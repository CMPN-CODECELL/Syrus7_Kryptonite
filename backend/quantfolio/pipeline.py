"""Numerical pipeline entry points used by the job worker and the API.

Owned by the quantum-core workstream. The job platform calls ONLY these
functions; keep their signatures stable. Heavy imports (qiskit, cvxpy) must
stay inside function bodies so the API process remains light.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .contracts import ExperimentConfig, ExperimentResult, PreflightReport, RunConfig, RunResult


class Cancelled(Exception):
    """Raised by ProgressSink.check_cancelled() when the user cancelled the job."""


class PipelineError(Exception):
    """Expected, user-explainable failure. `code` is a stable machine code,
    `message` is safe to show (no paths, secrets or stack traces)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ProgressSink(Protocol):
    """Implemented by the job platform. Calls must be cheap; the platform
    coalesces/persists them. Only report measured values — never synthetic
    progress percentages."""

    def stage(self, stage: str, message: str = "", **data: Any) -> None: ...

    def iteration(self, point: dict[str, Any]) -> None:
        """One objective evaluation (fields of contracts.IterationPoint)."""

    def log(self, level: str, message: str, **data: Any) -> None: ...

    def check_cancelled(self) -> None:
        """Raise Cancelled if cancellation was requested. Call often (every evaluation)."""


class NullSink:
    def stage(self, stage: str, message: str = "", **data: Any) -> None:
        pass

    def iteration(self, point: dict[str, Any]) -> None:
        pass

    def log(self, level: str, message: str, **data: Any) -> None:
        pass

    def check_cancelled(self) -> None:
        pass


IMPLEMENTATION_VERSION = "0.1.0"

# Fraction of timeout_seconds QAOA optimisation may use before it stops early
# (status time_limited); the rest is reserved for the final batch and baselines.
QAOA_DEADLINE_FRACTION = 0.7
SOLVER_ORDER = ["qaoa", "exact", "relaxation_rounding", "simulated_annealing", "greedy"]
_SOFTWARE_PACKAGES = ["numpy", "scipy", "pandas", "scikit-learn", "cvxpy", "clarabel", "qiskit", "qiskit-aer", "qiskit-ibm-runtime", "pydantic", "pyarrow"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def software_versions() -> dict[str, str]:
    import platform
    from importlib import metadata

    out = {"python": platform.python_version(), "platform": platform.platform(terse=True), "quantfolio": IMPLEMENTATION_VERSION}
    for pkg in _SOFTWARE_PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _uses_noise(config: RunConfig) -> bool:
    return config.qaoa.noise == "fake_backend" or bool(config.noise_comparison)


def _fake_backend_name(config: RunConfig) -> str:
    from .quantum.noise import DEFAULT_FAKE_BACKEND

    return config.qaoa.fake_backend or DEFAULT_FAKE_BACKEND


def memory_estimate_mb(total_qubits: int, noisy: bool) -> float:
    """Process baseline + statevector (complex128) with ~3 working copies."""
    state = 16.0 * (2.0**total_qubits) / 2**20
    return round(350.0 + 3.0 * state + (50.0 if noisy else 0.0), 1)


def _eval_seconds(n: int, depth: int, shots: int, noisy: bool) -> float:
    """Per-evaluation wall-time model. Coefficients come from the benchmark
    stored in presets_calibration.json ("eval_model"); conservative defaults
    are used until it exists."""
    from .presets import _calibration

    m = {"c0": 0.02, "c_amp": 1.2e-8, "c_shot": 4e-6, "c_noisy": 1.5e-8}
    m.update(_calibration().get("eval_model") or {})
    work = depth * (2.0**n) * max(n, 1)
    if noisy:
        return m["c0"] + shots * m["c_noisy"] * work + m["c_shot"] * shots
    return m["c0"] + m["c_amp"] * work + m["c_shot"] * shots


def estimate_runtime_s(config: RunConfig, total_qubits: int) -> float:
    q = config.qaoa
    noisy = q.noise == "fake_backend"
    t = q.max_evaluations * _eval_seconds(total_qubits, q.depth, q.shots, noisy)
    if q.init_strategy == "warm_interp" and q.depth > 1:
        from .quantum.warm_start import preparation_budget

        b = preparation_budget(q.max_evaluations, q.depth)
        t += sum(b * _eval_seconds(total_qubits, d, q.shots, noisy) for d in range(1, q.depth))
    t += _eval_seconds(total_qubits, q.depth, q.final_shots, noisy) + 1.0
    if config.noise_comparison:
        t += 5.0 + config.noise_trials * (_eval_seconds(total_qubits, q.depth, q.final_shots, True) + _eval_seconds(total_qubits, q.depth, q.final_shots, False))
    b = config.baselines
    if b.simulated_annealing:
        t += 2e-5 * b.sa_sweeps * b.sa_restarts * max(1, len(config.symbols))
    return round(t, 1)


def _issue(sev: str, code: str, msg: str, **kw: Any):
    from .contracts import Issue

    return Issue(severity=sev, code=code, message=msg, **kw)  # type: ignore[arg-type]


def _conflict_kind(issues) -> str | None:
    from .assemble import PROVEN_INFEASIBLE, RESOURCE

    errs = [i for i in issues if i.severity == "error"]
    if not errs:
        return None
    if any(e.code in PROVEN_INFEASIBLE for e in errs):
        return "proven_infeasible"
    if all(e.code in RESOURCE for e in errs):
        return "resource_limit"
    return "preflight_conflict"


def _resource_issues(config: RunConfig, problem, total: int, n_dec: int) -> list:
    from .classical.exact import EXACT_LIMIT, MAX_EXACT_ASSETS
    from .portfolio import count_candidates
    from .presets import MAX_QUBITS, MAX_QUBITS_NOISE, MEMORY_LIMIT_MB
    from .quantum.noise import FAKE_BACKENDS

    out = []
    if total > MAX_QUBITS:
        out.append(
            _issue(
                "error",
                "too_many_qubits",
                f"The problem needs {total} qubits ({n_dec} decision + {total - n_dec} slack); the statevector simulator limit here is {MAX_QUBITS}.",
                suggestions=["Use screening (top_sharpe_train / min_variance_train) to encode fewer assets", "Remove or loosen inequality constraints (each adds slack qubits)", "Use fewer lots per asset"],
            )
        )
    noisy = _uses_noise(config)
    if noisy and total > MAX_QUBITS_NOISE:
        out.append(
            _issue(
                "error",
                "noise_too_many_qubits",
                f"Noisy simulation runs one statevector simulation per shot; {total} qubits exceeds the noisy limit of {MAX_QUBITS_NOISE}.",
                suggestions=["Disable noise and the noise comparison", f"Reduce the problem to at most {MAX_QUBITS_NOISE} qubits"],
            )
        )
    mem = memory_estimate_mb(total, noisy)
    if total <= MAX_QUBITS and mem > MEMORY_LIMIT_MB:
        out.append(_issue("error", "memory_limit", f"Estimated memory {mem:.0f} MB exceeds the {MEMORY_LIMIT_MB:.0f} MB limit."))
    if noisy and config.qaoa.fake_backend is not None and config.qaoa.fake_backend not in FAKE_BACKENDS:
        out.append(_issue("error", "unknown_fake_backend", f"Unknown fake backend; choose one of {', '.join(FAKE_BACKENDS)}."))
    if config.qaoa.noise == "fake_backend" and config.qaoa.init_strategy == "warm_interp" and config.qaoa.depth > 1:
        out.append(
            _issue(
                "error",
                "warm_interp_noise_unsupported",
                "INTERP warm start is not supported when the optimisation itself runs under noise.",
                suggestions=["Use init_strategy 'random'", "Optimise noise-free and enable the noise comparison instead"],
            )
        )
    if problem is not None and config.baselines.exact:
        nc = count_candidates(problem)
        if (problem.mode == "selection" and problem.n > MAX_EXACT_ASSETS) or nc > EXACT_LIMIT:
            out.append(_issue("info", "exact_unsupported", f"Exact enumeration will be skipped: {nc:,} candidate portfolios exceed the limit; gaps will be relative to the best known solution."))
    return out


# --------------------------------------------------------------------------- #
# preflight / cache key
# --------------------------------------------------------------------------- #
def preflight(config: RunConfig) -> PreflightReport:
    """Fast (target < 200 ms warm), no qiskit import. Validates against the
    dataset, applies training-only screening, counts decision/slack qubits,
    estimates memory/runtime and reports conflicts."""
    from .assemble import assemble
    from .presets import MAX_QUBITS
    from .qubo import count_qubits

    a = assemble(config)
    issues = list(a.issues)
    p = a.problem
    n_dec = n_slack = 0
    cons_info = []
    if p is not None:
        n_dec, n_slack, _ = count_qubits(p)
        cons_info = [c.encoding_info(p, None) for c in p.constraints]
    total = n_dec + n_slack
    if p is not None:
        issues.extend(_resource_issues(config, p, total, n_dec))
    kind = _conflict_kind(issues)
    ok = kind is None
    q = config.qaoa
    return PreflightReport(
        ok=ok,
        can_submit=ok,
        conflict_kind=kind,  # type: ignore[arg-type]
        requested_symbols=list(config.symbols),
        encoded_symbols=list(a.encoded),
        screened_out=list(a.screened_out),
        screening_rule=config.screening.rule,
        portfolio_size=config.constraints.cardinality if config.mode == "selection" else None,
        decision_bits=n_dec,
        slack_bits=n_slack,
        total_qubits=total,
        depth=q.depth,
        shots=q.shots,
        final_shots=q.final_shots,
        evaluation_budget=q.max_evaluations,
        timeout_seconds=config.timeout_seconds,
        memory_estimate_mb=memory_estimate_mb(total, _uses_noise(config)),
        estimated_runtime_s=estimate_runtime_s(config, total) if p is not None else None,
        max_qubits=MAX_QUBITS,
        constraints=cons_info,
        issues=issues,
        dataset_id=a.dataset.info.dataset_id if a.dataset is not None else None,
        dataset_label=a.dataset.info.label if a.dataset is not None else None,
    )


def _dataset_identity(config: RunConfig) -> tuple[str, dict[str, str]]:
    """(stored sha256, load-time corrections that touch the configured symbols)."""
    from .data import get_dataset

    try:
        ds = get_dataset(config.dataset_id)
    except KeyError as exc:
        raise PipelineError("unknown_dataset", "The selected dataset does not exist on the server.") from exc
    return ds.info.sha256, {s: ds.corrections[s] for s in sorted(ds.corrections) if s in config.symbols}


def cache_key(config: RunConfig) -> tuple[str, str]:
    """Return (config_hash, cache_key). cache_key includes dataset sha256,
    canonical config, seeds, IMPLEMENTATION_VERSION and backend/noise identity."""
    from .contracts import SCHEMA_VERSION

    cfg = config.model_dump(mode="json")
    config_hash = hashlib.sha256(_canonical(cfg).encode("utf-8")).hexdigest()
    sw = software_versions()
    sha, corrections = _dataset_identity(config)
    material = {
        "config": cfg,
        "dataset_sha256": sha,
        "implementation_version": IMPLEMENTATION_VERSION,
        "schema_version": SCHEMA_VERSION,
        "simulator": {"primitive": "qiskit_aer.primitives.SamplerV2", "method": "statevector", "qiskit": sw.get("qiskit"), "qiskit-aer": sw.get("qiskit-aer")},
        "noise": {"fake_backend": _fake_backend_name(config), "qiskit-ibm-runtime": sw.get("qiskit-ibm-runtime")} if _uses_noise(config) else None,
        "numerics": {k: sw.get(k) for k in ("numpy", "scipy", "cvxpy", "clarabel", "scikit-learn", "pandas")},
    }
    if corrections:  # only when relevant, so keys of runs without corrected symbols are unchanged
        material["data_corrections"] = corrections
    key = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()
    return config_hash, key


# --------------------------------------------------------------------------- #
# warm-up (called once in each pre-spawned worker child before a job arrives)
# --------------------------------------------------------------------------- #
def warmup() -> dict[str, float]:
    """Pre-import pandas/cvxpy/qiskit/Aer and load the default dataset so the
    first job does not pay ~10 s of imports. Returns measured seconds."""
    t: dict[str, float] = {}
    t0 = time.perf_counter()
    from .data import get_dataset

    try:
        get_dataset()
    except Exception:  # data may be missing in some test setups
        pass
    t["data"] = round(time.perf_counter() - t0, 3)
    t1 = time.perf_counter()
    import cvxpy as cp

    w = cp.Variable(2)
    cp.Problem(cp.Minimize(cp.sum_squares(w)), [cp.sum(w) == 1, w >= 0]).solve(solver=cp.CLARABEL)
    t["cvxpy"] = round(time.perf_counter() - t1, 3)
    t2 = time.perf_counter()
    from qiskit import QuantumCircuit
    from qiskit_aer.primitives import SamplerV2

    from . import classical, frontier, qubo  # noqa: F401  (module import cost)
    from .quantum import qaoa  # noqa: F401

    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    SamplerV2(seed=1).run([qc], shots=8).result()
    t["qiskit"] = round(time.perf_counter() - t2, 3)
    t["total"] = round(time.perf_counter() - t0, 3)
    return t


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _redact(exc: BaseException) -> str:
    msg = str(exc).replace("\\", "/")
    for marker in (":/", "/Users/", "/home/"):
        if marker in msg:
            msg = msg.split(marker)[0] + "<path>"
    return f"{type(exc).__name__}: {msg[:200]}"


def _assemble_or_raise(config: RunConfig, ds, sink):
    from .assemble import assemble
    from .qubo import count_qubits

    sink.stage("estimating", "training-window estimates (simple returns, Ledoit-Wolf covariance)")
    a = assemble(config, dataset=ds)
    sink.stage("screening", str(a.screening.get("description", "")) if a.screening else "")
    if a.problem is not None:
        n_dec, n_slack, _ = count_qubits(a.problem)
        a.issues.extend(_resource_issues(config, a.problem, n_dec + n_slack, n_dec))
    errs = [i for i in a.issues if i.severity == "error"]
    if errs or a.problem is None:
        e = errs[0] if errs else _issue("error", "invalid_config", "The configuration could not be assembled into a problem.")
        raise PipelineError(
            e.code,
            e.message,
            {"conflict_kind": _conflict_kind(a.issues) or "preflight_conflict", "issues": [i.model_dump(mode="json") for i in a.issues if i.severity == "error"]},
        )
    return a


def _jsonable(obj: Any) -> Any:
    import numpy as np

    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        obj = float(obj)
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _evaluate_solver(p, ds, cfg: RunConfig, solver: str, status: str, x, *, detail=None, runtime=None, work=None, notes=None):
    from .contracts import SolverResult
    from .portfolio import decision_string, evaluate

    d = cfg.dates
    pe = None
    bitstring = None
    if x is not None:
        pe = evaluate(p, x, prices=ds.prices, validation=(d.validation_start, d.validation_end), test=(d.test_start, d.test_end))
        bitstring = decision_string(x)
        if not pe.feasibility.feasible:
            status, detail = "failed", "the shared validator rejected the solver's portfolio (a constraint is violated)"
    return SolverResult(
        solver=solver,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        status_detail=detail,
        bitstring=bitstring,
        portfolio=pe,
        runtime_s={k: float(v) for k, v in (runtime or {}).items()},
        notes=list(notes or []),
        work=_jsonable(work or {}),
    )


def _run_baselines(p, qubo, cfg: RunConfig, sink, deadline: float | None) -> tuple[dict[str, Any], dict[str, float]]:
    from .classical import solve_annealing, solve_exact, solve_greedy, solve_relaxation_rounding
    from .classical.common import ClassicalOutcome

    b = cfg.baselines
    out: dict[str, Any] = {}
    times: dict[str, float] = {}
    jobs = [
        ("exact", b.exact, lambda: solve_exact(p)),
        ("relaxation_rounding", b.relaxation_rounding, lambda: solve_relaxation_rounding(p)),
        ("simulated_annealing", b.simulated_annealing, lambda: solve_annealing(p, sweeps=b.sa_sweeps, restarts=b.sa_restarts, seed=cfg.qaoa.seed, penalty=qubo.penalty.weight, deadline=deadline)),
        ("greedy", b.greedy, lambda: solve_greedy(p)),
    ]
    for name, enabled, fn in jobs:
        sink.check_cancelled()
        if not enabled:
            out[name] = ClassicalOutcome(name, "skipped", status_detail="not requested")
            continue
        t0 = time.perf_counter()
        sink.log("info", f"baseline {name} started")
        try:
            out[name] = fn()
        except Cancelled:
            raise
        except Exception as exc:  # recorded, never hidden
            out[name] = ClassicalOutcome(name, "failed", status_detail=_redact(exc))
        times[name] = round(time.perf_counter() - t0, 4)
        sink.log("info", f"baseline {name}: {out[name].status} in {times[name]:.2f}s")
    return out, times


def _gaps(solvers: list, exact_extra: dict | None) -> None:
    from .classical.common import compute_gap

    feas = [s for s in solvers if s.portfolio is not None and s.portfolio.feasibility.feasible]
    if not feas:
        return
    ex = next((s for s in feas if s.solver == "exact" and s.status == "ok"), None)
    if ex is not None:
        ref, kind, ref_name = ex.portfolio.objective, "exact_optimum", "exact"
        f_range = (exact_extra or {}).get("f_range")
    else:
        best = min(feas, key=lambda s: (s.portfolio.objective, SOLVER_ORDER.index(s.solver)))
        ref, kind, ref_name, f_range = best.portfolio.objective, "best_known", best.solver, None
    for s in feas:
        s.gap = compute_gap(s.portfolio.objective, ref, kind, ref_name, f_range)


def _constraint_notes(p, solvers) -> list[str]:
    from .constraints import TargetReturn

    notes = []
    for c in p.constraints:
        if isinstance(c, TargetReturn):
            st = c.status(p)
            notes.append(
                {
                    "binding": f"The target return of {100 * c.target:.1f}% p.a. is binding: some candidate portfolios fall below it and are excluded.",
                    "nonbinding": f"The target return of {100 * c.target:.1f}% p.a. is not binding: every candidate portfolio already meets it.",
                    "infeasible": f"The target return of {100 * c.target:.1f}% p.a. is unreachable.",
                }[st]
            )
    ex = next((s for s in solvers if s.solver == "exact" and s.portfolio is not None), None)
    if ex is not None:
        at_limit = [r.explanation for r in ex.portfolio.feasibility.residuals if r.constraint.startswith("sector_cap") and r.limit is not None and abs(r.value - r.limit) < 1e-9]
        if at_limit:
            notes.append("Sector caps at their limit in the optimal portfolio: " + "; ".join(at_limit) + ".")
    return notes


def _ms(v: list) -> dict[str, float | None]:
    import numpy as np

    v = [x for x in v if x is not None]
    if not v:
        return {"mean": None, "std": None, "n": 0}
    a = np.asarray(v, dtype=np.float64)
    return {"mean": float(a.mean()), "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0, "n": int(len(a))}


def noise_comparison(qubo, outcome, cfg: RunConfig, sink):
    """Fixed trained parameters, matched shots and seeds, repeated paired trials."""
    import numpy as np

    from .contracts import NoiseComparison, NoiseTrial
    from .quantum.circuit import parameter_permutation
    from .quantum.noise import noisy_setup
    from .quantum.qaoa import _split_params, best_feasible, seeds_for
    from .quantum.sampling import AerSampler, Decoder

    theta = np.asarray(outcome.optimal_parameters, dtype=np.float64)
    circ = outcome.circuits
    name = _fake_backend_name(cfg)
    seeds = seeds_for(cfg.qaoa.seed)
    setup = noisy_setup(circ.logical, name, seeds["transpiler"])
    perm_noisy = parameter_permutation(setup.circuit, *_split_params(setup.circuit, circ.depth))
    ideal_s = AerSampler(circ.tqc, circ.perm)
    noisy_s = AerSampler(setup.circuit, perm_noisy, noise_model=setup.noise_model)
    dec = Decoder(qubo)
    p = qubo.problem
    shots = cfg.qaoa.final_shots

    def trial(sampler, seed: int) -> NoiseTrial:
        sink.check_cancelled()
        t0 = time.perf_counter()
        d = dec.decode(sampler.counts(theta, shots, seed))
        tot = float(d.counts.sum())
        bi = best_feasible(d)
        ret = vol = obj = None
        if bi is not None:
            x = np.array([int(ch) for ch in d.decision[bi]], dtype=np.int64)
            w = p.enc.weights(x)
            ret, vol, obj = float(w @ p.mu), float(np.sqrt(max(float(w @ p.sigma @ w), 0.0))), float(d.objective[bi])
        return NoiseTrial(
            seed=seed,
            feasible_probability=float(d.counts[d.feasible].sum() / tot) if tot else 0.0,
            best_objective=obj,
            expected_return=ret,
            volatility=vol,
            runtime_s=round(time.perf_counter() - t0, 4),
        )

    ideal, noisy = [], []
    for k in range(cfg.noise_trials):
        seed = seeds["final_batch"] + 7919 * (k + 1)
        ideal.append(trial(ideal_s, seed))
        noisy.append(trial(noisy_s, seed))
        sink.log("info", f"noise trial {k + 1}/{cfg.noise_trials}: feasible probability ideal {ideal[-1].feasible_probability:.3f}, noisy {noisy[-1].feasible_probability:.3f}")

    fi = [t.feasible_probability for t in ideal]
    fn = [t.feasible_probability for t in noisy]
    paired_obj = [b.best_objective - a.best_objective for a, b in zip(ideal, noisy) if a.best_objective is not None and b.best_objective is not None]
    summary = {
        "trials": cfg.noise_trials,
        "shots_per_trial": shots,
        "feasible_probability_ideal": _ms(fi),
        "feasible_probability_noisy": _ms(fn),
        "delta_feasible_probability": _ms([b - a for a, b in zip(fi, fn)]),
        "best_objective_ideal": _ms([t.best_objective for t in ideal]),
        "best_objective_noisy": _ms([t.best_objective for t in noisy]),
        "delta_best_objective": _ms(paired_obj),
        "noisy_trials_without_feasible_sample": sum(1 for t in noisy if t.best_objective is None),
        "method": "same trained parameters, same shots and simulator seed in each paired trial; delta = noisy - ideal per trial; std = sample std over trials (ddof=1)",
        "active_physical_qubits": list(setup.active_physical),
    }
    return NoiseComparison(
        backend=name,
        noise_model_summary=setup.summary,
        shots=shots,
        parameters_source="optimal parameters of the noise-free optimisation (fixed, not re-optimised under noise)",
        ideal=ideal,
        noisy=noisy,
        summary=summary,
    )


def run_optimisation(config: RunConfig, run_id: str, sink: ProgressSink, artifacts_dir: str) -> RunResult:
    """Full run: data -> estimation -> QUBO -> QAOA (Aer) -> final sampling ->
    shared validation -> baselines -> frontier -> optional noise comparison.
    Writes raw artifacts (full counts JSON, etc.) into artifacts_dir."""
    from .data import get_dataset
    from .data.universe import SURVIVORSHIP_NOTE
    from .explain import explain_run
    from .frontier import continuous_frontier, discrete_points, solver_point
    from .qubo import build_qubo
    from .risk import estimation_info

    t_start = time.perf_counter()
    deadline = time.monotonic() + QAOA_DEADLINE_FRACTION * config.timeout_seconds
    timings: dict[str, float] = {}
    warnings: list[str] = []
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    sink.check_cancelled()
    sink.stage("loading_data", "loading the price dataset")
    t0 = time.perf_counter()
    try:
        ds = get_dataset(config.dataset_id)
    except KeyError as exc:
        raise PipelineError("unknown_dataset", "The selected dataset does not exist on the server.") from exc
    timings["loading_data"] = round(time.perf_counter() - t0, 4)

    t0 = time.perf_counter()
    a = _assemble_or_raise(config, ds, sink)
    p = a.problem
    timings["estimating_and_screening"] = round(time.perf_counter() - t0, 4)
    warnings += [i.message for i in a.issues if i.severity == "warning"]

    sink.check_cancelled()
    sink.stage("building_qubo", "QUBO with derived penalties and bounded slack")
    t0 = time.perf_counter()
    qubo = build_qubo(p, config.qaoa.penalty_scale)
    timings["building_qubo"] = round(time.perf_counter() - t0, 4)
    warnings += list(qubo.penalty.warnings)

    # ---------------- QAOA ----------------
    from .quantum.qaoa import make_circuits, run_qaoa, seeds_for

    t0 = time.perf_counter()
    noise_model = noisy_circuit = noise_label = None
    if config.qaoa.noise == "fake_backend":
        from .quantum.noise import noisy_setup

        sink.stage("transpiling", f"preparing the fake-backend noise model ({_fake_backend_name(config)})")
        tseed = seeds_for(config.qaoa.seed)["transpiler"]
        setup = noisy_setup(make_circuits(qubo, config.qaoa.depth, tseed).logical, _fake_backend_name(config), tseed)
        noise_model, noisy_circuit, noise_label = setup.noise_model, setup.circuit, setup.summary
    outcome = None
    qaoa_error = None
    try:
        outcome = run_qaoa(qubo, config.qaoa, sink, artifacts_dir, deadline=deadline, noise_model=noise_model, noisy_circuit=noisy_circuit, noise_label=noise_label)
    except Cancelled:
        raise
    except Exception as exc:
        qaoa_error = _redact(exc)
        sink.log("error", f"QAOA failed: {qaoa_error}")
    timings["qaoa"] = round(time.perf_counter() - t0, 4)

    # ---------------- baselines ----------------
    sink.check_cancelled()
    sink.stage("baselines", "exact / relaxation / annealing / greedy baselines")
    t0 = time.perf_counter()
    base, base_t = _run_baselines(p, qubo, config, sink, deadline=time.monotonic() + max(5.0, 0.2 * config.timeout_seconds))
    timings["baselines"] = round(time.perf_counter() - t0, 4)
    for k, v in base_t.items():
        timings[f"baseline_{k}"] = v

    from .contracts import SolverResult

    solvers: list[SolverResult] = []
    if outcome is not None:
        solvers.append(_evaluate_solver(p, ds, config, "qaoa", outcome.status, outcome.best_decision, detail=outcome.status_detail, runtime=outcome.runtime, work=outcome.work, notes=outcome.notes))
    else:
        solvers.append(SolverResult(solver="qaoa", status="failed", status_detail=qaoa_error))
    for name in SOLVER_ORDER[1:]:
        o = base[name]
        solvers.append(_evaluate_solver(p, ds, config, name, o.status, o.x, detail=o.status_detail, runtime=o.runtime, work=o.work, notes=o.notes))
    exact_extra = base["exact"].extra if base["exact"].status == "ok" else None
    _gaps(solvers, exact_extra)

    # ---------------- frontier ----------------
    sink.check_cancelled()
    sink.stage("frontier", "relaxed continuous frontier + discrete feasible portfolios")
    t0 = time.perf_counter()
    frontier = []
    try:
        frontier += continuous_frontier(p)
    except Cancelled:
        raise
    except Exception as exc:
        warnings.append(f"continuous frontier unavailable: {_redact(exc)}")
    if exact_extra:
        frontier += discrete_points(exact_extra)
    for s in solvers:
        if s.portfolio is not None and s.portfolio.feasibility.feasible:
            frontier.append(solver_point(s.solver, s.portfolio.expected_return, s.portfolio.volatility, s.portfolio.objective))
    timings["frontier"] = round(time.perf_counter() - t0, 4)

    # ---------------- noise comparison ----------------
    noise_cmp = None
    if config.noise_comparison:
        if outcome is None or outcome.optimal_parameters is None:
            warnings.append("noise comparison skipped: QAOA did not produce trained parameters")
        else:
            sink.stage("noise", f"noise comparison on {_fake_backend_name(config)}: {config.noise_trials} paired trials")
            t0 = time.perf_counter()
            try:
                noise_cmp = noise_comparison(qubo, outcome, config, sink)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append(f"noise comparison failed: {_redact(exc)}")
            timings["noise_comparison"] = round(time.perf_counter() - t0, 4)

    # ---------------- report ----------------
    sink.stage("reporting", "assembling the result")
    config_hash, ckey = cache_key(config)
    explanations = explain_run(
        n_assets=p.n,
        n_decision=qubo.n_decision,
        n_slack=qubo.n - qubo.n_decision,
        solvers=solvers,
        qaoa_feasible_probability=outcome.details.final_batch.feasible_probability if outcome is not None else None,
        qaoa_depth=config.qaoa.depth,
        constraint_notes=_constraint_notes(p, solvers),
        dataset_label=ds.info.label,
        survivorship_note=SURVIVORSHIP_NOTE,
        cost_start=p.start_type,
        noise_summary=noise_cmp.summary if noise_cmp is not None else None,
    )
    if p.cost_is_constant() and p.costs_enabled:
        explanations.append("Transaction costs are identical for every candidate portfolio here, so they shift the objective but cannot change the ranking.")
    timings["total"] = round(time.perf_counter() - t_start, 4)
    screening = dict(a.screening)
    screening["dropped_insufficient_history"] = dict(a.dropped)
    return RunResult(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        implementation_version=IMPLEMENTATION_VERSION,
        config=config,
        config_hash=config_hash,
        cache_key=ckey,
        dataset=ds.info,
        estimation=estimation_info(a.estimate, ds.prices, config.dates),
        encoded_symbols=list(p.symbols),
        sectors={s: sec for s, sec in zip(p.symbols, p.sectors)},
        screening=_jsonable(screening),
        qubo=qubo.info(),
        qaoa=outcome.details if outcome is not None else None,
        solvers=solvers,
        frontier=frontier,
        noise_comparison=noise_cmp,
        timings_s=timings,
        software=software_versions(),
        warnings=list(dict.fromkeys(warnings)),
        explanations=explanations,
    )


# --------------------------------------------------------------------------- #
# experiments
# --------------------------------------------------------------------------- #
class _CellSink:
    """Forwards cancellation and logs; a cell's per-evaluation points are
    summarised in the cell instead of streamed as run iterations."""

    def __init__(self, sink: ProgressSink, prefix: str):
        self.sink, self.prefix = sink, prefix
        self.evaluations = 0

    def stage(self, stage: str, message: str = "", **data: Any) -> None:
        pass

    def iteration(self, point: dict[str, Any]) -> None:
        self.evaluations += 1

    def log(self, level: str, message: str, **data: Any) -> None:
        self.sink.log(level, f"{self.prefix}{message}")

    def check_cancelled(self) -> None:
        self.sink.check_cancelled()


def experiment_cells(config: ExperimentConfig) -> list[dict[str, Any]]:
    """Cell specs. one_factor_at_a_time varies one factor from the base
    settings at a time (identical cells are run once and reused)."""
    b = config.base
    q = b.qaoa
    n0 = len(b.symbols)
    seeds = list(config.seeds) or [q.seed]
    cells: list[dict[str, Any]] = []
    if config.design == "full_factorial":
        for n in list(config.universe_sizes) or [n0]:
            for d in config.depths:
                for o in config.optimizers:
                    for i in config.init_strategies:
                        for s in seeds:
                            cells.append({"factor": "full_factorial", "depth": d, "optimizer": o, "init_strategy": i, "seed": s, "n_assets": n})
        return cells
    plan = [("depth", [(d, q.optimizer, q.init_strategy, n0) for d in config.depths])]
    plan.append(("optimizer", [(q.depth, o, q.init_strategy, n0) for o in config.optimizers]))
    plan.append(("init_strategy", [(q.depth, q.optimizer, i, n0) for i in config.init_strategies]))
    if config.universe_sizes:
        plan.append(("universe_size", [(q.depth, q.optimizer, q.init_strategy, n) for n in config.universe_sizes]))
    for factor, levels in plan:
        for d, o, i, n in levels:
            for s in seeds:
                cells.append({"factor": factor, "depth": d, "optimizer": o, "init_strategy": i, "seed": s, "n_assets": n})
    return cells


def _variant(base: RunConfig, n: int) -> RunConfig:
    """Base config restricted to its first n symbols (K scaled proportionally)."""
    if n == len(base.symbols):
        return base
    if n > len(base.symbols) or n < 2:
        raise PipelineError("invalid_universe_size", f"Universe size {n} must be between 2 and the {len(base.symbols)} base symbols.")
    c = base.constraints
    upd: dict[str, Any] = {}
    if base.mode == "selection" and c.cardinality is not None:
        upd["cardinality"] = max(1, min(n - 1, round(c.cardinality * n / len(base.symbols))))
    return base.model_copy(update={"symbols": list(base.symbols[:n]), "constraints": c.model_copy(update=upd)})


def run_experiment(config: ExperimentConfig, run_id: str, sink: ProgressSink, artifacts_dir: str) -> ExperimentResult:
    from .classical import solve_exact
    from .contracts import ExperimentCell
    from .data import get_dataset
    from .portfolio import evaluate
    from .qubo import build_qubo
    from .quantum.qaoa import run_qaoa

    t_start = time.perf_counter()
    specs = experiment_cells(config)
    if len(specs) > config.max_cells:
        raise PipelineError("too_many_cells", f"The design has {len(specs)} cells, more than max_cells = {config.max_cells}.", {"cells": len(specs)})
    sink.stage("loading_data", "loading the price dataset")
    try:
        ds = get_dataset(config.base.dataset_id)
    except KeyError as exc:
        raise PipelineError("unknown_dataset", "The selected dataset does not exist on the server.") from exc
    warnings: list[str] = []
    per_size: dict[int, dict[str, Any]] = {}
    for n in sorted({s["n_assets"] for s in specs}):
        cfg_n = _variant(config.base, n)
        a = _assemble_or_raise(cfg_n, ds, NullSink())
        p = a.problem
        per_size[n] = {"config": cfg_n, "problem": p, "qubo": build_qubo(p, cfg_n.qaoa.penalty_scale), "exact": solve_exact(p)}
        warnings += [f"n={n}: {i.message}" for i in a.issues if i.severity == "warning"]

    cells: list[ExperimentCell] = []
    done: dict[tuple, ExperimentCell] = {}
    outcomes: dict[tuple, Any] = {}
    sink.stage("experiment", f"{len(specs)} cells")
    d = config.base.dates
    for k, spec in enumerate(specs):
        sink.check_cancelled()
        key = (spec["depth"], spec["optimizer"], spec["init_strategy"], spec["seed"], spec["n_assets"])
        if key in done:  # same run already executed for another factor: reuse it
            cells.append(done[key].model_copy(update={"factor": spec["factor"]}))
            continue
        ctx = per_size[spec["n_assets"]]
        cfg_n: RunConfig = ctx["config"]
        p, qubo, ex = ctx["problem"], ctx["qubo"], ctx["exact"]
        settings = cfg_n.qaoa.model_copy(update={"depth": spec["depth"], "optimizer": spec["optimizer"], "init_strategy": spec["init_strategy"], "seed": spec["seed"], "noise": "ideal"})
        sink.stage("experiment", f"cell {k + 1}/{len(specs)}: {spec['factor']} depth={spec['depth']} {spec['optimizer']} {spec['init_strategy']} seed={spec['seed']} n={spec['n_assets']}")
        f_opt = ex.extra.get("f_opt") if ex.status == "ok" else None
        f_worst = ex.extra.get("f_worst_feasible") if ex.status == "ok" else None
        t0 = time.perf_counter()
        try:
            out = run_qaoa(qubo, settings, _CellSink(sink, f"[cell {k + 1}] "), Path(artifacts_dir) / "cells" / f"{k + 1:03d}", deadline=time.monotonic() + cfg_n.timeout_seconds)
            best = val_sharpe = None
            if out.best_decision is not None:
                pe = evaluate(p, out.best_decision, prices=ds.prices, validation=(d.validation_start, d.validation_end))
                best = pe.objective
                val_sharpe = pe.validation.sharpe if pe.validation is not None else None
            ar = None
            if best is not None and f_opt is not None and f_worst is not None and f_worst - f_opt > 1e-12:
                ar = float((f_worst - best) / (f_worst - f_opt))
            cell = ExperimentCell(
                factor=spec["factor"],
                depth=spec["depth"],
                optimizer=spec["optimizer"],
                init_strategy=spec["init_strategy"],
                seed=spec["seed"],
                n_assets=spec["n_assets"],
                total_qubits=qubo.n,
                status=out.status,  # type: ignore[arg-type]
                feasible_probability=out.details.final_batch.feasible_probability,
                best_objective=best,
                exact_objective=f_opt,
                gap_abs=(best - f_opt) if (best is not None and f_opt is not None) else None,
                approximation_ratio=ar,
                evaluations=out.details.evaluations_used,
                preparation_evaluations=out.details.preparation_evaluations,
                runtime_s=round(time.perf_counter() - t0, 4),
                validation_sharpe=val_sharpe,
            )
            outcomes[key] = out
        except (Cancelled, PipelineError):
            raise
        except Exception as exc:
            cell = ExperimentCell(
                factor=spec["factor"],
                depth=spec["depth"],
                optimizer=spec["optimizer"],
                init_strategy=spec["init_strategy"],
                seed=spec["seed"],
                n_assets=spec["n_assets"],
                total_qubits=qubo.n,
                status="failed",
                exact_objective=f_opt,
                runtime_s=round(time.perf_counter() - t0, 4),
                error=_redact(exc),
            )
        done[key] = cell
        cells.append(cell)
        sink.log("info", f"cell {k + 1}/{len(specs)} {cell.status}: feasible p={cell.feasible_probability}, gap={cell.gap_abs}, {cell.runtime_s:.1f}s")

    summary = summarise_cells(cells)
    selected, rule = select_configuration(cells)
    noise_cmp = None
    if config.base.noise_comparison and selected is not None:
        sel = (selected["depth"], selected["optimizer"], selected["init_strategy"], selected["seeds"][0], selected["n_assets"])
        if sel in outcomes:
            sink.stage("noise", "noise comparison for the selected configuration")
            ctx = per_size[sel[4]]
            cfg_sel = ctx["config"].model_copy(update={"qaoa": ctx["config"].qaoa.model_copy(update={"seed": sel[3], "depth": sel[0]})})
            try:
                noise_cmp = noise_comparison(ctx["qubo"], outcomes[sel], cfg_sel, sink)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append(f"noise comparison failed: {_redact(exc)}")
    return ExperimentResult(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        implementation_version=IMPLEMENTATION_VERSION,
        config=config,
        dataset=ds.info,
        cells=cells,
        summary=summary,
        selected_configuration=selected,
        selection_rule=rule,
        noise_comparison=noise_cmp,
        timings_s={"total": round(time.perf_counter() - t_start, 4), "cells_sum": float(sum(c.runtime_s for c in done.values()))},
        software=software_versions(),
        warnings=list(dict.fromkeys(warnings)),
    )


def _level(c, factor: str) -> str:
    v = {"depth": c.depth, "optimizer": c.optimizer, "init_strategy": c.init_strategy, "universe_size": c.n_assets}.get(factor)
    return str(v) if v is not None else f"p{c.depth}-{c.optimizer}-{c.init_strategy}-n{c.n_assets}"


def summarise_cells(cells) -> list[dict[str, Any]]:
    groups: dict[tuple, list] = {}
    for c in cells:
        groups.setdefault((c.factor, _level(c, c.factor)), []).append(c)
    out = []
    for (factor, level), cs in groups.items():
        out.append(
            {
                "factor": factor,
                "level": level,
                "cells": len(cs),
                "seeds": sorted({c.seed for c in cs}),
                "feasible_result_rate": sum(1 for c in cs if c.best_objective is not None) / len(cs),
                "optimum_found_rate": sum(1 for c in cs if c.gap_abs is not None and abs(c.gap_abs) <= 1e-9) / len(cs),
                "feasible_probability": _ms([c.feasible_probability for c in cs]),
                "gap_abs": _ms([c.gap_abs for c in cs]),
                "approximation_ratio": _ms([c.approximation_ratio for c in cs]),
                "evaluations": _ms([c.evaluations for c in cs]),
                "preparation_evaluations": _ms([c.preparation_evaluations for c in cs]),
                "runtime_s": _ms([c.runtime_s for c in cs]),
                "validation_sharpe": _ms([c.validation_sharpe for c in cs]),
                "total_qubits": sorted({c.total_qubits for c in cs}),
            }
        )
    return out


SELECTION_RULE = (
    "Configuration (depth, optimizer, init, universe size) with the highest mean approximation ratio on the TRAINING objective "
    "across seeds (1 = exact optimum, 0 = worst feasible portfolio; a seed without a feasible sample counts as 0; compared to 9 decimals); ties -> higher "
    "mean final-batch feasible probability, then fewer mean circuit evaluations (optimisation + warm-start preparation), then lower "
    "depth. Every criterion is deterministic for fixed seeds (wall time is not used). Validation Sharpe is reported only; test data is never used."
)


def select_configuration(cells) -> tuple[dict[str, Any] | None, str]:
    import numpy as np

    groups: dict[tuple, dict[int, Any]] = {}
    for c in cells:
        groups.setdefault((c.depth, c.optimizer, c.init_strategy, c.n_assets), {})[c.seed] = c
    best_key, best_score = None, None
    for key, by_seed in groups.items():
        cs = list(by_seed.values())
        if any(c.exact_objective is None for c in cs):
            continue
        score = (
            round(float(np.mean([c.approximation_ratio if c.approximation_ratio is not None else 0.0 for c in cs])), 9),
            float(np.mean([c.feasible_probability or 0.0 for c in cs])),
            -float(np.mean([(c.evaluations or 0) + (c.preparation_evaluations or 0) for c in cs])),
            -int(key[0]),
            str(key),
        )
        if best_score is None or score > best_score:
            best_key, best_score = key, score
    if best_key is None:
        return None, SELECTION_RULE
    cs = list(groups[best_key].values())
    return (
        {
            "depth": best_key[0],
            "optimizer": best_key[1],
            "init_strategy": best_key[2],
            "n_assets": best_key[3],
            "seeds": sorted(c.seed for c in cs),
            "mean_approximation_ratio": best_score[0],
            "mean_feasible_probability": best_score[1],
            "mean_circuit_evaluations": -best_score[2],
            "mean_runtime_s": float(np.mean([c.runtime_s for c in cs])),
            "mean_validation_sharpe": _ms([c.validation_sharpe for c in cs])["mean"],
        },
        SELECTION_RULE,
    )
