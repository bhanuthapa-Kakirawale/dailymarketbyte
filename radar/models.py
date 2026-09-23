"""Typed output of the unusual-volume detector.

Everything here describes participation that already happened, relative to a stock's own
recent history. `AnomalyLevel` is a descriptive label for how far today's volume sits from
that history - never an investment rating, never derived from price direction.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from enum import Enum

RADAR_SCHEMA_VERSION = "1.0"
# Bumped when classification or percentile logic changes meaning, so two snapshots are never
# silently compared across an algorithm change.
CALCULATION_VERSION = "1.0"


class AnomalyLevel(str, Enum):
    """How unusual today's volume is relative to this stock's own history.

    Internal, descriptive classification only - not a BUY/SELL rating and not derived from
    price direction. `radar.thresholds.classify_anomaly` is the only place a level is set.
    """
    ELEVATED = "ELEVATED"
    UNUSUAL = "UNUSUAL"
    EXTREME = "EXTREME"


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(value) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


def _parse_dt(value) -> dt.datetime | None:
    if value is None or isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(value)


@dataclass
class VolumeAnomaly:
    """One stock's relative-volume reading, classified and traced back to canonical facts.

    `supporting_fact_ids`/`supporting_report_ids` are not decoration: a reading that cannot
    be traced back to the canonical records behind it is an assertion, not a detection, and
    this pipeline does not publish assertions. `price_change_pct` is carried for context only
    and must never be read by classification - a modest price move with extreme relative
    volume is exactly the case this detector exists to surface.
    """
    instrument: str
    market_date: dt.date
    current_volume: float | None
    average_volume_20: float | None
    relative_volume: float | None
    prior_sessions_available: int
    rvol_percentile: float | None
    rvol_historical_rank: int | None
    highest_rvol_in_n_sessions: dict | None
    price_change_pct: float | None
    level: AnomalyLevel | None
    why_flagged: str
    supporting_fact_ids: list = field(default_factory=list)
    supporting_report_ids: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    definition_version: str | None = None
    data_quality: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "market_date": _iso(self.market_date),
            "current_volume": self.current_volume,
            "average_volume_20": self.average_volume_20,
            "relative_volume": self.relative_volume,
            "prior_sessions_available": self.prior_sessions_available,
            "rvol_percentile": self.rvol_percentile,
            "rvol_historical_rank": self.rvol_historical_rank,
            "highest_rvol_in_n_sessions": self.highest_rvol_in_n_sessions,
            "price_change_pct": self.price_change_pct,
            "level": self.level.value if self.level else None,
            "why_flagged": self.why_flagged,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "supporting_report_ids": list(self.supporting_report_ids),
            "calculation_version": self.calculation_version,
            "definition_version": self.definition_version,
            "data_quality": list(self.data_quality),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> VolumeAnomaly:
        return cls(
            instrument=d["instrument"], market_date=_parse_date(d["market_date"]),
            current_volume=d.get("current_volume"), average_volume_20=d.get("average_volume_20"),
            relative_volume=d.get("relative_volume"),
            prior_sessions_available=d.get("prior_sessions_available", 0),
            rvol_percentile=d.get("rvol_percentile"),
            rvol_historical_rank=d.get("rvol_historical_rank"),
            highest_rvol_in_n_sessions=d.get("highest_rvol_in_n_sessions"),
            price_change_pct=d.get("price_change_pct"),
            level=AnomalyLevel(d["level"]) if d.get("level") else None,
            why_flagged=d.get("why_flagged", ""),
            supporting_fact_ids=list(d.get("supporting_fact_ids") or []),
            supporting_report_ids=list(d.get("supporting_report_ids") or []),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            definition_version=d.get("definition_version"),
            data_quality=list(d.get("data_quality") or []),
            warnings=list(d.get("warnings") or []))


class TechnicalEventType(str, Enum):
    """A structural transition in price, never a BUY/SELL/bullish/bearish label.

    `radar.technical` is the only place a `TechnicalEvent` is constructed.
    """
    BREAK_ABOVE_20D_RANGE = "BREAK_ABOVE_20D_RANGE"
    BREAK_BELOW_20D_RANGE = "BREAK_BELOW_20D_RANGE"
    BREAK_ABOVE_50D_RANGE = "BREAK_ABOVE_50D_RANGE"
    BREAK_BELOW_50D_RANGE = "BREAK_BELOW_50D_RANGE"
    CROSS_ABOVE_SMA20 = "CROSS_ABOVE_SMA20"
    CROSS_BELOW_SMA20 = "CROSS_BELOW_SMA20"
    CROSS_ABOVE_SMA50 = "CROSS_ABOVE_SMA50"
    CROSS_BELOW_SMA50 = "CROSS_BELOW_SMA50"
    RANGE_COMPRESSION = "RANGE_COMPRESSION"


@dataclass
class TechnicalEvent:
    """One structural event, self-explaining: the numbers behind it travel with it so the
    Radar can always answer "why was this emitted?" without recomputing anything.
    """
    event_type: TechnicalEventType
    evidence: dict = field(default_factory=dict)
    why: str = ""

    def to_dict(self) -> dict:
        return {"event_type": self.event_type.value, "evidence": dict(self.evidence),
               "why": self.why}

    @classmethod
    def from_dict(cls, d: dict) -> TechnicalEvent:
        return cls(event_type=TechnicalEventType(d["event_type"]),
                   evidence=dict(d.get("evidence") or {}), why=d.get("why", ""))


@dataclass
class TechnicalStructure:
    """One stock's price-structure reading for one session - discrete evidence, never a score.

    `prior_20_high`/`prior_20_low`/`prior_50_high`/`prior_50_low` exclude the current session
    by construction (`radar.technical` builds them from sessions strictly before it). `sma20`/
    `sma50` are the conventional inclusive rolling means (the current session's own close DOES
    count toward its own SMA) - a deliberately different convention from the prior-N-exclusive
    range/RVOL windows, documented here so the two are never confused.

    No canonical `MarketHistory` Facts back this reading (High/Low are not persisted anywhere
    in this pipeline yet - see docs/MARKET_INTELLIGENCE_RADAR.md) so provenance is the acquired
    session dates themselves, `supporting_session_dates`, rather than fabricated fact ids.
    """
    instrument: str
    market_date: dt.date
    close: float | None
    prior_20_high: float | None
    prior_20_low: float | None
    prior_50_high: float | None
    prior_50_low: float | None
    sma20: float | None
    sma50: float | None
    distance_from_sma20_pct: float | None
    distance_from_sma50_pct: float | None
    recent_5d_range_pct: float | None
    median_5d_range_pct: float | None
    sessions_available: int
    events: list = field(default_factory=list)
    supporting_session_dates: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    data_quality: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "market_date": _iso(self.market_date),
            "close": self.close,
            "prior_20_high": self.prior_20_high,
            "prior_20_low": self.prior_20_low,
            "prior_50_high": self.prior_50_high,
            "prior_50_low": self.prior_50_low,
            "sma20": self.sma20,
            "sma50": self.sma50,
            "distance_from_sma20_pct": self.distance_from_sma20_pct,
            "distance_from_sma50_pct": self.distance_from_sma50_pct,
            "recent_5d_range_pct": self.recent_5d_range_pct,
            "median_5d_range_pct": self.median_5d_range_pct,
            "sessions_available": self.sessions_available,
            "events": [e.to_dict() for e in self.events],
            "supporting_session_dates": [_iso(d) for d in self.supporting_session_dates],
            "calculation_version": self.calculation_version,
            "data_quality": list(self.data_quality),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TechnicalStructure:
        return cls(
            instrument=d["instrument"], market_date=_parse_date(d["market_date"]),
            close=d.get("close"), prior_20_high=d.get("prior_20_high"),
            prior_20_low=d.get("prior_20_low"), prior_50_high=d.get("prior_50_high"),
            prior_50_low=d.get("prior_50_low"), sma20=d.get("sma20"), sma50=d.get("sma50"),
            distance_from_sma20_pct=d.get("distance_from_sma20_pct"),
            distance_from_sma50_pct=d.get("distance_from_sma50_pct"),
            recent_5d_range_pct=d.get("recent_5d_range_pct"),
            median_5d_range_pct=d.get("median_5d_range_pct"),
            sessions_available=d.get("sessions_available", 0),
            events=[TechnicalEvent.from_dict(e) for e in d.get("events", [])],
            supporting_session_dates=[_parse_date(x) for x in d.get("supporting_session_dates") or []],
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            data_quality=list(d.get("data_quality") or []), warnings=list(d.get("warnings") or []))


class RelativePersistenceState(str, Enum):
    """Whether a stock's market-relative performance has been directionally consistent across
    the 5-session and 20-session windows, or a one-session blip / a recent reversal. Internal,
    descriptive classification only - never a BUY/SELL rating, never derived from price alone
    (it reads relative-to-benchmark performance, not absolute return). `radar.relative` is the
    only place this is set, from `RelativePerformanceThresholds.persistence_threshold_pp`.
    """
    PERSISTENT_POSITIVE = "PERSISTENT_POSITIVE"
    PERSISTENT_NEGATIVE = "PERSISTENT_NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"


@dataclass
class RelativePerformance:
    """One stock's return relative to its market benchmark (and, where a mapping exists, its
    sector benchmark) across 1/5/20-session windows - never a prediction, rating or ranking.

    Absolute return alone is insufficient context: a stock up 3% in a sector up 5% has
    UNDERPERFORMED its sector even though its own return is positive. `market_relative_*_pp`/
    `sector_relative_*_pp` are the return differential in percentage points and are what every
    downstream consumer should read, not `stock_return_*` in isolation.

    Every window is computed independently and can be legitimately absent
    (`stock_return_20d is None`) when the underlying data does not support it - never silently
    computed over a shorter window and reported as the requested one. `sector_*` fields are
    `None` whenever no reliable stock->sector mapping was supplied for this instrument, which is
    the default (see `radar.relative`) - "sector mapping unavailable" is legitimate output, not
    a bug, and this package never fabricates one.
    """
    instrument: str
    session_date: dt.date
    stock_return_1d: float | None
    stock_return_5d: float | None
    stock_return_20d: float | None
    market_return_1d: float | None
    market_return_5d: float | None
    market_return_20d: float | None
    market_relative_1d_pp: float | None
    market_relative_5d_pp: float | None
    market_relative_20d_pp: float | None
    sector: str | None
    sector_return_1d: float | None
    sector_return_5d: float | None
    sector_return_20d: float | None
    sector_relative_1d_pp: float | None
    sector_relative_5d_pp: float | None
    sector_relative_20d_pp: float | None
    persistence_state: RelativePersistenceState | None
    relative_shift_pp: float | None
    supporting_session_dates: list = field(default_factory=list)
    benchmark_source: str | None = None
    sector_source: str | None = None
    calculation_version: str = CALCULATION_VERSION
    data_quality: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "session_date": _iso(self.session_date),
            "stock_return_1d": self.stock_return_1d,
            "stock_return_5d": self.stock_return_5d,
            "stock_return_20d": self.stock_return_20d,
            "market_return_1d": self.market_return_1d,
            "market_return_5d": self.market_return_5d,
            "market_return_20d": self.market_return_20d,
            "market_relative_1d_pp": self.market_relative_1d_pp,
            "market_relative_5d_pp": self.market_relative_5d_pp,
            "market_relative_20d_pp": self.market_relative_20d_pp,
            "sector": self.sector,
            "sector_return_1d": self.sector_return_1d,
            "sector_return_5d": self.sector_return_5d,
            "sector_return_20d": self.sector_return_20d,
            "sector_relative_1d_pp": self.sector_relative_1d_pp,
            "sector_relative_5d_pp": self.sector_relative_5d_pp,
            "sector_relative_20d_pp": self.sector_relative_20d_pp,
            "persistence_state": self.persistence_state.value if self.persistence_state else None,
            "relative_shift_pp": self.relative_shift_pp,
            "supporting_session_dates": [_iso(d) for d in self.supporting_session_dates],
            "benchmark_source": self.benchmark_source,
            "sector_source": self.sector_source,
            "calculation_version": self.calculation_version,
            "data_quality": list(self.data_quality),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> RelativePerformance:
        return cls(
            instrument=d["instrument"], session_date=_parse_date(d["session_date"]),
            stock_return_1d=d.get("stock_return_1d"), stock_return_5d=d.get("stock_return_5d"),
            stock_return_20d=d.get("stock_return_20d"),
            market_return_1d=d.get("market_return_1d"), market_return_5d=d.get("market_return_5d"),
            market_return_20d=d.get("market_return_20d"),
            market_relative_1d_pp=d.get("market_relative_1d_pp"),
            market_relative_5d_pp=d.get("market_relative_5d_pp"),
            market_relative_20d_pp=d.get("market_relative_20d_pp"),
            sector=d.get("sector"), sector_return_1d=d.get("sector_return_1d"),
            sector_return_5d=d.get("sector_return_5d"), sector_return_20d=d.get("sector_return_20d"),
            sector_relative_1d_pp=d.get("sector_relative_1d_pp"),
            sector_relative_5d_pp=d.get("sector_relative_5d_pp"),
            sector_relative_20d_pp=d.get("sector_relative_20d_pp"),
            persistence_state=(RelativePersistenceState(d["persistence_state"])
                               if d.get("persistence_state") else None),
            relative_shift_pp=d.get("relative_shift_pp"),
            supporting_session_dates=[_parse_date(x) for x in d.get("supporting_session_dates") or []],
            benchmark_source=d.get("benchmark_source"), sector_source=d.get("sector_source"),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            data_quality=list(d.get("data_quality") or []), warnings=list(d.get("warnings") or []))


@dataclass
class RelativePerformanceSnapshot:
    """Result of one relative-performance scan. Derived and ephemeral - not canonical history.

    `results` holds every scanned instrument's `RelativePerformance` (unlike `VolumeRadarSnapshot
    .anomalies`/`TechnicalRadarSnapshot.flagged`, which only hold instruments that cleared a
    threshold) - this detector describes context for every stock, not just outliers; Case A of
    the packet spec (a quiet stock with genuine relative strength that is not a top mover) must
    not be dropped. `results` is sorted by instrument, never by the size of the move, so nothing
    here reads as a ranking.
    """
    session_date: dt.date
    generated_at: dt.datetime
    universe_size: int
    universe_source: str
    benchmark_source: str | None = None
    sector_source: str | None = None
    results: list = field(default_factory=list)
    scanned: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    radar_schema_version: str = RADAR_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "radar_schema_version": self.radar_schema_version,
            "calculation_version": self.calculation_version,
            "session_date": _iso(self.session_date),
            "generated_at": _iso(self.generated_at),
            "universe_size": self.universe_size,
            "universe_source": self.universe_source,
            "benchmark_source": self.benchmark_source,
            "sector_source": self.sector_source,
            "results": [r.to_dict() for r in self.results],
            "scanned": list(self.scanned),
            "skipped": dict(self.skipped),
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> RelativePerformanceSnapshot:
        return cls(
            session_date=_parse_date(d["session_date"]), generated_at=_parse_dt(d["generated_at"]),
            universe_size=d.get("universe_size", 0), universe_source=d.get("universe_source", ""),
            benchmark_source=d.get("benchmark_source"), sector_source=d.get("sector_source"),
            results=[RelativePerformance.from_dict(r) for r in d.get("results", [])],
            scanned=list(d.get("scanned") or []), skipped=dict(d.get("skipped") or {}),
            warnings=list(d.get("warnings") or []),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            radar_schema_version=d.get("radar_schema_version", RADAR_SCHEMA_VERSION))

    @classmethod
    def from_json(cls, text: str) -> RelativePerformanceSnapshot:
        return cls.from_dict(json.loads(text))


class EvidenceFamily(str, Enum):
    """The three detector families Packet 4 treats as INDEPENDENT evidence.

    Multiple events inside one family (e.g. a range break plus an SMA cross, or a 5D plus a 20D
    relative-performance reading) still count as ONE family - only agreement ACROSS families is
    treated as corroborating evidence. `radar.composite` is the only place a family is judged
    active or inactive.
    """
    VOLUME = "VOLUME"
    STRUCTURE = "STRUCTURE"
    RELATIVE_PERFORMANCE = "RELATIVE_PERFORMANCE"


class DirectionCompatibility(str, Enum):
    """Whether a candidate's active directional families agree, conflict, or say nothing about
    direction at all. Internal evidence descriptors, never predictions - `radar.composite` never
    emits BULLISH/BEARISH. Volume itself never contributes a direction (relative volume says
    nothing about which way price moved); only STRUCTURE and RELATIVE_PERFORMANCE evidence can.
    """
    ALIGNED_POSITIVE = "ALIGNED_POSITIVE"
    ALIGNED_NEGATIVE = "ALIGNED_NEGATIVE"
    MIXED = "MIXED"
    NON_DIRECTIONAL = "NON_DIRECTIONAL"


class AttentionLevel(str, Enum):
    """Internal editorial-significance label distinguishing a two-family candidate from a rarer
    three-family convergence. Deliberately coarse (two values, not a 0-100 score) and never an
    expected-return or conviction signal - `radar.composite` is the only place this is set, from
    `independent_signal_count` alone.
    """
    NOTABLE = "NOTABLE"
    HIGH_INTEREST = "HIGH_INTEREST"


@dataclass
class StockRadarCandidate:
    """Packet 4 evidence-composition envelope for one instrument on one session.

    `volume_anomaly` (Packet 1), `technical_anomaly` (Packet 2) and `relative_strength`
    (Packet 3) are the three detectors' own typed output, reused by reference - never
    recomputed or duplicated. `evidence`/`reason_codes` are built ONLY from evidence that
    cleared each family's "meaningful" bar (see `radar.composite`); a family that produced only
    contextual evidence (ELEVATED volume, RANGE_COMPRESSION alone, MIXED/NEUTRAL persistence)
    does not appear here and does not count toward `independent_signal_count`.

    An instance only exists for a symbol that cleared `CompositeThresholds
    .min_independent_families` (default 2) - `radar.composite.build_candidates` returns `None`
    for anything short of that, it is never constructed and then filtered later.
    `fundamental_event` remains reserved for a future packet.
    """
    instrument: str
    market_date: dt.date
    volume_anomaly: VolumeAnomaly | None = None
    technical_anomaly: TechnicalStructure | None = None
    relative_strength: RelativePerformance | None = None
    fundamental_event: None = None
    price_change_pct: float | None = None
    evidence: list = field(default_factory=list)
    reason_codes: list = field(default_factory=list)
    independent_signal_count: int = 0
    active_families: list = field(default_factory=list)
    direction_compatibility: DirectionCompatibility | None = None
    attention_level: AttentionLevel | None = None
    supporting_session_dates: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    warnings: list = field(default_factory=list)
    composite_note: str | None = None

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "market_date": _iso(self.market_date),
            "volume_anomaly": self.volume_anomaly.to_dict() if self.volume_anomaly else None,
            "technical_anomaly": self.technical_anomaly.to_dict() if self.technical_anomaly else None,
            "relative_strength": self.relative_strength.to_dict() if self.relative_strength else None,
            "fundamental_event": None,
            "price_change_pct": self.price_change_pct,
            "evidence": list(self.evidence),
            "reason_codes": list(self.reason_codes),
            "independent_signal_count": self.independent_signal_count,
            "active_families": list(self.active_families),
            "direction_compatibility": (self.direction_compatibility.value
                                        if self.direction_compatibility else None),
            "attention_level": self.attention_level.value if self.attention_level else None,
            "supporting_session_dates": [_iso(d) for d in self.supporting_session_dates],
            "calculation_version": self.calculation_version,
            "warnings": list(self.warnings),
            "composite_note": self.composite_note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StockRadarCandidate:
        return cls(
            instrument=d["instrument"], market_date=_parse_date(d["market_date"]),
            volume_anomaly=(VolumeAnomaly.from_dict(d["volume_anomaly"])
                            if d.get("volume_anomaly") else None),
            technical_anomaly=(TechnicalStructure.from_dict(d["technical_anomaly"])
                               if d.get("technical_anomaly") else None),
            relative_strength=(RelativePerformance.from_dict(d["relative_strength"])
                               if d.get("relative_strength") else None),
            price_change_pct=d.get("price_change_pct"),
            evidence=list(d.get("evidence") or []), reason_codes=list(d.get("reason_codes") or []),
            independent_signal_count=d.get("independent_signal_count", 0),
            active_families=list(d.get("active_families") or []),
            direction_compatibility=(DirectionCompatibility(d["direction_compatibility"])
                                     if d.get("direction_compatibility") else None),
            attention_level=AttentionLevel(d["attention_level"]) if d.get("attention_level") else None,
            supporting_session_dates=[_parse_date(x) for x in d.get("supporting_session_dates") or []],
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            warnings=list(d.get("warnings") or []), composite_note=d.get("composite_note"))


@dataclass
class RadarCompositeSnapshot:
    """Result of one evidence-composition pass (Phase 4.2 Packet 4). Derived and ephemeral -
    not canonical history, exactly like the three detector snapshots it joins.

    `candidates` holds only instruments that cleared `CompositeThresholds
    .min_independent_families`, sorted by instrument - never ranked by the size of any move or
    reading. `symbols_with_volume`/`symbols_with_technical`/`symbols_with_relative` count
    symbols for which THAT detector produced a reading for this exact `session_date` (an
    anomaly/flagged-structure/result entry), independent of whether it ended up meaningful or a
    candidate - so a caller can see which detectors actually covered the universe versus which
    were entirely absent. `non_candidates_count` is `universe_size - candidate_count`, i.e. every
    symbol considered by at least one detector that did not clear the eligibility bar.
    """
    session_date: dt.date
    generated_at: dt.datetime
    universe_size: int
    symbols_with_volume: int
    symbols_with_technical: int
    symbols_with_relative: int
    candidates: list = field(default_factory=list)
    candidate_count: int = 0
    non_candidates_count: int = 0
    warnings: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    radar_schema_version: str = RADAR_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "radar_schema_version": self.radar_schema_version,
            "calculation_version": self.calculation_version,
            "session_date": _iso(self.session_date),
            "generated_at": _iso(self.generated_at),
            "universe_size": self.universe_size,
            "symbols_with_volume": self.symbols_with_volume,
            "symbols_with_technical": self.symbols_with_technical,
            "symbols_with_relative": self.symbols_with_relative,
            "candidates": [c.to_dict() for c in self.candidates],
            "candidate_count": self.candidate_count,
            "non_candidates_count": self.non_candidates_count,
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> RadarCompositeSnapshot:
        return cls(
            session_date=_parse_date(d["session_date"]), generated_at=_parse_dt(d["generated_at"]),
            universe_size=d.get("universe_size", 0),
            symbols_with_volume=d.get("symbols_with_volume", 0),
            symbols_with_technical=d.get("symbols_with_technical", 0),
            symbols_with_relative=d.get("symbols_with_relative", 0),
            candidates=[StockRadarCandidate.from_dict(c) for c in d.get("candidates", [])],
            candidate_count=d.get("candidate_count", 0),
            non_candidates_count=d.get("non_candidates_count", 0),
            warnings=list(d.get("warnings") or []),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            radar_schema_version=d.get("radar_schema_version", RADAR_SCHEMA_VERSION))

    @classmethod
    def from_json(cls, text: str) -> RadarCompositeSnapshot:
        return cls.from_dict(json.loads(text))


@dataclass
class VolumeRadarSnapshot:
    """Result of one unusual-volume scan. Derived and ephemeral - not canonical history."""
    session_date: dt.date
    generated_at: dt.datetime
    universe_size: int
    universe_source: str
    anomalies: list = field(default_factory=list)
    scanned: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    radar_schema_version: str = RADAR_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "radar_schema_version": self.radar_schema_version,
            "calculation_version": self.calculation_version,
            "session_date": _iso(self.session_date),
            "generated_at": _iso(self.generated_at),
            "universe_size": self.universe_size,
            "universe_source": self.universe_source,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "scanned": list(self.scanned),
            "skipped": dict(self.skipped),
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> VolumeRadarSnapshot:
        return cls(
            session_date=_parse_date(d["session_date"]), generated_at=_parse_dt(d["generated_at"]),
            universe_size=d.get("universe_size", 0), universe_source=d.get("universe_source", ""),
            anomalies=[VolumeAnomaly.from_dict(a) for a in d.get("anomalies", [])],
            scanned=list(d.get("scanned") or []), skipped=dict(d.get("skipped") or {}),
            warnings=list(d.get("warnings") or []),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            radar_schema_version=d.get("radar_schema_version", RADAR_SCHEMA_VERSION))

    @classmethod
    def from_json(cls, text: str) -> VolumeRadarSnapshot:
        return cls.from_dict(json.loads(text))


@dataclass
class TechnicalRadarSnapshot:
    """Result of one technical-structure scan. Derived and ephemeral - not canonical history.

    `flagged` holds only instruments with at least one `TechnicalEvent`; `scanned` names every
    instrument considered (flagged or not) - mirroring `VolumeRadarSnapshot`'s
    `anomalies`/`scanned` split.
    """
    session_date: dt.date
    generated_at: dt.datetime
    universe_size: int
    universe_source: str
    flagged: list = field(default_factory=list)
    scanned: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    calculation_version: str = CALCULATION_VERSION
    radar_schema_version: str = RADAR_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "radar_schema_version": self.radar_schema_version,
            "calculation_version": self.calculation_version,
            "session_date": _iso(self.session_date),
            "generated_at": _iso(self.generated_at),
            "universe_size": self.universe_size,
            "universe_source": self.universe_source,
            "flagged": [s.to_dict() for s in self.flagged],
            "scanned": list(self.scanned),
            "skipped": dict(self.skipped),
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> TechnicalRadarSnapshot:
        return cls(
            session_date=_parse_date(d["session_date"]), generated_at=_parse_dt(d["generated_at"]),
            universe_size=d.get("universe_size", 0), universe_source=d.get("universe_source", ""),
            flagged=[TechnicalStructure.from_dict(s) for s in d.get("flagged", [])],
            scanned=list(d.get("scanned") or []), skipped=dict(d.get("skipped") or {}),
            warnings=list(d.get("warnings") or []),
            calculation_version=d.get("calculation_version", CALCULATION_VERSION),
            radar_schema_version=d.get("radar_schema_version", RADAR_SCHEMA_VERSION))

    @classmethod
    def from_json(cls, text: str) -> TechnicalRadarSnapshot:
        return cls.from_dict(json.loads(text))


# ------------------------------------------------------------------ Phase 4.2 Packet 5.4A
class NoveltyType(str, Enum):
    """What changed about a composite Radar candidate since its own most recent prior
    appearance within the novelty lookback window - never a ranking, a score, or an opinion
    about which stock is "better". `radar.novelty` is the only place this is set.

    A candidate with no prior appearance inside the lookback is `NEW_CANDIDATE`; one whose
    state exactly matches its prior appearance is `CONTINUATION`; everything in between names
    the SPECIFIC kind of change (`MULTIPLE_CHANGES` when two or more fire at once, with every
    individual reason preserved rather than collapsed into the umbrella type alone).
    """
    NEW_CANDIDATE = "NEW_CANDIDATE"
    NEW_EVIDENCE_FAMILY = "NEW_EVIDENCE_FAMILY"
    NEW_TECHNICAL_EVENT = "NEW_TECHNICAL_EVENT"
    PERSISTENCE_TRANSITION = "PERSISTENCE_TRANSITION"
    DIRECTION_TRANSITION = "DIRECTION_TRANSITION"
    ATTENTION_ESCALATION = "ATTENTION_ESCALATION"
    MULTIPLE_CHANGES = "MULTIPLE_CHANGES"
    CONTINUATION = "CONTINUATION"


NOVELTY_CALCULATION_VERSION = "1.0"


@dataclass
class CandidateNovelty:
    """One composite Radar candidate's novelty classification for one session - additional
    metadata layered onto the existing `StockRadarCandidate`, never a replacement for it and
    never a reason to drop one: `radar.novelty.classify_session` returns exactly one
    `CandidateNovelty` per input candidate, in the same order, and touches no field on the
    `StockRadarCandidate` it describes.

    `previous_*`/`current_*` carry enough of the compared state for a later editorial layer to
    explain a classification without re-fetching anything; `change_reason_codes` preserves
    EVERY individual triggering reason even when `novelty_type` collapses to `MULTIPLE_CHANGES`
    - see `radar.novelty`'s module docstring for the exact per-type rules.
    """
    instrument: str
    session_date: dt.date
    novelty_type: NoveltyType
    previous_candidate_date: dt.date | None
    sessions_since_previous_candidate: int | None
    new_families: list = field(default_factory=list)
    lost_families: list = field(default_factory=list)
    new_technical_events: list = field(default_factory=list)
    previous_persistence: str | None = None
    current_persistence: str | None = None
    previous_direction: str | None = None
    current_direction: str | None = None
    previous_attention: str | None = None
    current_attention: str | None = None
    change_reason_codes: list = field(default_factory=list)
    reason: str = ""
    calculation_version: str = NOVELTY_CALCULATION_VERSION

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "session_date": _iso(self.session_date),
            "novelty_type": self.novelty_type.value,
            "previous_candidate_date": _iso(self.previous_candidate_date),
            "sessions_since_previous_candidate": self.sessions_since_previous_candidate,
            "new_families": list(self.new_families),
            "lost_families": list(self.lost_families),
            "new_technical_events": list(self.new_technical_events),
            "previous_persistence": self.previous_persistence,
            "current_persistence": self.current_persistence,
            "previous_direction": self.previous_direction,
            "current_direction": self.current_direction,
            "previous_attention": self.previous_attention,
            "current_attention": self.current_attention,
            "change_reason_codes": list(self.change_reason_codes),
            "reason": self.reason,
            "calculation_version": self.calculation_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CandidateNovelty:
        return cls(
            instrument=d["instrument"], session_date=_parse_date(d["session_date"]),
            novelty_type=NoveltyType(d["novelty_type"]),
            previous_candidate_date=_parse_date(d.get("previous_candidate_date")),
            sessions_since_previous_candidate=d.get("sessions_since_previous_candidate"),
            new_families=list(d.get("new_families") or []),
            lost_families=list(d.get("lost_families") or []),
            new_technical_events=list(d.get("new_technical_events") or []),
            previous_persistence=d.get("previous_persistence"),
            current_persistence=d.get("current_persistence"),
            previous_direction=d.get("previous_direction"),
            current_direction=d.get("current_direction"),
            previous_attention=d.get("previous_attention"),
            current_attention=d.get("current_attention"),
            change_reason_codes=list(d.get("change_reason_codes") or []),
            reason=d.get("reason", ""),
            calculation_version=d.get("calculation_version", NOVELTY_CALCULATION_VERSION))


__all__ = ["AnomalyLevel", "VolumeAnomaly", "TechnicalEventType", "TechnicalEvent",
           "TechnicalStructure", "RelativePersistenceState", "RelativePerformance",
           "RelativePerformanceSnapshot", "EvidenceFamily", "DirectionCompatibility",
           "AttentionLevel", "StockRadarCandidate", "RadarCompositeSnapshot",
           "VolumeRadarSnapshot", "TechnicalRadarSnapshot", "RADAR_SCHEMA_VERSION",
           "CALCULATION_VERSION", "NoveltyType", "CandidateNovelty",
           "NOVELTY_CALCULATION_VERSION"]
