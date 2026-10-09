"""QUBO assembly, penalty derivation, scaling and the Ising mapping.

Variables (also qubits, Qiskit little-endian: variable q is bitstring[-1-q]):

    0 .. n_dec-1      decision bits  x[SYM] (selection) or n[SYM]_b{k} (lots)
    n_dec .. n-1      slack bits     s[constraint]_b{k}

Energy:  E(z) = offset + lin . z + z' U z   (U strictly upper triangular)
         = f_quad(x) + P * sum_c pen_c(x, s_c)

Constraint penalties (P = penalty weight, see `derive_penalty`):

    ==          P (a.y - b)^2                              no slack
    <=          P (a.y + c.s - b)^2,  s in [0, b - lhs_min]  bounded binary slack
    >=          P (a.y - c.s - b)^2,  s in [0, lhs_max - b]  bounded binary slack
    at_most_one P sum_{i<j} x_i x_j                         no slack

Raw inequalities are never squared: a squared inequality would penalise
strictly-feasible points. Because a, b, c are integers, every infeasible
decision vector has min_s pen_c >= 1 while every feasible one has
min_s pen_c = 0.

Coefficient quantisation: after assembly every coefficient is rounded to a
multiple of 2^-40 * max|coef| and terms below that are dropped, so the QUBO
(and its hash) is deterministic across platforms; the rounding error per
coefficient is reported (< 1e-12 relative).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from .constraints.base import IntegerForm
from .contracts import PenaltyInfo, QuboInfo, VariableInfo
from .portfolio import Problem, QuadForm, bounded_coefficients, canonical_bits, objective_quadform

EXACT_RANGE_MAX_BITS = 18  # enumerate all 2^n decision vectors for the penalty range up to this
PENALTY_EPS_REL = 1e-3
QUANT_BITS = 40

OBJECTIVE_DEFINITION = (
    "minimise q * w'Σw - μ'w + C(w)  (annualised; C = one-off transaction cost as a fraction of budget), "
    "w from decision bits; plus P * (squared integer constraint residuals with bounded binary slack)"
)


@dataclass
class SlackBlock:
    form: IntegerForm
    indices: list[int]
    coefs: list[int]


@dataclass
class PenaltyDerivation:
    weight: float
    scale: float
    delta: float
    method: str
    text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class Qubo:
    problem: Problem
    n: int
    n_decision: int
    offset: float
    lin: np.ndarray
    quad: np.ndarray  # strictly upper triangular
    names: list[str]
    kinds: list[str]
    var_asset: list[str | None]
    var_constraint: list[str | None]
    var_coef: list[float | None]
    blocks: list[SlackBlock]
    penalty: PenaltyDerivation
    objective: QuadForm
    quantisation_error: float = 0.0

    # ---------------- energies ----------------
    def energy(self, Z: np.ndarray) -> np.ndarray:
        Z = np.atleast_2d(np.asarray(Z, dtype=np.float64))
        return self.offset + Z @ self.lin + np.einsum("ij,ij->i", Z @ self.quad, Z)

    def complete_slack(self, Xd: np.ndarray) -> np.ndarray:
        """Append the penalty-minimising slack bits to decision vectors."""
        Xd = np.atleast_2d(np.asarray(Xd, dtype=np.int64))
        Z = np.zeros((len(Xd), self.n), dtype=np.int8)
        Z[:, : self.n_decision] = Xd
        for b in self.blocks:
            if not b.indices:
                continue
            lhs = b.form.lhs(Xd)
            smax = int(sum(b.coefs))
            need = (b.form.rhs - lhs) if b.form.sense == "<=" else (lhs - b.form.rhs)
            need = np.clip(need, 0, smax)
            table = np.array([canonical_bits(v, b.coefs) for v in range(smax + 1)], dtype=np.int8)
            Z[:, b.indices] = table[need]
        return Z

    def decision_energy(self, Xd: np.ndarray) -> np.ndarray:
        """min over slack of E: equals f_quad(x) for feasible x."""
        return self.energy(self.complete_slack(Xd))

    # ---------------- bitstrings ----------------
    def bits_from_string(self, s: str) -> np.ndarray:
        """Qiskit bitstring (qubit 0 rightmost) -> variable vector."""
        if len(s) != self.n:
            raise ValueError("bitstring length does not match the QUBO")
        return np.frombuffer(s[::-1].encode("ascii"), dtype=np.uint8)[: self.n] - ord("0")

    def bits_from_strings(self, strings: list[str]) -> np.ndarray:
        if not strings:
            return np.zeros((0, self.n), dtype=np.int8)
        raw = np.frombuffer("".join(s[::-1] for s in strings).encode("ascii"), dtype=np.uint8)
        return (raw.reshape(len(strings), self.n) - ord("0")).astype(np.int8)

    def string_from_bits(self, z: np.ndarray) -> str:
        return "".join("1" if int(v) else "0" for v in np.asarray(z)[::-1])

    # ---------------- Ising ----------------
    def ising(self) -> tuple[np.ndarray, dict[tuple[int, int], float], float]:
        """x = (1 - z)/2 (z = +1 <=> bit 0). Returns (h, J, offset)."""
        c, U = self.lin, self.quad
        h = -c / 2.0 - (U.sum(axis=1) + U.sum(axis=0)) / 4.0
        J = {(int(i), int(j)): float(U[i, j] / 4.0) for i, j in zip(*np.nonzero(U))}
        off = float(self.offset + c.sum() / 2.0 + U.sum() / 4.0)
        return h, J, off

    def to_sparse_pauli_op(self, scale: float = 1.0):
        """SparsePauliOp of sum h_i Z_i + sum J_ij Z_i Z_j (times `scale`); the
        constant ising offset is returned separately by `ising()`."""
        from qiskit.quantum_info import SparsePauliOp

        h, J, _ = self.ising()
        terms = [("Z", [i], float(h[i]) * scale) for i in range(self.n) if h[i] != 0]
        terms += [("ZZ", [i, j], v * scale) for (i, j), v in J.items() if v != 0]
        if not terms:
            terms = [("I", [0], 0.0)]
        return SparsePauliOp.from_sparse_list(terms, num_qubits=self.n).simplify(atol=0)

    def circuit_scale(self) -> float:
        h, J, _ = self.ising()
        m = max([abs(float(v)) for v in h] + [abs(v) for v in J.values()] + [0.0])
        return 1.0 / m if m > 0 else 1.0

    # ---------------- reporting ----------------
    def variables(self) -> list[VariableInfo]:
        return [
            VariableInfo(index=q, name=self.names[q], kind=self.kinds[q], asset=self.var_asset[q], constraint=self.var_constraint[q], coefficient=self.var_coef[q])  # type: ignore[arg-type]
            for q in range(self.n)
        ]

    def info(self) -> QuboInfo:
        _, J, ising_off = self.ising()
        coefs = np.concatenate([self.lin, self.quad[np.nonzero(self.quad)]])
        pens = [
            PenaltyInfo(constraint=b.form.name, weight=self.penalty.weight, derivation=self.penalty.text)
            for b in self.blocks
        ]
        return QuboInfo(
            n_variables=self.n,
            n_decision=self.n_decision,
            n_slack=self.n - self.n_decision,
            offset=float(self.offset),
            ising_offset=ising_off,
            n_linear_terms=int(np.count_nonzero(self.lin)),
            n_quadratic_terms=int(np.count_nonzero(self.quad)),
            max_abs_coefficient=float(np.max(np.abs(coefs))) if coefs.size else 0.0,
            scale=self.circuit_scale(),
            penalties=pens,
            variables=self.variables(),
            objective_definition=OBJECTIVE_DEFINITION,
        )

    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(np.float64(self.offset).tobytes())
        h.update(np.ascontiguousarray(self.lin, dtype=np.float64).tobytes())
        h.update(np.ascontiguousarray(self.quad, dtype=np.float64).tobytes())
        h.update("|".join(self.names).encode())
        return h.hexdigest()


# --------------------------------------------------------------------------- #
# Penalty derivation
# --------------------------------------------------------------------------- #
def objective_range(obj: QuadForm, n_dec: int) -> tuple[float, str]:
    """Range (max - min) of the objective over ALL binary decision vectors
    (feasible or not): exact by enumeration up to EXACT_RANGE_MAX_BITS bits,
    else the bound sum|lin| + sum|quad| (>= the true range)."""
    if n_dec <= EXACT_RANGE_MAX_BITS:
        best_lo, best_hi = np.inf, -np.inf
        chunk = 1 << min(n_dec, 16)
        total = 1 << n_dec
        cols = np.arange(n_dec)
        for start in range(0, total, chunk):
            idx = np.arange(start, min(total, start + chunk), dtype=np.int64)
            X = ((idx[:, None] >> cols) & 1).astype(np.float64)
            e = obj.energy(X)
            best_lo, best_hi = min(best_lo, float(e.min())), max(best_hi, float(e.max()))
        return best_hi - best_lo, f"exact range over all 2^{n_dec} decision vectors"
    bound = float(np.abs(obj.lin).sum() + np.abs(obj.quad).sum())
    return bound, "upper bound sum|linear| + sum|quadratic| (too many bits to enumerate)"


def derive_penalty(obj: QuadForm, n_dec: int, penalty_scale: float = 1.0) -> PenaltyDerivation:
    """P = penalty_scale * (Delta + eps), Delta = objective range over all
    decision vectors. With penalty_scale >= 1, any decision vector violating
    an integer constraint by >= 1 unit has energy >= f_min + P > f_max, i.e.
    above EVERY feasible portfolio, so the QUBO minimiser is the constrained
    optimum (up to the objective's quadratic-fit error in lots mode)."""
    delta, method = objective_range(obj, n_dec)
    eps = max(PENALTY_EPS_REL * delta, 1e-6)
    weight = float(penalty_scale * (delta + eps))
    warnings = []
    if penalty_scale < 1.0:
        warnings.append(
            f"penalty_scale {penalty_scale:g} < 1: the penalty may be smaller than the objective range, so an infeasible "
            "bitstring can have lower QUBO energy than the constrained optimum (the validator still rejects it)."
        )
    text = f"P = {penalty_scale:g} x (Δ + ε) = {weight:.6g}; Δ = {delta:.6g} ({method}), ε = {eps:.3g}"
    return PenaltyDerivation(weight=weight, scale=float(penalty_scale), delta=float(delta), method=method, text=text, warnings=warnings)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def _add_square(lin: np.ndarray, quad: np.ndarray, coeffs: dict[int, float], b: float, P: float) -> float:
    """lin/quad += P (sum_j v_j z_j - b)^2 for binary z; returns the constant P b^2."""
    items = sorted(coeffs.items())
    for j, v in items:
        lin[j] += P * (v * v - 2.0 * b * v)
    for a in range(len(items)):
        ja, va = items[a]
        for c in range(a + 1, len(items)):
            jc, vc = items[c]
            i, k = (ja, jc) if ja < jc else (jc, ja)
            quad[i, k] += 2.0 * P * va * vc
    return P * b * b


def _quantise(lin: np.ndarray, quad: np.ndarray, offset: float) -> tuple[np.ndarray, np.ndarray, float, float]:
    m = max(float(np.max(np.abs(lin))) if lin.size else 0.0, float(np.max(np.abs(quad))) if quad.size else 0.0)
    if m == 0:
        return lin, quad, offset, 0.0
    step = m * 2.0**-QUANT_BITS
    lq = np.round(lin / step) * step
    qq = np.round(quad / step) * step
    err = float(max(np.max(np.abs(lq - lin)), np.max(np.abs(qq - quad))))
    return lq, qq, offset, err


def build_qubo(p: Problem, penalty_scale: float = 1.0) -> Qubo:
    enc = p.enc
    n_dec = enc.n_decision
    obj = objective_quadform(p)
    pen = derive_penalty(obj, n_dec, penalty_scale)
    P = pen.weight

    forms: list[IntegerForm] = []
    for c in p.constraints:
        if not c.is_objective_term():
            forms.extend(c.integer_forms(p))

    names, kinds, assets, cons, coefv = [], [], [], [], []
    for k in range(n_dec):
        sym = p.symbols[int(enc.bit_asset[k])]
        if p.mode == "selection":
            names.append(f"x[{sym}]")
            kinds.append("decision")
            coefv.append(1.0)
        else:
            b = k - enc.bits_of(int(enc.bit_asset[k]))[0]
            names.append(f"n[{sym}]_b{b}")
            kinds.append("lot_bit")
            coefv.append(float(enc.bit_coef[k]))
        assets.append(sym)
        cons.append(None)

    blocks: list[SlackBlock] = []
    nxt = n_dec
    for f in forms:
        coefs = f.slack_coefficients() if f.sense in ("<=", ">=") else []
        idx = list(range(nxt, nxt + len(coefs)))
        for b, cv in enumerate(coefs):
            names.append(f"s[{f.name}]_b{b}")
            kinds.append("slack")
            assets.append(None)
            cons.append(f.name)
            coefv.append(float(cv))
        nxt += len(coefs)
        blocks.append(SlackBlock(form=f, indices=idx, coefs=coefs))
    n = nxt

    lin = np.zeros(n)
    quad = np.zeros((n, n))
    lin[:n_dec] = obj.lin
    quad[:n_dec, :n_dec] = obj.quad
    offset = float(obj.const)
    for blk in blocks:
        f = blk.form
        if f.sense == "at_most_one":
            ks = sorted(f.coeffs)
            for a in range(len(ks)):
                for c in range(a + 1, len(ks)):
                    quad[ks[a], ks[c]] += P
            continue
        sign = 1.0 if f.sense == "<=" else (-1.0 if f.sense == ">=" else 0.0)
        coeffs: dict[int, float] = {j: float(v) for j, v in f.coeffs.items()}
        for j, cv in zip(blk.indices, blk.coefs):
            coeffs[j] = sign * cv
        offset += _add_square(lin, quad, coeffs, float(f.rhs), P)

    lin, quad, offset, qerr = _quantise(lin, quad, offset)
    return Qubo(
        problem=p,
        n=n,
        n_decision=n_dec,
        offset=offset,
        lin=lin,
        quad=quad,
        names=names,
        kinds=kinds,
        var_asset=assets,
        var_constraint=cons,
        var_coef=coefv,
        blocks=blocks,
        penalty=pen,
        objective=obj,
        quantisation_error=qerr,
    )


def count_qubits(p: Problem) -> tuple[int, int, list[tuple[str, int]]]:
    """(decision bits, slack bits, per-form slack) without building matrices."""
    per = []
    for c in p.constraints:
        if c.is_objective_term():
            continue
        for f in c.integer_forms(p):
            per.append((f.name, len(f.slack_coefficients()) if f.sense in ("<=", ">=") else 0))
    return p.enc.n_decision, sum(s for _, s in per), per


__all__ = ["Qubo", "build_qubo", "derive_penalty", "objective_range", "count_qubits", "bounded_coefficients"]
