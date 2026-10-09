# Coverage: PS-03 requirements and POC guardrails

Each row maps a requirement to where it is implemented, the tests that exercise it and the recorded evidence.
Paths are relative to the project root; `core/` and `api/` mean `backend/tests/core/` and `backend/tests/api/`.
Test names are functions in the listed file. Evidence folders: `evidence/api-tests/` (platform test report),
`evidence/core-tests/` (numerical test report), `evidence/experiments/` (experiment matrix), `evidence/load/` (load and
failure tests), `evidence/e2e/` (frontend tests).

## PS-03 objectives

| # | Requirement | Implementation | Tests | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Historical daily prices from a free source, cached so the app works offline | `backend/quantfolio/data/store.py`, `data/cache/datasets/` (Yahoo Finance via yfinance, 50 symbols, 2019-01-01 to 2026-10-08, SHA-256 in the metadata), `data/universe/SOURCE.md` | `core/test_data_risk.py` | `GET /api/data/status`; dataset id and hash in every report |
| 1 | Returns, expected returns and covariance; missing data handled; lookback documented | `backend/quantfolio/risk.py`, `backend/quantfolio/data/store.py` (missing-data policy text is part of the dataset metadata) | `test_simple_returns_annualised_by_252`, `test_covariance_symmetric_psd`, `test_missing_data_policy`, `test_too_short_training_window_is_rejected` | `evidence/core-tests/` |
| 1 | Estimation and test periods separate (no look-ahead) | `DateSplit` in `contracts.py` (train / validation / test), `pipeline.py`, `screening.py` | `test_training_estimates_ignore_everything_after_train_end`, `test_held_out_metrics_use_only_their_window`, `core/test_pipeline.py::test_held_out_prices_cannot_change_anything_trained` | `evidence/core-tests/` |
| 2 | Selection problem solved with QAOA; the circuit produces the answer | `backend/quantfolio/quantum/{circuit,qaoa,sampling}.py`, `qubo.py`; result taken from the saved final sampling batch | `core/test_qaoa.py` (all), `core/test_pipeline.py::test_run_result_provenance`, `test_baselines_cannot_influence_the_quantum_result`, `api/test_e2e_real.py::test_default_example_runs_for_real` | `data/examples/run-20261008-f29dc129/` (result and raw counts), `evidence/load/real_quick.json` |
| 2 | Effect of depth, two optimisers, two initial-point strategies | Depths 1 to 5, COBYLA and SPSA (`quantum/optimizers.py`), random and `warm_interp` (`quantum/warm_start.py`); `POST /api/experiments`, `backend/scripts/experiments.py` | `core/test_pipeline.py::test_experiment_designs_are_bounded`, `api/test_jobs_api.py::test_experiment_runs_and_reports` | `evidence/experiments/RESULTS.md`, `factorial_small_universe.json`, `universe_size.json`, `cells.csv` |
| 3 | At least two constraint types beyond cardinality; adding one does not rewrite the solver | `backend/quantfolio/constraints/` (`cardinality`, `sector`, `lots`, `target_return`, `costs`, common interface in `base.py`) | `core/test_constraints_qubo.py` (all 10) | `evidence/core-tests/` |
| 4 | Exact brute force, relaxation with rounding, one heuristic; same data and constraints | `backend/quantfolio/classical/{exact,relaxation,annealing,greedy}.py`, shared evaluator `portfolio.py` | `core/test_classical.py` (all 5) | Example run: QAOA, exact, relaxation and annealing all report objective -0.18992 for the same bitstring |
| 5 | Re-run the best configuration under a fake-backend noise model and report the degradation | `backend/quantfolio/quantum/noise.py`, noise block of `pipeline.py` | `core/test_qaoa.py::test_fake_backend_noise_on_tiny_circuit`, `core/test_pipeline.py::test_noise_comparison_is_paired_and_labelled` | `evidence/experiments/RESULTS.md` |
| 5 | Real IBM hardware optional, with job ids | Partial. `backend/quantfolio/ibm.py`: account status and an explicit access check (API and command line); `ibm_jobs` table for job ids. The `submit` command (needs `--confirm`) exists but currently stops with "hardware re-sampling is unavailable" | `api/test_ibm.py` (all 8, provider mocked) | Not run on hardware; see "Not done" |
| 6 | Polished web app: pick assets, risk appetite, size, constraints; run; watch results update | `frontend/src/views/` (`Overview`, `lab/`, `run/`, `Experiments`, `History`), SSE client `frontend/src/api/events.ts` | `frontend/e2e/journey.spec.ts`, `views.spec.ts`, component tests in `frontend/src/**/*.test.ts(x)` | `evidence/e2e/` |
| 6 | Portfolio with return, variance and objective against the frontier and classical solutions; convergence curve; bitstring distribution | `frontier.py`, `assemble.py`, `frontend/src/components/charts/`, `frontend/src/views/run/RunResultView.tsx` | `frontend/e2e/journey.spec.ts`, `api/test_reports.py` | `evidence/e2e/` |
| 6 | Loading, errors and long-running jobs handled gracefully; usable on mobile | Job queue, SSE progress, cancel and timeout (`backend/quantfolio/jobs/`), error envelope (`api/errors.py`); responsive views | `api/test_jobs_api.py`, `api/test_sse.py`, `api/test_restart.py`, `frontend/e2e/states.spec.ts`, `perf.spec.ts` | `evidence/api-tests/`, `evidence/load/summary.md`, `evidence/e2e/` |

## Allowed tools and rules

| Rule | How it is met | Check |
| --- | --- | --- |
| Qiskit stack | Qiskit 2.5.2, Qiskit Aer 0.17.2 (`SamplerV2`), fake backends from Qiskit IBM Runtime | `backend/requirements.lock.txt`; `qaoa.primitive` in every result |
| Python + FastAPI, React + Vite + Tailwind, open-source solvers only | `backend/requirements.in`, `frontend/package.json`; CVXPY with open-source solvers | lock files |
| Free data, cached | Yahoo Finance via yfinance, cached Parquet with metadata; the app starts and runs without network | `core/test_data_risk.py`; load and API tests run offline |
| Hardware optional | Local Aer is the default; no token needed | `api/test_ibm.py::test_status_unconfigured_makes_no_call` |
| Not solving classically and wrapping it in a circuit | QAOA result is the best feasible bitstring observed in the saved final batch; baselines run separately | `test_baselines_cannot_influence_the_quantum_result`, `test_no_feasible_sample_is_not_replaced_by_a_baseline` |
| No undeclared optimal initial state | Initialisation is declared in the config and the result (`random` or `warm_interp`) | `core/test_qaoa.py::test_qaoa_bookkeeping_and_provenance` |
| No future data in estimation | Train / validation / test split enforced in the pipeline | `test_held_out_prices_cannot_change_anything_trained` |
| No unsupported quantum-advantage claims | Results wording is generated from measured values; gaps are reported only against an exact reference | `core/test_classical.py::test_gap_normalisation_is_safe`, `api/test_reports.py::test_html_report_has_provenance_disclaimer_and_csp` |

## POC guardrails

| Guardrail | Implementation | Tests | Evidence |
| --- | --- | --- | --- |
| Look-ahead | Training-only estimation, screening and tuning (`risk.py`, `screening.py`, `pipeline.py`); held-out metrics computed separately | `test_training_estimates_ignore_everything_after_train_end`, `test_held_out_prices_cannot_change_anything_trained`, `test_selection_rule_is_deterministic_and_training_only` | `evidence/core-tests/` |
| Initial state | `init_strategy` is part of the config, cache key and report; no planted solution | `test_qaoa_bookkeeping_and_provenance`, `test_same_seed_reproduces_and_seed_changes_result` | Example run `result.json` |
| Sampling | Result traces to `final_counts.json`; "no feasible sample" is a reported status, never repaired | `test_no_feasible_sample_is_reported_not_repaired`, `api/test_reports.py::test_counts_csv_matches_artifact_and_shots`, `api/test_e2e_real.py` (counts sum equals final shots) | `data/examples/run-20261008-f29dc129/final_counts.json` |
| Feasibility | One shared validator (`portfolio.py`, `constraints/base.py`) re-checks every solver's output and reports residuals | `core/test_constraints_qubo.py::test_penalty_zero_iff_feasible_and_energy_decomposition`, `core/test_classical.py::test_heuristics_feasible_and_bounded_by_exact` | `evidence/core-tests/` |
| Qubit / time limits | Server preflight (`POST /api/preflight`, also run on submit); limits 24 qubits ideal, 14 with noise, 20 assets for exact enumeration; bounded queue (8) and one compute worker; evaluation budget, wall-clock timeout, cancellation | `core/test_pipeline.py::test_preflight_*`, `api/test_api_schemas.py::test_preflight_failure_is_422_with_report`, `api/test_jobs_api.py::test_queue_saturation_returns_429_with_retry_after`, `test_hung_job_times_out`, `test_cancel_running_job`, `test_uncooperative_job_is_force_cancelled_after_grace` | `evidence/experiments/qubit_benchmark.json`, `evidence/load/summary.md` (saturation, cancel storm) |
| Hardware quota | `GET /api/ibm/status` never calls the network; `POST /api/ibm/check` is explicit and submits nothing; hardware submission only from the command line with `--confirm` | `api/test_ibm.py` (all 8) | `evidence/load/summary.md` (mocked provider outage: 502 / 504, no token leak) |
| Data quality | Dataset label, source, dates, hash, staleness and issue list in `DatasetInfo`; uploads validated and labelled `uploaded` | `core/test_data_risk.py::test_missing_data_policy`, `test_upload_*`, `api/test_upload.py` (all 6) | `data/cache/datasets/*.json` |
| Fair comparison | All solvers receive the same problem object (data, universe, constraints, units, objective) and are scored by the same evaluator | `core/test_classical.py::test_exact_matches_brute_force`, `core/test_pipeline.py::test_run_result_provenance` | Example run comparison table |
| QUBO correctness | Exhaustive checks of encoding, slack ranges, energies, offsets and the Ising mapping on small instances | `core/test_constraints_qubo.py` (all 10), `core/test_qaoa.py::test_cost_layer_phases_equal_encoded_energies`, `test_sampled_energies_match_exhaustive_enumeration` | `evidence/core-tests/` |
| Results wording | Provenance (primitive, simulator, seeds, versions, dataset hash) in the result and every export; cached replays are labelled with the original run time | `api/test_reports.py::test_json_report_equals_stored_result`, `test_csv_numbers_equal_stored_result`, `api/test_job_store.py::test_cache_replay_is_marked_and_not_a_new_run` | `evidence/api-tests/` |
| Disclaimer | "Research demonstration - not investment advice" in the result contract, the HTML report and the result views | `api/test_reports.py::test_html_report_has_provenance_disclaimer_and_csp` | Any `report.html` |

## Platform requirements (master prompt section 3 and 10)

| Requirement | Implementation | Tests | Evidence |
| --- | --- | --- | --- |
| Heavy work outside the API event loop; Windows-compatible worker | Supervisor process plus one spawned child per job (`jobs/worker.py`, `jobs/child.py`) | `api/test_jobs_api.py::test_crash_is_isolated_and_next_job_runs`, `api/test_process.py` | `evidence/api-tests/` |
| Durable job states with explicit transitions | SQLite WAL store with conditional updates (`jobs/store.py`) | `api/test_job_store.py` (all 14) | `evidence/api-tests/` |
| Restart reconciliation without duplicate execution | Lock file, reconcile-on-start, atomic claim | `api/test_restart.py` (all 4), `api/test_job_store.py::test_restart_reconciliation_exactly_once` | `evidence/load/summary.md` (worker kill and restart) |
| Cancellation and timeouts | Cooperative flag, hard kill after grace, wall-clock timeout | `test_cancel_running_job`, `test_cancel_race_never_leaves_inconsistent_state`, `test_hung_job_times_out`, `api/test_e2e_real.py::test_real_run_can_be_cancelled` | `evidence/load/summary.md` (cancel storm) |
| SSE with ids, heartbeat, reconnection, snapshot fallback, coalescing | `api/routes.py` (`/runs/{id}/events`, `/events/snapshot`) | `api/test_sse.py` (all 7) | `evidence/load/summary.md` (SSE fan-out, dropped connections) |
| Idempotent submission, bounded queue | `api/service.py`, `jobs/store.py` | `test_idempotency_key_replay_and_conflict`, `test_identical_active_submission_is_deduplicated`, `test_queue_saturation_returns_429_with_retry_after` | `evidence/load/summary.md` (saturation) |
| Result cache labelled, force-fresh, missing cache | `api/service.py` | `test_cache_replay_and_force_fresh`, `test_missing_cached_result_falls_back_to_fresh_run` | `evidence/load/summary.md` (missing cache) |
| Input validation and safe errors | pydantic contracts, body guard, error envelope | `api/test_api_schemas.py` (all 20), `api/test_upload.py` | `evidence/api-tests/` |
| Localhost, narrow CORS, no paths/URLs/commands from clients, secrets redacted | `api/app.py`, `api/middleware.py`, `logging_utils.py` | `test_trusted_hosts_only`, `test_cors_is_narrow`, `test_paths_and_urls_never_accepted`, `api/test_reports.py::test_no_credentials_in_reports_logs_or_db`, `test_spa_traversal_never_escapes_dist`, `api/test_ibm.py::test_worker_never_receives_the_token` | `evidence/api-tests/` |
| Reports: HTML, JSON, CSV, raw counts; exported numbers equal stored numbers | `reports/render.py` | `api/test_reports.py` (all 11) | `evidence/api-tests/` |
| Genuine saved example | `jobs/examples.py`, `data/examples/`, `GET /api/example` | `api/test_examples.py` (all 3) | `data/examples/run-20261008-f29dc129/` |
| One-command start, port conflicts, clean stop of own processes | `scripts/start.ps1`, `stop.ps1`, `quantfolio/serve.py` | `api/test_process.py::test_serve_lifecycle` | `docs/PERFORMANCE.md` (graceful stop, orphan check) |
| Load and resilience tests | `backend/loadtest/` | n/a (they are the tests) | `evidence/load/`, `docs/PERFORMANCE.md` |

## Not done

- No run on real IBM hardware: no token was configured on this machine. Status and the access check are tested only against a
  mocked provider. Re-sampling a stored run on hardware (`python -m quantfolio.ibm submit`) is not wired up: the numerical core
  does not yet expose the bound circuit of a stored run, so the command exits with a clear message and submits nothing.
- No authentication or per-user job ownership. The server is meant for localhost only.
- Load tests cover a single machine with one compute worker. They say nothing about production scale.
