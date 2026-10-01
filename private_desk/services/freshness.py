"""Session / freshness header: what each source covers, against the latest completed session.

The "latest completed session" is the canonical calendar's answer (`operations.sessions.
latest_final_session`), never a provider's latest row. A source whose latest session is older is
STALE; one with nothing is MISSING. The desk's working session never silently mixes sources: a
panel whose source lacks the desk session shows it as unavailable (see `session_context`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..db import SourceUnavailable
from ..repository import BENCHMARK_SYMBOL, DeskRepository

FRESH, STALE, MISSING, AHEAD = "FRESH", "STALE", "MISSING", "AHEAD"


@dataclass
class SourceStatus:
    key: str
    label: str
    session: dt.date | None
    as_of: str | None = None          # timestamp text (retrieved / generated), when known
    status: str = MISSING
    detail: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label,
                "session": self.session.isoformat() if self.session else None,
                "as_of": self.as_of, "status": self.status, "detail": self.detail}


@dataclass
class Freshness:
    now: dt.datetime
    latest_completed: dt.date | None
    sources: list = field(default_factory=list)

    @property
    def by_key(self) -> dict:
        return {s.key: s for s in self.sources}

    @property
    def warnings(self) -> list:
        return [s for s in self.sources if s.status != FRESH]

    @property
    def ok(self) -> bool:
        return not self.warnings and self.latest_completed is not None


def _judge(session: dt.date | None, latest: dt.date | None) -> str:
    if session is None:
        return MISSING
    if latest is None:
        return STALE
    if session < latest:
        return STALE
    if session > latest:
        return AHEAD
    return FRESH


def compute_freshness(repo: DeskRepository, now: dt.datetime | None = None) -> Freshness:
    from config import now_ist
    from operations.sessions import latest_final_session
    now = now or now_ist()
    latest = latest_final_session(now)
    f = Freshness(now=now, latest_completed=latest)

    def add(key, label, session, as_of=None, detail=""):
        f.sources.append(SourceStatus(key, label, session, as_of, _judge(session, latest), detail))

    # Radar: the COMPLETE candidate-history marker is authoritative; the artifact gives the time
    try:
        markers = [m for m in repo.radar_run_markers() if m["status"] == "COMPLETE"]
        last = markers[-1] if markers else None
        session = dt.date.fromisoformat(last["session_date"]) if last else None
        art, _ = repo.radar_artifact(session) if session else (None, None)
        detail = (f"pipeline {art.get('pipeline_status')}" if art
                  else "no daily Radar artifact for this session (candidate history only)")
        add("radar", "Radar", session, (art or {}).get("generated_at") or
            (last or {}).get("processed_at"), detail)
    except SourceUnavailable as exc:
        add("radar", "Radar", None, detail=str(exc))

    try:
        st = repo.ohlcv_status()
        session = st["latest_by_symbol"].get(BENCHMARK_SYMBOL)
        add("market_data", "Market data (OHLCV)", session, st["max_retrieved_at"],
            "benchmark NIFTY 50 bar in the OHLCV store")
    except SourceUnavailable as exc:
        add("market_data", "Market data (OHLCV)", None, detail=str(exc))

    ms = repo.market_structure_sessions()
    ms_as_of = None
    if ms:
        data, _ = repo.market_structure(ms[-1])
        ms_as_of = (((data or {}).get("snapshot") or {}).get("universe_source") or {}).get("retrieved_at")
    add("market_structure", "Market Structure", ms[-1] if ms else None, ms_as_of,
        "NIFTY 200 breadth / unusual volume / range events")

    try:
        rows = repo.report_rows()
        last = rows[-1] if rows else None
        add("report", "Canonical report", dt.date.fromisoformat(last["session_date"])
            if last and last["session_date"] else None, (last or {}).get("generated_at"),
            (last or {}).get("report_id") or "")
    except SourceUnavailable as exc:
        add("report", "Canonical report", None, detail=str(exc))

    off = repo.official_sessions()
    session = off[-1] if off else None
    as_of = None
    if session:
        manifest = repo.official_manifest(session) or {}
        times = [e.get("retrieved_at") for e in (manifest.get("snapshots") or {}).values()
                 if e.get("retrieved_at")]
        as_of = max(times) if times else manifest.get("updated_at")
    add("official", "Official events", session, as_of, "NSE F&O ban / ASM / GSM / IPO lists")
    return f


def session_context(repo: DeskRepository, session: dt.date) -> dict:
    """Which sources exist for EXACTLY `session` - drives the per-page alignment banner."""
    art, _ = repo.radar_artifact(session)
    _, _, report_status = repo.report(session)
    return {
        "radar_artifact": art is not None,
        "radar_pipeline_status": (art or {}).get("pipeline_status"),
        "market_structure": repo.market_structure_path(session) is not None,
        "report": report_status == "FOUND",
        "report_status": report_status,
        "official": session in repo.official_sessions(),
    }


__all__ = ["compute_freshness", "session_context", "Freshness", "SourceStatus", "FRESH", "STALE",
          "MISSING", "AHEAD"]
