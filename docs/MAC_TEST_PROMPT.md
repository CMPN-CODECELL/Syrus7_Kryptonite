# QuantFolio: MacBook validation prompt

Paste everything below the line into Claude Code on the MacBook, started in the root of the QuantFolio repository (or just say: "Read docs/MAC_TEST_PROMPT.md and do what it says").

---

## What this is

QuantFolio is a hackathon project (SYRUS 7.0, problem statement PS-03: portfolio optimisation using quantum computing, Team Kryptonite). It selects a constrained portfolio from the NIFTY 50 with QAOA on the Qiskit Aer simulator and compares it against classical baselines (exact enumeration, convex relaxation with rounding, simulated annealing). It has a FastAPI backend with a SQLite job queue, a separate worker process and live progress over SSE, and a React + Three.js frontend.

It was built and tested on one Windows laptop: Intel i5-1135G7 (4 cores / 8 threads), 16 GB RAM with only about 3 to 5 GB free, Windows 11, Python 3.13.2, Node 24. That laptop was the limiting factor for several measurements, and everything was measured while three build workstreams were running on it at the same time.

You are running on a second machine: a friend's MacBook Air (Apple Silicon, 16 GB RAM, 512 GB SSD, reportedly 10-core CPU / 10-core GPU; confirm the real spec and record it). Your job is to do the validation that the Windows laptop could not do, and to write down what you measured so the team can cite it to the judges. You are not here to add features or redesign anything.

Start by reading `README.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACT.md`, `docs/STATUS.md`, `docs/MATH.md`, `docs/DATA.md`, `docs/PERFORMANCE.md`, `docs/UI.md` and `evidence/experiments/RESULTS.md`. They describe how the system works, how to launch it and what was measured on Windows. If one of them is missing, say so in your report and carry on.

## Ground rules

- **This is someone else's computer.** Keep everything inside the repository folder: the virtual environment in `backend/.venv`, and pip, npm and Playwright caches under `.cache/` (`PIP_CACHE_DIR`, `npm_config_cache`, `PLAYWRIGHT_BROWSERS_PATH`), temp files under `.tmp/` (`TMPDIR`). Check what is already installed (`python3.13`, `node`, `git`). If something system-wide is missing, stop and show the exact install command (for example a Homebrew command) and let the person at the keyboard decide; do not install system-wide software on your own. Never stop or kill a process you did not start.
- **Do not overwrite the Windows evidence.** Everything you produce goes under `evidence/mac/` and into one new document, `docs/MAC_VALIDATION.md`. Leave `evidence/experiments`, `evidence/load`, `evidence/core-tests`, `evidence/api-tests`, `evidence/e2e`, `evidence/screenshots` and the repo's `data/` folder as they are. For any server you start, use a private data directory such as `.tmp/mac-data` seeded with copies of `data/cache/datasets`, `data/universe` and `data/examples`, and point `QF_DATA_DIR` at it.
- **Use the same dataset as Windows.** Datasets are content-hashed; the default one is `nifty50-yahoo-aaa53c0c94ef` (50 symbols, 2019-01-01 to 2026-10-08) and is committed in `data/cache/datasets/` (`.gitattributes` keeps its bytes unchanged on checkout). Check that `nifty50-yahoo-aaa53c0c94ef.parquet`, `.close.parquet` and `.json` are present and that `default.txt` names it. Do not re-download from Yahoo: a fresh download gets a different hash and the numbers stop being comparable. Confirm the dataset id before measuring anything.
- **Keep Windows behaviour unchanged.** The repository ships PowerShell scripts and was only ever run on Windows, so expect some macOS breakage (process groups, signals, file locking, path handling, anything that imports a Windows-only module). Fix what is needed with a platform branch that leaves the Windows path exactly as it was, keep each fix small, and list every one in your report with the file and the reason. If a change would alter default behaviour, make it opt-in through an environment variable whose default is the current value.
- **Git.** Work on a new local branch named `mac-validation` and commit there. `origin` is the team's hackathon repository; do not push anything unless the person at the keyboard tells you to.
- **Networks and credentials.** Load and stress tests go only at `127.0.0.1`. Never load-test Yahoo, IBM or any third party. Do not use real IBM hardware in any automated test. Never print, log or commit a token or key.
- **Honest reporting.** Report what you measured, with sample counts. If a test fails, give the failure output. If you skipped a stage or cut it short, say so and why. Do not describe any result as quantum advantage or as evidence that returns are achievable; this is a research demonstration on a simulator.
- **Fanless machine.** A MacBook Air throttles under sustained load. Keep it on mains power, close other apps if the person agrees, record `pmset -g therm` before and after each long stage, and leave a short pause between heavy stages. If throttling shows up, note which numbers it affected.

## Windows reference numbers

These are what the Windows build reported. Put your number next to each one in `docs/MAC_VALIDATION.md`.

| Measurement | Windows (i5-1135G7, contended) |
|---|---|
| Backend core tests | 154 passed, 0 failed, 0 skipped, 66 to 74 s |
| Backend API tests | 146 passed, 0 failed, 0 skipped, 421 s |
| Quick preset, default example (8 assets, 10 qubits), warm worker | median 2.49 s, p95 2.86 s (5 runs) |
| Balanced preset, same example | median 9.07 s, p95 9.7 s (3 runs) |
| Research preset, same example | median 26.75 s, p95 28.28 s (3 runs) |
| Quick preset in a fresh process (includes Qiskit import) | 8.3 to 9.8 s |
| Server start to ready | 6.2 to 7.5 s |
| Quick run through HTTP + worker + SSE (8 fresh jobs, `backend/loadtest/real_quick.py`) | 4.8 to 15.8 s wall (16.5 s in an earlier pass); first job after start 10.2 to 15.8 s; cache replay 0.34 s; peak memory of the process tree 498 MB |
| Read-API load, test pipeline (`run_load.py`) | 6,611 requests at 1/10/25/50 users (40 s per stage) plus a 180 s soak at 10 users, 0 failures; p95 10/15/26/100 ms; 16 expected 429s at 50 users; server memory at most 134 MB |
| SSE event lag | p95 0.30 to 0.32 s with 1, 10 and 40 clients |
| Largest ideal simulation allowed | 24 qubits (7.05 s per evaluation, 521 MB peak); 25 refused because the estimate of 1886 MB exceeds the 1500 MB limit |
| Largest noisy simulation allowed | 14 qubits (30.2 s per evaluation; 6.09 s at 12 qubits) |
| Exact enumeration | up to 20 assets |
| Experiment matrix | 60 cells (depth 1 to 5 x COBYLA/SPSA x random/warm_interp x 3 seeds), 30 evaluations x 256 shots, 189 s; approximation ratio 1.000 in every cell |
| Universe size study at depth 1 | ratio 1.000 at 8 and 10 qubits, 0.963 at 17, 0.905 at 20 (optimum found in 1 of 3 seeds at the two larger sizes) |
| Noise (FakeTorino, 5 paired trials) | feasible probability 0.392 to 0.183, best feasible objective unchanged |
| Frontend unit tests (Vitest) | 45 passed; ESLint and `tsc` clean |
| Playwright, Chromium, real backend | 14 passed; 0 console errors, 0 warnings, 0 failed requests |
| Playwright, Chromium, scripted UI states | 16 passed (the 32 console errors in that run are the intended offline and error states) |
| Playwright, Firefox and WebKit | primary journey only: 1 passed on each (Firefox logs 4 allow-listed WebGL warnings) |
| Accessibility (axe-core) | 16 scans, 0 violations |
| Page performance, mobile profile (slow 4G, 4x CPU slowdown) | LCP 2.36 s on `/` and 2.31 s on `/lab` (budget 2.5 s, so little margin); CLS 0.002; INP 19 ms, 35 ms with the slowed CPU |
| Manual check in Chrome against the real server | every view, a fresh Quick run (7.2 s), downloads: no console messages, no failed requests, exported numbers matched the screen |

The caps are constants in `backend/quantfolio/presets.py` (`MAX_QUBITS`, `MAX_QUBITS_NOISE`, `MEMORY_LIMIT_MB`, `MAX_EXACT_ASSETS`). The experiment and benchmark runner is `backend/scripts/experiments.py` (`--dry-run`, `--matrix`, `--benchmark-qubits`, `--calibrate-presets`, `--all`); read it before changing anything, and run `--dry-run` first. The load tests are in `backend/loadtest/` (`run_load.py`, `locustfile.py`, `real_quick.py`, `sse_latency.py`, `servectl.py`, `outage_mock.py`).

## What to do, in priority order

Time is short. Work through these in order and stop a stage when it reaches its bound rather than letting it run on. Stages 1 to 3 matter most.

### 1. Clean-machine setup (about 20 minutes)

This is the one thing the development laptop cannot test: whether someone can go from a fresh checkout to a running app using only the documentation.

- Follow `README.md` as written and note every step that fails, is missing, or assumes Windows.
- Python dependencies: `backend/requirements.lock.txt` was resolved on Windows and contains Windows-only pins (`pywin32`, `colorama`). Install from `backend/requirements.in` using the lock as constraints with the Windows-only packages removed, then write the resolved set to `backend/requirements.lock.macos.txt`. If a pinned version has no macOS arm64 wheel, do not quietly substitute: record the pinned and the installed version, because a version change can change the numbers.
- Frontend: `npm ci` in `frontend/`, then `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`.
- Write `scripts/setup.sh`, `scripts/start.sh`, `scripts/stop.sh` and `scripts/test.sh` that do what the corresponding `.ps1` files do (read those first), including stopping only the processes they started. Paths with spaces must work, because the repository folder may contain one.
- Launch the app with your `start.sh`, open it, and run one Quick optimisation by hand through the API to confirm the real pipeline works end to end.
- Check two server behaviours added late on Windows: JSON responses of 1 KB or more come back with `content-encoding: gzip` (try `curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' http://127.0.0.1:PORT/api/presets`), and the live event stream (`/api/runs/ID/events`) is **not** compressed and still delivers events one by one.

### 2. Full test suites on macOS (about 25 minutes)

- Backend: `pytest backend/tests/core` and `pytest backend/tests/api`, single process. Save junit XML and text output under `evidence/mac/tests/`. Give pass, fail and skip counts for each. For each failure, decide whether it is a real macOS defect, a Windows assumption in the test, or flakiness, and fix the first two kinds.
- Browser: install Chromium, Firefox and WebKit for Playwright into `.cache/` and run the **whole** end-to-end suite on all three against the real backend. On Windows the full suite (14 real-backend tests plus 16 scripted-state tests) ran on Chromium; Firefox and WebKit ran only the primary journey, and the Firefox runner hung after passing, so it left a log (`evidence/e2e/playwright-firefox.log`) but no JSON report. The full WebKit and Firefox results here are therefore new information. `three` is pinned to 0.182.0 in `frontend/package.json`; keep the pin so the numbers are comparable. Include the accessibility scans. Save reports under `evidence/mac/e2e/`.
- Take screenshots of each of the five views at 360, 390, 768, 1280 and 1440 px in WebKit into `evidence/mac/screenshots/`, look at them, and list any visual defect that differs from the Chromium screenshots in `evidence/screenshots/`.
- Measure the 3D hero on this display: frame time over about 10 seconds with the hero visible (sample `requestAnimationFrame` deltas in headed WebKit and Chromium), whether it pauses when scrolled offscreen, and LCP and CLS on the mobile route under the same throttling profile `docs/UI.md` documents.

### 3. Simulations the Windows laptop had no memory or time for (about 40 minutes)

- First re-measure the preset timings (Quick, Balanced, Research; warm and fresh-process) with nothing else running. Use at least 10 runs for Quick and 5 for the others, and report median and p95. The Windows numbers were taken under contention, so these become the clean reference.
- **Ideal simulation above 24 qubits.** A double-precision statevector needs 16 bytes x 2^n, and the measured peak on Windows was about twice that (521 MB at 24 qubits). So expect roughly 1 GB at 25 qubits, 2 GB at 26, 4 GB at 27 and 8 GB at 28. Measure free memory first. Go up one qubit at a time from 24, and stop when the predicted peak passes 60% of free memory, when swap starts to grow, or when one evaluation takes longer than 120 seconds. Record seconds per evaluation and peak memory at each size. Then do one complete Quick-budget QAOA run at the largest size that finishes in under about 10 minutes. Above 20 assets there is no exact optimum to compare against, so label the quality figure as a gap to the best classical result found, not as an approximation ratio.
- **Noisy simulation above 14 qubits.** Measure seconds per evaluation at 12, 14 and 16 qubits (18 only if 16 takes under 60 seconds per evaluation), with the same stop rule.
- **A stronger experiment matrix.** Three seeds were all the Windows time budget allowed, and at 8 qubits every cell hit the optimum, so the matrix separated nothing. Re-run it with 10 seeds, and run the universe-size study at depths 1, 2 and 3 with 10 seeds, since that is where QAOA quality starts to fall and the comparison becomes informative. Re-run the noise comparison with 20 paired trials. If the runner has no options for seeds, budget or output directory, add them as optional arguments whose defaults reproduce the current behaviour, and write results to `evidence/mac/experiments/`. Report means with standard deviations and say plainly where differences are within seed spread. Bound the whole matrix at about 25 minutes; if it will not fit, cut seeds before cutting factor levels and say what you cut.

### 4. Load and stress at full length (about 25 minutes)

Run the existing load suite against a server you start on a spare port with the private data directory, with nothing else running, and write raw output to `evidence/mac/load/`.

- Read-API ramp at 1, 10, 25 and 50 users, two minutes per stage.
- Concurrent SSE streams, then job submission beyond queue capacity (expect controlled 429 responses with `Retry-After`, and no unbounded process spawning).
- 20 real Quick jobs submitted in a row.
- A 15-minute soak at 10 users.
- Cancellation storm, dropped connections, and killing the worker mid-run followed by restart (the interrupted run must end as `failed` with code `interrupted_by_restart`, exactly once, and nothing may run twice).
- Report p50, p95 and p99 latency, throughput, expected rejections separately from real failures, queue wait, event latency, peak CPU and memory, and whether any orphan process was left behind.

### 5. Real Safari check (5 minutes, done by a person)

Playwright's WebKit is close to Safari but is not Safari. At the end, give the person at the keyboard this checklist and record their answers: the landing page renders the 3D hero; selecting assets visibly updates it; a Quick run shows live progress and then results; a report downloads; the page has no horizontal scroll at a narrow window width.

### Optional: IBM hardware

Only if the person explicitly asks for it and supplies their own IBM Quantum token through the `.env` file. `python -m quantfolio.ibm status` (no network) and `python -m quantfolio.ibm check` (lists backends, submits nothing) work. `python -m quantfolio.ibm submit` is **not wired up** on Windows (the helper that rebuilds a stored run's bound circuit is missing), so report that rather than building it, unless the person asks. If it is built and used: smallest instance, submit once, record the job id, never in an automated test.

## What to hand back

1. `docs/MAC_VALIDATION.md`: the machine's real spec and software versions; one table with each Windows reference number beside the Mac number; the qubit scaling table; the experiment results; the load results; per-browser test counts; and a list of defects found, each marked fixed or open.
2. `evidence/mac/`: the raw outputs behind every number in that document.
3. A final message of about a page: what passed, what failed, the largest ideal and noisy simulations this machine handled, the three results most worth showing the judges, every code change you made (file and reason), anything you could not finish, and the exact commands to remove what you installed (deleting the repository folder should be enough if you kept to the rules above).

Mark anything in the final message that you inferred rather than measured.
