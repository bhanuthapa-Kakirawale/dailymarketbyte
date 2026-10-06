# Market Events Engine V1

A unified engine for official calendar/lifecycle events - earnings board-meeting/result
filings, IPO, OFS, government securities auctions (T-Bill/G-Sec/SDL), buyback, open
offer/takeover, delisting - feeding PRE ("what matters today"), POST ("what happened today")
and the Private Desk's own, broader event calendar. One reusable
acquisition→revision→replay→rights pipeline (`market_events/`), not seven independent hacks,
mirroring `institutional_flows/`'s proven shape rather than duplicating it.

**Status (P2B, 2026-10-06): EARNINGS and OFS are both live from NSE's own feeds. IPO is live as
a read-only projection over the existing, already-live `ipo_watch` pipeline (never a second
acquisition). Every other family still returns `NOT_SUPPORTED_YET`.**

## Purpose

`official_snapshots/` (per-session complete-list snapshots: IPO list, F&O ban, ASM/GSM) and
`exchange_watch/` (list-membership rows diffed day-over-day) don't fit this problem.
Earnings/buyback/open-offer/delisting/OFS are **individual filings**, each independently
revisable over time (a board-meeting date moves, a buyback price is revised) - the same shape
`institutional_flows/` already solved for NSE/CDSL/NSDL flow reports: per-identity immutable
revisions, checksum-based change detection, a point-in-time replay gate, an attempts log, typed
`PublishableFact` builders, read-only Private Desk sections. `market_events/` is that same
shape, generalized from "one source → one snapshot" to "one family → many events, each
independently revisioned by `event_key`."

`exchange_watch.EventFamily.CORPORATE_EVENT`/`BOARD_MEETING` (previously "planned, no adapter"
placeholders) are superseded by this package - implementing both would duplicate the same
abstraction. IPO is **not** re-acquired here: `ipo_watch`/`official_snapshots` remain the one
source of truth for IPO, shown through the existing IPO WATCH scene; Market Events does not
project or duplicate it.

## Canonical model (`market_events/models.py`)

```
MarketEvent(
    schema_version, family, event_key, symbol, company, status, sub_type, data_as_of,
    source_name, source_reference, facts, source_text, retrieved_at, first_retrieved_at,
    status_capture, raw_sha256, records_checksum_value, parser_version, revision, bootstrap,
    capture_mode, reason, connectivity_status, publication_rights_status,
)
```

* `event_key` is a STABLE identity: `f"{family}:{symbol or 'MARKET'}:{natural_id}"`, where
  `natural_id` is the filing's own reference (an NSE announcement sequence number, an RBI
  release number) where the source states one, else a deterministic hash of
  `(symbol, family, first-seen date)` - **never** a sequence number this pipeline assigns itself.
* `fields`/`facts` carry only what the exchange/regulator actually stated. A missing figure is
  **omitted**, never inferred (the same rule `ipo_watch.IPOEvent` already enforces). EARNINGS'
  `data_as_of` is never computed from prior-quarter cadence or a third-party calendar - only a
  value the official filing itself states.
* `status` is the shared lifecycle vocabulary (`ANNOUNCED`/`SCHEDULED`/`REVISED_DATE`/`OPEN`/
  `CLOSED`/`COMPLETED`/`WITHDRAWN`/`CANCELLED`) - a family only ever uses the subset its own
  filing can actually state. `DELAYED`/`CANCELLED`/`COMPLETED` are never inferred without
  explicit source evidence.
* `status_capture` is the acquisition-attempt vocabulary
  (`SUCCESS`/`SOURCE_UNAVAILABLE`/`PARSE_ERROR`/`VALIDATION_FAILED`/`NOT_SUPPORTED_YET`) -
  `NOT_SUPPORTED_YET` mirrors `official_snapshots.NOT_SUPPORTED` (ESM): a family this pass never
  attempts, not a failure. There is deliberately **no separate `NO_DATA` value**: "reachable,
  zero qualifying events today" is `FamilyFetchResult(status=SUCCESS, events=[])`, which
  `MarketEventsService.capture()` already treats as healthy (nothing written, no attempt-log
  failure) - adding a real `NO_DATA` status would ripple into the `VALIDATED`/`FAILED`
  frozensets for a distinction this shape already makes.
* **Event identity across a reschedule** (`MarketEventsService._lookup_open_event`): a filing
  with no stable per-row ID (e.g. NSE's board-meetings feed, where the one field that changes
  on a reschedule is exactly the meeting date) reconciles identity via an opt-in fetcher
  contract: `fetch_x.needs_lookup = True` makes `capture()` pass `lookup_fn(family, symbol)` -
  the latest NON-TERMINAL event for that symbol, or `None`. The fetcher reuses that event's
  `event_key` on a match (`status=REVISED_DATE` if the date itself changed) or mints a fresh
  key (`f"{family}:{symbol}:{sha256(symbol|family|today)[:16]}"`, "today" being when THIS
  pipeline first saw it) when none is open. Fully backward-compatible: a fetcher without the
  marker is called exactly as before.
* `records_checksum_value` (`MarketEvent.seal()`) hashes **both** `status` and `facts` -
  unlike `institutional_flows` (which hashes facts alone, since its snapshots carry no business
  lifecycle status), a Market Event's status is itself part of what changed: a filing that gets
  cancelled with identical `facts` must never checksum as `UNCHANGED`.

## Source hierarchy

Preferred, in order: (1) RBI/SEBI/NSE/BSE's own primary official source; (2) the official
issuer/company filing where appropriate. Never Moneycontrol, Groww, Economic Times,
NiftyTrader, Chittorgarh, Investing.com, a generic calendar API, or a search snippet. If an
official source is unreachable or unverified, the family returns `SOURCE_UNAVAILABLE` or
`NOT_SUPPORTED_YET` - never a third-party substitution.

| Family | Candidate real source | V1 status |
|---|---|---|
| EARNINGS | NSE's board-meeting prior-intimation feed (`market_events/sources/earnings.py`) | **LIVE** - `/api/corporate-board-meetings?index=equities`, confirmed reachable and returning real data (verified 2026-10-06; a live pull classified 5 genuine results-qualifying board meetings, e.g. Asian Paints Limited, 29-Oct-2026) |
| IPO | `ipo_watch`/`official_snapshots` (`market_events/sources/ipo_projection.py`) | **LIVE as a read-only projection** - never re-acquired; shown via the existing IPO WATCH scene in PRE/POST, and via this projection on the Private Desk's `/events`/dashboard/Data Quality only |
| OFS | NSE's own OFS mechanism feed (`market_events/sources/ofs.py`) | **LIVE** - `/api/live-ofs-active-issues` + `/api/live-ofs-past-issues`, confirmed reachable 2026-10-06; every row IS an OFS by construction (no text classification needed, unlike EARNINGS) |
| GOVT_SECURITIES_AUCTION | RBI's semi-annual G-Sec/T-Bill borrowing calendar | `NOT_SUPPORTED_YET` (planned: hand-maintained controlled file, see Known limitations) |
| BUYBACK | Same corp-filings API family as EARNINGS | `NOT_SUPPORTED_YET` - deferred to P2C |
| OPEN_OFFER | NSE/BSE announcements + SEBI SAST filings (often unstructured PDFs) | `NOT_SUPPORTED_YET` - deferred to P2C |
| DELISTING | NSE/BSE delisting announcements | `NOT_SUPPORTED_YET` - deferred to P2C |

Governing rule (already established via `official_snapshots.NOT_SUPPORTED`, and the project's
"reliability over feature count" posture): **no live adapter is written against an endpoint
that has not actually been verified reachable from this environment.** `market_events/sources/`
holds `DEFAULT_FETCHERS`, one per family; EARNINGS and OFS now map to real adapters
(`market_events.sources.earnings.fetch_earnings`, `market_events.sources.ofs.fetch_ofs`), IPO's
stub carries an honest reason ("sourced via `ipo_watch` by design, never acquired by this
engine" - not "unreachable", since it IS reachable, just intentionally out of this engine's
scope), and every other family stays the generic `NOT_SUPPORTED_YET` stub carrying an explicit
`not_supported_yet = True` marker (so Data Quality can tell "never attempted" apart from "has a
real adapter").

`python validate_market_events_sources.py` is the manual, read-only connectivity diagnostic
(mirrors `validate_pre_production.py --connectivity-only`) - one probe per family, never writes
to `market_events/`, never gates anything.

## Government securities auction yield semantics (design constraint for the eventual adapter)

Before an auction's result is published, any coupon shown is labelled `Coupon: X%`, **never**
`Yield: X%` - yield is `DETERMINED AT AUCTION` until the result exists. A previous auction's
cut-off yield may be shown only explicitly labelled `PREVIOUS AUCTION`, never implied as
guaranteed for the upcoming one. After the result is officially published, `Cut-off yield: X%`
may be shown with its own result date/source. This rule governs the `GOVT_SECURITIES_AUCTION`
family's `facts` the moment a real source lands; nothing here is bypassable by a looser display
choice downstream.

## EARNINGS classification (`market_events/sources/earnings.py`)

NSE's `/api/corporate-board-meetings?index=equities` lists every upcoming board meeting, for
any purpose - a row becomes an EARNINGS `MarketEvent` only if its own `bm_desc`/`bm_purpose`
text explicitly matches `financial\s+results` (confirmed live wording: "...consider and
approve the Unaudited Financial results..."). A meeting "to consider Other business" is
dropped, never shown. Company name comes directly from the feed's own `sm_name` field (a
regex fallback reads it from the filing's own sentence - "`<Company>` has informed the
Exchange..." - for the rare row missing that field; never guessed). `reporting_period` is only
ever an explicit Q1-Q4/H1-H2/9M/FY token found in the source text itself; most real rows state
none, so `"UNKNOWN"` is the common, correct case - never computed from the meeting date.
Identity across a reschedule uses the `needs_lookup` mechanism above (this feed's
`oriiginalMeetingDate`/`proposedMeetingDate` fields exist but were `null` in every row sampled
live this session - the lookup-based reconciliation does not depend on them being populated).

## IPO adaptation strategy (`market_events/sources/ipo_projection.py`)

`project_ipo_events(ipos, day)` is a **pure function, no network, never calls
`market_events/store.py`** - it reuses `ipo_watch.watch.todays_event()`'s own dated-event rule
over already-validated `IPOEvent` objects, so a projected `MarketEvent` can never disagree with
the IPO WATCH scene about the same IPO (both read the identical source data; see
`crosscheck_against_ipo_watch()`, exercised by tests, for the equivalence proof - there is
structurally only one source here, so a divergence is a bug in the projection, never a live
`SOURCE_CONFLICT`). `capture_mode="PROJECTION"` marks these events as never-acquired-by-this-
engine. The Private Desk calls it over a -3..+7 day window per session (since a single call is
only ever "today's" dated events) and unions the results for its TODAY/TOMORROW/NEXT 7 DAYS/
RECENTLY ANNOUNCED buckets; the per-family `quality_rows()`/`family_section()` status is
`"VIEW_ONLY"`, not `NOT_SUPPORTED_YET` or `OK` (it was never meant to be "captured" by this
engine at all).

## OFS semantics (`market_events/sources/ofs.py`)

**Discovery (P2B, 2026-10-06):** NSE's dedicated OFS page (`/market-data/all-upcoming-issues-ofs`)
turned out to be served by the exact same frontend controller as the IPO page
(`upcoming-ipo.js` - confirmed by reading that page's own `<script>` tags), which reads this
exact endpoint pair:

* `/api/live-ofs-active-issues` - OFS currently open/scheduled. Confirmed reachable; genuinely
  empty (`{"data": []}`) the day this was built - a healthy `SUCCESS`+`[]` result, not
  `NOT_SUPPORTED_YET`. Field names (`symbol`, `series`, `ofsStartDate`/`startDate`,
  `ofsEndDate`/`endDate`, `status`) are taken from the controller script's own field reads -
  official evidence, never guessed - so a populated row missing them fails closed
  (`PARSE_ERROR`), distinguished from a structurally-valid row that simply has no "today" fact
  (a middle day of a multi-day window), which is silently skipped, never a drop.
* `/api/live-ofs-past-issues` - OFS already completed. Confirmed reachable with 471 real
  historical records (e.g. Sustainable Energy Infra Trust / SEITINVITCUMU, offer date
  24-Sep-2026, floor price ₹120, allocated at ₹120). Only rows within `RECENT_WINDOW_DAYS = 7`
  of "now" are imported as `MarketEvent`s (status `COMPLETED`) - older history is deliberately
  not backfilled into the store every day.

Every row from either endpoint **is** an OFS by construction - the source itself is NSE's own
OFS listing, so there is no "reject a non-OFS row" classification step the way EARNINGS rejects
a non-results board meeting.

**Why the earlier `/api/corporate-announcements` search found nothing**: that is NSE's general
corporate-disclosure feed (109 `desc` categories, confirmed via a 19,307-row, 5-week pull - no
"Offer for Sale" category exists in it). OFS runs through this separate, dedicated mechanism
feed instead - a different page, a different underlying API, never guessed, found only after
reading the real OFS page's own script tags.

**Single `data_as_of` per multi-dated event**: an active OFS has both an open and close date,
but `MarketEvent` carries one `data_as_of`. `_classify_active()` resolves this exactly like
`ipo_watch.todays_event()` resolves an IPO's four possible dated milestones: before the open
date → `(open_date, ANNOUNCED)`; on the open date → `(open_date, OPEN)` (or `CLOSED` if it's a
single-day OFS, open date == close date); on the close date → `(close_date, CLOSED)`; any other
day → no event today (silently skipped, never a drop). Event identity for an active row uses
the same `needs_lookup` reconciliation as EARNINGS (a revised floor price or date is a revision
of the same event, never a new one); a completed row's `event_key` is built directly from the
source's own `symbol`/`offerDate`/`category` (a genuine natural identifier - no lookup needed,
since a completed record is terminal).

OFS never uses Chittorgarh/Groww/Moneycontrol or any other third-party tracker - both endpoints
are NSE's own.

## Revisions and change detection

`market_events/store.py` mirrors `institutional_flows/store.py` exactly, keyed by
`(family, event_key)`:

```
<out>/market_events/<FAMILY>/<safe_event_key>.json        revision 1
<out>/market_events/<FAMILY>/<safe_event_key>.r2.json      a later acquisition
<out>/market_events/attempts/<YYYY-MM-DD>/<family>_<HHMMSS>_<mode>.json
```

`write_revision()` is an exclusive-create (`open(path, "x", ...)`) - a revision file, once
written, is never edited or replaced, only superseded by a new, separately-numbered file. A
later revision is **never refused** because an earlier one already validated - a board-meeting
date or a buyback price can legitimately be restated. `MarketEventsService.capture()` compares
checksums against the prior latest revision of the same `event_key`: identical → `UNCHANGED`
(nothing written); different → `REVISED` (`first_retrieved_at` carried forward from the prior
revision; `CANCELLED_CHANGE` specifically when the new status is terminal); none exists →
`NEW_EVENT` (`bootstrap` flag, `first_retrieved_at = now`). A family's fetcher failing, or
returning `NOT_SUPPORTED_YET`, is recorded as an attempt and never crashes the caller.

## Source conflicts

Not yet exercised (no two sources for one family exist this pass), but the model is designed
for it: a canonical `event_key` is meant to collapse NSE/BSE/SEBI/company duplicates of the
same filing into one representation while every source's own reference is preserved in
`facts`/`source_reference`. A genuine conflict between two official sources must surface as
`VALIDATION_FAILED` (public scene fails closed) rather than being silently resolved by guessing
- this is a requirement on any adapter that reads more than one source per family, not yet
implemented since no family does.

## Editorial ranking (`market_events/watch.py`)

`select_market_events()` is deterministic and transparent: an event qualifies only when dated
`day` (its `data_as_of`) and - if it names a security - that security is in the tracked
universe. Family priority (spec-ordered, never by "expected impact"): EARNINGS before IPO/OFS
before GOVT_SECURITIES_AUCTION before BUYBACK before OPEN_OFFER before DELISTING. The public cap
(`MAX_PUBLIC_EVENTS = 3`) trims the tail - a dump of every family is never the point. No model,
no AI, no ranking by predicted market impact.

## PRE use

Wired through the **same shared path** EXCHANGE WATCH/IPO WATCH already use
(`presentation/public_intelligence.py:plan_public_sections`), not a second, parallel wiring:
`presentation/pre_plan.py`'s `SECTION_ORDER` carries a `"MARKET_EVENTS"` key (distinct from the
single-event RBI/FOMC/expiry `"EVENT"` card already there), inside `PUBLIC_OPTIONAL` -
trimmed only under the `MAX_RUNTIME` ceiling, never competing for the previous-session
`OPTIONAL_BUDGET`. Maximum 3 concise items, one card per event (family chip, company/instrument,
one fixed factual line, source). Never forced onto screen when nothing qualifies.

## POST use

No change to `post_plan.py` - exactly like EXCHANGE/IPO, the Market Events scene flows straight
from `PublicSections` into the storyboard (`daily_video/storyboard.py:build_storyboard`), added
to `POST_TRIM_ORDER` for the same runtime-ceiling trimming. May state a filing/result/auction
occurred, closed, or was updated today - never interprets a financial result (no revenue/
EBITDA/PAT/margin/EPS/beat-miss parsing; that is explicitly out of scope for V1).

## Private Desk use

`GET /events` (`private_desk/services/market_events.py`, `templates/events.html`): TODAY /
TOMORROW / NEXT 7 DAYS / RECENTLY ANNOUNCED per family, with a family filter. The dashboard gets
one compact panel (`dashboard_section()` - context only). The stock page's existing "Official
events" card (already showing FNO_BAN/ASM/GSM/IPO from `official_snapshots`) is **extended**,
not duplicated, with EARNINGS/BUYBACK/OPEN_OFFER/DELISTING/OFS rows for that symbol
(`candidates.official_index()`, `market_events.STOCK_FAMILIES`). `GOVT_SECURITIES_AUCTION` is
market-wide and is never attached to a stock page. Data Quality gets one row per family
(status, record count, `NOT_SUPPORTED_YET` flag, recent capture attempts).

**Nothing here is reachable from `services/attention.py`, `services/candidates.py`'s Radar
building, `services/packet.py` (`PrivateCandidatePacket`), the Radar order, or Market Regime -
test-enforced** (`tests/test_private_desk_market_events.py`'s `test_radar_order_and_
attention_set_are_unchanged` / `test_market_regime_snapshot_is_unaffected` /
`test_packet_schema_and_fields_are_unaffected`).

## Replay / historical behaviour

`market_events/context.py:load_market_events()` mirrors
`institutional_flows.context.load_institutional`'s replay/live split exactly: a replay
(`live=False`) **never fetches** - nothing stored is `HISTORICAL_SNAPSHOT_UNAVAILABLE`, never
today's page relabelled as the past. A live run may capture a missing family inside its own
window (`CAPTURED_THIS_RUN`). The point-in-time gate (`first_retrieved_before`) excludes an
event discovered after its own cutoff even if dated on or before the session - the same
`institutional_flows` discipline, tested directly
(`tests/test_market_events_boundary.py::test_first_retrieved_before_cutoff_gate_excludes_report_
discovered_after_cutoff`). `presentation/public_intelligence.py:load_public_intelligence()`
passes a live run's cutoff as real "now" and a replay's cutoff as the END of the session being
replayed (never real wall-clock "now", which would let a late-arriving revision leak into a
replay of the past).

## Rights

Every new source defaults through `strictest_rights()`/`core.sources`'s `UNREVIEWED` registry
default to `REVIEW_REQUIRED` - nothing here loosens `PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK`.
`market_events/facts.py:market_event_fact()` assigns scope/content_class per family so each
satisfies `publication/policy.py`'s existing rules unmodified: EARNINGS/BUYBACK/OPEN_OFFER/
DELISTING are `Scope.SECURITY` + `ContentClass.CORPORATE_EVENT` (a named security needs an
OFFICIAL event); IPO/OFS are `Scope.IPO` + `ContentClass.IPO_EVENT`; GOVT_SECURITIES_AUCTION is
market-wide, `Scope.MARKET` + `ContentClass.EXCHANGE_EVENT` (a minor naming imprecision -
RBI is a regulator, not an exchange - accepted for V1 rather than adding a new
`ContentClass.REGULATOR_EVENT` to the shared `publication/classification.py` enum surface;
owner-confirmed).

## Known limitations

* **EARNINGS is live** (`/api/corporate-board-meetings?index=equities`, verified reachable
  2026-10-06 from this environment via the existing `market.NSE()` warm-up-cookie client - the
  SAME client/session pattern that already powers `exchange_watch`/`ipo_watch`). GitHub Actions
  runners are known to be blocked for `nseindia.com` in general (CLAUDE.md's own "Known
  limitations"); this adapter's reachability from the actual production runner is **unverified**
  and may degrade to `SOURCE_UNAVAILABLE` there - that is a normal, handled outcome
  (`capture_market_events` is never fatal), not a design gap.
* **OFS is now live (P2B, 2026-10-06), resolving P2A's blocker.** P2A's search of
  `/api/corporate-announcements?index=equities` (109 `desc` categories, 19,307 rows, no "Offer
  for Sale" category) was a correct dead end - that is NSE's general corporate-disclosure feed,
  and OFS turned out to run through a separate, dedicated mechanism page/API instead
  (`/market-data/all-upcoming-issues-ofs`, served by the same frontend controller as the IPO
  page), found by reading that page's own script tags rather than guessing further endpoint
  names. See "OFS semantics" above for the full discovery path and adapter design.
* **BUYBACK/OPEN_OFFER/DELISTING are explicitly deferred to P2C**, though note the
  corporate-announcements taxonomy above already confirms `Buyback` and
  `Public Announcement-Open Offer` categories exist and are reachable through the same feed -
  a strong head start for that later packet, not re-investigated here.
* **GOVT_SECURITIES_AUCTION** (P2B): RBI's own semi-annual G-Sec/T-Bill borrowing calendar is
  published as a document covering the whole half-year, making a hand-maintained controlled
  file (mirroring `data/official_events.json`'s fail-closed loader in `core/event_calendar.py`)
  cheaper and more reliable than a weekly scraper - but it requires real, owner-verified
  entries (quoted line, source URL, verified-on date) exactly like the RBI MPC schedule; no
  placeholder/synthetic entries are shipped in `data/`.
* Detailed earnings-result interpretation (revenue/EBITDA/PAT/margin parsing) is explicitly out
  of scope for V1 - a later packet.
* Source-conflict handling (two official sources disagreeing on one filing) is designed for but
  unexercised, since no family currently reads more than one source.
* The IPO projection window (-3..+7 days per Desk page load) re-reads the stored official
  snapshot and re-runs `ipo_watch`'s own selection logic on every request - cheap at today's IPO
  volume, but worth revisiting if the Desk ever needs to serve many concurrent requests.
