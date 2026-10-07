"""MarketStructureSnapshot: counts over ONE named universe, reconciled and coverage-checked.

Every metric carries numerator, denominator, coverage and a sector distribution whose rows sum
exactly to the numerator (checked - a snapshot that does not reconcile raises). Coverage policy,
per metric, deterministic:

    coverage == 100%                 PUBLISHABLE  "18 / 200"
    MIN_COVERAGE_PCT <= cov < 100%   PARTIAL      denominator = covered count, and the scene
                                                  says so: "18 of 196 covered (200 in index)"
    cov < MIN_COVERAGE_PCT           SUPPRESSED   never shown

The universe is fixed at construction. An observation from another universe is rejected, so a
NIFTY 100 reading can never be counted as a NIFTY 200 one.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from fractions import Fraction

from .observations import UNUSUAL_VOLUME_RVOL, StructureObservation
from .sectors import MAPPING_VERSION, UNCLASSIFIED

MIN_COVERAGE_PCT = 95.0
SNAPSHOT_VERSION = "market-structure-1.0"
PUBLISHABLE, PARTIAL, SUPPRESSED = "PUBLISHABLE", "PARTIAL", "SUPPRESSED"


class ReconciliationError(AssertionError):
    pass


@dataclass
class Metric:
    key: str                          # UNUSUAL_VOLUME / RANGE_UP / RANGE_DOWN / ADVANCES / DECLINES
    numerator: int
    denominator: int                  # covered constituents for this metric
    universe_size: int
    coverage_pct: float
    status: str
    by_sector: dict                   # sector -> count (sums to numerator)
    observation_ids: list = field(default_factory=list)

    @property
    def denominator_text(self) -> str:
        if self.denominator == self.universe_size:
            return f"{self.numerator} / {self.universe_size}"
        return f"{self.numerator} of {self.denominator} covered ({self.universe_size} in index)"

    def top_sector(self):
        """The single largest named sector - None on a tie for first (no sector "leads" then)."""
        rows = sorted(((s, n) for s, n in self.by_sector.items() if s != UNCLASSIFIED),
                      key=lambda r: (-r[1], r[0]))
        if not rows or (len(rows) > 1 and rows[1][1] == rows[0][1]):
            return None
        return rows[0]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["denominator_text"] = self.denominator_text
        return d


@dataclass
class MarketStructureSnapshot:
    session_date: str
    universe: str                      # "NIFTY200"
    universe_label: str                # "NIFTY 200"
    constituent_count: int
    metrics: dict                      # key -> Metric
    sector_mapping: dict
    universe_source: dict
    definitions: dict
    unclassified: list
    version: str = SNAPSHOT_VERSION
    subsets: dict = field(default_factory=dict)    # e.g. {"NIFTY100": {...}} contrast

    def metric(self, key) -> Metric | None:
        return self.metrics.get(key)

    def to_dict(self, include_observation_ids: bool = True) -> dict:
        return {"version": self.version, "session_date": self.session_date,
                "universe": self.universe, "universe_label": self.universe_label,
                "constituent_count": self.constituent_count,
                "metrics": {k: (m.to_dict() if include_observation_ids else
                                {kk: vv for kk, vv in m.to_dict().items() if kk != "observation_ids"})
                            for k, m in self.metrics.items()},
                "sector_mapping": self.sector_mapping, "universe_source": self.universe_source,
                "definitions": self.definitions, "unclassified": self.unclassified,
                "subsets": self.subsets}


DEFINITIONS = {
    "UNUSUAL_VOLUME": f"session volume at least {UNUSUAL_VOLUME_RVOL:g}x the stock's own average "
                      "over the prior 20 sessions (Radar volume detector, RVOL v2.0)",
    "RANGE_UP": "closed above the high of its prior 20 (or 50) sessions",
    "RANGE_DOWN": "closed below the low of its prior 20 (or 50) sessions",
    "ADVANCES": "closed higher than on the previous canonical session",
    "DECLINES": "closed lower than on the previous canonical session",
}


def _status(coverage_pct: float) -> str:
    if coverage_pct >= 100.0 - 1e-9:
        return PUBLISHABLE
    return PARTIAL if coverage_pct >= MIN_COVERAGE_PCT else SUPPRESSED


def _metric(key, obs, covered_attr, hit, size) -> Metric:
    covered = [o for o in obs if getattr(o, covered_attr)]
    hits = [o for o in covered if hit(o)]
    by_sector = {}
    for o in hits:
        by_sector[o.sector] = by_sector.get(o.sector, 0) + 1
    by_sector = dict(sorted(by_sector.items(), key=lambda r: (-r[1], r[0])))
    cov = round(100.0 * len(covered) / size, 2) if size else 0.0
    m = Metric(key=key, numerator=len(hits), denominator=len(covered), universe_size=size,
               coverage_pct=cov, status=_status(cov), by_sector=by_sector,
               observation_ids=[o.observation_id for o in hits])
    if sum(by_sector.values()) != m.numerator or m.numerator > m.denominator:
        raise ReconciliationError(f"{key}: sector rows {sum(by_sector.values())} != {m.numerator}")
    return m


def aggregate(observations, universe_def, session_date: dt.date,
              subset_defs=()) -> MarketStructureSnapshot:
    universe_def.require_official()
    obs = list(observations)
    foreign = [o for o in obs if o.universe != universe_def.index]
    if foreign:
        raise ValueError(f"{len(foreign)} observation(s) belong to another universe "
                         f"({sorted({o.universe for o in foreign})}) - universes are never mixed")
    obs = [o for o in obs if o.symbol in universe_def.constituents]
    if len({o.symbol for o in obs}) != len(obs):
        raise ReconciliationError("duplicate constituent observations")
    size = universe_def.size
    metrics = {
        "UNUSUAL_VOLUME": _metric("UNUSUAL_VOLUME", obs, "volume_covered",
                                  lambda o: o.unusual_volume, size),
        "RANGE_UP": _metric("RANGE_UP", obs, "technical_covered", lambda o: o.range_up, size),
        "RANGE_DOWN": _metric("RANGE_DOWN", obs, "technical_covered", lambda o: o.range_down, size),
        "ADVANCES": _metric("ADVANCES", obs, "breadth_covered",
                            lambda o: (o.price_change_pct or 0) > 0, size),
        "DECLINES": _metric("DECLINES", obs, "breadth_covered",
                            lambda o: (o.price_change_pct or 0) < 0, size),
    }
    adv, dec = metrics["ADVANCES"], metrics["DECLINES"]
    if adv.denominator != dec.denominator or adv.numerator + dec.numerator > adv.denominator:
        raise ReconciliationError("breadth does not reconcile")
    subsets = {}
    for sub in subset_defs or ():
        subsets[sub.index] = subset_contrast(obs, universe_def, sub)
    return MarketStructureSnapshot(
        session_date=session_date.isoformat(), universe=universe_def.index,
        universe_label=universe_def.label, constituent_count=size, metrics=metrics,
        sector_mapping=dict(universe_def.sector_mapping(), mapping_version=MAPPING_VERSION),
        universe_source={"source": universe_def.source,
                         "source_reference": universe_def.source_reference,
                         "retrieved_at": universe_def.retrieved_at},
        definitions=dict(DEFINITIONS), unclassified=universe_def.sector_mapping()["unclassified"],
        subsets=subsets)


def subset_contrast(obs, universe_def, subset_def) -> dict:
    """e.g. NIFTY 100 vs the rest of the NIFTY 200. Only when the subset is EXACTLY contained
    in the universe - otherwise the two populations are not comparable and nothing is said."""
    subset_def.require_official()
    inside = subset_def.symbols()
    if not inside <= universe_def.symbols():
        return {"status": SUPPRESSED, "reason": f"{subset_def.label} is not a subset of "
                                                f"{universe_def.label}"}
    a = [o for o in obs if o.symbol in inside]
    b = [o for o in obs if o.symbol not in inside]
    ma = _metric("UNUSUAL_VOLUME", a, "volume_covered", lambda o: o.unusual_volume, len(inside))
    mb = _metric("UNUSUAL_VOLUME", b, "volume_covered", lambda o: o.unusual_volume,
                 universe_def.size - len(inside))
    ok = ma.status == PUBLISHABLE and mb.status == PUBLISHABLE
    return {"status": PUBLISHABLE if ok else SUPPRESSED, "subset": subset_def.index,
            "subset_label": subset_def.label, "rest_label": f"rest of {universe_def.label}",
            "subset": ma.to_dict(), "rest": mb.to_dict()}


def exact_share(n: int, total: int) -> str | None:
    """A plain-English share only when it is EXACT ("one-third"), never a rounded one."""
    if total <= 0 or n <= 0:
        return None
    f = Fraction(n, total)
    return {Fraction(1, 2): "half", Fraction(1, 3): "one-third", Fraction(2, 3): "two-thirds",
            Fraction(1, 4): "one-quarter", Fraction(3, 4): "three-quarters",
            Fraction(1, 1): "all"}.get(f)


__all__ = ["MarketStructureSnapshot", "Metric", "aggregate", "subset_contrast", "exact_share",
           "MIN_COVERAGE_PCT", "PUBLISHABLE", "PARTIAL", "SUPPRESSED", "ReconciliationError",
           "DEFINITIONS", "StructureObservation"]
