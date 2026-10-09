# Performance and resilience measurements

Everything here was measured on one development laptop on 2026-10-09. The numbers describe this machine and this
workload only. Nothing is extrapolated to other hardware, to more workers or to production use.

## Setup

| Item | Value |
| --- | --- |
| Machine | Intel i5-1135G7 (4 cores, 8 threads), 16 GB RAM, Windows 11; 3.2 to 3.8 GB of RAM free during the runs, IDE and browser open |
| Software | Python 3.13.2, FastAPI 0.143.0, Starlette 1.7.0, uvicorn 0.54.0 (one process), sse-starlette 3.5.0, SQLite (WAL), Locust 2.46.7, httpx 0.28.1, Qiskit 2.5.2, Qiskit Aer 0.17.2 |
| Server under test | A private instance started by the harness on `127.0.0.1` with its own port and data folder under `.tmp\`; queue capacity 8, one compute worker |
| Load generator | Locust, on the same laptop (it competes with the server for CPU) |
| Third parties | None contacted. IBM is mocked in-process; no market data is downloaded |

Commands (PowerShell, from the project root):

```powershell
scripts\bench.ps1 -Smoke     # about 1 minute; writes to evidence\load\smoke, never over a full run
scripts\bench.ps1            # everything below, about 10 minutes
# or one part at a time, from backend\ with .venv\Scripts\python.exe:
#   loadtest\run_load.py       load stages and failure scenarios   (fake pipeline, port 8130)
#   loadtest\sse_latency.py    event delivery lag and cancellation (fake pipeline, port 8131)
#   loadtest\real_quick.py     genuine Quick jobs on the simulator (real pipeline, port 8121)
#   loadtest\real_quick.py --gap 15 --out real_quick_idle15.json     the same with 15 s idle before each job
```

Raw output: `evidence/load/` (`summary.json`, `summary.md`, `<stage>_stats.csv`, `<stage>_stats_history.csv`,
`<stage>_jobflows.jsonl`, `resources.csv`, `run_load.log`, `sse_latency.json`, `real_quick.json`, `real_quick_idle15.json`).

### Workload

The load stages use the **fake pipeline** (`QF_PIPELINE_IMPL=fake`): a deterministic stand-in that emits the same stages and
events as a real run and takes about 0.02 s per optimiser evaluation (roughly 1.5 to 2 s per job). They therefore measure the
platform (API, queue, SQLite, worker supervision, event streaming), not the quantum simulation. Simulation time is measured
separately with real jobs in the section "Real Quick jobs".

Two kinds of simulated users, mixed 4 to 1:

- **Browser** (think time 0.2 to 1.0 s): health, assets, capabilities, run list, run status, result, event snapshot, HTML report, preflight.
- **Submitter** (think time 0.5 to 2.0 s): posts a new run with a unique seed, follows its event stream to the end, then reads the status.
  A 429 answer is counted as correct behaviour, not as a failure, and the user backs off.

Stages: 1, 10, 25 and 50 users for 40 s each, then 10 users for 180 s (soak). The 1-user stage contains only a Browser user, so it submits no jobs.

## Load stages (fake pipeline)

| Stage | Users | Requests | Failed | p50 ms | p95 ms | p99 ms | Requests/s | 429 answers | Jobs finished | Submit-to-finish p50 / p95 s (n) | Server CPU avg / max % of one core | Server RSS max MB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ramp-01 | 1 | 44 | 0 | 5 | 10 | 31 | 1.1 | 0 | 0 | none | 1.3 / 27.5 | 121.4 |
| ramp-10 | 10 | 559 | 0 | 5 | 15 | 46 | 14.7 | 0 | 20 | 2.8 / 3.6 (20) | 25.4 / 100.9 | 123.7 |
| ramp-25 | 25 | 1231 | 0 | 6 | 26 | 110 | 24.3 | 0 | 22 | 9.1 / 14.8 (22) | 27.4 / 95.2 | 127.6 |
| ramp-50 | 50 | 2250 | 0 | 8 | 100 | 700 | 44.9 | 16 | 17 | 25.0 / 30.2 (14) | 31.4 / 87.3 | 133.8 |
| soak-10 | 10 | 2527 | 0 | 6 | 14 | 59 | 14.2 | 0 | 85 | 2.8 / 5.8 (85) | 25.8 / 99.7 | 133.7 |

Latency columns are over all request types in the stage. CPU and RSS are for the server's whole process tree (API, supervisor,
job children), sampled once per second; the tree never exceeded 5 processes. In ramp-50, three jobs finished after their
simulated user had already been stopped, so 14 of the 17 have a submit-to-finish time.

Per request type (p50 / p95 ms, sample count in brackets):

| Request | 10 users, soak | 25 users | 50 users |
| --- | --- | --- | --- |
| `GET /api/health` | 5 / 11 (339) | 5 / 21 (186) | 6 / 24 (358) |
| `GET /api/runs` | 7 / 14 (382) | 8 / 22 (197) | 9 / 23 (327) |
| `GET /api/runs/{id}` | 5 / 10 (536) | 5 / 12 (228) | 7 / 18 (451) |
| `GET /api/runs/{id}/result` | 6 / 23 (224) | 7 / 14 (115) | 9 / 22 (209) |
| `GET /api/runs/{id}/report.html` | 10 / 48 (144) | 12 / 33 (62) | 12 / 46 (119) |
| `GET /api/runs/{id}/events/snapshot` | 6 / 14 (139) | 7 / 16 (50) | 7 / 27 (89) |
| `POST /api/preflight` | 5 / 13 (115) | 6 / 15 (70) | 7 / 45 (131) |
| `POST /api/runs` (accepted) | 7 / 14 (85) | 8 / 33 (22) | 19 / 200 (17) |
| `POST /api/runs` (429, queue full) | none | none | 12 / 93 (16) |
| SSE connect (time to response headers) | 4 / 6 (84) | 5 / 7 (21) | 20 / 100 (14) |
| `GET /api/presets` (once per user at start) | 18 / 33 (10) | 42 / 74 (25) | 580 / 800 (50) |

Soak (180 s, 10 users): 2527 requests, 0 failures, 85 jobs, server memory 130.7 MB at the start and 122.4 MB at the end
(maximum 133.7 MB), so no growth was observed over three minutes. The SQLite file was 2.4 MB after the whole run
(158 succeeded, 8 cancelled and 1 deliberately interrupted job).

## Failure scenarios (fake pipeline)

All seven passed in the full run (`evidence/load/summary.md`).

| Scenario | What was done | Result |
| --- | --- | --- |
| Queue saturation | 30 submissions in 0.16 s | 8 accepted, 22 answered 429 with `Retry-After: 10`, no other status; all 8 accepted jobs succeeded |
| SSE fan-out | 40 clients on one running job | Every client received the same 41 events, in strictly increasing order, identical to the stored snapshot |
| Cancel storm | 8 jobs, 3 cancel calls each | All 24 calls returned 200; all 8 jobs `cancelled` within 0.38 s; no job ended in two states |
| Dropped SSE connections | 120 connections closed mid-stream | Job still succeeded; server threads went from 32 back to 4; health p95 2.9 ms afterwards; a later client could replay all 39 events |
| Missing cached result | Result file deleted after a cached replay | Result and report answer 410; an identical submission runs fresh (202) and succeeds |
| Worker killed | The harness killed the supervisor it had started, mid-job | Worker ready again after 0.43 s; the running job became `failed` / `interrupted_by_restart`; the queued job ran and succeeded; no orphan process |
| Provider outage (mocked IBM) | Provider raises, then hangs | `POST /api/ibm/check` answered 502, then 504; token not present in any response; health stayed available (p95 102 ms and 58 ms against 5.7 ms normally) |

Shutdown at the end: graceful, 1.05 s, exit code 0, no orphan processes. The harness only stops process ids it started.

## Event delivery and cancellation (fake pipeline)

`loadtest\sse_latency.py`, output in `evidence/load/sse_latency.json`. Lag is the time the client received an event minus the
timestamp the server put in that event (same clock).

| SSE clients on one job | Events received | Lag p50 ms | Lag p95 ms | Lag max ms | Request to first event p50 / p95 ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 34 | 180 | 303 | 313 | 7 / 7 |
| 10 | 340 | 129 | 312 | 325 | 50 / 58 |
| 40 | 1400 | 130 | 320 | 348 | 142 / 235 |

The stream is read from SQLite every 0.2 s, which sets the lag. (The fan-out scenario in `summary.json` shows 6.2 s to the
first event for 40 clients. That figure included creating 40 HTTP clients inside the test process; the harness has since been
corrected to start the clock after the client exists. The table above is the server-side figure.)

Cancelling a running job (10 repetitions): the cancel call returned in 4.3 ms (median; maximum 6.1 ms) and the job was
`cancelled` 0.93 s later (median; maximum 0.94 s, polled every 0.15 s).

## Real Quick jobs (local Aer simulator)

`loadtest\real_quick.py`. Real pipeline, default example and three variants with other seeds: 8 stocks, pick 4, 10 qubits,
depth 1, COBYLA, 25 evaluations, 256 shots per evaluation, 1024 final shots, plus the classical baselines. Jobs ran one at a
time; times are from the HTTP submission to the terminal event. Two passes, each on a freshly started server:

- **Back to back** (`--jobs 3`, `evidence/load/real_quick.json`): each job is submitted as soon as the previous one ends.
- **After idle** (`--jobs 3 --gap 15 --out real_quick_idle15.json`): the three variants are each submitted after 15 s of idle time.

| Measurement | Back to back | After 15 s idle |
| --- | --- | --- |
| Server start until `/api/ready` reports the worker | 7.5 s | 6.2 s |
| First job, submitted straight after start (default example) | 10.2 s (first evaluation after 7.3 s) | 15.8 s (first evaluation after 12.0 s) |
| Three further fresh jobs (other seeds) | 13.6, 11.3, 12.1 s (first evaluation after 9.7, 8.2, 9.2 s) | 6.2, 4.8, 6.8 s (first evaluation after 3.0, 2.9, 3.7 s) |
| All four fresh jobs, median / maximum | 11.7 / 13.6 s | 6.5 / 15.8 s |
| Same configuration again (replayed from cache, labelled `cached_from`) | 0.34 s | 0.35 s |
| Peak CPU of the server's process tree, % of one core | 308 | 338 |
| Peak memory of the server's process tree | 497 MB | 498 MB |
| Shutdown | graceful, 3.4 s, no orphan process | graceful, 3.2 s, no orphan process |

All 8 fresh jobs succeeded, all reports rendered, and in every one QAOA, exact enumeration, relaxation with rounding and
simulated annealing reported the same objective value. An earlier back-to-back pass of the same script (its file has since
been overwritten) gave 16.5, 13.3, 10.6 and 13.4 s, so the slowest fresh Quick job seen in 12 was 16.5 s.

Why idle time matters: every job runs in its own child process, and the supervisor starts the next child ahead of time so
that it can load the simulator libraries while nothing is running. A job that arrives before that has finished (straight after
start-up, or straight after another job) waits for it, which is the 7 to 12 s before the first evaluation in the slower rows.

## Against the targets in the build brief

| Target | Measured | Met on this machine |
| --- | --- | --- |
| Light API calls p95 under 200 ms at modest load | 10 users: 15 ms; 25 users: 26 ms; 50 users: 100 ms (all request types together) | Yes, except `GET /api/presets` at 50 users (p95 800 ms, see below) |
| Job accepted in under 500 ms | p95 14 ms (10 users), 33 ms (25), 200 ms (50); maximum 204 ms | Yes |
| Events delivered within about 1 s | Lag p95 0.30 to 0.32 s, maximum 0.35 s, with 1 to 40 clients | Yes |
| Cancellation acknowledged promptly | Call returns in about 4 ms; job terminal in about 0.9 s | Yes |
| Fresh Quick run within 30 s | 8 fresh runs: 4.8 to 15.8 s (median 6.5 s after idle, 11.7 s back to back); slowest of 12 seen: 16.5 s | Yes |

## Bottlenecks observed

1. **One compute worker.** Jobs run strictly one after another, each in a fresh child process. With the fake pipeline the
   server finished about 0.45 to 0.55 jobs per second whatever the number of users, so the submit-to-finish time grew from
   2.8 s (2 submitters) to 9 s (5 submitters) and 25 s (10 submitters), and at 50 users the queue filled and 16 submissions
   were refused with 429. Real jobs run back to back took about 12 s each, so a full queue of 8 would keep the last
   submitter waiting about a minute and a half (8 times 12 s; calculated, not measured).
   This is a deliberate limit for a laptop with a few GB free, not something the measurements show can be raised safely.
2. **`GET /api/presets` under a start-up burst.** It took 18 ms at 10 users but 580 ms (median) when 50 users requested it
   within two seconds. It is requested once per page load, so it did not affect the other figures, but it is the one light
   call that missed the 200 ms target.
3. **Tail latency at 50 users.** p99 reached 700 ms overall while p50 stayed at 8 ms: the API, the supervisor, the job child
   and the load generator share four cores.
4. **Process start-up for real jobs.** A fresh server needed 6.2 to 7.5 s to become ready. A real job submitted straight
   after start-up or straight after another job took 10 to 16 s, against 5 to 7 s after 15 s of idle time: most of the
   difference is the new child process loading the simulator libraries.

## Limits of these measurements

- One machine, one run of each stage; no confidence intervals. The load generator ran on the same CPU as the server, and
  the laptop was not idle: an IDE, a browser and, during part of the run, an unrelated browser test suite were also running.
- Stage lengths are short (40 s ramps, a 3-minute soak), so slow leaks would not be visible.
- The load stages use the fake pipeline. Only the section "Real Quick jobs" exercises the simulator, with small samples (8 jobs).
- No network, no authentication, no concurrent real jobs (the worker runs one job at a time by design).
