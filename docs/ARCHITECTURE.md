# Architecture

QuantFolio is a single-machine application: one API process, one job supervisor, and one child process per job.
State lives in SQLite and plain files under `data/`. Nothing leaves `127.0.0.1` unless the user explicitly runs the optional IBM check.

```
 Browser (React app, served from frontend/dist)
    |  HTTP + Server-Sent Events, same origin, /api/*
    v
 API process            python -m quantfolio.serve          (FastAPI on uvicorn, 127.0.0.1:8000)
    |  validates, preflights, queues, serves status / events / results / reports
    |  starts and watches the supervisor; never runs numerical work itself
    v
 SQLite (WAL)           data/state/quantfolio.sqlite3        jobs, events, idempotency, worker heartbeat
    ^
    |  claims queued jobs, writes progress events and the terminal state
 Supervisor             python -m quantfolio.jobs.worker     one per data directory (lock file)
    |  multiprocessing spawn + Pipe, shared cancel Event
    v
 Job child              quantfolio.jobs.child                exactly one pipeline call, then exits
    |  quantfolio.pipeline.run_optimisation / run_experiment (Qiskit Aer, classical baselines)
    v
 data/runs/<run_id>/    result.json, final_counts.json       reports are rendered from result.json
```

## Components

| Module | Responsibility |
| --- | --- |
| `quantfolio/serve.py` | Entry point. Checks the port (exit 4 if taken), writes `data/state/api.pid` (`pid`, `create_time`, `port`), shuts down gracefully when `data/state/api.stop` appears. |
| `quantfolio/api/app.py` | App factory, middleware stack, lifespan (starts/stops the supervisor), serves the built web app with SPA fallback. |
| `quantfolio/api/routes.py` | All `/api` endpoints (see `docs/CONTRACT.md`). |
| `quantfolio/api/service.py` | Submission rules: extra validation, preflight, idempotency, cache replay, queue admission. |
| `quantfolio/api/core.py` | Thin adapter to the numerical core (`data`, `presets`, `pipeline`); a missing core function becomes a clean 503. |
| `quantfolio/api/middleware.py` | Request id, access log, security headers; body guard (size limits, strict JSON without NaN/Infinity, depth limit). |
| `quantfolio/api/errors.py` | One error envelope for every non-2xx `/api` response; messages are redacted. |
| `quantfolio/api/worker_proc.py` | Starts, monitors and stops the supervisor; restarts it if it dies (at most 5 times in 5 minutes). |
| `quantfolio/jobs/store.py` | SQLite job store and state machine. |
| `quantfolio/jobs/worker.py` | Supervisor: single-instance lock, restart reconciliation, claim loop, cancellation, timeouts, heartbeat. |
| `quantfolio/jobs/child.py` | Job child process and its progress sink. |
| `quantfolio/jobs/fake_pipeline.py` | Deterministic stand-in pipeline for platform tests and load tests (`QF_PIPELINE_IMPL=fake`). |
| `quantfolio/jobs/examples.py` | Exports a succeeded run as the saved genuine example in `data/examples/`. |
| `quantfolio/reports/render.py` | HTML, JSON, CSV and counts-CSV reports rendered from the stored result. |
| `quantfolio/ibm.py` | Optional IBM status and explicit access check. Never used by jobs, tests or load tests. |
| `quantfolio/settings.py`, `logging_utils.py` | Environment settings; JSON logs with request/run ids and secret redaction. |

## Job lifecycle

```
queued -> running -> succeeded | failed | cancelled | timed_out
queued -> cancelled
```

1. **Submit** (`POST /api/runs`, `POST /api/experiments`). The body is parsed by the pydantic contract models, checked again
   (dataset id shape, date window, symbols) and run through the pipeline's `preflight`; a blocking finding returns 422 with the issues.
2. **Idempotency.** With an `Idempotency-Key` header, repeating the same request returns the same job (200); the same key with a
   different body returns 409.
3. **Cache.** The pipeline's `cache_key` covers the configuration, dataset hash, seeds and implementation version. An identical
   succeeded run is replayed: a new job row that points at the original (`cached_from`, `cached_original_time`), returned with 200.
   `?force_fresh=true` skips the cache. If the original result file has been deleted, the run is executed again.
4. **Admission.** At most `QF_QUEUE_CAPACITY` (8) jobs may be queued or running. Beyond that the API answers 429 with `Retry-After`.
5. **Claim.** The supervisor takes the oldest queued job with one conditional `UPDATE ... WHERE status='queued'`, so a job can
   only ever be claimed once.
6. **Run.** The job runs in a child process created with `spawn`. One child is kept pre-spawned while idle so imports are
   already done ("warm"). The child sends stages, optimiser evaluations and the final outcome over a pipe.
7. **Progress.** The supervisor writes events to SQLite. Iteration events are coalesced to at most 4 per second; each carries
   every evaluation since the previous event, so no measured point is lost.
8. **Finish.** `result.json` is written atomically (temp file, fsync, replace) before the job row moves to `succeeded`.

Every state change is a conditional update, so a terminal state can never be overwritten and no transition is applied twice.

## Cancellation, timeouts and crashes

| Situation | Handling | Final state |
| --- | --- | --- |
| Cancel a queued job | Row moves straight to `cancelled`. | `cancelled` |
| Cancel a running job | Shared event is set; the pipeline checks it between evaluations. After a 3 s grace period the child is killed. | `cancelled` |
| Wall-clock timeout | Child is killed. | `timed_out` |
| Child dies (crash, out of memory) | Supervisor sees the dead process. | `failed` / `worker_crashed` |
| Pipeline raises | Message is redacted; details kept only for known `PipelineError`s. | `failed` |
| Supervisor dies | The API restarts it; on start it marks jobs left `running` as `failed` / `interrupted_by_restart` exactly once. Queued jobs stay queued and run. | `failed` |
| Server stops with a job running | The supervisor stops the child and records the reason. | `failed` / `interrupted_by_shutdown` |
| API dies | The supervisor notices its parent is gone and exits, stopping its own child. | as above on next start |
| Second supervisor on the same data directory | Refused by the lock file (exit code 3). | n/a |

Processes are only ever stopped through handles this application created (the supervisor's `Popen` handle, its children,
the PID in `api.pid` after its creation time is verified). Nothing is matched by name.

## Live progress (SSE)

`GET /api/runs/{id}/events` streams the stored events: `id: <seq>`, `event: <type>`, `data: <RunEvent JSON>`. A client resumes with
`Last-Event-ID` or `?after=<seq>`. A named `heartbeat` event is sent every 10 s. The stream closes after the terminal `status`
event. Because the stream is read from SQLite, any number of clients see the same ordered events, a dropped connection costs
nothing on the server, and `GET /api/runs/{id}/events/snapshot` returns the same events for polling.

## Storage

| Path | Contents |
| --- | --- |
| `data/state/quantfolio.sqlite3` | Tables `jobs`, `events`, `idempotency`, `worker_heartbeat`, `meta`, `ibm_jobs` (optional hardware submissions made from the command line). WAL mode; per-thread connections; multi-statement writes use `BEGIN IMMEDIATE`. |
| `data/state/api.pid`, `worker.pid`, `worker.lock` | Process bookkeeping. |
| `data/runs/<run_id>/` | `result.json`, `final_counts.json`, other pipeline artefacts. Run ids match `^(run\|exp)-\d{8}-[0-9a-f]{8}$`; paths are built only from validated ids. |
| `data/examples/<run_id>/` | The saved genuine example (`example.json`, `result.json`, `final_counts.json`), imported read-only at startup and served at `GET /api/example`. |
| `data/cache/datasets/` | Price datasets used by the pipeline. |
| `data/logs/` | Server output when started with `scripts\start.ps1 -Background`. |

## Security model

- Binds to `127.0.0.1`. Host header allow-list (`127.0.0.1`, `localhost`). CORS allows only the Vite dev origins, `GET`/`POST`, no credentials.
- Security headers on every response (`nosniff`, `DENY` framing, `no-referrer`, same-origin opener, a content security policy for the web app and for HTML reports).
- Request bodies: 256 KB limit for JSON, 5 MB for the CSV upload; NaN/Infinity and deeply nested JSON are rejected.
- Clients cannot supply paths, URLs, expressions or commands. Uploads are parsed as CSV, validated and stored as an immutable dataset whose id is generated from the content; the client's file name is kept only as a sanitised display label.
- Errors never contain stack traces, local paths, request bodies or secrets. Each response carries a request id that also appears in the logs.
- The IBM token is read from the environment by the API process only, is removed from the supervisor's environment, and is
  redacted from logs. `GET /api/ibm/status` never touches the network; `POST /api/ibm/check` is an explicit user action that
  lists backends and submits nothing.

## Why this shape

- **Separate processes** keep the API responsive during CPU-bound simulation and make a hard kill safe: a cancelled, timed-out
  or crashed job cannot corrupt the server.
- **One job at a time** fits the target machine (4 cores, a few GB free). Extra requests wait in a bounded queue instead of
  exhausting memory.
- **SQLite as the only coordination point** means no broker to install, and the job history survives restarts.
