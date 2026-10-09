"""Pipeline level: preflight, cache keys, end-to-end provenance, cancellation,
the no-feasible-sample path and held-out isolation. Synthetic data, tiny circuits."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import BACKEND, SYM6, TINY_QAOA, make_config, median_feasible_return, problem_of

from quantfolio import pipeline
from quantfolio.assemble import assemble
from quantfolio.contracts import BaselineSettings, ExperimentConfig, LotConfig, RunConfig, RunResult, ScreeningConfig, SectorCaps
from quantfolio.data.store import SYNTHETIC_ID, content_sha256, get_dataset
from quantfolio.pipeline import Cancelled, NullSink, PipelineError
from quantfolio.portfolio import decision_string, evaluate
from quantfolio.presets import DEFAULT_DATES, MAX_QUBITS, MAX_QUBITS_NOISE
from quantfolio.qubo import build_qubo

SYM8 = SYM6 + ["RELIANCE", "SUNPHARMA"]
FS1 = SectorCaps(basis="count", per_sector={"Financial Services": 1})
SOLVERS = ["qaoa", "exact", "relaxation_rounding", "simulated_annealing", "greedy"]


def _with_target(cfg: RunConfig) -> RunConfig:
    return cfg.model_copy(update={"constraints": cfg.constraints.model_copy(update={"target_return": median_feasible_return(cfg)})})


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def test_preflight_reports_resources_without_importing_the_quantum_stack():
    code = (
        "import sys, json; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s');"
        "from conftest import make_config; from quantfolio import pipeline;"
        "r = pipeline.preflight(make_config());"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & {'qiskit', 'qiskit_aer', 'qiskit_ibm_runtime', 'cvxpy'});"
        "print(json.dumps({'ok': r.ok, 'qubits': r.total_qubits, 'heavy': heavy}))"
    ) % (BACKEND, Path(__file__).parent)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(BACKEND), check=True).stdout
    assert json.loads(out.strip().splitlines()[-1]) == {"ok": True, "qubits": 6, "heavy": []}


def test_preflight_counts_match_the_built_qubo(cases):
    for name, cfg in cases.items():
        r = pipeline.preflight(cfg)
        q = build_qubo(problem_of(cfg))
        assert r.ok and r.can_submit and r.conflict_kind is None, name
        assert (r.decision_bits, r.total_qubits) == (q.n_decision, q.n) and r.slack_bits == q.n - q.n_decision
        assert r.memory_estimate_mb == pipeline.memory_estimate_mb(q.n, False) >= 350
        assert r.estimated_runtime_s > 0 and r.max_qubits == MAX_QUBITS
        assert r.dataset_id == SYNTHETIC_ID and r.dataset_label == "synthetic test"
        assert sum(c.slack_bits for c in r.constraints) == r.slack_bits
        assert "synthetic_data" in {i.code for i in r.issues}  # never presented as market data


ALL12 = ["HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY", "RELIANCE", "ONGC", "SUNPHARMA", "CIPLA", "ITC", "HINDUNILVR", "LT"]
LOTS16 = dict(mode="lots", lots=LotConfig(total_lots=8, max_lots_per_asset=3))  # 8 assets x 2 bits = 16 qubits


@pytest.mark.parametrize(
    "cfg, kind, code",
    [
        (make_config(K=7), "proven_infeasible", "cardinality_exceeds_universe"),
        (make_config(target=4.0), "proven_infeasible", "target_return_unreachable"),
        (make_config(K=4, sector_caps=SectorCaps(basis="count", default_max=1)), "proven_infeasible", "sector_caps_infeasible"),
        (make_config(SYM6[:3], mode="lots", lots=LotConfig(total_lots=4, max_lots_per_asset=3), sector_caps=SectorCaps(basis="count", default_max=2)), "preflight_conflict", "unsupported_constraint_for_mode"),
        (make_config(ALL12, mode="lots", lots=LotConfig(total_lots=12, max_lots_per_asset=7)), "resource_limit", "too_many_qubits"),
        (make_config(ALL12[:8], qaoa=TINY_QAOA.model_copy(update={"noise": "fake_backend"}), **LOTS16), "resource_limit", "noise_too_many_qubits"),
        (make_config(ALL12[:8], noise_comparison=True, **LOTS16), "resource_limit", "noise_too_many_qubits"),
        (make_config(qaoa=TINY_QAOA.model_copy(update={"noise": "fake_backend", "fake_backend": "FakeNope"})), "preflight_conflict", "unknown_fake_backend"),
        (make_config(qaoa=TINY_QAOA.model_copy(update={"noise": "fake_backend", "depth": 2, "init_strategy": "warm_interp"})), "preflight_conflict", "warm_interp_noise_unsupported"),
    ],
)
def test_preflight_conflicts_are_classified_and_block_the_run(tmp_dir, cfg, kind, code):
    r = pipeline.preflight(cfg)
    errors = [i for i in r.issues if i.severity == "error"]
    assert not r.ok and not r.can_submit and r.conflict_kind == kind
    assert code in {i.code for i in errors} and all(i.message for i in errors)
    if code == "too_many_qubits":
        assert r.total_qubits == 36 > MAX_QUBITS
    if code == "noise_too_many_qubits":
        assert MAX_QUBITS_NOISE < r.total_qubits <= MAX_QUBITS
    # the same conflict stops a submitted run before any circuit is built
    with pytest.raises(PipelineError) as e:
        pipeline.run_optimisation(cfg, "t-conflict", NullSink(), str(tmp_dir))
    assert e.value.details["conflict_kind"] == kind and not (tmp_dir / "final_counts.json").exists()


def test_malformed_configs_are_rejected_by_the_contract():
    import pydantic

    base = make_config().model_dump(mode="json")
    bad = [
        {"bogus": 1},
        {"symbols": ["TCS", "TCS", "ITC"]},
        {"symbols": ["../etc", "TCS", "ITC"]},
        {"symbols": ["TCS"]},
        {"budget_inr": -5},
        {"risk_aversion": float("nan")},
        {"timeout_seconds": 10**7},
        {"dates": {**base["dates"], "train_end": "2021-01-01"}},
        {"dates": {**base["dates"], "validation_start": "2024-06-01"}},  # validation overlapping training
        {"constraints": {**base["constraints"], "cardinality": 0}},
        {"qaoa": {**base["qaoa"], "shots": 10**9}},
        {"qaoa": {**base["qaoa"], "depth": 9}},
        {"qaoa": {**base["qaoa"], "optimizer": "ADAM"}},
    ]
    for upd in bad:
        with pytest.raises(pydantic.ValidationError):
            RunConfig.model_validate({**base, **upd})
    # a path-like dataset id is never turned into a file path: it is simply unknown
    evil = make_config().model_copy(update={"dataset_id": "../../secrets"})
    with pytest.raises(PipelineError) as e:
        pipeline.cache_key(evil)
    assert e.value.code == "unknown_dataset"
    assert not pipeline.preflight(evil).ok


# --------------------------------------------------------------------------- #
# cache key
# --------------------------------------------------------------------------- #
def test_cache_key_changes_with_every_input(monkeypatch):
    base = make_config(K=3, sector_caps=FS1, target=0.05)
    c = base.constraints
    variants = {
        "cardinality": base.model_copy(update={"constraints": c.model_copy(update={"cardinality": 2})}),
        "target_return": base.model_copy(update={"constraints": c.model_copy(update={"target_return": 0.06})}),
        "sector_cap": base.model_copy(update={"constraints": c.model_copy(update={"sector_caps": SectorCaps(basis="count", per_sector={"Financial Services": 2})})}),
        "cost_bps": base.model_copy(update={"constraints": c.model_copy(update={"transaction_costs": c.transaction_costs.model_copy(update={"proportional_bps": 30.0})})}),
        "risk_aversion": base.model_copy(update={"risk_aversion": 2.5}),
        "seed": base.model_copy(update={"qaoa": base.qaoa.model_copy(update={"seed": 4})}),
        "shots": base.model_copy(update={"qaoa": base.qaoa.model_copy(update={"shots": 128})}),
        "symbols": base.model_copy(update={"symbols": SYM6[:5]}),
        "train_end": base.model_copy(update={"dates": base.dates.model_copy(update={"train_end": DEFAULT_DATES.train_end.replace(day=30)})}),
        "noise": base.model_copy(update={"noise_comparison": True}),
    }
    h0, k0 = pipeline.cache_key(base)
    assert pipeline.cache_key(make_config(K=3, sector_caps=FS1, target=0.05)) == (h0, k0)  # equal content -> equal key
    keys = {name: pipeline.cache_key(cfg) for name, cfg in variants.items()}
    assert len({k0, *(k for _, k in keys.values())}) == len(variants) + 1, "two different configs share a cache key"
    assert len({h0, *(h for h, _ in keys.values())}) == len(variants) + 1
    # same request, different data -> same config hash, different cache key
    ds = get_dataset(SYNTHETIC_ID)
    other = dataclasses.replace(ds, info=ds.info.model_copy(update={"sha256": "0" * 64}))
    monkeypatch.setattr("quantfolio.data.get_dataset", lambda dataset_id=None: other)
    h1, k1 = pipeline.cache_key(base)
    assert h1 == h0 and k1 != k0


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
class RecordingSink(NullSink):
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.points: list[dict] = []

    def stage(self, stage: str, message: str = "", **data) -> None:
        self.stages.append(stage)

    def iteration(self, point: dict) -> None:
        self.points.append(point)


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    cfg = _with_target(make_config(SYM6, K=3, sector_caps=FS1, holdings={"HDFCBANK": 0.5, "TCS": 0.5}, noise_comparison=True, noise_trials=2))
    cfg = cfg.model_copy(update={"qaoa": TINY_QAOA.model_copy(update={"depth": 2, "init_strategy": "warm_interp", "max_evaluations": 8, "seed": 11})})
    art = Path(pipeline.__file__).resolve().parents[2] / ".tmp" / "core-tests" / "e2e"
    art.mkdir(parents=True, exist_ok=True)
    sink = RecordingSink()
    res = pipeline.run_optimisation(cfg, "t-e2e", sink, str(art))
    return cfg, res, art, sink


def test_run_result_provenance(e2e):
    cfg, res, art, sink = e2e
    p = problem_of(cfg)
    qubo = build_qubo(p, cfg.qaoa.penalty_scale)
    assert [s.solver for s in res.solvers] == SOLVERS
    assert res.dataset.label == "synthetic test" and res.dataset.sha256 == get_dataset(SYNTHETIC_ID).info.sha256
    assert (res.config_hash, res.cache_key) == pipeline.cache_key(cfg) and res.config == cfg
    assert (res.qubo.n_decision, res.qubo.n_variables) == (qubo.n_decision, qubo.n) and res.encoded_symbols == p.symbols

    # raw counts on disk are the ones the result refers to
    raw = (art / "final_counts.json").read_bytes()
    counts = json.loads(raw)
    fb = res.qaoa.final_batch
    assert hashlib.sha256(raw).hexdigest() == fb.counts_sha256 and sum(counts.values()) == fb.shots == cfg.qaoa.final_shots

    # every reported portfolio is recomputed here from its bitstring by the shared validator
    for s in res.solvers:
        assert s.status == "ok", (s.solver, s.status, s.status_detail)
        x = np.array([int(ch) for ch in s.bitstring], dtype=np.int64)
        pe = evaluate(p, x)
        assert pe.feasibility.feasible and s.portfolio.feasibility.feasible
        assert abs(pe.objective - s.portfolio.objective) < 1e-12 and abs(pe.cost_inr - s.portfolio.cost_inr) < 1e-9
        assert s.portfolio.validation.n_obs > 0 and s.portfolio.test.n_obs > 0
        assert s.gap is not None and s.gap.reference == "exact_optimum" and s.gap.absolute >= -1e-12
    by = {s.solver: s for s in res.solvers}
    assert abs(by["exact"].gap.absolute) < 1e-12

    # the QAOA portfolio is a feasible bitstring that was actually sampled in the final batch
    assert fb.best_bitstring in counts
    assert decision_string(qubo.bits_from_string(fb.best_bitstring)[: qubo.n_decision]) == by["qaoa"].bitstring
    assert abs(by["qaoa"].gap.absolute - (by["qaoa"].portfolio.objective - by["exact"].portfolio.objective)) < 1e-12

    assert len(sink.points) == res.qaoa.evaluations_used + res.qaoa.preparation_evaluations == len(res.qaoa.iterations)
    order = [s for s in ("loading_data", "building_qubo", "baselines", "frontier", "noise", "reporting") if s in sink.stages]
    assert order == ["loading_data", "building_qubo", "baselines", "frontier", "noise", "reporting"]
    assert {pt.kind for pt in res.frontier} >= {"solver"} and len(res.frontier) > len(res.solvers)
    assert RunResult.model_validate_json(res.model_dump_json()).model_dump(mode="json") == res.model_dump(mode="json")
    assert "not investment advice" in res.disclaimer.lower()


def test_noise_comparison_is_paired_and_labelled(e2e):
    cfg, res, _, _ = e2e
    nc = res.noise_comparison
    assert nc is not None and nc.backend == "FakeTorino" and "FakeTorino" in nc.noise_model_summary
    assert len(nc.ideal) == len(nc.noisy) == 2 and [t.seed for t in nc.ideal] == [t.seed for t in nc.noisy]
    assert nc.shots == cfg.qaoa.final_shots and nc.summary["trials"] == 2
    d = nc.summary["delta_feasible_probability"]
    assert d["n"] == 2 and abs(d["mean"] - np.mean([b.feasible_probability - a.feasible_probability for a, b in zip(nc.ideal, nc.noisy)])) < 1e-12
    assert res.qaoa.noise == "ideal"  # the optimisation itself stayed noise-free; only the comparison is noisy


def test_baselines_cannot_influence_the_quantum_result(e2e, tmp_dir):
    cfg, res, _, _ = e2e
    off = cfg.model_copy(update={"noise_comparison": False, "baselines": BaselineSettings(exact=False, relaxation_rounding=False, simulated_annealing=False, greedy=False)})
    alone = pipeline.run_optimisation(off, "t-alone", NullSink(), str(tmp_dir))
    assert alone.qaoa.optimal_parameters == res.qaoa.optimal_parameters
    assert alone.qaoa.final_batch.counts_sha256 == res.qaoa.final_batch.counts_sha256
    a, b = alone.solvers[0], res.solvers[0]
    assert a.solver == b.solver == "qaoa" and a.bitstring == b.bitstring and a.portfolio.objective == b.portfolio.objective
    assert all(s.portfolio is None and s.status != "ok" for s in alone.solvers[1:])
    assert a.gap is None or a.gap.reference == "best_known"  # no exact optimum is claimed when it was not computed


def test_no_feasible_sample_is_not_replaced_by_a_baseline(monkeypatch, tmp_dir):
    from quantfolio.quantum.sampling import AerSampler

    cfg = _with_target(make_config(SYM6, K=3, sector_caps=FS1))
    n = build_qubo(problem_of(cfg)).n
    monkeypatch.setattr(AerSampler, "counts", lambda self, params, shots, seed: {"1" * n: int(shots)})  # everything selected: infeasible
    res = pipeline.run_optimisation(cfg, "t-nofeas", NullSink(), str(tmp_dir))
    by = {s.solver: s for s in res.solvers}
    q = by["qaoa"]
    assert q.status == "no_feasible_sample" and q.portfolio is None and q.bitstring is None and q.gap is None
    assert res.qaoa.final_batch.feasible_probability == 0.0 and res.qaoa.final_batch.best_bitstring is None
    assert by["exact"].status == "ok" and by["exact"].portfolio.feasibility.feasible  # the problem itself is feasible
    assert all(pt.label != "qaoa" for pt in res.frontier if pt.kind == "solver")
    assert json.loads((tmp_dir / "final_counts.json").read_text()) == {"1" * n: cfg.qaoa.final_shots}


def test_cancellation_stops_the_run(tmp_dir):
    class Sink(NullSink):
        def __init__(self) -> None:
            self.calls = 0

        def check_cancelled(self) -> None:
            self.calls += 1
            if self.calls > 4:
                raise Cancelled()

    with pytest.raises(Cancelled):
        pipeline.run_optimisation(make_config(), "t-cancel", Sink(), str(tmp_dir))


# --------------------------------------------------------------------------- #
# held-out isolation
# --------------------------------------------------------------------------- #
def _strip(cell) -> dict:
    return cell.model_dump(exclude={"runtime_s", "validation_sharpe"})


def test_held_out_prices_cannot_change_anything_trained(monkeypatch, tmp_dir):
    """Rewrite every price after train_end. Training estimates, screening, the
    QUBO, the trained circuit, every solver's portfolio and the experiment's
    chosen settings must be bit-identical; only held-out metrics may move."""
    cfg = _with_target(make_config(SYM8, K=3, sector_caps=FS1, screening=ScreeningConfig(rule="top_sharpe_train", keep=5)))
    ec = ExperimentConfig(base=cfg, depths=[1, 2], optimizers=["COBYLA", "SPSA"], init_strategies=["random"], seeds=[1, 2], design="full_factorial", max_cells=8)

    base_ds = get_dataset(SYNTHETIC_ID)
    prices = base_ds.prices.copy()
    after = prices.index > pd.Timestamp(DEFAULT_DATES.train_end)
    rng = np.random.default_rng(42)
    prices.loc[after] = prices.loc[after].to_numpy() * rng.uniform(0.3, 3.0, size=(int(after.sum()), prices.shape[1]))
    changed = dataclasses.replace(base_ds, prices=prices, info=base_ds.info.model_copy(update={"sha256": content_sha256(prices, None)}))
    assert changed.info.sha256 != base_ds.info.sha256

    def run(tag: str):
        r = pipeline.run_optimisation(cfg, f"t-iso-{tag}", NullSink(), str(tmp_dir / tag))
        e = pipeline.run_experiment(ec, f"t-iso-exp-{tag}", NullSink(), str(tmp_dir / f"exp-{tag}"))
        return r, e, pipeline.preflight(cfg)

    r0, e0, pf0 = run("orig")
    monkeypatch.setattr("quantfolio.data.get_dataset", lambda dataset_id=None: changed)
    monkeypatch.setattr("quantfolio.assemble.get_dataset", lambda dataset_id=None: changed)
    r1, e1, pf1 = run("changed")
    assert r1.dataset.sha256 == changed.info.sha256 and r1.cache_key != r0.cache_key  # the changed data really was used

    # training estimates and screening
    a0, a1 = assemble(cfg, dataset=base_ds), assemble(cfg, dataset=changed)
    assert a0.estimate.mu.tobytes() == a1.estimate.mu.tobytes() and a0.estimate.sigma.tobytes() == a1.estimate.sigma.tobytes()
    assert r0.encoded_symbols == r1.encoded_symbols and len(r0.encoded_symbols) == 5
    assert r0.screening == r1.screening and pf0.encoded_symbols == pf1.encoded_symbols and pf0.total_qubits == pf1.total_qubits
    assert r0.estimation.model_dump(exclude={"validation", "test"}) == r1.estimation.model_dump(exclude={"validation", "test"})
    # encoded problem and trained circuit
    assert r0.qubo == r1.qubo
    assert build_qubo(a0.problem).sha256() == build_qubo(a1.problem).sha256()
    assert r0.qaoa.initial_parameters == r1.qaoa.initial_parameters and r0.qaoa.optimal_parameters == r1.qaoa.optimal_parameters
    assert r0.qaoa.final_batch.counts_sha256 == r1.qaoa.final_batch.counts_sha256
    assert (tmp_dir / "orig" / "final_counts.json").read_bytes() == (tmp_dir / "changed" / "final_counts.json").read_bytes()
    # every solver's portfolio and its training-side numbers
    moved = 0
    for s0, s1 in zip(r0.solvers, r1.solvers):
        assert (s0.solver, s0.status, s0.bitstring) == (s1.solver, s1.status, s1.bitstring)
        assert s0.gap == s1.gap
        if s0.portfolio is not None:
            held = {"validation", "test"}
            assert s0.portfolio.model_dump(exclude=held) == s1.portfolio.model_dump(exclude=held)
            moved += s0.portfolio.validation != s1.portfolio.validation and s0.portfolio.test != s1.portfolio.test
    assert moved >= 1, "held-out metrics should react to the rewritten held-out prices"
    # experiment: identical cells and identical chosen settings
    assert [_strip(c) for c in e0.cells] == [_strip(c) for c in e1.cells] and len(e0.cells) == 8
    keep = ("depth", "optimizer", "init_strategy", "n_assets", "seeds", "mean_approximation_ratio", "mean_feasible_probability", "mean_circuit_evaluations")
    assert {k: e0.selected_configuration[k] for k in keep} == {k: e1.selected_configuration[k] for k in keep}
    assert [c.validation_sharpe for c in e0.cells] != [c.validation_sharpe for c in e1.cells]
    assert "test data is never used" in e0.selection_rule


# --------------------------------------------------------------------------- #
# experiment design
# --------------------------------------------------------------------------- #
def test_experiment_designs_are_bounded():
    base = make_config()
    full = ExperimentConfig(base=base, design="full_factorial", max_cells=60)
    cells = pipeline.experiment_cells(full)
    assert len(cells) == 5 * 2 * 2 * 3 == len({(c["depth"], c["optimizer"], c["init_strategy"], c["seed"]) for c in cells})
    ofat = pipeline.experiment_cells(ExperimentConfig(base=base, design="one_factor_at_a_time"))
    assert len(ofat) == (5 + 2 + 2) * 3
    # each one-factor level differs from the base settings in at most that one factor
    q = base.qaoa
    for c in ofat:
        differs = [c["depth"] != q.depth, c["optimizer"] != q.optimizer, c["init_strategy"] != q.init_strategy]
        assert sum(differs) <= 1
    assert len({(c["depth"], c["optimizer"], c["init_strategy"], c["seed"]) for c in ofat}) == (5 + 1 + 1) * 3  # repeated base cells run once
    with pytest.raises(PipelineError) as e:
        pipeline.run_experiment(full.model_copy(update={"max_cells": 10}), "t-big", NullSink(), "unused")
    assert e.value.code == "too_many_cells"


def test_selection_rule_is_deterministic_and_training_only():
    from quantfolio.contracts import ExperimentCell

    def cell(depth, seed, ratio, feas, evals, runtime, sharpe):
        return ExperimentCell(factor="f", depth=depth, optimizer="COBYLA", init_strategy="random", seed=seed, n_assets=6, total_qubits=8, status="ok",
                              feasible_probability=feas, best_objective=-1.0, exact_objective=-1.0, approximation_ratio=ratio, evaluations=evals, runtime_s=runtime, validation_sharpe=sharpe)  # fmt: skip

    cells = [cell(1, 1, 0.90, 0.9, 10, 0.1, 9.0), cell(1, 2, 0.90, 0.9, 10, 0.1, 9.0), cell(2, 1, 1.0, 0.2, 10, 5.0, -3.0), cell(2, 2, 0.95, 0.2, 10, 5.0, -3.0)]
    sel, _ = pipeline.select_configuration(cells)
    assert sel["depth"] == 2  # higher training approximation ratio wins although its validation Sharpe is worse
    # ties on the ratio (to 9 decimals) -> feasible probability; wall time and validation Sharpe never decide
    tie = [cell(1, 1, 1.0, 0.30, 10, 9.0, -5.0), cell(2, 1, 1.0 + 4e-16, 0.20, 10, 0.1, 5.0)]
    assert pipeline.select_configuration(tie)[0]["depth"] == 1
    assert pipeline.select_configuration(list(reversed(tie)))[0]["depth"] == 1
    tie2 = [cell(3, 1, 1.0, 0.30, 12, 0.1, 0.0), cell(2, 1, 1.0, 0.30, 12, 9.0, 0.0), cell(4, 1, 1.0, 0.30, 20, 0.1, 0.0)]
    assert pipeline.select_configuration(tie2)[0]["depth"] == 2  # then fewer evaluations, then lower depth
