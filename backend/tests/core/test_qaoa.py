"""QAOA on real (tiny) Aer circuits: energies, count bookkeeping, provenance, both
optimisers and initialisations at depths 1-5, the no-feasible-sample path and
fake-backend noise. No IBM account or hardware quota is used anywhere."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from conftest import TINY_QAOA, all_bits, make_config, problem_of

from quantfolio.contracts import SectorCaps
from quantfolio.pipeline import NullSink
from quantfolio.portfolio import decision_string
from quantfolio.quantum import warm_start
from quantfolio.quantum.qaoa import make_circuits, run_qaoa, seeds_for
from quantfolio.quantum.sampling import AerSampler, Decoder, estimate_energy
from quantfolio.qubo import build_qubo


@pytest.fixture(scope="module")
def qubo():
    """4 assets, K = 2, at most one Financial Services stock, binding target return -> 4 decision + slack qubits."""
    cfg = make_config(["HDFCBANK", "ICICIBANK", "TCS", "ITC"], K=2, sector_caps=SectorCaps(basis="count", per_sector={"Financial Services": 1}))
    p = problem_of(cfg)
    X = all_bits(p.n)
    X = X[p.feasible_mask(X)]
    target = float(np.round(np.median(p.enc.weights(X) @ p.mu), 6))
    cfg = cfg.model_copy(update={"constraints": cfg.constraints.model_copy(update={"target_return": target})})
    q = build_qubo(problem_of(cfg))
    assert q.n_decision == 4 and 4 < q.n <= 8
    return q


def test_cost_layer_phases_equal_encoded_energies(qubo):
    """With beta = 0 the circuit is H^n then exp(-i gamma s H_C): the phase of basis
    state z must be -gamma * s * (E(z) - ising offset) for every one of the 2^n states."""
    from qiskit.quantum_info import Statevector

    c = make_circuits(qubo, 1, 0)
    gamma = 0.37
    qc = c.logical.remove_final_measurements(inplace=False)
    by_name = {prm.name: prm for prm in qc.parameters}
    sv = Statevector(qc.assign_parameters({by_name["gamma[0]"]: gamma, by_name["beta[0]"]: 0.0})).data
    k = np.arange(1 << qubo.n)
    Z = (k[:, None] >> np.arange(qubo.n)) & 1  # little-endian: qubit q is bit q of the basis index
    _, _, off = qubo.ising()
    expect = np.exp(-1j * gamma * qubo.circuit_scale() * (qubo.energy(Z) - off)) / np.sqrt(1 << qubo.n)
    np.testing.assert_allclose(sv, expect, atol=1e-9)


def test_sampled_energies_match_exhaustive_enumeration(qubo):
    c = make_circuits(qubo, 2, 5)
    counts = AerSampler(c.tqc, c.perm).counts(np.array([0.4, 0.7, 0.3, 0.2]), 2000, seed=11)
    assert sum(counts.values()) == 2000 and all(len(s) == qubo.n for s in counts)
    d = Decoder(qubo).decode(counts)
    table = {qubo.string_from_bits(z): float(e) for z, e in zip(all_bits(qubo.n), qubo.energy(all_bits(qubo.n)))}
    p = qubo.problem
    for i, s in enumerate(d.strings):
        z = qubo.bits_from_string(s)
        x = z[: qubo.n_decision]
        assert abs(d.energy[i] - table[s]) <= 1e-9 * max(1.0, abs(table[s]))
        assert d.decision[i] == decision_string(x)
        assert bool(d.feasible[i]) == bool(p.feasible_mask(x)[0])
        assert abs(d.objective[i] - p.objective(p.enc.weights(x))) <= 1e-12
    mean = float(np.dot(d.counts, d.energy) / d.counts.sum())
    assert abs(estimate_energy(d, "mean", 1.0) - mean) <= 1e-9 * abs(mean)
    assert estimate_energy(d, "cvar", 0.1) <= mean  # CVaR of the lowest 10 % is never above the mean
    # same seed -> identical counts; different seed -> different counts
    assert AerSampler(c.tqc, c.perm).counts(np.array([0.4, 0.7, 0.3, 0.2]), 2000, seed=11) == counts
    assert AerSampler(c.tqc, c.perm).counts(np.array([0.4, 0.7, 0.3, 0.2]), 2000, seed=12) != counts


@pytest.mark.parametrize("depth", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("optimizer", ["COBYLA", "SPSA"])
@pytest.mark.parametrize("init", ["random", "warm_interp"])
def test_qaoa_bookkeeping_and_provenance(qubo, tmp_dir, depth, optimizer, init):
    st = TINY_QAOA.model_copy(update={"depth": depth, "optimizer": optimizer, "init_strategy": init, "shots": 64, "final_shots": 256, "max_evaluations": 8, "seed": 5})
    out = run_qaoa(qubo, st, NullSink(), tmp_dir)
    dt = out.details
    fb = dt.final_batch
    assert (dt.depth, dt.optimizer, dt.init_strategy) == (depth, optimizer, init)
    assert len(dt.optimal_parameters) == len(dt.initial_parameters) == len(dt.parameter_order) == 2 * depth
    assert 1 <= dt.evaluations_used <= dt.evaluation_budget == 8
    # every circuit evaluation is recorded, numbered and attributed to its phase
    assert [pt.evaluation for pt in dt.iterations] == list(range(1, len(dt.iterations) + 1))
    assert sum(pt.phase == "optimisation" for pt in dt.iterations) == dt.evaluations_used
    assert sum(pt.phase == "warm_start_preparation" for pt in dt.iterations) == dt.preparation_evaluations
    if init == "warm_interp" and depth > 1:
        assert 0 < dt.preparation_evaluations <= (depth - 1) * warm_start.preparation_budget(8, depth)
    else:
        assert dt.preparation_evaluations == 0
    assert out.work["total_shots"] == (dt.evaluations_used + dt.preparation_evaluations) * 64 + 256
    assert dt.seeds == seeds_for(5) and fb.seed == dt.seeds["final_batch"]

    # the declared final batch is stored raw and hashed
    raw = (tmp_dir / "final_counts.json").read_bytes()
    counts = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == fb.counts_sha256
    assert (tmp_dir / "final_counts.sha256").read_text().split()[0] == fb.counts_sha256
    assert sum(counts.values()) == fb.shots == 256 and len(counts) == fb.distinct_bitstrings
    p = qubo.problem
    feas = {s: n for s, n in counts.items() if p.feasible_mask(qubo.bits_from_string(s)[: qubo.n_decision])[0]}
    assert abs(fb.feasible_probability - sum(feas.values()) / 256) < 1e-12
    for row in fb.top_samples:
        assert counts[row.bitstring] == row.count

    # the reported portfolio is the best feasible OBSERVED sample, recomputed here from the raw counts
    if feas:
        obj = {s: float(p.objective(p.enc.weights(qubo.bits_from_string(s)[: qubo.n_decision]))) for s in feas}
        assert out.status == "ok" and fb.best_bitstring in feas
        assert obj[fb.best_bitstring] == min(obj.values())
        assert decision_string(out.best_decision) == decision_string(qubo.bits_from_string(fb.best_bitstring)[: qubo.n_decision])
    else:
        assert out.status == "no_feasible_sample" and out.best_decision is None and fb.best_bitstring is None


def test_same_seed_reproduces_and_seed_changes_result(qubo):
    st = TINY_QAOA.model_copy(update={"depth": 2, "optimizer": "SPSA", "seed": 9})
    a = run_qaoa(qubo, st, NullSink(), None)
    b = run_qaoa(qubo, st, NullSink(), None)
    c = run_qaoa(qubo, st.model_copy(update={"seed": 10}), NullSink(), None)
    assert a.details.optimal_parameters == b.details.optimal_parameters
    assert a.details.final_batch.counts_sha256 == b.details.final_batch.counts_sha256
    assert a.details.final_batch.counts_sha256 != c.details.final_batch.counts_sha256


def test_no_feasible_sample_is_reported_not_repaired(qubo, monkeypatch):
    """If the final batch holds only constraint-violating bitstrings QAOA reports
    'no_feasible_sample' and NO portfolio (nothing is repaired or substituted)."""
    infeasible = "0" * qubo.n  # nothing selected: violates the cardinality constraint
    assert not qubo.problem.feasible_mask(qubo.bits_from_string(infeasible)[: qubo.n_decision])[0]
    monkeypatch.setattr(AerSampler, "counts", lambda self, params, shots, seed: {infeasible: int(shots)})
    out = run_qaoa(qubo, TINY_QAOA, NullSink(), None)
    fb = out.details.final_batch
    assert out.status == "no_feasible_sample" and out.best_decision is None
    assert fb.best_bitstring is None and fb.feasible_probability == 0.0 and fb.distinct_bitstrings == 1
    assert "NOT a proof of infeasibility" in out.status_detail
    assert all(pt.best_feasible_objective is None and pt.feasible_fraction == 0.0 for pt in out.details.iterations)


def test_cancellation_and_deadline(qubo):
    import time

    from quantfolio.pipeline import Cancelled

    class Sink(NullSink):
        calls = 0

        def check_cancelled(self) -> None:
            Sink.calls += 1
            if Sink.calls > 3:
                raise Cancelled()

    with pytest.raises(Cancelled):
        run_qaoa(qubo, TINY_QAOA.model_copy(update={"max_evaluations": 50}), Sink(), None)
    out = run_qaoa(qubo, TINY_QAOA.model_copy(update={"max_evaluations": 50}), NullSink(), None, deadline=time.monotonic() - 1.0)
    assert out.details.evaluations_used < 50 and "time limit" in (out.status_detail or "")
    assert out.status in ("time_limited", "no_feasible_sample")
    assert out.details.final_batch.shots == TINY_QAOA.final_shots  # the declared final batch is still measured


def test_fake_backend_noise_on_tiny_circuit(qubo):
    """Local Aer simulation with a noise model built from a fake-backend snapshot,
    restricted to the active physical qubits (no IBM service call)."""
    from quantfolio.quantum.noise import DEFAULT_FAKE_BACKEND, noisy_setup

    logical = make_circuits(qubo, 1, 5).logical
    setup = noisy_setup(logical, DEFAULT_FAKE_BACKEND, 5)
    assert setup.circuit.num_qubits == len(setup.active_physical) == qubo.n  # compacted, not the full device
    assert len(set(setup.active_physical)) == qubo.n and DEFAULT_FAKE_BACKEND in setup.summary
    assert not setup.noise_model.is_ideal()
    st = TINY_QAOA.model_copy(update={"noise": "fake_backend", "fake_backend": DEFAULT_FAKE_BACKEND, "seed": 5})
    out = run_qaoa(qubo, st, NullSink(), None, noise_model=setup.noise_model, noisy_circuit=setup.circuit, noise_label=setup.summary)
    fb = out.details.final_batch
    assert out.details.noise == "fake_backend" and out.details.noise_model == setup.summary
    assert "noisy" in out.details.simulator_method
    assert all(len(r.bitstring) == qubo.n for r in fb.top_samples) and sum(r.count for r in fb.top_samples) <= fb.shots
    # noise changes the sampled distribution for the same parameters and seed
    theta = np.array(out.details.optimal_parameters)
    ideal = make_circuits(qubo, 1, 5)
    from quantfolio.quantum.circuit import parameter_permutation
    from quantfolio.quantum.qaoa import _split_params

    noisy = AerSampler(setup.circuit, parameter_permutation(setup.circuit, *_split_params(setup.circuit, 1)), noise_model=setup.noise_model).counts(theta, 2000, 1)
    clean = AerSampler(ideal.tqc, ideal.perm).counts(theta, 2000, 1)
    assert sum(noisy.values()) == sum(clean.values()) == 2000 and noisy != clean
    # INTERP warm start is not available for a noisy optimisation at depth > 1 (rejected, not silently changed)
    deep = noisy_setup(make_circuits(qubo, 2, 5).logical, DEFAULT_FAKE_BACKEND, 5)
    with pytest.raises(ValueError, match="warm_interp"):
        run_qaoa(qubo, st.model_copy(update={"depth": 2, "init_strategy": "warm_interp"}), NullSink(), None, noise_model=deep.noise_model, noisy_circuit=deep.circuit)
