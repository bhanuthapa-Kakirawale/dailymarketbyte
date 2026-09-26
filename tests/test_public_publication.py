"""Packets C-G: the publication boundary wired end to end - POST and PRE storyboards, the
provenance bar, UNDER THE SURFACE, EXCHANGE / IPO WATCH, the hook engine, titles/descriptions,
the publication audit and the upload hard-block - and PRIVATE_ANALYTICS surviving intact."""
import datetime as dt
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

import daily_video.storyboard as sbm
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer, theme
from daily_video.provenance_bar import BAND_BOTTOM, SIZE
from daily_video.public_storyboard import audit_storyboard, scene_audit
from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
from products import public_fixtures as PF
from publication import PublicationBlocked, PublicationGate, require_publication_pass
from publication.language import scan_public_text
from publication.public_hooks import restrict_sheet

SESSION, PREV, NEXT = dt.date(2026, 9, 25), dt.date(2026, 9, 24), dt.date(2026, 9, 28)


# --------------------------------------------------------------------------- synthetic POST inputs
def _obs(source="nse_website", stype="PRIMARY"):
    from core.enums import SourceType
    return NS(source_name=source, source_type=SourceType(stype),
              retrieved_at=dt.datetime(2026, 9, 25, 16, 5, tzinfo=dt.timezone.utc))


class _Report:
    def __init__(self, ai_flows=False):
        self.session_date, self.report_date = SESSION, NEXT
        self.nifty = {"fact_ids": ["f.nifty"]}
        self.sectors = [{"name": "Metal", "fact_id": "f.metal"}, {"name": "IT", "fact_id": "f.it"}]
        self.institutional_flows = {"fact_ids": ["f.fii"]}
        self.global_cues, self.events = [], []
        self._facts = {"f.nifty": [_obs(), _obs("yahoo_finance", "SECONDARY")],
                       "f.metal": [_obs("yahoo_finance", "SECONDARY")],
                       "f.it": [_obs("yahoo_finance", "SECONDARY")],
                       "f.fii": [_obs("gemini", "AI")] if ai_flows else [_obs()]}

    def fact(self, fid):
        obs = self._facts.get(fid)
        return NS(observations=obs) if obs else None


def _pres(pct=0.12, report=None):
    n = 40
    closes = [24000 + 30 * np.sin(i / 3) for i in range(n)]
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame({"Open": opens, "High": [max(o, c) + 20 for o, c in zip(opens, closes)],
                       "Low": [min(o, c) - 20 for o, c in zip(opens, closes)], "Close": closes,
                       "ema20": [24040.0] * n}, index=pd.bdate_range(end=SESSION, periods=n))
    c = float(df["Close"].iloc[-1])
    m = {"pct": pct, "close": c, "chg": c * pct / 100, "open": float(df["Open"].iloc[-1]),
         "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]), "chart_df": df,
         "prev_date": PREV}
    return NS(m=m, sec=[{"name": "Metal", "pct": 1.2}, {"name": "IT", "pct": -0.4}],
              session_date=SESSION, fd=None, report=report or _Report())


def _items(rows):
    return [EditorialItem(title=t, value=f"{v:+.2f}%", numeric=v, positive=v >= 0, rank=i + 1)
            for i, (t, v) in enumerate(rows)]


def _plan(gainers=(("STOCK-005", 8.4),), losers=(("STOCK-006", -3.2),)):
    return ShortsPlan(report_id="SYNTH", session_date=SESSION, scenes=[
        ScenePlan(scene_id="g", scene_type=SceneType.GAINERS, items=_items(gainers)),
        ScenePlan(scene_id="l", scene_type=SceneType.LOSERS, items=_items(losers))])


def _radar(*syms):
    pres = {"status": "OK", "scenes": [{"role": "STORY", "story": {
        "instrument": s, "headline": "Closed above its 20-day high", "price_change_display": "+6.1%"}}
        for s in syms] + [{"role": "CLOSING", "headline": "Signals, not advice"}]}
    result = {"stories": [{"instrument": s, "price_change_pct": 6.1, "technical_context":
                           {"events": ["BREAK_ABOVE_20D_RANGE"]}} for s in syms]}
    return pres, result


def _intel(**kw):
    kw.setdefault("structure_scenario", "CONCENTRATED")
    kw.setdefault("exchange_scenario", "FNO_BAN")
    kw.setdefault("ipo_scenario", None)
    return PF.intelligence(SESSION, PREV, NEXT, ipo_day=SESSION, **kw)


def _post(profile=None, pct=0.12, radar=("STOCK-001", "STOCK-002"), **kw):
    rp, rr = _radar(*radar) if radar else (None, None)
    return sbm.build_storyboard(_plan(), _pres(pct), rp, rr, {}, "Nifty 100", {},
                                profile=profile, intelligence=_intel(**kw))


# --------------------------------------------------------------------------- POST: public
def test_public_post_is_the_default_and_has_no_stock_level_radar():
    sb = _post()
    assert sb.publication_profile == "PUBLIC_UNREGISTERED"
    kinds = [s.kind for s in sb.scenes]
    assert "RADAR_STORY" not in kinds and "RADAR_INTRO" not in kinds and "MOVERS" not in kinds
    text = " ".join(sb.public_text().values())
    for sym in ("STOCK-001", "STOCK-002", "STOCK-005", "STOCK-006"):
        assert sym not in text
    gated = [o for o in sb.omitted if o["section"] == "MARKET RADAR"]
    assert {o["item"] for o in gated} == {"STOCK-001", "STOCK-002"}
    assert sb.publication["block_reasons"]["SECURITY_TECHNICAL_ANALYSIS"] >= 2
    assert "MOVERS" not in sb.post_plan["order"]
    assert "security ranking" in sb.post_plan["reasons"]["MOVERS"]


def test_private_post_keeps_radar_stories_and_movers():
    sb = _post(profile="PRIVATE_ANALYTICS")
    kinds = [s.kind for s in sb.scenes]
    assert kinds.count("RADAR_STORY") == 2 and "RADAR_INTRO" in kinds and "MOVERS" in kinds


def test_under_the_surface_scene_shows_universe_denominator_and_sectors():
    sb = _post()
    st = [s for s in sb.scenes if s.kind == "STRUCTURE"]
    assert st, [s.kind for s in sb.scenes]
    uv = next(s for s in st if s.data["kind"] == "UNUSUAL_VOLUME")
    assert uv.texts["hero_value"] == "18 / 200"
    assert "NIFTY 200" in uv.texts["hero_label"]
    assert uv.data["universe"] == {"label": "NIFTY 200", "denominator_text": "18 / 200",
                                   "coverage_pct": 100.0, "status": "PUBLISHABLE"}
    assert sum(uv.data["rows_n"]) == 18
    assert uv.texts["takeaway"] == "Healthcare accounted for one-third of them."
    assert uv.section == "STRUCTURE" and sb.sections()[-2][1] == "UNDER THE SURFACE"


def test_no_meaningful_structure_means_no_section():
    sb = _post(structure_scenario="QUIET")
    assert all(s.kind != "STRUCTURE" for s in sb.scenes)


def test_exchange_watch_names_only_officially_listed_securities():
    sb = _post()
    ex = next(s for s in sb.scenes if s.kind == "EXCHANGE_WATCH")
    names = [c["name"] for c in ex.texts["cards"]]
    assert names == ["STOCK-011", "STOCK-042"]          # NEW before CONTINUING
    assert ex.texts["cards"][0]["change"] == "Entered the list for this date"
    assert all(c["tag"] == "F&O BAN" for c in ex.texts["cards"])
    named = sb.publication["named_securities"]
    assert set(named) == {"STOCK-011", "STOCK-042"}
    assert all("EXCHANGE_EVENT" in why[0] for why in named.values())


def test_radar_stock_with_an_official_event_appears_only_as_that_event():
    sb = _post(radar=("STOCK-011",))
    assert all(s.kind != "RADAR_STORY" for s in sb.scenes)
    ex = next(s for s in sb.scenes if s.kind == "EXCHANGE_WATCH")
    card = next(c for c in ex.texts["cards"] if c["name"] == "STOCK-011")
    text = " ".join(str(v) for v in card.values()).lower()
    for word in ("radar", "breakout", "20-day", "volume", "high"):
        assert word not in text


def test_every_factual_scene_shows_source_and_data_as_of():
    sb = _post(ipo_scenario="LISTING_DAY")
    for s in scene_audit(sb):
        if s["requires_provenance"]:
            assert s["provenance"].get("source", "").startswith("SOURCE: "), s
            assert "AS OF" in s["provenance"].get("data_as_of", "") or \
                   "DATE" in s["provenance"].get("data_as_of", ""), s


def test_provenance_bar_is_readable_and_inside_the_safe_area():
    sb = _post(ipo_scenario="CLOSES_TODAY")
    comp = Composer(sb)
    for i, spec in enumerate(sb.scenes):
        img, rec, qa = comp.freeze(i)
        assert qa["passed"], (spec.kind, qa["issues"])
        prov = [tb for tb in rec.texts if tb.role == "provenance"]
        if isinstance(spec.texts.get("provenance"), dict):
            assert prov, spec.kind
            assert "provenance" in qa["marks"]
            for tb in prov:
                assert tb.size >= theme.MIN_FONT and tb.size <= SIZE
                assert tb.box[3] <= BAND_BOTTOM + 4 < theme.RESERVED_BOTTOM
                assert tb.box[2] <= theme.RIGHT_RAIL_X
        else:
            assert not prov


def test_provenance_is_visible_for_the_whole_scene_no_flash():
    sb = _post()
    comp = Composer(sb)
    i = next(k for k, s in enumerate(sb.scenes) if s.kind == "STRUCTURE")
    d = sb.scenes[i].duration
    for t in (0.7, d / 2, d - 0.1):
        _, rec, _ = comp.freeze(i, t)
        assert any(tb.role == "provenance" for tb in rec.texts)


def test_public_post_audit_passes_and_is_complete():
    sb = _post(ipo_scenario="CLOSES_TODAY")
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert audit["final"] == "PASS", audit["failed_checks"]
    for key in ("publication_profile", "session_date", "facts_considered", "facts_allowed",
                "facts_blocked", "block_reasons", "named_securities",
                "why_each_named_security_is_allowed", "market_structure", "sources",
                "source_references", "data_as_of", "retrieved_at", "publication_rights_status",
                "ipo", "gemini", "scans", "final"):
        assert key in audit, key
    for scan in ("recommendation_language", "security_specific_technical_analysis",
                 "forecast_language", "english_only", "source_visibility", "universe_visibility"):
        assert audit["scans"][scan]["passed"], scan
    assert audit["ipo"]["gmp_present"] is False and audit["ipo"]["ipo_content_present"]
    assert audit["market_structure"]["universe"] == "NIFTY 200"
    assert audit["market_structure"]["sector_mapping"]["mapping_version"]
    assert "DEMO IPO A LTD" in audit["named_securities"]


def test_public_video_text_is_english_and_passes_content_safety():
    sb = _post(ipo_scenario="BOARD", exchange_scenario="ALL")
    assert scan_publication(sb.public_text()).status is SafetyStatus.SAFE
    scan = scan_public_text(sb.public_text(), PF.universe().companies(),
                            sb.gate.named_securities())
    assert scan.passed, scan.issues


def test_ai_only_flows_never_reach_the_public_post():
    plan = _plan()
    plan.scenes.append(ScenePlan(scene_id="f", scene_type=SceneType.FLOWS, items=[
        EditorialItem(title="FII", value="-Rs 5,000 cr", numeric=-5000.0, positive=False),
        EditorialItem(title="DII", value="+Rs 4,300 cr", numeric=4300.0, positive=True)]))
    sb = sbm.build_storyboard(plan, _pres(report=_Report(ai_flows=True)), None, None, {},
                              "Nifty 100", {}, intelligence=_intel())
    assert all(s.kind != "FLOWS" for s in sb.scenes)
    assert "AI_NOT_A_SOURCE" in sb.publication["block_reasons"]


def test_public_hook_never_offers_a_blocked_stock_fact():
    from hooks import post_market_sheet
    rp, rr = _radar("STOCK-001", "STOCK-002")
    stories = [(sc["story"], rr["stories"][i]) for i, sc in enumerate(rp["scenes"][:2])]
    sheet = post_market_sheet(_plan(), _pres(), stories, {}, ["PULSE", "RADAR"], "Nifty 100")
    assert any(f.kind == "STOCK_MOVE" for f in sheet.facts)
    removed = restrict_sheet(sheet, PublicationGate("PUBLIC_UNREGISTERED"))
    assert removed and not any(f.kind in ("STOCK_MOVE", "RADAR_EVENT", "RADAR_VOLUME")
                               for f in sheet.facts)
    assert not any(b.beat_id.startswith(("RADAR", "TOP_", "VOLUME_SPIKE")) for b in sheet.beats)
    assert "RADAR" not in sheet.sections
    private = post_market_sheet(_plan(), _pres(), stories, {}, ["PULSE", "RADAR"], "Nifty 100")
    assert restrict_sheet(private, PublicationGate("PRIVATE_ANALYTICS")) == []


def test_public_hook_opens_on_market_structure_when_nifty_is_quiet():
    sb = _post(pct=0.12)
    hp = sb.hook_plan
    assert hp["curiosity_line"] in ("Nifty moved just +0.12%. 18 NIFTY 200 stocks saw unusual volume.",
                                    "18 NIFTY 200 stocks saw unusual volume.")
    assert hp["archetype"] == "QUIET_MARKET_HIDDEN_ACTION"
    offered = " ".join(str(c) for c in hp["candidates"])
    assert "unusual" not in offered.lower() or "structure" in offered


def test_gemini_never_sees_blocked_stock_facts():
    seen = []
    rp, rr = _radar("STOCK-001")
    sbm.build_storyboard(_plan(), _pres(), rp, rr, {}, "Nifty 100", {}, hook_ai=True,
                         hook_client=lambda prompt, schema: seen.append(prompt) or None,
                         intelligence=_intel())
    assert seen and "STOCK-001" not in seen[0] and "STOCK-005" not in seen[0]


# --------------------------------------------------------------------------- PRE
def _pre(profile=None, **kw):
    from daily_video.pre_storyboard import build_pre_storyboard
    from presentation.pre_plan import plan_pre_sections
    from presentation.pre_public import apply_publication_profile
    from products.pre_fixtures import AS_OF, PRE_DATE, PREV as PPREV, synthetic_brief
    brief = synthetic_brief("RISK_OFF")
    brief.publication_profile = profile or "PUBLIC_UNREGISTERED"
    brief.public_intelligence = PF.intelligence(PPREV, PPREV - dt.timedelta(days=3), PRE_DATE,
                                                structure_scenario=None,
                                                exchange_scenario=kw.get("exchange", "FNO_BAN"),
                                                ipo_scenario=kw.get("ipo", "CLOSES_TODAY"))
    gate = apply_publication_profile(brief)
    plan = plan_pre_sections(brief)
    return brief, plan, build_pre_storyboard(brief, plan, gate=gate)


def test_public_pre_has_no_stock_watch_and_carries_exchange_and_ipo_watch():
    brief, plan, sb = _pre()
    assert "STOCK_WATCH" not in plan.order
    assert not any(w.category == "STOCK" for w in plan.watch)
    assert "EXCHANGE" in plan.order and "IPO" in plan.order
    assert plan.labels["EXCHANGE"] == "EXCHANGE WATCH" and plan.labels["IPO"] == "IPO WATCH"
    assert "STOCK-A" not in " ".join(sb.public_text().values())
    assert any(o["section"] == "STOCK WATCH" for o in sb.omitted)
    ipo = next(s for s in sb.scenes if s.kind == "IPO_WATCH")
    assert ipo.texts["chip"] == "CLOSES TODAY"
    assert ipo.texts["provenance"]["as_of"] == "DATA AS OF: 5 OCT 2026 · 5:00 PM IST"


def test_private_pre_keeps_stock_watch():
    _, plan, sb = _pre(profile="PRIVATE_ANALYTICS")
    assert "STOCK_WATCH" in plan.order
    assert any(s.kind == "PRE_STOCKS" for s in sb.scenes)


def test_public_pre_withholds_gift_nifty_unless_the_policy_allows_it():
    brief, plan, sb = _pre()
    assert brief.gift is None and not plan.show_gift_nifty
    assert "GIFT" not in " ".join(sb.public_text().values()).upper()
    assert "RIGHTS_RESTRICTED" in sb.publication["block_reasons"]
    _, private_plan, _ = _pre(profile="PRIVATE_ANALYTICS")
    assert private_plan.show_gift_nifty


def test_public_pre_audit_passes_and_overnight_shows_fetched_time():
    brief, plan, sb = _pre()
    audit = audit_storyboard(sb, "PRE", synthetic=True)
    assert audit["final"] == "PASS", audit["failed_checks"]
    ov = next(s for s in sb.scenes if s.kind == "PRE_OVERNIGHT")
    assert "FETCHED:" in ov.texts["provenance"]["as_of"]           # live Asian readings
    comp = Composer(sb)
    for i, spec in enumerate(sb.scenes):
        _, _, qa = comp.freeze(i)
        assert qa["passed"], (spec.kind, qa["issues"])


def test_missing_ipo_subscription_is_omitted_not_inferred():
    _, plan, sb = _pre(ipo="MISSING_SUBSCRIPTION")
    ipo = next(s for s in sb.scenes if s.kind == "IPO_WATCH")
    labels = [r["label"] for r in ipo.texts["rows"]]
    assert "TOTAL BIDS" not in labels


# --------------------------------------------------------------------------- legacy upload path
def test_legacy_public_metadata_has_no_stock_names_or_rankings():
    from presentation.legacy_public import public_metadata
    plan = ShortsPlan(report_id="X", session_date=SESSION, scenes=[
        ScenePlan(scene_id="s", scene_type=SceneType.SECTORS, items=_items((("Metal", 1.2),)))])
    meta = public_metadata({"recap_date": SESSION, "close": 24012.3, "pct": 0.42}, plan,
                           {"recap_str": "Fri, 25 Sep 2026", "today_short": "Mon 28 Sep"})
    text = meta["title"] + meta["description"] + " ".join(meta["tags"])
    assert "gainer" not in text.lower() and "loser" not in text.lower()
    assert "What Changed in India's Market" in meta["title"]
    from publication.disclaimer import DESCRIPTION, strip_registered
    assert DESCRIPTION in meta["description"]
    assert scan_public_text({"t": meta["title"], "d": strip_registered(meta["description"])}).passed


def test_upload_is_impossible_without_a_pass_audit(tmp_path, monkeypatch):
    import upload
    touched = []
    monkeypatch.setattr(upload, "get_service", lambda: touched.append(1))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(TypeError):
        upload.upload(str(video), {"title": "t", "description": "d"})       # audit is required
    for bad in (None, {"final": "BLOCK", "failed_checks": ["x"]},
                {"final": "PASS", "publication_profile": "PRIVATE_ANALYTICS"},
                {"final": "PASS", "publication_profile": "PUBLIC_UNREGISTERED",
                 "artifact": {"sha256": "0" * 64}}):
        with pytest.raises(PublicationBlocked):
            upload.upload(str(video), {"title": "t", "description": "d"}, bad)
    assert touched == [], "no YouTube client may be created before the audit passes"


def test_blocked_public_audit_blocks_the_scheduled_post_upload(monkeypatch):
    import main
    audit = {"final": "BLOCK", "failed_checks": ["recommendation_language"]}
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, __file__)
    import inspect
    src = inspect.getsource(main.run)
    assert "publication_ok" in src and "publish(out, meta, pres.session_date, audit_path)" in src


# --------------------------------------------------------------------------- legacy editorial gate
def _legacy_report(gainer_pct=9.0):
    from conftest_intelligence import movers, session_report, trading_sessions
    rows = movers(["STOCK-A", "STOCK-B", "STOCK-C"])
    rows[0] = dict(rows[0], pct=gainer_pct, change_pct=gainer_pct)
    return session_report(trading_sessions(1)[0], pct=0.2, fii=-3810.0, dii=2100.0,
                          gainers=rows, losers=[],
                          events=[{"tag": "RESULTS", "text": "Large-cap quarterly results today",
                                   "source": "GEMINI_SEARCH", "origin": "GEMINI_SEARCH"},
                                  {"tag": "F&O", "text": "Nifty weekly F&O expiry",
                                   "source": "expiry_calendar_rule", "origin": "RULE_FNO_EXPIRY"}])


def test_legacy_plan_is_public_by_default_and_names_no_stock():
    from conftest import NOW
    from editorial import plan_short
    plan = plan_short(_legacy_report(), None, now=NOW)
    assert plan.publication_profile == "PUBLIC_UNREGISTERED"
    kinds = {s.scene_type.value for s in plan.scenes}
    assert not kinds & {"GAINERS", "LOSERS", "MOVERS"}
    assert plan.hook.metadata["candidate_id"] != "hook-mover"
    assert any(o.get("reason") == "publication_profile" and o["scene"] == "movers"
               for o in plan.omitted)
    events = plan.scene(SceneType.EVENTS)
    assert events is not None and [i.title for i in events.items] == ["Nifty weekly F&O expiry"]
    text = " ".join(plan.public_text().values())
    assert "STOCK-A" not in text


def test_legacy_plan_private_keeps_movers_and_the_mover_hook():
    from conftest import NOW
    from editorial import plan_short
    plan = plan_short(_legacy_report(), None, now=NOW, profile="PRIVATE_ANALYTICS")
    assert plan.scene(SceneType.GAINERS) is not None
