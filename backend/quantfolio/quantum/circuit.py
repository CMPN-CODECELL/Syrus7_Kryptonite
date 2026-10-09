"""Explicit QAOA circuit construction and one-time transpilation.

    |psi(gamma, beta)> = prod_{l=1..p} U_M(beta_l) U_C(gamma_l) H^{(x)n} |0>
    U_C(gamma) = exp(-i gamma H_C),  H_C = s * (sum_i h_i Z_i + sum_{i<j} J_ij Z_i Z_j)
    U_M(beta)  = exp(-i beta sum_i X_i)

RZ(theta) = exp(-i theta Z/2) and RZZ(theta) = exp(-i theta ZZ/2), so the
cost layer applies RZ(2 gamma s h_i) and RZZ(2 gamma s J_ij); the mixer applies
RX(2 beta). `s` = 1 / max|h, J| scales the Hamiltonian so parameter ranges
are comparable across problems; reported energies are always unscaled QUBO
energies. Qubit q holds variable q (Qiskit little-endian bitstrings).
"""

from __future__ import annotations

import time

import numpy as np

from ..contracts import CircuitStats

TWO_QUBIT = {"cx", "cz", "ecr", "rzz", "swap", "iswap", "rxx", "ryy", "rzx", "cp", "crz"}


def build_qaoa_circuit(h: np.ndarray, J: dict[tuple[int, int], float], depth: int, scale: float):
    from qiskit import QuantumCircuit
    from qiskit.circuit import ParameterVector

    n = len(h)
    gamma = ParameterVector("gamma", depth)
    beta = ParameterVector("beta", depth)
    qc = QuantumCircuit(n, name=f"qaoa_p{depth}")
    qc.h(range(n))
    for layer in range(depth):
        for i in range(n):
            if h[i] != 0:
                qc.rz(2.0 * scale * float(h[i]) * gamma[layer], i)
        for (i, j), v in sorted(J.items()):
            if v != 0:
                qc.rzz(2.0 * scale * float(v) * gamma[layer], i, j)
        qc.rx(2.0 * beta[layer], range(n))
    qc.measure_all()
    return qc, list(gamma), list(beta)


def parameter_permutation(circuit, gamma, beta) -> np.ndarray:
    """Index array mapping our [gamma_0..gamma_{p-1}, beta_0..beta_{p-1}]
    vector onto `circuit.parameters` order (which Qiskit sorts by name)."""
    ours = list(gamma) + list(beta)
    pos = {prm: k for k, prm in enumerate(ours)}
    return np.array([pos[prm] for prm in circuit.parameters], dtype=np.int64)


def transpile_once(circuit, backend, seed: int, optimization_level: int = 1):
    from qiskit import transpile

    t0 = time.perf_counter()
    tqc = transpile(circuit, backend=backend, optimization_level=optimization_level, seed_transpiler=int(seed))
    dt = time.perf_counter() - t0
    return tqc, dt


def circuit_stats(tqc, transpile_seconds: float, optimization_level: int, basis_gates: list[str]) -> CircuitStats:
    ops = {k: int(v) for k, v in tqc.count_ops().items()}
    two_q = sum(v for k, v in ops.items() if k in TWO_QUBIT)
    active = sorted({tqc.find_bit(q).index for inst in tqc.data for q in inst.qubits})
    return CircuitStats(
        n_qubits=len(active) if active else tqc.num_qubits,
        depth=int(tqc.depth()),
        size=int(tqc.size()),
        gate_counts=ops,
        two_qubit_gates=int(two_q),
        basis_gates=list(basis_gates),
        transpile_seconds=round(transpile_seconds, 4),
        optimization_level=optimization_level,
    )
