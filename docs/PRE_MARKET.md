# PRE-MARKET Short (V1)

The PRE Short answers one question: **"What should I know before the bell?"** It is the
morning sibling of the frozen POST Short: same brand, same chrome, same hook engine, same
scene grammar. It is not yesterday's recap replayed, not a prediction and not a stock-pick list.

```
canonical report of the PREVIOUS session ──┐   (MarketHistory -> stored JSON; never rebuilt)
providers.premarket (pre-open readings) ───┤   (dated / timestamped, freshness-checked)
core.event_calendar (verified schedules) ──┼──> presentation.pre_plan.build_pre_brief
previous session's published Radar ────────┘            │  PreMarketBrief
                                                         ▼
                                       PreEditorialPlanner.plan()  (deterministic, no model)
                                                         │  PreSectionPlan (+ reasons, omitted)
                                                         ▼
               hooks (approved Dynamic Hook Engine) ─> daily_video.pre_storyboard ─> Composer
                                                         │
                                  products.premarket.render_pre: language + content gates,
                                  runtime ceiling, freeze-frame QA, MP4, JSON artifacts
```

Run it:

```
python main.py --mode premarket                          # today, cutoff = now (IST)
python main.py --mode premarket --shadow                 # live shadow run -> output/pre_shadow/<date>/
python main.py --mode premarket --session-date 2026-09-25   # reconstruct that morning at 07:45
python main.py --mode premarket --demo                   # synthetic, badged
python render_pre_phase1.py [--frames-only]              # validation scenarios -> output/pre_phase1/
python main.py            # POST, unchanged (== --mode postmarket)
```

V1 never uploads: `--upload` with `--mode premarket` is refused (and always in `--shadow`).

The previous session's canonical report is built the evening before by the REPORT job
(`python main.py --mode report`, docs/PRODUCTION_SCHEDULE.md); PRE only reads it
(`products.premarket.require_previous_report`: the calendar's previous session, exactly, and
publication-ready - else BLOCKED). `--shadow` is the production dress rehearsal: live data, the
full render + QA + run history, a `shadow_manifest.json` and a GIFT audit per morning, and never
an upload.

## Structure

| # | Section | Core? | Appears when |
|---|---|---|---|
| 0 | Dynamic Hook | core | always (the approved engine; PRE archetypes) |
| 1 | OVERNIGHT | core | at least one FRESH global cue or a FRESH GIFT reading |
| 2 | SETUP (previous session) | core | always - if it cannot be built, the Short is BLOCKED |
| 3 | FLOWS (previous session) | optional | one side >= Rs 3,000 cr, or opposite sides each >= Rs 1,000 cr |
| 4 | VIX (previous session) | optional | FRESH and \|change\| >= 8% |
| 5 | SECTORS (previous session) | optional | leader-laggard spread >= 1.0 pt or any sector >= 1.5% |
| 6 | EVENT (today) | core slot | a VERIFIED scheduled event today |
| 7 | STOCK WATCH | optional | the previous session's published Radar stories exist (max 2) |
| 8 | WATCH AT THE OPEN | core | always, 1-3 cards (only cards that carry a real fact) |
| 9 | CLOSING | core | "That's your setup before the bell." + brand + one CTA |

At most **two** optional sections, in priority `STOCK_WATCH > FLOWS > VIX > SECTORS`. A
qualified section that loses on budget is recorded with that reason, and its fact may still
appear as one Watch-at-the-open card. Runtime ceiling 65 s (the planner drops optional sections,
lowest priority first, if ever needed; the render refuses anything above it).

The order is: overnight -> the previous session (setup, then its optional context) -> today
(event) -> stocks -> what to watch -> close. Every decision has a written reason in
`pre_section_plan_<date>.json`; every omission is in `omitted`.

## Freshness (core/freshness.py)

Every pre-open reading carries `source`, `retrieved_at`, `market_date` / `market_timestamp`
and a `freshness` verdict, recorded at acquisition. Only FRESH readings reach the screen.

| Reading | Kind | FRESH when |
|---|---|---|
| S&P 500, Nasdaq, Dow | SESSION_CLOSE | bar date == the latest weekday before the PRE date (Mon -> Fri) AND the cutoff is after that session's 16:00 New York close + 20 min (DST-correct). An older bar (US holiday, missing bar) is STALE; a bar dated after is FUTURE. |
| Nikkei 225, Hang Seng | LIVE | the last 5-minute bar that **ended** at/before the cutoff, dated the PRE date, at most 30 min old; change vs that market's last daily close before the PRE date. A market that has not opened (holiday) has no reading. |
| GIFT Nifty | LIVE | NSE IX near-month NIFTY future, its own last-trade timestamp: dated the PRE date, at/before the cutoff, at most **20 min** old, and taken **before the 09:15 IST open** (see GIFT Nifty below). |
| India VIX | SESSION_CLOSE | the reading's session == the canonical previous session; the change compares it with the canonical session before that (never "the previous row"); cross-checked against the report's own VIX fact (> 1% apart -> CONFLICT, no change shown). |
| FII/DII, sectors, Nifty | canonical | from the previous session's canonical report, whose session must equal the calendar's previous session. |

Timestamps are shown on screen for every LIVE reading ("AT 7:45 AM IST"); US closes carry
their day ("US CLOSE · THU").

## Global-cue selection

Inputs: the FRESH cues only. Lead = the largest absolute move. Then one CONTRAST (the largest
opposite-sign cue >= 0.30%), then the largest cue from a region not yet shown; at most three
cue cards. If every fresh cue is below 0.50%, the scene says so plainly ("A quiet night for
global markets" / "Every tracked market moved less than 0.5%") and shows no contrast.
Takeaways are fixed templates: "All 5 tracked markets fell", "3 of 5 tracked markets rose",
"Not every market moved the same way: Japan's Nikkei rose 1.12%". A LIVE lead is written with
its time: "Hong Kong's Hang Seng was 1.73% lower at 7:45 AM". Nothing links one market to
another, and no cue is said to "point to" an Indian open.

V1 universe: S&P 500, Nasdaq, Dow (US closes), Nikkei 225, Hang Seng (live). Not in V1: Brent
(its Yahoo ticker is verified wrong, and the only other source is Gemini), gold and USD/INR
(24-hour instruments with no unambiguous "previous close" for a pre-open snapshot). Adding
them needs a verified source and a freshness rule first.

## GIFT Nifty

GIFT is shown only as a timestamped, past-tense fact - "GIFT Nifty was 0.60% lower at 7:52 AM
IST", reference line "VS ITS THU SETTLEMENT" - never "points to", never "the market will
open", and never compared with Nifty's spot close (a future-vs-spot gap carries the futures
basis and reads as an opening call). The GIFT strip sits under the overnight cues; a Watch card
may repeat it ("GIFT Nifty -0.60%", "At 7:52 AM IST, vs its Thu settlement").

**Source (pre-data-sources phase): the exchange that lists it - NSE IX** (`providers/gift_nifty.py`,
wired by `products.premarket.build_real_brief` through the `gift_fn` seam; the offline default
in `fetch_premarket_quotes` is still "no source"):

| | |
|---|---|
| live quote | `GET https://www.nseix.com/api/market-rate?type=derivative` - one row per NIFTY FUTIDX contract with `LASTPRICE`, `DAYCHANGE` and `TIMESTMP` (the contract's last-trade time, IST). Source `nseix_market_rate`, PRIMARY, group `NSE_IX`. |
| reference | NSE IX's official Daily Settlement Price file `G_T_DSP_PRICE_<DDMMYYYY>.CSV`: the latest one dated before the PRE date (a missing file - NSE IX holiday - steps back, max 7 days). Source `nseix_settlement_file`, same group. |
| contract | near month = earliest expiry on/after the PRE date (never "most traded": that would switch the reference silently during rollover). |
| change | `LTP / previous DSP - 1` - GIFT Nifty's own move since its last settlement. |

Checks, all fail-closed (a failure omits GIFT; nothing blocks the Short):
- the DSP file must carry the date it is named for;
- the exchange's own `DAYCHANGE` must imply the same reference (`LTP - DAYCHANGE == DSP` within
  0.51 pt) - verified live 2026-09-25 21:29 IST (Sep contract 23183.0 - (-5.50) = 23188.5 =
  DSP 25-Sep); a disagreement is `CONFLICT`;
- |change| <= 10% and the future within 5% of the canonical Nifty close (plausibility only,
  never shown) - else `REJECTED`;
- freshness as above. On a live run (fetched <= 10 min after the cutoff) freshness is judged at
  retrieval time (<= 60 s exchange clock skew tolerated); on a reconstruction of a past morning
  it is judged at the cutoff, so the live reading is `FUTURE` - a past morning never shows GIFT
  (NSE IX publishes no intraday history).

Every reading records `source`, `source_reference` (URL), `retrieved_at`, `market_timestamp`,
`reference_date`, freshness, validation and a `provenance` block (contract, all contracts seen,
settlement URL/attempts, implied reference, consistency, judged-at). Limitations: undocumented
website API (shape can change -> UNAVAILABLE), display rights UNREVIEWED, reachability from
GitHub runners unverified, and the session-1 (morning) `DAYCHANGE` reference was verified only
for the evening session - the first live morning's audit must show `CONSISTENT`.

**Publication gate (default OFF).** Display rights are not established
(docs/NSEIX_RIGHTS_REVIEW.md), so `operations/gift_policy.py` gates display on
`GIFT_NIFTY_PUBLICATION_ENABLED=true` AND a non-empty `GIFT_NIFTY_PUBLICATION_APPROVAL`
reference. Gate closed: publication mode does not fetch GIFT (`POLICY_DISABLED`, not a
degradation); shadow mode fetches, validates and audits it (`gift_audit_<date>.json`:
available / valid / allowed / displayed / reason + contract, expiry, live price, exchange time,
previous settlement, computed %, exchange change, reconciliation, freshness, verdict) and then
removes it from the brief (`brief.gift = None`, `brief.gift_policy.withheld`), so nothing can
display it. The gate never blocks PRE.

## India VIX

Own scene only when FRESH and the previous-session change is >= 8%: "India VIX closed 22.6%
higher on Thursday", the two closes as bars from zero, and a neutral note ("India's volatility
index, from Nifty option prices"). VIX is drawn in the volume amber and a Watch card is
neutral-coloured: a VIX rise is not styled as good or bad news. Never "VIX up = market will
fall".

## Previous-session setup

Always labelled with its day: the chip is `YESTERDAY'S SETUP` only when the previous session
was literally yesterday, otherwise `FRIDAY'S SETUP` (Monday) or the session's weekday; every
headline names the weekday ("Nifty closed sharply lower on Thursday"). Optional sections from
the previous session carry `· THU` in their chip.

- A NEW structural event (a 20-day range break, else a cross of the 20-day average - the POST
  `structural_event` rule) -> the POST chart grammar (`MarketStructureScene`), takeaway "Nifty
  closed below its 20-day low on Thursday."
- Otherwise the compact POST market-pulse card: close, change, and where the close sat in the
  day's range.

Nothing in the scene implies the pattern predicts today.

## Events (core/event_calendar.py)

`ScheduledEvent(event_name, event_date, event_time, time_label, event_type, tag, source,
importance, validation_status, verified_on)`. Only two statuses are ever shown:

- `OFFICIAL_SCHEDULE` - entered by hand from the organiser's page, with that page and the
  check date. V1: the 2026 FOMC decision days from federalreserve.gov (checked 2026-09-25).
  The statement time is not on that page, so no IST time is claimed ("US time - overnight in
  India"). 2027 FOMC dates are `TENTATIVE` (the Fed says so) and never shown.
  **RBI MPC 2026-27** lives in the controlled official-event file `data/official_events.json`,
  read from RBI's own **Press Release 2025-2026/2306** (23 Mar 2026, "Meeting Schedule of the
  Monetary Policy Committee for 2026-2027", www.rbi.org.in prid=62422) on 2026-09-25: decision
  days 8 Apr, 5 Jun, 5 Aug, **7 Oct**, 4 Dec 2026, 5 Feb 2027 (each the meeting's final day).
  RBI's schedule states no time, so none is claimed: the card says "Final day of the RBI MPC
  meeting", source "RBI press release".

The controlled file (`load_official_events()`): every entry carries `source_url`,
`source_reference`, the exact quoted `source_text` line, `retrieved_at` and `verified_on`. The
loader REJECTS (fails closed, recorded in the audit) an entry that is incomplete, cites a host
that is not the organiser's own (`OFFICIAL_HOSTS`: rbi.org.in hosts), whose `meeting_dates` do
not match its own quoted line, whose `event_date` is not the final meeting day, that claims an
`event_time` without a quoted `time_source_text`, or that is not an official status. An
`OFFICIAL_SCHEDULE` last verified more than 180 days before the day being planned is not shown
(an official schedule can be amended). A missing/corrupt file means no file events
(`UNAVAILABLE`/`INVALID`, recorded in run history) - never invented ones. The daily run never
fetches RBI's site; `python verify_official_events.py [--stamp]` re-checks the file against the
live RBI page (every entry's meeting on the page, no page meeting missing from the file, the
release number present) and only then may re-stamp `verified_on`. Verified 2026-09-25: all 6
match.
- `RULE_DERIVED` - Nifty F&O expiry on the configured weekday (`NIFTY_EXPIRY_WEEKDAY`) when the
  canonical calendar says that day is a session; monthly when no same-weekday session follows
  in the month. If the expiry weekday is a holiday, the exchange moves expiry - that move is
  not guessed, no event is shown.

News headlines in the report (`report.events`, Google News RSS) are never "today's event": they
are recorded in `omitted` with "news headline ..., not a verified schedule". A weekly expiry is
a briefing item, never an `EVENT_LED` hook; monthly expiry and FED/RBI are.

## Hook coherence (PRE V1 final polish)

- **Curiosity entity first.** The first, full-stage teaser beat shows the entity the curiosity
  line names (`hooks.engine.lead_beat_first`: the line's first named entity, read with the
  validator's alias index -> the first sheet beat carrying a fact about it). The other beats
  follow as supporting / contrasting facts, one beat per entity, at most three; the settled
  hero is still that entity. No beat for it (e.g. "Two things before the bell.") -> the beats
  are unchanged. Applied to every hook source (deterministic, Gemini, Gemini-choice) in PRE
  mode only, and kept only if the lines still validate against the reordered beats. POST beat
  order (hooks/diversity) is untouched. Real 25 Sep: "Hang Seng was 1.73% lower at 7:45 AM." ->
  beats Hang Seng, Nikkei, Dow (was Nikkei, Dow, Nifty).
- **Summary in play order.** In PRE the summary line lists its sections in the order the
  viewer will see them (`sheet.sections` is built from the PreSectionPlan's `order`), not in
  POST's static order; it can only ever name sections the plan contains.

## Watch at the open

1-3 numbered cards, each a fact with its day or time - never an action. Card 1 is always the
Nifty reference: the structural level ("20-day low: 23,116.1 - Nifty closed below it on
Thursday, at 23,063.1"), else the day's high/low the close sat near, else the day's range.
Then, in order, up to two of: the scheduled event (its time is the cue), VIX (>= 5% move, if
VIX has no scene), the weakest/strongest sector (if no sector scene, and only if that sector
moved >= 1.0% - a +0.31% "strongest sector" on a quiet morning adds nothing and is not shown to
fill time; a quiet morning may therefore have a single card and a ~24 s Short), the first Radar stock (if
no stock-watch scene), flows (if no flows scene), GIFT, the lead overnight cue. A fact that
already has its own scene is not repeated (a timed event is the exception). The subline says
it plainly: "Reference points from Thursday and overnight, not trade signals".

## Stock watch

At most two stocks, taken from the previous session's **published** Radar stories - those a
completed, QA-passed POST artifact actually showed (`radar_publications`, via
`products.radar_publication.published_instruments`), in on-screen order, move-guarded - no new
overnight stock selector. A story the Radar only SELECTED (POST failed, or the POST renderer has
no Radar section - the current legacy production POST) is never shown; no published story means
no stock-watch section. Each
card: symbol, the session move labelled with its day ("THURSDAY'S CLOSE" under the figure),
its recent closes and the Radar story's own fixed-template sentence, prefixed with the day
("Thursday: Price fell below its recent range with exceptionally high volume."), under a
`STOCK WATCH · THU` chip - it can never read as a live pre-open signal. Never "watch for continuation", never "likely to".

## Language guard

`pre_language_issues()` runs on every public string of the Short (hook included) before
anything is rendered: the hook engine's prediction / causal / recommendation / hype lists, plus
PRE-specific phrasings ("open higher", "softer start", "continuation", "rally", "hold", "watch
for", ", so", "which means", "in response to") and content safety. Any hit blocks the render -
a hit is a template bug. Flow tags therefore read `NET BUYERS` / `NET SELLERS`, not `NET BUY`.

## Degraded mode

| Missing / stale | Result |
|---|---|
| GIFT unavailable / stale / future / conflict / rejected | omitted, reason in `omitted` and run history; run DEGRADED |
| GIFT withheld by the publication policy | omitted (`publication policy: ...`), audited; NOT a degradation |
| official event file missing/invalid | no file events (FOMC in code + expiry rule still apply); run DEGRADED |
| an AI-only previous-session fact (FII/DII, a sector) | dropped by `build_pre_brief` (note + run DEGRADED); a displayed AI-provenance fact would BLOCK (`provenance gate`) |
| one global market | the rest are used; a failed fetch never costs the others |
| every global cue | OVERNIGHT dropped; the Short starts at the setup |
| VIX stale / conflicting | VIX scene and card omitted |
| flows / sectors / Radar | that section omitted |
| no verified event | EVENT slot empty (no news headline takes its place) |
| no canonical report for the previous session | **BLOCKED** (`PREVIOUS_SESSION_MISSING`) - the REPORT job did not build it |
| previous report's artifact missing/unreadable/mismatched | **BLOCKED** (`PREVIOUS_REPORT_UNUSABLE`) |
| previous report failed data validation | **BLOCKED** (`PREVIOUS_REPORT_NOT_PUBLICATION_READY`) |
| report for another session | **BLOCKED** (`PREVIOUS_SESSION_MISMATCH`) - an older session is never shown as "previous" |
| no validated Nifty close/change | **BLOCKED** (`PREVIOUS_NIFTY_MISSING`) |
| PRE date not a session | **SKIPPED** (`NOT_A_SESSION`) - checked before anything is read |

## Validation scenarios (render_pre_phase1.py)

| Scenario | Data | Shows |
|---|---|---|
| REAL_2026-09-25 | real | large previous-session move (Nifty -1.64%, below its 20-day low), FII -5,027 / DII +4,301 cr, VIX +22.6%, Radar carry-forward, quiet US night, Hang Seng -1.73% at 7:45 |
| REAL_2026-09-22 | real | strong risk-on night (Nasdaq +2.26%), Nikkei closed (Japanese holiday - omitted), weekly F&O expiry |
| REAL_2026-09-24 / 23 | real | BLOCKED: no canonical report for 23 / 22 Sep |
| SYNTHETIC_QUIET | synthetic | quiet morning (shorter Short, no padding) |
| SYNTHETIC_RISK_OFF | synthetic | risk-off night + GIFT |
| SYNTHETIC_RISK_ON | synthetic | risk-on + GIFT |
| SYNTHETIC_EVENT | synthetic | RBI policy at 10:00 AM (fixture event, "Source: synthetic fixture") |

`validate_pre_data_sources.py` -> `output/pre_data_sources/` (GIFT / RBI / run-history audits,
the real 25 Sep plan, frames-only runs recorded in a validation history DB).

Real mornings are reconstructions at a 07:45 IST cutoff: the stored canonical report (its
session facts only - the report itself may have been generated later, which the brief notes),
Yahoo dated daily bars and 5-minute bars that ended before the cutoff. Synthetic scenarios use
placeholder stocks and carry "SYNTHETIC DATA - NOT REAL" on every frame.

## Artifacts (per run)

`pre_section_plan_<date>.json`, `pre_brief_<date>.json` (every input, with provenance and the
acquisition log), `pre_acquisition_<date>.json`, `storyboard_<date>.json`,
`hook_plan_<date>.json`, `pre_result_<date>.json` (gates, QA, probed duration),
`freeze_frames/` + per-scene folders (`global_scene/`, `previous_session_scene/`,
`event_scene/`, `watch_open_scene/`, ... each with `entry.png` and `settled.png`),
`full_pre_<date>.mp4`, `pre_contact_sheet_<date>.png`, `pre_provenance_<date>.json` (every
freshness-sensitive fact: source, type, group, retrieved_at, market date/timestamp, freshness,
displayed or not) and `pre_run_<date>.json` (the run-history record). PRE writes nothing into
CANONICAL history (no report, no fact rows).

## Provenance (presentation/pre_provenance.py)

US indices, Asian indices, GIFT, India VIX, previous-session Nifty/FII-DII/sectors (from the
report's own observations, recorded on the brief as `fact_provenance`), events and news
headlines are audited per run. Hard rule: **no displayed market fact may have an LLM as its
provenance** - `render_pre` blocks on any `ai_violations` (`provenance gate`). Upstream,
`build_pre_brief` already drops FII/DII or a sector whose canonical fact has only AI
observations (POST may carry those as PROVISIONAL; PRE never shows them). No acquisition module
imports a model (a test greps for it).

## Run history (products/pre_run_history.py)

Every PRE run is a row in the SAME operational history as POST runs - `publication_runs`
(schema v2 adds `target_date` and `run_status`, v3 `job_type` and `source_session_date`; see
docs/STORAGE.md) - not a second system.

| field | PRE value |
|---|---|
| `mode` | `PRE_MARKET` (`PRE_MARKET_DEMO` for `--demo` and synthetic validation runs) |
| `job_type` | `PRE_MARKET` (never confused with `POST_MARKET` / `REPORT_BUILD` rows) |
| `target_date` | the session about to open |
| `source_session_date` | the previous canonical session whose report PRE read |
| `started_at` / `completed_at` | UTC |
| `run_status` | `SKIPPED` (the PRE date is not a session) / `SUCCESS` (rendered, every gate passed, every expected source usable) / `DEGRADED` (rendered, but an optional source was missing/stale/rejected - each reason listed) / `BLOCKED` (a rule refused: no canonical previous session, content/language/provenance/runtime gate) / `FAILED` (an error, or render/video QA failed) |
| `stage` | STARTED -> RENDERED / FRAMES_ONLY / GATE_BLOCKED / BRIEF_BLOCKED / RENDER_FAILED / FAILED |
| QA columns | data (brief built), content (safety + language), video (freeze-frame QA + probe) |
| `failure_stage` / `failure_reason` | the blocking code and message (e.g. `PREVIOUS_SESSION_MISSING`) |
| `publication_status` | `SHADOW` for `--shadow`, else `NOT_ATTEMPTED` (V1 never uploads) |
| `report_id` | NULL - the previous-session report PRE read is in details, so a POST report's runs never include PRE runs |
| `details_json` | section plan (order, reasons, omitted), source freshness summary, GIFT status + reading, event-calendar audit, `degradations` (source, status, exact reason), hook status (source, fallback reason, whether Gemini was requested), output paths, QA, provenance verdict, brief notes |

Recording is never fatal (PRE publishes nothing): a history failure is written into the run's
own `pre_run_<date>.json`. An exception during acquisition or render is recorded as FAILED and
then re-raised.

## Not in V1

Music, narration, upload, commodities/FX, a historical (reconstructable) GIFT series.
