"""DeskService: the one facade the routes call. Combines the repository, the replay cache and
the per-page services; resolves which session a page shows (never silently mixing sessions)."""
from __future__ import annotations

import datetime as dt

from ..cache import DeskCache
from ..db import SourceUnavailable
from ..regime.store import RECENT_SESSIONS, RegimeStore
from ..replay import get_replay
from ..repository import DeskRepository
from ..settings import DeskSettings
from . import attention as at
from . import candidates as cs
from . import history as hs
from . import institutional as inst
from . import market as mk
from . import quality as qa
from . import stock as st
from .freshness import compute_freshness, session_context


class DeskService:
    def __init__(self, settings: DeskSettings):
        self.settings = settings
        self.repo = DeskRepository(settings.out_dir)
        self.cache = DeskCache(settings.cache_dir, settings.out_dir)
        self.regimes = RegimeStore(self.repo, settings.regime_dir)

    # ------------------------------------------------------------------ session resolution
    def resolve_session(self, requested: str | None) -> tuple:
        """`(session, notice)`. Defaults to the latest COMPLETE Radar session. A requested date
        that has no Radar run is refused with a notice (never replaced by a nearby date)."""
        try:
            sessions = self.repo.radar_sessions()
        except SourceUnavailable as exc:
            return None, f"Radar candidate history unavailable: {exc}"
        if not sessions:
            return None, "No completed Radar session recorded yet."
        if requested:
            try:
                d = dt.date.fromisoformat(requested)
            except ValueError:
                return sessions[-1], f"Invalid date {requested!r}; showing the latest session."
            if d in sessions:
                return d, ""
            return None, f"No completed Radar run is recorded for {d}."
        return sessions[-1], ""

    def sessions(self) -> list:
        try:
            return self.repo.radar_sessions()
        except SourceUnavailable:
            return []

    def freshness(self):
        return compute_freshness(self.repo)

    def replay(self, session: dt.date) -> dict:
        return get_replay(self.repo, self.cache, session)

    # ------------------------------------------------------------------ market regime (context)
    def regime(self, session: dt.date, n: int = RECENT_SESSIONS) -> dict:
        """The market regime of `session` + the recent history before it. CONTEXT ONLY: nothing
        here reads or changes a Radar candidate, its order or the attention set."""
        from config import now_ist
        history = self.regimes.snapshots(session, n=n, generated_at=now_ist().isoformat())
        on_session = history and history[-1].session_date == session.isoformat()
        snap = history[-1] if on_session else self.regimes.snapshot(session)
        latest = self.freshness().latest_completed
        return {"snapshot": snap, "history": list(reversed(history)),
                "stale": bool(latest and dt.date.fromisoformat(snap.session_date) < latest),
                "latest_completed": latest}

    # ------------------------------------------------------------------ pages
    def radar(self, session: dt.date) -> dict:
        replay = self.replay(session)
        spine = cs.session_spine(self.repo, session)
        official = cs.official_index(self.repo, session)
        views = cs.build_candidate_views(self.repo, session, replay=replay, spine=spine,
                                         official=official)
        return {"views": views, "replay": replay, "spine": spine, "official": official,
                "context": session_context(self.repo, session),
                "radar_order": at.radar_default_order(views)}

    def story_order(self, session) -> list:
        """The editorial selector's own story order (daily Radar artifact), else none."""
        art, _ = self.repo.radar_artifact(session)
        return [s.get("instrument") for s in (art or {}).get("stories") or []]

    def dashboard(self, session: dt.date) -> dict:
        r = self.radar(session)
        report = mk.report_diagnostics(self.repo, session)
        ms = mk.market_structure(self.repo, session)
        sectors = mk.sector_table(ms, r["views"])
        left = cs.left_radar(self.repo, session, r["spine"])
        intel, _ = self.repo.intelligence(session)
        insights = [i for i in (intel or {}).get("insights") or [] if i.get("statement")]
        changes = cs.what_changed(r["views"], left)
        attention = at.attention_set(r["views"], self.story_order(session))
        firsts = [a["first"] for a in self.repo.appearance_counts().values() if a["first"]]
        history_start = min(firsts) if firsts else None       # earliest recorded candidate row
        return {**r, "report": report, "ms": ms, "sectors": sectors,
                "regime": mk.regime_diagnostics(report, ms, sectors),
                "market_regime": self.regime(session, n=1)["snapshot"],
                "changes": changes, "insights": insights, "attention": attention,
                "change_summary": at.change_summary(r["views"], changes),
                "change_items": at.change_items(attention, r["views"], history_start),
                "history_start": history_start,
                # Institutional Flow Intelligence V1 - context only; never read by
                # attention/candidates/regime, so it cannot change the attention set, Radar
                # order or the regime classification.
                "institutional": inst.dashboard_section(self.repo, session)}

    def sectors(self, session: dt.date) -> dict:
        d = self.dashboard(session)
        return d

    def institutional(self, session: dt.date) -> dict:
        """The dedicated Institutional Intelligence page."""
        return {"nse": inst.nse_section(self.repo, session),
               "cdsl": inst.cdsl_section(self.repo, session),
               "nsdl": inst.nsdl_section(self.repo, session)}

    def stock(self, symbol: str, session: dt.date) -> dict:
        symbol = symbol.upper()
        r = self.radar(session)
        view = next((v for v in r["views"] if v["symbol"] == symbol), None)
        detail = st.stock_detail(self.repo, symbol, session, replay=r["replay"], spine=r["spine"],
                                 candidate_view=view, official=r["official"])
        ms = mk.market_structure(self.repo, session)
        sector_rows = mk.sector_table(ms, r["views"])
        detail["sector_row"] = next((s for s in sector_rows if s["sector"] == detail["sector"]), None)
        detail["context"] = r["context"]
        detail["report"] = mk.report_diagnostics(self.repo, session)
        detail["market_regime"] = self.regime(session, n=1)["snapshot"]   # context only
        # Sector-level FPI context only - never a claim about this stock's own FPI activity.
        detail["fpi_sector_context"] = inst.sector_context(self.repo, session, detail["sector"])
        return detail

    def chart(self, symbol: str, session: dt.date) -> dict:
        spine = cs.session_spine(self.repo, session)
        series = st.aligned_series(self.repo, symbol.upper(), session, spine)
        payload = st.chart_payload(series)
        tech = ((self.replay(session).get("symbols") or {}).get(symbol.upper()) or {}).get("technical")
        payload["levels"] = {k: (tech or {}).get(k) for k in
                             ("prior_20_high", "prior_20_low", "prior_50_high", "prior_50_low")}
        payload["symbol"], payload["session"] = symbol.upper(), session.isoformat()
        return payload

    def history(self, f: hs.HistoryFilter) -> dict:
        sessions = self.sessions()
        end = f.end or (sessions[-1] if sessions else dt.date.today())
        return hs.explore(self.repo, f, cs.session_spine(self.repo, end))

    def quality(self, session: dt.date | None) -> dict:
        replay = self.replay(session) if session else None
        out = qa.data_quality(self.repo, session, replay, self.freshness())
        out["regime"] = qa.regime_quality(self, session)
        out["institutional"] = inst.quality_rows(self.repo, session)
        return out

    def universe_symbols(self, session: dt.date) -> dict:
        u, _ = self.repo.universe(session)
        return u

    def packets(self, session: dt.date) -> list:
        from config import now_ist

        from ..packet import build_packet
        d = self.dashboard(session)
        _, radar_path = self.repo.radar_artifact(session)
        ms_path = self.repo.market_structure_path(session)
        sources = [f"radar_candidate_history.db:{session}"]
        sources += [p.replace("\\", "/").split("/output/")[-1] for p in (radar_path, ms_path) if p]
        if d["report"].get("report_id"):
            sources.append(f"report:{d['report']['report_id']}")
        market_ctx = {**d["regime"], "nifty_change_pct": (d["report"].get("nifty") or {}).get("change_pct"),
                      "india_vix": d["report"].get("india_vix")}
        by_sector = {s["sector"]: {k: v for k, v in s.items() if k != "radar_symbols"}
                     for s in d["sectors"]}
        regime = d["market_regime"]
        market_regime = {**regime.summary(), "reason_code": regime.reason_code}
        now = now_ist()
        return [build_packet(v, market_context=market_ctx, sector_row=by_sector.get(v["sector"]),
                             source_artifacts=sources, generated_at=now,
                             market_regime=market_regime) for v in d["views"]]


__all__ = ["DeskService"]
