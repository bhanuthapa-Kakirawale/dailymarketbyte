# NSE official MCP — feasibility (research packet, 2026-10-23)

**Decision: A. RESEARCH_ONLY.** Technically the Bhavcopy server is an excellent independent
EOD check (11/11 sampled equities matched DMB's stored OHLC exactly over 21 sessions; the Nifty
50 close matched the canonical report exactly). It also found two real data defects in DMB's
Yahoo-sourced store. But NSE's published terms give no permission for the uses DMB would need:
automated daily collection, local storage, feeding stock-selection analytics, or anything
public or monetised. **Overall: CONDITIONAL GO** for a private, disabled-by-default secondary
verification adapter (decision B), **only** after NSE gives written permission. **NO-GO** for
public video, monetised content and trading.

Nothing in this packet touches production. `nse_mcp_research/` is a standalone package that
no pipeline module imports (test-enforced). No adapter is wired up and nothing is scheduled.

## 1. Verified official endpoints

Source: https://www.nseindia.com/nse-mcp (fetched 2026-10-23 ~15:45 IST).

| Server | URL | Transport | Auth | `serverInfo` (live) |
|---|---|---|---|---|
| NSE Bhavcopy | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | streamable-http | "No Auth" | `nse-bhavcopy-redis-mcp` 1.0.0 |
| CM Market Live | `https://mcp.nseindia.in/cmmkt/mcp` | streamable-http | "No Auth" | `cm-market-mcp` 1.0.0 |

Both servers negotiated MCP protocol `2025-06-18` and advertise `tools`, `prompts`,
`resources`, `completions` and `logging`. The research client uses only `tools`.

From the page, quoted: Bhavcopy *"updates every day using previous trading day's final
numbers, giving access to the trading history for the latest 5 years"*. CM Market *"running
just 1-3 minutes behind real-time trading"*.

**Transport quirk (verified).** Each server returns an `Mcp-Session-Id` but no affinity
cookie, and it sits behind Akamai with a backend pool that is not sticky. A session-bound
request sometimes lands on a node that never saw the session and gets **HTTP 404** with a Java
stack trace. On the first discovery runs, about half of the follow-up calls did this; on the
37-call probe, none did. The client retries exactly this case at most twice, and each retry
counts against its request budget. SSE responses can stay open after their one `message`
event, so the client stops reading at the first complete event.

## 2. Tools discovered (live `tools/list`, 2026-10-23)

**Bhavcopy (21 tools):** `get_stock_history` (daily OHLCV, max 3 months per call, chained via
`next_end_date`), `get_bulk_quote` (≤ 50 symbols, *latest* bar only, no date parameter),
`get_corporate_actions` (splits/bonus/dividends with `exDate`, `actionType`,
`adjustmentFactor`; fetched live from NSE, ~0.7 s), `get_market_breadth`, `get_top_movers`
(≤ 50, by date), `get_top_by_volume`, `get_index_snapshot` (~170 indices, EOD OHLC, P/E, P/B,
dividend yield), `get_index_history` (≤ 12 months per call), `get_index_movers`,
`get_index_performance`, `get_index_valuation`, `compare_indices`, `get_market_mood`,
`get_ltp_by_date`, `get_volume_analysis`, `get_52_week_high_low`, `moving_average`,
`compare_stocks`, `get_stock_vs_index`, `search_symbols`, `nse_lookup_symbol`.

**CM Market (15 tools):** `cm_get_stock_quote`, `cm_get_equity_stocks`, `cm_get_sme_stocks`,
`cm_get_bond_stocks`, `cm_get_call_auction_stocks`, `cm_get_live_indices`,
`cm_get_index_quote`, `cm_get_live_market_data`, `cm_get_live_gainers`/`losers` (raw),
`nse_get_gainers`/`losers`/`market_movers` (sorted), `cm_get_data_status`,
`cm_get_allstocks_status`.

Observations that matter to DMB:
- **No tool carries a `readOnlyHint` annotation.** The client therefore calls only an explicit
  allowlist (`client.RESEARCH_TOOLS`), never a tool picked by its name.
- **Tool descriptions contain instructions aimed at an LLM**, e.g. *"ALWAYS call this before
  get_stock_history…"*, and characterisations such as *"A/D ratio > 1.5 indicates a bullish
  day"*. DMB treats these as untrusted data. The deterministic client never acts on them, and
  that wording must never reach DMB's on-screen text (DMB never calls a day "bullish").
- **Silent date substitution.** For a holiday or weekend, `get_market_breadth`,
  `get_top_movers`, `get_index_snapshot` and `get_ltp_by_date` return *"the nearest trading
  day"*. Verified: breadth for 2026-10-20 (NSE holiday) came back dated 2026-10-19. Only
  `get_index_snapshot` echoes `requested_date`. Any adapter must reject a row whose `date` is
  not the requested session (DMB's session-alignment rule, VALIDATION_RULES §9).
- **Prices are raw and unadjusted** (Bhavcopy). Only `get_stock_vs_index` says it adjusts.
  Verified on HDFCBANK's 1:1 bonus (ex-date 2025-08-26): before the ex-date, NSE's closes are
  exactly 2.0× DMB's Yahoo-adjusted closes and its volumes are 0.5×. NSE's
  `get_corporate_actions` reports `BONUS`, `adjustmentFactor 0.5`, `exDate 2025-08-26`.
- Some fields are **present but zero-filled** in `get_stock_history` (`deliveryQty`,
  `ch52WeekHigh`, `ch52WeekLow`). Zero must not be read as a value.
- The server's own SEBI-style disclaimer is appended to every tool description.

## 3. Source rights (NSE's published terms)

Read on 2026-10-23: the MCP page's Disclaimer and https://www.nseindia.com/static/nse-terms-of-use
(`/static/disclaimer` returned 404). I am not a lawyer. This is a conservative engineering
reading and the owner should confirm it with NSE.

Quoted from the MCP page:
- *"…access information and data … already available on the website of NSEIL, for
  informational purpose only and not using it for any commercial purposes."*
- *"…all data is provided strictly for educational and informational purposes; it is not
  intended for real-time trading, investment advice, or commercial deployment."*
- *"Access to Data through the MCP shall not be construed as granting any license, right or
  permission to commercially exploit, train or fine-tune any AI systems, models, algorithms …
  using such Data."*
- *"Access to and use of the MCP and Data shall be subject to … NSEIL's applicable Terms of Use."*

Quoted from the Terms of Use:
- data *"shall not be copied, modified, … reproduced, uploaded, transmitted, posted, stored
  (either in hardcopy or in an electronic retrieval system), … displayed, … broadcasted,
  published, … in any form, without prior written permission of NSE."*
- *"User is prohibited to conduct any systematic or automated data collection activities
  (including scraping, data mining, data extraction and data harvesting)…"*

| # | Use | Classification | Basis |
|---|---|---|---|
| 1 | Personal, interactive, informational research | **PERMITTED** (non-commercial only) | The stated purpose of the MCP; its intended users include "quantitative researchers" |
| 2 | Local historical caching | **PERMISSION_REQUIRED** | ToU: no storage "in an electronic retrieval system" without written permission |
| 3 | Automated (scheduled) collection | **PERMISSION_REQUIRED** | ToU forbids systematic or automated data collection |
| 4 | Feeding stock-selection analytics (Radar, trade candidates) | **PERMISSION_REQUIRED** | No licence to exploit "algorithms"; "not intended for real-time trading" |
| 5 | Commercial or monetised content | **NOT_ALLOWED** | "not … for any commercial purposes", "commercial deployment" |
| 6 | Derived charts or public publication (DMB Shorts) | **NOT_ALLOWED** for DMB | ToU: no display, broadcast or publication without permission; also #5 |
| 7 | A trading execution system | **NOT_ALLOWED** | "not intended for real-time trading" |

Having no authentication is not permission. Should NSE MCP ever enter `core/sources.py`, its
rights record would be **RESTRICTED** (like `SRC_NSEIX_*`), not REVIEW_REQUIRED. Under
`PUBLIC_REVIEW_REQUIRED_POLICY=ATTRIBUTED_EOD` a REVIEW_REQUIRED source could publish, and the
terms above forbid that.

Live probing stayed within use #1: one interactive, bounded probe of about 80 requests in total
across discovery, the comparison sample and the targeted checks, spaced 0.5 s apart. No
scheduling. Only derived deltas and shapes were written, to a session temp folder. No NSE
values were stored in DMB's stores and none are committed, apart from the few figures quoted in
this document to explain a finding.

## 4. Sample comparison: NSE MCP Bhavcopy vs DMB `market_ohlcv.db` (Yahoo)

Run: `python -m nse_mcp_research probe --session 2026-10-22`, on 2026-10-23 ~15:58 IST. The
store was read only from a byte copy (`storage.readonly.copy_database`). The production DB's
mtime was unchanged.

Window: 2026-09-22..2026-10-22 (21 NSE sessions). Tolerance: prices ±0.05, volume ±1%.

| Symbol | Sector / size | Verdict | O/H/L/C | Volume |
|---|---|---|---|---|
| RELIANCE | Energy, large | MATCH | 21/21 exact | 21/21 (worst 0.17%) |
| HDFCBANK | Bank, large | MATCH | exact | 21/21 |
| INFY | IT, large | MATCH | exact | 21/21 |
| ITC | FMCG, large | MATCH | exact | 21/21 (worst 0.19%) |
| MARUTI | Auto, large | MATCH | exact | 21/21 |
| SUNPHARMA | Pharma, large | MATCH | exact | 21/21 |
| POLYCAB | Cap. goods, mid | MATCH | exact | 21/21 |
| MAZDOCK | Defence, mid, volatile | MATCH | worst Δ 0.0001 | 21/21 |
| IDEA | Telecom, penny, very high volume | **VOLUME_ONLY_DIFF** | exact | **20/21** |
| HINDCOPPER | Metals, mid | MATCH | exact | 21/21 |
| M&MFIN | NBFC, mid (`&` symbol) | MATCH | exact | 21/21 |

The `&` symbol mapped without translation, since DMB stores the bare NSE symbol. There were no
`only_in_nse` sessions. **Every** symbol had one `only_in_store` session, 2026-10-02.

Other probes:
- **Nifty 50, 2026-10-22:** `get_index_snapshot` close 22,687.65 (−0.54%), identical to the
  canonical report `20261023_PRE_MARKET`.
- **History depth:** RELIANCE returned bars from 2021-08-30, slightly more than the documented
  5 years.
- **`get_bulk_quote`** at 15:58 IST on 2026-10-23 returned bars dated **2026-10-22**: during
  the session, "latest" means the previous session.
- **Corporate actions:** none in the window for the 11 symbols. The HDFCBANK test above covered
  the adjustment case.

### Discrepancies found (both are DMB-side data defects, not NSE errors)

1. **IDEA 2026-09-29 volume: Yahoo wrapped by exactly 2³⁰.** The store holds 76,437,580. NSE
   MCP and NSE's own archive bhavcopy (`sec_bhavdata_full_29092026.csv`, read once through the
   existing `radar.cache_repair.nse_bhavcopy`) both say 1,150,179,404. The difference is
   exactly 1,073,741,824 = 2³⁰. The stored value is about 8× below the neighbouring sessions
   (300–640M). This corrupts IDEA's relative-volume denominator for the 20 sessions after it,
   and any symbol trading more than 2³⁰ shares in a day is exposed to the same defect. **Not
   fixed here** (follow-up below).
2. **2026-10-02 (Gandhi Jayanti, NSE holiday): 183 placeholder bars** in the store
   (O=H=L=C=previous close, volume 0, `quality_status=OK`). This is the known Yahoo holiday
   placeholder (CLAUDE.md, session-alignment patch). `core/trading_calendar` lists 2026-10-02,
   so readers that apply the calendar drop these bars, and the impact is store hygiene only.
   NSE independently confirms there was no session. Any **new** reader that skips the calendar
   would ingest them.

## 5. Freshness, finality and PIT

| | Bhavcopy server | CM Market server |
|---|---|---|
| Represented session | the row's `date` (must equal the requested session; see the substitution note) | none: an intraday snapshot |
| Finality | FINAL_EOD ("previous trading day's final numbers") | **INTRADAY_DELAYED**, never EOD. At 15:58 IST it still served `lastTradedPrice` + `indicativeClosePrice` with `latestTimestamp "2026-10-23 15:58:26"` (IST, no zone) |
| Server-side timestamp | **none** (`source: "in-memory-cache"`, `query_ms`) | `updatedAt` / `lastCrawled` (UTC `Z`), `latestTimestamp` (naive IST) |
| Publication lag for session D | **not measured**. At 15:58 IST on D+1 the latest was D. When D appears on the evening of D (needed by the 19:30 REPORT job) or only on D+1 is **unverified** | 1–3 min (stated); 5-min crawl for gainers/losers, 1-min for all-stocks |

Because the Bhavcopy server publishes no "available since" timestamp, DMB cannot prove when a
bar became available. An adapter can only stamp its own `captured_at`. **A bar retrieved later
must never be used as evidence that it was available at an earlier decision cutoff**, so a
later NSE fetch can never upgrade an earlier PIT snapshot (Policy 2.0 snapshots, Radar
history).

## 6. Scalability (≈200-symbol Radar universe), estimated from measured calls

Measured: `tools/call` median latency 0.09–0.16 s (`get_corporate_actions` ~0.73 s, a live NSE
lookup); `initialize` ~0.6 s. Response sizes: 1 month of history ≈ 6.8 KB per symbol, bulk
quote ≈ 190 B per symbol, breadth 256 B.

| Task | Requests | Payload | Wall time (0.5 s spacing) | Note |
|---|---|---|---|---|
| Verify the **latest** session, 200 symbols | 4 × `get_bulk_quote` (50 each) | ~40 KB | ~3 s | only works if "latest" == the target session; no date parameter |
| Verify a **specific** past session, 200 symbols | 200 × `get_stock_history` | ~1.4 MB | ~2–3 min | no multi-symbol-by-date tool for OHLCV |
| Two-year backfill, 200 symbols | 8 × 200 = 1,600 | ~32 MB | ~20+ min | unacceptable without explicit permission |
| Corporate-action screen, 200 symbols | 200 × `get_corporate_actions` | small | ~4 min | slowest tool |

DMB **already** reads NSE's archive bhavcopy file (`radar.cache_repair.nse_bhavcopy`):
**1 request per session for the whole EQ universe** (2,671 rows on 2026-09-29), the same
official numbers. For bulk EOD verification, that existing route is far cheaper than the MCP.
It is still subject to the same NSE Terms of Use, which this packet does not re-litigate.

## 7. Use cases for DMB

| # | Use case | Tool support | Rights | Verdict |
|---|---|---|---|---|
| 1 | Independent OHLCV verification | ✅ exact match | #2/#3 permission | private, after permission; the archive CSV is cheaper |
| 2 | Corporate-action discrepancy detection | ✅ `get_corporate_actions` + raw prices (verified step detection) | #3/#4 | **strongest unique value** |
| 3 | Missing / phantom-bar detection | ✅ (found the 2026-10-02 placeholders) | #3 | private, after permission |
| 4 | Radar data-quality cross-checks | ✅ (found the IDEA 2³⁰ wrap) | #4 | private, after permission |
| 5 | Market-breadth verification | partial: all NSE mainboard stocks, **not** NIFTY 200, so it is a different universe and must never be mixed (PUBLICATION_POLICY) | #6 for public | private only |
| 6 | Sector / index verification | ✅ `get_index_snapshot` / `get_index_history` (Nifty matched exactly) | #6 | private only |
| 7 | Public gainers/losers verification | tools exist (`get_top_movers`) | **#5/#6 NOT_ALLOWED** | excluded; the public Short does not rank stocks anyway |
| 8 | Private Stock Explorer research | ✅ (interactive) | #1 | **permitted interactively today**, e.g. through Claude's own connector, outside the pipeline |

**Excluded outright:** anything in the public PRE/POST Short, anything monetised, any input
into trade execution, any CM Market quote used as an EOD fact, and any training on the data.

## 8. Security assessment

The research client (`nse_mcp_research/client.py`, tested in `tests/test_nse_mcp_research.py`):
- HTTPS, two-endpoint allowlist; redirects refused.
- No credentials: `trust_env=False`, so no proxy or netrc auth leaks in, and no auth header is
  ever sent.
- Bounded: 30 s timeout, a hard per-client request budget, a 5 MB response cap, bounded
  `tools/list` pagination, at most 2 session-404 retries.
- Calls only the explicit `RESEARCH_TOOLS` allowlist of read-only queries, and only tools the
  server actually listed.
- Responses are shape-checked JSON-RPC. Tool text is returned as data and is never executed or
  forwarded as an instruction (pinned with an injection-style string). Error excerpts are
  capped at 200 characters.
- Repository content is never sent. Requests carry only tool arguments (symbols and dates).
- Server-side observation: errors leak full Java stack traces. That is not DMB's risk, but it
  is a reason to treat the service as best-effort.

## 9. Possible future adapter (not built): `nse_mcp_research_adapter`

Pre-conditions: NSE's **written** permission covering automated collection, local storage and
private analytics use; the evening publication lag measured; owner approval.

- **Location:** `providers/nse_mcp.py`, mirroring `providers/gift_nifty.py`. A config gate,
  `NSE_MCP_ENABLED` plus `NSE_MCP_PERMISSION_REFERENCE` (both required, default OFF), copies the
  `operations/gift_policy.py` pattern: the endpoint working is not permission.
- **Output:** `Observation`s only. `SourceMetadata(source_name="nse_mcp_bhavcopy",
  source_type=PRIMARY, independence_group="NSE", display_rights_status="RESTRICTED")`. It joins
  the **NSE** independence group, so NSE MCP plus NSE's archive CSV is one witness; only NSE
  MCP plus Yahoo is two (CrossSourceValidator counts groups).
- **Provenance per reading:** `represented_session` (the row's own `date`; reject if it is not
  the requested session), `observed_at = None` (the server gives no market timestamp, so none
  is invented), `captured_at` (our clock), `source`, `source_version` (`serverInfo.name` plus
  version plus the MCP `protocolVersion`), `finality` (`FINAL_EOD` for Bhavcopy only; CM
  Market is never admitted as EOD), and `rights_status = RESTRICTED`.
- **Integration:** only as a verification input for the PRIVATE data-quality surface, e.g. a
  `production_quality`-style read-only audit or a Private Desk Data Quality row. Compare against
  `market_ohlcv.db` and report `VOLUME_WRAP`, `ADJUSTMENT_STEP`, `PHANTOM_BAR` and
  `PRICE_MISMATCH`. It never writes into `market_ohlcv.db`, never changes a Radar candidate,
  its order, Policy 2.0 scores or a PIT snapshot, and never reaches `publication/`. A
  `test_*_isolation.py` would enforce this, as `private_candidates` does.
- **Limits:** latest-session checks via `get_bulk_quote` (4 calls); specific sessions only on
  demand; no backfill loops; a per-run request budget; the session-404 retry; the
  silent-substitution guard.

## 10. Follow-ups (not in this packet)

1. **IDEA 2³⁰ volume wrap:** audit `market_ohlcv.db` for every symbol whose volume can exceed
   2³⁰, using the already-integrated archive bhavcopy. Repair through the existing manual
   `radar.cache_repair` route (owner-run). Then decide whether the Yahoo ingest should
   cross-check very large volumes.
2. Measure the Bhavcopy server's evening publication time for session D before revisiting
   decision B.
3. Ask NSE (data licensing) whether automated, non-public verification use is licensable, and
   on what terms.
