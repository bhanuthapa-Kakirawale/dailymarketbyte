# Institutional Flow Intelligence V1

Three official institutional-flow sources, each kept in its own time semantics, feeding a
deterministic materiality/sector layer that PRE, POST and the Private Desk may optionally draw
on. This is additive: the canonical report's existing FII/DII facts (`market.fii_dii_nse`,
`report.institutional_flows`) and the FLOWS scene they already produce are untouched.

## Purpose

The project previously knew only NSE's same-day net FII/DII cash figures, with gross buy/sell
and NSE's own timestamp discarded after one run, and no sector- or custodian-level view at all.
V1 adds:

* NSE's own gross + net FII/FPI and DII cash-market flow, captured and stored immutably
  (`institutional_flows.sources.nse`) - a second, richer read of the endpoint
  `market.fii_dii_nse` already uses, never a replacement for it.
* CDSL's daily depository-reported FPI flow (`institutional_flows.sources.cdsl`).
* NSDL's fortnightly sector-wise FPI net investment, with the latest and previous published
  fortnight always discovered from NSDL's own selection page, never hard-coded
  (`institutional_flows.sources.nsdl`).
* A deterministic materiality/streak/reversal layer over NSE history
  (`intelligence.flow_materiality`) and a deterministic sector-flow/selection layer over NSDL
  (`institutional_flows.sector_flow`).
* Optional PRE/POST editorial use and a Private Desk "Institutional Intelligence" area.

## Source hierarchy and semantics

| Source | Class | Identity (`report_key`) | What it actually says |
|---|---|---|---|
| NSE | `EXCHANGE_PROVISIONAL` | the exchange's own trade date | same-day, explicitly provisional, subject to revision |
| CDSL | `DEPOSITORY_REPORTED` | CDSL's own reporting date | "compiled from custodian reports ... trades on and upto the previous trading day(s)" - CDSL's own words, never shortened to "yesterday" |
| NSDL | `DEPOSITORY_FORTNIGHTLY` | the fortnight-end date | an official fortnightly regulatory publication |

These three identities are **never** forced onto one shared "session date" - NSE's trade date,
CDSL's reporting date and NSDL's fortnight end are different things, and `report_key` is
source-specific by design (`institutional_flows/models.py`).

### NSE

`institutional_flows.sources.nse.fetch_nse_fii_dii` re-reads
`https://www.nseindia.com/api/fiidiiTradeReact` (the same endpoint `market.fii_dii_nse` already
calls) through the existing `market.NSE` client, but keeps gross purchases/sales as well as net,
for both FPI and DII, requiring both rows to agree on one date and `buy - sell == net` within
0.05 cr. A schema/arithmetic mismatch is `PARSE_ERROR`/`VALIDATION_FAILED`; the request itself
failing is `SOURCE_UNAVAILABLE`.

### CDSL

`institutional_flows.sources.cdsl.fetch_cdsl_daily` reads
`https://www.cdslindia.com/eservices/publications/fiidaily` (verified live 2026-10-05; the
legacy `publications/FIIDailyData.aspx` page is **stale** - its equity table still showed
30-Aug-2024 on that date - and is never used). The page's own table is category × route
(Equity/Debt-General Limit/Debt-VRR/Debt-FAR/Hybrid/Mutual Funds/AIFs × Stock
Exchange/Primary market & others/Sub-total/Total) with gross purchases, gross sales, net
investment (INR crore and USD million) and the day's conversion rate; a second table of FPI
derivative trades is captured into `extra.raw_derivatives` with `used_in_p1: false` and is
never interpreted. The source's own sentence - "compiled on the basis of reports submitted to
depositories by custodians on `<date>` and constitutes trades conducted by FIIs/FPIs on and
upto the previous trading day(s)" - is kept verbatim in `represented_period.source_note`, and
`represented_period.basis` stays `UNKNOWN`: this pipeline never asserts a specific trading
session for a CDSL figure. Internal consistency is checked (purchases - sales == net per row,
per-category rows sum to that category's Sub-total, Sub-totals sum to the Total row); a
mismatch is `VALIDATION_FAILED`, a changed table header is `PARSE_ERROR` (schema change).

### NSDL

`institutional_flows.sources.nsdl` always **discovers** the fortnight to fetch rather than
assuming one:

1. `discover()` parses `<select id="ddlfortnighly">` on
   `https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx` (verified live
   2026-10-05: 352 options, newest first, e.g. `value="~/StaticReports/.../FIIInvestSector_Sep152026.html"`
   / label `SEP 15, 2026`), parses each label as a date, and resolves `~/` paths against
   `https://www.fpi.nsdl.co.in/web/`.
2. A label date that maps to two different URLs is **dropped entirely**, never resolved by
   picking one (verified live: NSDL's own dropdown carries exactly one such historical
   duplicate, "Feb 28, 2022", unrelated to any current fortnight). Discovery fails only if
   fewer than 2 unambiguous dates remain.
3. The latest and the next-latest dates (by parsed date, never page order) are fetched and
   parsed with `parse_report()`.

Each report's table is four column blocks - `[AUC as of the prior period end, Net Investment
for the prior period, Net Investment for the current period, AUC as of the current period end]`
- each split into `IN INR Cr.` / `IN USD Mn`, each split into 12 fixed sub-categories (Equity,
Debt General Limit, Debt VRR, Debt-FAR, Hybrid, then the same five Mutual-Funds sub-categories,
AIF, Total): 2 leading columns + 4 blocks × 2 currencies × 12 categories = 98 columns. The
parser locates these blocks **structurally** - by walking the header rows and checking the
block kinds, the per-block date text and every category label - rather than assuming fixed
column indices, so a genuine NSDL layout change surfaces as `PARSE_ERROR` instead of silently
reading a different column as a sector's flow. The current period's own header date must equal
the dropdown date requested; sector rows plus the per-column Grand Total are cross-checked
within a small rounding tolerance. 22 named sectors plus Sovereign and Others were present in
the live 2026-09-15 report; rows outside the numbered sector list (Sovereign/Others/Grand
Total) are never treated as sectors.

## Flow vs AUC

NSDL's report carries both **Net Investment** (a flow measure for the stated period) and
**AUC** (asset under custody - a value/exposure measure that also moves because market prices
changed). This pipeline never computes `AUC change = flow`: AUC is captured as its own `metric`
("auc"), always alongside but never substituting for `net_investment`, and the Private Desk
always labels it "AUC (value, not flow)".

## Materiality (NSE)

`intelligence.flow_materiality` (`RULE_VERSION = "inst-flow-materiality-1.0"`) reports two
independent, deterministic dimensions per participant (FII, DII) from canonical history
(`intelligence.history`, eligible facts only) - never a model, never a prediction:

* **Magnitude**: needs >= 10 prior eligible sessions (`MATERIALITY_LOOKBACK`), else
  `INSUFFICIENT_HISTORY`. `LARGE_NET_BUY`/`LARGE_NET_SELL` when `|current| >= 2.0x` the median
  `|value|` of the prior 10 sessions **and** `|current| >= Rs 1,000 cr`; otherwise
  `NORMAL_RANGE`.
* **Streak/reversal**: `DIRECTION_CONTINUES` with `streak_milestone=True` at length 4 or 5, or
  every 5th session from 10 onward (`intelligence.history.continuity_streak` - a CONTINUITY
  claim that breaks at any recorded session whose evidence does not continue the run, exactly
  like the existing `fii-flow-streak`/`dii-flow-streak` insights). `DIRECTION_REVERSES` when
  today's sign flips after an unbroken opposite-direction run of >= 3 prior recorded sessions
  **and** `|current| >= 1.0x` the prior-10 median; otherwise `NO_MEANINGFUL_CHANGE`.

`is_material()` is true for `LARGE_NET_BUY`/`LARGE_NET_SELL`, a streak milestone, or a reversal
- never for `NORMAL_RANGE`/`INSUFFICIENT_HISTORY` alone. This context is computed once, in
`intelligence.engine.build_snapshot`, and stored as `IntelligenceSnapshot.flow_context` -
**not** an insight, so it never competes for `selected()` screen time or hook candidate
selection.

## Sector flow and selection (NSDL)

`institutional_flows.sector_flow.sector_flows()` reads one NSDL snapshot's own current/previous
"equity, net investment" columns per sector and classifies direction: `INFLOW_CONTINUES`,
`OUTFLOW_CONTINUES`, `REVERSAL_TO_INFLOW`, `REVERSAL_TO_OUTFLOW`, or `NO_MEANINGFUL_CHANGE`
(both readings under Rs 100 cr). `select_public_bars()` then picks up to 4 for a public scene:
the largest net inflow and the largest net outflow (each needing `|current| >= Rs 500 cr`),
then up to two more by largest `|change|` (`>= Rs 1,000 cr`), ties broken alphabetically;
Sovereign/Others are never candidates; nothing qualifying returns an empty list, never a padded
one.

## Sector mapping

`institutional_flows.sector_map.NSDL_TO_SECTOR` is an explicit, hand-reviewed table from NSDL's
own "Sectors" label to the same display sector `market_structure.sectors.INDUSTRY_TO_SECTOR`
already uses for the equivalent NSE Industry name - most labels match outright, a few only in
punctuation ("Oil, Gas & Consumable Fuels" vs NSE's "Oil Gas & Consumable Fuels"). NSDL
classifies by BSE's "Common Industry Classification" (its own footnote); NIFTY 200 is
classified from NSE Indices' "Industry" column - the mapping is **label-level equivalence**,
not proof the two systems classify every company identically. "Utilities" has no NSE-Industry
equivalent in the current taxonomy and stays `UNMAPPED`; "Sovereign"/"Others"/"Grand Total" are
`NOT_A_SECTOR`. No fuzzy matching, no LLM assignment: a label not in the table is `UNMAPPED`,
never guessed (`tests/test_institutional_intelligence.py` pins every mapped label).

## Change detection

`institutional_flows.service.InstitutionalFlowService.capture()` compares the normalized facts
(`records_checksum` - never the raw page, so a changing viewstate/session token can never make
an unchanged report look new) against the newest stored revision for that `(source,
report_key)`:

* no prior snapshot of that `report_key` -> `NEW_REPORT` (and `bootstrap=True` when that
  source had **no** prior snapshot at all - a bootstrap report carries no "new today" meaning
  and is never shown as one in PRE).
* same checksum -> `UNCHANGED` (nothing written - retrieval time alone is never "new").
* different checksum -> `REVISED` (a new, separately-numbered revision file; unlike
  `official_snapshots`, a provisional NSE/CDSL/NSDL figure may legitimately be restated later,
  so an earlier validated revision never blocks a later one - what stays immutable is each
  revision FILE, never rewritten once written).

Every attempt (success or failure) is additionally logged to
`output/institutional_flows/attempts/<date>/` for Data Quality / debugging, and is never
consulted to decide what "the current snapshot" is.

## Sector flow materiality for POST/PRE display

POST's `FLOWS` section (`presentation.post_plan.plan_post_sections`) reads the FLOWS scene's
`metadata["materiality"]` (set by `editorial.planner._flows_scene` from
`snapshot.flow_context`). With >= 10 prior eligible sessions on both sides, FLOWS qualifies iff
any participant is `LARGE_NET_BUY`/`LARGE_NET_SELL`, a streak milestone, or a reversal; the
reason names the rule and the numbers. Below that history depth, the original fixed
`FLOW_MIN_CRORE` (Rs 1,000 cr) threshold still applies, with the reason prefixed
`INSUFFICIENT_HISTORY:` so it is never mistaken for a materiality verdict.

## PRE use

PRE (`presentation.pre_plan._flows`) tries, in order, and shows **at most one**:

1. **A new CDSL report** - its own reporting date falls in `[previous_session, pre_date)` and
   it is not this source's bootstrap report. Shown as two bars (Stock Exchange / Primary &
   Others, equity, net investment), headline "FPIs were net buyers/sellers of equity in the
   latest depository-reported figures", provenance `SOURCE: CDSL` / `REPORT DATE: ...`.
2. **A new NSDL sector report** - its fortnight ends within 5 days of `pre_date` and it is not
   this source's bootstrap report. Shown as up to 4 sector bars from
   `sector_flow.select_public_bars()`, headline "New fortnightly data: FPI equity flow by
   sector", provenance `SOURCE: NSDL` / `FORTNIGHT: ...`.
3. **A genuinely new NSE story** - a streak milestone or a reversal (`flow_materiality`), never
   a bare single-day magnitude alone, because POST already carries that the evening before and
   PRE must not just repeat it. Headline: "FIIs/DIIs have been net buyers/sellers for N
   consecutive reported sessions" or "... reversing a N-session run".
4. **Nothing** - with fewer than 10 prior eligible NSE sessions the original fixed-threshold
   rule still applies (reason prefixed `INSUFFICIENT_HISTORY:`); otherwise the FLOWS section is
   simply omitted, with the reason recorded (`plan.omitted`).

The FLOWS scene stays optional (`OPTIONAL_PRIORITY`), its duration unchanged (`DUR["FLOWS"]`),
and its on-screen wording is always "net buyers/sellers" - including the previous-session watch
card fallback, which previously said "bought"/"sold" and has been corrected to match POST.

Replay (`institutional_flows.context.load_institutional(..., live=False)`) never fetches: a
candidate counts only if its snapshot's `first_retrieved_at` was strictly before the brief's
`as_of` cutoff, so a report fetched later can never appear to have been available earlier, even
if its own date is on or before the session. A live PRE run may capture a missing CDSL/NSDL
snapshot (`CAPTURED_THIS_RUN`, persisted for next time); a replay of a past morning only ever
reads what was already on disk and reports `HISTORICAL_SNAPSHOT_UNAVAILABLE` otherwise.

## Private Desk use

`private_desk/services/institutional.py` reads only stored snapshots (`institutional_flows`
never fetches on a page request - only the REPORT/PRE jobs capture). Three source-specific
freshness windows (deliberately **not** folded into `services.freshness`, which would flag a
fortnightly report as STALE on every single day it is not published): NSE FRESH within 1 day of
the viewed session, CDSL within 3 days, NSDL within 20 days (a fortnight plus processing
slack).

* **Dashboard**: a compact "Institutional flow" panel (NSE latest provisional figures, CDSL
  latest report date, NSDL latest fortnight) plus a link to the full page.
* **`/institutional`**: NSE's latest figures and last 10 reported sessions; CDSL's category ×
  route table (gross purchases / gross sales / net investment - never "buy"/"sell"); NSDL's
  sector table (current/previous fortnight, change, direction) with AUC shown separately.
* **Sectors page**: a separate "FPI sector flow (NSDL)" table, joined by `sector_map` - the
  existing NIFTY 200 breadth table and its underlying `sector_table()`/`packets()` rows are
  untouched.
* **Stock page (panel F)**: "FPI sector context (sector-level, not stock-level)" - the mapped
  sector's latest/previous fortnight flow, or an explicit `UNAVAILABLE`/`UNMAPPED`, with the
  fixed note "Sector flow is not evidence of FPI activity in this stock." It never says an FPI
  bought or sold that stock.
* **Data Quality**: per-source latest/previous status, NSDL sector-mapping mapped/unmapped
  counts, and the most recent capture attempts.

Nothing institutional feeds `services/attention.py`, `candidates.py`, the Radar order, the
Market Regime classifier (its pre-existing, unrelated "institutional flow" CONTEXT dimension
reads the *canonical report's* FII/DII, not this package, and is untouched), or
`PrivateCandidatePacket` - all proven by dedicated tests
(`tests/test_private_desk_institutional.py`).

## Replay / historical behaviour

Exactly the `official_snapshots` pattern: live acquisition may capture the current official
page; a stored snapshot becomes immutable history; replay (PRE reconstructions of a past
morning, and any `--session-date` run) reads stored snapshots only and never pretends today's
page existed on an earlier morning.

## Rights

Every new source (`core.sources.SRC_NSE_FIIDII_API`, `SRC_CDSL_FPI_DAILY`,
`SRC_NSDL_FPI_FORTNIGHTLY`) is registered `display_rights_status="UNREVIEWED"`, which
`publication.rights.rights_for` resolves to `REVIEW_REQUIRED` - the same fail-closed default
every other not-yet-reviewed official source gets. Under the default
`PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK`, any production publication relying on one of these
sources is blocked at the audit (`publication_rights`), exactly like NSE's existing
`nse_website` source; renders stay complete, review/shadow renders show the content, and
`ATTRIBUTED_EOD` is an explicit, separate owner decision this packet does not make. New facts
are tagged `FPI_DII_PROVISIONAL` / `FPI_DEPOSITORY_REPORTED` / `FPI_SECTOR_FLOW`
(`institutional_flows/facts.py`) and classified `Origin.OFFICIAL_DEPOSITORY` (CDSL/NSDL) or
`MARKET_DATA` (NSE, matching the existing canonical FII/DII classification) - never `AI` or
`NEWS`.

## Known limitations

* NSDL sector labels are matched to DMB's display sectors by an explicit table, not a live
  cross-check against NSE's Industry column; a genuine NSDL re-classification would need the
  table updated by hand (`institutional_flows/sector_map.py`).
* CDSL's represented period stays `UNKNOWN` by design - the source itself does not state a
  single trading session, and this pipeline will not invent one even if a future format change
  makes it tempting to guess.
* NSDL's historical dropdown carries one known duplicate-date entry ("Feb 28, 2022") that
  discovery permanently excludes; a future NSDL correction to that entry needs no code change
  (duplicates are detected generically, not by that specific date).
* The NSDL table parser is structural but still pinned to the current four-block, 12-category,
  98-column shape; a genuine NSDL redesign will show up as `PARSE_ERROR` and need a new parser
  version, by design (never silent misalignment).
* FPI derivative trades (the second CDSL table) are parsed and stored (`extra.raw_derivatives`)
  but never interpreted in V1 (`used_in_p1: false`) - explicitly out of scope, per the brief.
