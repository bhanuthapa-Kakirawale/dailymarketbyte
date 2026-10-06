"""Institutional Flow Intelligence V1 - PRE/POST integration: POST materiality-gated FLOWS,
PRE's CDSL/NSDL-first FLOWS chooser, provenance, claims and rights, all offline.
"""
import dataclasses
import datetime as dt

import pytest

from presentation.pre_plan import plan_pre_sections
from products.pre_fixtures import synthetic_brief

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 10, 5, 7, 40, tzinfo=IST)


def _storyboard(brief):
    from daily_video.pre_storyboard import build_pre_storyboard
    plan = plan_pre_sections(brief)
    return build_pre_storyboard(brief, plan), plan


def _snapshot_dict(source, report_key, facts, bootstrap=False, report_date=None,
                   represented_period=None):
    from institutional_flows.models import (CDSL, DEPOSITORY_FORTNIGHTLY, DEPOSITORY_REPORTED,
                                            EXCHANGE_PROVISIONAL, NSDL, NSE, RepresentedPeriod,
                                            SCHEMA_VERSION, SUCCESS, InstitutionalFlowSnapshot)
    cls = {NSE: EXCHANGE_PROVISIONAL, CDSL: DEPOSITORY_REPORTED, NSDL: DEPOSITORY_FORTNIGHTLY}[source]
    snap = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=source, source_class=cls,
        report_type="x", report_key=report_key, report_date=report_date or report_key,
        data_as_of=report_date or report_key,
        represented_period=represented_period or RepresentedPeriod(),
        retrieved_at=NOW.isoformat(), first_retrieved_at=NOW.isoformat(), status=SUCCESS,
        facts=facts, bootstrap=bootstrap)
    snap.seal()
    return snap.to_dict()


def _cdsl_snapshot(report_date, bootstrap=False, stock_ex=-1000.0, primary=50.0):
    from institutional_flows.models import RepresentedPeriod
    facts = [
        {"category": "equity", "route": "stock_exchange", "metric": "net_investment",
         "value": stock_ex, "unit": "INR_CRORE"},
        {"category": "equity", "route": "primary_market_others", "metric": "net_investment",
         "value": primary, "unit": "INR_CRORE"},
    ]
    return _snapshot_dict("CDSL", report_date.isoformat(), facts, bootstrap=bootstrap,
                          report_date=report_date.isoformat(),
                          represented_period=RepresentedPeriod(
                              basis="UNKNOWN", source_note="compiled ... previous trading day(s)"))


def _nsdl_snapshot(fortnight_end, bootstrap=False):
    from institutional_flows.models import RepresentedPeriod
    facts = [
        {"sector": "Healthcare", "metric": "net_investment", "period": "current",
         "category": "equity", "value": 2000.0, "unit": "INR_CRORE"},
        {"sector": "Healthcare", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": 500.0, "unit": "INR_CRORE"},
        {"sector": "Financial Services", "metric": "net_investment", "period": "current",
         "category": "equity", "value": -2500.0, "unit": "INR_CRORE"},
        {"sector": "Financial Services", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": -300.0, "unit": "INR_CRORE"},
    ]
    return _snapshot_dict("NSDL", fortnight_end.isoformat(), facts, bootstrap=bootstrap,
                          report_date=fortnight_end.isoformat(),
                          represented_period=RepresentedPeriod(
                              basis="SOURCE_STATED",
                              source_note=f"Net Investment September 01-15, {fortnight_end.year}"))


def _brief_with(institutional=None, institutional_nse_context=None, flows=None):
    brief = synthetic_brief("RISK_OFF")
    changes = {}
    if institutional is not None:
        changes["institutional"] = institutional
    if institutional_nse_context is not None:
        changes["institutional_nse_context"] = institutional_nse_context
    if flows is not None:
        changes["flows"] = flows
    return dataclasses.replace(brief, **changes)


# --------------------------------------------------------------------------- PRE: priority order
def test_pre_prefers_a_new_cdsl_report_over_nse_context():
    prev = dt.date(2026, 10, 5)
    brief = _brief_with(
        flows={"fii": -5000.0, "dii": 4800.0},
        institutional={"cdsl": {"status": "PERSISTED_SNAPSHOT",
                               "snapshot": _cdsl_snapshot(prev)},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={"FII": {"subject": "FII", "current_value": -5000.0,
                                           "magnitude_state": "LARGE_NET_SELL",
                                           "streak_state": "NO_MEANINGFUL_CHANGE",
                                           "streak_length": 1, "streak_milestone": False,
                                           "prior_run_length": 0}})
    brief = dataclasses.replace(brief, pre_date=prev + dt.timedelta(days=1))
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert ok and model.kind == "CDSL"
    assert model.provenance["source"] == "SOURCE: CDSL"
    assert "CDSL" in reasons["FLOWS"]


def test_pre_uses_nsdl_sector_report_when_cdsl_is_not_new():
    fortnight_end = dt.date(2026, 9, 15)
    pre_date = fortnight_end + dt.timedelta(days=3)
    brief = _brief_with(
        flows={"fii": -500.0, "dii": 400.0},
        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                      "nsdl": {"status": "PERSISTED_SNAPSHOT",
                              "latest": _nsdl_snapshot(fortnight_end), "previous": None}},
        institutional_nse_context={})
    brief = dataclasses.replace(brief, pre_date=pre_date)
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert ok and model.kind == "NSDL_SECTOR"
    assert model.provenance["source"] == "SOURCE: NSDL"
    assert any(b["name"] == "HEALTHCARE" for b in model.bars)
    assert "NSDL" in reasons["FLOWS"]


def test_pre_bootstrap_cdsl_report_is_never_shown_as_new():
    prev = dt.date(2026, 10, 5)
    brief = _brief_with(
        flows={"fii": -500.0, "dii": 400.0},
        institutional={"cdsl": {"status": "PERSISTED_SNAPSHOT",
                               "snapshot": _cdsl_snapshot(prev, bootstrap=True)},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={})
    brief = dataclasses.replace(brief, pre_date=prev + dt.timedelta(days=1))
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert model is None or model.kind != "CDSL"


def test_pre_stale_nsdl_report_is_not_shown_as_new():
    fortnight_end = dt.date(2026, 8, 15)          # long past - not "new" any more
    pre_date = dt.date(2026, 10, 6)
    brief = _brief_with(
        flows={"fii": -500.0, "dii": 400.0},
        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                      "nsdl": {"status": "PERSISTED_SNAPSHOT",
                              "latest": _nsdl_snapshot(fortnight_end), "previous": None}},
        institutional_nse_context={})
    brief = dataclasses.replace(brief, pre_date=pre_date)
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert model is None or model.kind != "NSDL_SECTOR"


def test_pre_does_not_blindly_repeat_a_plain_large_nse_number():
    """A large single-day NSE number alone (no streak milestone, no reversal) is POST's story,
    not PRE's - PRE must not just restate it."""
    brief = _brief_with(
        flows={"fii": -9000.0, "dii": 8000.0},
        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={"FII": {"subject": "FII", "current_value": -9000.0,
                                           "magnitude_state": "LARGE_NET_SELL",
                                           "streak_state": "NO_MEANINGFUL_CHANGE",
                                           "streak_length": 1, "streak_milestone": False,
                                           "prior_run_length": 0},
                                   "DII": {"subject": "DII", "current_value": 8000.0,
                                           "magnitude_state": "NORMAL_RANGE",
                                           "streak_state": "NO_MEANINGFUL_CHANGE",
                                           "streak_length": 1, "streak_milestone": False,
                                           "prior_run_length": 0}})
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert not ok and model is None
    assert "does not repeat" in reasons["FLOWS"]


def test_pre_shows_an_nse_streak_milestone():
    brief = _brief_with(
        flows={"fii": -2000.0, "dii": 1800.0},
        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={"FII": {"subject": "FII", "current_value": -2000.0,
                                           "magnitude_state": "NORMAL_RANGE",
                                           "streak_state": "DIRECTION_CONTINUES",
                                           "streak_length": 4, "streak_milestone": True,
                                           "prior_run_length": 0}})
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert ok and model.kind == "NSE"
    assert "4 consecutive reported sessions" in model.headline


def test_pre_insufficient_history_falls_back_to_legacy_rule():
    brief = _brief_with(flows={"fii": -5000.0, "dii": 4500.0},
                        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                                      "nsdl": {"status": "NOT_CAPTURED", "latest": None,
                                              "previous": None}},
                        institutional_nse_context={})
    from presentation.pre_plan import _flows
    reasons = {}
    model, ok = _flows(brief, reasons)
    assert ok and model.kind == "NSE"
    assert reasons["FLOWS"].startswith("INSUFFICIENT_HISTORY:")


# --------------------------------------------------------------------------- PRE: watch wording
def test_pre_watch_card_says_net_buyers_sellers_never_bought_sold():
    brief = _brief_with(flows={"fii": -800.0, "dii": 1200.0},
                        institutional={"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
                                      "nsdl": {"status": "NOT_CAPTURED", "latest": None,
                                              "previous": None}},
                        institutional_nse_context={})
    plan = plan_pre_sections(brief)
    watch_text = " ".join(f"{w.title} {w.note}" for w in plan.watch)
    assert "bought" not in watch_text and "sold" not in watch_text
    if "FII" in watch_text:
        assert "net buyers" in watch_text or "net sellers" in watch_text


# --------------------------------------------------------------------------- PRE: full pipeline
def test_pre_cdsl_candidate_renders_claims_and_rights():
    from publication.scene_claims import pre_claims
    prev = dt.date(2026, 10, 5)
    brief = _brief_with(
        flows={"fii": -5000.0, "dii": 4800.0},
        institutional={"cdsl": {"status": "PERSISTED_SNAPSHOT",
                               "snapshot": _cdsl_snapshot(prev)},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={})
    brief = dataclasses.replace(brief, pre_date=prev + dt.timedelta(days=1))
    sb, plan = _storyboard(brief)
    assert plan.labels.get("FLOWS")
    claims = pre_claims(sb, brief)
    flow_claims = [c for c in claims if c["scene_id"].endswith("_flows")]
    assert flow_claims
    for c in flow_claims:
        assert c["publication_rights_status"] == "REVIEW_REQUIRED"
        assert "CDSL" in c["source_label"]
        assert any(fid.startswith("institutional:CDSL:") for fid in c["fact_ids"])


def test_pre_runtime_stays_in_bounds_with_a_cdsl_candidate():
    prev = dt.date(2026, 10, 5)
    brief = _brief_with(
        flows={"fii": -5000.0, "dii": 4800.0},
        institutional={"cdsl": {"status": "PERSISTED_SNAPSHOT",
                               "snapshot": _cdsl_snapshot(prev)},
                      "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}},
        institutional_nse_context={})
    brief = dataclasses.replace(brief, pre_date=prev + dt.timedelta(days=1))
    sb, _plan = _storyboard(brief)
    from presentation.pre_plan import MAX_RUNTIME
    assert 0 < sb.total_duration <= MAX_RUNTIME


def test_pre_language_scan_passes_for_every_candidate():
    from presentation.pre_plan import pre_language_issues
    prev = dt.date(2026, 10, 5)
    for institutional, ctx in [
        ({"cdsl": {"status": "PERSISTED_SNAPSHOT", "snapshot": _cdsl_snapshot(prev)},
          "nsdl": {"status": "NOT_CAPTURED", "latest": None, "previous": None}}, {}),
        ({"cdsl": {"status": "NOT_CAPTURED", "snapshot": None},
          "nsdl": {"status": "PERSISTED_SNAPSHOT",
                  "latest": _nsdl_snapshot(dt.date(2026, 9, 15)), "previous": None}}, {}),
    ]:
        brief = _brief_with(flows={"fii": -5000.0, "dii": 4800.0}, institutional=institutional,
                            institutional_nse_context=ctx)
        brief = dataclasses.replace(brief, pre_date=prev + dt.timedelta(days=3))
        sb, _plan = _storyboard(brief)
        issues = pre_language_issues(sb.public_text())
        assert issues == []


# --------------------------------------------------------------------------- POST: materiality
def _post_pres(pct=0.3):
    from types import SimpleNamespace
    import numpy as np
    df_closes = [24000 + 30 * np.sin(i / 3) for i in range(40)]
    import pandas as pd
    idx = pd.bdate_range(end="2026-10-05", periods=40)
    df = pd.DataFrame({"Open": df_closes, "High": [c + 20 for c in df_closes],
                      "Low": [c - 20 for c in df_closes], "Close": df_closes,
                      "ema20": [df_closes[0]] * 40}, index=idx)
    c = float(df["Close"].iloc[-1])
    m = {"pct": pct, "close": c, "chg": c * pct / 100, "open": float(df["Open"].iloc[-1]),
        "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]), "chart_df": df}
    return SimpleNamespace(m=m, sec=[{"name": "IT", "pct": 0.4}, {"name": "Metal", "pct": -0.2}],
                           session_date=dt.date(2026, 10, 5), fd=None)


def _post_flow_scene(fii=-9000.0, dii=8000.0, flow_context=None):
    from editorial.models import EditorialItem, ScenePlan, SceneType
    scene = ScenePlan(scene_id="flows", scene_type=SceneType.FLOWS,
                      primary_text="FII / DII FLOWS · PROVISIONAL",
                      metadata={"presentation": "FLOWS_SCAN",
                               "materiality": flow_context or {}})
    for label, value in (("FII", fii), ("DII", dii)):
        scene.items.append(EditorialItem(title=label, value=f"{value:+.0f}",
                                         label="NET BUYERS" if value >= 0 else "NET SELLERS",
                                         numeric=value, positive=value >= 0))
    scene.duration = 5.5
    return scene


class _FakePlan:
    def __init__(self, scenes):
        self.scenes = scenes


def test_post_insufficient_history_uses_legacy_threshold():
    from presentation.post_plan import plan_post_sections
    plan = _FakePlan([_post_flow_scene(fii=-9000.0, dii=8000.0, flow_context={})])
    out = plan_post_sections(_post_pres(), plan)
    assert out.show_flows is True
    assert out.reasons["FLOWS"].startswith("INSUFFICIENT_HISTORY:")


def test_post_large_magnitude_is_included_with_materiality_reason():
    from presentation.post_plan import plan_post_sections
    ctx = {"FII": {"subject": "FII", "current_value": -2000.0,
                  "magnitude_state": "LARGE_NET_SELL", "multiple_of_median": 3.5,
                  "streak_state": "NO_MEANINGFUL_CHANGE", "streak_length": 1,
                  "streak_milestone": False, "prior_run_length": 0}}
    plan = _FakePlan([_post_flow_scene(fii=-2000.0, dii=1800.0, flow_context=ctx)])
    out = plan_post_sections(_post_pres(), plan)
    assert out.show_flows is True
    assert "3.5x" in out.reasons["FLOWS"]


def test_post_normal_range_with_full_history_is_omitted():
    from presentation.post_plan import plan_post_sections
    ctx = {"FII": {"subject": "FII", "current_value": -600.0, "magnitude_state": "NORMAL_RANGE",
                  "multiple_of_median": 1.1, "streak_state": "NO_MEANINGFUL_CHANGE",
                  "streak_length": 1, "streak_milestone": False, "prior_run_length": 0},
          "DII": {"subject": "DII", "current_value": 500.0, "magnitude_state": "NORMAL_RANGE",
                  "multiple_of_median": 1.0, "streak_state": "NO_MEANINGFUL_CHANGE",
                  "streak_length": 1, "streak_milestone": False, "prior_run_length": 0}}
    plan = _FakePlan([_post_flow_scene(fii=-600.0, dii=500.0, flow_context=ctx)])
    out = plan_post_sections(_post_pres(), plan)
    assert out.show_flows is False
    assert "normal range" in out.reasons["FLOWS"]


def test_post_streak_milestone_is_included():
    from presentation.post_plan import plan_post_sections
    ctx = {"FII": {"subject": "FII", "current_value": -2000.0, "magnitude_state": "NORMAL_RANGE",
                  "multiple_of_median": 1.2, "streak_state": "DIRECTION_CONTINUES",
                  "streak_length": 5, "streak_milestone": True, "prior_run_length": 0}}
    plan = _FakePlan([_post_flow_scene(fii=-2000.0, dii=1800.0, flow_context=ctx)])
    out = plan_post_sections(_post_pres(), plan)
    assert out.show_flows is True
    assert "5 recorded sessions" in out.reasons["FLOWS"]


def test_post_reversal_is_included():
    from presentation.post_plan import plan_post_sections
    ctx = {"FII": {"subject": "FII", "current_value": 1500.0, "magnitude_state": "NORMAL_RANGE",
                  "multiple_of_median": 1.4, "streak_state": "DIRECTION_REVERSES",
                  "streak_length": 1, "streak_milestone": False, "prior_run_length": 3}}
    plan = _FakePlan([_post_flow_scene(fii=1500.0, dii=1200.0, flow_context=ctx)])
    out = plan_post_sections(_post_pres(), plan)
    assert out.show_flows is True
    assert "reverses a 3-session run" in out.reasons["FLOWS"]
