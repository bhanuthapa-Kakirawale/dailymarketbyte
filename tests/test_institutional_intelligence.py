"""Institutional Flow Intelligence V1 - the deterministic derived layer: FII/DII materiality
(intelligence.flow_materiality) and NSDL sector flow (institutional_flows.sector_flow /
sector_map). No network, no model, no fetch - pure arithmetic over already-eligible facts.
"""
import datetime as dt

import pytest

from conftest import NOW
from conftest_intelligence import seed, session_report, trading_sessions

from core import Metric
from intelligence import flow_materiality
from intelligence.history import HistoricalWindow
from storage import MarketHistory, default_db_path


def _window(tmp_path, sessions, fii_values, dii_values, today_pct=0.2, today_fii=None,
           today_dii=None):
    """`len(sessions)` prior sessions seeded with `fii_values`/`dii_values` (same length),
    then one more (unsaved) report for `today`, returned as (report, window)."""
    history = MarketHistory(default_db_path(str(tmp_path)))
    reports = [session_report(s, pct=0.1, fii=f, dii=d)
              for s, f, d in zip(sessions, fii_values, dii_values)]
    seed(history, reports)
    today = sessions[-1] + dt.timedelta(days=1)
    while today.weekday() >= 5:
        today += dt.timedelta(days=1)
    report = session_report(today, pct=today_pct, fii=today_fii, dii=today_dii,
                            report_date=today + dt.timedelta(days=1))
    window = HistoricalWindow(history, report.session_date, report_id=report.report_id,
                              window_sessions=60)
    return report, window


# --------------------------------------------------------------------------- magnitude
def test_insufficient_history_with_fewer_than_ten_prior_sessions(tmp_path):
    sessions = trading_sessions(5)
    report, window = _window(tmp_path, sessions, [500] * 5, [400] * 5,
                             today_fii=-6000, today_dii=5000)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["magnitude_state"] == flow_materiality.INSUFFICIENT_HISTORY
    assert ctx["FII"]["median_abs_prior_10"] is None
    # INSUFFICIENT_HISTORY never counts as material on its own, whatever the streak looks like.
    assert ctx["FII"]["magnitude_state"] != flow_materiality.LARGE_NET_SELL


def test_large_net_sell_flagged_against_ten_session_median(tmp_path):
    sessions = trading_sessions(10)
    # alternating small sells/buys -> median |value| = 500
    vals = [500, -500] * 5
    report, window = _window(tmp_path, sessions, vals, [100] * 10, today_fii=-2000, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["magnitude_state"] == flow_materiality.LARGE_NET_SELL
    assert ctx["FII"]["median_abs_prior_10"] == 500
    assert ctx["FII"]["multiple_of_median"] == 4.0
    assert flow_materiality.is_material(ctx["FII"])


def test_normal_range_flow_is_not_material(tmp_path):
    sessions = trading_sessions(10)
    vals = [500, -500] * 5
    report, window = _window(tmp_path, sessions, vals, [100] * 10, today_fii=600, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["magnitude_state"] == flow_materiality.NORMAL_RANGE
    assert not flow_materiality.is_material(ctx["FII"])


def test_large_floor_blocks_a_small_absolute_move_even_at_a_big_multiple(tmp_path):
    """A x20 multiple of a tiny median is still not Rs 1,000 cr - never material."""
    sessions = trading_sessions(10)
    report, window = _window(tmp_path, sessions, [10] * 10, [5] * 10, today_fii=300, today_dii=5)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["multiple_of_median"] == 30.0
    assert ctx["FII"]["magnitude_state"] == flow_materiality.NORMAL_RANGE


# --------------------------------------------------------------------------- streak / reversal
def test_streak_milestone_at_length_four(tmp_path):
    # 10 unrelated (buying) sessions provide the magnitude history, then a 3-session sell run
    # right before today, which today extends to a streak of exactly 4.
    sessions = trading_sessions(13)
    fii_hist = [500] * 10 + [-700, -700, -700]
    report, window = _window(tmp_path, sessions, fii_hist, [100] * 13,
                             today_fii=-650, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["streak_state"] == flow_materiality.DIRECTION_CONTINUES
    assert ctx["FII"]["streak_length"] == 4
    assert ctx["FII"]["streak_milestone"] is True
    assert flow_materiality.is_material(ctx["FII"])


def test_streak_of_three_is_not_yet_a_milestone(tmp_path):
    sessions = trading_sessions(13)
    fii_hist = [500] * 10 + [-700, -700]   # a 2-session sell run before today
    report, window = _window(tmp_path, sessions, fii_hist, [100] * 13,
                             today_fii=-650, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["streak_state"] == flow_materiality.DIRECTION_CONTINUES
    assert ctx["FII"]["streak_length"] == 3
    assert ctx["FII"]["streak_milestone"] is False


def test_direction_reversal_after_a_three_session_sell_run(tmp_path):
    sessions = trading_sessions(13)
    fii_hist = [500] * 10 + [-900, -900, -900]  # median of prior 10 is well-defined
    report, window = _window(tmp_path, sessions, fii_hist, [100] * 13,
                             today_fii=1200, today_dii=50)   # flips positive, large vs median
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["streak_state"] == flow_materiality.DIRECTION_REVERSES
    assert ctx["FII"]["prior_run_length"] == 3
    assert ctx["FII"]["streak_length"] == 1
    assert flow_materiality.is_material(ctx["FII"])


def test_short_opposite_run_is_not_a_reversal(tmp_path):
    # Values chosen so magnitude alone is NOT material (today's 1200 is only ~1.2x the
    # prior-10 median of 1000) - this isolates the streak/reversal rule: a 2-session opposite
    # run is below REVERSAL_MIN_PRIOR_RUN(3), so the sign flip is NO_MEANINGFUL_CHANGE even
    # though it is technically >= REVERSAL_MULTIPLE of the median.
    sessions = trading_sessions(13)
    fii_hist = [1000] * 10 + [-900, -900]
    report, window = _window(tmp_path, sessions, fii_hist, [100] * 13,
                             today_fii=1200, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["magnitude_state"] == flow_materiality.NORMAL_RANGE
    assert ctx["FII"]["streak_state"] == flow_materiality.NO_MEANINGFUL_CHANGE
    assert ctx["FII"]["prior_run_length"] == 2
    assert not flow_materiality.is_material(ctx["FII"])


def test_zero_flow_is_no_meaningful_change(tmp_path):
    sessions = trading_sessions(10)
    report, window = _window(tmp_path, sessions, [500] * 10, [100] * 10, today_fii=0, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert ctx["FII"]["streak_state"] == flow_materiality.NO_MEANINGFUL_CHANGE
    assert ctx["FII"]["streak_length"] == 0


def test_no_current_fact_is_simply_absent_from_the_context(tmp_path):
    sessions = trading_sessions(10)
    report, window = _window(tmp_path, sessions, [500] * 10, [100] * 10,
                             today_fii=None, today_dii=50)
    ctx = flow_materiality.analyse(report, window)
    assert "FII" not in ctx and "DII" in ctx


def test_deterministic_same_inputs_same_output(tmp_path):
    sessions = trading_sessions(13)
    fii_hist = [500] * 10 + [-900, -900, -900]
    report, window = _window(tmp_path, sessions, fii_hist, [100] * 13,
                             today_fii=1200, today_dii=50)
    first = flow_materiality.analyse(report, window)
    second = flow_materiality.analyse(report, window)
    assert first == second


def test_no_network_or_model_dependency():
    import inspect
    src = inspect.getsource(flow_materiality)
    for term in ("requests", "yfinance", "providers", "news", "gemini", "urllib", "socket"):
        assert term not in src.lower()


# --------------------------------------------------------------------------- sector mapping
def test_every_nsdl_sector_label_is_explicitly_pinned():
    from institutional_flows.sector_map import NSDL_TO_SECTOR, sector_for_nsdl
    # Verified live against NSDL's fortnight ending 15 Sep 2026 (24 rows incl. Sovereign/Others)
    expected_mapped = {
        "Automobile and Auto Components": "Auto", "Capital Goods": "Capital Goods",
        "Chemicals": "Chemicals", "Construction": "Construction",
        "Construction Materials": "Construction Materials", "Consumer Durables": "Consumer Durables",
        "Consumer Services": "Consumer Services", "Diversified": "Diversified",
        "Fast Moving Consumer Goods": "FMCG", "Financial Services": "Financials",
        "Forest Materials": "Forest Materials", "Healthcare": "Healthcare",
        "Information Technology": "IT", "Media, Entertainment & Publication": "Media",
        "Metals & Mining": "Metals", "Oil, Gas & Consumable Fuels": "Oil & Gas",
        "Power": "Power", "Realty": "Realty", "Services": "Services",
        "Telecommunication": "Telecom", "Textiles": "Textiles",
    }
    for label, sector in expected_mapped.items():
        assert sector_for_nsdl(label) == sector
    assert sector_for_nsdl("Utilities") == "UNMAPPED"
    assert sector_for_nsdl("Sovereign") == "NOT_A_SECTOR"
    assert sector_for_nsdl("Others") == "NOT_A_SECTOR"
    assert sector_for_nsdl("Grand Total") == "NOT_A_SECTOR"
    assert sector_for_nsdl("Something NSDL Never Published") == "UNMAPPED"


def test_unknown_sector_label_never_guessed():
    from institutional_flows.sector_map import sector_for_nsdl
    assert sector_for_nsdl("") == "UNMAPPED"
    assert sector_for_nsdl("  Healthcare  ") == "Healthcare"   # whitespace only, no fuzzy match
    assert sector_for_nsdl("Health Care") == "UNMAPPED"          # NOT a fuzzy match to Healthcare


# --------------------------------------------------------------------------- sector flow
def _snapshot(facts):
    from institutional_flows.models import (DEPOSITORY_FORTNIGHTLY, NSDL, RepresentedPeriod,
                                            SCHEMA_VERSION, SUCCESS, InstitutionalFlowSnapshot)
    return InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=NSDL, source_class=DEPOSITORY_FORTNIGHTLY,
        report_type="fpi_fortnightly_sector", report_key="2026-09-15", report_date="2026-09-15",
        data_as_of="2026-09-15", represented_period=RepresentedPeriod(), retrieved_at=NOW.isoformat(),
        first_retrieved_at=NOW.isoformat(), status=SUCCESS, facts=facts)


def test_auc_never_creates_a_flow_fact():
    from institutional_flows.sector_flow import sector_flows
    snap = _snapshot([
        {"sector": "Healthcare", "metric": "auc", "period": "current", "category": "equity",
         "value": 500000.0, "unit": "INR_CRORE"},
        {"sector": "Healthcare", "metric": "auc", "period": "previous", "category": "equity",
         "value": 480000.0, "unit": "INR_CRORE"},
    ])
    assert sector_flows(snap) == []


def test_sovereign_and_others_rows_are_never_sectors():
    from institutional_flows.sector_flow import sector_flows
    snap = _snapshot([
        {"sector": "Sovereign", "metric": "net_investment", "period": "current",
         "category": "equity", "value": 100.0, "unit": "INR_CRORE"},
        {"sector": "Sovereign", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": -100.0, "unit": "INR_CRORE"},
        {"sector": "Others", "metric": "net_investment", "period": "current",
         "category": "equity", "value": 50.0, "unit": "INR_CRORE"},
        {"sector": "Others", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": 50.0, "unit": "INR_CRORE"},
    ])
    assert sector_flows(snap) == []


@pytest.mark.parametrize("current,previous,expected", [
    (500, 400, "INFLOW_CONTINUES"), (-500, -400, "OUTFLOW_CONTINUES"),
    (500, -400, "REVERSAL_TO_INFLOW"), (-500, 400, "REVERSAL_TO_OUTFLOW"),
    (20, -20, "NO_MEANINGFUL_CHANGE"),
])
def test_sector_flow_direction_classification(current, previous, expected):
    from institutional_flows.sector_flow import sector_flows
    snap = _snapshot([
        {"sector": "Healthcare", "metric": "net_investment", "period": "current",
         "category": "equity", "value": float(current), "unit": "INR_CRORE"},
        {"sector": "Healthcare", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": float(previous), "unit": "INR_CRORE"},
    ])
    flows = sector_flows(snap)
    assert len(flows) == 1 and flows[0].direction == expected


def test_select_public_bars_caps_at_four_and_respects_floors():
    from institutional_flows.sector_flow import select_public_bars, sector_flows
    rows = []
    for sector, cur, prev in [("Healthcare", 2000, 1000), ("Financial Services", -3000, -500),
                              ("Information Technology", 1200, -200),
                              ("Automobile and Auto Components", -1500, 200),
                              ("Metals & Mining", 50, 40), ("Power", 20, 5)]:
        rows.append({"sector": sector, "metric": "net_investment", "period": "current",
                    "category": "equity", "value": float(cur), "unit": "INR_CRORE"})
        rows.append({"sector": sector, "metric": "net_investment", "period": "previous",
                    "category": "equity", "value": float(prev), "unit": "INR_CRORE"})
    snap = _snapshot(rows)
    flows = sector_flows(snap)
    bars = select_public_bars(flows)
    assert len(bars) <= 4
    sectors = {b.display_sector for b in bars}
    assert "Healthcare" in sectors   # top inflow
    assert "Financials" in sectors   # top outflow
    assert "Metals" not in sectors and "Power" not in sectors   # below every floor
    assert "IT" in sectors and "Auto" in sectors   # change >= floor, distinct from the two picked above


def test_select_public_bars_empty_when_nothing_qualifies():
    from institutional_flows.sector_flow import select_public_bars, sector_flows
    snap = _snapshot([
        {"sector": "Healthcare", "metric": "net_investment", "period": "current",
         "category": "equity", "value": 10.0, "unit": "INR_CRORE"},
        {"sector": "Healthcare", "metric": "net_investment", "period": "previous",
         "category": "equity", "value": 5.0, "unit": "INR_CRORE"},
    ])
    assert select_public_bars(sector_flows(snap)) == []
