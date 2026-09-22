# Historical intelligence

Deterministic context derived from canonical history: how today compares with recent
sessions. It is the first layer that makes the database earn its keep.

```
                    Canonical SQLite history
                              |
                              v
Today's MarketReport --> Intelligence Engine --> IntelligenceSnapshot
                                                         |
                                            JSON artifact + MARKET CONTEXT scene
```

## What it is not

No predictions, no forward returns, no rankings of investments, no BUY/SELL/HOLD, no target
prices, no backtesting. The boundary is deliberate and load-bearing: *"FIIs have been net
sellers for four consecutive available sessions"* is a countable fact, while *"FIIs are
exiting India"* is a claim about the future wearing a number's clothes.

Statements are deterministic templates, asserted in tests against a banned-wording list and
run through the Phase 1.1 content scan before display.

## Deterministic, not generated

    DATA -> RULES -> INTELLIGENCE

No LLM computes a streak, percentile, average, frequency, rank or classification. Given the
same report and the same database, the engine produces byte-identical output (with `now`
injected). That is asserted directly in `tests/test_intelligence_integration.py`.

## No acquisition

The engine reads exactly two things: today's canonical `MarketReport` and what is already in
`MarketHistory`. No Yahoo, NSE, Gemini, Google News, provider or web call. A test greps every
module in the package for those references and fails if one appears.

## Derived, never canonical

The snapshot is a **function of** canonical records and can be regenerated from them at any
time. It is therefore written as its own artifact and never folded back into the report:

- `output/intelligence/intelligence_YYYY-MM-DD.json`
- Phase 3.1 immutability is binding — canonical JSON and SQLite rows are untouched, asserted
  by a test that snapshots all six canonical tables before and after.
- Nothing is inserted into `facts`: a derived statistic is not an observed market datum.

## The rules that are easy to get wrong

**Current session exclusion.** History loads strictly before the reported session. Comparing
today against a window that already contains today quietly flatters every percentile. Enforced
in one place (`HistoricalWindow`), with a `report_id` guard as well as a date filter, and
tested by persisting today *before* analysing it.

**Trading sessions, not calendar days.** A window of 20 means the last 20 `market_date`s that
exist in canonical history. Weekends and holidays are not missing sessions; they are not
sessions. A test asserts 20 sessions necessarily span more than 20 calendar days.

**Streak continuity is strict.** A streak is a *continuity* claim, so it walks the **canonical
session spine** — every session we hold a report for, regardless of any metric's status — and
stops at the first recorded session that does not continue the run:

| At an adjacent recorded session | Effect |
| --- | --- |
| same direction, eligible evidence | continues |
| opposite direction | **breaks** |
| zero | **breaks** |
| the fact is missing | **breaks** |
| the fact exists but is ineligible (`CONFLICT`, `STALE`, `PROVISIONAL`, `MISSING`, `REJECTED`) | **breaks** |
| weekend / holiday / a day we never recorded | not on the spine — never consulted, cannot break |

> **A missing calendar day is not missing canonical trading-session evidence.**

The walk never reaches past a broken session to find another matching value behind it.
Applies to FII, DII and sector streaks.

**Continuity and summary are different statistics, and the wording says which:**

| | Semantics | Wording |
| --- | --- | --- |
| Streak | unbroken run over adjacent recorded sessions | "N consecutive **recorded** sessions" |
| Cumulative flow | sum over the last N eligible readings, gaps passed over | "last N **available** sessions" |

The session spine comes from `MarketHistory.get_recent_sessions()` — distinct `session_date`
values from the `reports` table, deliberately independent of eligibility so that "we never
recorded that day" and "that session's evidence was unusable" remain distinguishable.

**Validation-status eligibility.** Centralised in one frozen set:

```python
ELIGIBLE_HISTORICAL_STATUSES = {VERIFIED, SINGLE_SOURCE}
```

`CONFLICT`, `STALE`, `MISSING`, `REJECTED` and `PROVISIONAL` never enter a calculation. An
AI-only provisional number is not evidence, and averaging it into a 20-session mean would
launder it into one.

**Definition compatibility.** Relative volume is only compared against readings carrying
`definition_version == "2.0"`. Phase 3 changed the window from 10 prior sessions to 20, so a
1.x reading measures a different thing that happens to share a name. Incompatible readings are
excluded and the exclusion is recorded as a warning.

**Canonical fact identity, not observation count.** A fact may carry several observations, and
`get_recent_metric_points` returns one row per observation. Relative-volume history is
collapsed to `(report_id, fact_id)` before anything is counted — counting observations would
inflate `comparable_sample`, the rank and the supporting-fact list, making a reading look
better corroborated than it is. A fact counts as comparable when **any** of its observations
establishes the 2.0 definition.

**Minimum history is explicit.** A statistic defined as N sessions is not that statistic with
fewer. With 15 of 20 sessions the insight is `INSUFFICIENT_HISTORY` and makes **no statement
at all** — calling it "partial" would invite presenting it anyway. Shorter descriptive counts
are allowed only when the sample size travels with them and the wording says *available
sessions*.

**`strength` is data support, never confidence.** `FULL_HISTORY` / `PARTIAL_HISTORY` /
`INSUFFICIENT_HISTORY` describe how much of the window existed. There is no probability
anywhere in this package.

**No causality.** The layer reports that FIIs sold and that Nifty moved. It never infers that
one caused the other.

## What it computes

| Category | Statement shape |
| --- | --- |
| `INDEX_MOVE` | "Nifty's 1.80% move is larger than 17 of the previous 20 sessions." |
| `INSTITUTIONAL_FLOW` | continuity streak ending today; cumulative 5/20-session flow with the matching direction count |
| `VOLATILITY` | VIX vs its 5-session average; rank within the previous 20 readings |
| `SECTOR_PERSISTENCE` | consecutive up/down run; **compounded** return, never summed |
| `MOVER_RECURRENCE` | prior appearances in 20 sessions; `FIRST_APPEARANCE` (0) / `REPEAT_MOVER` (1–2) / `FREQUENT_MOVER` (3+) |
| `RELATIVE_VOLUME` | "higher than 8 of its previous 10 comparable readings" |

Sector returns compound as `product(1 + r/100) - 1`. Summing daily percentages is wrong
immediately and increasingly wrong as the window lengthens, and the result would still be
labelled a "return".

Streaks and cumulative flows **include today** (a run ending today), counted exactly once:
history is loaded strictly before the session and today's value is appended from the current
report. Ranks and percentiles **exclude today** entirely.

## Traceability

Every insight carries `category`, `subject`, `sample_size`, `lookback_sessions`,
`supporting_report_ids` and `supporting_fact_ids`. A test resolves every supporting report id
back to a row in canonical history. An insight that cannot be traced to the records behind it
is an assertion, not an observation.

## Video selection

At most **three** statements reach the screen, chosen deterministically:

1. sort by `selection_score` (how unusual the reading is, derived from the data itself)
2. category priority as a tiebreak only
3. `insight_id` last, so the order is total and stable
4. at most one statement per `(category, subject)` — a 5-session and a 20-session reading of
   the same index are the same story told twice

No model decides what is interesting. Every computed insight stays in the snapshot even when
it is not displayed.

## The MARKET CONTEXT scene

Optional, like the global/FII/sector scenes: it appears only when there are displayable
insights. Its 6 seconds are **carved out of the three scenes already designed to flex**, so
the Short stays exactly 75.0s and a cold-start run is timed identically to a Phase 3 one.

## Cold start and failure

The database began in Phase 3, so for the first weeks most 20-session windows are unavailable.
That is normal, not an error:

- **Zero history** → valid snapshot, `insights: []`, a warning, video renders unchanged.
- **Partial history** → the 5-session statistics work; the 20-session ones report
  `INSUFFICIENT_HISTORY` and are omitted from the video.
- **Database unreadable** → no insights, a recorded warning, pipeline continues.

Historical context is an enhancement, not a new single point of failure, and it never weakens
a publication gate. What must never happen is fabricating context to fill a gap, so every
failure path produces silence rather than a plausible-looking statement.
