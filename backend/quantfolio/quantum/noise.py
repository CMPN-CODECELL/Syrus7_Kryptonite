"""Fake-backend noise, restricted to the qubits the circuit actually uses.

1. Transpile the logical QAOA circuit to the fake backend (its basis gates,
   coupling map, routing) once.
2. Find the active physical qubits and re-index them 0..k-1, keeping every
   measurement writing to the same classical bit (so classical bit i is still
   QUBO variable i).
3. Build a reduced NoiseModel that contains only the backend's gate and
   readout errors on those physical qubits, re-indexed the same way.

The compact circuit is simulated with the Aer statevector method (noisy
trajectories); without step 2 Aer would allocate the full device register.
Importing qiskit_ibm_runtime.fake_provider costs several seconds, so this
module is only imported on noise paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

FAKE_BACKENDS = ["FakeTorino", "FakeBrisbane", "FakeSherbrooke", "FakeKyiv", "FakeFez"]
DEFAULT_FAKE_BACKEND = "FakeTorino"


@dataclass
class NoisySetup:
    backend_name: str
    circuit: object  # compact transpiled circuit
    noise_model: object
    active_physical: list[int]
    transpile_seconds: float
    summary: str
    basis_gates: list[str]


def load_fake_backend(name: str):
    if name not in FAKE_BACKENDS:
        raise ValueError(f"unsupported fake backend {name!r}")
    from qiskit_ibm_runtime import fake_provider

    return getattr(fake_provider, name)()


def compact(tqc):
    """Re-index active qubits of a transpiled circuit to 0..k-1."""
    from qiskit import QuantumCircuit

    active = sorted({tqc.find_bit(q).index for inst in tqc.data for q in inst.qubits})
    index = {p: k for k, p in enumerate(active)}
    out = QuantumCircuit(len(active), tqc.num_clbits, name=tqc.name)
    for inst in tqc.data:
        qs = [out.qubits[index[tqc.find_bit(q).index]] for q in inst.qubits]
        cs = [out.clbits[tqc.find_bit(c).index] for c in inst.clbits]
        out.append(inst.operation, qs, cs)
    return out, active


def reduced_noise_model(full, active: list[int]):
    from qiskit_aer.noise import NoiseModel

    index = {p: k for k, p in enumerate(active)}
    nm = NoiseModel(basis_gates=list(full.basis_gates))
    n_gate = n_ro = 0
    for gate, by_qubits in getattr(full, "_local_quantum_errors", {}).items():
        for qubits, err in by_qubits.items():
            if all(q in index for q in qubits):
                nm.add_quantum_error(err, gate, [index[q] for q in qubits], warnings=False)
                n_gate += 1
    for qubits, err in getattr(full, "_local_readout_errors", {}).items():
        if all(q in index for q in qubits):
            nm.add_readout_error(err, [index[q] for q in qubits], warnings=False)
            n_ro += 1
    for gate, err in getattr(full, "_default_quantum_errors", {}).items():
        nm.add_all_qubit_quantum_error(err, gate, warnings=False)
    if getattr(full, "_default_readout_error", None) is not None:
        nm.add_all_qubit_readout_error(full._default_readout_error, warnings=False)
    return nm, n_gate, n_ro


def noisy_setup(logical_circuit, backend_name: str, seed: int, optimization_level: int = 1) -> NoisySetup:
    from qiskit import transpile
    from qiskit_aer.noise import NoiseModel

    backend = load_fake_backend(backend_name)
    t0 = time.perf_counter()
    tqc = transpile(logical_circuit, backend=backend, optimization_level=optimization_level, seed_transpiler=int(seed))
    small, active = compact(tqc)
    dt = time.perf_counter() - t0
    full = NoiseModel.from_backend(backend)
    nm, n_gate, n_ro = reduced_noise_model(full, active)
    summary = (
        f"{backend_name} (qiskit-ibm-runtime snapshot) NoiseModel.from_backend restricted to {len(active)} active physical "
        f"qubits {active}: {n_gate} local gate errors, {n_ro} readout errors; basis {sorted(full.basis_gates)}"
    )
    return NoisySetup(
        backend_name=backend_name,
        circuit=small,
        noise_model=nm,
        active_physical=active,
        transpile_seconds=dt,
        summary=summary,
        basis_gates=sorted(full.basis_gates),
    )
