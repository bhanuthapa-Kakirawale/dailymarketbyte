"""RegimeData: every input the classifier reads, session-aligned, loaded read-only ONCE.

Sources (all through `DeskRepository`, so read-only by construction):

* the NIFTY 50 benchmark (^NSEI) from the OHLCV store -> the canonical session spine
  (`radar.session_alignment.canonical_session_list`: benchmark bars UNION NSE's published
  sessions, so a provider holiday placeholder never becomes a session and Yahoo's missing
  2026-09-22 is still a session);
* per-constituent OHLCV (quality OK only) placed onto that spine - a row dated on a non-session
  is dropped, a session the stock has no row for stays NaN (never filled);
* NIFTY 200 membership + NSE Industry sector from the Market Structure artifacts (the latest
  artifact ON OR BEFORE each session; sessions before the first artifact use the earliest one).
  Every session carries a UNIVERSE QUALITY (`universe_provenance`): POINT_IN_TIME,
  BACKDATED_UNIVERSE or UNKNOWN - audit metadata only, it never changes a classification
  (docs/PRIVATE_MARKET_REGIME.md, "Historical universe limitation");
* India VIX and FII/DII from the canonical report of EXACTLY that session (context only).

NO LOOKAHEAD: `until(d)` returns a view that physically ends at `d`. The engine only ever
computes session d's metrics from `data.until(d)`, so a value dated after d cannot reach them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

import numpy as np

from ..db import SourceUnavailable
from ..repository import BENCHMARK_SYMBOL, DeskRepository

LOOKBACK_DAYS = 800          # calendar days of benchmark history loaded (spine + 50-session SMA)

# universe quality (per session; metadata, never a regime input)
POINT_IN_TIME = "POINT_IN_TIME"            # the session's own stored list, or a carried-forward
                                           # list proven unchanged by the next stored list
BACKDATED_UNIVERSE = "BACKDATED_UNIVERSE"  # a LATER list reused for an earlier session
UNKNOWN = "UNKNOWN"                        # provenance cannot be established
UNIVERSE_QUALITIES = (POINT_IN_TIME, BACKDATED_UNIVERSE, UNKNOWN)
BACKDATED_NOTE = ("Historical constituent membership for this session was unavailable; the "
                  "earliest stored universe was used. Prices remain session-bounded.")
# sessions back from d whose membership the classification of d reads: the 10-session advance
# share of d and of d-1 (confirmation rule) and both 5-session volume windows
WINDOW_BACK = 10


@dataclass(frozen=True)
class RegimeData:
    sessions: tuple                      # canonical sessions, oldest first
    symbols: tuple                       # union of constituents over the loaded sessions
    sector: dict                         # symbol -> NSE Industry display sector
    close: np.ndarray                    # [T, N] float, NaN = no usable bar on that session
    volume: np.ndarray                   # [T, N]
    member: np.ndarray                   # [T, N] bool: constituent of the universe on that session
    nifty: np.ndarray                    # [T] NIFTY 50 close, NaN when the benchmark lacks the bar
    universe_session: tuple              # [T] Market Structure session the membership came from
    universe_label: str = "NIFTY 200"
    context: dict = field(default_factory=dict)   # session -> report context (VIX / flows)
    # list session -> {"symbols": frozenset, "source", "source_reference", "retrieved_at"}
    universe_meta: dict = field(default_factory=dict, compare=False, repr=False)
    rvol_memo: dict = field(default_factory=dict, compare=False, repr=False)

    def index_of(self, d: dt.date) -> int | None:
        try:
            return self.sessions.index(d)
        except ValueError:
            return None

    def until(self, d: dt.date) -> "RegimeData":
        """Everything up to and including session `d` - nothing after it."""
        i = self.index_of(d)
        if i is None:
            raise KeyError(f"{d} is not a canonical session in the loaded data")
        n = i + 1
        return replace(self, sessions=self.sessions[:n], close=self.close[:n],
                       volume=self.volume[:n], member=self.member[:n], nifty=self.nifty[:n],
                       universe_session=self.universe_session[:n],
                       context={k: v for k, v in self.context.items() if k <= d},
                       rvol_memo=self.rvol_memo)        # memo keys are (session, symbol): a
        # value computed for session j used only rows <= j, so sharing it cannot leak forward

    @property
    def last(self) -> dt.date:
        return self.sessions[-1]

    # ------------------------------------------------------------------ universe provenance
    def universe_provenance(self, j: int) -> dict:
        """Which constituent list session j used and how far it can be trusted for j.

        POINT_IN_TIME: the list stored for j itself, or the latest earlier list when the next
        stored list is identical (membership proven unchanged across the gap).
        BACKDATED_UNIVERSE: the list is dated AFTER j (j precedes the stored universe history).
        UNKNOWN: no recorded provenance, or a carried-forward list that the next stored list
        contradicts (membership changed in the gap) or that no later list confirms yet.
        Later lists are used only to LABEL quality - never as a classification input."""
        d, src = self.sessions[j], self.universe_session[j]
        meta = self.universe_meta.get(src) or {}
        out = {"session_date": d.isoformat(), "universe_source_date": src.isoformat(),
               "universe_source_reference": meta.get("source_reference"),
               "universe_source": meta.get("source"),
               "universe_retrieved_at": meta.get("retrieved_at")}
        if not meta.get("source_reference"):
            return {**out, "universe_quality": UNKNOWN,
                    "universe_note": "Constituent-list provenance is not recorded."}
        if src > d:
            return {**out, "universe_quality": BACKDATED_UNIVERSE,
                    "universe_note": f"{BACKDATED_NOTE} (list of {src})"}
        if src == d:
            note = f"Constituent list stored for this session ({meta.get('source_reference')})."
            ret = str(meta.get("retrieved_at") or "")[:10]
            if ret and ret > d.isoformat():
                note += f" Retrieved {ret}, after the session (rebuild)."
            return {**out, "universe_quality": POINT_IN_TIME, "universe_note": note}
        later = sorted(k for k in self.universe_meta if k > d)
        if later and self.universe_meta[later[0]].get("symbols") == meta.get("symbols"):
            return {**out, "universe_quality": POINT_IN_TIME,
                    "universe_note": f"No list stored for this session; the {src} list is "
                                     f"carried forward and the next stored list ({later[0]}) "
                                     "is identical."}
        why = (f"the next stored list ({later[0]}) differs - membership changed in the gap"
               if later else "no later list confirms it yet")
        return {**out, "universe_quality": UNKNOWN,
                "universe_note": f"No list stored for this session; the {src} list was carried "
                                 f"forward, but {why}."}

    def window_universe_quality(self, j: int, back: int = WINDOW_BACK) -> dict:
        """Universe quality across every session whose membership classifying j reads."""
        rows = [self.universe_provenance(jj)["universe_quality"]
                for jj in range(max(0, j - back), j + 1)]
        worst = (POINT_IN_TIME if all(q == POINT_IN_TIME for q in rows) else
                 BACKDATED_UNIVERSE if BACKDATED_UNIVERSE in rows else UNKNOWN)
        return {"window_universe_quality": worst, "window_sessions": len(rows),
                "window_non_point_in_time_sessions": sum(1 for q in rows if q != POINT_IN_TIME)}


def _universes(repo: DeskRepository) -> list:
    """`[(session, {symbol: {...}}, universe_block)]` for every Market Structure artifact,
    oldest first."""
    out = []
    for s in repo.market_structure_sessions():
        data, _ = repo.market_structure(s)
        uni = (data or {}).get("universe") or {}
        if uni.get("constituents"):
            out.append((s, uni["constituents"], uni))
    return out


def earliest_point_in_time_session(repo: DeskRepository):
    """The first session with its own stored constituent list - where point-in-time regime
    validation can start. Discovered from the artifacts, never hard-coded."""
    for s, _, uni in _universes(repo):
        if uni.get("source_reference"):
            return s
    return None


def _report_context(repo: DeskRepository, sessions) -> dict:
    """VIX / flows of the canonical report of exactly each session (where one exists)."""
    from ..services.market import report_diagnostics
    try:
        dated = {dt.date.fromisoformat(r["session_date"]) for r in repo.report_rows()
                 if r.get("session_date")}
    except SourceUnavailable:
        return {}
    out = {}
    for d in sorted(dated & set(sessions)):
        rd = report_diagnostics(repo, d)
        if rd.get("status") != "FOUND":
            continue
        out[d] = {"report_id": rd.get("report_id"), "india_vix": rd.get("india_vix"),
                  "vix_status": rd.get("vix_status"), "fii_net_cr": rd.get("fii_net_cr"),
                  "dii_net_cr": rd.get("dii_net_cr"), "fii_status": rd.get("fii_status")}
    return out


def load_regime_data(repo: DeskRepository, end: dt.date, *, constituents: dict | None = None,
                     lookback_days: int = LOOKBACK_DAYS) -> RegimeData:
    """Load everything the classifier needs for sessions up to `end` (inclusive).

    `constituents` overrides the Market Structure membership (tests). Raises
    `SourceUnavailable` when the benchmark or the universe is missing - the caller turns that
    into INSUFFICIENT_DATA, never into a guess.
    """
    from radar.session_alignment import canonical_session_list
    from storage.ohlcv_models import QualityStatus

    bench = repo.benchmark_series(end, days=lookback_days)
    if not bench:
        raise SourceUnavailable("benchmark (^NSEI) not in the OHLCV store")
    sessions = tuple(canonical_session_list(bench, end=end))
    if not sessions:
        raise SourceUnavailable("no canonical session on or before the requested date")
    pos = {d: i for i, d in enumerate(sessions)}

    if constituents is not None:
        universes = [(sessions[0], constituents, {})]     # no provenance -> UNKNOWN
    else:
        universes = _universes(repo)
    if not universes:
        raise SourceUnavailable("no Market Structure artifact holds the NIFTY 200 constituents")

    symbols = sorted({s for _, cons, _ in universes for s in cons})
    col = {s: j for j, s in enumerate(symbols)}
    sector = {}
    for _, cons, _ in universes:                    # later artifacts win (oldest first)
        for s, c in cons.items():
            sector[s] = (c or {}).get("sector") or "Unclassified"

    T, N = len(sessions), len(symbols)
    member = np.zeros((T, N), dtype=bool)
    source = []
    for i, d in enumerate(sessions):
        eligible = [u for u in universes if u[0] <= d]
        u_session, cons, _ = eligible[-1] if eligible else universes[0]
        source.append(u_session)
        for s in cons:
            member[i, col[s]] = True

    close = np.full((T, N), np.nan)
    volume = np.full((T, N), np.nan)
    for r in repo.ohlcv_rows(symbols, sessions[0], end):
        i = pos.get(r.session_date)
        if i is None or r.quality_status != QualityStatus.OK:
            continue                                # non-session placeholder / not final
        if r.close is None or not r.close > 0:
            continue
        close[i, col[r.symbol]] = float(r.close)
        volume[i, col[r.symbol]] = float(r.volume) if r.volume is not None else np.nan

    nifty = np.full(T, np.nan)
    for b in bench:
        i = pos.get(b["date"])
        if i is not None:
            nifty[i] = b["close"]

    return RegimeData(sessions=sessions, symbols=tuple(symbols), sector=sector, close=close,
                      volume=volume, member=member, nifty=nifty,
                      universe_session=tuple(source),
                      context=_report_context(repo, sessions),
                      universe_meta={u: {"symbols": frozenset(cons), "source": meta.get("source"),
                                         "source_reference": meta.get("source_reference"),
                                         "retrieved_at": meta.get("retrieved_at")}
                                     for u, cons, meta in universes})


__all__ = ["RegimeData", "load_regime_data", "earliest_point_in_time_session", "BENCHMARK_SYMBOL",
           "POINT_IN_TIME", "BACKDATED_UNIVERSE", "UNKNOWN", "UNIVERSE_QUALITIES", "BACKDATED_NOTE",
           "WINDOW_BACK"]
