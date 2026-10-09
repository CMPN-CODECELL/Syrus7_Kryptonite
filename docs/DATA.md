# QuantFolio data

What data the application uses, where it comes from, how it is checked, and
what it can and cannot support. The code is in `backend/quantfolio/data/` and
`backend/quantfolio/risk.py`; the tests are in
`backend/tests/core/test_data_risk.py`.

## 1. Universe

- **Source:** NSE Indices official NIFTY 50 constituent list
  (`ind_nifty50list.csv`), downloaded 2026-10-08. Stored as
  `data/universe/ind_nifty50list_2026-10-08.csv`; details in
  `data/universe/SOURCE.md`.
- **Content:** 50 constituents with company name, NSE sector classification
  (used for sector caps), symbol and ISIN.
- **As-of date:** 2026-10-08. It is the membership on that day, not
  point-in-time membership.

### Survivorship bias

The current list is applied to earlier periods. Companies that left the index
are missing, and recent entrants are included before they joined. Historical
figures, including validation and test metrics, are therefore biased upwards
relative to what an investor could have known at the time. Every dataset and
every result carries this note (`DatasetInfo.survivorship_note`) so that it
can be shown next to held-out figures. The application does not correct for
it.

## 2. Datasets and their labels

Every dataset has exactly one of four labels, and the label travels with every
result built on it.

| Label | Meaning |
|---|---|
| `downloaded historical` | Prices fetched from the provider; last price date within 7 days of today |
| `stale cache` | The same kind of dataset, but its last price date is more than 7 days old. Applied when the dataset is read; the stored file is unchanged. Runs carry the warning `stale_data`. |
| `uploaded` | A CSV supplied by the user. Prices are unverified. |
| `synthetic test` | Generated numbers for tests. Not market data. Runs carry the warning `synthetic_data`. |

### Default dataset

| | |
|---|---|
| Id | `nifty50-yahoo-aaa53c0c94ef` |
| Provider | Yahoo Finance via `yfinance` 1.7.0, NSE tickers (`.NS`) |
| Fields | daily adjusted close (used for returns) and unadjusted close (used only for indicative share counts) |
| Range | 2019-01-01 to 2026-10-08, 1925 rows, 50 symbols |
| Downloaded | 2026-10-08 17:38 UTC |
| SHA-256 | `aaa53c0c94ef942dfb631ae14ca2c91e2bf0e72ca3b1b40d579efec14ded4a78` |

Quality findings recorded with the dataset: three weekend-dated rows (kept as
observed), one interior missing price (INDIGO), and three symbols whose
history starts after the dataset start (MAXHEALTH 2020-08-21, ETERNAL
2021-07-23, JIOFIN 2023-08-21).

Yahoo Finance is a free, unofficial source. Adjusted closes are computed by
the provider for splits and dividends and can be revised; they have not been
checked against exchange data. The application never contacts the provider
during a run or a test. A refresh is an explicit operator action
(`data.refresh_from_yahoo`): one download call for the 50 tickers, without
threads, producing a new dataset with a new id instead of changing an
existing one.

### Synthetic fixture

`synthetic-test-v1`: 12 symbols, business days 2021-01-01 to 2026-10-07, from
a seeded multi-factor geometric random walk (market factor, sector factors,
idiosyncratic noise; seed 20261008). It is regenerated identically on every
machine, which is what makes the test suite deterministic. Its source field
reads "SYNTHETIC deterministic random walk (tests only, not market data)".

## 3. Storage, identity and provenance

- A dataset is a wide table: rows are dates, columns are NSE symbols, values
  are prices, `NaN` means no observation.
- Datasets are immutable. They are written once, atomically, to
  `data/cache/datasets/<id>.parquet` with a `.json` metadata file, and are
  never modified.
- The id ends with the first 12 hex characters of the content SHA-256. The
  hash covers column names, dates and the exact float64 values of the
  adjusted and unadjusted tables, so it does not depend on the Parquet writer.
- The hash is recomputed on every load; a mismatch fails the load.
- Dataset ids accepted from clients contain only letters, digits and `-` and
  are at most 64 characters. They are looked up in the dataset directory;
  clients cannot supply a path or URL.
- A run's cache key includes the dataset SHA-256, the full configuration, the
  seeds, the implementation and schema versions and the simulator and
  numerical library versions. Changing any price changes the key.
- Each result records the dataset id, label, SHA-256, source, download time
  and date range, together with the estimation details below.

## 4. Missing data

Prices are stored as received. Nothing is back-filled with later data.

At estimation time, on the training window only:

1. A gap is forward-filled from the last earlier observation for at most 5
   trading days.
2. A symbol is dropped from the run, with an explicit issue naming the reason,
   if it is not in the dataset, if its first price is more than 5 trading days
   after the training start, or if it has prices on less than 95 % of the
   training dates.
3. Returns are computed on the remaining symbols and only complete rows are
   kept (a gap longer than 5 days removes the unfilled days, and the first
   day after them, for all symbols).
4. At least 60 complete training return observations are required; otherwise
   the run is rejected with `insufficient_training_data`.

## 5. Returns and risk estimates

- **Returns:** simple daily returns on adjusted close,
  `r_t = P_t / P_{t-1} - 1`. Simple returns add across assets
  (`r_p = sum w_i r_i`), which is what `w'mu` and `w'Sigma w` assume; log
  returns do not.
- **Annualisation:** `mu = 252 * mean(r)`, `Sigma = 252 * Cov(r)`. This
  assumes independent, identically distributed daily returns.
- **Covariance:** Ledoit-Wolf shrinkage towards a scaled identity,
  `(1 - d) S + d * tr(S)/n * I`, on daily returns, then multiplied by 252.
  The shrinkage intensity `d` is reported.
- **Positive semi-definiteness:** the matrix is symmetrised and, if its
  smallest eigenvalue is below `1e-10`, eigenvalues are floored at that value.
  The smallest eigenvalue before and after and whether a correction was made
  are reported; the correction is never silent.

Sample mean returns over a few years are imprecise, and the optimiser treats
them as known. This is the largest source of error in any mean-variance
result and no shrinkage is applied to the means.

## 6. Date splits and held-out isolation

A run has three consecutive, non-overlapping date ranges. Defaults:

| Segment | Range | Used for |
|---|---|---|
| Training | 2022-01-03 to 2024-12-31 | `mu`, `Sigma`, screening, the QUBO, all optimisation, configuration selection |
| Validation | 2025-01-01 to 2025-09-30 | reporting only |
| Test | 2025-10-01 to 2026-10-07 | reporting only |

The training end date is inclusive. Estimation reads `prices[:train_end]` and
nothing later. Tests replace every price after the training end with different
values and require byte-identical estimates, screening, QUBO, trained
parameters, sample counts, solver portfolios, experiment cells and selected
configuration; only the held-out metrics may change.

**Held-out metrics** are buy-and-hold: the portfolio is bought at the last
close before the segment and held to the segment end, without rebalancing.
Reported: total return, annualised return, annualised volatility, Sharpe ratio
(against the configured risk-free rate), maximum drawdown, and total return
net of the one-off transaction cost (charged once). Cash earns zero. A segment
with fewer than 20 daily observations, or beyond the dataset end, is reported
as insufficient instead of producing numbers.

These are single-period results on one validation and one test window. They
describe what happened in those windows, with survivorship bias, and are not
evidence of future performance. Validation results are shown by experiments
but are not used to choose a configuration, and test results are never used
for any choice.

## 7. Uploads

A user may upload a wide price CSV. It is validated strictly and stored as a
new immutable dataset labelled `uploaded`.

Format: header `Date,<SYM1>,<SYM2>,...`; one row per trading day; ISO dates
`YYYY-MM-DD`; plain positive decimal prices; an empty cell means no
observation. Symbols are NIFTY 50 NSE codes, with or without `.NS`.

| Rule | Limit |
|---|---|
| Encoding | UTF-8 text, no NUL bytes |
| Size | 5 MB |
| Rows | 60 to 20 000 dated rows |
| Columns | `Date` plus at most 50 symbols; at least 2 known symbols |
| Symbols | must match `[A-Z0-9&_-]{1,20}`; duplicates rejected; symbols outside the universe are ignored and listed |
| Dates | valid calendar dates, 1990 to today, no duplicates |
| Prices | plain decimal numbers, finite, greater than 0, at most 10 000 000; `NaN`, `inf` and signed values are rejected |
| Rows | every row has the same number of cells as the header |
| Formula content | any cell starting with `=`, `+`, `-`, `@`, tab, carriage return or `|` is rejected |
| File name | used only as a display label; base name only, must end in `.csv` and match `[A-Za-z0-9 _.()-]{1,100}` |

Any violation returns the error code `invalid_upload` with the line and
column where it applies. The data layer parses the bytes in memory and stores
the result under a content-hash id; the file name is never used as a path.
Uploaded prices are not checked against any market source, which is why the
label is distinct and is shown on every result.

## 8. What the data does not include

- Point-in-time index membership (see survivorship bias).
- Delisted companies.
- Intraday prices, bid-ask spreads, market impact, or taxes. Transaction
  costs are the user's proportional and fixed-fee parameters, not measured
  costs.
- Dividends as separate cash flows; they enter only through the provider's
  adjusted close.
- Any verification of the provider's prices against exchange data.
