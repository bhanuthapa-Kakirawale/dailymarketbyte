"""RegimeData: every input the classifier reads, session-aligned, loaded read-only ONCE.

Sources (all through `DeskRepository`, so read-only by construction):

* the NIFTY 50 benchmark (^NSEI) from the OHLCV store -> the canonical session spine
  (`radar.session_alignment.canonical_session_list`: benchmark bars UNION NSE's published
  sessions, so a provider holiday placeholder never becomes a session and Yahoo's missing
  2026-09-22 is still a session);
* per-constituent OHLCV (quality OK only) placed onto that spine - a row dated on a non-session
  is dropped, a session the stock has no row for stays NaN (never filled);
* NIFTY 200 membership + NSE Industry sector from the Market Structure artifacts (the latest
  artifact ON OR BEFORE each session; sessions before the first artifact use the earliest one and
  are flagged `membership_backdated` - see docs/PRIVATE_MARKET_REGIME.md, known limitations);
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


def _universes(repo: DeskRepository) -> list:
    """`[(session, {symbol: {...}})]` for every Market Structure artifact, oldest first."""
    out = []
    for s in repo.market_structure_sessions():
        data, _ = repo.market_structure(s)
        cons = ((data or {}).get("universe") or {}).get("constituents")
        if cons:
            out.append((s, cons))
    return out


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
        universes = [(sessions[0], constituents)]
    else:
        universes = _universes(repo)
    if not universes:
        raise SourceUnavailable("no Market Structure artifact holds the NIFTY 200 constituents")

    symbols = sorted({s for _, cons in universes for s in cons})
    col = {s: j for j, s in enumerate(symbols)}
    sector = {}
    for _, cons in universes:                       # later artifacts win (oldest first)
        for s, c in cons.items():
            sector[s] = (c or {}).get("sector") or "Unclassified"

    T, N = len(sessions), len(symbols)
    member = np.zeros((T, N), dtype=bool)
    source = []
    for i, d in enumerate(sessions):
        eligible = [u for u in universes if u[0] <= d]
        u_session, cons = eligible[-1] if eligible else universes[0]
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
                      context=_report_context(repo, sessions))


__all__ = ["RegimeData", "load_regime_data", "BENCHMARK_SYMBOL"]
