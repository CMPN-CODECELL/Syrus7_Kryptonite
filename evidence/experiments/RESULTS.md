# Experiment matrix results

Generated 2026-10-08T18:54:30+00:00 by `backend/scripts/experiments.py --matrix`.

Machine: `{"platform": "Windows-11", "processor": "Intel64 Family 6 Model 140 Stepping 1, GenuineIntel", "logical_cpus": 8, "ram_gb": 15.8, "python": "3.13.2"}`

All runs: Aer `SamplerV2` statevector simulator; estimates and selection use the training window only; 
approximation ratio = (f_worst_feasible − f) / (f_worst_feasible − f_opt) over the exactly enumerated feasible set (1 = optimum; a run with no feasible sample counts as 0 in the marginal tables).

## factorial_small_universe

Dataset `nifty50-yahoo-aaa53c0c94ef` (label: downloaded historical; sha256 `aaa53c0c94ef942d...`), training window 2022-01-03..2024-12-31.

Design `full_factorial`, 60 cells, seeds [1, 2, 3], budget 30 evaluations × 256 shots, final batch 1024 shots. Wall time 189 s.

### Marginal over depth (mean ± std over all other factor levels and seeds)

| level | cells | approx. ratio | feasible prob. | optimum found | evaluations (incl. prep) | runtime s |
|---|---|---|---|---|---|---|
| 1 | 12 | 1.000 ± 0.000 | 0.157 ± 0.029 | 1.00 | 29.7 ± 0.8 | 0.75 ± 0.31 |
| 2 | 12 | 1.000 ± 0.000 | 0.160 ± 0.053 | 1.00 | 37.2 ± 7.6 | 1.92 ± 0.64 |
| 3 | 12 | 1.000 ± 0.000 | 0.156 ± 0.076 | 1.00 | 36.5 ± 6.8 | 1.66 ± 0.54 |
| 4 | 12 | 1.000 ± 0.000 | 0.189 ± 0.108 | 1.00 | 36.8 ± 7.1 | 1.81 ± 0.65 |
| 5 | 12 | 1.000 ± 0.000 | 0.168 ± 0.106 | 1.00 | 39.0 ± 9.5 | 2.64 ± 1.41 |

### Marginal over optimizer (mean ± std over all other factor levels and seeds)

| level | cells | approx. ratio | feasible prob. | optimum found | evaluations (incl. prep) | runtime s |
|---|---|---|---|---|---|---|
| COBYLA | 30 | 1.000 ± 0.000 | 0.168 ± 0.088 | 1.00 | 36.3 ± 8.2 | 1.68 ± 0.77 |
| SPSA | 30 | 1.000 ± 0.000 | 0.163 ± 0.070 | 1.00 | 35.4 ± 6.8 | 1.82 ± 1.17 |

### Marginal over init_strategy (mean ± std over all other factor levels and seeds)

| level | cells | approx. ratio | feasible prob. | optimum found | evaluations (incl. prep) | runtime s |
|---|---|---|---|---|---|---|
| random | 30 | 1.000 ± 0.000 | 0.138 ± 0.065 | 1.00 | 29.9 ± 0.5 | 1.39 ± 0.81 |
| warm_interp | 30 | 1.000 ± 0.000 | 0.194 ± 0.082 | 1.00 | 41.8 ± 6.4 | 2.11 ± 1.02 |

### Per configuration (mean ± std over seeds)

| level | cells | qubits | approx. ratio | feasible prob. | optimum found | runtime s |
|---|---|---|---|---|---|---|
| full_factorial=p1-COBYLA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.155 ± 0.019 | 1.00 | 0.98 ± 0.49 |
| full_factorial=p1-COBYLA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.175 ± 0.020 | 1.00 | 0.61 ± 0.05 |
| full_factorial=p1-SPSA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.158 ± 0.014 | 1.00 | 0.51 ± 0.02 |
| full_factorial=p1-SPSA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.142 ± 0.054 | 1.00 | 0.89 ± 0.25 |
| full_factorial=p2-COBYLA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.126 ± 0.077 | 1.00 | 1.13 ± 0.47 |
| full_factorial=p2-COBYLA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.193 ± 0.010 | 1.00 | 2.49 ± 0.47 |
| full_factorial=p2-SPSA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.154 ± 0.061 | 1.00 | 2.16 ± 0.45 |
| full_factorial=p2-SPSA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.167 ± 0.050 | 1.00 | 1.91 ± 0.25 |
| full_factorial=p3-COBYLA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.108 ± 0.026 | 1.00 | 1.40 ± 0.26 |
| full_factorial=p3-COBYLA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.216 ± 0.078 | 1.00 | 2.36 ± 0.42 |
| full_factorial=p3-SPSA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.153 ± 0.105 | 1.00 | 1.10 ± 0.12 |
| full_factorial=p3-SPSA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.145 ± 0.070 | 1.00 | 1.75 ± 0.08 |
| full_factorial=p4-COBYLA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.072 ± 0.018 | 1.00 | 1.35 ± 0.04 |
| full_factorial=p4-COBYLA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.241 ± 0.127 | 1.00 | 1.95 ± 0.08 |
| full_factorial=p4-SPSA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.235 ± 0.105 | 1.00 | 1.28 ± 0.27 |
| full_factorial=p4-SPSA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.208 ± 0.092 | 1.00 | 2.64 ± 0.65 |
| full_factorial=p5-COBYLA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.106 ± 0.032 | 1.00 | 1.56 ± 0.16 |
| full_factorial=p5-COBYLA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.293 ± 0.130 | 1.00 | 2.99 ± 0.35 |
| full_factorial=p5-SPSA-random-n6 | 3 | [8] | 1.000 ± 0.000 | 0.113 ± 0.036 | 1.00 | 2.46 ± 2.08 |
| full_factorial=p5-SPSA-warm_interp-n6 | 3 | [8] | 1.000 ± 0.000 | 0.161 ± 0.093 | 1.00 | 3.54 ± 1.80 |

**Reading this table:** every configuration found the exact optimum for every seed, so the approximation ratio does not separate them at this problem size and final-batch size; the differences are in the final-batch feasible probability, whose seed-to-seed spread (the +/- column, 3 seeds) is often as large as the differences between configurations. This study does not show any configuration to be better at finding the optimum.

**Selected (training-only rule):** `{"depth": 5, "optimizer": "COBYLA", "init_strategy": "warm_interp", "n_assets": 6, "seeds": [1, 2, 3], "mean_approximation_ratio": 1.0, "mean_feasible_probability": 0.29296875, "mean_circuit_evaluations": 50.0, "mean_runtime_s": 2.9941, "mean_validation_sharpe": -1.3737183715374626}`

Rule: Configuration (depth, optimizer, init, universe size) with the highest mean approximation ratio on the TRAINING objective across seeds (1 = exact optimum, 0 = worst feasible portfolio; a seed without a feasible sample counts as 0; compared to 9 decimals); ties -> higher mean final-batch feasible probability, then fewer mean circuit evaluations (optimisation + warm-start preparation), then lower depth. Every criterion is deterministic for fixed seeds (wall time is not used). Validation Sharpe is reported only; test data is never used.

### Noise comparison (FakeTorino, 5 paired trials × 1024 shots, fixed trained parameters)

- noise model: FakeTorino (qiskit-ibm-runtime snapshot) NoiseModel.from_backend restricted to 8 active physical qubits [73, 80, 81, 82, 83, 92, 98, 99]: 46 local gate errors, 8 readout errors; basis ['cz', 'delay', 'for_loop', 'id', 'if_else', 'measure', 'reset', 'rz', 'switch_case', 'sx', 'x']
- feasible probability ideal 0.392 ± 0.020, noisy 0.183 ± 0.012, paired delta -0.210 ± 0.031
- best feasible objective ideal -0.15858 ± 0.00000, noisy -0.15858 ± 0.00000, paired delta 0.00000 ± 0.00000
- noisy trials without any feasible sample: 0

## universe_size

Dataset `nifty50-yahoo-aaa53c0c94ef` (label: downloaded historical; sha256 `aaa53c0c94ef942d...`), training window 2022-01-03..2024-12-31.

Design `one_factor_at_a_time`, 12 cells, seeds [1, 2, 3], budget 30 evaluations × 256 shots, final batch 1024 shots. Wall time 97 s.

### Per configuration (mean ± std over seeds)

| level | cells | qubits | approx. ratio | feasible prob. | optimum found | runtime s |
|---|---|---|---|---|---|---|
| universe_size=6 | 3 | [8] | 1.000 ± 0.000 | 0.155 ± 0.019 | 1.00 | 0.86 ± 0.08 |
| universe_size=8 | 3 | [10] | 1.000 ± 0.000 | 0.138 ± 0.024 | 1.00 | 1.34 ± 0.28 |
| universe_size=10 | 3 | [17] | 0.963 ± 0.032 | 0.082 ± 0.014 | 0.33 | 3.42 ± 0.60 |
| universe_size=12 | 3 | [20] | 0.905 ± 0.082 | 0.100 ± 0.010 | 0.33 | 26.55 ± 15.51 |

**Selected (training-only rule):** `{"depth": 1, "optimizer": "COBYLA", "init_strategy": "random", "n_assets": 6, "seeds": [1, 2, 3], "mean_approximation_ratio": 1.0, "mean_feasible_probability": 0.15462239583333334, "mean_circuit_evaluations": 28.666666666666668, "mean_runtime_s": 0.8584999999999999, "mean_validation_sharpe": -1.3737183715374626}`

Rule: Configuration (depth, optimizer, init, universe size) with the highest mean approximation ratio on the TRAINING objective across seeds (1 = exact optimum, 0 = worst feasible portfolio; a seed without a feasible sample counts as 0; compared to 9 decimals); ties -> higher mean final-batch feasible probability, then fewer mean circuit evaluations (optimisation + warm-start preparation), then lower depth. Every criterion is deterministic for fixed seeds (wall time is not used). Validation Sharpe is reported only; test data is never used.
