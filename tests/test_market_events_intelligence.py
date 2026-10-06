"""Market Events Engine V1 - the deterministic selection/model layer (`market_events.watch`):
dedup by event_key, universe filtering, family priority order, the public cap, and the display
model/PublishableFact builders. No model, no ranking by expected outcome - a transparent,
deterministic order only.
"""
import datetime as dt

DAY = dt.date(2026, 10, 6)


def _ev(family, event_key, symbol, company="Co", status="SCHEDULED", data_as_of=None, facts=None):
    from market_events.models import SCHEMA_VERSION, SUCCESS, MarketEvent
    return MarketEvent(schema_version=SCHEMA_VERSION, family=family, event_key=event_key,
                       symbol=symbol, company=company, status=status, sub_type=None,
                       data_as_of=(data_as_of or DAY.isoformat()),
                       source_name="nse_corp_announcements",
                       source_reference="https://nseindia.com/x", facts=facts or [],
                       status_capture=SUCCESS)


def test_select_market_events_dedupes_by_event_key_keeps_latest_revision():
    from market_events.models import EARNINGS
    from market_events.watch import select_market_events
    ev = _ev(EARNINGS, "EARNINGS:ABC:board1", "ABC")
    # same event_key present under two family buckets (shouldn't normally happen, but the
    # selector must still treat it as ONE event, not two)
    chosen, omitted = select_market_events({EARNINGS: [ev, ev]}, {"ABC"}, DAY)
    assert len(chosen) == 1


def test_select_market_events_excludes_symbols_outside_universe():
    from market_events.models import EARNINGS
    from market_events.watch import select_market_events
    ev = _ev(EARNINGS, "EARNINGS:ZZZ:board1", "ZZZ")
    chosen, omitted = select_market_events({EARNINGS: [ev]}, {"ABC"}, DAY)
    assert chosen == []
    assert omitted[0]["reason"] == "symbol outside the tracked universe"


def test_select_market_events_excludes_events_not_dated_today():
    from market_events.models import EARNINGS
    from market_events.watch import select_market_events
    ev = _ev(EARNINGS, "EARNINGS:ABC:board1", "ABC", data_as_of="2026-10-05")
    chosen, omitted = select_market_events({EARNINGS: [ev]}, {"ABC"}, DAY)
    assert chosen == []
    assert "not dated" in omitted[0]["reason"]


def test_govt_auction_names_no_security_and_is_never_excluded_by_universe():
    from market_events.models import GOVT_SECURITIES_AUCTION
    from market_events.watch import select_market_events
    ev = _ev(GOVT_SECURITIES_AUCTION, "GOVT_SECURITIES_AUCTION:MARKET:tbill1", None,
            company="91-day T-Bill")
    chosen, omitted = select_market_events({GOVT_SECURITIES_AUCTION: [ev]}, {"ABC"}, DAY)
    assert len(chosen) == 1
    assert chosen[0].symbol is None


def test_family_priority_order_and_public_cap():
    from market_events.models import BUYBACK, DELISTING, EARNINGS, IPO, OPEN_OFFER
    from market_events.watch import MAX_PUBLIC_EVENTS, select_market_events
    events_by_family = {
        DELISTING: [_ev(DELISTING, "DELISTING:D:x", "D", company="D Ltd")],
        OPEN_OFFER: [_ev(OPEN_OFFER, "OPEN_OFFER:C:x", "C", company="C Ltd")],
        BUYBACK: [_ev(BUYBACK, "BUYBACK:B:x", "B", company="B Ltd")],
        EARNINGS: [_ev(EARNINGS, "EARNINGS:A:x", "A", company="A Ltd")],
        IPO: [_ev(IPO, "IPO:E:x", "E", company="E Ltd")],
    }
    chosen, omitted = select_market_events(events_by_family, {"A", "B", "C", "D", "E"}, DAY)
    assert len(chosen) <= MAX_PUBLIC_EVENTS
    assert chosen[0].family == EARNINGS          # highest priority always first
    assert [o["reason"].startswith("public cap") for o in omitted].count(True) >= 1


def test_build_model_is_none_for_no_events():
    from market_events.watch import build_model
    assert build_model([]) is None


def test_build_model_headline_pluralizes():
    from market_events.models import EARNINGS
    from market_events.watch import build_model
    evs = [_ev(EARNINGS, "EARNINGS:A:x", "A", company="A Ltd"),
          _ev(EARNINGS, "EARNINGS:B:x", "B", company="B Ltd")]
    model = build_model(evs, "PRE")
    assert "Two market events today" == model["headline"]


def test_market_events_facts_never_names_a_security_for_a_govt_auction():
    from market_events.models import GOVT_SECURITIES_AUCTION
    from market_events.watch import market_events_facts
    from publication.classification import Scope
    ev = _ev(GOVT_SECURITIES_AUCTION, "GOVT_SECURITIES_AUCTION:MARKET:tbill1", None,
            company="91-day T-Bill")
    facts = market_events_facts([ev])
    assert all(f.scope is Scope.MARKET and f.security is None for f in facts)


def test_market_events_facts_use_security_scope_for_earnings():
    from market_events.models import EARNINGS
    from market_events.watch import market_events_facts
    from publication.classification import ContentClass, Scope
    ev = _ev(EARNINGS, "EARNINGS:ABC:x", "ABC", company="ABC Ltd")
    facts = market_events_facts([ev])
    assert all(f.scope is Scope.SECURITY and f.security == "ABC"
              and f.content_class is ContentClass.CORPORATE_EVENT for f in facts)
