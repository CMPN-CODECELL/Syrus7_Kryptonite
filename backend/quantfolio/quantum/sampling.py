"""Aer SamplerV2 execution, bitstring decoding and the energy estimators."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..qubo import Qubo


@dataclass
class Decoded:
    strings: list[str]
    counts: np.ndarray  # int
    energy: np.ndarray  # unscaled QUBO energy
    feasible: np.ndarray  # bool, exact constraint check on decision bits
    objective: np.ndarray  # exact economic objective of decoded weights
    decision: list[str]  # decision bits, asset order


class Decoder:
    """bitstring -> energy / feasibility / economic objective, cached."""

    def __init__(self, qubo: Qubo):
        self.q = qubo
        self.p = qubo.problem
        self._cache: dict[str, tuple[float, bool, float, str]] = {}

    def _fill(self, strings: list[str]) -> None:
        new = [s for s in strings if s not in self._cache]
        if not new:
            return
        Z = self.q.bits_from_strings(new)
        E = self.q.energy(Z)
        Xd = Z[:, : self.q.n_decision].astype(np.int64)
        feas = self.p.feasible_mask(Xd)
        W = self.p.enc.weights(Xd)
        obj = np.asarray(self.p.objective(W), dtype=np.float64)
        for k, s in enumerate(new):
            dec = "".join("1" if v else "0" for v in Xd[k])
            self._cache[s] = (float(E[k]), bool(feas[k]), float(obj[k]), dec)

    def decode(self, counts: dict[str, int]) -> Decoded:
        strings = list(counts.keys())
        self._fill(strings)
        rows = [self._cache[s] for s in strings]
        return Decoded(
            strings=strings,
            counts=np.fromiter((counts[s] for s in strings), dtype=np.int64, count=len(strings)),
            energy=np.fromiter((r[0] for r in rows), dtype=np.float64, count=len(rows)),
            feasible=np.fromiter((r[1] for r in rows), dtype=bool, count=len(rows)),
            objective=np.fromiter((r[2] for r in rows), dtype=np.float64, count=len(rows)),
            decision=[r[3] for r in rows],
        )


def estimate_energy(d: Decoded, kind: str, alpha: float) -> float:
    """mean: shot-weighted mean QUBO energy.
    cvar: mean energy of the lowest alpha fraction of shots (CVaR_alpha,
    Barkoutsos et al. 2020); the boundary bitstring contributes fractionally."""
    tot = int(d.counts.sum())
    if tot == 0:
        return float("nan")
    if kind == "mean":
        return float((d.energy * d.counts).sum() / tot)
    order = np.argsort(d.energy, kind="stable")
    need = alpha * tot
    acc = 0.0
    taken = 0.0
    for i in order:
        c = float(d.counts[i])
        take = min(c, need - taken)
        if take <= 0:
            break
        acc += take * d.energy[i]
        taken += take
    return float(acc / taken)


class AerSampler:
    """Runs a transpiled, parameterised circuit with qiskit-aer SamplerV2.

    A fresh SamplerV2 with an explicit seed is used for every call so each
    objective evaluation is individually reproducible (seed recorded)."""

    def __init__(self, tqc, perm: np.ndarray, *, method: str = "statevector", noise_model=None):
        self.tqc = tqc
        self.perm = np.asarray(perm)
        self.backend_options: dict = {"method": method}
        if noise_model is not None:
            self.backend_options["noise_model"] = noise_model

    def counts(self, params: np.ndarray, shots: int, seed: int) -> dict[str, int]:
        from qiskit_aer.primitives import SamplerV2

        sampler = SamplerV2(seed=int(seed), options={"backend_options": self.backend_options})
        vals = np.asarray(params, dtype=np.float64)[self.perm]
        res = sampler.run([(self.tqc, vals)], shots=int(shots)).result()
        data = res[0].data
        reg = next(iter(data.keys())) if hasattr(data, "keys") else "meas"
        return dict(getattr(data, reg).get_counts())
