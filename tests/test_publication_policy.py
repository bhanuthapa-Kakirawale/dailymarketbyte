"""Packet A: publication profiles, typed classification, the PUBLIC_UNREGISTERED policy, the
publication audit and the upload hard-block."""
import datetime as dt

import pytest

from publication import (ContentClass, Orientation, Origin, ProfileNotAvailable,
                         PublicationBlocked, PublicationGate, PublicationProfile, PublishableFact,
                         RightsStatus, Scope, build_publication_audit, evaluate,
                         require_publication_pass, resolve_profile, scan_public_text)
from publication import classify as C
from publication.disclaimer import DESCRIPTION, ON_SCREEN
from publication.rights import rights_for

D = dt.date(2026, 9, 25)
KNOWN = {"MANKIND": "Mankind Pharma Ltd.", "RELIANCE": "Reliance Industries Ltd.",
         "KAYNES": "Kaynes Technology India Ltd."}


def fact(**kw):
    base = dict(fact_id="f", text="Nifty 50 closed 0.42% higher.", scope=Scope.INDEX,
                origin=Origin.MARKET_DATA, content_class=ContentClass.MARKET_AGGREGATE,
                orientation=Orientation.HISTORICAL, source_name="nse_website",
                source_label="NSE", source_reference="https://www.nseindia.com", data_as_of=D,
                publication_rights_status=RightsStatus.REVIEW_REQUIRED)
    base.update(kw)
    return PublishableFact(**base)


def official_event(**kw):
    base = dict(fact_id="ban.KAYNES", text="KAYNES F&O BAN", scope=Scope.SECURITY,
                origin=Origin.OFFICIAL_EXCHANGE, content_class=ContentClass.EXCHANGE_EVENT,
                orientation=Orientation.CURRENT_FACT, source_name="nse_fo_secban",
                source_label="NSE",
                source_reference="https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
                data_as_of=D, security="KAYNES",
                publication_rights_status=RightsStatus.REVIEW_REQUIRED)
    base.update(kw)
    return fact(**base)


# --------------------------------------------------------------------------- profiles
def test_public_unregistered_is_the_default(monkeypatch):
    monkeypatch.delenv("PUBLICATION_PROFILE", raising=False)
    assert resolve_profile() is PublicationProfile.PUBLIC_UNREGISTERED
    assert PublicationGate().profile is PublicationProfile.PUBLIC_UNREGISTERED


def test_ra_registered_profile_is_reserved_and_unselectable(monkeypatch):
    with pytest.raises(ProfileNotAvailable):
        resolve_profile("PUBLIC_RA_REGISTERED")
    monkeypatch.setenv("PUBLICATION_PROFILE", "PUBLIC_RA_REGISTERED")
    with pytest.raises(ProfileNotAvailable):
        resolve_profile()


def test_unknown_profile_name_is_an_error_not_a_fallback():
    with pytest.raises(ValueError):
        resolve_profile("PUBLIC")


# --------------------------------------------------------------------------- policy
def test_index_fact_is_allowed_with_review_required_note():
    d = evaluate(fact(), "PUBLIC_UNREGISTERED")
    assert d.allowed and any("RIGHTS_REVIEW_REQUIRED" in n for n in d.notes)


def test_named_stock_radar_analysis_is_blocked_publicly_but_kept_privately():
    f = C.radar_story_fact("MANKIND", "Closed above its 20-day high with 4.1x normal volume.", D)
    pub = evaluate(f, "PUBLIC_UNREGISTERED", KNOWN)
    assert not pub.allowed
    assert "SECURITY_TECHNICAL_ANALYSIS" in pub.reasons
    assert "SECURITY_WITHOUT_OFFICIAL_EVENT" in pub.reasons
    assert evaluate(f, "PRIVATE_ANALYTICS", KNOWN).allowed


def test_removing_the_price_does_not_make_technical_analysis_safe():
    f = C.radar_story_fact("MANKIND", "Trading range has tightened.", D)
    assert not evaluate(f, "PUBLIC_UNREGISTERED", KNOWN).allowed


def test_a_disclaimer_cannot_override_a_block():
    f = C.radar_story_fact("MANKIND", "Closed above its 20-day high. " + ON_SCREEN + ". "
                           + DESCRIPTION, D)
    d = evaluate(f, "PUBLIC_UNREGISTERED", KNOWN)
    assert not d.allowed
    # and a disclaimer on an otherwise-blocked recommendation line changes nothing
    g = fact(text="Buy Reliance before results. Not investment advice.")
    assert not evaluate(g, "PUBLIC_UNREGISTERED", KNOWN).allowed


def test_top_mover_ranking_is_blocked_publicly():
    f = C.mover_fact("RELIANCE", "RELIANCE +3.2% - top gainer", D)
    d = evaluate(f, "PUBLIC_UNREGISTERED", KNOWN)
    assert not d.allowed and "SECURITY_RANKING" in d.reasons


def test_stock_watch_is_blocked_publicly():
    f = C.stock_watch_fact("MANKIND", "Thursday: Price closed above its recent range.", D)
    assert not evaluate(f, "PUBLIC_UNREGISTERED", KNOWN).allowed


def test_official_exchange_event_may_name_a_security():
    d = evaluate(official_event(), "PUBLIC_UNREGISTERED", KNOWN)
    assert d.allowed, d.reasons


def test_official_event_without_reference_is_blocked():
    d = evaluate(official_event(source_reference=""), "PUBLIC_UNREGISTERED", KNOWN)
    assert "OFFICIAL_REFERENCE_MISSING" in d.reasons


def test_official_event_wording_must_stay_factual():
    d = evaluate(official_event(text="KAYNES in F&O ban - expected fall, avoid"),
                 "PUBLIC_UNREGISTERED", KNOWN)
    assert not d.allowed
    d = evaluate(official_event(text="KAYNES F&O ban after its breakout"), "PUBLIC_UNREGISTERED",
                 KNOWN)
    assert "LANGUAGE_SECURITY_SPECIFIC_TECHNICAL_ANALYSIS" in d.reasons


def test_ai_is_never_a_published_source():
    d = evaluate(fact(origin=Origin.AI), "PUBLIC_UNREGISTERED")
    assert "AI_NOT_A_SOURCE" in d.reasons


def test_news_headline_is_not_a_published_fact():
    ev = C.report_event_fact({"tag": "RESULTS", "text": "XYZ Q2 results today",
                              "origin": "GOOGLE_NEWS_RSS", "source": "google_news_rss"}, D)
    assert "NEWS_NOT_A_SOURCE" in evaluate(ev, "PUBLIC_UNREGISTERED").reasons
    rule = C.report_event_fact({"tag": "F&O", "text": "Nifty weekly F&O expiry",
                                "origin": "RULE_FNO_EXPIRY", "source": "expiry_calendar_rule"}, D)
    assert evaluate(rule, "PUBLIC_UNREGISTERED").allowed


def test_forward_looking_is_blocked():
    d = evaluate(fact(orientation=Orientation.FORWARD_LOOKING), "PUBLIC_UNREGISTERED")
    assert "FORWARD_LOOKING" in d.reasons
    d = evaluate(fact(text="Nifty is likely to open higher."), "PUBLIC_UNREGISTERED")
    assert "LANGUAGE_FORECAST_LANGUAGE" in d.reasons


def test_market_aggregate_needs_universe():
    d = evaluate(fact(text="18 stocks showed unusual volume.", scope=Scope.MARKET,
                      source_name="daily_byte_market_structure",
                      tags=frozenset({"MARKET_STRUCTURE"}),
                      publication_rights_status=RightsStatus.APPROVED), "PUBLIC_UNREGISTERED")
    assert "UNIVERSE_MISSING" in d.reasons
    ok = evaluate(fact(text="NIFTY 200 · 18 of 200 stocks showed unusual volume.",
                       scope=Scope.MARKET, universe="NIFTY 200",
                       tags=frozenset({"MARKET_STRUCTURE"}),
                       source_name="daily_byte_market_structure",
                       publication_rights_status=RightsStatus.APPROVED), "PUBLIC_UNREGISTERED",
                  KNOWN)
    assert ok.allowed, ok.reasons


def test_data_as_of_is_required():
    assert "DATA_AS_OF_MISSING" in evaluate(fact(data_as_of=None), "PUBLIC_UNREGISTERED").reasons


def test_gmp_is_blocked_by_tag_and_by_text():
    ipo = dict(scope=Scope.IPO, origin=Origin.OFFICIAL_EXCHANGE,
               content_class=ContentClass.IPO_EVENT, security="ABC LTD",
               source_name="nse_ipo_issues")
    assert "GMP" in evaluate(fact(text="ABC LTD", tags=frozenset({"GMP"}), **ipo),
                             "PUBLIC_UNREGISTERED").reasons
    assert not evaluate(fact(text="ABC LTD GMP Rs 40", **ipo), "PUBLIC_UNREGISTERED").allowed


def test_ipo_recommendations_are_blocked():
    ipo = dict(scope=Scope.IPO, origin=Origin.OFFICIAL_EXCHANGE,
               content_class=ContentClass.IPO_EVENT, security="ABC LTD",
               source_name="nse_ipo_issues")
    for text in ("Apply for ABC LTD", "Avoid this IPO", "Expected listing gain 30%",
                 "Best IPO of the week", "Fair value Rs 500", "Strong listing expected",
                 "Subscribe to the IPO"):
        assert not evaluate(fact(text=text, **ipo), "PUBLIC_UNREGISTERED").allowed, text
    assert evaluate(fact(text="ABC LTD closes today. Price band Rs 258 to Rs 272.", **ipo),
                    "PUBLIC_UNREGISTERED").allowed


def test_ipo_facts_must_be_official():
    d = evaluate(fact(text="ABC LTD", scope=Scope.IPO, origin=Origin.NEWS,
                      content_class=ContentClass.IPO_EVENT, security="ABC LTD"),
                 "PUBLIC_UNREGISTERED")
    assert "IPO_UNOFFICIAL_SOURCE" in d.reasons


# --------------------------------------------------------------------------- rights
def test_rights_registry_never_upgrades_unreviewed_sources():
    assert rights_for("nse_website").status is RightsStatus.REVIEW_REQUIRED
    assert rights_for("nse_fo_secban").status is RightsStatus.REVIEW_REQUIRED
    assert rights_for("gemini").status is RightsStatus.RESTRICTED
    assert rights_for("nseix_market_rate").status is RightsStatus.RESTRICTED
    assert rights_for("no_such_source").status is RightsStatus.UNKNOWN
    assert rights_for("daily_byte_market_structure").status is RightsStatus.APPROVED


def test_restricted_and_unknown_rights_block():
    assert "RIGHTS_RESTRICTED" in evaluate(
        fact(publication_rights_status=RightsStatus.RESTRICTED), "PUBLIC_UNREGISTERED").reasons
    assert "RIGHTS_UNKNOWN" in evaluate(
        fact(publication_rights_status=RightsStatus.UNKNOWN), "PUBLIC_UNREGISTERED").reasons


def test_review_required_default_is_block_and_is_enforced_at_the_audit(monkeypatch):
    from publication.claims import decision_for
    from publication.rights import review_required_policy
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)
    assert review_required_policy() == "BLOCK"                     # conservative default
    # the gate keeps the fact (review renders stay complete) and records the verdict ...
    d = evaluate(fact(), "PUBLIC_UNREGISTERED")
    assert d.allowed and any("production publication BLOCKED" in n for n in d.notes)
    # ... production publication is refused per displayed claim
    assert decision_for("REVIEW_REQUIRED") == "BLOCKED_RIGHTS_REVIEW_REQUIRED"
    assert decision_for("APPROVED") == "PUBLISHABLE"
    monkeypatch.setenv("PUBLIC_REVIEW_REQUIRED_POLICY", "ATTRIBUTED_EOD")   # explicit owner call
    assert decision_for("REVIEW_REQUIRED") == "PUBLISHABLE_WITH_ATTRIBUTION"
    monkeypatch.setenv("PUBLIC_REVIEW_REQUIRED_POLICY", "WHATEVER")         # fails closed
    assert decision_for("REVIEW_REQUIRED") == "BLOCKED_RIGHTS_REVIEW_REQUIRED"
    for st in ("RESTRICTED", "UNKNOWN"):
        assert decision_for(st).startswith("BLOCKED")
    # live readings are never covered, whatever the policy
    assert not evaluate(fact(tags=frozenset({"LIVE"})), "PUBLIC_UNREGISTERED").allowed


# --------------------------------------------------------------------------- language
def test_english_only_scan():
    s = scan_public_text({"a": "निफ्टी 50 बढ़ा"})
    assert not s.check_passed("english_only")
    s = scan_public_text({"a": "Aaj market mein tezi hai"})
    assert not s.check_passed("english_only")
    s = scan_public_text({"a": "Nifty was quiet, but activity underneath was not. ₹1,200 cr · 4.8×"})
    assert s.passed, s.issues


def test_named_security_with_technical_term_is_flagged_even_if_approved():
    s = scan_public_text({"x": "KAYNES broke out above resistance"}, KNOWN, {"KAYNES"})
    assert not s.check_passed("security_specific_technical_analysis")
    s = scan_public_text({"x": "Mankind Pharma showed unusual volume"}, KNOWN)
    assert not s.check_passed("security_specific_technical_analysis")
    assert not s.check_passed("unapproved_named_security")


def test_index_technical_context_is_allowed():
    s = scan_public_text({"x": "Nifty closed below its 20-day low on Thursday."}, KNOWN)
    assert s.passed, s.issues


@pytest.mark.parametrize("title", ["Top Stocks to Buy Today", "Stock to Watch: XYZ",
                                   "Breakout Stock Alert", "Bullish on RELIANCE",
                                   "Apply IPO now", "Multibagger alert", "Best IPO this week",
                                   "Listing Gain Expected", "Target 1500"])
def test_blocked_title_wording(title):
    assert not scan_public_text({"title": title}, KNOWN).passed


@pytest.mark.parametrize("title", ["What Changed Inside India's Market Today?",
                                   "Nifty Was Quiet - But Market Activity Wasn't",
                                   "Three Exchange Updates Before the Bell",
                                   "Where Was Unusual Volume Concentrated Today?",
                                   "Two IPOs Close Today - Here Are the Official Numbers"])
def test_allowed_title_wording(title):
    s = scan_public_text({"title": title}, KNOWN)
    assert s.passed, s.issues


# --------------------------------------------------------------------------- audit + upload block
def _audit(tmp_path, texts, scenes=None, gate=None, video=True, **kw):
    gate = gate or PublicationGate("PUBLIC_UNREGISTERED", KNOWN)
    gate.admit(fact())
    path = None
    if video:
        path = tmp_path / "v.mp4"
        path.write_bytes(b"fake video")
        path = str(path)
    kw.setdefault("claims", [])            # a trivially complete displayed-claim trace
    kw.setdefault("scene_texts", {})
    return build_publication_audit(gate=gate, product="POST", session_date=D, public_text=texts,
                                   scenes=scenes or [], video_path=path, **kw), path


def test_audit_passes_clean_public_video(tmp_path):
    audit, path = _audit(tmp_path, {"a": "Nifty 50 closed 0.42% higher."},
                         [{"kind": "PULSE", "requires_provenance": True,
                           "provenance": {"source": "NSE", "data_as_of": "25 SEP 2026"}}],
                         metadata={"title": "What Changed Inside India's Market Today?",
                                   "description": "Nifty 50 closed higher.\n\n" + DESCRIPTION})
    assert audit["final"] == "PASS", audit["failed_checks"]
    assert audit["ipo"]["gmp_present"] is False
    assert require_publication_pass(audit, path)["final"] == "PASS"


def test_audit_blocks_and_upload_is_refused(tmp_path):
    audit, path = _audit(tmp_path, {"a": "Buy MANKIND - breakout confirmed"})
    assert audit["final"] == "BLOCK"
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, path)


def test_audit_blocks_on_missing_visible_source(tmp_path):
    audit, _ = _audit(tmp_path, {"a": "Nifty 50 closed higher."},
                      [{"kind": "PULSE", "requires_provenance": True, "provenance": {}}])
    assert "source_visibility" in audit["failed_checks"]


def test_audit_blocks_on_missing_universe(tmp_path):
    audit, _ = _audit(tmp_path, {"a": "18 stocks showed unusual volume"},
                      [{"kind": "STRUCTURE", "universe_required": True, "universe": {}}])
    assert "universe_visibility" in audit["failed_checks"]


def test_audit_blocks_gmp(tmp_path):
    audit, _ = _audit(tmp_path, {"a": "ABC LTD GMP is Rs 40"})
    assert audit["ipo"]["gmp_present"] is True and audit["final"] == "BLOCK"


def test_description_disclaimer_is_exempt_only_verbatim(tmp_path):
    audit, _ = _audit(tmp_path, {"a": "Nifty 50 closed higher."},
                      metadata={"description": "Nifty closed higher.\n" + DESCRIPTION})
    assert audit["final"] == "PASS", audit["failed_checks"]
    audit, _ = _audit(tmp_path, {"a": "Nifty 50 closed higher."},
                      metadata={"description": "Not advice, but buy the dip."})
    assert audit["final"] == "BLOCK"


def test_upload_block_requires_matching_video(tmp_path):
    audit, path = _audit(tmp_path, {"a": "Nifty 50 closed higher."})
    other = tmp_path / "other.mp4"
    other.write_bytes(b"a different video")
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, str(other))
    with pytest.raises(PublicationBlocked):
        require_publication_pass(None, path)
    with pytest.raises(PublicationBlocked):
        require_publication_pass(str(tmp_path / "missing.json"), path)


def test_private_profile_can_never_pass_for_upload(tmp_path):
    gate = PublicationGate("PRIVATE_ANALYTICS", KNOWN)
    gate.admit(C.radar_story_fact("MANKIND", "Closed above its 20-day high", D))
    audit, path = _audit(tmp_path, {"a": "Nifty"}, gate=gate)
    assert audit["final"] == "BLOCK" and "profile" in audit["failed_checks"]
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, path)


def test_synthetic_audit_can_never_upload(tmp_path):
    audit, path = _audit(tmp_path, {"a": "Nifty 50 closed higher."}, synthetic=True)
    with pytest.raises(PublicationBlocked):
        require_publication_pass(audit, path)


def test_gate_records_why_a_named_security_is_allowed():
    gate = PublicationGate("PUBLIC_UNREGISTERED", KNOWN)
    assert gate.admit(official_event())
    assert not gate.admit(C.radar_story_fact("MANKIND", "Closed above its 20-day high", D))
    named = gate.named_securities()
    assert list(named) == ["KAYNES"] and "EXCHANGE_EVENT" in named["KAYNES"][0]
    assert gate.to_dict()["block_reasons"]["SECURITY_TECHNICAL_ANALYSIS"] == 1
