"""Private market regime classifier V1 (docs/PRIVATE_MARKET_REGIME.md).

Two kinds of fixture:
* in-memory SYNTHETIC `RegimeData` (placeholder symbols SYN-A00..) with controlled trends, so each
  regime rule can be driven deliberately and every number checked by hand;
* the Private Desk's synthetic output tree (tests/test_private_desk.py `desk_out` / `svc` /
  `client`) for the dashboard, detail page, Data Quality, packet and read-only guarantees.
"""
import ast
import dataclasses
import datetime as dt
import json
import math
import os
import re

import numpy as np
import pytest

from conftest import SESSION
from test_private_desk import (DESK, FORBIDDEN_WORDS, NOW_AFTER, _fingerprint, client, desk_out,  # noqa: F401
                               svc)

from private_desk.regime import engine, model, rules
from private_desk.regime.data import RegimeData
from private_desk.regime.metrics import session_metrics
from private_desk.regime.model import (BEARISH, BULLISH, INSUFFICIENT_DATA, MIXED, NEGATIVE,
                                       NEUTRAL, NEUTRAL_STATE, POSITIVE, TRANSITIONAL,
                                       UNAVAILABLE, RegimeDimension)

REGIME_PKG = os.path.join(DESK, "regime")
SECTORS = ("Alpha", "Beta", "Gamma", "Delta")


# ------------------------------------------------------------------ synthetic data
def _days(n, start=dt.date(2026, 1, 5)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return tuple(out)


def _data(*, n=120, per_sector=15, nifty_drift=0.0, drifts=(0.0, 0.0, 0.0, 0.0),
          spike_days=0, spikes_per_day=10, context=None) -> RegimeData:
    """Stocks drift by sector (daily rate) with a small oscillation; NIFTY 50 drifts with its own
    oscillation. `spike_days`: the last N sessions carry 3x volume on `spikes_per_day` stocks."""
    days = _days(n)
    syms, sector = [], {}
    for s, name in enumerate(SECTORS):
        for i in range(per_sector):
            sym = f"SYN-{chr(65 + s)}{i:02d}"
            syms.append(sym)
            sector[sym] = name
    N = len(syms)
    close = np.zeros((n, N))
    volume = np.full((n, N), 1e6)
    for k, sym in enumerate(syms):
        g = drifts[SECTORS.index(sector[sym])]
        for i in range(n):
            close[i, k] = 100 * (1 + g) ** i * (1 + 0.001 * math.sin(0.7 * i + k))
    for back in range(1, spike_days + 1):
        i = n - back
        for k in range((back * 7) % N, (back * 7) % N + spikes_per_day):
            volume[i, k % N] = 3e6
    nifty = np.array([20000 * (1 + nifty_drift) ** i * (1 + 0.002 * math.sin(1.3 * i))
                      for i in range(n)])
    return RegimeData(sessions=days, symbols=tuple(syms), sector=sector, close=close,
                      volume=volume, member=np.ones((n, N), dtype=bool), nifty=nifty,
                      universe_session=tuple(days[0] for _ in days), context=context or {})


def _bull(**kw):
    return _data(nifty_drift=0.003, drifts=(0.003,) * 4, spike_days=5, **kw)


def _bear(**kw):
    return _data(nifty_drift=-0.003, drifts=(-0.003,) * 4, spike_days=5, **kw)


def _neutral(**kw):
    return _data(nifty_drift=0.0, drifts=(0.002, 0.002, -0.002, -0.002), **kw)


def _conflict(**kw):
    return _data(nifty_drift=0.003, drifts=(-0.003,) * 4, **kw)


def _classify(data, d=None):
    return engine.classify_session(data, d or data.last, generated_at="T")


def _strip(s):
    d = s.to_dict()
    d.pop("generated_at")
    return d


def _dims(**states):
    base = {k: UNAVAILABLE for k in rules.DIMENSION_ORDER}
    base.update({"VOLATILITY": "STABLE"})
    base.update(states)
    return {k: RegimeDimension(key=k, name=rules.NAMES[k], role=rules.CLASSIFIERS[k][1],
                               state=v, facts={}, rule="", explanation="", source="",
                               data_as_of="2026-01-01", available=v != UNAVAILABLE)
            for k, v in base.items()}


# ------------------------------------------------------------------ 1-2. determinism
def test_classification_is_deterministic():
    a, b = _classify(_bull()), _classify(_bull())
    assert _strip(a) == _strip(b)
    assert json.dumps(_strip(a), sort_keys=True, default=str) == \
        json.dumps(_strip(model.MarketRegimeSnapshot.from_dict(a.to_dict())), sort_keys=True,
                   default=str)                                    # stable round trip


def test_same_inputs_same_regime_through_the_desk(svc):
    one = svc.regime(SESSION)["snapshot"]
    two = svc.regime(SESSION)["snapshot"]
    assert _strip(one) == _strip(two)


# ------------------------------------------------------------------ 3. no LLM / no network
def test_regime_package_has_no_model_or_network_dependency():
    banned_imports = ("news", "providers", "google", "requests", "urllib", "httpx", "openai",
                      "anthropic", "hooks", "upload", "yfinance", "kiteconnect", "socket")
    for f in os.listdir(REGIME_PKG):
        if not f.endswith(".py"):
            continue
        src = open(os.path.join(REGIME_PKG, f), encoding="utf-8").read()
        assert "gemini" not in src.lower(), f
        for node in ast.walk(ast.parse(src)):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in banned_imports, f"{f}: imports {m}"


# ------------------------------------------------------------------ 4/5/21. no lookahead
def test_future_data_cannot_change_a_past_classification():
    data = _bull()
    d = data.sessions[-15]
    before = _classify(data, d)
    i = data.index_of(d)
    rng = np.random.default_rng(7)
    close, volume, nifty = data.close.copy(), data.volume.copy(), data.nifty.copy()
    close[i + 1:] = rng.uniform(1, 1000, close[i + 1:].shape)      # wreck every future row
    volume[i + 1:] *= 50
    nifty[i + 1:] = np.nan
    mutated = dataclasses.replace(data, close=close, volume=volume, nifty=nifty, rvol_memo={})
    assert _strip(_classify(mutated, d)) == _strip(before)
    assert _strip(_classify(data.until(d), d)) == _strip(before)  # physically cut at d
    # sanity: the mutation does matter for a session that can see it
    assert _classify(mutated).regime != _classify(data).regime or \
        _strip(_classify(mutated)) != _strip(_classify(data))


def test_until_physically_removes_later_rows():
    data = _bull()
    d = data.sessions[60]
    cut = data.until(d)
    assert cut.sessions[-1] == d and cut.close.shape[0] == 61 and len(cut.nifty) == 61
    assert all(k <= d for k in cut.context)


def test_no_forward_return_is_an_input():
    """Metrics of session d depend on rows <= d only: shuffling every later row changes nothing
    in ANY metric (a forward return would)."""
    data = _neutral()
    d = data.sessions[-10]
    m1 = session_metrics(data.until(d))
    close = data.close.copy()
    close[data.index_of(d) + 1:] *= 3.0
    m2 = session_metrics(dataclasses.replace(data, close=close, rvol_memo={}).until(d))
    assert m1 == m2
    for f in os.listdir(REGIME_PKG):
        if f.endswith(".py"):
            src = open(os.path.join(REGIME_PKG, f), encoding="utf-8").read()
            # the only mention allowed is the research summary's explicit `forward_returns_used: False`
            assert not re.search(r"forward_return(?!s_used)|future_return|next_session", src), f


# ------------------------------------------------------------------ 6/8. minimum-data gate
def test_short_constituent_history_is_insufficient_not_guessed():
    s = _classify(_data(n=40, nifty_drift=0.003, drifts=(0.003,) * 4))
    assert s.regime == INSUFFICIENT_DATA and s.reason_code == "MINIMUM_DATA_NOT_MET"
    assert "BREADTH" in s.missing_dimensions


def test_missing_benchmark_bar_makes_trend_unavailable_and_regime_insufficient():
    data = _bull()
    nifty = data.nifty.copy()
    nifty[-1] = np.nan
    s = _classify(dataclasses.replace(data, nifty=nifty))
    assert s.dimension("TREND").state == UNAVAILABLE
    assert s.regime == INSUFFICIENT_DATA


def test_partial_constituent_coverage_is_never_filled():
    data = _bull()
    close = data.close.copy()
    close[-1, :5] = np.nan                                       # 55 / 60 = 91.7% < 95%
    s = _classify(dataclasses.replace(data, close=close))
    assert s.dimension("BREADTH").state == UNAVAILABLE
    assert s.regime == INSUFFICIENT_DATA


def test_gate_rules():
    assert rules.decide(_dims(TREND=UNAVAILABLE, BREADTH=POSITIVE, SECTORS=POSITIVE))[0] == INSUFFICIENT_DATA
    assert rules.decide(_dims(TREND=POSITIVE, BREADTH=UNAVAILABLE, SECTORS=POSITIVE))[0] == INSUFFICIENT_DATA
    assert rules.decide(_dims(TREND=POSITIVE, BREADTH=POSITIVE, VOLATILITY=UNAVAILABLE))[0] == INSUFFICIENT_DATA
    assert rules.decide(_dims(TREND=POSITIVE, BREADTH=POSITIVE))[0] == BULLISH   # volatility suffices


# ------------------------------------------------------------------ 7. missing VIX
def test_missing_vix_fabricates_nothing():
    data = _bull()
    s = _classify(data)
    vol = s.dimension("VOLATILITY")
    assert vol.facts["india_vix"] is None and vol.facts["vix_status"] is None
    assert "India VIX not recorded" in vol.explanation
    with_vix = _classify(dataclasses.replace(data, context={data.last: {"india_vix": 13.2,
                                                                         "vix_status": "SINGLE_SOURCE"}},
                                             rvol_memo={}))
    assert with_vix.dimension("VOLATILITY").facts["india_vix"] == 13.2
    assert with_vix.dimension("VOLATILITY").state == vol.state      # VIX is not a V1 rule input
    assert with_vix.regime == s.regime


# ------------------------------------------------------------------ 9-12. the regime rules
def test_bullish_scenario_and_requirements():
    s = _classify(_bull())
    assert s.regime == BULLISH and s.reason_code == "BOTH_PILLARS_POSITIVE"
    states = {d.key: d.state for d in s.dimensions}
    assert states["TREND"] == states["BREADTH"] == POSITIVE
    d = rules.decide
    assert d(_dims(TREND=POSITIVE, BREADTH=MIXED, SECTORS=POSITIVE, VOLUME=POSITIVE))[0] == TRANSITIONAL
    assert d(_dims(TREND=POSITIVE, BREADTH=POSITIVE, SECTORS=NEGATIVE))[1] == "SUPPORT_CONTRADICTS"
    assert d(_dims(TREND=POSITIVE, BREADTH=POSITIVE, SECTORS=MIXED, VOLUME=NEGATIVE))[0] == TRANSITIONAL
    assert d(_dims(TREND=POSITIVE, BREADTH=POSITIVE, SECTORS=POSITIVE, VOLUME=NEGATIVE))[0] == BULLISH
    assert d(_dims(TREND=POSITIVE, BREADTH=POSITIVE, SECTORS=POSITIVE,
                   VOLATILITY="ELEVATED"))[1] == "VOLATILITY_STRESS"


def test_bearish_scenario_and_requirements():
    s = _classify(_bear())
    assert s.regime == BEARISH and s.reason_code == "BOTH_PILLARS_NEGATIVE"
    d = rules.decide
    assert d(_dims(TREND=NEGATIVE, BREADTH=NEUTRAL_STATE, SECTORS=NEGATIVE))[0] == TRANSITIONAL
    assert d(_dims(TREND=NEGATIVE, BREADTH=NEGATIVE, SECTORS=POSITIVE))[0] == TRANSITIONAL
    assert d(_dims(TREND=NEGATIVE, BREADTH=NEGATIVE, SECTORS=NEGATIVE, VOLUME=POSITIVE))[0] == BEARISH
    assert d(_dims(TREND=NEGATIVE, BREADTH=NEGATIVE, SECTORS=MIXED, VOLUME=POSITIVE))[0] == TRANSITIONAL
    assert d(_dims(TREND=NEGATIVE, BREADTH=NEGATIVE, SECTORS=NEGATIVE,
                   VOLATILITY="ELEVATED"))[0] == BEARISH           # stress only blocks BULLISH


def test_conflicting_evidence_is_transitional():
    s = _classify(_conflict())
    assert s.regime == TRANSITIONAL and s.reason_code == "CONFLICT"
    assert s.dimension("TREND").state == POSITIVE and s.dimension("BREADTH").state == NEGATIVE
    assert rules.decide(_dims(TREND=MIXED, BREADTH=MIXED, SECTORS=NEGATIVE, VOLUME=POSITIVE))[1] == "CONFLICT"


def test_balanced_evidence_is_neutral_and_differs_from_transitional():
    s = _classify(_neutral())
    assert s.regime == NEUTRAL and s.reason_code == "BALANCED"
    assert s.dimension("TREND").state == NEUTRAL_STATE and s.dimension("BREADTH").state == NEUTRAL_STATE
    d = rules.decide
    assert d(_dims(TREND=MIXED, BREADTH=NEUTRAL_STATE, SECTORS=MIXED, VOLUME=MIXED))[0] == NEUTRAL
    assert d(_dims(TREND=MIXED, BREADTH=NEUTRAL_STATE, SECTORS=POSITIVE,
                   VOLUME=POSITIVE))[1] == "PARTICIPATION_BUILDING"
    assert d(_dims(TREND=NEUTRAL_STATE, BREADTH=MIXED, SECTORS=MIXED))[1] == "BREADTH_MIXED"
    assert d(_dims(TREND=NEUTRAL_STATE, BREADTH=NEUTRAL_STATE, SECTORS=MIXED,
                   VOLATILITY="ELEVATED"))[0] == TRANSITIONAL


def test_confirmation_rule_is_one_session_and_transparent():
    assert rules.confirm(BULLISH, "BOTH_PILLARS_POSITIVE", TRANSITIONAL) == (TRANSITIONAL, "UNCONFIRMED_CHANGE")
    assert rules.confirm(BULLISH, "BOTH_PILLARS_POSITIVE", BULLISH) == (BULLISH, "BOTH_PILLARS_POSITIVE")
    assert rules.confirm(TRANSITIONAL, "CONFLICT", BULLISH) == (TRANSITIONAL, "CONFLICT")
    assert rules.confirm(INSUFFICIENT_DATA, "MINIMUM_DATA_NOT_MET", BULLISH)[0] == INSUFFICIENT_DATA
    # a one-session index dip (below SMA20) the session before: today's BULLISH reading is not
    # yet confirmed, and the snapshot says exactly that
    data = _bull()
    nifty = data.nifty.copy()
    nifty[-2] *= 0.965
    s = _classify(dataclasses.replace(data, nifty=nifty, rvol_memo={}))
    assert s.candidate_regime == BULLISH and s.previous_candidate == TRANSITIONAL
    assert s.regime == TRANSITIONAL and s.reason_code == "UNCONFIRMED_CHANGE"
    assert s.supporting_evidence[0].startswith("Previous session's evidence read TRANSITIONAL")
    assert s.rule_applied == rules.CONFIRMATION_RULE
    assert "not yet confirmed" in s.explanation


# ------------------------------------------------------------------ 14. WHY maps to dimensions
def test_why_explanation_maps_exactly_to_dimensions():
    for data in (_bull(), _bear(), _neutral(), _conflict()):
        s = _classify(data)
        dims = {d.key: d for d in s.dimensions}
        for k in rules.CORE_KEYS:
            d = dims[k]
            assert f"{d.name} {d.state}" in s.explanation
            if rules.brief(d):
                assert rules.brief(d) in s.explanation
        lines = s.supporting_evidence + s.conflicting_evidence
        assert lines
        for line in lines:
            if line.startswith("Previous session"):
                continue
            d = next(x for x in s.dimensions if line.startswith(f"{x.name} {x.state}: "))
            assert line == f"{d.name} {d.state}: {d.explanation}"
            assert d.role != "CONTEXT"                               # context never argues


# ------------------------------------------------------------------ 15. detail values = data
def test_dimension_facts_are_the_underlying_numbers():
    data = _bear()
    s = _classify(data)
    t, b = s.dimension("TREND").facts, s.dimension("BREADTH").facts
    assert t["close"] == round(data.nifty[-1], 2)
    assert t["sma20"] == round(data.nifty[-20:].mean(), 2)
    assert t["sma50"] == round(data.nifty[-50:].mean(), 2)
    chg = data.close[-1] / data.close[-2] - 1
    assert b["advances"] == int((chg > 0).sum()) and b["declines"] == int((chg < 0).sum())
    sma50 = data.close[-50:].mean(axis=0)
    assert b["above_sma50"] == int((data.close[-1] > sma50).sum())
    sec = s.dimension("SECTORS").facts
    assert sec["sectors_counted"] == 4 and sec["sectors_positive"] == 0
    v = s.dimension("VOLUME").facts
    assert v["events"] == 5 * 10 and v["down_events"] == 50 and v["up_events"] == 0


def test_breadth_reconciles_with_the_market_structure_artifact(svc):
    from private_desk.regime.data import load_regime_data
    data = load_regime_data(svc.repo, SESSION)
    m = session_metrics(data.until(SESSION))
    art, _ = svc.repo.market_structure(SESSION)
    metrics = art["snapshot"]["metrics"]
    assert m["breadth"]["advances"] == metrics["ADVANCES"]["numerator"]
    assert m["breadth"]["declines"] == metrics["DECLINES"]["numerator"]
    day = m["volume"]["days"][-1]
    assert day["unusual"] == metrics["UNUSUAL_VOLUME"]["numerator"]


# ------------------------------------------------------------------ 13. dashboard / pages
def test_dashboard_displays_the_regime_card(client, svc):
    g = svc.regime(SESSION)["snapshot"]
    html = client.get("/").text
    assert "regime-card" in html and "Market regime" in html
    assert f"rl-{g.regime.lower()}" in html
    assert "no regime classifier" not in html
    for d in g.dimensions:
        assert d.name in html


def test_regime_detail_page_shows_rules_numbers_and_history(client, svc):
    prev = svc.sessions()[-2]
    g = svc.regime(prev)["snapshot"]
    html = client.get(f"/regime?session={prev}").text
    assert "Why this regime" in html and "Regime rules" in html and "Recent regime history" in html
    assert g.dimension("BREADTH").rule.split(":")[0] in html
    b = g.dimension("BREADTH").facts
    if g.dimension("BREADTH").available:
        assert f"advances: {b['advances']}" in html
    for label in (BULLISH, BEARISH, NEUTRAL, TRANSITIONAL):
        assert f"rl-{label.lower()}" in html                        # the rules table


def test_stock_page_shows_regime_as_context_only(client, svc):
    html = client.get("/stock/SYMA").text
    g = svc.regime(SESSION)["snapshot"]
    assert "Market regime" in html and f"rl-{g.regime.lower()}" in html
    assert "does not change this stock" in html


# ------------------------------------------------------------------ 16-17. context only
def test_regime_never_changes_radar_candidates_or_attention_order(svc, monkeypatch):
    base = svc.dashboard(SESSION)
    for forced in (BULLISH, BEARISH):
        fake = dataclasses.replace(base["market_regime"], regime=forced)
        monkeypatch.setattr(svc.regimes, "snapshots", lambda *a, _f=fake, **k: [_f])
        monkeypatch.setattr(svc.regimes, "snapshot", lambda *a, _f=fake, **k: _f)
        d = svc.dashboard(SESSION)
        assert d["market_regime"].regime == forced
        assert [v["symbol"] for v in d["views"]] == [v["symbol"] for v in base["views"]]
        assert [a["view"]["symbol"] for a in d["attention"]] == \
            [a["view"]["symbol"] for a in base["attention"]]
        assert d["radar_order"] == base["radar_order"]
        assert [(v["symbol"], v["reason_codes"], v["attention_level"]) for v in d["views"]] == \
            [(v["symbol"], v["reason_codes"], v["attention_level"]) for v in base["views"]]
    for f in ("attention.py", "candidates.py", "stock.py", "history.py"):
        assert "regime" not in open(os.path.join(DESK, "services", f), encoding="utf-8").read(), f


# ------------------------------------------------------------------ 18. no trade language
def test_no_recommendation_language(client):
    texts = [*rules.RULES.values(), *rules.REGIME_RULES.values(), *rules.REASON_TEXT.values(),
             rules.CONFIRMATION_RULE, *engine.NOTES]
    for data in (_bull(), _bear(), _neutral(), _conflict()):
        s = _classify(data)
        texts += [s.explanation, *s.supporting_evidence, *s.conflicting_evidence,
                  *(d.explanation for d in s.dimensions)]
    for t in texts:
        assert not FORBIDDEN_WORDS.search(t), t
    prev = client.app.state.service.sessions()[-2]
    for url in ("/", "/regime", f"/regime?session={prev}", "/quality", "/stock/SYMA"):
        assert not FORBIDDEN_WORDS.search(re.sub(r"<[^>]+>", " ", client.get(url).text)), url


# ------------------------------------------------------------------ 19-20. boundaries
def test_public_pipeline_never_imports_the_regime():
    from test_private_desk import test_dmb_critical_path_never_imports_the_desk
    test_dmb_critical_path_never_imports_the_desk()


def test_production_stores_untouched_by_regime_and_research(desk_out, svc, client):
    before = _fingerprint(desk_out)
    prev = svc.sessions()[-2]
    for url in ("/", "/regime", f"/regime?session={prev}", "/quality", "/radar.csv"):
        assert client.get(url).status_code == 200
    from private_desk.regime.research import build
    build(desk_out, SESSION, now=NOW_AFTER)
    assert _fingerprint(desk_out) == before                         # private_desk/ excluded
    assert os.path.isfile(os.path.join(desk_out, "private_desk", "regime_research",
                                       "validation_summary.json"))


def test_regime_writes_only_inside_the_desk_folder(desk_out, tmp_path):
    from private_desk.cache import CacheWriteRefused
    from private_desk.regime import research
    from private_desk.regime.store import RegimeStore
    from private_desk.repository import DeskRepository
    with pytest.raises(CacheWriteRefused):
        RegimeStore(DeskRepository(desk_out), os.path.join(desk_out, "data"))
    with pytest.raises(CacheWriteRefused):
        research._write(os.path.join(desk_out, "data"), desk_out, "x.json", "{}")


# ------------------------------------------------------------------ 21. research proof
def test_historical_reconstruction_matches_store_cut_at_each_session(desk_out):
    from private_desk.regime.research import build
    res = build(desk_out, SESSION, now=NOW_AFTER)
    sm = res["summary"]
    proof = sm["no_lookahead_proof"]
    assert proof["checked"] == sm["sessions_total"] > 50
    assert proof["identical"] == proof["checked"] and not proof["differences"]
    assert sm["forward_returns_used"] is False
    assert set(sm["counts_by_regime"]) == set(model.REGIMES)
    for p in res["paths"]:
        assert os.path.dirname(p) == os.path.join(desk_out, "private_desk", "regime_research")


# ------------------------------------------------------------------ 22. versioned cache
def test_calculation_version_change_rebuilds_snapshots(desk_out, tmp_path, monkeypatch):
    from private_desk.regime import store as rs
    from private_desk.repository import DeskRepository
    calls = []
    real = rs.classify_history

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(rs, "classify_history", counting)
    st = rs.RegimeStore(DeskRepository(desk_out), str(tmp_path / "regime"))
    first = st.snapshots(SESSION, n=3)
    assert len(calls) == 1 and {s.calculation_version for s in first} == {model.CALCULATION_VERSION}
    st.snapshots(SESSION, n=3)
    assert len(calls) == 1                                         # served from the store
    monkeypatch.setattr(model, "CALCULATION_VERSION", "regime-test-v2")
    second = st.snapshots(SESSION, n=3)
    assert len(calls) == 2 and {s.calculation_version for s in second} == {"regime-test-v2"}
    assert len(st.stored_versions()) == 2
    # an input change (new OHLCV / Market Structure) also invalidates
    ms = os.path.join(desk_out, "market_structure", f"market_structure_{SESSION}.json")
    os.utime(ms, (os.path.getatime(ms), os.path.getmtime(ms) + 5))
    st.snapshots(SESSION, n=3)
    assert len(calls) == 3


# ------------------------------------------------------------------ 23. data quality
def test_data_quality_reports_regime_freshness(svc, client, monkeypatch):
    q = svc.quality(SESSION)["regime"]
    assert q["status"] in ("OK", "INSUFFICIENT_DATA") and q["stale"] is False
    assert q["calculation_version"] == model.CALCULATION_VERSION
    assert set(q["available"]) | set(q["unavailable"]) == set(rules.DIMENSION_ORDER)
    html = client.get("/quality").text
    assert "Market regime classifier" in html and model.CALCULATION_VERSION in html
    import config
    monkeypatch.setattr(config, "now_ist", lambda: dt.datetime(2026, 9, 23, 20, 0,
                                                               tzinfo=NOW_AFTER.tzinfo))
    later = svc.quality(SESSION)["regime"]
    assert later["stale"] is True and later["status"] == "STALE"


# ------------------------------------------------------------------ 24. packet stays context
def test_candidate_packet_carries_regime_context_only(svc):
    from private_desk.packet import FORBIDDEN_FIELD_TOKENS, SCHEMA_VERSION
    packets = svc.packets(SESSION)
    g = svc.regime(SESSION)["snapshot"]
    assert SCHEMA_VERSION == "private-candidate-packet-1.1"
    for p in packets:
        mr = p.to_dict()["market_regime"]
        assert mr["label"] == g.regime and mr["session_date"] == SESSION.isoformat()
        assert mr["calculation_version"] == model.CALCULATION_VERSION
        for key in list(mr) + list(mr["dimensions"]):
            assert not set(key.lower().split("_")) & set(FORBIDDEN_FIELD_TOKENS), key


def test_snapshot_has_no_order_fields():
    from private_desk.packet import FORBIDDEN_FIELD_TOKENS
    for cls in (model.MarketRegimeSnapshot, model.RegimeDimension):
        for name in cls.__dataclass_fields__:
            assert not set(name.lower().split("_")) & set(FORBIDDEN_FIELD_TOKENS), name
