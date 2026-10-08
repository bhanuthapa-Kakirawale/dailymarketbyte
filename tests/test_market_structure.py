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


def snapshot(uni, nifty100=None, fiftytwo=None, **kw):
    vol, tech, series = detectors(uni, **kw)
    obs = ms.build_observations(uni, D, P, vol, tech, series, fiftytwo)
    return ms.aggregate(obs, uni, D, [nifty100] if nifty100 else []), obs


def hc(n):   # the first n Healthcare constituents (every 5th symbol)
    return [f"STOCK-{i:03d}" for i in range(0, 5 * n, 5)]


def fw_series(prior_closes, final_close, final_date=D, start=None):
    """A deterministic oldest-first close series ending at `final_date`: `len(prior_closes)`
    prior sessions (one calendar day apart, starting `start` or 253 days before `final_date`)
    followed by the session being evaluated."""
    n = len(prior_closes)
    start = start or (final_date - dt.timedelta(days=n))
    rows = [{"date": start + dt.timedelta(days=i), "close": c} for i, c in enumerate(prior_closes)]
    rows.append({"date": final_date, "close": final_close})
    return rows


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


# ------------------------------------------ point-in-time revisioning (mirrors institutional_
# flows'/market_events' P2F.1 pattern, extended to a store that previously had no revisioning
# at all): the mutable "current" file keeps its unchanged path/semantics for every existing
# live reader; an immutable `.revN.json` chain alongside it is the only thing a replay may trust.
def test_save_snapshot_same_content_rebuild_is_a_true_noop(tmp_path):
    from market_structure.store import _existing_revisions

    uni1 = universe()
    snap1, obs1 = snapshot(uni1, unusual=hc(6))
    path = ms.save_snapshot(snap1, obs1, uni1, str(tmp_path))
    before = json.load(open(path, encoding="utf-8"))

    uni2 = universe()
    uni2.retrieved_at = "2026-09-25T20:15:00+05:30"   # rebuilt later, identical constituents
    snap2, obs2 = snapshot(uni2, unusual=hc(6))        # identical content
    ms.save_snapshot(snap2, obs2, uni2, str(tmp_path))
    after = json.load(open(path, encoding="utf-8"))

    assert len(_existing_revisions(str(tmp_path), D)) == 1   # no new revision created
    assert before == after                                    # current file untouched


def test_save_snapshot_changed_content_writes_a_new_revision(tmp_path):
    from market_structure.store import _existing_revisions

    uni1 = universe()
    snap1, obs1 = snapshot(uni1, unusual=hc(6))
    path = ms.save_snapshot(snap1, obs1, uni1, str(tmp_path))
    rev1_path = _existing_revisions(str(tmp_path), D)[0]
    rev1_before = json.load(open(rev1_path, encoding="utf-8"))

    uni2 = universe()
    uni2.retrieved_at = "2026-09-25T20:15:00+05:30"
    snap2, obs2 = snapshot(uni2, unusual=hc(3))        # genuinely different content
    ms.save_snapshot(snap2, obs2, uni2, str(tmp_path))

    revs = _existing_revisions(str(tmp_path), D)
    assert len(revs) == 2
    rev1_after = json.load(open(revs[0], encoding="utf-8"))
    assert rev1_after == rev1_before                   # the original revision is byte-unchanged

    current = json.load(open(path, encoding="utf-8"))
    assert current["snapshot"]["metrics"]["UNUSUAL_VOLUME"]["numerator"] == 3   # the rebuild


def test_load_revision_as_of_replay_never_leaks_a_post_cutoff_rebuild(tmp_path):
    from market_structure.store import load_revision_as_of

    uni1 = universe()
    snap1, obs1 = snapshot(uni1, unusual=hc(6))
    ms.save_snapshot(snap1, obs1, uni1, str(tmp_path))

    uni2 = universe()
    uni2.retrieved_at = "2026-09-26T09:00:00+05:30"
    snap2, obs2 = snapshot(uni2, unusual=hc(3))
    ms.save_snapshot(snap2, obs2, uni2, str(tmp_path))

    between = load_revision_as_of(str(tmp_path), D, "2026-09-26T00:00:00+05:30")
    assert between["snapshot"]["metrics"]["UNUSUAL_VOLUME"]["numerator"] == 6   # rev 1, not 2

    after = load_revision_as_of(str(tmp_path), D, "2026-09-27T00:00:00+05:30")
    assert after["snapshot"]["metrics"]["UNUSUAL_VOLUME"]["numerator"] == 3     # rev 2 now active

    before_any = load_revision_as_of(str(tmp_path), D, "2026-09-25T19:30:00+05:30")
    assert before_any is None   # strictly before rev 1's own retrieved_at - not yet known at all


def test_load_revision_as_of_falls_back_to_the_bare_legacy_file_when_no_chain_exists(tmp_path):
    import os as _os
    from market_structure.store import artifact_path, load_revision_as_of

    path = artifact_path(str(tmp_path), D)
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    art = {"snapshot": {"session_date": D.isoformat(),
                        "universe_source": {"retrieved_at": "2026-09-25T19:30:00+05:30"}}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(art, fh)

    assert load_revision_as_of(str(tmp_path), D, "2026-09-25T20:00:00+05:30") == art
    assert load_revision_as_of(str(tmp_path), D, "2026-09-25T19:30:00+05:30") is None


def test_save_snapshot_seeds_rev1_from_a_preexisting_legacy_bare_file(tmp_path):
    import os as _os
    from market_structure.store import _existing_revisions, artifact_path

    path = artifact_path(str(tmp_path), D)
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    legacy = {"snapshot": {"session_date": D.isoformat(), "metrics": {},
                          "universe_source": {"retrieved_at": "2026-09-25T19:30:00+05:30"}},
             "universe": {}, "subset_universes": [], "observations": []}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(legacy, fh)

    uni2 = universe()
    uni2.retrieved_at = "2026-09-26T09:00:00+05:30"
    snap2, obs2 = snapshot(uni2, unusual=hc(3))
    ms.save_snapshot(snap2, obs2, uni2, str(tmp_path))

    revs = _existing_revisions(str(tmp_path), D)
    assert len(revs) == 2
    rev1 = json.load(open(revs[0], encoding="utf-8"))
    assert rev1 == legacy   # seeded byte-identical from the pre-existing legacy file, not discarded


def test_private_radar_detectors_are_unchanged_by_aggregation():
    uni = universe()
    vol, tech, series = detectors(uni, unusual=hc(3))
    before = (list(vol.scanned), [a.instrument for a in vol.anomalies], len(tech.flagged))
    ms.aggregate(ms.build_observations(uni, D, P, vol, tech, series), uni, D)
    assert before == (list(vol.scanned), [a.instrument for a in vol.anomalies], len(tech.flagged))


# --------------------------------------------------------------------------- fifty-two-week (calc)
def test_exact_new_high_is_strict():
    r = ms.classify_fifty_two_week(fw_series([100.0] * 252, 100.01), D)
    assert r["status"] == "OK" and r["new_52w_high"] is True and r["new_52w_low"] is False


def test_equal_to_prior_high_is_not_new_high():
    r = ms.classify_fifty_two_week(fw_series([100.0] * 251 + [105.0], 105.0), D)
    assert r["new_52w_high"] is False


def test_exact_new_low_is_strict():
    r = ms.classify_fifty_two_week(fw_series([100.0] * 252, 99.99), D)
    assert r["status"] == "OK" and r["new_52w_low"] is True and r["new_52w_high"] is False


def test_equal_to_prior_low_is_not_new_low():
    r = ms.classify_fifty_two_week(fw_series([100.0] * 251 + [90.0], 90.0), D)
    assert r["new_52w_low"] is False


def test_current_bar_excluded_from_lookback():
    """A single extreme ONLY on the current bar's own prior-session slot must not count twice -
    the window is `series[:-1]`, never `series` itself."""
    closes = [100.0] * 252
    series = fw_series(closes, 100.0)             # flat window, flat close -> neither
    assert ms.classify_fifty_two_week(series, D)["new_52w_high"] is False
    # if the window wrongly included the current bar, a close of 100.01 would equal its own
    # (wrongly self-included) max and still not register as "above" - so prove the window size
    # itself: exactly 252 rows are consulted, a 253rd prior row is dropped (never widens it)
    longer = fw_series([50.0] + closes, 100.01)    # 253 prior rows; the extra one is a low of 50
    shorter = fw_series(closes, 100.01)            # 252 prior rows, all 100
    assert ms.classify_fifty_two_week(longer, D) == ms.classify_fifty_two_week(shorter, D)


def test_insufficient_history_is_classified_not_false():
    r = ms.classify_fifty_two_week(fw_series([100.0] * 251, 200.0), D)   # only 251 prior sessions
    assert r == {"status": "INSUFFICIENT_HISTORY", "new_52w_high": False, "new_52w_low": False}


def test_missing_ohlcv_is_insufficient_history():
    assert ms.classify_fifty_two_week([], D)["status"] == "INSUFFICIENT_HISTORY"
    assert ms.classify_fifty_two_week(None, D)["status"] == "INSUFFICIENT_HISTORY"


def test_duplicate_bars_never_satisfy_the_window():
    closes = [100.0] * 251 + [100.0]   # 252 rows, but the last two share a date (duplicate)
    series = fw_series(closes, 200.0)
    series[-2] = dict(series[-2], date=series[-3]["date"])   # force a duplicate date in the window
    assert ms.classify_fifty_two_week(series, D)["status"] == "INSUFFICIENT_HISTORY"


def test_session_alignment_required():
    """The series' own last row must be dated exactly `session_date` - never trusted
    positionally (mirrors `observations.py::_dated_change`'s discipline)."""
    series = fw_series([100.0] * 252, 200.0, final_date=P)   # ends at P, not D
    assert ms.classify_fifty_two_week(series, D)["status"] == "INSUFFICIENT_HISTORY"


def test_no_lookahead_a_future_row_is_never_used():
    series = fw_series([100.0] * 252, 100.01, final_date=D)
    series.append({"date": D + dt.timedelta(days=1), "close": 500.0})   # a future session
    # the series' own last row is now the future one, not D - D's own classification must not
    # silently use it
    assert ms.classify_fifty_two_week(series, D)["status"] == "INSUFFICIENT_HISTORY"


# --------------------------------------------------------------------------- fifty-two-week (aggregation)
def fw_full(uni, highs=(), lows=()):
    """A `fifty_two_week_series_by_symbol` covering EVERY constituent: `highs`/`lows` close
    strictly outside a flat 252-session prior window, everyone else stays flat (covered, but
    neither a new high nor a new low)."""
    out = {}
    for s in uni.constituents:
        if s in highs:
            out[s] = fw_series([100.0] * 252, 101.0)
        elif s in lows:
            out[s] = fw_series([100.0] * 252, 99.0)
        else:
            out[s] = fw_series([100.0] * 252, 100.0)
    return out


def test_fifty_two_week_eligible_count_and_high_low_counts():
    uni = universe()
    highs, lows = hc(6), ["STOCK-001", "STOCK-002", "STOCK-003"]
    snap, obs = snapshot(uni, fiftytwo=fw_full(uni, highs=highs, lows=lows))
    high_m, low_m = snap.metric("NEW_52W_HIGH"), snap.metric("NEW_52W_LOW")
    assert high_m.numerator == 6 and high_m.denominator == 200 and high_m.status == ms.PUBLISHABLE
    assert low_m.numerator == 3 and low_m.denominator_text == "3 / 200"
    assert {o.symbol for o in obs if o.new_52w_high} == set(highs)
    assert {o.symbol for o in obs if o.new_52w_low} == set(lows)


def test_fifty_two_week_never_shows_a_rounded_share_next_to_the_count():
    """2/200 = 1% is not an exact share (`ms.exact_share`) - the insight must never round one
    in anyway, unlike `_breadth()`'s optional exact-share suffix."""
    uni = universe()
    assert ms.exact_share(2, 200) is None
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(2) + ["STOCK-001", "STOCK-003",
                                                                "STOCK-006"]))
    ins, _ = ms.select_insights(snap, nifty_pct=0.05)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert "%" not in f.hero_value and "%" not in f.hero_label and "%" not in f.headline


def test_fifty_two_week_partial_coverage_shows_the_real_denominator():
    uni = universe()
    fw = fw_full(uni, highs=hc(6))
    for s in ("STOCK-199", "STOCK-198", "STOCK-197", "STOCK-196"):
        del fw[s]
    snap, _ = snapshot(uni, fiftytwo=fw)
    m = snap.metric("NEW_52W_HIGH")
    assert m.status == ms.PARTIAL and m.denominator == 196
    assert m.denominator_text == "6 of 196 covered (200 in index)"


def test_fifty_two_week_zero_eligible_is_never_a_valid_zero_reading():
    uni = universe()
    snap, _ = snapshot(uni)    # no `fiftytwo` at all -> every constituent uncovered
    m = snap.metric("NEW_52W_HIGH")
    assert m.numerator == 0 and m.denominator == 0 and m.status == ms.SUPPRESSED
    ins, _ = ms.select_insights(snap, nifty_pct=0.5)
    assert all(i.kind != "FIFTY_TWO_WEEK" for i in ins)


# --------------------------------------------------------------------------- fifty-two-week (editorial/divergence)
def test_fifty_two_week_below_threshold_is_omitted():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(1)))   # 1 < FIFTY_TWO_WEEK_MIN
    ins, reasons = ms.select_insights(snap, nifty_pct=0.5)
    assert all(i.kind != "FIFTY_TWO_WEEK" for i in ins)
    assert reasons["FIFTY_TWO_WEEK"].startswith("omitted")


def test_fifty_two_week_plain_count_when_no_divergence_applies():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=0.05)   # below DIVERGENCE_MIN_NIFTY_PCT
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert f.headline == "6 NIFTY 200 stocks closed at new 52-week highs"
    assert "Nifty" not in f.headline


def test_fifty_two_week_index_up_breadth_weak():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, lows=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert f.headline == ("Nifty 50 rose 0.50%, but 6 NIFTY 200 stocks closed at new "
                         "52-week lows")


def test_fifty_two_week_index_down_breadth_resilient():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=-0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert f.headline == ("Nifty 50 fell 0.50%, yet 6 NIFTY 200 stocks closed at new "
                         "52-week highs")


def test_fifty_two_week_aligned_strong():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert f.headline == ("Nifty 50 rose 0.50%, and 6 NIFTY 200 stocks closed at new "
                         "52-week highs")


def test_fifty_two_week_aligned_weak():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, lows=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=-0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert f.headline == ("Nifty 50 fell 0.50%, and 6 NIFTY 200 stocks closed at new "
                         "52-week lows")


def test_fifty_two_week_omitted_when_below_threshold_even_with_a_big_index_move():
    """Divergence framing never manufactures a story out of nothing - it only decorates an
    already-qualifying count (>= FIFTY_TWO_WEEK_MIN), never lowers the bar."""
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(1)))   # 1 < FIFTY_TWO_WEEK_MIN
    ins, _ = ms.select_insights(snap, nifty_pct=2.5)   # a large index move changes nothing here
    assert all(i.kind != "FIFTY_TWO_WEEK" for i in ins)


def test_fifty_two_week_never_bullish_bearish_buy_sell():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)))
    for nifty_pct in (None, 0.05, 0.5, -0.5):
        ins, _ = ms.select_insights(snap, nifty_pct=nifty_pct)
        f = next((i for i in ins if i.kind == "FIFTY_TWO_WEEK"), None)
        if f is None:
            continue
        texts = {f"t{i}": s for i, s in enumerate(f.public_strings())}
        result = scan_public_text(texts, uni.companies())
        assert result.check_passed("recommendation_language")
        assert result.check_passed("forecast_language")
        assert result.check_passed("ranking_language")


def test_fifty_two_week_rows_never_exceed_the_scene_row_cap():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(20)))   # every sector represented
    ins, _ = ms.select_insights(snap, nifty_pct=0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    assert len(f.rows) <= ms.editorial.SECTOR_ROWS + 1
    assert sum(r["n"] for r in f.rows) == snap.metric("NEW_52W_HIGH").numerator


def test_fifty_two_week_structure_facts_pass_the_public_gate_and_name_no_stock():
    uni = universe()
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)))
    ins, _ = ms.select_insights(snap, nifty_pct=0.5)
    f = next(i for i in ins if i.kind == "FIFTY_TWO_WEEK")
    gate = PublicationGate("PUBLIC_UNREGISTERED", uni.companies())
    facts = structure_facts(f, snap, D)
    assert all(gate.admit(fact) for fact in facts), gate.to_dict()["blocked"]
    texts = {fact.fact_id: fact.text for fact in facts}
    assert scan_public_text(texts, uni.companies()).check_passed("unapproved_named_security")


def test_fifty_two_week_outranks_unusual_volume_and_range_but_not_breadth():
    uni = universe()
    moves = {f"STOCK-{i:03d}": (-1.0 if i < 130 else 1.0) for i in range(200)}
    snap, _ = snapshot(uni, unusual=hc(6), up=[f"STOCK-{i:03d}" for i in range(12)],
                       fiftytwo=fw_full(uni, highs=hc(6)), moves=moves)
    ins, _ = ms.select_insights(snap, nifty_pct=0.42)
    assert [i.kind for i in ins] == ["BREADTH", "FIFTY_TWO_WEEK"]   # at most MAX_INSIGHTS=2


def test_allowed_kinds_restricts_pre_to_fifty_two_week_only():
    uni = universe()
    moves = {f"STOCK-{i:03d}": (-1.0 if i < 130 else 1.0) for i in range(200)}
    snap, _ = snapshot(uni, fiftytwo=fw_full(uni, highs=hc(6)), moves=moves)
    ins, reasons = ms.select_insights(snap, nifty_pct=0.42,
                                      allowed_kinds=frozenset({"FIFTY_TWO_WEEK"}))
    assert [i.kind for i in ins] == ["FIFTY_TWO_WEEK"]
    assert reasons["BREADTH"].startswith("omitted: not in this product's allowed kinds")


# --------------------------------------------------------------------------- fifty-two-week (private desk)
def test_private_desk_market_structure_surfaces_new_metrics(tmp_path):
    from private_desk.services.market import fifty_two_week_symbols, sector_table

    uni = universe()
    highs, lows = hc(6), ["STOCK-001", "STOCK-002"]
    snap, obs = snapshot(uni, fiftytwo=fw_full(uni, highs=highs, lows=lows))
    path = ms.save_snapshot(snap, obs, uni, str(tmp_path))
    data = json.load(open(path, encoding="utf-8"))
    desk_ms = {
        "status": "OK", "metrics": data["snapshot"]["metrics"],
        "constituents_by_sector": data["snapshot"]["sector_mapping"]["constituents_by_sector"],
        "observations": data["observations"],
    }
    assert desk_ms["metrics"]["NEW_52W_HIGH"]["numerator"] == 6
    assert set(fifty_two_week_symbols(desk_ms)["new_52w_high"]) == set(highs)
    assert set(fifty_two_week_symbols(desk_ms)["new_52w_low"]) == set(lows)
    rows = sector_table(desk_ms, [])
    assert sum(r["new_52w_high"] or 0 for r in rows) == 6
    assert sum(r["new_52w_low"] or 0 for r in rows) == 2
