# Market Events Engine V1

A unified engine for official calendar/lifecycle events - earnings board-meeting/result
filings, IPO, OFS, government securities auctions (T-Bill/G-Sec/SDL), buyback, open
offer/takeover, delisting - feeding PRE ("what matters today"), POST ("what happened today")
and the Private Desk's own, broader event calendar. One reusable
acquisition→revision→replay→rights pipeline (`market_events/`), not seven independent hacks,
mirroring `institutional_flows/`'s proven shape rather than duplicating it.

**Status (P2F, 2026-10-07): EARNINGS, OFS, BUYBACK, OPEN_OFFER, DELISTING (all NSE's own feeds)
and GOVT_SECURITIES_AUCTION (RBI's own press-release feed) are all live. IPO is live as a
read-only projection over the existing, already-live `ipo_watch` pipeline (never a second
acquisition). State Development Loans (SDL) - the same RBI feed, deferred out of
GOVT_SECURITIES_AUCTION's V1 scope - and every other family still return `NOT_SUPPORTED_YET`.**

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
| BUYBACK | NSE's structured corporate-actions feed + corporate-announcements + daily-buyback disclosure (`market_events/sources/buyback.py`) | **LIVE** - `/api/corporates-corporateActions?index=equities` (`subject == "Buy Back"`, an exact categorical match) is PRIMARY, confirmed reachable 2026-10-07; cross-referenced with `/api/corporate-announcements` and `/api/corporates-daily-buyback?` for lifecycle status and route/price/quantity when explicitly stated |
| GOVT_SECURITIES_AUCTION | RBI's own press-release feed (`market_events/sources/govt_securities_auction.py`) | **LIVE** - `https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=2757`, confirmed reachable via a plain HTTP GET (no cookie warm-up, unlike nseindia.com) 2026-10-07; the announcement AND result press releases each carry the substantive table directly in their own HTML, never PDF-only |
| OPEN_OFFER | NSE's corporate-announcements feed, `desc == "Public Announcement-Open Offer"` (`market_events/sources/open_offer.py`) | **LIVE** - confirmed reachable 2026-10-07; an exact categorical match, same feed BUYBACK already reads as its secondary source. Price/shares/%/dates are not in the feed's own text and stay absent - see "OPEN_OFFER semantics" above |
| DELISTING | NSE's corporate-announcements feed, `desc in {"Delisting", "Voluntary Delisting"}` (`market_events/sources/delisting.py`) | **LIVE** - confirmed reachable 2026-10-07; a 365-day pull returned 17 real rows. BSE's delisting purpose code and NSE's own XLSX-based delisting rosters were investigated and NOT used (see "DELISTING semantics" below) |

Governing rule (already established via `official_snapshots.NOT_SUPPORTED`, and the project's
"reliability over feature count" posture): **no live adapter is written against an endpoint
that has not actually been verified reachable from this environment.** `market_events/sources/`
holds `DEFAULT_FETCHERS`, one per family; EARNINGS, OFS, BUYBACK, OPEN_OFFER, DELISTING and
GOVT_SECURITIES_AUCTION now map to real adapters (`market_events.sources.earnings.fetch_earnings`,
`market_events.sources.ofs.fetch_ofs`, `market_events.sources.buyback.fetch_buyback`,
`market_events.sources.open_offer.fetch_open_offer`,
`market_events.sources.delisting.fetch_delisting`,
`market_events.sources.govt_securities_auction.fetch_govt_securities_auction`), IPO's stub
carries an honest reason ("sourced via `ipo_watch` by design, never acquired by this engine" -
not "unreachable", since it IS reachable, just intentionally out of this engine's scope), and
every other family (SDL, CMB, switches, ...) stays the generic `NOT_SUPPORTED_YET` stub carrying
an explicit `not_supported_yet = True` marker (so Data Quality can tell "never attempted" apart
from "has a real adapter").

`python validate_market_events_sources.py` is the manual, read-only connectivity diagnostic
(mirrors `validate_pre_production.py --connectivity-only`) - one probe per family, never writes
to `market_events/`, never gates anything.

## GOVT_SECURITIES_AUCTION semantics (`market_events/sources/govt_securities_auction.py`)

**Discovery (P2F, 2026-10-07):** RBI's own press-release listing
(`https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=2757`) is plain, un-scripted HTML -
confirmed reachable from this environment via a direct `requests` GET, no cookie warm-up needed
(unlike `nseindia.com`). Each individual press-release detail page (same listing, one `prid=`
link per release) was confirmed to carry the SUBSTANTIVE notification/result table directly in
its own HTML - security name, notified amount, auction/settlement date for an announcement
("Auction of Government of India Dated Securities" / "Auction of 91-Day, 182-Day and 364-Day
Treasury Bills"); notified amount, amount accepted, cut-off price/yield (YTM), weighted-average
price/yield (WAY) for a result ("Government Stock - Full Auction Results" / "Treasury Bills:
Full Auction Result") - never PDF-only (the PDF link is supplementary), confirmed with real
October 2026 examples (e.g. "7.06% GS 2041" / "7.43% GS 2076", notified ₹36,000 crore, auctioned
09-Oct-2026; the 30-Sep-2026 T-Bill result: 91-day cut-off yield 5.5199%, weighted average
5.4902%). A candidate `dbie.rbihub.in` domain surfaced during discovery was investigated and
rejected - it is **not** RBI's own site (RBI's actual Database on Indian Economy domain is
`data.rbi.org.in`, formerly `dbie.rbi.org.in`, per RBI's own URL-change announcement) - and was
never used.

**V1 scope:** central government dated securities (`GSEC_NEW`/`GSEC_REISSUE`) and the three
standard Treasury Bill tenors (`TBILL_91`/`TBILL_182`/`TBILL_364`), both their announcement and
matching result lifecycle stages. **Deferred:** State Development Loans (the SAME feed, under
"Auction of State Government Securities" / "...Full Auction Result" / "Result of Yield/Price
Based Auction of State Government Securities" titles - same source/schema, left out of V1 to
keep scope narrow: one notification can name several states at once, a materially different
grouping shape from one security/tenor per row); Cash Management Bills; switches; government
buybacks of its own debt; underwriting-auction releases; the half-yearly/quarterly
issuance-calendar PDFs (calendar-level documents, not individual auction events); RBI liquidity
operations (WMA limits, floating-rate-bond interest resets). The abbreviated "...Auction
Results: Cut-off" result variant (published alongside the Full Auction Result, same day, a
strict subset of its fields) is deliberately skipped - only the Full Auction Result is read, to
avoid a redundant second parse of the same auction.

**Event identity is a direct natural key, not `needs_lookup`:** this family is market-wide
(`symbol=None`, per `MarketEvent`'s own documented invariant), so the `_lookup_open_event`
symbol-keyed reconciliation EARNINGS/OFS/BUYBACK/OPEN_OFFER/DELISTING share cannot apply (and a
pseudo-symbol would silently break `watch.select_market_events`'s "if it names a security, that
security is in the tracked universe" check - never done).
`event_key = f"GOVT_SECURITIES_AUCTION:MARKET:{instrument_slug}:{auction_date_iso}"` instead:
`instrument_slug` is the security's own stated name (G-Sec, e.g. `7_06_GS_2041`) or the fixed
tenor constant (T-Bill), `auction_date_iso` is the auction date the announcement itself states
(or, for a result captured without its announcement in the same pass, the press release's own
publication date - RBI states results are "announced on the same day" as the auction). A later
notified-amount revision for the SAME instrument/date naturally keeps the SAME key and is picked
up as an ordinary checksum-driven `REVISED` write - no new logic needed. **Known, accepted
limitation:** a genuine auction-DATE revision (RBI rescheduling an already-announced auction)
mints a new event_key rather than revising the original, because this feed gives no symbol or
other cross-referencing field to reconcile it by (unlike EARNINGS' board-meeting reschedule,
which reconciles via NSE's own stable `bm_symbol`). No live example of this was observed this
pass.

**Announcement + result merge:** a single fetch groups every qualifying row from a trailing
`lookback_days = 21` window by `(instrument_slug, auction_date_iso)` BEFORE building any
`MarketEvent`, so an auction whose announcement AND result both fall inside one fetch's window
becomes ONE `COMPLETED` event carrying both fact sets - never two events for one auction cycle.
21 days is generous against the real observed announcement-to-result gap (4-6 days), so a daily
capture almost never sees a result without its announcement already in the same window. Before a
result exists, the event is `SCHEDULED` with only the announcement's facts (notified amount,
repayment/settlement date, GoI notification reference) - no yield field is ever shown, satisfying
the pre-existing project design note that a coupon is never shown labelled `Yield:` before a
result exists. After the result is captured, `cutoff_yield_pct`/`cutoff_price`/
`weighted_avg_yield_pct`/`weighted_avg_price` facts are added and the status becomes `COMPLETED`.
`Devolvement on Primary Dealers` is read only when the source states a non-"NIL" figure - a
`NIL` (the overwhelmingly common case) is omitted, never written as a fabricated `0`.

GOVT_SECURITIES_AUCTION never uses a third-party auction calendar, `dbie.rbihub.in`, or any
hand-maintained date file - the one source used is RBI's own live press-release feed.

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

## BUYBACK semantics (`market_events/sources/buyback.py`)

**Discovery (P2C, 2026-10-07):** found via the same evidence-based nav → page → script-tag
methodology used for OFS. NSE's corporate-filings-actions page
(`/companies-listing/corporate-filings-actions`) reads its own `/dist/js/sections/
corporate-filings.js`, which calls `ensureSymbolAndDates("/api/corporates-corporateActions?
index=equities", ...)`. Confirmed live: the default (no date-range) call already returns
current rows with `subject: "Buy Back"` (an exact categorical field, not free text); a widened
window returned 25 real `subject == "Buy Back"` rows over 2026, e.g. Fairchem Organics Limited
(`FAIRCHEMOR`, record/ex-date 05-Jan-2026, face value 10). Three sources are combined, each
contributing only what it explicitly states:

* `/api/corporates-corporateActions?index=equities` - **PRIMARY**. `subject == "Buy Back"` is
  the classification (never a text search); contributes `record_date`, `ex_date`, `face_value`
  when stated (NSE's own `"-"` sentinel maps to "omit", never "zero"/"unknown").
* `/api/corporate-announcements?index=equities` - **SECONDARY**, narrative/lifecycle. `desc` in
  a fixed 4-value set (`"Buyback"`, `"Public Announcement - Buyback of Shares"`,
  `"Post Buyback Public Announcement"`, `"Closure of Buy Back"`) drives `ANNOUNCED`/`COMPLETED`;
  `route` (`TENDER`/`OPEN_MARKET`), `buyback_price` and `shares_offered` are read ONLY via a
  narrow regex match on an explicit phrase in the filing's own text (e.g. Gandhi Special Tubes'
  "tender offer... at a price of Rs 900/- per Equity Share... 8,68,100 Equity Shares"; SIS's
  "open market route") - never inferred when the text doesn't state them.
* `/api/corporates-daily-buyback?` - **SECONDARY**, explicit "currently open" proof. A symbol's
  presence in this dedicated daily-purchase-disclosure feed (e.g. SIS Limited, Emami Limited,
  06-Oct-2026) is itself official evidence the buyback is presently `OPEN`, upgrading from
  `ANNOUNCED` - not an inference from silence.

A debt-instrument sibling, `/api/corporates-buyback-redemption?` (bond/debenture redemption),
exists and is explicitly out of scope - a different instrument class from an equity share
buyback.

**Event identity reuses EARNINGS/OFS's `needs_lookup`/`lookup_fn` contract with zero new shared
code**: a symbol's latest non-terminal buyback event is reused across multiple filings for one
cycle (a real example: TeamLease Services filed a Public Announcement on 01-Jul-2026 then a
Letter of Offer on 07-Jul-2026 for the SAME buyback); once a cycle reaches `COMPLETED`
(terminal), the existing `TERMINAL_STATUSES` filter in `_lookup_open_event` already makes the
next genuinely new buyback for that symbol mint a fresh `event_key` - no new logic required.
`WITHDRAWN` is reachable only via an explicit withdrawal/lapse phrase in a filing's own text;
no live example was seen this pass, so it is fixture-tested only.

BUYBACK never uses Chittorgarh/Groww/Moneycontrol or any other third-party tracker - all three
endpoints are NSE's own.

## OPEN_OFFER semantics (`market_events/sources/open_offer.py`)

**Discovery (P2D, 2026-10-07):** real navigation first ruled out a dedicated path - NSE's
"Corporate Filings - Offer Documents" page (`/companies-listing/corporate-filings-offer-documents`)
and its own JS controller (`offer-document.js`) turned out to be about IPO/FPO prospectuses
(abridged-prospectus lookups), not takeover open offers; NSE's structured
`corporates-corporateActions` feed (BUYBACK's PRIMARY source) was probed with a full-year window
and carries **zero** open-offer rows - an open offer is acquirer-driven under SEBI (SAST)
Regulations, 2011, never a company corporate action. SEBI's own site
(`sebi.gov.in/sebi_data/commondocs/...`) hosts only the underlying PDFs (Public
Announcement/Detailed Public Statement/Letter of Offer), no JSON/API. The one genuine official,
machine-readable source turned out to be the SAME general corporate-announcements feed BUYBACK
already reads as its secondary source (`/api/corporate-announcements?index=equities`), filtered
to the exact category `desc == "Public Announcement-Open Offer"` - confirmed live with a 90-day
window (2026-07-09 → 2026-10-07): 9 real rows across 7 distinct target companies, e.g. G-TEC
JAINX EDUCATION LIMITED (`GTECJAINX`, Public Announcement 29-Sep-2026 then Detailed Public
Statement 30-Sep-2026 - the SAME cycle) and Shankara Building Products Limited (`SHANKARA`,
Public Announcement 15-Jul-2026 then an offer-opening announcement/corrigendum under Regulation
18(7) on 04-Sep-2026 - the SAME cycle, 51 days later).

Every open-offer lifecycle filing - PA, DPS, draft/final Letter of Offer, corrigendum, the
offer-opening announcement, the post-offer advertisement, a withdrawal - shares this ONE `desc`
value (unlike BUYBACK's 4-value lifecycle set), so lifecycle stage is read from the filing's own
stated text/regulation, never a separate categorical field:

* `regulation 18(7)` / "offer opening" → `OPEN` (Regulation 18(7) of SEBI (SAST) Regulations,
  2011 is literally the offer-opening-announcement regulation - explicit, not inferred)
* `regulation 18(12)` / "post offer advertisement" → `COMPLETED` (Regulation 18(12) is literally
  the post-offer-advertisement regulation; real example: Niraj Cement Structurals Limited,
  20-Aug-2026)
* "withdrawal"/"lapsed" → `WITHDRAWN` (fixture-tested only; no live example seen this pass)
* anything else (PA/DPS/draft or final Letter of Offer/an unflagged corrigendum) → `ANNOUNCED`

**Acquirer** is read ONLY from the explicit phrase `"made by <name>, the acquirer"` - the real
BLISSGVS/Anupam Rasayan Limited addendum states it this way; a filing naming only its submitting
intermediary/manager (the common case) leaves `acquirer` absent, never guessed from that list.
**Offer price, shares tendered, percentage sought, and open/close tendering dates are not present
in any of the 9 real rows'** one-line `attchmntText` - those numbers live in the linked PDF
(`attchmntFile`), which this engine does not parse (the same "feed's own stated text only" limit
EARNINGS/OFS/BUYBACK already have for anything beyond their own structured fields). Those fields
stay `null`/absent, honestly, rather than fabricated or scraped from a PDF.

**Event identity reuses EARNINGS/OFS/BUYBACK's `needs_lookup`/`lookup_fn` contract with zero new
shared code**: a symbol's latest non-terminal open-offer event is reused across multiple filings
of one cycle (GTECJAINX, SHANKARA above); once a cycle reaches `COMPLETED`/`WITHDRAWN`
(terminal), the existing `TERMINAL_STATUSES` filter in `_lookup_open_event` already makes the
next genuinely new open offer for that symbol mint a fresh `event_key`. One addition beyond
BUYBACK's pattern: because a cycle can span weeks/months while each capture only requests a
trailing `lookback_days` (10-day) window, status is carried forward NON-REGRESSIVELY - each
event's `facts` remember the most-advanced stage any filing has ever stated for it
(`stage_evidence`), and a later run that only re-sees an older, less-advanced filing never
downgrades it back.

OPEN_OFFER never uses Chittorgarh/Groww/Moneycontrol, taxguru.in or any other third-party
tracker - the one endpoint used is NSE's own, already relied on by BUYBACK.

**Bonus finding, now built:** the same `desc` taxonomy also carries `"Delisting"` and
`"Voluntary Delisting"` as their own exact categories - the head start this section originally
flagged is now the basis of the live DELISTING adapter (P2E, below).

## DELISTING semantics (`market_events/sources/delisting.py`)

**Discovery (P2E, 2026-10-07):** official sources were checked in preference order. NSE has
three dedicated static pages (`/static/list/list-of-companies-proposed-to-be-delisted`,
`/static/list/orders-of-delisting`, `/static/regulations/voluntary-delisting`) offering XLSX
roster downloads and a compulsory-delisting "Orders of Delisting Committee" table - real and
official, but XLSX-only (no JSON API), a periodic-roster shape rather than per-filing
granularity, and unverified for live reachability from this environment (a research pass's
sandbox was fully blocked on `nseindia.com`, consistent with CLAUDE.md's documented cloud-IP
blocking). SEBI's site (`sebi.gov.in/sebi_data/docfiles/20626_t.html`) is a plain-text FAQ with
no company-specific structured data or API. BSE's delisting purpose code (`P29` in its
corporate-actions API) was only discoverable via a third-party reverse-engineered wrapper (not
BSE's own documented navigation), 403s on every fetch attempt, carries no explicit
Voluntary/Compulsory field (free-text `Purpose`), and this codebase has zero existing BSE
integration - not pursued. The source actually used is the SAME general corporate-announcements
feed BUYBACK/OPEN_OFFER already read (`/api/corporate-announcements?index=equities`), filtered
to `desc in {"Delisting", "Voluntary Delisting"}` - confirmed live via this project's own
`market.NSE()` client with a 365-day pull: 17 real rows, e.g. Atcom Technologies Limited
(`ATCOM`, compulsorily delisted by BSE Limited, 31-Aug-2026), IZMO Limited (`IZMO`, voluntary
delisting from the Calcutta Stock Exchange, 14-Aug-2026), Jaiprakash Associates Limited
(`JPASSOCIAT`, delisted pursuant to an NCLT-approved Resolution Plan, 11-Jun-2026), and Gammon
India Limited (`GAMMONIND`, a prior delisting withdrawn/reversed by a SAT order, 25-Feb-2026).

A second, TEXT-level guard (`"delist" in attchmntText.lower()`) is required alongside the `desc`
match: two of the 17 rows (KEI Industries, 21-Jan-2026; Fedders Electric, 05-Nov-2025) carry
`desc == "Voluntary Delisting"` but an `attchmntText` that is a plain, unrelated board-meeting
disclosure with no delisting wording at all - a confirmed NSE tagging mismatch. A row passing
`desc` but failing this text check is dropped, never kept on tag alone.

**Voluntary vs compulsory** is read only from explicit text (`"voluntary"` / `compulsor(y|ily)`),
never inferred from circumstance; `desc == "Voluntary Delisting"` is itself also accepted as
explicit evidence for a terse filing whose own prose doesn't repeat the word. A third, genuinely
distinct bucket exists in the live data: NCLT Resolution-Plan-driven delistings (Jaiprakash
Associates, Future Supply Chain Solutions, Fedders Electric, Rolta India - all "pursuant to
Resolution plan approved by... NCLT... under the Insolvency and Bankruptcy Code") state neither
word, so `delisting_type` stays `UNKNOWN` with a `mechanism=NCLT_RESOLUTION_PLAN` fact rather
than being forced into either bucket. This means DELISTING ships with real evidence for
VOLUNTARY and COMPULSORY, plus an honest UNKNOWN for the mechanism-driven cases - not a case of
"only one type provable."

**Exchange scope:** several real rows describe a delisting from an OTHER exchange (BSE /
Calcutta Stock Exchange), reported by NSE to its own members as an FYI, not a statement about
the symbol's own NSE listing (Atcom/Visesh Infotecnics - compulsorily delisted by BSE; IZMO/Jay
Bharat Maruti/ITC - voluntary delisting from CSE). An `other_exchange` fact is populated only
when the text explicitly names that other exchange; its absence is never read as "this is an
NSE delisting" - the adapter only ever states what the filing's own text names.

**Lifecycle mapping** reuses `ANNOUNCED`/`SCHEDULED`/`COMPLETED`/`WITHDRAWN` from `models.py` -
no new status added:

* "withdrawal of delisting" / "restore the listing" → `WITHDRAWN` (explicit reversal; real
  examples: Gammon India, Era Infra Engineering, both a prior delisting reversed by a SAT
  order/BSE notice)
* an explicit `"w.e.f. <date>"` in the future at capture time → `SCHEDULED`; once that date has
  passed → `COMPLETED`
* no parseable effective date (e.g. Jindal Photo's/Hitech Corporation's bare "has informed the
  Exchange about Voluntary Delisting") → `ANNOUNCED`

Floor price, exit/discovered price, bidding open/close dates, and shareholder/exchange approval
dates are never stated in this feed's text in any of the 17 observed rows and stay absent - not
guessed, not backfilled from the XLSX rosters or BSE/SEBI (both investigated and not used, per
above).

**Event identity reuses the SAME `needs_lookup`/`lookup_fn` reconciliation** EARNINGS/BUYBACK/
OPEN_OFFER already use: a symbol's latest non-terminal delisting event is reused across multiple
filings of one cycle (Hitech Corporation filed two identical same-day Voluntary Delisting
notices; Fedders Electric's Nov-2025 board-meeting disclosure and its Feb-2026 final NCLT
delisting notice are the SAME cycle). Once a cycle reaches a terminal status
(`COMPLETED`/`WITHDRAWN`), the next filing for that symbol mints a fresh `event_key` - this also
means a `WITHDRAWN` notice arriving after a prior `COMPLETED` record (Gammon India, Era Infra)
becomes its OWN event rather than mutating the completed one; both are real, distinct historical
facts about the symbol, and this avoids new generic reconciliation machinery.

DELISTING never uses BSE, SEBI, or any third-party aggregator - the one endpoint used is NSE's
own, already relied on by BUYBACK/OPEN_OFFER.

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

Multi-source MERGE (not conflict resolution) is now exercised: BUYBACK combines three NSE feeds
(corporateActions/announcements/daily-buyback) per-symbol, but each field is contributed by
exactly one source role (corporateActions owns record/ex-date/face-value; announcements own
status/route/price/quantity; daily-buyback owns the open-today signal) - so there is no
disagreement to adjudicate, only union. A genuine CONFLICT - two official sources stating
different values for the SAME field - is not yet exercised for any family, but the model is
designed for it: a canonical `event_key` is meant to collapse NSE/BSE/SEBI/company duplicates
of the same filing into one representation while every source's own reference is preserved in
`facts`/`source_reference`. A genuine conflict between two official sources must surface as
`VALIDATION_FAILED` (public scene fails closed) rather than being silently resolved by guessing.

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
* **BUYBACK is now live (P2C, 2026-10-07).** Discovered via NSE's own structured
  corporate-actions feed (`subject == "Buy Back"`, an exact categorical match - stronger
  evidence than either EARNINGS or OFS's text/listing-based classification), cross-referenced
  with the corporate-announcements and daily-buyback feeds. See "BUYBACK semantics" above.
* **OPEN_OFFER is now live (P2D, 2026-10-07)**, using the exact `Public Announcement-Open Offer`
  category in the corporate-announcements feed that the prior pass (P2A) had already spotted as
  a head start. Reachability from the actual production runner is unverified for the same
  general reason as EARNINGS (GitHub Actions runners are known to be blocked for
  `nseindia.com`); a `SOURCE_UNAVAILABLE` there is a normal, handled outcome. Offer
  price/shares/percentage/tendering dates are not present in the feed's own text and are never
  fabricated - see "OPEN_OFFER semantics" above.
* **DELISTING is now live (P2E, 2026-10-07)**, using the same corporate-announcements feed's
  `desc in {"Delisting", "Voluntary Delisting"}` categories - the head start the OPEN_OFFER pass
  had already spotted. NSE's separate XLSX-based delisting rosters and BSE's/SEBI's sources were
  investigated and intentionally NOT used (no JSON API / bot-blocked+unofficial-discovery /
  PDF-only respectively) - see "DELISTING semantics" above for the full evidence and the
  false-positive tagging guard it required. Reachability from the actual production runner is
  unverified for the same general reason as EARNINGS/OPEN_OFFER; a `SOURCE_UNAVAILABLE` there is
  a normal, handled outcome. Floor/exit price and bidding dates are not present in the feed's
  own text and are never fabricated.
* **GOVT_SECURITIES_AUCTION is now live (P2F, 2026-10-07)**, using RBI's own live press-release
  feed (`https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=2757`) rather than the
  hand-maintained half-yearly-calendar file originally planned at P2B - the weekly
  announcement/result press releases turned out to carry fully structured, directly-parseable
  HTML, making a live adapter both possible and more current than a manually-entered calendar.
  See "GOVT_SECURITIES_AUCTION semantics" above for scope, event identity and the
  announcement+result merge design. Reachability from the actual production runner (GitHub
  Actions) is unverified - RBI's site is a different host from `nseindia.com`, so the
  documented NSE cloud-IP blocking doesn't directly apply, but this has not been tested live
  from a runner; a `SOURCE_UNAVAILABLE` there is a normal, handled outcome. State Development
  Loans (SDL), Cash Management Bills, switches, and an auction-DATE revision (as opposed to an
  amount revision) remain explicitly out of scope / a known limitation - see the semantics
  section above.
* Detailed earnings-result interpretation (revenue/EBITDA/PAT/margin parsing) is explicitly out
  of scope for V1 - a later packet.
* Source-conflict handling (two official sources disagreeing on one filing) is designed for but
  unexercised, since no family currently reads more than one source.
* The IPO projection window (-3..+7 days per Desk page load) re-reads the stored official
  snapshot and re-runs `ipo_watch`'s own selection logic on every request - cheap at today's IPO
  volume, but worth revisiting if the Desk ever needs to serve many concurrent requests.
