"""QuantFolio experiment / benchmark CLI (quantum-core workstream).

Runs locally against the cached dataset on the Aer simulator only (never IBM
hardware, never a network call). Outputs go to <repo>/evidence/experiments/.

  python scripts/experiments.py --dry-run              # print the plan, run nothing
  python scripts/experiments.py --matrix               # bounded experiment matrix (+ noise on the selected config)
  python scripts/experiments.py --benchmark-qubits     # qubit-cap benchmark -> qubit_benchmark.json + runtime model
  python scripts/experiments.py --calibrate-presets    # preset wall times (median / p95) -> presets_calibration.json
  python scripts/experiments.py --all                  # benchmark, calibration, then matrix

Matrix (fixed, bounded):
  A. full factorial on a 6-asset universe (K=3): depth 1..5 x {COBYLA, SPSA} x {random, warm_interp} x seeds {1,2,3}
     = 60 cells with a small budget (30 evaluations x 256 shots, 1024 final shots); noise comparison
     (FakeTorino, 5 paired trials) on the configuration chosen by the training-only selection rule.
  B. universe-size study at depth 1 (COBYLA, random): 6, 8, 10, 12 assets x seeds {1,2,3}.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
OUT = REPO / "evidence" / "experiments"
TMP = REPO / ".tmp" / "experiments"
sys.path.insert(0, str(BACKEND))

from quantfolio import pipeline  # noqa: E402
from quantfolio.contracts import ExperimentConfig, RunConfig  # noqa: E402
from quantfolio.presets import CALIBRATION_FILE, apply_preset, default_example_config  # noqa: E402

SEEDS = [1, 2, 3]
SIZE_STUDY_EXTRA = ["RELIANCE", "LT", "MARUTI", "SUNPHARMA"]  # appended to the default 8 symbols
SIZE_STUDY_SIZES = [6, 8, 10, 12]


# --------------------------------------------------------------------------- #
# configs
# --------------------------------------------------------------------------- #
def _small_budget(cfg: RunConfig, **qaoa) -> RunConfig:
    q = cfg.qaoa.model_copy(update={"shots": 256, "final_shots": 1024, "max_evaluations": 30, **qaoa})
    return cfg.model_copy(update={"qaoa": q, "timeout_seconds": 600, "preset": "custom"})


def matrix_configs() -> dict[str, ExperimentConfig]:
    base = default_example_config()
    fact_base = _small_budget(base).model_copy(update={"noise_comparison": True, "noise_trials": 5})
    factorial = ExperimentConfig(
        base=fact_base,
        depths=[1, 2, 3, 4, 5],
        optimizers=["COBYLA", "SPSA"],
        init_strategies=["random", "warm_interp"],
        seeds=SEEDS,
        universe_sizes=[6],
        design="full_factorial",
        max_cells=60,
    )
    size_base = _small_budget(base).model_copy(
        update={"symbols": list(base.symbols) + SIZE_STUDY_EXTRA, "constraints": base.constraints.model_copy(update={"cardinality": 6})}
    )
    sizes = ExperimentConfig(
        base=size_base,
        depths=[],
        optimizers=[],
        init_strategies=[],
        seeds=SEEDS,
        universe_sizes=SIZE_STUDY_SIZES,
        design="one_factor_at_a_time",
        max_cells=len(SIZE_STUDY_SIZES) * len(SEEDS),
    )
    return {"factorial_small_universe": factorial, "universe_size": sizes}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class PrintSink(pipeline.NullSink):
    def __init__(self, quiet_logs: bool = False):
        self.quiet_logs = quiet_logs

    def stage(self, stage, message="", **data):
        if stage == "experiment" and message.startswith("cell"):
            print(f"  {message}", flush=True)

    def log(self, level, message, **data):
        if not self.quiet_logs or level != "info":
            print(f"    [{level}] {message}", flush=True)


def machine() -> dict:
    import psutil

    return {
        "platform": platform.platform(terse=True),
        "processor": platform.processor() or platform.machine(),
        "logical_cpus": psutil.cpu_count(logical=True),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
    }


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    print(f"wrote {path.relative_to(REPO)}")


def merge_calibration(update: dict) -> None:
    cur = {}
    if CALIBRATION_FILE.exists():
        cur = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    cur.update(update)
    CALIBRATION_FILE.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    print(f"updated {CALIBRATION_FILE.relative_to(REPO)}")


def fmt(ms: dict, nd: int = 3) -> str:
    if not ms or ms.get("mean") is None:
        return "n/a"
    return f"{ms['mean']:.{nd}f} ± {ms['std']:.{nd}f}"


def _ms(v):
    v = [x for x in v if x is not None]
    if not v:
        return {"mean": None, "std": None, "n": 0}
    return {"mean": statistics.fmean(v), "std": statistics.stdev(v) if len(v) > 1 else 0.0, "n": len(v)}


# --------------------------------------------------------------------------- #
# dry run
# --------------------------------------------------------------------------- #
def dry_run() -> int:
    print("QuantFolio experiment plan (nothing is executed)\n")
    total = 0
    for name, ec in matrix_configs().items():
        cells = pipeline.experiment_cells(ec)
        unique = {(c["depth"], c["optimizer"], c["init_strategy"], c["seed"], c["n_assets"]) for c in cells}
        total += len(unique)
        print(f"[{name}] design={ec.design} cells={len(cells)} unique runs={len(unique)} seeds={ec.seeds}")
        for n in sorted({c["n_assets"] for c in cells}):
            cfg_n = pipeline._variant(ec.base, n)
            pf = pipeline.preflight(cfg_n)
            depths = sorted({c["depth"] for c in cells if c["n_assets"] == n})
            print(
                f"  n={n:>2} K={cfg_n.constraints.cardinality} qubits={pf.total_qubits} ({pf.decision_bits}+{pf.slack_bits} slack) "
                f"depths={depths} est/run(p=1)={pf.estimated_runtime_s}s ok={pf.ok}"
            )
        q = ec.base.qaoa
        print(f"  budget: {q.max_evaluations} evaluations x {q.shots} shots, final batch {q.final_shots} shots; noise comparison={ec.base.noise_comparison} trials={ec.base.noise_trials}")
    print(f"\nunique QAOA runs: {total}; outputs -> {OUT.relative_to(REPO)}")
    return 0


# --------------------------------------------------------------------------- #
# matrix
# --------------------------------------------------------------------------- #
def marginal_tables(cells) -> dict[str, list[dict]]:
    out = {}
    for factor, attr in (("depth", "depth"), ("optimizer", "optimizer"), ("init_strategy", "init_strategy")):
        rows = []
        for level in sorted({getattr(c, attr) for c in cells}, key=str):
            cs = [c for c in cells if getattr(c, attr) == level]
            rows.append(
                {
                    "level": level,
                    "cells": len(cs),
                    "approximation_ratio": _ms([c.approximation_ratio if c.best_objective is not None else 0.0 for c in cs]),
                    "feasible_probability": _ms([c.feasible_probability for c in cs]),
                    "optimum_found_rate": sum(1 for c in cs if c.gap_abs is not None and abs(c.gap_abs) <= 1e-9) / len(cs),
                    "evaluations_total": _ms([c.evaluations + c.preparation_evaluations for c in cs]),
                    "runtime_s": _ms([c.runtime_s for c in cs]),
                }
            )
        out[factor] = rows
    return out


def run_matrix() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pipeline.warmup()
    md = [f"# Experiment matrix results\n", f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `backend/scripts/experiments.py --matrix`.", ""]
    md.append(f"Machine: `{json.dumps(machine())}`\n")
    md.append("All runs: Aer `SamplerV2` statevector simulator; estimates and selection use the training window only; ")
    md.append("approximation ratio = (f_worst_feasible − f) / (f_worst_feasible − f_opt) over the exactly enumerated feasible set (1 = optimum; a run with no feasible sample counts as 0 in the marginal tables).\n")
    rows_csv = []
    for name, ec in matrix_configs().items():
        print(f"== {name}: {len(pipeline.experiment_cells(ec))} cells", flush=True)
        t0 = time.perf_counter()
        res = pipeline.run_experiment(ec, f"exp-{name}", PrintSink(quiet_logs=True), str(TMP / name))
        dt = time.perf_counter() - t0
        (OUT / f"{name}.json").write_text(res.model_dump_json(indent=1), encoding="utf-8")
        print(f"wrote evidence/experiments/{name}.json ({dt:.0f}s)")
        for c in res.cells:
            rows_csv.append({"study": name, **c.model_dump(mode="json")})
        md.append(f"## {name}\n")
        ds, d = res.dataset, ec.base.dates
        md.append(f"Dataset `{ds.dataset_id}` (label: {ds.label}; sha256 `{ds.sha256[:16]}...`), training window {d.train_start}..{d.train_end}.\n")
        md.append(f"Design `{ec.design}`, {len(res.cells)} cells, seeds {ec.seeds}, budget {ec.base.qaoa.max_evaluations} evaluations × {ec.base.qaoa.shots} shots, final batch {ec.base.qaoa.final_shots} shots. Wall time {dt:.0f} s.\n")
        if ec.design == "full_factorial":
            for factor, rows in marginal_tables(res.cells).items():
                md.append(f"### Marginal over {factor} (mean ± std over all other factor levels and seeds)\n")
                md.append("| level | cells | approx. ratio | feasible prob. | optimum found | evaluations (incl. prep) | runtime s |")
                md.append("|---|---|---|---|---|---|---|")
                for r in rows:
                    md.append(
                        f"| {r['level']} | {r['cells']} | {fmt(r['approximation_ratio'])} | {fmt(r['feasible_probability'])} | {r['optimum_found_rate']:.2f} | {fmt(r['evaluations_total'], 1)} | {fmt(r['runtime_s'], 2)} |"
                    )
                md.append("")
        md.append("### Per configuration (mean ± std over seeds)\n")
        md.append("| level | cells | qubits | approx. ratio | feasible prob. | optimum found | runtime s |")
        md.append("|---|---|---|---|---|---|---|")
        for s in res.summary:
            md.append(f"| {s['factor']}={s['level']} | {s['cells']} | {s['total_qubits']} | {fmt(s['approximation_ratio'])} | {fmt(s['feasible_probability'])} | {s['optimum_found_rate']:.2f} | {fmt(s['runtime_s'], 2)} |")
        md.append("")
        ratios = [s["approximation_ratio"]["mean"] for s in res.summary if s["approximation_ratio"]["n"]]
        if len(ratios) > 1 and min(ratios) > 1 - 1e-9:
            md.append(
                "**Reading this table:** every configuration found the exact optimum for every seed, so the approximation ratio does not separate "
                "them at this problem size and final-batch size; the differences are in the final-batch feasible probability, whose seed-to-seed "
                "spread (the +/- column, 3 seeds) is often as large as the differences between configurations. This study does not show any "
                "configuration to be better at finding the optimum.\n"
            )
        if res.selected_configuration:
            md.append(f"**Selected (training-only rule):** `{json.dumps(res.selected_configuration)}`\n")
            md.append(f"Rule: {res.selection_rule}\n")
        if res.noise_comparison:
            nc = res.noise_comparison
            s = nc.summary
            md.append(f"### Noise comparison ({nc.backend}, {s['trials']} paired trials × {nc.shots} shots, fixed trained parameters)\n")
            md.append(f"- noise model: {nc.noise_model_summary}")
            md.append(f"- feasible probability ideal {fmt(s['feasible_probability_ideal'])}, noisy {fmt(s['feasible_probability_noisy'])}, paired delta {fmt(s['delta_feasible_probability'])}")
            md.append(f"- best feasible objective ideal {fmt(s['best_objective_ideal'], 5)}, noisy {fmt(s['best_objective_noisy'], 5)}, paired delta {fmt(s['delta_best_objective'], 5)}")
            md.append(f"- noisy trials without any feasible sample: {s['noisy_trials_without_feasible_sample']}\n")
        if res.warnings:
            md.append("Warnings: " + "; ".join(res.warnings) + "\n")
    with (OUT / "cells.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()))
        w.writeheader()
        w.writerows(rows_csv)
    print("wrote evidence/experiments/cells.csv")
    (OUT / "RESULTS.md").write_text("\n".join(md), encoding="utf-8")
    print("wrote evidence/experiments/RESULTS.md")
    return 0


# --------------------------------------------------------------------------- #
# qubit benchmark
# --------------------------------------------------------------------------- #
def _peak_rss_mb() -> float:
    """OS-tracked peak resident set of THIS process (a sampling thread would miss
    the peak: Aer holds the GIL while it simulates)."""
    import psutil

    mi = psutil.Process().memory_info()
    if hasattr(mi, "peak_wset"):  # Windows
        return round(mi.peak_wset / 2**20, 1)
    import resource

    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(ru / (2**20 if sys.platform == "darwin" else 2**10), 1)


def _bench_sampler(kind: str, n: int, depth: int = 1):
    from quantfolio.quantum.circuit import parameter_permutation
    from quantfolio.quantum.noise import DEFAULT_FAKE_BACKEND, noisy_setup
    from quantfolio.quantum.qaoa import _split_params, make_circuits
    from quantfolio.quantum.sampling import AerSampler

    c = make_circuits(_bench_problem(n), depth, 7)
    if kind == "ideal":
        return AerSampler(c.tqc, c.perm), {}
    t0 = time.perf_counter()
    setup = noisy_setup(c.logical, DEFAULT_FAKE_BACKEND, 7)
    info = {"noise_setup_seconds": round(time.perf_counter() - t0, 2), "noisy_circuit_ops": int(setup.circuit.size()), "active_physical_qubits": len(setup.active_physical)}
    perm = parameter_permutation(setup.circuit, *_split_params(setup.circuit, depth))
    return AerSampler(setup.circuit, perm, noise_model=setup.noise_model), info


def _mem_probe(kind: str, n: int) -> int:
    """Run in a fresh process: one depth-1 evaluation at n qubits, then print the process peak RSS."""
    import numpy as np

    pipeline.warmup()
    sampler, _ = _bench_sampler(kind, n)
    before = _peak_rss_mb()
    sampler.counts(np.full(2, 0.4), 256 if kind == "ideal" else 64, 1)
    row = {"kind": kind, "qubits": n, "peak_rss_before_eval_mb": before, "peak_rss_mb": _peak_rss_mb(), "predicted_memory_mb": pipeline.memory_estimate_mb(n, kind == "noisy")}
    print("MEMPROBE " + json.dumps(row), flush=True)
    return 0


def _bench_problem(n: int):
    """Selection problem with the first n usable dataset symbols, K = n//2,
    no inequality constraints -> exactly n qubits."""
    from quantfolio.assemble import assemble
    from quantfolio.contracts import ConstraintsConfig, TransactionCosts
    from quantfolio.data import list_assets
    from quantfolio.qubo import build_qubo

    assets, _ = list_assets()
    syms = [a.symbol for a in assets if a.data_ok][:n]
    base = default_example_config()
    cfg = base.model_copy(update={"symbols": syms, "constraints": ConstraintsConfig(cardinality=n // 2, transaction_costs=TransactionCosts(enabled=False))})
    a = assemble(cfg)
    assert a.problem is not None and not a.errors, [e.message for e in a.errors]
    q = build_qubo(a.problem, 1.0)
    assert q.n == n, (q.n, n)
    return q


MEM_PROBES = [("ideal", 16), ("ideal", 20), ("ideal", 22), ("ideal", 24), ("noisy", 10), ("noisy", 14)]


def benchmark_qubits() -> int:
    import numpy as np
    from scipy.optimize import nnls

    from quantfolio.presets import MAX_QUBITS, MAX_QUBITS_NOISE, MEMORY_LIMIT_MB

    pipeline.warmup()
    rows = []
    rng = np.random.default_rng(0)

    def measure(kind, n, depth, shots, sampler, reps):
        theta = rng.uniform(0, np.pi, 2 * depth)
        sampler.counts(theta, 64, 1)  # first-call overhead excluded
        times = []
        for r in range(reps):
            t0 = time.perf_counter()
            sampler.counts(theta, shots, 100 + r)
            times.append(time.perf_counter() - t0)
        row = {"kind": kind, "qubits": n, "depth": depth, "shots": shots, "seconds_per_evaluation_median": round(statistics.median(times), 4), "repeats": reps}
        rows.append(row)
        print(json.dumps(row), flush=True)
        return row

    for n in [8, 12, 16, 20, 22, 24]:
        for depth in [1, 3] if n <= 20 else [1]:
            measure("ideal", n, depth, 256, _bench_sampler("ideal", n, depth)[0], 3 if n <= 20 else 2)
        if n == 12:
            measure("ideal", n, 1, 4096, _bench_sampler("ideal", n)[0], 3)
    for n in [6, 8, 10, 12, 14]:
        sampler, info = _bench_sampler("noisy", n)
        measure("noisy", n, 1, 256, sampler, 2).update(info)

    # peak memory, one fresh process per point (OS high-water mark)
    memory_rows = []
    for kind, n in MEM_PROBES:
        out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_mem-probe", kind, str(n)], check=True, cwd=str(BACKEND), capture_output=True, text=True).stdout
        row = json.loads(next(ln for ln in out.splitlines() if ln.startswith("MEMPROBE "))[len("MEMPROBE ") :])
        memory_rows.append(row)
        print(json.dumps(row), flush=True)

    # runtime model t = c0 + c_amp * depth * 2^n * n + c_shot * shots (ideal), fitted on RELATIVE error
    # (rows scaled by 1/t) with non-negative coefficients, so small circuits are not swamped by the 24-qubit row
    ideal = [r for r in rows if r["kind"] == "ideal"]
    A = np.array([[1.0, r["depth"] * 2.0 ** r["qubits"] * r["qubits"], r["shots"]] for r in ideal])
    b = np.array([r["seconds_per_evaluation_median"] for r in ideal])
    (c0, c_amp, c_shot), _ = nnls(A / b[:, None], np.ones(len(b)))
    noisy = [r for r in rows if r["kind"] == "noisy"]
    An = np.array([[r["shots"] * r["depth"] * 2.0 ** r["qubits"] * r["qubits"]] for r in noisy])
    tn = np.array([r["seconds_per_evaluation_median"] for r in noisy])
    (c_noisy,), _ = nnls(An / tn[:, None], np.maximum(tn - c0 - c_shot * 256, 0.0) / tn)
    for r, pred in zip(ideal, A @ np.array([c0, c_amp, c_shot])):
        r["model_over_measured"] = round(float(pred / r["seconds_per_evaluation_median"]), 2)
    for r, pred in zip(noisy, An[:, 0] * c_noisy + c0 + c_shot * 256):
        r["model_over_measured"] = round(float(pred / r["seconds_per_evaluation_median"]), 2)
    model = {
        "c0": float(c0),
        "c_amp": float(c_amp),
        "c_shot": float(c_shot),
        "c_noisy": float(c_noisy),
        "form": "ideal: c0 + c_amp*depth*2^n*n + c_shot*shots; noisy: c0 + shots*(c_noisy*depth*2^n*n) + c_shot*shots",
        "fit": "non-negative least squares on relative error (each row scaled by 1/measured seconds)",
    }
    t_cap = next(r for r in rows if r["kind"] == "ideal" and r["qubits"] == MAX_QUBITS)
    t_noise = next(r for r in rows if r["kind"] == "noisy" and r["qubits"] == MAX_QUBITS_NOISE)
    t_noise_prev = next(r for r in rows if r["kind"] == "noisy" and r["qubits"] == MAX_QUBITS_NOISE - 2)
    m_cap = next(r for r in memory_rows if r["kind"] == "ideal" and r["qubits"] == MAX_QUBITS)
    m_noise = next(r for r in memory_rows if r["kind"] == "noisy" and r["qubits"] == MAX_QUBITS_NOISE)
    basis = (
        f"Measured on this machine: an ideal depth-1 evaluation (256 shots) takes {t_cap['seconds_per_evaluation_median']:.2f} s at {MAX_QUBITS} qubits "
        f"and a fresh process peaks at {m_cap['peak_rss_mb']:.0f} MB resident (preflight predicts {m_cap['predicted_memory_mb']:.0f} MB, deliberately conservative). "
        f"One more qubit doubles the statevector: the prediction for {MAX_QUBITS + 1} qubits is {pipeline.memory_estimate_mb(MAX_QUBITS + 1, False):.0f} MB, "
        f"above MEMORY_LIMIT_MB = {MEMORY_LIMIT_MB} (set for a 16 GB laptop with about 3.5 GB free), hence MAX_QUBITS = {MAX_QUBITS}. "
        f"Noisy (fake-backend) simulation runs one trajectory per shot: {t_noise_prev['seconds_per_evaluation_median']:.2f} s per 256-shot evaluation at "
        f"{MAX_QUBITS_NOISE - 2} qubits and {t_noise['seconds_per_evaluation_median']:.2f} s at {MAX_QUBITS_NOISE} qubits (peak {m_noise['peak_rss_mb']:.0f} MB), "
        f"i.e. x{t_noise['seconds_per_evaluation_median'] / t_noise_prev['seconds_per_evaluation_median']:.1f} for two more qubits, hence MAX_QUBITS_NOISE = {MAX_QUBITS_NOISE}."
    )
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": machine(),
        "conditions": "one process, Aer statevector via SamplerV2, first call excluded; other development processes were running on the same laptop",
        "rows": rows,
        "memory_rows": memory_rows,
        "memory_method": "peak resident set reported by the OS for a fresh process that loads the data, builds the circuit and runs one depth-1 evaluation",
        "eval_model": model,
        "qubit_caps_basis": basis,
    }
    write_json(OUT / "qubit_benchmark.json", out)
    merge_calibration({"eval_model": model, "qubit_caps_basis": basis})
    return 0


# --------------------------------------------------------------------------- #
# preset calibration
# --------------------------------------------------------------------------- #
REPEATS = {"quick": 5, "balanced": 3, "research": 3}


def calibrate_presets() -> int:
    import numpy as np

    out_presets = {}
    # cold: a fresh process (imports + data load + run), as a worker without warm-up would see it
    cold = []
    for r in range(2):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_cold-quick"], check=True, cwd=str(BACKEND))
        cold.append(round(time.perf_counter() - t0, 2))
    w = pipeline.warmup()
    for name, reps in REPEATS.items():
        cfg = apply_preset(default_example_config(), name)
        times = []
        for r in range(reps):
            t0 = time.perf_counter()
            res = pipeline.run_optimisation(cfg, f"calib-{name}-{r}", pipeline.NullSink(), str(TMP / "calibration" / f"{name}-{r}"))
            times.append(time.perf_counter() - t0)
            q = next(s for s in res.solvers if s.solver == "qaoa")
            print(f"{name} #{r + 1}: {times[-1]:.2f}s qaoa={q.status} gap={None if q.gap is None else q.gap.absolute}", flush=True)
        out_presets[name] = {
            "median_s": round(float(np.median(times)), 2),
            "p95_s": round(float(np.percentile(times, 95)), 2),
            "min_s": round(min(times), 2),
            "max_s": round(max(times), 2),
            "repeats": reps,
            "runs_s": [round(t, 2) for t in times],
            "machine": f"{machine()['processor']}, {machine()['logical_cpus']} logical CPUs",
            "measured_at": datetime.now(timezone.utc).date().isoformat(),
            "conditions": "warm worker process (pipeline.warmup() done), default example config with the preset applied, Aer statevector",
        }
    out_presets["quick"]["cold_process_s"] = cold
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "machine": machine(), "warmup_s": w, "presets": out_presets}
    write_json(OUT / "presets_calibration.json", report)
    merge_calibration({"presets": out_presets})
    return 0


def _cold_quick() -> int:
    cfg = apply_preset(default_example_config(), "quick")
    pipeline.run_optimisation(cfg, "calib-cold", pipeline.NullSink(), str(TMP / "calibration" / "cold"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan without running anything")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--benchmark-qubits", action="store_true")
    ap.add_argument("--calibrate-presets", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--_cold-quick", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_mem-probe", nargs=2, metavar=("KIND", "QUBITS"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a._cold_quick:
        return _cold_quick()
    if a._mem_probe:
        kind, n = a._mem_probe
        if kind not in ("ideal", "noisy") or not n.isdigit() or not 2 <= int(n) <= 24:
            ap.error("invalid --_mem-probe arguments")
        return _mem_probe(kind, int(n))
    if a.dry_run or not (a.matrix or a.benchmark_qubits or a.calibrate_presets or a.all):
        return dry_run()
    if a.benchmark_qubits or a.all:
        benchmark_qubits()
    if a.calibrate_presets or a.all:
        calibrate_presets()
    if a.matrix or a.all:
        run_matrix()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
