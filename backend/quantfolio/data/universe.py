"""NIFTY 50 universe (symbols, names, NSE sector classification, ISIN).

Source: NSE Indices official constituent CSV, downloaded 2026-10-08 (see
data/universe/SOURCE.md). It is the *current* membership, not point-in-time
historical membership, so every historical evaluation carries survivorship bias.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .paths import find_data_file

UNIVERSE_FILE = "universe/ind_nifty50list_2026-10-08.csv"
UNIVERSE_SOURCE = "NSE Indices official NIFTY 50 constituent list (ind_nifty50list.csv)"
UNIVERSE_AS_OF = date(2026, 10, 8)
SURVIVORSHIP_NOTE = (
    "Universe is the NIFTY 50 membership as of 2026-10-08, applied to earlier periods. "
    "Stocks that left the index are absent and recent entrants are included before they joined, "
    "so historical (including held-out) performance is subject to survivorship bias."
)


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    sector: str
    isin: str


@dataclass(frozen=True)
class Universe:
    assets: tuple[Asset, ...]
    source: str
    as_of: date

    @property
    def symbols(self) -> list[str]:
        return [a.symbol for a in self.assets]

    def get(self, symbol: str) -> Asset | None:
        return self._index().get(symbol.upper())

    def sector_of(self, symbol: str) -> str:
        a = self.get(symbol)
        return a.sector if a else "Unknown"

    def _index(self) -> dict[str, Asset]:
        idx = getattr(self, "_idx_cache", None)
        if idx is None:
            idx = {a.symbol: a for a in self.assets}
            object.__setattr__(self, "_idx_cache", idx)
        return idx


def _read_universe(path: Path) -> Universe:
    assets: list[Asset] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("Symbol") or "").strip().upper()
            if not sym:
                continue
            assets.append(
                Asset(
                    symbol=sym,
                    name=(row.get("Company Name") or "").strip(),
                    sector=(row.get("Industry") or "Unknown").strip(),
                    isin=(row.get("ISIN Code") or "").strip(),
                )
            )
    if len({a.symbol for a in assets}) != len(assets):
        raise ValueError("duplicate symbols in universe file")
    return Universe(assets=tuple(assets), source=UNIVERSE_SOURCE, as_of=UNIVERSE_AS_OF)


@lru_cache(maxsize=1)
def load_universe() -> Universe:
    return _read_universe(find_data_file(UNIVERSE_FILE))
