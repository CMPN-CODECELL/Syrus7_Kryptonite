# QuantFolio workstream status

Readiness notes from each workstream. Append; keep entries short and dated.

## Orchestrator

- 2026-10-08: First session ended right after the three workstreams started (the host IDE was closed). Workstreams relaunched in a new session. Do not close or kill `Antigravity IDE.exe` or other user apps: Claude Code runs inside it.
- 2026-10-09: A, B and C finished. Orchestrator browser pass in Chrome against the real server (`scripts\start.ps1 -Background -Rebuild`): Overview, Lab, cached replay, a fresh Quick run (7.2 s, live SSE; the access log shows the stream as 200 for 7.5 s), History, the not-found page, an unknown run id, the mobile menu, and the HTML/JSON/CSV/counts downloads. No console messages, no failed requests, `content-encoding: gzip` on `/api/presets` and none on the event stream, exported numbers equal to the screen. Fixed the backend wording "1 assets held" (`constraints/sector.py`); core suite rerun: 154 passed in 66 s. The API suite was not rerun for this one-word change.
- 2026-10-09: Commits are local only and are not pushed.

## A

- 2026-10-08 (relaunch): interfaces B asked for exist with exactly these names (no adaptation needed):
  `quantfolio.data.list_assets(dataset_id=None) -> (list[AssetInfo], DatasetInfo)`, `quantfolio.data.dataset_status()`,
  `quantfolio.data.ingest_upload(data, filename) -> DatasetInfo` (raises `pipeline.PipelineError`), `quantfolio.presets.list_presets()`,
  `quantfolio.presets.default_example_config()`, `quantfolio.presets.core_capabilities()` (the 9 core Capabilities fields).
  `run_optimisation` writes `artifacts_dir/final_counts.json` (`{bitstring: count}`, full final batch). Optional
  `quantfolio.pipeline.warmup()` pre-imports pandas/cvxpy/qiskit and loads the default dataset (call it in the pre-spawned child).
  `pipeline.py` bodies are landing now; readiness of the real pipeline will be posted here next.
- 2026-10-09: **real pipeline READY** (`QF_PIPELINE_IMPL=real`). `preflight`, `cache_key`, `run_optimisation`, `run_experiment`, `warmup` implemented.
  Default example (`presets.default_example_config()`: 8 NIFTY assets, K=4, Financial Services cap 1, binding 15% target, 25 bps + INR 20 from holdings,
  depth-1 COBYLA seed 7) -> 10 qubits (8 decision + 2 slack); real Aer SamplerV2 run 4.8 s warm, ~9 s in a fresh process incl. imports;
  QAOA, exact, relaxation+rounding and SA all hit the exact optimum; `final_counts.json` (+ `.sha256`) written to `artifacts_dir`.
  Warm `preflight` ~10 ms (no qiskit/cvxpy import). `warmup()` returns measured seconds per import group (~1-10 s cold).
  `PipelineError.details` = `{conflict_kind, issues:[Issue...]}`; QAOA without feasible samples -> solver status `no_feasible_sample` (run still succeeds).
- 2026-10-09 (final): workstream A complete. Default example in a warm worker: quick median 2.49 s / p95 2.86 s (5 runs; 8.3-9.8 s in a fresh process),
  balanced median 9.07 s, research median 26.75 s. Limits from the benchmark: 24 qubits ideal, 14 qubits noisy, 1500 MB, 20 assets.
  Core tests: 154 passed, 0 failed, 0 skipped (`evidence/core-tests/junit.xml`, `pytest.txt`). Experiment matrix executed: 72 QAOA runs + 5 paired
  noise trials (`evidence/experiments/RESULTS.md`, `cells.csv`, `qubit_benchmark.json`, `presets_calibration.json`). `docs/MATH.md` and `docs/DATA.md` written.
  No `contracts.py` changes were needed. Not supported, rejected by preflight: count-basis sector caps in lots mode; warm_interp with noisy optimisation at depth > 1.

## B

- 2026-10-08 (relaunch): building platform against a fake pipeline (`QF_PIPELINE_IMPL=fake`). Interface B will call from A
  (beyond the four `pipeline.py` functions). Please provide these names, or post the actual names here and B will adapt:
  - `quantfolio.data.list_assets(dataset_id: str | None = None) -> tuple[list[AssetInfo], DatasetInfo]`
  - `quantfolio.data.dataset_status() -> tuple[DatasetInfo, list[DatasetInfo]]` (active, available)
  - `quantfolio.data.ingest_upload(data: bytes, filename: str) -> DatasetInfo` (raise `PipelineError` with a safe message on bad input)
  - `quantfolio.presets.list_presets() -> list[Preset]` and `quantfolio.presets.default_example_config() -> RunConfig`
  - `quantfolio.presets.core_capabilities() -> dict` with the core fields of `Capabilities`
    (`max_qubits, max_qubits_noise, max_exact_assets, depths, optimizers, init_strategies, fake_backends, modes, limits`);
    B fills `queue_capacity` and `ibm_configured`.
  - Full raw counts of the final batch written by `run_optimisation` as `artifacts_dir/final_counts.json`
    (`{bitstring: count}`; B serves it as `counts.csv`). Anything else in `artifacts_dir` is kept with the run.
  - Worker contract: `run_optimisation` runs in a spawned child process; `sink.check_cancelled()` raises `Cancelled`;
    `artifacts_dir` exists and is private to the run; the child's TEMP/TMP point at `artifacts_dir/tmp` (deleted afterwards).

- 2026-10-08: **API runnable** (fake pipeline until A posts readiness; then the same command with `QF_PIPELINE_IMPL=real`, the default).
  From `backend/` in PowerShell (private data dir so e2e never touches `data/`):
  ```
  $env:QF_PIPELINE_IMPL="fake"; $env:QF_DATA_DIR="E:\All projects\QuantFolio\.tmp\e2e-data"; $env:QF_CORS_ORIGINS="http://127.0.0.1:5200,http://localhost:5200"
  .venv\Scripts\python.exe -m quantfolio.serve --port 8100
  ```
  Verified: boots in ~2 s, `/api/ready` reports `worker:true` within ~3 s, a default job succeeds in <1 s (`QF_FAKE_STEP_S`, default 0.02 s/eval).
  Stop: create `<QF_DATA_DIR>\state\api.stop` (graceful, also stops the worker) or Ctrl+C. Port in use -> exit code 4. Docs at `/api/docs`.
  Fake-pipeline magic seeds (`qaoa.seed`): 666 crash -> `worker_crashed`, 777 -> `internal_error`, 888 hang (use `timeout_seconds`=10 -> `timed_out`), 999 -> `infeasible_config`.
- 2026-10-08: answers to C's requests:
  1. Added `GET /api/runs/{run_id}/config` -> the stored submitted config JSON (`RunConfig`, or `ExperimentConfig` for `exp-*`), for every status.
     `JobStatus` is unchanged.
  2. SSE confirmed: `id: <seq>`, `event: <EventType>`, `data: <full RunEvent JSON>` (`{id, run_id, ts, type, stage, data}`). Resume with
     `?after=<seq>` and/or `Last-Event-ID` (the larger wins). Heartbeat every 10 s is a named event with no id:
     `event: heartbeat` + `data: {"type":"heartbeat","data":{}}`. The stream closes after the terminal `status` event (and its
     `result`/`error` event); resuming after the end closes immediately. One `iteration` event per <=250 ms carries `points` (all
     evaluations since the previous one) plus the latest point's fields. Fallback: `GET /runs/{id}/events/snapshot?after=&limit=` (same RunEvents).
  3. Confirmed: `GET /runs?status=&kind=&limit=1..200&offset=` (newest first), total in `X-Total-Count` (exposed via CORS).
  Other behaviour: cached replay of an identical config returns **200** with `cached_from` + `cached_original_time` (use `?force_fresh=true`
  for a new run); same Idempotency-Key + different body -> 409 `idempotency_key_reused`; queue full -> 429 `queue_full` + `Retry-After`;
  `/result` and reports -> 409 `result_not_available` unless succeeded, 410 `result_missing` if the file is gone.
- 2026-10-09: **switched to the real pipeline** (default `QF_PIPELINE_IMPL=real`). Verified over HTTP+SSE on an isolated server:
  5 real Quick jobs, all succeeded (fresh runs 10.6-16.5 s wall, cache replay 0.4 s), clean stop, no orphans (`evidence/load/real_quick.json`).
  Genuine example recorded: `data/examples/run-20261008-f29dc129/` (default config, 10 qubits, QAOA = exact optimum `00101101`),
  served at `GET /api/example` (imported read-only at startup). For e2e against real runs drop `QF_PIPELINE_IMPL` and use a data dir
  that contains `cache/datasets` (or the repo `data/` dir itself, which is what `scripts/start.ps1` uses).
- 2026-10-09 (final): **workstream B complete.**
  - Tests: `scripts\test.ps1 -Suite api` -> 146 passed, 0 failed, 0 skipped in 439 s (`evidence/api-tests/junit.xml`, `pytest.txt`).
  - Load (fake pipeline, `evidence/load/`, write-up in `docs/PERFORMANCE.md`): 1/10/25/50 users for 40 s each plus a 180 s soak at
    10 users, 6611 requests, 0 failures; p95 10 / 15 / 26 / 100 ms and 14 ms in the soak; at 50 users 16 submissions answered 429.
    Seven failure scenarios passed (queue saturation, SSE fan-out, cancel storm, dropped SSE connections, missing cached result,
    worker kill and restart, mocked provider outage). SSE lag p95 0.30-0.32 s with 1 to 40 clients (`sse_latency.json`).
  - Real Quick jobs over HTTP (`real_quick.json`, `real_quick_idle15.json`): 8 fresh runs, all succeeded, 4.8-15.8 s
    (5-7 s after 15 s idle, 10-16 s straight after start-up or back to back); cache replay 0.35 s; peak 498 MB for the process tree.
  - Docs written by B: `docs/ARCHITECTURE.md`, `docs/COVERAGE.md`, `docs/PERFORMANCE.md`, `.env.example`, `.gitignore` (keeps the bundled
    dataset and `data/examples/`). Root `README.md` now belongs to the orchestrator.
  - Scripts: `start.ps1`, `stop.ps1`, `test.ps1 -Suite api` and `bench.ps1 -Smoke` were run end to end. `setup.ps1`, `dev.ps1`, the
    core and frontend parts of `test.ps1` and the full `bench.ps1` were not run through the script (their Python commands were run directly).
    A smoke pass now writes to `evidence/load/smoke/` so it cannot overwrite a full run.
  - Open: no IBM hardware run. `python -m quantfolio.ibm submit` stops with "hardware re-sampling is unavailable" because
    `quantfolio.quantum.bound_circuit_for_run(result: dict)` does not exist; if A adds it, `submit` works without changes in B.
    No ruff configuration in the repo (ruff 0.16 defaults report style findings only, no pyflakes errors, in platform code).

## C

- 2026-10-08 (relaunch): npm install verified (no reinstall). Contract types regenerate from `contracts.py` via `npm run gen:types`
  (`npm run check:types` fails on drift); currently in sync with B's wrapper models (ErrorBody envelope, Health/Ready, AssetsResponse,
  PresetsResponse.default_config, ExampleResponse, IbmStatus, JobStatus.cancel_requested/timeout_seconds, X-Total-Count).
  Typed client, SSE stream (Last-Event-ID via `?after=`, backoff reconnect, snapshot polling fallback), design system and 3D/SVG hero done.
  Building views now against the contract; e2e will run against the real API on 127.0.0.1:8100 (Vite on 5200) once B posts it runnable.
- **2026-10-09, C paused at the usage limit (handover).**
  - **Done before the pause:** all five views and the required states are built. Lint, typecheck, Vitest (38 tests, 0 failures) and build were green at the last check, and no source file has changed since. Specs written: `e2e/journey.spec.ts` (real pipeline), `e2e/states.spec.ts` (16 tests, fake pipeline), `e2e/views.spec.ts` (screenshots at 360/390/768/1280/1440 plus axe) and `e2e/perf.spec.ts`. Last runs: `chromium-states` 16 passed, 0 failed (fake API, run before the zero-errors guard existed). `chromium` 12 passed, 2 failed (real API: run-view overflow and perf `/lab` CLS); the journey passed. axe found no serious or critical issues on any view at 1280 or 390. The screenshots in `evidence/screenshots/` predate the fixes and must be retaken.
  - **Not done:** the shared zero-errors guard in `e2e/helpers.ts` (console error/warning, pageerror, unhandled rejection, HTTP >= 400 and failed requests, with per-test allow-lists); error boundaries around each view (inside Layout, using `ErrorState` with retry) and around `HeroScene` (falling back to `HeroFallback`); a scroll-to-top `capture()` helper; review of ALL screenshots; a check of each state in the real app; the Firefox/WebKit journey; `docs/UI.md`; the final report.
  - **Diagnosed but not fixed:** (a) Run view overflows by 488 px at 360/390 because a plain `grid gap-5` has an implicit auto track that grows to the min-content width of the tab strip. Fix: add `grid-cols-1` at `src/views/lab/Controls.tsx:28`, `src/views/run/RunResultView.tsx:24,128,293,365` and `src/views/run/ExperimentResultView.tsx:18`. (b) Console warnings on a clean walk: react-router "No `HydrateFallback` element provided" (fix: add `HydrateFallback` to the root route in `src/App.tsx`); "THREE.Clock: This module has been deprecated", raised by R3F 9.8.1's store on three 0.186.1 (fix: pin `three` below 0.183 or update R3F); headless "GPU stall due to ReadPixels", a software-GL driver message from Chromium, not app code (fix: Chromium launch flags, otherwise an exact allow-list documented in `docs/UI.md`). The walk saw no pageerror, failed request or HTTP >= 400.
  - **The eight UI defects from the coordinator: all 8 NOT FIXED** (no source edits since the list arrived). 1 sticky header in full-page captures and `scroll-margin-top`: not fixed. 2 truncated run ids: not fixed (`shortId` in `src/lib/format.ts` returns `run-2026`; used at `RunView.tsx:30,67,108` and `History.tsx:138`; update `format.test.ts`). 3 duplicated disclaimer: not fixed (the Notice body in `RunResultView.tsx` and `ExperimentResultView.tsx`). 4 memory/runtime formatting: not fixed (`formatBytesMb`/`formatDuration`; `Preflight.tsx:125-135`). 5 clipped submit button: not fixed (the `#preflight` aside in `LabView.tsx`, `lg:max-h-[calc(100dvh-6rem)] lg:overflow-y-auto`). 6 static hero tile collisions and the 130 px gap: not fixed (orbit projection in `HeroFallback.tsx`; `items-center` and top padding in `Overview.tsx`). 7 lone experiment chart and heading/selector row: not fixed (`xl:grid-cols-2` in `ExperimentResultView.tsx`). 8 wide-table scroll affordance and sticky first column: not fixed (`src/components/ui/Table.tsx`).
  - **perf.spec.ts failure:** on the enforced target `preview-gzip` (5202), `/lab` CLS max was 0.2776 against a 0.1 budget. LCP passed: 2288 ms median on both `/` and `/lab`, and `/` CLS was 0.0018. The cause is one shift at about 3.8 s: the footer, visible below the short loading skeleton, is pushed down when the Lab renders. Fix: `min-h-dvh` on `<main>` in `src/components/layout/Layout.tsx`. The recorded, non-enforced `api-served-uncompressed` target (8101) had LCP 7.1-7.6 s.
  - **For B (to file under Requests for B):** the fake `default_config` has `constraints.cardinality: null`. The API serves `frontend/dist` uncompressed; gzip that skips `text/event-stream` would fix it.
  - **Processes:** all five of C's servers are stopped: fake API 8100 and real API 8101 via `api.stop` files; previews 5200, 5201 and 5202 killed by PID. No Playwright browser is running.
  - **Next, after making the source fixes above:** `cd frontend && npm run lint && npm run typecheck && npm run test && npm run build`. Then restart the servers: fake API `QF_PIPELINE_IMPL=fake QF_DATA_DIR=<repo>\.tmp\e2e-data python -m quantfolio.serve --port 8100`; real API `QF_DATA_DIR=<repo>\.tmp\e2e-real ... --port 8101`; previews `QF_API_TARGET=http://127.0.0.1:8100 node node_modules/vite/bin/vite.js preview --port 5200 --strictPort`, the same on 5201 pointing at 8101, and 5202 pointing at 8101 with `QF_PREVIEW_GZIP=1`. Run `node ../.tmp/diag.mjs`, then `QF_E2E_TAG=chromium npx playwright test --project=chromium-states --project=chromium`, then `QF_E2E_TAG=firefox-webkit npx playwright test --project=firefox --project=webkit`.

- **2026-10-09, C final.** Everything from the handover is fixed: the eight coordinator UI defects, run-view overflow, `HydrateFallback`, three pinned to 0.182.0, the zero-errors guard (`e2e/helpers.ts`), per-view and hero error boundaries, the scroll-to-top `capture()`, the Lab `min-h-dvh` CLS fix. Since the last report, also: the phantom horizontal overflow at 1280 from the Shares header tooltip (now "Shares*" with a footnote), and the copy-id button moved next to the run id. ALL screenshots reviewed (360 and 1280 for every view, plus all states).
  - **Final runs (production build, one suite at a time, 1 worker):** Vitest 45/45. `chromium-states` 16/16: 32 provoked findings allowed (lost stream 6, API unreachable 26), 0 unexpected. `chromium` 14/14 with 0 console errors, 0 warnings, 0 page errors, 0 failed requests, 0 HTTP >= 400. `firefox` journey 1/1: 4 Firefox-only WebGL warnings on an explicit two-entry list, 0 unexpected. `webkit` journey 1/1, all zero. axe: 16 scans, 0 violations of any impact. Perf (mobile, Slow 4G, 4x CPU, gzip preview): LCP median `/` 2356 ms, `/lab` 2308 ms; CLS 0.002; control response median 18.9 ms (35.3 ms at 4x CPU). Details are in `docs/UI.md`.
  - **Not clean:** the Firefox Playwright runner did not exit after its test passed (the worker stayed alive after the browser closed), so there is no Firefox JSON/JUnit report; the list output is in `evidence/e2e/playwright-firefox.log`. The auto-mode permission check would not let C stop that worker process, so it may still be running (`node ... playwrightlibworkerworkerProcessEntry.js` under `playwright test --project=firefox`). Known limits are listed in `docs/UI.md`.
  - **Processes:** C's two APIs (8100, 8101) and three previews (5200-5202) are stopped at the end of this session.

## Requests for B

- C, 2026-10-08: (1) Please include the submitted `RunConfig` in `GET /runs/{id}` (e.g. `JobStatus.config`) or add `GET /runs/{id}/config`,
  so a failed/cancelled/timed-out run can be retried or edited after a page reload. Until then C keeps a local copy per run id.
  (2) Please confirm the SSE wire format C implements: `id: <seq>`, `event: <EventType>`, `data: <RunEvent JSON>` (C also accepts the bare
  payload), resume with `?after=<seq>` and the `Last-Event-ID` header, heartbeat as `event: heartbeat` or a `:` comment.
  (3) C sends `GET /runs?status=&kind=&limit=&offset=` exactly as CONTRACT.md lists; C reads `X-Total-Count` for paging.
- C, 2026-10-09: gzip from the API is verified live: transfer for `/` and `/lab` matches the gzip preview (629 kB and 402 kB). For information only: `Cross-Origin-Opener-Policy: same-origin` makes Chromium swap renderer processes on a fresh navigation, about 2.4 s in headless cold loads here (LCP 4.9-5.6 s from a fresh tab, 2.3 s when already on the origin). No change requested unless COOP is not needed.
