"""Public intelligence V1 - final review corrections: Nifty de-duplication, net-flow and
tracked-sector wording, source roles, the displayed-claim audit, optional-section omission
codes, the conservative rights default and the silent V2 MP4."""
import datetime as dt
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

import daily_video.storyboard as sbm
from daily_video.public_storyboard import audit_storyboard
from products import public_fixtures as PF
from publication import PublicationBlocked, require_publication_pass
from publication.audit import content_checks_passed
from publication.claims import check_claims, claim, numbers
from publication.scene_claims import storyboard_scene_texts
from test_public_publication import NEXT, PREV, SESSION, _Report, _intel, _plan, _post, _pre


def _pres_break(pct=-1.64, reversal=False):
    """A Nifty session that closes BELOW its prior 20-day low (a new structural event), near
    the day's low - or, with `reversal`, near the day's HIGH (an intraday reversal)."""
    n = 40
    closes = [24000 + 30 * np.sin(i / 3) for i in range(n)]
    closes[-1] = min(closes[:-1]) - 150
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 20 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 20 for o, c in zip(opens, closes)]
    if reversal:
        highs[-1], lows[-1] = closes[-1] + 5, closes[-1] - 300
    else:
        highs[-1], lows[-1] = opens[-1] + 20, closes[-1] - 5
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                       "ema20": [24040.0] * n}, index=pd.bdate_range(end=SESSION, periods=n))
    c = float(closes[-1])
    m = {"pct": pct, "close": c, "chg": c * pct / 100, "open": float(opens[-1]),
         "high": float(highs[-1]), "low": float(lows[-1]), "chart_df": df, "prev_date": PREV}
    return NS(m=m, sec=[{"name": "Metal", "pct": -0.4}, {"name": "IT", "pct": -1.2}],
              session_date=SESSION, fd=None, report=_Report())


def _sb(pres, **kw):
    return sbm.build_storyboard(_plan(), pres, None, None, {}, "Nifty 100", {},
                                intelligence=_intel(**kw))


# --------------------------------------------------------------------------- 1. de-duplication
def test_hook_plus_chart_suppresses_the_redundant_market_pulse():
    sb = _sb(_pres_break())
    kinds = [s.kind for s in sb.scenes]
    assert "nifty.move" in sb.hook_plan["fact_ids"]
    assert kinds[:3] == ["DYNAMIC_HOOK", "NIFTY", "SECTORS"] and "PULSE" not in kinds
    assert "PULSE" not in sb.post_plan["order"]
    assert sb.post_plan["reasons"]["PULSE"].startswith("omitted: editorial de-duplication")
    assert "the close" not in sb.hook_plan["summary_line"]      # never promises a dropped scene
    assert any(o["section"] == "MARKET PULSE" for o in sb.omitted)


def test_market_pulse_kept_when_it_adds_a_distinct_fact():
    sb = _sb(_pres_break(reversal=True))           # down day that closed near the day's HIGH
    assert "PULSE" in [s.kind for s in sb.scenes]
    assert sbm.pulse_adds_distinct_fact({"positive": False,
                                         "support": {"location": "HIGH"}})


def test_market_pulse_kept_when_there_is_no_chart_scene():
    sb = _post(pct=-1.64)                           # no structural event -> no NIFTY chart
    kinds = [s.kind for s in sb.scenes]
    assert "NIFTY" not in kinds and "PULSE" in kinds


# --------------------------------------------------------------------------- 2-3. wording
def test_flows_use_net_seller_buyer_wording_and_stay_provisional():
    from editorial.models import EditorialItem, ScenePlan, SceneType
    plan = _plan()
    plan.scenes.append(ScenePlan(scene_id="f", scene_type=SceneType.FLOWS, items=[
        EditorialItem(title="FII", value="-Rs 5,027 cr", numeric=-5027.0, positive=False),
        EditorialItem(title="DII", value="+Rs 4,301 cr", numeric=4301.0, positive=True)]))
    sb = sbm.build_storyboard(plan, _pres_break(), None, None, {}, "Nifty 100", {},
                              intelligence=_intel())
    f = next(s for s in sb.scenes if s.kind == "FLOWS")
    assert f.headline == "FIIs were net sellers; DIIs were net buyers"
    assert "provisional" in f.subline.lower()
    text = " ".join(sb.public_text().values())
    assert "FIIs sold" not in text and "DIIs bought" not in text


def test_sector_counts_say_tracked():
    sb = _sb(_pres_break())
    s = next(s for s in sb.scenes if s.kind == "SECTORS")
    assert s.headline == "All 2 tracked sector indices fell"
    assert s.subline == "0 of 2 tracked indices closed higher"
    # the sector board is laid out for a ONE-line headline (its cards sit at a fixed y)
    from daily_video import theme
    from daily_video.typography import wrap
    from video import fit
    for n in (3, 12):
        t = f"All {n} tracked sector indices fell"
        f = fit(t, theme.CONTENT_W * 2, theme.T_HEADLINE, min_size=theme.MIN_FONT + 10)
        assert len(wrap(t, f, theme.CONTENT_W)) == 1


# --------------------------------------------------------------------------- 4-5. roles / share
def test_source_roles_are_separated_and_single_source_scenes_unchanged():
    sb = _post()
    st = next(s for s in sb.scenes if s.kind == "STRUCTURE")
    assert st.texts["provenance"]["source"] == \
        "PRICES: YAHOO FINANCE EOD · UNIVERSE & SECTORS: NSE"
    assert st.texts["provenance"]["as_of"].startswith("DATA AS OF: ")
    ex = next(s for s in sb.scenes if s.kind == "EXCHANGE_WATCH")
    assert ex.texts["provenance"]["source"] == "SOURCE: NSE"


def test_breadth_share_is_exact_and_secondary():
    from market_structure.editorial import exact_share_pct
    assert exact_share_pct(179, 200) == "89.5%" and exact_share_pct(1, 3) == ""
    sb = _post(structure_scenario="DIVERGENCE", pct=0.42)
    b = next(s for s in sb.scenes if s.kind == "STRUCTURE" and s.data["kind"] == "BREADTH")
    assert b.texts["hero_value"] == "130 / 200"                   # the raw count stays primary
    assert b.texts["hero_label"].endswith("· 65%")


# --------------------------------------------------------------------------- 6. displayed claims
def test_every_displayed_number_resolves_to_a_fact_or_derivation():
    sb = _post(ipo_scenario="CLOSES_TODAY")
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert audit["scans"]["displayed_claims"]["passed"], audit["scans"]["displayed_claims"]
    claims = audit["displayed_claims"]
    assert claims and all(c["fact_ids"] or c["derivation"] for c in claims)
    for key in ("claim_id", "scene_id", "visible_text", "fact_ids", "metric", "value",
                "source_name", "source_reference", "source_role", "data_as_of", "retrieved_at",
                "publication_rights_status", "publication_decision"):
        assert all(key in c for c in claims), key
    texts = {c["visible_text"] for c in claims}
    assert "18 / 200" in texts and any(t == "6" for t in texts)       # count + sector row


def test_an_unresolved_displayed_number_blocks_the_audit():
    sb = _post()
    st = next(s for s in sb.scenes if s.kind == "STRUCTURE")
    st.texts["takeaway"] = "Healthcare accounted for 7 of them."      # not what was counted
    sb.claim_texts = storyboard_scene_texts(sb)
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert "displayed_claims" in audit["failed_checks"] and audit["final"] == "BLOCK"


def test_claim_without_fact_or_derivation_is_unresolved():
    c = claim("s", "Nifty 24,000", "x")
    assert any("resolves to no fact" in i for i in check_claims({"s": {"a": "Nifty 24,000"}}, [c]))
    assert numbers("NIFTY 200 · 18 / 200 stocks, 20-day low 23,116.1") == {"18", "200", "23116.1"}


# --------------------------------------------------------------------------- 7. omissions
def test_exchange_and_ipo_omissions_distinguish_nothing_from_failure():
    none = _post(exchange_scenario=None, ipo_scenario=None)
    secs = none.public_audit["omitted_sections"]
    assert secs["EXCHANGE_WATCH"]["code"] == "SOURCE_UNAVAILABLE"
    assert secs["IPO_WATCH"]["code"] == "SOURCE_UNAVAILABLE"
    intel = _intel(exchange_scenario=None, ipo_scenario=None)
    intel.exchange_status, intel.ipo_status = "FETCHED", "FETCHED"      # read, nothing today
    sb = sbm.build_storyboard(_plan(), _pres_break(), None, None, {}, "Nifty 100", {},
                              intelligence=intel)
    secs = sb.public_audit["omitted_sections"]
    assert secs["EXCHANGE_WATCH"]["code"] == "NO_ELIGIBLE_EVENT"
    assert secs["IPO_WATCH"]["code"] == "NO_ELIGIBLE_IPO_EVENT"
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert audit["optional_sections"]["EXCHANGE_WATCH"]["code"] == "NO_ELIGIBLE_EVENT"


def test_market_structure_omission_codes():
    assert _post(structure_scenario="QUIET").public_audit["omitted_sections"][
        "MARKET_STRUCTURE"]["code"] == "NO_MEANINGFUL_OBSERVATION"
    assert _post(structure_scenario=None).public_audit["omitted_sections"][
        "MARKET_STRUCTURE"]["code"] == "SOURCE_UNAVAILABLE"


# --------------------------------------------------------------------------- 8. rights
def test_review_required_is_blocked_from_production_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)
    sb = _post()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    audit = audit_storyboard(sb, "POST_UNIFIED", video_path=str(video))
    assert audit["final"] == "BLOCK" and audit["failed_checks"] == ["publication_rights"]
    assert content_checks_passed(audit)
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, str(video))


def test_approved_sources_remain_publishable(tmp_path):
    from publication import PublicationGate, build_publication_audit
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    c = claim("s", "18 / 200", "count", ["market_structure:x"], ["daily_byte_market_structure"],
              derivation="count")
    audit = build_publication_audit(gate=PublicationGate(), product="T", session_date=SESSION,
                                    public_text={"s": "18 / 200"}, scenes=[], claims=[c],
                                    scene_texts={"s": {"a": "18 / 200"}}, video_path=str(video))
    assert audit["final"] == "PASS" and audit["displayed_claims"][0]["publication_decision"] == \
        "PUBLISHABLE"
    require_publication_pass(audit, str(video))


def test_review_and_internal_renders_stay_complete_under_block(monkeypatch):
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)
    kinds = [s.kind for s in _post().scenes]
    assert {"SECTORS", "STRUCTURE", "EXCHANGE_WATCH"} <= set(kinds)      # nothing emptied
    private = _post(profile="PRIVATE_ANALYTICS")
    assert "RADAR_STORY" in [s.kind for s in private.scenes]
    brief, plan, sb = _pre()
    assert "SETUP" in plan.order and "EXCHANGE" in plan.order


def test_scheduled_post_upload_is_refused_under_the_default(monkeypatch):
    import inspect

    import main
    src = inspect.getsource(main.run)
    assert 'audit["final"] != "PASS"' in src and "content_checks_passed" in src


# --------------------------------------------------------------------------- 10. audio
def test_v2_mp4_has_no_audio_unless_the_audio_phase_is_enabled(monkeypatch, tmp_path):
    import daily_video.composer as comp
    assert comp.AUDIO_ENABLED is False
    captured = {}

    class _P:
        def __init__(self, cmd, stdin=None):
            captured["cmd"] = cmd
            self.stdin = NS(write=lambda b: None, close=lambda: None)

        def wait(self):
            return 0

    monkeypatch.setattr(comp.subprocess, "Popen", _P)
    sb = _post()
    sb.scenes = sb.scenes[-1:]
    comp.Composer(sb).render(str(tmp_path / "x.mp4"), fps=1)
    cmd = captured["cmd"]
    assert "-an" in cmd and cmd.count("-i") == 1          # one input: the raw video pipe
    audit = audit_storyboard(_post(), "POST_UNIFIED")
    assert audit["audio"]["audio_stream"] is False
