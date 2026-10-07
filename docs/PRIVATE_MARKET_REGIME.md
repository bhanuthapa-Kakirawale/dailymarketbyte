# Private market regime classifier (V1, provisional)

Package `private_desk/regime/`. Calculation version `regime-v1.0-provisional`, snapshot schema
`private-market-regime-1.1`. PRIVATE / LOCAL ONLY: part of the Private Trading Intelligence Desk
(docs/PRIVATE_DESK_USER_GUIDE.md), never imported by REPORT / PRE / POST (test-enforced).

**STATUS: FROZEN / PROVISIONAL RESEARCH MODEL** (`model.MODEL_STATUS`, owner decision, Oct 2026):
- approved for current private context;
- deterministic and explainable;
- not a forecast and not a trade signal;
- historical validation is restricted to point-in-time universe sessions (section 6);
- the volume thresholds are provisional until more history accumulates.

The rule table is frozen. `tests/test_private_regime.py::test_regime_rule_table_is_frozen` hashes
every combination of dimension states plus the confirmation rule. Changing a rule or a threshold
is a new calculation version and needs owner approval.

## 1. What a market regime is, and what it is not

A **market regime** here is a one-word description of the **current** market environment of one
NSE session, with the evidence behind it:

| Label | Meaning |
|---|---|
| **BULLISH** | The index is in an orderly uptrend AND participation is broad, with no major contradicting evidence. |
| **BEARISH** | The index is in an orderly downtrend AND weakness is broad, with no major contradicting evidence. |
| **NEUTRAL** | No established index direction and balanced participation - a range-like market. |
| **TRANSITIONAL** | The evidence conflicts, or is changing from one state to another. |
| **INSUFFICIENT_DATA** | The minimum reliable evidence is not available. Nothing is guessed. |

It is **not** a forecast (it says nothing about tomorrow), **not** a trade recommendation, and
**not** a score. There is no confidence number and no "bullishness" scale. A label exists only
together with the dimension states, numbers and rule that produced it.

Why it is useful: the Radar describes individual stocks. The regime describes the market
those stocks appeared in, so the owner can read a candidate in context, for example a
"relative strength" candidate during a BEARISH session. In V1 it is **context only**. It never
changes the Radar candidate list, its ranking, the attention set or any detector (test-enforced).

**NEUTRAL vs TRANSITIONAL.** NEUTRAL is a *settled* state with no direction: the index is flat or
crossing its averages without a trend, participation is balanced, and nothing leans one way.
TRANSITIONAL is an *unsettled* state: something points one way while something else points the
other way, the index trend has a direction that breadth does not confirm (or the reverse), or a
new reading has not yet been confirmed by a second session.

## 2. Inputs (all read-only, all session-aligned)

| Source | Used for |
|---|---|
| OHLCV store `^NSEI` (NIFTY 50) | canonical session spine (`radar.session_alignment.canonical_session_list`), trend, realised volatility |
| OHLCV store, NIFTY 200 constituents (quality OK only) | breadth, sector returns, unusual volume, relative strength |
| Market Structure artifacts `universe.constituents` | NIFTY 200 membership + NSE Industry sector (the latest list on or before the session) |
| Canonical report of exactly that session | India VIX (with validation status), FII/DII net cash (context) |

Stock rows dated on a non-session (provider holiday placeholders) are dropped. A session a stock
has no bar for stays empty and is never filled. Every universe statistic needs **>= 95%
coverage** (`market_structure.aggregator.MIN_COVERAGE_PCT`), or it is unavailable.

## 3. Dimensions (exact V1 rules)

Thresholds live in `private_desk/regime/rules.py`. Changing one means bumping
`model.CALCULATION_VERSION`.

### CORE: these decide the regime

**TREND (NIFTY 50).** Close vs its 20- and 50-session simple averages.
- NEUTRAL (checked first, range-like): |close vs SMA50| < 1.0% and |SMA20 vs SMA50| < 0.5%.
- POSITIVE: close > SMA20, close > SMA50, SMA20 > SMA50.
- NEGATIVE: close < SMA20, close < SMA50, SMA20 < SMA50.
- MIXED: anything else (price and averages disagree).
- UNAVAILABLE: no NIFTY bar on the session, or fewer than 50 complete sessions.

**BREADTH (NIFTY 200).** P50 = share of constituents above their own SMA50. A10 = 10-session
average of the daily advance share. Today = that session's advances / declines.
- POSITIVE: P50 >= 60% and A10 >= 50%.
- NEGATIVE: P50 <= 40% and A10 <= 50%.
- NEUTRAL: 40% < P50 < 60% and 45% <= A10 <= 55%.
- MIXED: anything else.
- Washout override: a POSITIVE reading with >= 80% of stocks declining today becomes MIXED, and a
  NEGATIVE reading with >= 80% advancing today becomes MIXED. One day can block a direction, but
  it can never create one ("179/200 declining" marks participation as weak through P50 and A10,
  not by itself).
- UNAVAILABLE: fewer than 50 sessions of constituent history, or coverage < 95%.

**SECTORS.** For each NSE Industry sector with >= 3 covered constituents, the median 20-session
return.
- POSITIVE: at least two-thirds of the counted sectors positive.
- NEGATIVE: at most one-third positive.
- MIXED: in between.
This dimension separates broad participation from index strength carried by a few sectors.

**VOLUME.** Unusual-volume events over the last 5 sessions. An event is RVOL >= 2.0x the prior
20-session average (`market.relative_volume`, the Market Structure UNUSUAL_VOLUME definition),
split by that stock's day direction.
- NEUTRAL (quiet): fewer than 20 events.
- POSITIVE: share of events on advancing stocks >= 65%.
- NEGATIVE: share <= 45%.
- MIXED: in between.
The thresholds are asymmetric on purpose: in the stored sample the median up-share is about 60%,
so 50% is not neutral. Status **V1_PROVISIONAL** (section 6). Volume is subordinate to trend,
breadth and sectors.

### STRESS: can only block BULLISH

**VOLATILITY.** NIFTY 50 20-session realised volatility, annualised.
- ELEVATED: >= 20%.
- RISING / FALLING: a change of >= +25% / <= -25% versus 5 sessions earlier.
- STABLE: otherwise.
India VIX from the canonical report is shown beside it with its validation status. V1 does not
use VIX as a rule input because only 7 readings are stored, too few to set a threshold. A
missing VIX is shown as "not recorded" and never estimated.

### CONTEXT: shown, never decide

- **RELATIVE** (relative strength spread): the share of NIFTY 200 stocks beating NIFTY 50 over
  20 sessions. BROAD >= 55%, NARROW <= 45%, BALANCED in between.
- **FLOWS**: FII net cash from the canonical report. NET_BUYERS / NET_SELLERS, with its
  validation status (SINGLE_SOURCE / VERIFIED). It is never the basis of a regime.

## 4. Regime rules (exact)

Evidence hierarchy: **trend + breadth (the pillars) > sectors > volume**.

1. **INSUFFICIENT_DATA** (minimum-data gate): TREND unavailable, or BREADTH unavailable, or none of
   SECTORS / VOLUME / VOLATILITY available.
2. **BULLISH**: TREND POSITIVE and BREADTH POSITIVE; SECTORS not NEGATIVE; VOLUME not NEGATIVE
   unless SECTORS is POSITIVE; VOLATILITY not ELEVATED.
   (Otherwise TRANSITIONAL: `SUPPORT_CONTRADICTS` or `VOLATILITY_STRESS`.)
3. **BEARISH**: TREND NEGATIVE and BREADTH NEGATIVE; SECTORS not POSITIVE; VOLUME not POSITIVE
   unless SECTORS is NEGATIVE. (Otherwise TRANSITIONAL `SUPPORT_CONTRADICTS`.)
4. **TRANSITIONAL `CONFLICT`**: any core dimension POSITIVE while another is NEGATIVE.
5. **NEUTRAL**: TREND NEUTRAL or MIXED (no established direction), BREADTH NEUTRAL, SECTORS and
   VOLUME not both leaning the same way, VOLATILITY not ELEVATED. If SECTORS and VOLUME do both
   lean the same way, the result is TRANSITIONAL `PARTICIPATION_BUILDING`.
6. **TRANSITIONAL**, anything else: `TREND_WITHOUT_BREADTH`, `BREADTH_WITHOUT_TREND`,
   `TREND_MIXED`, `BREADTH_MIXED`.

"Volume alone cannot overturn trend, breadth AND sectors agreeing": volume is the noisiest
dimension (5 sessions of event counts). Verified on the real history, 18-23 Sep 2026: volume read
POSITIVE inside an otherwise fully negative market. Without the hierarchy, a 17-session bearish
run broke into four TRANSITIONAL sessions.

### Confirmation rule (the only persistence rule)

The rules above give this session's **candidate** regime. A BULLISH, BEARISH or NEUTRAL candidate
becomes the regime only when the previous session's own candidate was the same, so it takes two
consecutive sessions. Until then the regime is TRANSITIONAL (`UNCONFIRMED_CHANGE`), and the
snapshot shows both readings. TRANSITIONAL and INSUFFICIENT_DATA apply immediately. The
previous session's candidate is recomputed from data up to that session only. There is no
smoothing, no weighting and no chain back further than one session, so every regime is
reproducible from any history that covers its windows.

Why it exists (measured on the 82 classifiable sessions): without it, 27 switches and 9
one-session reversals (A-B-A). With it, 15 switches and 5 reversals.

## 5. No lookahead: price data vs universe membership

Two separate claims, never mixed.

**PRICE DATA: no future data is ever used.** Session d is classified from `RegimeData.until(d)`,
a view that physically ends at d. Every window looks backwards: SMA, 10-session advance share,
20-session returns, 5-session volume events, realised volatility. No forward return is computed
anywhere in the package. Proof, tested and in the research artifact: every historical session is
reclassified from inputs reloaded with the PRICE store (OHLCV, benchmark, report context) cut at
that session, and the result must be byte-identical. Result: 131 / 131 identical.
`tests/test_private_regime.py` also overwrites every row after d with random values and checks
that the session-d result does not change.

**UNIVERSE MEMBERSHIP: point-in-time only where historical membership is actually stored.**
The price proof does not cut the constituent lists, and it is not a claim that a historical
session's NIFTY 200 membership was known on that day. That is what the universe-quality label
reports (section 6). A reconstructed session that uses a later list is
**PRICE-POINT-IN-TIME + BACKDATED-UNIVERSE**. It is never described as fully point-in-time.

## 6. Historical universe limitation

Exact historical NIFTY 200 membership is unavailable before the stored universe history begins.
The first session with its own stored constituent list is **2026-09-24**. This date is
discovered from the Market Structure artifacts, never hard-coded
(`data.earliest_point_in_time_session`). No future prices are used for any session, but
earlier sessions reuse a later constituent list. Those sessions are exploratory only. Primary
validation starts where point-in-time membership is available.

### Universe quality (snapshot field `universe_quality`, schema 1.1)

| Value | Meaning |
|---|---|
| `POINT_IN_TIME` | The session's own stored list; or the latest earlier list carried forward when the NEXT stored list is identical, which proves membership was unchanged across the gap. |
| `BACKDATED_UNIVERSE` | A list dated AFTER the session was reused (the session precedes the stored history). |
| `UNKNOWN` | Provenance not recorded; or a carried-forward list that the next stored list contradicts (membership changed in the gap) or that no later list confirms yet. |

Every snapshot also records `universe_source_date`, `session_date`, `universe_source_reference`
(the NSE constituent-file URL), `universe_retrieved_at`, `universe_note`, and the
`window_universe_quality` of every session whose membership the classification read. That
covers the 10-session advance-share windows of d and of d-1 (confirmation rule) and both
5-session volume windows (`WINDOW_BACK = 10`).

Universe quality is **metadata**. It never changes the regime, its dimensions or WHY THIS
REGIME, and it is not a regime dimension (test-enforced). Later lists are used only to LABEL
quality. The label can upgrade from UNKNOWN to POINT_IN_TIME when a later identical list
arrives, and the snapshot cache rebuilds because the input fingerprint changes.

Verified on the stored lists (2 Oct 2026). The 24, 25 and 28 Sep lists are identical. The 30 Sep
list differs by 7 in / 7 out: APARINDS, HINDCOPPER, LICI, MAHABANK, MEESHO, NLCINDIA and VAML in;
ALKEM, COROMANDEL, HUDCO, KPITTECH, SHREECEM, TATAELXSI and TATAINVEST out. That is the
semi-annual rebalance. 29 Sep has no list of its own, so its membership is **UNKNOWN**. The 24 Sep
list was retrieved on 26 Sep during a rebuild. It is identical to the 25 and 28 Sep lists and is
treated as point-in-time; its note says so.

### Validation groups (`validation_summary.json`, `validation_report.md`, `regime_history.csv`)

| Group | Contents | Use |
|---|---|---|
| `POINT_IN_TIME_VALIDATED` | sessions with `universe_quality = POINT_IN_TIME` | **PRIMARY**: the only group any validation claim may use (counts, persistence, switching, durations, one-session reversals, dimension validation, Phase 2) |
| `ALL_RECONSTRUCTED` | every reconstructed session | EXPLORATORY: classifier behaviour only |
| `BACKDATED_UNIVERSE` | the back-dated sessions | EXPLORATORY / APPROXIMATE |

Run statistics are computed over CONSECUTIVE sessions only: a gap in a group ends a segment, and
runs touching a segment edge are counted as censored. Below 60 classifiable sessions a group's
statistics are listed but explicitly marked NOT statistically meaningful.

### Results (build of 2026-10-02, latest session 2026-10-01)

- Reconstructed: **131** sessions (2026-03-23 .. 2026-10-01). The benchmark goes back to
  2025-09-23 (253 sessions).
- Universe quality: **5 POINT_IN_TIME** (24, 25, 28, 30 Sep, 1 Oct), **125 BACKDATED_UNIVERSE**,
  **1 UNKNOWN** (29 Sep).
- INSUFFICIENT_DATA: **49** (all before 2026-06-08; BREADTH needs 50 sessions of constituent
  history).
- PRIMARY (point-in-time), 5 classifiable sessions: BEARISH 5. There are 0 switches in 2
  consecutive segments (24-28 Sep and 30 Sep - 1 Oct, split by the UNKNOWN 29 Sep), and both runs
  are censored. **This is far too small to be statistically meaningful.** Persistence, switching
  and duration cannot be validated yet.
- EXPLORATORY (all reconstructed), 82 classifiable sessions:
  - BEARISH 17, BULLISH 3, NEUTRAL 7, TRANSITIONAL 55, INSUFFICIENT_DATA 49;
  - 15 switches and 5 one-session reversals (27 and 9 without the confirmation rule);
  - one 17-session BEARISH run, one 3-session BULLISH run, six NEUTRAL runs averaging 1.2
    sessions, eight TRANSITIONAL runs averaging 6.9.

  These describe classifier behaviour on a back-dated universe, not validation.
- Reconciliation: classifier advances / declines / unusual volume equal the stored Market
  Structure artifact counts on all 5 sessions that have one.
- India VIX: 7 readings. FII/DII: 6 readings.

**This is about 6 months of one market** (a March-April stress episode, a June-August range, a
September decline). Every threshold is a conventional round number, checked against the
exploratory distributions, not fitted to them, and **must not be optimised against this same
sample**. No profitability conclusion is drawn or implied.

### NEUTRAL and TRANSITIONAL frequency

TRANSITIONAL is frequent and NEUTRAL runs are short (exploratory sample). This may reflect
genuinely conflicting evidence in the available market sample, for example the July range where
the index crossed its 20-session average back and forth. **No rule is changed solely to force
a desired distribution of labels.**

### Volume thresholds: `V1_PROVISIONAL`

The 65% / 45% up-share thresholds (`rules.VOLUME_THRESHOLD_STATUS`) were chosen on the limited
~6-month sample. They are not retuned and not optimised against that sample. Volume
participation is subordinate to (1) trend, (2) breadth and (3) sectors: it can never overturn
them when they agree.

### Phase 2 research gate: `eligibility.is_regime_research_eligible(snapshot)`

The gate returns True only when ALL of these hold:
1. the session is classifiable: neither the regime nor this session's candidate is
   INSUFFICIENT_DATA;
2. `universe_quality == POINT_IN_TIME`;
3. source alignment passes:
   - `window_universe_quality == POINT_IN_TIME`;
   - the constituent list is dated on or before the session;
   - every dimension describes exactly this session.

`research_eligibility()` returns the failed reasons. Future Phase 2 work must use this gate by
default. **BACKDATED_UNIVERSE (and UNKNOWN) sessions are excluded from:**
- forward-return studies;
- MFE / MAE studies;
- regime-conditioned Radar performance;
- threshold optimisation;
- strategy research.

They stay excluded unless the historical universe is later recovered.

Today **0** sessions are eligible. Every point-in-time session's lookback windows still read
back-dated or UNKNOWN (29 Sep) membership. If a constituent list is stored every session from
now on, the first fully eligible session is **2026-10-15**: the eleventh session after 29 Sep on
the NSE calendar.

## 7. Where it shows (Private Desk)

- **Dashboard → Market context**: a compact regime card (label, as-of session, dimension
  states, WHY, unavailable dimensions) beside the existing facts. Colours are restrained (a thin
  coloured edge, no green/red banner), because this is context, not a signal.
- **/regime**: current classification, the rule applied, supporting and conflicting evidence,
  every dimension with its numbers, rule and source (sector table, 5-session volume table), the
  last 30 sessions of regime history, and the full rule set.
- **Universe-quality badges** (understated, metadata, not part of WHY):
  - the history table has a `Univ` column: PIT / APPROX / UNKN, with a "point-in-time universe
    only" filter;
  - a back-dated session's page shows an `APPROXIMATE UNIVERSE` badge with the tooltip
    "Historical constituent membership for this session was unavailable; the earliest stored
    universe was used. Prices remain session-bounded." plus a one-line note;
  - the dashboard card shows a badge only when the viewed session is not point-in-time. The
    current session shows no warning.
- **Stock page** (Sector context): "Market regime (date): LABEL", context only.
- **Data Quality** shows:
  - classifier version and model status;
  - latest regime session and freshness (STALE when older than the latest completed session);
  - the latest session's universe quality;
  - "Point-in-time validation from <date>", discovered from the artifacts;
  - Phase 2 eligibility of the latest session with its reasons;
  - required / available / unavailable dimensions;
  - universe coverage, both for the recent window and for the whole history (POINT_IN_TIME /
    BACKDATED_UNIVERSE / UNKNOWN);
  - the PRIMARY (point-in-time) validation counts with a "not statistically meaningful" flag;
  - the volume-threshold status;
  - historical-validation status (CURRENT / OUTDATED_VERSION / NOT_BUILT);
  - stored snapshot files.
- **Radar CSV** gains a `market_regime` column. The **PrivateCandidatePacket** (schema 1.1)
  gains `market_regime` = {label, session_date, calculation_version, dimension states,
  reason_code}. Context only, with no order fields (test-enforced).

## 8. Storage and performance

Snapshots are DERIVED and rebuildable: `output/private_desk/regime/regime_snapshots_<version>.json`,
keyed by calculation version + snapshot schema version + a fingerprint of the OHLCV store, the
market-history store and the Market Structure artifacts. Any change rebuilds. Deleting the folder only costs a
recompute (about 1 s for the dashboard session, about 1 s for a 30-session history). The
dashboard computes only the session shown (plus the previous session's candidate). Full
historical validation runs only from the research command. Nothing is written to any DMB
database.

## 9. Future research (not in V1)

The snapshots carry session_date + label + dimension states + calculation_version +
universe_quality, so Phase 2 can join them to Radar setup outcomes, through
`is_regime_research_eligible` only (section 6). Example question: does Volume + Structure behave
differently in BULLISH vs BEARISH sessions? V1 computes no forward return and makes no
performance claim.
