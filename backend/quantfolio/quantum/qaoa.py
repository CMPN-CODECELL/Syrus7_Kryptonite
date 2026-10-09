"""QAOA run: circuit -> transpile once -> budgeted optimisation -> declared
final batch -> decoded result (or an explicit no-feasible-sample outcome).

The QAOA answer is ONLY ever taken from the declared final batch: the
lowest-economic-objective sample that passes the shared exact constraint
check. If no final-batch sample is feasible the status is
`no_feasible_sample` and no portfolio is reported (a baseline answer is never
substituted).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import CircuitStats, FinalBatch, IterationPoint, QaoaDetails, QaoaSettings, SampleRow
from ..data.paths import atomic_write_bytes
from ..qubo import Qubo
from . import warm_start
from .circuit import build_qaoa_circuit, circuit_stats, parameter_permutation, transpile_once
from .optimizers import BudgetExhausted, DeadlineReached, Tracked, cobyla, spsa
from .sampling import AerSampler, Decoded, Decoder, estimate_energy

FINAL_SEED_OFFSET = 1_000_003
TOP_SAMPLES = 32
HIST_BINS = 20
SELECTION_POLICY = (
    "lowest economic objective among final-batch samples that pass the exact constraint check "
    "(ties: higher count, then lexicographic bitstring)"
)
AER_BASIS = ["h", "rz", "rzz", "rx", "measure"]


@dataclass
class Circuits:
    depth: int
    logical: Any
    tqc: Any
    perm: np.ndarray
    transpile_s: float
    stats: CircuitStats


@dataclass
class QaoaOutcome:
    details: QaoaDetails
    status: str
    status_detail: str | None
    best_decision: np.ndarray | None
    runtime: dict[str, float]
    work: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    circuits: Circuits | None = None
    optimal_parameters: np.ndarray | None = None


def seeds_for(seed: int) -> dict[str, int]:
    return {
        "init": int(seed),
        "transpiler": int(seed),
        "optimizer": int(seed) + 17,
        "evaluation_base": int(seed) * 1000 + 1,
        "final_batch": int(seed) + FINAL_SEED_OFFSET,
    }


def make_circuits(qubo: Qubo, depth: int, seed: int, backend=None) -> Circuits:
    from qiskit_aer import AerSimulator

    h, J, _ = qubo.ising()
    scale = qubo.circuit_scale()
    qc, gamma, beta = build_qaoa_circuit(h, J, depth, scale)
    backend = backend or AerSimulator(method="statevector")
    tqc, dt = transpile_once(qc, backend, seed)
    perm = parameter_permutation(tqc, gamma, beta)
    return Circuits(depth=depth, logical=qc, tqc=tqc, perm=perm, transpile_s=dt, stats=circuit_stats(tqc, dt, 1, AER_BASIS))


def _counts_bytes(counts: dict[str, int]) -> bytes:
    return json.dumps({k: int(counts[k]) for k in sorted(counts)}, separators=(",", ":")).encode("utf-8")


def _histogram(d: Decoded) -> list[dict[str, float]]:
    tot = float(d.counts.sum())
    if tot == 0 or not len(d.objective):
        return []
    lo, hi = float(d.objective.min()), float(d.objective.max())
    if hi - lo < 1e-15:
        hi = lo + 1e-12
    edges = np.linspace(lo, hi, HIST_BINS + 1)
    idx = np.clip(np.searchsorted(edges, d.objective, side="right") - 1, 0, HIST_BINS - 1)
    out = []
    for b in range(HIST_BINS):
        m = idx == b
        out.append(
            {
                "bin_start": float(edges[b]),
                "bin_end": float(edges[b + 1]),
                "probability": float(d.counts[m].sum() / tot),
                "feasible_probability": float(d.counts[m & d.feasible].sum() / tot),
            }
        )
    return out


def best_feasible(d: Decoded) -> int | None:
    idx = np.nonzero(d.feasible)[0]
    if not len(idx):
        return None
    return int(min(idx, key=lambda i: (d.objective[i], -d.counts[i], d.strings[i])))


def final_batch_record(d: Decoded, shots: int, seed: int, counts_sha: str) -> tuple[FinalBatch, int | None]:
    tot = float(d.counts.sum())
    bi = best_feasible(d)
    order = sorted(range(len(d.strings)), key=lambda i: (-d.counts[i], d.strings[i]))[:TOP_SAMPLES]
    if bi is not None and bi not in order:
        order.append(bi)
    rows = [
        SampleRow(
            bitstring=d.strings[i],
            count=int(d.counts[i]),
            probability=float(d.counts[i] / tot),
            decision_bits=d.decision[i],
            feasible=bool(d.feasible[i]),
            objective=float(d.objective[i]),
            qubo_energy=float(d.energy[i]),
        )
        for i in order
    ]
    fb = FinalBatch(
        shots=int(shots),
        seed=int(seed),
        distinct_bitstrings=len(d.strings),
        feasible_probability=float(d.counts[d.feasible].sum() / tot) if tot else 0.0,
        selection_policy=SELECTION_POLICY,
        best_bitstring=d.strings[bi] if bi is not None else None,
        top_samples=rows,
        objective_histogram=_histogram(d),
        counts_sha256=counts_sha,
    )
    return fb, bi


def run_qaoa(
    qubo: Qubo,
    settings: QaoaSettings,
    sink,
    artifacts_dir: str | Path | None,
    *,
    deadline: float | None = None,
    noise_model=None,
    noisy_circuit=None,
    noise_label: str | None = None,
) -> QaoaOutcome:
    t0 = time.perf_counter()
    seeds = seeds_for(settings.seed)
    depth = int(settings.depth)
    decoder = Decoder(qubo)
    scale = qubo.circuit_scale()
    runtime: dict[str, float] = {}

    sink.stage("transpiling", f"building and transpiling the depth-{depth} QAOA circuit on {qubo.n} qubits")
    circ = make_circuits(qubo, depth, seeds["transpiler"])
    if noisy_circuit is not None:
        # caller supplies the compact fake-backend circuit for the same depth
        run_tqc = noisy_circuit
        perm = parameter_permutation(noisy_circuit, *_split_params(noisy_circuit, depth))
    else:
        run_tqc, perm = circ.tqc, circ.perm
    sampler = AerSampler(run_tqc, perm, noise_model=noise_model)
    runtime["transpile"] = circ.transpile_s

    iterations: list[IterationPoint] = []
    best_feasible_obj = [None]
    eval_counter = [0]

    def make_objective(sampler_: AerSampler, phase: str):
        def f(theta: np.ndarray) -> float:
            sink.check_cancelled()
            k = eval_counter[0]
            counts = sampler_.counts(theta, settings.shots, seeds["evaluation_base"] + k)
            d = decoder.decode(counts)
            e = estimate_energy(d, settings.estimator, settings.cvar_alpha)
            tot = float(d.counts.sum())
            ff = float(d.counts[d.feasible].sum() / tot) if tot else 0.0
            bi = best_feasible(d)
            if bi is not None:
                v = float(d.objective[bi])
                if best_feasible_obj[0] is None or v < best_feasible_obj[0]:
                    best_feasible_obj[0] = v
            eval_counter[0] += 1
            pt = IterationPoint(
                evaluation=eval_counter[0],
                energy=e,
                best_feasible_objective=best_feasible_obj[0],
                feasible_fraction=ff,
                elapsed_s=round(time.perf_counter() - t0, 4),
                phase=phase,  # type: ignore[arg-type]
            )
            iterations.append(pt)
            sink.iteration(pt.model_dump(mode="json"))
            return e * scale

        return f

    def optimise(fun_tr: Tracked, x0: np.ndarray) -> dict:
        if settings.optimizer == "COBYLA":
            return cobyla(fun_tr, x0)
        return spsa(fun_tr, x0, seed=seeds["optimizer"])

    # ---------------- initial point ----------------
    prep_evals = 0
    stopped = None
    t_prep = time.perf_counter()
    if settings.init_strategy == "random":
        x0 = warm_start.random_init(depth, seeds["init"])
        init_desc = warm_start.DESCRIPTIONS["random"] + f" (seed {seeds['init']})"
    else:
        x = warm_start.linear_ramp(1)
        per = warm_start.preparation_budget(settings.max_evaluations, depth)
        levels = []
        if depth > 1 and noisy_circuit is not None:
            raise ValueError("warm_interp with a noisy optimisation is not supported")
        for p_lvl in range(1, depth):
            c_l = make_circuits(qubo, p_lvl, seeds["transpiler"])
            s_l = AerSampler(c_l.tqc, c_l.perm, noise_model=noise_model)
            tr = Tracked(make_objective(s_l, "warm_start_preparation"), per, deadline)
            optimise(tr, x)
            prep_evals += tr.n
            if tr.stopped == "deadline":
                stopped = "deadline"
            best = tr.best_x if tr.best_x is not None else x
            levels.append({"depth": p_lvl, "evaluations": tr.n, "best_scaled_energy": tr.best_f})
            x = warm_start.interp(best, p_lvl)
            sink.log("info", f"INTERP: depth {p_lvl} optimised with {tr.n} evaluations, interpolated to depth {p_lvl + 1}")
            if stopped:
                break
        if len(x) != 2 * depth:  # deadline hit early: pad by repeated interpolation
            while len(x) < 2 * depth:
                x = warm_start.interp(x, len(x) // 2)
        x0 = x
        init_desc = warm_start.DESCRIPTIONS["warm_interp"] + (
            f"; preparation: {prep_evals} evaluations over depths 1..{depth - 1} ({levels})" if depth > 1 else "; depth 1: linear-ramp start, no preparation"
        )
    runtime["warm_start_preparation"] = round(time.perf_counter() - t_prep, 4)

    # ---------------- optimisation ----------------
    sink.stage("optimising", f"{settings.optimizer}, budget {settings.max_evaluations} evaluations x {settings.shots} shots")
    t_opt = time.perf_counter()
    tr = Tracked(make_objective(sampler, "optimisation"), int(settings.max_evaluations), deadline)
    opt_settings: dict[str, Any] = {}
    if stopped != "deadline":
        opt_settings = optimise(tr, x0)
    stopped = stopped or tr.stopped
    theta = tr.best_x if tr.best_x is not None else np.asarray(x0, dtype=np.float64)
    runtime["optimisation"] = round(time.perf_counter() - t_opt, 4)

    # ---------------- declared final batch ----------------
    sink.check_cancelled()
    sink.stage("final_sampling", f"declared final batch: {settings.final_shots} shots, seed {seeds['final_batch']}")
    t_fin = time.perf_counter()
    counts = sampler.counts(theta, settings.final_shots, seeds["final_batch"])
    raw = _counts_bytes(counts)
    sha = hashlib.sha256(raw).hexdigest()
    if artifacts_dir is not None:
        ad = Path(artifacts_dir)
        ad.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(ad / "final_counts.json", raw)
        atomic_write_bytes(ad / "final_counts.sha256", (sha + "  final_counts.json\n").encode("ascii"))
    d = decoder.decode(counts)
    fb, bi = final_batch_record(d, settings.final_shots, seeds["final_batch"], sha)
    runtime["final_batch"] = round(time.perf_counter() - t_fin, 4)
    runtime["total"] = round(time.perf_counter() - t0, 4)

    if bi is None:
        status, detail, best = "no_feasible_sample", (
            f"none of the {settings.final_shots} final-batch shots ({len(d.strings)} distinct bitstrings) satisfied the constraints; "
            "this is NOT a proof of infeasibility"
        ), None
    else:
        best = np.array([int(ch) for ch in d.decision[bi]], dtype=np.int64)
        status, detail = "ok", None
    if stopped == "deadline":
        status = "time_limited" if status == "ok" else status
        detail = ((detail + "; ") if detail else "") + "optimisation stopped early by the run time limit"

    used = tr.n
    details = QaoaDetails(
        depth=depth,
        optimizer=settings.optimizer,
        optimizer_settings=opt_settings,
        init_strategy=settings.init_strategy,
        init_description=init_desc,
        estimator=settings.estimator,
        cvar_alpha=settings.cvar_alpha if settings.estimator == "cvar" else None,
        primitive="qiskit_aer.primitives.SamplerV2",
        simulator_method="statevector" + (" (noisy trajectories)" if noise_model is not None else ""),
        noise=settings.noise,
        noise_model=noise_label,
        circuit=circ.stats if noisy_circuit is None else circuit_stats(noisy_circuit, circ.transpile_s, 1, sorted({i.operation.name for i in noisy_circuit.data})),
        initial_parameters=[float(v) for v in x0],
        optimal_parameters=[float(v) for v in theta],
        parameter_order=[f"gamma[{i}]" for i in range(depth)] + [f"beta[{i}]" for i in range(depth)],
        evaluations_used=used,
        evaluation_budget=int(settings.max_evaluations),
        preparation_evaluations=prep_evals,
        iterations=iterations,
        final_batch=fb,
        seeds=seeds,
    )
    work = {
        "evaluations": used,
        "preparation_evaluations": prep_evals,
        "shots_per_evaluation": settings.shots,
        "total_shots": (used + prep_evals) * settings.shots + settings.final_shots,
        "distinct_bitstrings_decoded": len(decoder._cache),
        "hamiltonian_scale": scale,
    }
    notes = [
        f"Energies are unscaled QUBO energies; the circuit uses H scaled by s = {scale:.6g}.",
        "Optimal parameters = " + (opt_settings.get("result_rule") or "best evaluated parameters") + ".",
    ]
    return QaoaOutcome(
        details=details,
        status=status,
        status_detail=detail,
        best_decision=best,
        runtime=runtime,
        work=work,
        notes=notes,
        circuits=circ,
        optimal_parameters=theta,
    )


def _split_params(circuit, depth: int):
    by_name = {prm.name: prm for prm in circuit.parameters}
    gamma = [by_name[f"gamma[{i}]"] for i in range(depth)]
    beta = [by_name[f"beta[{i}]"] for i in range(depth)]
    return gamma, beta


__all__ = ["run_qaoa", "make_circuits", "QaoaOutcome", "Circuits", "BudgetExhausted", "DeadlineReached", "seeds_for", "final_batch_record", "best_feasible"]
