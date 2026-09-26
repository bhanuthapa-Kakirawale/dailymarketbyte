# Validation rules

Validation runs at two levels. Per-observation checks decide whether a single reading
qualifies at all; the cross-source check decides how much the surviving readings actually
establish. Every validator is deterministic and unit-tested offline (`tests/test_validation.py`).

## 1. Source validation

Each observation carries a `SourceType` that fixes how much independent weight it can
contribute. Authority order, used when choosing which source a fact quotes:

```
PRIMARY > SECONDARY > BROKER > NEWS > DERIVED > AI
```

`DERIVED` means this pipeline computed it (EMA, RSI, pivots, relative volume). Nothing
external vouches for a derived value and nothing can corroborate it, so derived facts sit at
`SINGLE_SOURCE` by design.

## 2. Date validation

An observation's `market_date` must equal the session being reported.

**A mismatch is `REJECTED`, not `STALE`.** This is a deliberate distinction: a wrong-dated
reading is not an old copy of the right number, it is a different day's number. Publishing it
would misattribute one session's move to another - precisely the yfinance day-gap failure
(ADANIGREEN, 17 Sep 2026) that the `prev_date` guards in `market.py` already defend against.

A rejected observation is dropped from consideration. If some observations survive, the fact
continues with those. If none do, the fact is `MISSING` even when the pipeline still holds a
display value - the artifact must never imply support that is not there.

## 3. Freshness validation

Age of `observed_at` (or `retrieved_at` when the source gives no timestamp) against a
configured `max_age` produces `STALE`. Unset `max_age` skips the check; `now` is injectable
so freshness is testable without a clock.

## 4. Range validation

Magnitude sanity only - it catches unit errors and obvious nonsense. Defaults mirror the
sanity ranges `news.py` already enforced (FII/DII within +/-60,000 crore, and so on).

**Passing a range check is explicitly not verification.** A plausible wrong number passes.
This is the reason the LLM rule below cannot be satisfied by range-checking alone.

## 5. Cross-source verification — counted in independence groups

**Corroboration is counted in independence groups, not in observations.** Two readings from
one source are one witness however they were fetched, so counting observations would let a
single source appear to confirm itself. See `docs/SOURCE_PROVENANCE.md`.

Given the observations that survived the checks above:

| Situation                                                          | Status          |
| ------------------------------------------------------------------ | --------------- |
| No usable observation                                               | `MISSING`       |
| Any two usable observations disagree beyond tolerance               | `CONFLICT`      |
| One independent group, non-AI (any number of observations)          | `SINGLE_SOURCE` |
| One independent group, AI, **critical** metric                      | `PROVISIONAL`   |
| One independent group, AI, non-critical metric                      | `SINGLE_SOURCE` |
| Two or more groups agreeing, at least one non-AI                    | `VERIFIED`      |
| Two or more groups agreeing, **all AI**, critical metric            | `PROVISIONAL`   |

Worked examples:

- NSE + Yahoo agreeing → **VERIFIED** (two groups, one non-AI)
- Yahoo index endpoint + Yahoo bulk endpoint agreeing → **SINGLE_SOURCE** (one group)
- Gemini response A + Gemini response B agreeing → **PROVISIONAL** for a critical metric
- Two Google News items from the same publisher → one group, so **not** corroboration
- Yahoo + Gemini agreeing → **VERIFIED** (AI corroborated by an acceptable non-AI source)

Disagreement is checked across *all* usable observations, including within one group: two
readings of one number that do not match is a data problem regardless of whether they were
ever going to count as corroboration.

`details` records `independence_groups`, `groups_compared`, `group_values`,
`source_identities`, `values`, `min`/`max`, `difference`, `difference_pct` and the tolerances,
so an archived verdict can be re-argued without re-fetching anything.

### Tolerances

Set per metric in `core/validation.py::policy_for`:

- Price/level metrics: **0.2 %** relative - the same threshold `main.py`'s Nifty cross-check
  gate has always used.
- Percentage-point metrics (`INDEX_CHANGE_PCT`, `STOCK_CHANGE_PCT`, `SECTOR_CHANGE_PCT`):
  **0.05 absolute**. Comparing these relatively misleads - `+0.29 %` vs `+0.31 %` is a 6.5 %
  relative gap but a 0.02-point one.
- FII/DII: **1 %**, because provisional flow figures are revised between sources.

When both tolerances are configured, agreement on either is enough.

## 6. The LLM rule

> An LLM is not a source of truth for critical numerical market data.

Concretely, for `CRITICAL_METRICS` - index closes and levels, index and stock percentage
changes, sector changes, FII/DII, commodity prices, FX rates:

- An AI-only fact **can never** reach `VERIFIED`. Its ceiling is `PROVISIONAL`.
- Two agreeing AI observations are still `PROVISIONAL`. Agreement between LLM answers is not
  independent corroboration - and because every Gemini answer shares the `GEMINI`
  independence group, the group rule enforces this structurally rather than by special case.
- An AI observation **may** form part of a `VERIFIED` fact once an acceptable non-AI source
  independently agrees inside tolerance.
- Grounded Google Search does **not** make Gemini a primary source. It remains
  `AI_DISCOVERY`.

In today's pipeline this means GIFT Nifty and Brent - which only Gemini can source - are
correctly recorded as `PROVISIONAL` rather than verified, while Gemini's Nifty close is
valuable precisely because it corroborates Yahoo's.

The rule is enforced in `CrossSourceValidator`, not at the call sites, so no new caller can
forget it.

## 7. Publication gate

`MarketReport.validation_summary.publication_ready` is `False` when either:

- a **critical** fact is in `CONFLICT` - reproducing the existing safeguard in `main.py`
  that aborts the run on a >0.2 % Nifty mismatch, or
- a **required** fact (`INDEX_CLOSE`, `INDEX_CHANGE_PCT` for NIFTY 50) is absent or unusable.

An optional fact being `MISSING` never blocks. NSE blocking cloud IPs and Gemini returning
nothing is an ordinary day on GitHub Actions, not a failure.

**Phase 2 makes this verdict authoritative.** `main.check_publication` runs immediately after
the report is built and before anything is rendered: a report that is not fit to publish stops
the run, writes its JSON for diagnosis, and produces no video and no upload. Demo runs are
exempt, because synthetic data is never published anywhere.

The legacy >0.2% Nifty `RuntimeError` in `main.collect` remains as defence in depth, but the
authoritative verdict on whether sources agree is now the `INDEX_CLOSE` fact's
`CrossSourceValidator` result.

Content safety (`docs/CONTENT_SAFETY.md`) is a **separate and equally binding** gate. Data
validation and content safety both have to pass; neither can override the other.

## 8. Single-stock move guard and universe coverage (POST freeze)

A fact can be `VERIFIED`/`SINGLE_SOURCE` and still not be publishable as a *ranking* or a
*move story*. Two deterministic gates sit between the report and the Short:

- `core/move_guard.py` - every stock's session move is checked for a missing or stale previous
  close, a self-contradicting OHLC bar, a stated % that does not match its closes, a
  split / bonus / consolidation ratio at the open, |move| >= 20% (unresolvable with one price
  source and no corporate-action feed) and a >= 10% move without >= 1.5x volume. A held move is
  never ranked or published; its raw values stay in the acquisition audit.
- `editorial/movers_gate.py` - top gainer / top loser / Movers need `coverage_pct >= 90`
  (validated moves / universe size), recorded at acquisition in
  `report.metadata["movers_coverage"]`. No record means UNKNOWN, which is not publishable.

## 9. Session alignment - provider rows never define trading sessions

`core/trading_calendar.py` is the canonical NSE session rule. A date is a session if the
benchmark index printed a bar for it, or NSE listed it as a special session (Budget Saturday,
Muhurat), or it is a weekday not on NSE's published equity-segment holiday list. Years the
list does not cover fall back to the benchmark's own bar dates, which is the pre-patch spine.
Beyond the benchmark's range the answer is UNKNOWN, and the row is kept, never guessed at.

Why not the benchmark bars alone: verified 2026-09-25, every Yahoo Indian index feed (^NSEI,
^NSEBANK, ^CNXIT, ^INDIAVIX, even BSE's ^BSESN) has no bar for **Tue 2026-09-22**. That was a
real NSE session: it is not on NSE's holiday list, and every stock has a genuine bar that day.
Every other weekday ^NSEI skipped in a year of history is an official holiday. Per-stock feeds
also carry **placeholder rows on genuine holidays** (2026-05-01, 05-28, 06-26, 09-14: O=H=L=C =
previous close, volume 0, on ~200 stocks).

Rules:
- **Non-session provider rows are dropped before any positional read.** `market._prepare_series`
  (movers, RVOL, technical series) and `_drop_non_sessions` (Nifty, Bank Nifty, sectors) drop
  them, as does `radar.session_alignment` (Radar store path, visual evidence). A placeholder can
  never be a previous close. It also cannot shorten an RVOL / SMA / range window, or make a real
  stock look date-gapped. Before this patch, every session after a placeholder aborted
  (`Only N stocks had clean data`), and RVOL was inflated on 60 sessions by the zero-volume rows.
- **Previous close = the canonical previous session**, never calendar day - 1 and never the
  symbol's own previous row. A symbol missing that session is `date_gap` for that symbol only:
  never filled, never fatal, and it lowers coverage.
- **Session-level vs symbol-level.** `market.check_index_session_alignment` (in `get_market`)
  raises `SessionAlignmentError` when the benchmark's own previous bar is not the canonical
  previous session. For 2026-09-23, Yahoo's Nifty would otherwise report 21 → 23 Sep, a
  two-session change, as the day's move. That stops the run; a bad or missing symbol never does.
  The recovery is a later run once Yahoo backfills, or the second index source below. It is
  never a relabelled multi-session change.
- **Raw stays raw.** The OHLCV cache keeps every provider row. Alignment is a read-time filter.
  One exception at write time: `_write_through_ohlcv` never stores a session that is still
  trading (before 15:40 IST) as an OK bar. Verified 2026-09-25: 200 intraday bars were cached
  as final.
- Audit: `report.metadata["movers_coverage"]["session_alignment"]` (provider_rows_seen,
  canonical_rows_used, non_session_rows_ignored + dates, duplicate_rows_removed, status) and
  `report.metadata["session_alignment"]` (canonical previous session, benchmark gaps).
- The move guard (section 8) and the coverage gate run AFTER alignment, on aligned closes.
- **Maintenance:** add NSE's next-year holiday list every December (`NSE_TRADING_HOLIDAYS`).
  Until then the audit shows `calendar_covered: false` and the index-spine fallback applies.

## 10. Benchmark gap recovery - a second source for a missing index session

The primary index source is Yahoo. When the canonical calendar (section 9) says a session
existed and the primary index series has no bar for it, `market.recover_index_gaps` fetches
that session from **NSE's own end-of-day index file**
(`nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv`: every NSE index's OHLC,
points change and % change; a static archive, not the cookie-gated `/api/` endpoints). It
applies to ^NSEI and ^NSEBANK in `get_market`, the sector indices in `get_sectors`, and the
Radar benchmark (`radar.relative_acquisition.build_market_benchmark_series`).

A recovered bar is used only if every check passes (`validation_status = VALIDATED`):
- the source row is dated the requested session;
- its OHLC is internally consistent (low <= open/close <= high, all > 0);
- it is **anchored**: its implied previous close (close - NSE's points change) matches the
  adjacent canonical session's close already held (the primary's, or an earlier recovered bar)
  within 0.05%, and NSE's stated % change agrees with close / anchor. A wrong-day or
  wrong-index row cannot pass this, and neither can a plausible but wrong number;
- |change| < 20% (NSE's index circuit-breaker level).

A failure is recorded as `REJECTED_SOURCE_UNAVAILABLE / _INDEX_NOT_IN_SOURCE / _DATE_MISMATCH /
_INVALID_OHLC / _NO_CONTINUITY_ANCHOR / _CONTINUITY_MISMATCH / _CHANGE_INCONSISTENT`, and the
gap stays a gap. If that gap is the recap session's previous session, the
`SessionAlignmentError` above still stops publication, and its message names the fallback
outcome.

Never: interpolate, infer an index close from its constituents, or write a recovered bar into the
Yahoo OHLCV cache. `recover_index_gaps` fills only gaps strictly inside the primary series; the
recap session itself is handled by the stricter recap-session recovery below (section 10.1). Volume stays NaN on
a recovered bar, because NSE's index volume is a different unit from Yahoo's.

Provenance: each attempt yields a record (`index, session_date, source, source_url,
retrieval_time, source_published_at, validation_status, fallback_reason, anchor, checks`). It
lands in:
- `report.metadata["session_alignment"]["benchmark_recovery"]`, plus `index_recovery` for Bank
  Nifty and sectors;
- `status = ALIGNED_WITH_FALLBACK` and `previous_close_source` when the Nifty previous close came
  from it;
- `previous_close_source` on the NIFTY 50 INDEX_CHANGE_PCT observation, and on a sector
  observation;
- `source`/`provenance` on a recovered Radar benchmark row, with Radar issue
  `BENCHMARK_SESSION_RECOVERED_FROM_FALLBACK` (INFO) or `..._FALLBACK_REJECTED` (WARNING);
- a `FALLBACK ...` line in the run log.

Verified 2026-09-25, 22 Sep: NSE close 23329.0 with change -85.3, so the implied previous close
is 23414.3, which is exactly Yahoo's 21 Sep close. NSE's own 23 Sep file (change +117.8) implies
the same 23329.0. The 23 Sep Nifty move is +0.505% (the two-session change was +0.139%). Bank
+0.59, IT -0.87 and Pharma +0.90 match NSE's 23 Sep file exactly.

### 10.1 Recap-session recovery (POST final edge-case patch, owner decision)

The recap session itself can be missing from the primary: on the morning of 23 Sep, every Yahoo
Indian index feed ended at 21 Sep, so the 22 Sep POST could not be built. (Yahoo backfilled it
later: ^NSEI 23329.0, identical to NSE's file.) `market.recover_recap_session` now fills the
primary's missing **trailing** canonical sessions from the same official NSE end-of-day file,
under the same validation as section 10, plus these gates:
- the calendar **covers** the date (NSE holiday list) and calls it a SESSION;
- the session is **final**: `SESSION_FINAL_TIME` (15:40 IST) has passed on its own date, or it
  is an earlier date. A session still in progress is not a candidate at all. It is never
  fetched and never recorded, because it is not missing: its end-of-day value does not exist yet.
  **No fallback value is ever used intraday**;
- the row is anchored on the previous canonical close already held (the primary's last bar, or
  an earlier recovered one). Chaining runs oldest-first and stops at the first failure;
- at most `RECAP_RECOVERY_MAX_SESSIONS` (2) trailing sessions. More than that is a broken feed,
  not a lag, so nothing is filled (`NOT_ATTEMPTED_PRIMARY_STALE`).

It is applied to ^NSEI in `get_market`, and to ^NSEBANK and the Yahoo sector indices up to the
Nifty recap date (`until=`). None of them can reach past the benchmark's session. Provenance:
- each record carries `role: RECAP_SESSION`, `fallback_reason: PRIMARY_MISSING_RECAP_SESSION`
  and `session_final_check`;
- `session_alignment.recap_close_source` and `m["close_source"]` hold `NSE_INDEX_CLOSE_ARCHIVE`,
  with `status = ALIGNED_WITH_FALLBACK`;
- the NIFTY 50 INDEX_CLOSE / INDEX_CHANGE_PCT observations are then `nse_index_close_archive`
  (PRIMARY, group `NSE`) with `close_source` metadata. It is never labelled Yahoo. A sector or
  Bank Nifty change built on a recovered close is labelled the same way;
- India VIX is date-checked the same way: a VIX bar from an earlier session is dropped, not shown
  as this session's.

**If Yahoo and NSE both fail**, the benchmark still ends before the expected session. When the
calendar says that session was real, `main.collect` in upload mode raises
`SessionAlignmentError("BENCHMARK_MISSING_RECAP_SESSION ...")` naming the fallback outcome.
`main.main` records the run as FAILED / BLOCKED (stage SESSION_ALIGNMENT) and publishes nothing.
This used to be a silent holiday-style skip. A real holiday is unchanged: it is still a quiet skip.

Verified 2026-09-25, replaying the 23 Sep 07:40 run with the index feeds as served then
(`validate_post_final_edge_cases.py`):
- Nifty 22 Sep closed 23329.0, -85.3 (-0.364%), from `ind_close_all_22092026.csv`, anchored on
  Yahoo's 21 Sep 23414.3 with 0.0 difference;
- NSE's own 23 Sep file implies the same close;
- Bank, IT and Pharma were recovered the same way, and all four match Yahoo's later backfill
  within 0.001 points.

## 11. Cached non-final bars (repair)

`market.SESSION_FINAL_TIME` (15:40 IST) is the single rule for when a session's bar is final. It
is shared by `_clean`, the write-through guard and `radar.cache_repair`. Rows cached before the
guard existed are repaired by `radar.cache_repair.repair_non_final_rows` (run via
`recover_benchmark_gap.py cache`).

Non-final means one of two things, and nothing else is selected:
- `BACKFILL_PENDING` (Close missing);
- `OK` but retrieved, in IST, before the session's own final time.

Rows on canonical non-session dates are left alone, as are sessions still in progress (DEFERRED).
Each write is conditional on the row still having the scanned `retrieved_at`/status, so a final
bar written meanwhile is never overwritten. Only the targeted keys are written. The written row
is read back, and its close is checked against NSE's bhavcopy. 2026-09-25 run: 252 rows
repaired (200 intraday 25 Sep, 52 backfill-pending 24 Sep). All 252 match NSE's close, and the
other 26,195 rows are byte-identical to the pre-repair backup.

## 12. Pre-open freshness (PRE-MARKET V1)

A pre-open number is shown only if it is the RIGHT reading, not merely a successful fetch
(`core/freshness.py`; `docs/PRE_MARKET.md`):

- US index close: bar date == the latest weekday before the PRE date, and the cutoff is after
  that session's 16:00 New York close + 20 min. Older -> STALE (US holiday / missing bar);
  newer -> FUTURE (look-ahead, rejected). A bar dated the PRE date itself is never read.
- LIVE reading (Asian index, GIFT Nifty): the last 5-minute bar whose END is at/before the
  cutoff, dated the PRE date, at most 30 min old (GIFT 20). A closed market has none.
- India VIX: the canonical previous session's close vs the canonical session before it, both
  by date; must agree with the report's own VIX fact within 1% or the change is dropped.
- The previous session's facts come only from the canonical report whose session IS the
  calendar's previous session of the PRE date; anything else blocks the PRE Short.
- Only FRESH readings are drawn; everything else is recorded in the plan's `omitted` with its
  freshness reason.

