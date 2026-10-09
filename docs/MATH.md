# QuantFolio mathematics

This document states exactly what the quantum core computes. Every formula
here is implemented in `backend/quantfolio/` and exercised by
`backend/tests/core/`. Numbers quoted from experiments come from
`evidence/experiments/` (regenerate with `backend/scripts/experiments.py`).

QuantFolio is a research and teaching tool. Nothing here is investment advice,
and nothing here claims a quantum advantage: all quantum runs are classical
simulations of small circuits, and an exact classical solver is always
available at these sizes.

## 1. Notation and units

| Symbol | Meaning | Unit |
|---|---|---|
| `n` | number of encoded assets | – |
| `mu` | expected return per asset, from training data | per year, decimal (0.15 = 15 %) |
| `Sigma` | covariance of asset returns, from training data | per year |
| `w` | portfolio weights; `w_i >= 0`, `sum w <= 1` | fraction of the budget |
| `w0` | starting weights (cash or existing holdings) | fraction of the budget |
| `q` | risk aversion | – |
| `K` | cardinality (selection mode) | assets |
| `L`, `m` | total lots and maximum lots per asset (lots mode) | lots |
| `P` | QUBO penalty weight | objective units |

Returns are simple daily returns annualised by 252 (see `docs/DATA.md`).

## 2. Decision variables

**Selection mode.** One bit per asset, `x_i in {0,1}`, exactly `K` chosen,
equal weights `w_i = x_i / K`.

**Lots mode.** Asset `i` holds `n_i in {0..m}` lots and `w_i = n_i / L`. The
integer is written with bounded binary bits `n_i = sum_k c_k b_ik` with
coefficients `1, 2, 4, ...` and the last coefficient reduced so that the
largest representable value is exactly `m` (`portfolio.bounded_coefficients`).
No bit pattern can therefore exceed the per-asset limit. The budget is
`sum n_i = L`, or `sum n_i <= L` when a cash remainder is allowed; cash earns
zero.

The decision string shown in the UI has character `i` = decision variable `i`
(not Qiskit order; see section 6).

## 3. Objective

All solvers minimise the same function of the weights:

```
f(w) = q * w' Sigma w  -  mu' w  +  C(w)
C(w) = (bps / 1e4) * sum_i |w_i - w0_i|
     + (F / budget) * #{ i : w_i != w0_i }
     + (constant cost of liquidating starting holdings outside the encoded set)
```

`bps` is the proportional cost in basis points, `F` the fixed fee per traded
asset in INR. `C` is a one-off cost and is charged against a one-year
expected-return horizon; this is a modelling choice, stated in every result.
If the start is cash and `K` is fixed, `C` is the same for every portfolio and
the run reports the issue `transaction_cost_constant`.

`portfolio.evaluate` recomputes weights, objective, expected return,
volatility, costs and every constraint from the decision bits. It is the only
validator: QAOA samples and all classical baselines go through it, so a solver
cannot report a portfolio it has not been checked against.

**Quadratic form.** Risk and return are already quadratic in the bits. The
cost of asset `i` depends only on that asset's bits, and it is expanded
exactly into a multilinear polynomial (Mobius transform). That expansion is
quadratic, hence exact, in selection mode and in lots mode with at most 2 bits
per asset. With 3 or more bits per asset the cost is replaced by a
least-squares quadratic fit and the largest fit error is reported as
`max_cost_fit_error`; the evaluator still uses the exact cost.

## 4. Constraints

Each constraint produces integer linear forms `a . y (sense) b` over the
decision bits (`constraints.IntegerForm`), with known `lhs_min` and `lhs_max`.

| Constraint | Form |
|---|---|
| Cardinality | `sum x_i == K` |
| Lot budget | `sum n_i == L`, or `<= L` with cash; skipped if it can never bind |
| Sector cap, count basis (selection) | cap 1: at most one (pairwise term); larger cap: `sum_{i in sector} x_i <= cap` |
| Sector cap, weight basis | unit limit `floor(cap * K)` or `floor(cap * L)` on the sector's units |
| Target return | `mu' w >= R`, quantised (below) |
| Transaction costs | objective term, no form |

Caps that can never bind are skipped. A count-basis sector cap in lots mode is
not supported and is rejected by preflight (`unsupported_constraint_for_mode`).

**Slack.** An inequality becomes an equality with a non-negative integer slack
written in bounded binary, so that the slack range is exactly the feasible
range and no more:

```
a.y <= b   ->   a.y + s = b,   s in [0, b - lhs_min]
a.y >= b   ->   a.y - s = b,   s in [0, lhs_max - b]
```

**Target return.** Real coefficients cannot be matched by an integer slack, so
the constraint is quantised. With `N = K` or `L` and a shift `s0 = min(mu)`
when the budget equality holds (otherwise 0):

```
a_i = round((mu_i - s0) / delta),   rhs = ceil(N * (R - s0) / delta)
sum_i a_i * units_i >= rhs
```

`delta = span / (2^bits - 1)` is refined for `bits = 1..8` until the quantised
and the exact constraint agree on every structurally feasible portfolio
(checked by enumeration up to 20 000 portfolios) or the error bound
`delta / 2 <= 0.001` holds. If they still disagree the run carries the warning
`target_return_quantisation`. The target is classified as infeasible,
non-binding (not encoded) or binding. The evaluator always applies the exact,
unquantised check, so a quantisation error can cost QAOA feasible probability
but can never make an infeasible portfolio look feasible.

## 5. QUBO

Variables are ordered decision bits first (`x[SYM]` or `n[SYM]_b{k}`), then
slack bits (`s[constraint]_b{k}`). With `z` the full bit vector:

```
E(z) = offset + lin . z + z' U z          (U strictly upper triangular)
     = f_quad(x) + P * sum_c pen_c(z)

equality     pen = (a.y - b)^2
<=           pen = (a.y + c.s - b)^2
>=           pen = (a.y - c.s - b)^2
at most one  pen = sum_{i<j} x_i x_j
```

**Penalty weight.** Because every form has integer coefficients, an infeasible
decision vector has `min_s pen >= 1` and a feasible one has `min_s pen = 0`.
Let `Delta = max f_quad - min f_quad` over all `2^n_dec` decision vectors
(exact enumeration up to 18 decision bits, otherwise the bound
`sum |lin| + sum |quad|`). Then

```
P = penalty_scale * (Delta + eps),     eps = max(1e-3 * Delta, 1e-6)
```

With `penalty_scale >= 1`, every infeasible assignment has energy at least
`min f + P > max f`, which is above every feasible assignment with its slack
completed. The QUBO ground state is therefore the constrained optimum (with
respect to the quantised target-return constraint and, in lots mode with 3 or
more bits per asset, the fitted cost). `penalty_scale < 1` is allowed for
experiments and produces a warning because the guarantee no longer holds. A
larger `P` is safe but flattens the objective part of the landscape, which is
why `P` is derived instead of set to a large constant. The derivation text is
part of every result (`PenaltyDerivation`). Example (default configuration,
8 assets): `Delta = 0.221775`, `P = 0.221997`.

**Determinism.** Coefficients are rounded to multiples of
`2^-40 * max|coef|` before hashing, and the QUBO's SHA-256 is stored with the
run, so two runs can be shown to have solved the same matrix.

## 6. Ising mapping and bit order

With `x = (1 - z) / 2`, `z in {+1, -1}`:

```
h_i   = -lin_i / 2 - (rowsum_i U + colsum_i U) / 4
J_ij  = U_ij / 4
const = offset + sum(lin) / 2 + sum(U) / 4
H     = const + sum_i h_i Z_i + sum_{i<j} J_ij Z_i Z_j
```

Qubit `i` is variable `i`. Qiskit prints bitstrings little-endian (qubit 0 is
the rightmost character), so every sampled string is reversed before it is
read as a variable vector. Tests check `E(bits) == <bits|H|bits>` for every
basis state of small instances.

## 7. QAOA circuit and estimator

```
|psi(gamma, beta)> = prod_{l=1..p} U_M(beta_l) U_C(gamma_l)  H^(x n) |0>
U_C(gamma) = exp(-i gamma s H_C),   H_C = sum h_i Z_i + sum J_ij Z_i Z_j
U_M(beta)  = exp(-i beta sum_i X_i)
```

`s = 1 / max(|h|, |J|)` rescales the Hamiltonian so that angles are of order
one regardless of the problem's units; reported energies are always unscaled.
Gates: `RZ(2 gamma s h_i)`, `RZZ(2 gamma s J_ij)`, `RX(2 beta)`. The circuit is
transpiled once (optimisation level 1, seeded) and re-bound for each
evaluation.

Sampling uses Aer `SamplerV2` with the statevector method. A fresh sampler
with an explicit seed is created for each evaluation, so an evaluation is a
pure function of (parameters, shots, seed).

**Estimator.** Each sampled string is decoded to its energy `E(z)`. The
optimiser minimises either the sample mean or `CVaR_alpha`, the mean of the
lowest `alpha` fraction of shots with a fractional boundary shot (Barkoutsos
et al. 2020). Feasibility and the economic objective of a sample are always
computed from its decision bits by the exact evaluator; slack bits are ignored
at that stage.

**Seeds.** From one user seed: initial point = `seed`, transpiler = `seed`,
optimiser = `seed + 17`, evaluation `k` = `seed * 1000 + 1 + k`, final batch =
`seed + 1 000 003`. All are recorded in the result.

## 8. Optimisers and initial points

Both optimisers run under a hard evaluation budget and a deadline. The
returned parameters are those of the lowest objective estimate actually
evaluated, not the optimiser's last iterate.

- **COBYLA** (SciPy): `rhobeg = 0.3`, `tol = 1e-4`.
- **SPSA** (own implementation after Spall 1998): 2 evaluations per iteration,
  Rademacher perturbations, gains `a_k = a / (k + 1 + A)^0.602`,
  `c_k = 0.15 / (k + 1)^0.101`, `A = 0.1 * iterations`, with `a` calibrated
  from the first gradient estimate so that the first step moves each
  parameter by 0.2.

Parameter layout is `[gamma_1..gamma_p, beta_1..beta_p]`.

- **random**: `gamma ~ U(0, pi)`, `beta ~ U(0, pi/2)`, seeded.
- **warm_interp** (INTERP, Zhou et al. 2020): depth 1 starts from a linear
  ramp (`t = (l - 0.5) / p`, `gamma = 0.75 t`, `beta = 0.75 (1 - t)`); each
  depth `1..p-1` is optimised and its optimum is linearly interpolated to the
  next depth. Each preparation level gets
  `max(5, (max_evaluations // 2) // (p - 1))` evaluations. These are extra
  circuit evaluations; they are counted and reported separately as warm-start
  preparation, and comparisons between strategies report evaluations
  including preparation.

`warm_interp` together with optimisation under noise at depth > 1 is not
supported and is rejected by preflight (`warm_interp_noise_unsupported`).

## 9. Final batch and the reported portfolio

After optimisation one declared batch of `final_shots` is sampled at the
returned parameters with its own seed. Its raw counts are written to
`final_counts.json` with a SHA-256, and everything reported about the quantum
result is derived from that file.

Selection policy: **the lowest economic objective among final-batch samples
that pass the exact constraint check**; ties go to the higher count, then the
lexicographically smaller bitstring. The result also reports the feasible
probability (share of final-batch shots whose decision bits are feasible), the
probability of the selected string, the 32 most frequent samples and a 20-bin
objective histogram.

If no final-batch sample is feasible the status is `no_feasible_sample` and
no quantum portfolio is reported. A baseline result is never substituted, and
the message states that this is not a proof of infeasibility. If the deadline
is reached the status is `time_limited` and the result is built from the
evaluations completed so far.

Picking the best of many samples is a post-selection step. With 1024 shots
over a few hundred basis states, finding the optimum among the samples is
weak evidence about the circuit; the feasible probability and the probability
of the optimum are the more informative numbers and are always shown next to
the portfolio.

## 10. Noise

`noise = "fake_backend"` or `noise_comparison = true` uses a noise model from
a Qiskit fake backend snapshot (FakeTorino by default; FakeBrisbane,
FakeSherbrooke, FakeKyiv, FakeFez allowed). The circuit is transpiled to the
backend, the physical qubits it uses are relabelled `0..k-1` with classical
bit `i` still equal to variable `i`, and a reduced `NoiseModel` keeps only the
local gate and readout errors on those qubits. It is simulated with Aer
statevector trajectories. This is a simulation of a calibration snapshot, not
a hardware run, and results are labelled as such. No IBM hardware is used.

**Noise comparison.** The trained parameters are fixed; each trial samples the
ideal and the noisy circuit with the same shots and the same seed; the
reported delta is noisy minus ideal per trial, with mean and sample standard
deviation over trials.

## 11. Classical baselines and gaps

| Solver | Method | What it proves |
|---|---|---|
| `exact` | exhaustive enumeration of structurally valid portfolios (up to 1 200 000 candidates, 20 assets) | optimality, or infeasibility |
| `relaxation_rounding` | continuous relaxation (CVXPY + CLARABEL), rounding, best-improvement local repair | a lower bound; infeasibility if the relaxation is infeasible |
| `simulated_annealing` | structure-preserving moves, indicator penalty with the QUBO weight `P`, geometric cooling from the median `|delta f|`, seeded restarts | nothing; heuristic |
| `greedy` | constructive, no local search | nothing; heuristic |

The relaxation minimises `q w'Sigma w - mu'w + (bps/1e4) * ||w - w0||_1` over
box, budget, sector-weight and target-return constraints. The fixed fee is
dropped, so its optimum is a valid lower bound on the discrete optimum.

Baselines run after the quantum result is final and cannot change it (tested).

**Gap.** `gap = f - f_ref`, where the reference is the exact optimum when the
exact solver ran, otherwise the best known value, and the reference kind is
always stated. The relative gap divides by the feasible objective range
(worst minus best feasible, from enumeration) when available, otherwise by
`|f_ref|`; the normalisation used is part of the result.

**Approximation ratio** (experiments):
`(f_worst_feasible - f) / (f_worst_feasible - f_opt)`; 1 is the optimum, 0 the
worst feasible portfolio, and a run without a feasible sample counts as 0.

## 12. Frontier

Three kinds of points are returned and never mixed:

- `continuous_relaxation`: minimum variance for each of 25 return levels over
  the relaxed set, without costs and without the target floor. It is an
  optimistic reference curve, not a set of attainable portfolios.
- `discrete_feasible`: Pareto points of the enumerated feasible portfolios
  plus a fixed-seed sample of up to 400 of them.
- `solver`: the portfolio of each solver.

## 13. Screening and configuration selection

If more assets are requested than can be encoded, a subset is chosen from
training data only (`top_sharpe_train` or `min_variance_train`, ties by the
requested order) and every solver gets the same subset.

The experiment runner picks a configuration with this rule:

> Highest mean approximation ratio on the TRAINING objective across seeds
> (compared to 9 decimals); ties -> higher mean final-batch feasible
> probability, then fewer mean circuit evaluations (optimisation plus
> warm-start preparation), then lower depth. Wall time is not used.
> Validation Sharpe is reported only; test data is never used.

Changing every price after the training end leaves estimates, screening, the
QUBO, trained parameters, counts, solver portfolios, experiment cells and the
selection unchanged (tested); only the held-out metrics move.

## 14. Resource limits

Measured on the development laptop (8 logical CPUs, 15.8 GB RAM, about 3.5 GB
free, other development processes running). Source:
`evidence/experiments/qubit_benchmark.json`.

| Qubits | Ideal, s per evaluation (depth 1, 256 shots) | Peak RSS, fresh process |
|---|---|---|
| 8 | 0.014 | – |
| 12 | 0.028 | – |
| 16 | 0.057 | 274 MB |
| 20 | 1.56 | 281 MB |
| 22 | 3.09 | 329 MB |
| 24 | 7.05 | 521 MB |

| Qubits | Noisy FakeTorino, s per evaluation (depth 1, 256 shots) |
|---|---|
| 6 | 0.31 |
| 8 | 0.84 |
| 10 | 1.98 |
| 12 | 6.09 |
| 14 | 30.2 |

Limits enforced by preflight before any circuit is built:

- **24 qubits, ideal.** A statevector doubles with every qubit. Preflight
  budgets `350 MB + 3 * 16 B * 2^n` (three working copies, deliberately
  conservative against the measured 521 MB at 24 qubits); 25 qubits would be
  1886 MB, above the 1500 MB limit set for this machine. At 24 qubits one
  evaluation already takes about 7 s.
- **14 qubits, noisy.** Each shot is its own trajectory. Time grows about
  5x per two qubits (6.1 s at 12, 30 s at 14 per evaluation). Extrapolating
  that trend gives roughly 150 s per evaluation at 16 qubits, which was not
  measured and would not fit a useful optimisation inside the run timeout.
- **20 assets** for exact enumeration and for encoding.
- Depth 1 to 5, at most 8192 shots per evaluation, 16 384 final shots and
  2000 evaluations.

**Runtime model** used for the preflight estimate, fitted to the benchmark by
relative-error non-negative least squares:

```
ideal:  t = c0 + c_amp * depth * 2^n * n + c_shot * shots
noisy:  t = c0 + shots * (c_noisy * depth * 2^n * n) + c_shot * shots
c0 = 0.0126, c_amp = 2.57e-8, c_shot = 2.56e-5, c_noisy = 6.05e-7
```

It is a rough guide: the ratio of model to measurement ranges from 0.14 to
1.47 across the benchmark points. The UI presents it as an estimate.

**Presets** (default example: 8 assets, `K = 4`, one sector cap, 15 % target,
costs from existing holdings, 10 qubits = 8 decision + 2 slack), wall time of
a full run including baselines in a warm worker:

| Preset | Depth, optimiser, init | Shots / final / evaluations | Median | p95 |
|---|---|---|---|---|
| quick | 1, COBYLA, random | 256 / 1024 / 25 | 2.5 s | 2.9 s |
| balanced | 2, COBYLA, warm_interp | 512 / 2048 / 60 | 9.1 s | 9.7 s |
| research | 3, COBYLA, warm_interp | 1024 / 4096 / 150 | 26.8 s | 28.3 s |

Quick was run 5 times, the others 3 times, so p95 is close to the maximum. The
first run in a new process adds the import of Qiskit and Aer: 8 to 10 s in
total for quick.

## 15. What the experiment matrix showed

Dataset `nifty50-yahoo-aaa53c0c94ef`, training window 2022-01-03 to
2024-12-31, 30 evaluations of 256 shots, final batch 1024, seeds 1, 2, 3.
Full tables: `evidence/experiments/RESULTS.md`.

- **Depth 1 to 5 x {COBYLA, SPSA} x {random, warm_interp}, 6 assets, 8
  qubits (60 runs).** Every configuration found the exact optimum for every
  seed, so the approximation ratio is 1.000 everywhere and does not separate
  them. Final-batch feasible probability: 0.157 / 0.160 / 0.156 / 0.189 /
  0.168 for depths 1 to 5; COBYLA 0.168, SPSA 0.163; random 0.138,
  warm_interp 0.194 at 41.8 against 29.9 evaluations. The spread over three
  seeds is often as large as these differences. The study does not show that
  any configuration is better at finding the optimum.
- **Selected by the rule:** depth 5, COBYLA, warm_interp (the tie on the
  ratio was broken by feasible probability, 0.293 ± 0.130).
- **Noise on the selected configuration (FakeTorino, 5 paired trials).**
  Feasible probability 0.392 ± 0.020 ideal against 0.183 ± 0.012 noisy,
  paired difference -0.210 ± 0.031. The best feasible objective was the same
  in every trial.
- **Universe size at depth 1 (12 runs).** 6 assets / 8 qubits: ratio 1.000;
  8 / 10: 1.000; 10 / 17: 0.963 ± 0.032, optimum found in 1 of 3 seeds;
  12 / 20: 0.905 ± 0.082, optimum found in 1 of 3 seeds.

## 16. Limitations

- Simulation only. The instances are small enough for exact enumeration, so
  QAOA is never the best available solver here.
- Three seeds and a 30-evaluation budget per cell: differences between
  configurations at 8 qubits are within seed noise.
- Quality drops as the qubit count grows at fixed depth and budget (section
  15), and the noisy limit of 14 qubits covers only small instances.
- Expected returns are sample means over three years and are imprecise; the
  optimiser treats them as known. Held-out metrics cover one validation and
  one test period, with survivorship bias (`docs/DATA.md`).
- Equal weights in selection mode and whole lots in lots mode; no short
  positions, no leverage.
- Costs are one-off and compared against a one-year horizon. In lots mode
  with 3 or more bits per asset the QUBO uses a fitted cost.
- The target-return constraint is quantised inside the QUBO (bounded and
  reported); the exact check is applied to every reported portfolio.
- Count-basis sector caps are not available in lots mode; warm_interp is not
  available for optimisation under noise.
- Timings were taken while other processes were running on the same laptop
  and the runtime model is approximate.

## References

- E. Farhi, J. Goldstone, S. Gutmann. A Quantum Approximate Optimization
  Algorithm. arXiv:1411.4028 (2014).
- L. Zhou, S.-T. Wang, S. Choi, H. Pichler, M. D. Lukin. Quantum Approximate
  Optimization Algorithm: Performance, Mechanism, and Implementation on
  Near-Term Devices. Phys. Rev. X 10, 021067 (2020).
- P. Kl. Barkoutsos, G. Nannicini, A. Robert, I. Tavernelli, S. Woerner.
  Improving Variational Quantum Optimization using CVaR. Quantum 4, 256
  (2020).
- J. C. Spall. Implementation of the simultaneous perturbation algorithm for
  stochastic optimization. IEEE Trans. Aerosp. Electron. Syst. 34 (1998).
- O. Ledoit, M. Wolf. A well-conditioned estimator for large-dimensional
  covariance matrices. J. Multivariate Anal. 88 (2004).
- A. Lucas. Ising formulations of many NP problems. Front. Phys. 2 (2014).
