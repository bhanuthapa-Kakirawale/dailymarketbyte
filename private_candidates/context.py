"""Read-only context from the four existing subsystems (Market Regime, Market Structure,
Institutional Flows, Market Events) plus the OHLCV store - each read through THAT subsystem's
own point-in-time primitive, never a uniform wrapper that assumes one cutoff convention (the
four genuinely differ - see the table in docs/PRIVATE_TRADE_CANDIDATES.md):

    Market Regime          no cutoff gate at all (pure date lookup, PIT-safe by construction)
    Market Structure       `load_revision_as_of(out_dir, session, cutoff_iso)`  - inclusive <=
    Institutional Flows    `store.load_latest(..., first_retrieved_before=...)` - inclusive <=
    Market Events          `store.load_latest(..., first_retrieved_before=...)` - EXCLUSIVE <

Nothing here recomputes a subsystem's own calculation (regime classification, 52-week breadth,
sector mapping, institutional flow materiality, event lifecycle) - it only reads what that
subsystem already produced. The one new, local, read-only computation this module performs is
the equal-weight sector series (`metrics.build_equal_weight_sector_series`) fed into the
EXISTING `radar.relative.scan_relative_performance_universe` (which already supports
`sector_map`/`sector_series_by_name` - production simply never feeds them; see
docs/PRIVATE_TRADE_CANDIDATES.md for why this package does not change that production wiring).
"""
from __future__ import annotations

import datetime as dt

from market_events.models import BUYBACK, DELISTING, EARNINGS, OFS, OPEN_OFFER

# Stock-level Market Events families only (GOVT_SECURITIES_AUCTION is market-wide, symbol=None;
# IPO is a read-only projection over ipo_watch, not meaningful as a Radar-candidate's own event).
STOCK_EVENT_FAMILIES = (EARNINGS, BUYBACK, OPEN_OFFER, DELISTING, OFS)


def _as_date(d) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


# ------------------------------------------------------------------ Market Regime
def regime_snapshots(repo, regime_dir: str, session: dt.date, n: int = 30):
    """`RegimeStore.snapshots(session, n)`, oldest first - no cutoff parameter exists for this
    subsystem (date-indexed only; PIT-safe by construction via `RegimeData.until(d)`)."""
    from private_desk.regime.store import RegimeStore
    store = RegimeStore(repo, regime_dir)
    return store.snapshots(session, n=n)


# ------------------------------------------------------------------ Market Structure
def market_structure_as_of(out_dir: str, session: dt.date, cutoff_iso: str) -> dict | None:
    """The artifact genuinely on disk for `session` as of `cutoff_iso` - never
    `private_desk.repository`'s mutable-current-file shortcut, which is not point-in-time."""
    from market_structure.store import load_revision_as_of
    return load_revision_as_of(out_dir, session, cutoff_iso)


def sector_map_and_members(artifact: dict | None) -> tuple:
    """`(sector_map, members_by_sector)` from a Market Structure artifact's own universe
    constituents - the ONE canonical sector source (`market_structure.sectors.sector_for`,
    already applied when the artifact was built). `{}, {}` when no artifact is available."""
    constituents = ((artifact or {}).get("universe") or {}).get("constituents") or {}
    sector_map, members = {}, {}
    for symbol, info in constituents.items():
        sector = info.get("sector")
        if not sector:
            continue
        sector_map[symbol] = sector
        members.setdefault(sector, []).append(symbol)
    return sector_map, members


def observation_index(artifact: dict | None) -> dict:
    """`{symbol: observation}` from a Market Structure artifact's own per-constituent
    observations - no such lookup helper exists anywhere else in the repo (every existing
    caller manually filters the list); built fresh here rather than duplicated per caller."""
    return {o["symbol"]: o for o in (artifact or {}).get("observations") or [] if o.get("symbol")}


# ------------------------------------------------------------------ Institutional Flows (sector-level only)
def nsdl_sector_flows(out_dir: str, cutoff: dt.datetime, on_or_before: dt.date | None = None) -> list:
    """Sector-level (never stock-level) FPI flow direction from the latest NSDL snapshot known
    as of `cutoff` - `institutional_flows.sector_flow.sector_flows`, unmodified."""
    from institutional_flows.context import load_institutional
    from institutional_flows.sector_flow import sector_flows
    ctx = load_institutional(out_dir, cutoff=cutoff, live=False, on_or_before=on_or_before)
    if ctx.nsdl_latest is None:
        return []
    return sector_flows(ctx.nsdl_latest)


# ------------------------------------------------------------------ Market Events (stock-level)
def stock_events(out_dir: str, symbol: str, session: dt.date, cutoff_iso: str,
                 families: tuple = STOCK_EVENT_FAMILIES) -> list:
    """Every stock-level event known for `symbol` as of `cutoff_iso`, across `families` -
    `market_events.store.load_latest` called directly (bypassing
    `private_desk/services/market_events.py`'s `stock_official_rows`, which never passes
    `first_retrieved_before` and is therefore not point-in-time)."""
    from market_events.store import load_latest
    out = []
    for family in families:
        out.extend(load_latest(out_dir, family, symbol=symbol, first_retrieved_before=cutoff_iso))
    return out


# ------------------------------------------------------------------ OHLCV (date-bound only - see docs)
def daily_rows(repo, symbol: str, session: dt.date, lookback_days: int = 400) -> list:
    """`[{"date","open","high","low","close","volume"}]` oldest first, from the read-only
    OHLCV store (`DeskRepository.ohlcv_rows`), bounded by `session` (date-bound PIT safety -
    OHLCV has no revision chain, unlike the other three subsystems; sufficient for a session
    that is fully in the past, see docs/PRIVATE_TRADE_CANDIDATES.md)."""
    from storage.ohlcv_models import QualityStatus
    start = session - dt.timedelta(days=lookback_days)
    rows = repo.ohlcv_rows([symbol], start, session)
    return [{"date": r.session_date, "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume} for r in sorted(rows, key=lambda r: r.session_date)
           if r.quality_status == QualityStatus.OK]


def sector_series(repo, members: list, session: dt.date, lookback_days: int = 400) -> list:
    """`metrics.build_equal_weight_sector_series` over whichever `members` have OHLCV in the
    store - a read-only batched query (`DeskRepository.ohlcv_rows`), never a per-symbol call."""
    from .metrics import build_equal_weight_sector_series
    if not members:
        return []
    start = session - dt.timedelta(days=lookback_days)
    from storage.ohlcv_models import QualityStatus
    rows = repo.ohlcv_rows(members, start, session)
    by_symbol: dict = {}
    for r in rows:
        if r.quality_status != QualityStatus.OK:
            continue
        by_symbol.setdefault(r.symbol, []).append(
            {"date": r.session_date, "close": r.close})
    return build_equal_weight_sector_series(by_symbol)


def sector_relative_performance(repo, symbol: str, sector: str | None, members_by_sector: dict,
                                session: dt.date, benchmark_series: list) -> dict | None:
    """Stock-vs-sector relative strength via the EXISTING `radar.relative.
    scan_relative_performance_universe` (which already supports `sector_map`/
    `sector_series_by_name` - production simply never feeds them). Computed fresh, locally,
    read-only; never modifies `radar/daily_pipeline.py`'s production wiring. `None` when no
    sector is known or no sector series could be built."""
    if not sector:
        return None
    members = members_by_sector.get(sector) or []
    sec_series = sector_series(repo, members, session)
    if not sec_series:
        return None
    symbol_rows = daily_rows(repo, symbol, session)
    if not symbol_rows:
        return None
    from radar.relative import scan_relative_performance_universe
    snap = scan_relative_performance_universe(
        [symbol], {symbol: symbol_rows}, benchmark_series, session,
        sector_map={symbol: sector}, sector_series_by_name={sector: sec_series},
        sector_source="private_candidates equal-weight sector series (not an official index)")
    return snap.results[0].to_dict() if snap.results else None


__all__ = ["STOCK_EVENT_FAMILIES", "regime_snapshots", "market_structure_as_of",
          "sector_map_and_members", "observation_index", "nsdl_sector_flows", "stock_events",
          "daily_rows", "sector_series", "sector_relative_performance"]
