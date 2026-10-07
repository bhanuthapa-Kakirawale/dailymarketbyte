"""Market Events Engine V1 - PRE/POST integration: section inclusion/trimming, the scene only
ever draws what it declared, policy pass-through per family, and the public-language scan still
catches recommendation language if it ever reached a Market Events text.
"""
import dataclasses
import datetime as dt

from products.pre_fixtures import synthetic_brief
from presentation.pre_plan import plan_pre_sections

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 6)


def _model(n=1):
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.watch import build_model
    evs = [MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS,
                       event_key=f"EARNINGS:SYM{i}:board1", symbol=f"SYM{i}", company=f"Co {i}",
                       status="SCHEDULED", sub_type=None, data_as_of=DAY.isoformat(),
                       source_name="nse_corp_announcements",
                       source_reference="https://nseindia.com/x",
                       facts=[{"label": "reporting_period", "value": "Q2 FY27"}],
                       status_capture=SUCCESS)
          for i in range(n)]
    return build_model(evs, "PRE")


# --------------------------------------------------------------------------- PRE section
def test_pre_market_events_section_included_when_present():
    brief = dataclasses.replace(synthetic_brief("QUIET"), market_events=_model())
    plan = plan_pre_sections(brief)
    assert "MARKET_EVENTS" in plan.order
    assert plan.market_events is not None
    assert "included" in plan.reasons["MARKET_EVENTS"]


def test_pre_market_events_section_omitted_cleanly_when_absent():
    brief = synthetic_brief("QUIET")
    assert brief.market_events is None
    plan = plan_pre_sections(brief)
    assert "MARKET_EVENTS" not in plan.order
    assert "omitted" in plan.reasons["MARKET_EVENTS"]


def test_pre_market_events_is_a_public_optional_section_trimmed_under_runtime_ceiling():
    from presentation.pre_plan import PUBLIC_OPTIONAL
    assert "MARKET_EVENTS" in PUBLIC_OPTIONAL


def test_pre_duration_is_zero_when_market_events_absent():
    brief = synthetic_brief("QUIET")
    plan = plan_pre_sections(brief)
    assert plan.durations["MARKET_EVENTS"] == 0.0


def test_pre_duration_scales_with_card_count():
    brief1 = dataclasses.replace(synthetic_brief("QUIET"), market_events=_model(1))
    brief3 = dataclasses.replace(synthetic_brief("QUIET"), market_events=_model(3))
    d1 = plan_pre_sections(brief1).durations["MARKET_EVENTS"]
    d3 = plan_pre_sections(brief3).durations["MARKET_EVENTS"]
    assert d3 > d1 > 0


# --------------------------------------------------------------------------- scene
def test_market_events_scene_only_draws_declared_texts():
    from daily_video.public_storyboard import market_events_spec
    spec = market_events_spec(_model(2), "PRE")
    public = spec.public_text("00_market_events")
    assert any("Co 0" in v for v in public.values())
    assert any("Co 1" in v for v in public.values())
    # internal `data` (event_keys/sources) must never leak into the content-scan surface
    assert all("event_keys" not in k for k in public)
    assert all("EARNINGS:SYM0" not in v for v in public.values())


def test_market_events_scene_requires_provenance():
    from daily_video.public_storyboard import NO_PROVENANCE_KINDS, market_events_spec
    spec = market_events_spec(_model(1), "POST")
    assert spec.kind not in NO_PROVENANCE_KINDS
    assert "provenance" in spec.texts


# --------------------------------------------------------------------------- never inferred
def test_earnings_line_never_fabricates_a_result_date():
    """The card's one factual line must come only from `ev.facts` (what the filing itself
    stated) - never a result date synthesized from a reporting period or prior-quarter cadence."""
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.watch import _card
    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS,
                     event_key="EARNINGS:ABC:board1", symbol="ABC", company="ABC Ltd",
                     status="SCHEDULED", sub_type=None, data_as_of=DAY.isoformat(),
                     source_name="nse_corp_announcements", source_reference="https://x",
                     facts=[], status_capture=SUCCESS)   # no reporting_period stated
    card = _card(ev)
    assert "Q" not in card["line"]           # no fabricated quarter label
    assert card["date"] == ev.data_as_of     # the date is read verbatim, never derived


# --------------------------------------------------------------------------- policy pass-through
def test_earnings_fact_passes_policy_with_official_reference():
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.facts import market_event_fact
    from publication.policy import evaluate
    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS,
                     event_key="EARNINGS:ABC:board1", symbol="ABC", company="ABC Ltd",
                     status="SCHEDULED", sub_type=None, data_as_of=DAY.isoformat(),
                     source_name="nse_corp_announcements", source_reference="https://nse/x",
                     facts=[], status_capture=SUCCESS)
    f = market_event_fact(ev, "ABC Ltd reports earnings today.", "x.0")
    decision = evaluate(f, "PRIVATE_ANALYTICS")
    assert decision.allowed


def test_scan_public_text_catches_recommendation_language_on_market_events_text():
    from publication.language import scan_public_text
    scan = scan_public_text({"market_events.0": "Apply for this IPO before it closes."},
                            known_securities={}, approved_securities=set(), ipo_context=True)
    assert scan.issues.get("ipo_recommendation_language")


# --------------------------------------------------------------------------- IPO exclusion (P2A)
def test_plan_public_sections_market_events_section_excludes_ipo_even_when_intel_carries_it():
    """End-to-end through plan_public_sections: even if a future change were to populate
    intel.market_events["IPO"] (nothing does today), the public MARKET_EVENTS section must
    never render it - IPO stays on the existing IPO WATCH scene only."""
    from market_events.models import EARNINGS, IPO, SCHEMA_VERSION, SUCCESS, MarketEvent
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections

    class FakeGate:
        profile = type("P", (), {"value": "PRIVATE_ANALYTICS"})()

        def admit(self, fact):
            return True

    def ev(family, key, symbol):
        return MarketEvent(schema_version=SCHEMA_VERSION, family=family, event_key=key,
                           symbol=symbol, company=f"{symbol} Ltd", status="SCHEDULED",
                           sub_type=None, data_as_of=DAY.isoformat(),
                           source_name="nse_corp_board_meetings", source_reference="https://x",
                           facts=[], status_capture=SUCCESS)

    intel = PublicIntelligence()
    intel.market_events = {
        IPO: [ev(IPO, "IPO:DEMOIPO:x", "DEMOIPO")],
        EARNINGS: [ev(EARNINGS, "EARNINGS:ABC:x", "ABC")],
    }
    intel.universe_symbols = {"DEMOIPO", "ABC"}
    out = plan_public_sections(FakeGate(), intel, DAY, "PRE", max_structure=0)
    families_shown = {c["tag"] for c in (out.market_events or {}).get("cards", [])}
    assert "IPO" not in families_shown
    assert "EARNINGS" in families_shown


# --------------------------------------------------------------------------- OFS reaches the scene (P2B)
def test_plan_public_sections_market_events_section_includes_ofs():
    from market_events.models import OFS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections

    class FakeGate:
        profile = type("P", (), {"value": "PRIVATE_ANALYTICS"})()

        def admit(self, fact):
            return True

    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=OFS, event_key="OFS:DEMOOFS:x",
                     symbol="DEMOOFS", company="Demo OFS Ltd", status="OPEN", sub_type=None,
                     data_as_of=DAY.isoformat(), source_name="nse_ofs_live",
                     source_reference="https://x", facts=[{"label": "floor_price", "value": "250"}],
                     status_capture=SUCCESS)
    intel = PublicIntelligence()
    intel.market_events = {OFS: [ev]}
    intel.universe_symbols = {"DEMOOFS"}
    out = plan_public_sections(FakeGate(), intel, DAY, "PRE", max_structure=0)
    families_shown = {c["tag"] for c in (out.market_events or {}).get("cards", [])}
    assert "OFS" in families_shown


# --------------------------------------------------------------------------- BUYBACK reaches the scene (P2C)
def test_plan_public_sections_market_events_section_includes_buyback():
    from market_events.models import BUYBACK, SCHEMA_VERSION, SUCCESS, MarketEvent
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections

    class FakeGate:
        profile = type("P", (), {"value": "PRIVATE_ANALYTICS"})()

        def admit(self, fact):
            return True

    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=BUYBACK, event_key="BUYBACK:DEMOBB:x",
                     symbol="DEMOBB", company="Demo Buyback Ltd", status="OPEN", sub_type=None,
                     data_as_of=DAY.isoformat(), source_name="nse_corporate_actions",
                     source_reference="https://x",
                     facts=[{"label": "buyback_price", "value": "500"}], status_capture=SUCCESS)
    intel = PublicIntelligence()
    intel.market_events = {BUYBACK: [ev]}
    intel.universe_symbols = {"DEMOBB"}
    out = plan_public_sections(FakeGate(), intel, DAY, "PRE", max_structure=0)
    families_shown = {c["tag"] for c in (out.market_events or {}).get("cards", [])}
    assert "BUYBACK" in families_shown
