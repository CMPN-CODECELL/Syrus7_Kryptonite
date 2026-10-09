# Evidence

Recorded output of the test suites, load tests, experiments and browser checks. Everything here is produced by the scripts named below. Nothing in it is written by hand except this index and `experiments/RESULTS.md`.

| Folder | Contents | Produced by |
| --- | --- | --- |
| [`core-tests/`](core-tests/) | JUnit XML and text output of the numerical test suite | `scripts\test.ps1 -Suite core` |
| [`api-tests/`](api-tests/) | JUnit XML and text output of the platform test suite | `scripts\test.ps1 -Suite api` |
| [`e2e/`](e2e/) | Vitest JUnit, Playwright JSON and JUnit reports, page performance (`perf.json`) | `npm test`, `npm run e2e` in `frontend` |
| [`a11y/`](a11y/) | axe-core results for every view, desktop and mobile | `npm run e2e` |
| [`screenshots/`](screenshots/) | Every view at 360, 390, 768, 1280 and 1440 px; `states/` holds loading, empty, offline, error, cancelled, failed and timed-out states | `npm run e2e` |
| [`load/`](load/) | Locust stages (`ramp-01` to `ramp-50`, `soak-10`), failure scenarios, SSE lag, real Quick job timings, resource samples; `summary.md` is the readable summary; `smoke/` is the 1-minute pass | `scripts\bench.ps1`, `scripts\bench.ps1 -Smoke` |
| [`experiments/`](experiments/) | QAOA experiment matrix, universe-size study, qubit benchmark, preset calibration; `RESULTS.md` explains them | `backend\scripts\experiments.py` |

The numbers are summarised in the main [README](../README.md#performance-and-resilience) and in [`docs/PERFORMANCE.md`](../docs/PERFORMANCE.md). All measurements come from one Windows laptop (Intel i5-1135G7, 16 GB RAM).
