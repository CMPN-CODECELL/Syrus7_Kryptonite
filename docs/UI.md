# QuantFolio frontend (workstream C)

React 19 + TypeScript single-page app in `frontend/`, built with Vite and Tailwind CSS v4. It talks only to the
QuantFolio API (`/api/*`, contract in `docs/CONTRACT.md`, generated types in `frontend/src/api/contract.gen.ts`).
Everything the UI shows comes from the API. The app has no pricing, testimonials, invented tickers, prices or
performance, and the local app has no sign-in (the cloud deployment adds one, see `docs/GCP_DEPLOYMENT.md`). Results
produced by the fake pipeline or mocked in the browser exist only in the e2e evidence and are labelled as such in file
names and test titles.

## Stack

| Concern | Choice |
| --- | --- |
| UI | React 19.3, react-router 8 (data router, one lazy chunk per workspace view) |
| Server state | TanStack Query 5; live runs use server-sent events with automatic fallback to polling (`src/api/events.ts`) |
| Charts | Recharts 3. Every chart has a one-sentence text summary and a data table (`src/components/charts`) |
| Hero | `components/hero/QubitCoins.tsx`: plain CSS 3D coins that replay a stored run; no WebGL and no extra chunk. Reduced motion shows the landed coins |
| Live prices | `lib/live.ts`: one shared `/api/market/stream` connection for the whole app, polling `/api/market/live` every second after three failed connections. `components/market/` holds the ticker and the candlestick chart (plain SVG) |
| Styling | Tailwind CSS v4 with the design tokens below (`src/styles.css`) |
| Tests | Vitest + Testing Library (unit), Playwright + axe-core (e2e and accessibility) |

## Fonts and licences

One family is used for everything (headings, text, numbers, ids and code), self-hosted through Fontsource and
imported in `src/main.tsx`; no request leaves the origin.

| Role | Family | Package | Licence |
| --- | --- | --- | --- |
| All text | Plus Jakarta Sans Variable | `@fontsource-variable/plus-jakarta-sans` ^5.3.0 | SIL OFL 1.1 |

Numbers line up with the `tabular` class (tabular figures). Fallback stack: Segoe UI, system-ui. The font uses
`font-display: swap`.

## Design tokens (`src/styles.css`, Tailwind `@theme`)

| Token | Value | Use |
| --- | --- | --- |
| `ink` / `ink-2` | `#0b1220` / `#1e293b` | Headings, body text |
| `muted` / `faint` | `#525d6e` / `#6b7586` | Secondary text (both pass WCAG AA on white) |
| `line` / `line-strong` | `#e4e8ef` / `#cbd3df` | Borders, dividers |
| `canvas` / `mist` / `mist-2` | `#ffffff` / `#f6f8fb` / `#eef2f7` | Page, panels, pills |
| `cobalt` (700, 800, 50, 100, 200) | `#1f48d6` (`#1a3cb3`, `#162f8c`, `#eff3fe`, `#dde5fd`, `#bccbfa`) | Primary actions, QAOA series, focus |
| `teal` (600, 50, 100) | `#0b7471` (`#0e8f8b`, `#e8f6f5`, `#c9ebe8`) | Success, live state, selection |
| `amber` (50, 200) | `#9a5b00` (`#fff7e8`, `#f6d79b`) | Warnings, synthetic or stale data, timeouts |
| `danger` (50, 200) | `#b42318` (`#fef1ef`, `#f7c3bc`) | Errors, failed runs, blocking preflight issues |
| Radii | 6 / 10 / 14 / 20 px (`sm`/`md`/`lg`/`xl`) | Inputs, cards, panels |
| Shadows | `card`, `float` | Cards; floating panels and dialogs |
| Easing | `ease-out-soft` = `cubic-bezier(0.22, 1, 0.36, 1)` | Entrances. `prefers-reduced-motion` turns all motion off |

## Views

| Route | View | What it shows |
| --- | --- | --- |
| `/` | Landing | Headline, the steps with an "Explained simply" card (picking a team with pocket money; a qubit as a buy/skip coin), and the recorded example run (`/api/example`). The hero card shows live NIFTY 50 candles, then replays that run: eight coins spin and land on 1 (buy) or 0 (skip), with the gap to the best answer. Its caption names the stored run it replays |
| `/dashboard` | Dashboard | Five key numbers (live NIFTY 50 among them), the latest result in plain words, recent runs and shortcuts into the Lab |
| `/lab` | Portfolio Lab | Presets, asset picker (search, sector filter, selected-only), risk and budget, holdings K, sector caps, target return, transaction costs, date split, advanced settings; live preflight (qubits, depth, shots, memory and runtime estimates, blocking issues first) with a sticky submit |
| `/runs/:id` (run) | Run | Live progress (stages with durations, evaluation counter, convergence chart, worker log, cancel), then the result tabs: **Portfolio** (holdings in rupees, sector chart, costs, constraint residuals), **Comparison** (QAOA vs exact, relaxation, annealing, greedy; frontier; held-out test), **QAOA details** (circuit diagram with a depth p 1/2/3 toggle, convergence, sample distribution, noise), **Data & provenance** (dataset, software, seeds, config; HTML/JSON/CSV downloads). Failed, cancelled, timed-out and infeasible runs get their own panel with "Run the same settings again" |
| `/runs/:id` (experiment) | Experiment result | Factor sweep summary, one chart per metric with its table, per-cell table |
| `/experiments` | Experiment Lab | Sweep factor, levels, seeds and budget with a visible total cost |
| `/history` | History | Every stored run and experiment with status and kind filters |
| `/guardrails` | Guardrails | The limits the project keeps to, grouped by family, each with where it is enforced |
| `*` | Not found | Link back to the dashboard |

Every view has its own error boundary (`ViewError`), so a crash keeps the header, navigation and footer. A failed
chunk download offers a reload.

Shared chrome: a dark market ticker on top of every page (status pill: Live, Delayed, Market closed, Connecting,
Offline, or "Last close" with the date when live prices are off or Yahoo is unreachable; prices scroll and pause on
hover or focus), then the header, and in the workspace a rail that opens on hover or with its arrow. Charts and page backgrounds have no grid lines. Cards are compact: key/value lists use two columns,
long tables show the first rows with a "Show all" control, and downloads sit in the run toolbar.

## States and how they are tested

| State | Where | Evidence (`evidence/screenshots/states/`) | Test |
| --- | --- | --- | --- |
| Loading | Skeletons in every view; `HydrateFallback` while a route chunk loads | `lab-loading.png` (responses delayed in the browser) | states: loading, empty and stale |
| Empty | History with no runs; Overview without a recorded example | `history-empty-mocked.png` | states: loading, empty and stale |
| Error | `ErrorState` with message, code, request id and "Try again" | `lab-api-unreachable.png`, `run-failed.png` | states: API unreachable; failed run (fake seed 777) |
| Infeasible | Preflight blocks submission; server rejects the config | `lab-blocked-by-preflight.png`, `run-infeasible-config.png` | states: infeasible ×2 |
| No feasible sample | QAOA found no feasible bitstring; baselines still shown | `run-no-feasible-sample-mocked.png` (result mocked in the browser) | states: no feasible QAOA sample |
| Stale data | Amber notice and "(stale)" data badge | `lab-stale-mocked.png` (data status mocked in the browser) | states: loading, empty and stale |
| Offline | Notice, submit blocked, recovers on reconnect | `lab-offline.png` | states: offline |
| Live, cancel, timeout | Progress view, cancel dialog, time limit | `run-in-progress.png`, `run-cancelled.png`, `run-timed-out.png` | states: cancel, refresh mid-run, lost stream, timeout (fake seed 888) |
| Reduced motion | The hero coins show where they landed, without spinning | `landing-reduced-motion.png` | states: reduced motion |

## Running it

Production build and the two API instances used by the e2e suites (paths from the repo root; Windows Git Bash):

```sh
cd frontend && npm ci && npm run build

# API on the real pipeline (8101) and on the fake pipeline (8100), from backend/
QF_DATA_DIR=".tmp/e2e-real" QF_CORS_ORIGINS="http://127.0.0.1:5201" .venv/Scripts/python.exe -m quantfolio.serve --port 8101
QF_PIPELINE_IMPL=fake QF_DATA_DIR=".tmp/e2e-data" QF_CORS_ORIGINS="http://127.0.0.1:5200" .venv/Scripts/python.exe -m quantfolio.serve --port 8100

# Previews of frontend/dist that proxy /api, from frontend/
QF_API_TARGET=http://127.0.0.1:8101 npx vite preview --host 127.0.0.1 --port 5201 --strictPort                    # real pipeline
QF_API_TARGET=http://127.0.0.1:8100 npx vite preview --host 127.0.0.1 --port 5200 --strictPort                    # fake pipeline
QF_API_TARGET=http://127.0.0.1:8101 QF_PREVIEW_GZIP=1 npx vite preview --host 127.0.0.1 --port 5202 --strictPort  # gzip, perf budget
```

The API also serves `frontend/dist` itself at `http://127.0.0.1:8101/` (gzip-compressed).

Checks, from `frontend/`:

```sh
npm run lint && npm run typecheck && npm run test && npm run build
QF_E2E_TAG=chromium-states npx playwright test --project=chromium-states   # failure states, fake pipeline (5200)
QF_E2E_TAG=chromium        npx playwright test --project=chromium          # journey + views/axe + perf, real pipeline (5201/5202/8101)
QF_E2E_TAG=firefox         npx playwright test --project=firefox           # journey only
QF_E2E_TAG=webkit          npx playwright test --project=webkit            # journey only
```

Run one suite at a time with one worker (the config sets `workers: 1`). Stop the previews before `npm run build`:
on Windows, `vite preview` can keep a handle on a file in `dist/assets/` after an aborted request, and the build
then fails with `EPERM`.

## Console and network guard

`e2e/helpers.ts` exports `test`/`expect` with an automatic `guard` fixture that every spec uses. It records, per test:

- console errors and warnings;
- uncaught page errors and unhandled rejections;
- failed requests (`requestfailed`), counting cancellations (`net::ERR_ABORTED`, Firefox `NS_BINDING_ABORTED` /
  `NS_ERROR_ABORT`, WebKit "Load request cancelled") separately, for example a navigation that cancels a pending fetch
  or the app closing a finished run's event stream;
- HTTP responses ≥ 400.

Any finding fails the test, unless the test itself declares it with `guard.allow(...)`. Only two tests do, and they
cause the failure on purpose:

- the lost-stream test aborts `/api/runs/<id>/events?after=N` in the browser;
- the API-unreachable test aborts every `/api/` request.

No browser needs an allow-list: the hero is plain CSS and SVG, so `BROWSER_NOISE` in `e2e/helpers.ts` is empty.
Allowed findings are still counted in the log. Each test appends one JSON
line to `evidence/e2e/console-guard-<tag>.jsonl`; `e2e/global-setup.ts` clears that file when a run starts.

## Test results (final runs, 2026-10-09)

All runs used the production build, one suite at a time, with one worker.

| Suite | Tests | Console and network guard |
| --- | --- | --- |
| Vitest (`npm run test`) | 45 passed, 0 failed (7 files) | n/a |
| `chromium-states` (fake pipeline, 5200 → 8100) | 16 passed, 0 failed (3.3 min) | 16 console errors + 16 failed requests, all 32 provoked and allowed (lost stream 6, API unreachable 26); 0 unexpected; 5 cancelled |
| `chromium` (real pipeline: journey, 12 view tests, perf) | 14 passed, 0 failed (8.8 min) | 0 console errors, 0 warnings, 0 page errors, 0 failed requests, 0 HTTP ≥ 400; 1 cancelled; 0 allowed |
| `firefox` (journey) | 1 passed, 0 failed (19.9 s) | 0 errors; 4 warnings, all on the Firefox WebGL list; 0 unexpected; 1 cancelled |
| `webkit` (journey) | 1 passed, 0 failed (27.2 s run) | 0 of everything; 1 cancelled |
| axe (WCAG 2.0/2.1 A and AA, 2.2 AA) | 16 scans: 15 in `views.spec.ts` (every view at 1280 and 390, plus the three other run tabs at 1280), 1 in `states.spec.ts` (stopped run) | 0 violations of any impact |

Reports are in `evidence/e2e/` (`playwright-<tag>.json`/`.xml`, `console-guard-<tag>.jsonl`, `vitest-junit.xml`,
`perf.json`), axe results in `evidence/a11y/`, and screenshots in `evidence/screenshots/` (views) and
`evidence/screenshots/states/`. The Firefox runner did not exit after its test passed: its worker stayed alive after
the browser had closed (the same thing happened in an earlier combined Firefox + WebKit run), so it wrote no JSON/JUnit
report. Its list output is saved as `evidence/e2e/playwright-firefox.log`, and its guard record is in
`console-guard-firefox.jsonl`. WebKit and Chromium exit normally.

## Performance

`e2e/perf.spec.ts` (part of the `chromium` suite) measures cold loads in Chromium with this profile: mobile
390 × 844 at 3×, Slow 4G (150 ms RTT, 1.6 Mbit/s down, 750 kbit/s up), 4× CPU slowdown, empty cache, 3 runs per page.
Budget: LCP ≤ 2500 ms, CLS ≤ 0.1, control response < 100 ms. Results from the final run (`evidence/e2e/perf.json`):

| Target | Route | LCP median | Runs (ms) | CLS max | Enforced |
| --- | --- | --- | --- | --- | --- |
| Gzip preview (5202) | `/` | 2356 ms | 2356 / 2368 / 2216 | 0.002 | yes, passes |
| Gzip preview (5202) | `/lab` | 2308 ms | 2292 / 2372 / 2308 | 0.002 | yes, passes |
| API-served (8101), fresh tab | `/` | 4892 ms | 4940 / 4892 / 4776 | 0.002 | no, recorded |
| API-served (8101), fresh tab | `/lab` | 5632 ms | 4856 / 5632 / 7316 | 0.002 | no, recorded |
| API-served (8101), same-origin start | `/` | 2312 ms | 2216 / 2392 / 2312 | 0.002 | no, recorded |
| API-served (8101), same-origin start | `/lab` | 2256 ms | 2256 / 2236 / 2544 | 0.002 | no, recorded |

- The LCP element is the intro paragraph, and FCP equals LCP, so LCP is first paint: the entry chunk (131 kB gzip),
  the CSS and the font. These numbers were measured with the earlier WebGL hero; the hero is now plain CSS inside the
  entry chunk, so `/` loads less than the 629 kB recorded then (402 kB for `/lab`).
- The API-served numbers are slower only in a fresh tab. The API sends `Cross-Origin-Opener-Policy: same-origin`, so
  Chromium moves a fresh navigation into a new renderer process, which costs about 2.4 s here. Starting from a page
  that is already on the API origin (the same-origin-start rows first open `/favicon.svg` there) removes that cost, and the same build is then within
  budget. The transferred bytes are the same as from the gzip preview (629 kB and 402 kB).
- Controls: an asset checkbox click until the selection count is updated and painted takes a median of 18.9 ms
  on desktop (max 62.2 ms over 7 clicks) and 35.3 ms with 4× CPU slowdown (max 48.8 ms).
- The machine was shared with other agents' builds and test runs during measurement; `/` LCP medians varied between
  2.3 and 2.45 s across runs.

## Bundle sizes (production build)

From the final `npm run build` (Vite, minified):

| Chunk | Size | Gzip | Loaded |
| --- | --- | --- | --- |
| `index.html` | 0.90 kB | 0.49 kB | always |
| `index-*.css` | 52.05 kB | 12.30 kB | always |
| `index-*.js` (app shell, router, query, UI kit) | 419.64 kB | 131.32 kB | always |
| `jsx-runtime-*.js` | 8.90 kB | 3.38 kB | always (modulepreload) |
| `with-selector-*.js` | 1.62 kB | 0.71 kB | on demand (shared by lazy chunks) |
| `LabView-*.js` | 39.85 kB | 11.58 kB | `/lab` |
| `validate-*.js` | 5.22 kB | 2.01 kB | Lab and Experiments |
| `RunView-*.js` (includes Recharts) | 450.13 kB | 128.07 kB | `/runs/:id` |
| `Experiments-*.js` | 8.65 kB | 3.47 kB | `/experiments` |
| `History-*.js` | 5.37 kB | 2.20 kB | `/history` |
| `NotFound-*.js` | 0.62 kB | 0.42 kB | unknown routes |

Fonts: Plus Jakarta Sans only, as subset WOFF2 files that load per unicode range.

## Known defects and limits

Known and left as they are, each judged harmless or outside the frontend:

- **Server-written text.** A few strings come verbatim from the API and are not rewritten in the browser, for example
  "INR 3,890" in cost notes (the UI itself formats rupees as `₹`). The backend owns them. The backend now writes
  "1 asset held"; results stored before that fix, including the recorded example, still say "1 assets held".
- **Run labels from the server wrap mid-token.** Long run labels such as "… depth-1 QAOA" can break as "depth-/1" at
  360 px, and the run heading can break after the hyphen at 1280 px too. Nothing overflows.
- **Experiment metric labels wrap at 1280.** Long metric names on the experiment result charts take two lines.
- **Wide tables scroll at narrow widths.** The comparison, sample and holdings tables keep all their columns and
  scroll inside their card at 360–390 px, with a "Scroll for more columns →" hint. The page itself never scrolls
  sideways (checked at 360/390/768/1280/1440).
- **Research notice and footer on run pages.** Run pages show both the research notice card and the footer
  disclaimer. The repetition is deliberate.
- **`/` LCP margin is thin.** On the gzip preview the overview's mobile LCP median is close to the 2.5 s budget
  (2.3–2.45 s across runs on this shared machine); the LCP element is the intro paragraph, so LCP is first paint
  gated by the entry bundle.
- **API-served pages are slower in fresh headless loads.** The API sends `Cross-Origin-Opener-Policy: same-origin`,
  and Chromium swaps to a new renderer on a fresh navigation (~2.4 s here). This is measured and reported, not
  enforced; see Performance.
- **`vite preview` file lock on Windows.** Stop the previews before rebuilding (see Running it).
- **`RunResultView.tsx` formatting.** One accidental prettier pass changed line breaks in this file (no behaviour
  change); it was reformatted to the project style, so only some JSX wrapping differs.
