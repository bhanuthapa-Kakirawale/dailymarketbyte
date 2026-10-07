"""The exact population every Market Structure statistic is about.

A `UniverseDefinition` is one named NSE index's constituent list as NSE Indices published it
(`ind_<index>list.csv`): symbol, company and NSE's own 'Industry' classification, with where and
when it was read. Nothing else may define a universe - in particular not "whatever stocks the
Radar happened to have data for". A fallback list is not the index and cannot be used.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from .sectors import sector_for

LABELS = {"NIFTY50": "NIFTY 50", "NIFTY100": "NIFTY 100", "NIFTY200": "NIFTY 200",
          "NIFTY500": "NIFTY 500"}
OFFICIAL_SOURCE = "NSE_CONSTITUENT_FILE"


class UniverseError(ValueError):
    """The constituent list cannot define this universe (fallback, wrong size, mixed index)."""


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company: str
    industry: str          # NSE Indices' own 'Industry' value, verbatim ("" when absent)
    sector: str            # our deterministic display bucket (sectors.sector_for)


@dataclass
class UniverseDefinition:
    index: str                               # "NIFTY200"
    constituents: dict                       # symbol -> Constituent
    source: str                              # OFFICIAL_SOURCE, or the fallback marker
    source_reference: str                    # the file URL
    retrieved_at: str | None = None
    notes: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return LABELS.get(self.index, self.index)

    @property
    def size(self) -> int:
        return len(self.constituents)

    @property
    def official(self) -> bool:
        return self.source == OFFICIAL_SOURCE

    def symbols(self) -> set:
        return set(self.constituents)

    def companies(self) -> dict:
        return {s: c.company for s, c in self.constituents.items()}

    def require_official(self) -> None:
        if not self.official:
            raise UniverseError(f"{self.label}: constituent list is {self.source}, not NSE's own "
                                "file - no Market Structure statistic may be computed over it")
        nominal = {"NIFTY50": 50, "NIFTY100": 100, "NIFTY200": 200, "NIFTY500": 500}.get(self.index)
        if nominal and self.size != nominal:
            raise UniverseError(f"{self.label}: constituent file lists {self.size} symbols, "
                                f"expected {nominal}")

    def sector_mapping(self) -> dict:
        counts = {}
        for c in self.constituents.values():
            counts[c.sector] = counts.get(c.sector, 0) + 1
        return {"source": "NSE Indices constituent file, 'Industry' column",
                "source_reference": self.source_reference, "retrieved_at": self.retrieved_at,
                "constituents_by_sector": dict(sorted(counts.items())),
                "unclassified": sorted(s for s, c in self.constituents.items()
                                       if c.sector == "Unclassified")}

    def to_dict(self) -> dict:
        return {"index": self.index, "label": self.label, "size": self.size,
                "source": self.source, "source_reference": self.source_reference,
                "retrieved_at": self.retrieved_at, "notes": list(self.notes),
                "constituents": {s: {"company": c.company, "industry": c.industry,
                                     "sector": c.sector}
                                 for s, c in sorted(self.constituents.items())}}

    @classmethod
    def from_dict(cls, d: dict) -> "UniverseDefinition":
        cons = {s: Constituent(s, v.get("company", ""), v.get("industry", ""),
                               sector_for(v.get("industry", "")))
                for s, v in (d.get("constituents") or {}).items()}
        return cls(index=d["index"], constituents=cons, source=d.get("source", ""),
                   source_reference=d.get("source_reference", ""),
                   retrieved_at=d.get("retrieved_at"), notes=list(d.get("notes") or []))


def from_constituent_csv(text: str, index: str, source_reference: str,
                         retrieved_at: str | None = None) -> UniverseDefinition:
    """Parse NSE's `Company Name,Industry,Symbol,Series,ISIN Code` file."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Symbol" not in rows[0]:
        raise UniverseError("not an NSE constituent file (no Symbol column)")
    cons = {}
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        ind = (r.get("Industry") or "").strip()
        cons[sym] = Constituent(sym, (r.get("Company Name") or "").strip(), ind, sector_for(ind))
    return UniverseDefinition(index=index, constituents=cons, source=OFFICIAL_SOURCE,
                              source_reference=source_reference, retrieved_at=retrieved_at)


def from_market_meta(meta: dict) -> UniverseDefinition:
    """From `market.UNIVERSE_META[index]` - the same download `market.get_universe` made."""
    inds = meta.get("industries") or {}
    cons = {s: Constituent(s, name, inds.get(s, ""), sector_for(inds.get(s, "")))
            for s, name in (meta.get("companies") or {}).items()}
    return UniverseDefinition(index=meta["index"], constituents=cons, source=meta.get("source", ""),
                              source_reference=meta.get("source_reference", ""),
                              retrieved_at=meta.get("retrieved_at"))


__all__ = ["Constituent", "UniverseDefinition", "UniverseError", "from_constituent_csv",
           "from_market_meta", "LABELS", "OFFICIAL_SOURCE"]
