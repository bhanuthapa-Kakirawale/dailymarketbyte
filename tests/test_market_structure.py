"""Packet B: Market Structure aggregator - universe handling, coverage, sector mapping,
reconciliation, editorial selection and the public boundary."""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

import market_structure as ms
from market_structure.store import structure_facts
from publication import PublicationGate, scan_public_text

D, P = dt.date(2026, 9, 25), dt.date(2026, 9, 24)
INDUSTRIES = ["Healthcare", "Financial Services", "Automobile and Auto Components",
              "Information Technology", "Capital Goods"]


def csv_text(n, prefix="STOCK", industries=INDUSTRIES, extra=()):
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for i in range(n):
        lines.append(f"Demo {prefix} {i} Ltd.,{industries[i % len(industries)]},{prefix}-{i:03d},EQ,X{i}")
    lines += list(extra)
    return "\n".join(lines)


def universe(n=200, index="NIFTY200", prefix="STOCK", **kw):
    return ms.from_constituent_csv(csv_text(n, prefix, **kw), index, "https://example.invalid/"
                                   f"ind_{index.lower()}list.csv", "2026-09-25T19:30:00+05:30")


def detectors(uni, unusual=(), up=(), down=(), missing_volume=(), missing_tech=(), moves=None,
              stale=()):
    syms = sorted(uni.constituents)
    vol = NS(scanned=[s for s in syms if s not in missing_volume],
             anomalies=[NS(instrument=s, relative_volume=3.1) for s in unusual])
    ev = lambda names: [NS(event_type=NS(value=n)) for n in names]
    flagged = ([NS(instrument=s, events=ev(["BREAK_ABOVE_20D_RANGE"])) for s in up] +
               [NS(instrument=s, events=ev(["BREAK_BELOW_20D_RANGE"])) for s in down])
    tech = NS(scanned=[s for s in syms if s not in missing_tech], flagged=flagged)
    moves = moves or {}
    series = {s: [{"date": P, "close": 100.0},
                  {"date": D if s not in stale else P,
                   "close": 100.0 + moves.get(s, 0.5 if i % 2 else -0.5)}]
              for i, s in enumerate(syms)}
    return vol, tech, series


def snapshot(uni, nifty100=None, **kw):
    vol, tech, series = detectors(uni, **kw)
    obs = ms.build_observations(uni, D, P, vol, tech, series)
    return ms.aggregate(obs, uni, D, [nifty100] if nifty100 else []), obs


def hc(n):   # the first n Healthcare constituents (every 5th symbol)
    return [f"STOCK-{i:03d}" for i in range(0, 5 * n, 5)]


# --------------------------------------------------------------------------- universe
def test_universe_is_explicit_and_official():
    uni = universe()
    uni.require_official()
    assert uni.label == "NIFTY 200" and uni.size == 200
    snap, _ = snapshot(uni)
    assert snap.universe == "NIFTY200" and snap.universe_label == "NIFTY 200"
    assert snap.metric("UNUSUAL_VOLUME").universe_size == 200


def test_fallback_list_can_never_define_a_universe():
    meta = {"index": "NIFTY200", "source": "BUILTIN_NIFTY50_FALLBACK", "source_reference": "x",
            "companies": {f"S{i}": "x" for i in range(50)}, "industries": {}}
    with pytest.raises(ms.UniverseError):
        ms.from_market_meta(meta).require_official()


def test_wrong_constituent_count_is_refused():
    with pytest.raises(ms.UniverseError):
        universe(n=180).require_official()


def test_nifty100_and_nifty200_observations_cannot_mix():
    u200, u100 = universe(), universe(100, "NIFTY100")
    vol, tech, series = detectors(u100)
    obs100 = ms.build_observations(u100, D, P, vol, tech, series)
    with pytest.raises(ValueError, match="never mixed"):
        ms.aggregate(obs100, u200, D)


def test_observations_only_for_constituents():
    uni = universe()
    vol, tech, series = detectors(uni)
    vol.scanned.append("OUTSIDER")
    vol.anomalies.append(NS(instrument="OUTSIDER", relative_volume=9.0))
    obs = ms.build_observations(uni, D, P, vol, tech, series)
    assert len(obs) == 200 and all(o.symbol != "OUTSIDER" for o in obs)


# --------------------------------------------------------------------------- reconciliation
def test_aggregates_and_sector_totals_reconcile():
    uni = universe()
    unusual = hc(6) + ["STOCK-001", "STOCK-006", "STOCK-011", "STOCK-002", "STOCK-003"]
    snap, obs = snapshot(uni, unusual=unusual)
    m = snap.metric("UNUSUAL_VOLUME")
    assert m.numerator == 11 and m.denominator == 200 and m.denominator_text == "11 / 200"
    assert sum(m.by_sector.values()) == 11 and m.by_sector["Healthcare"] == 6
    assert len(m.observation_ids) == 11
    hit = {o.observation_id for o in obs if o.unusual_volume}
    assert set(m.observation_ids) == hit
    adv, dec = snap.metric("ADVANCES"), snap.metric("DECLINES")
    assert adv.numerator + dec.numerator <= adv.denominator == 200


def test_unknown_industry_is_unclassified_deterministically():
    uni = universe(industries=["Healthcare", "Quantum Widgets"])
    assert uni.constituents["STOCK-001"].sector == ms.UNCLASSIFIED
    snap, _ = snapshot(uni, unusual=["STOCK-001", "STOCK-003", "STOCK-000"])
    assert snap.metric("UNUSUAL_VOLUME").by_sector == {"Unclassified": 2, "Healthcare": 1}
    assert "STOCK-001" in snap.unclassified
    assert ms.sector_for("Quantum Widgets") == ms.sector_for(None) == "Unclassified"


def test_tied_sectors_never_claim_concentration():
    tie = hc(3) + ["STOCK-001", "STOCK-006", "STOCK-011"] + [f"STOCK-{i:03d}" for i in (2, 3, 4)]
    snap, _ = snapshot(universe(), unusual=tie)
    ins, _ = ms.select_insights(snap, nifty_pct=0.1)
    uv = next(i for i in ins if i.kind == "UNUSUAL_VOLUME")
    assert snap.metric("UNUSUAL_VOLUME").top_sector() is None
    assert uv.takeaway == "" and "concentrated" not in uv.headline


def test_healthcare_is_never_relabelled_pharma():
    assert ms.sector_for("Healthcare") == "Healthcare"


# --------------------------------------------------------------------------- coverage
def test_full_coverage_is_publishable():
    snap, _ = snapshot(universe(), unusual=hc(6))
    assert snap.metric("UNUSUAL_VOLUME").status == ms.PUBLISHABLE


def test_partial_coverage_shows_the_real_denominator():
    snap, _ = snapshot(universe(), unusual=hc(6), missing_volume=["STOCK-199", "STOCK-198",
                                                                  "STOCK-197", "STOCK-196"])
    m = snap.metric("UNUSUAL_VOLUME")
    assert m.status == ms.PARTIAL and m.denominator == 196
    assert m.denominator_text == "6 of 196 covered (200 in index)"


def test_low_coverage_is_suppressed_never_scaled():
    missing = [f"STOCK-{i:03d}" for i in range(180, 200)]
    snap, _ = snapshot(universe(), unusual=hc(8), missing_volume=missing)
    m = snap.metric("UNUSUAL_VOLUME")
    assert m.status == ms.SUPPRESSED and m.denominator == 180
    ins, _ = ms.select_insights(snap)
    assert all(i.kind != "UNUSUAL_VOLUME" for i in ins)


def test_undated_price_change_is_not_covered():
    snap, obs = snapshot(universe(), stale=["STOCK-000"])
    assert snap.metric("ADVANCES").denominator == 199
    assert not next(o for o in obs if o.symbol == "STOCK-000").breadth_covered


# --------------------------------------------------------------------------- editorial
def test_nothing_meaningful_means_no_section():
    snap, _ = snapshot(universe(), unusual=hc(2))
    ins, reasons = ms.select_insights(snap, nifty_pct=0.05)
    assert ins == [] and all(r.startswith("omitted") for r in reasons.values())


def test_unusual_volume_concentration_uses_exact_share_only():
    unusual = hc(6) + [f"STOCK-{i:03d}" for i in (1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9, 14)]
    snap, _ = snapshot(universe(), unusual=unusual)
    ins, _ = ms.select_insights(snap, nifty_pct=0.1)
    uv = next(i for i in ins if i.kind == "UNUSUAL_VOLUME")
    assert uv.hero_value == "18 / 200"
    assert uv.takeaway == "Healthcare accounted for one-third of them."
    assert sum(r["n"] for r in uv.rows) == 18
    assert "NIFTY 200" in uv.hero_label
    assert ms.exact_share(6, 17) is None and ms.exact_share(6, 18) == "one-third"


def test_breadth_divergence_is_selected_first():
    moves = {f"STOCK-{i:03d}": (-1.0 if i < 130 else 1.0) for i in range(200)}
    snap, _ = snapshot(universe(), unusual=hc(6), moves=moves)
    ins, _ = ms.select_insights(snap, nifty_pct=0.42)
    assert ins[0].kind == "BREADTH"
    assert ins[0].headline == "Nifty 50 rose 0.42%, but most NIFTY 200 stocks fell"
    assert ins[0].hero_value == "130 / 200"
    assert len(ins) <= ms.editorial.MAX_INSIGHTS


def test_range_events_counted_by_side():
    up = [f"STOCK-{i:03d}" for i in range(12)]
    snap, _ = snapshot(universe(), up=up, down=["STOCK-100", "STOCK-101", "STOCK-102"])
    ins, _ = ms.select_insights(snap, nifty_pct=0.05)
    r = next(i for i in ins if i.kind == "RANGE")
    assert r.headline == "12 NIFTY 200 stocks closed above their 20-day range"
    assert r.takeaway.startswith("3 closed below")


def test_nifty100_vs_nifty200_contrast_only_for_a_true_subset():
    u200 = universe()
    u100 = ms.from_constituent_csv(
        "\n".join(csv_text(200).splitlines()[:101]), "NIFTY100", "x", None)
    snap, _ = snapshot(u200, nifty100=u100, unusual=hc(6))
    sub = snap.subsets["NIFTY100"]
    assert sub["status"] == ms.PUBLISHABLE
    assert sub["subset"]["universe_size"] == 100 and sub["rest"]["universe_size"] == 100
    other = universe(100, "NIFTY100", prefix="OTHER")
    snap2, _ = snapshot(u200, nifty100=other, unusual=hc(6))
    assert snap2.subsets["NIFTY100"]["status"] == ms.SUPPRESSED


# --------------------------------------------------------------------------- public boundary
def test_structure_facts_pass_the_public_gate_and_name_no_stock():
    uni = universe()
    snap, _ = snapshot(uni, unusual=hc(6) + ["STOCK-001"] * 0 + [f"STOCK-{i:03d}" for i in (1, 2, 3)])
    ins, _ = ms.select_insights(snap, nifty_pct=0.1)
    gate = PublicationGate("PUBLIC_UNREGISTERED", uni.companies())
    for i in ins:
        facts = structure_facts(i, snap, D)
        assert all(f.universe == "NIFTY 200" and "MARKET_STRUCTURE" in f.tags for f in facts)
        assert all(gate.admit(f) for f in facts), gate.to_dict()["blocked"]
        texts = {f.fact_id: f.text for f in facts}
        assert scan_public_text(texts, uni.companies()).check_passed("unapproved_named_security")


def test_store_round_trip_and_tamper_detection(tmp_path):
    uni = universe()
    snap, obs = snapshot(uni, unusual=hc(6))
    path = ms.save_snapshot(snap, obs, uni, str(tmp_path))
    snap2, uni2, obs2 = ms.load_snapshot(path)
    assert snap2.metric("UNUSUAL_VOLUME").numerator == 6 and uni2.size == 200
    d = json.load(open(path, encoding="utf-8"))
    d["snapshot"]["metrics"]["UNUSUAL_VOLUME"]["numerator"] = 60
    json.dump(d, open(path, "w", encoding="utf-8"))
    with pytest.raises(ms.ReconciliationError):
        ms.load_snapshot(path)


def test_private_radar_detectors_are_unchanged_by_aggregation():
    uni = universe()
    vol, tech, series = detectors(uni, unusual=hc(3))
    before = (list(vol.scanned), [a.instrument for a in vol.anomalies], len(tech.flagged))
    ms.aggregate(ms.build_observations(uni, D, P, vol, tech, series), uni, D)
    assert before == (list(vol.scanned), [a.instrument for a in vol.anomalies], len(tech.flagged))
