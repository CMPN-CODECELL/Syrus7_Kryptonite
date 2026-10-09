"""Strict validation and ingestion of a user-uploaded wide price CSV.

Format: header ``Date,<SYM1>,<SYM2>,...`` (symbols are NIFTY 50 NSE codes,
optional ``.NS`` suffix), one row per trading day, ISO dates ``YYYY-MM-DD``,
plain positive decimal prices, empty cell = no observation. No formulas,
no quotes tricks, no paths: the filename is only used as a display label.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, timezone

import pandas as pd

from ..contracts import DatasetInfo
from ..pipeline import PipelineError
from .store import make_dataset
from .universe import load_universe

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 51
MIN_ROWS = 60
MAX_PRICE = 1e7
_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-()]{1,100}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUM_RE = re.compile(r"^\d{1,12}(\.\d{1,10})?([eE][+]?\d{1,2})?$")
_SYM_RE = re.compile(r"^[A-Z0-9&_\-]{1,20}$")
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r", "|")


def _bad(message: str, **details: object) -> PipelineError:
    return PipelineError("invalid_upload", message, dict(details))


def safe_display_name(filename: str) -> str:
    base = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not base.lower().endswith(".csv"):
        raise _bad("Only .csv files are accepted.")
    if ".." in base or not _NAME_RE.match(base):
        raise _bad("File name contains unsupported characters.")
    return base


def parse_price_csv(data: bytes) -> tuple[pd.DataFrame, list[str]]:
    """Parse and validate; returns (prices frame, issues). Raises PipelineError."""
    if not data:
        raise _bad("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise _bad(f"The file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    if b"\x00" in data:
        raise _bad("The file contains binary data.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad("The file must be UTF-8 encoded text.") from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        rows = list(reader)
    except csv.Error:
        raise _bad("The file is not valid CSV.") from None
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise _bad("The file needs a header row and at least one data row.")
    if len(rows) - 1 > MAX_ROWS:
        raise _bad(f"Too many rows (limit {MAX_ROWS}).")
    header = [h.strip() for h in rows[0]]
    if len(header) > MAX_COLUMNS:
        raise _bad(f"Too many columns (limit {MAX_COLUMNS}: Date + 50 symbols).")
    if not header or header[0].lower() != "date":
        raise _bad("The first column header must be 'Date'.")
    for cell in header:
        if cell.startswith(_FORMULA_PREFIX):
            raise _bad("Header contains formula-like content.", column=cell[:20])
    universe = load_universe()
    issues: list[str] = []
    symbols: list[str] = []
    keep_cols: list[int] = []
    for j, raw in enumerate(header[1:], start=1):
        sym = raw.upper().removesuffix(".NS")
        if not _SYM_RE.match(sym):
            raise _bad("Invalid symbol in header.", column=j + 1)
        if sym in symbols:
            raise _bad("Duplicate symbol in header.", symbol=sym)
        if universe.get(sym) is None:
            issues.append(f"column {sym} ignored: not a NIFTY 50 universe symbol")
            continue
        symbols.append(sym)
        keep_cols.append(j)
    if len(symbols) < 2:
        raise _bad("At least two NIFTY 50 symbols are required.")

    dates: list[date] = []
    values: list[list[float]] = []
    today = date.today()
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise _bad("Row has a different number of cells than the header.", line=i)
        for cell in row:
            if cell.strip().startswith(_FORMULA_PREFIX):
                raise _bad("Formula-like or signed content is not allowed.", line=i)
        ds = row[0].strip()
        if not _DATE_RE.match(ds):
            raise _bad("Dates must be ISO formatted YYYY-MM-DD.", line=i)
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            raise _bad("Invalid calendar date.", line=i) from None
        if d.year < 1990 or d > today:
            raise _bad("Date outside the supported range (1990 to today).", line=i)
        vals: list[float] = []
        for j in keep_cols:
            c = row[j].strip()
            if c == "":
                vals.append(math.nan)
                continue
            if not _NUM_RE.match(c):
                raise _bad("Prices must be plain positive decimal numbers.", line=i, column=j + 1)
            v = float(c)
            if not math.isfinite(v) or v <= 0 or v > MAX_PRICE:
                raise _bad("Prices must be finite, positive and below 10 million.", line=i, column=j + 1)
            vals.append(v)
        dates.append(d)
        values.append(vals)
    if len(set(dates)) != len(dates):
        raise _bad("Duplicate dates.")
    if len(dates) < MIN_ROWS:
        raise _bad(f"At least {MIN_ROWS} dated rows are required.")
    frame = pd.DataFrame(values, index=pd.DatetimeIndex(pd.to_datetime(dates), name="Date"), columns=symbols, dtype="float64")
    if not frame.index.is_monotonic_increasing:
        issues.append("rows were not in date order; sorted ascending")
        frame = frame.sort_index()
    empty = [s for s in symbols if frame[s].notna().sum() == 0]
    if empty:
        raise _bad("Some symbols have no prices.", symbols=empty[:10])
    return frame, issues


def ingest_upload(data: bytes, filename: str) -> DatasetInfo:
    """Validate an uploaded CSV and store it as an immutable dataset labelled 'uploaded'."""
    name = safe_display_name(filename)
    frame, issues = parse_price_csv(data)
    try:
        ds = make_dataset(
            frame,
            label="uploaded",
            source=f"user upload '{name}' (unverified prices)",
            id_prefix="upload",
            downloaded_at=datetime.now(timezone.utc),
            strict=True,
        )
    except ValueError as exc:
        raise _bad(f"Data quality check failed: {exc}") from None
    if issues:
        info = ds.info.model_copy(update={"issues": issues + list(ds.info.issues)})
        return info
    return ds.info
