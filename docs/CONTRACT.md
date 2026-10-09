# QuantFolio build contract (coordination doc for the three workstreams)

Root: `E:\All projects\QuantFolio`. Everything lives on **E:** — venv, pip/npm/Playwright caches, temp files.

## Environment (set in every shell that installs/builds/tests)

```powershell
$env:PIP_CACHE_DIR = "E:\All projects\QuantFolio\.cache\pip"
$env:npm_config_cache = "E:\All projects\QuantFolio\.cache\npm"
$env:PLAYWRIGHT_BROWSERS_PATH = "E:\All projects\QuantFolio\.cache\playwright"
$env:TEMP = "E:\All projects\QuantFolio\.tmp"; $env:TMP = $env:TEMP
```

- Python: `backend\.venv\Scripts\python.exe` (3.13.2, deps from `backend\requirements.in`, lock `backend\requirements.lock.txt`).
- Node 24.16.0 / npm 11.13.0. Frontend in `frontend\`.
- **RAM is scarce (~2.5 GB free of 16 GB).** Never run heavy jobs concurrently with another workstream if avoidable:
  pytest single process (no xdist), Playwright `workers: 1-2`, one compute worker, statevector only, no density-matrix noise sims.
- Ports: API `127.0.0.1:8000` (`QF_API_PORT`), Vite dev `127.0.0.1:5173` (proxy `/api` -> 8000). Production: FastAPI serves
  `frontend\dist` at `/` with SPA fallback; API under `/api`. Test instances may use 8100+/5200+ to avoid clashing.
- Only the orchestrator commits to git. Workstreams must not run `git commit`/`git add`.

## Shared files (orchestrator-owned; additive changes only)

- `backend/quantfolio/contracts.py` — pydantic request/response models (single source of truth). Backend agents may add
  optional fields with defaults; record every change in the changelog below.
- `backend/quantfolio/pipeline.py` — numerical entry points the platform calls:
  `preflight(config)`, `cache_key(config)`, `run_optimisation(config, run_id, sink, artifacts_dir)`,
  `run_experiment(config, run_id, sink, artifacts_dir)`, plus `ProgressSink`, `Cancelled`, `PipelineError`, `NullSink`.

## Ownership

| Workstream | Owns |
| --- | --- |
| **A — quantum core** | `backend/quantfolio/{data/, risk.py, portfolio.py, constraints/, qubo.py, quantum/, classical/, frontier.py, presets.py, explain.py, pipeline.py body}`, `backend/scripts/experiments.py`, `backend/tests/core/`, `docs/MATH.md`, `docs/DATA.md`, `evidence/experiments/`, `evidence/core-tests/` |
| **B — platform** | `backend/quantfolio/{api/, jobs/, reports/, settings.py, logging_utils.py, ibm.py}`, `backend/tests/api/`, `backend/loadtest/`, `scripts/*.ps1`, root `README.md`, `.gitignore`, `.env.example`, `docs/ARCHITECTURE.md`, `docs/COVERAGE.md`, `docs/PERFORMANCE.md`, `evidence/api-tests/`, `evidence/load/`, saved example run under `data/examples/` |
| **C — frontend** | `frontend/**` (incl. Playwright `frontend/e2e/`), `docs/UI.md`, `evidence/screenshots/`, `evidence/e2e/`, `evidence/a11y/` |

## HTTP API (prefix `/api`, JSON, models from contracts.py)

| Method & path | Body / query | Returns |
| --- | --- | --- |
| GET `/health` | — | `{status, version}` |
| GET `/ready` | — | `{ready, checks:{db, worker, dataset}}` |
| GET `/assets` | — | `AssetInfo[]` + dataset label |
| GET `/data/status` | — | `DatasetInfo` (+ available datasets) |
| POST `/data/upload` | multipart CSV (<= 5 MB) | `DatasetInfo` label `uploaded` |
| GET `/presets` | — | `Preset[]` + default example `RunConfig` |
| GET `/capabilities` | — | `Capabilities` |
| POST `/preflight` | `RunConfig` | `PreflightReport` |
| POST `/runs` | `RunConfig`; header `Idempotency-Key`; query `force_fresh` | 202 `JobStatus`; 200 same job on idempotent replay; 422 invalid/preflight error; 429 queue full (`Retry-After`) |
| GET `/runs` | `status, kind, limit, offset` | `RunSummary[]` |
| GET `/runs/{id}` | — | `JobStatus` |
| GET `/runs/{id}/result` | — | `RunResult` or `ExperimentResult`; 409 if not succeeded |
| POST `/runs/{id}/cancel` | — | `JobStatus` |
| GET `/runs/{id}/events` | SSE, `Last-Event-ID` / `?after=` | `RunEvent` stream, heartbeat every 10 s |
| GET `/runs/{id}/events/snapshot` | `after` | `RunEvent[]` (polling fallback) |
| GET `/runs/{id}/report.{html,json,csv}` and `/runs/{id}/counts.csv` | — | downloads |
| POST `/experiments` | `ExperimentConfig` (+ Idempotency-Key) | 202 `JobStatus` (kind `experiment`) |
| GET `/example` | — | `{run_id}` of the saved genuine example run |
| GET `/ibm/status` | — | `{configured, detail}` (never consumes quota) |

Job states: `queued -> running -> succeeded | failed | cancelled | timed_out`; `queued -> cancelled`.
After restart, jobs found `running` become `failed` with `error.code = "interrupted_by_restart"`; `queued` jobs stay queued.

SSE event `data` payloads: `stage` -> `{stage, message}`; `iteration` -> `IterationPoint` fields; `status` -> `JobStatus`;
`result` -> `{run_id}`; `error` -> `JobError`.

## Changelog of contract changes

- 2026-10-08: initial version (orchestrator).
- 2026-10-08 (B): `JobStatus` gains optional `cancel_requested: bool = False` and `timeout_seconds: int | None`.
  Added wrapper models `ErrorBody`/`ErrorEnvelope` (every non-2xx `/api` JSON body is `{"error": {code, message, details?, request_id?}}`),
  `HealthResponse`, `ReadyResponse`, `AssetsResponse {dataset, assets}`, `DataStatusResponse {active, available}`,
  `PresetsResponse {presets, default_config}`, `ExampleResponse {run_id, label, recorded_at, detail}`, `IbmStatus`.
  `GET /runs` returns `RunSummary[]` with the unfiltered total in the `X-Total-Count` header.
  SSE `iteration` events are coalesced to <= 4/s: `data` holds the latest `IterationPoint` fields **plus** `points`
  (every `IterationPoint` since the previous iteration event, in order, latest last), so no measured point is dropped.
  `status` events carry the full `JobStatus`; the stream closes after a terminal `status` event.
- 2026-10-08 (A): `DatasetInfo` gains optional `issues: list[str] = []` (data-quality findings such as late listings/gaps). `PeriodMetrics` gains optional `net_total_return: float | None` (held-out total return after the one-off transaction cost).
- 2026-10-09 (B): additions, all backwards compatible.
  New endpoints: `GET /runs/{id}/config` (the stored submitted `RunConfig`/`ExperimentConfig`, any status) and `POST /ibm/check`
  (explicit, user-initiated: lists visible backends, submits nothing; 400 `ibm_not_configured`, 502 `ibm_check_failed`, 504 `ibm_timeout`).
  `GET /health` also returns `schema_version`. Interactive reference at `/api/docs` (`/api/openapi.json`).
  `POST /runs` / `POST /experiments`: an identical succeeded run is replayed from cache with **200** and `cached_from` +
  `cached_original_time` (`?force_fresh=true` forces a new run); the same `Idempotency-Key` with a different body is 409
  `idempotency_key_reused`; queue full is 429 `queue_full` with `Retry-After`.
  `GET /runs/{id}/result` and the reports: 409 `result_not_available` unless the run succeeded, 410 `result_missing` if the stored file is gone.
  `GET /runs/{id}/events/snapshot` takes `after` and `limit` (1..2000, default 500).
  SSE heartbeat is a named event without an id (`event: heartbeat`, `data: {"type":"heartbeat","data":{}}`); resume with
  `?after=` and/or `Last-Event-ID` (the larger wins). Extra `error.code` values written by the platform: `worker_crashed`,
  `interrupted_by_restart`, `interrupted_by_shutdown`, `internal_error`.
  Unknown `/api` paths return a JSON 404 `not_found`; a known path with the wrong method returns 405 `method_not_allowed` with `Allow`.
  HTML reports and the web app are served with a content security policy; all responses carry `X-Request-ID`.
