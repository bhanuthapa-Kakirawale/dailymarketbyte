# Private Trading Intelligence Desk: user guide (Phase 1)

> **Status: PRIVATE DESK PHASE 1 (read-only intelligence dashboard) is feature-complete,
> pending the owner's final visual approval.** Phase 2 (historical outcome analytics), the Kite
> hand-off and any trade workflow have not been started.

A **local, read-only** web dashboard for the owner. It answers one question: *what deserves my
attention today, and why?* It turns the private Radar's output into an explainable list of
stocks for **human review**.

> **A Radar candidate is not a trade recommendation.** It is an attention item: a stock in
> which at least two independent evidence families fired on the session. The desk shows
> observations, context and detector results. It never says what to do, it never trades, and
> the owner makes every decision.

## 1. Start and stop

```
scripts\run_private_desk.bat          (or: python -m private_desk.app)
```

- Open **http://127.0.0.1:8765** in a browser. The console prints the URL and how fresh each
  source is.
- Stop it with **Ctrl+C** in that console window.
- `scripts\check_private_desk.bat` (`python -m private_desk.check`) runs the same freshness and
  health checks without starting a server. It exits 0 when the data is ready and 2 when
  something needs attention.
- One-time setup: `pip install -r requirements-desk.txt` (FastAPI, uvicorn, Jinja2, Plotly). The
  DMB pipeline and GitHub Actions do not need these packages.

Options are `--port N`, `--out-dir PATH` (read another output root) and `--cache-dir PATH`.
`--host` accepts only loopback addresses. `0.0.0.0` or a LAN address is refused, because Phase 1
is local only.

**When to use it:** after `scripts\run_evening_full.bat` has finished (around 19:30 IST). If the
evening job is still writing, a page may say DATA UNAVAILABLE. Reload it a minute later.

If PRE/POST haven't run for a few sessions, `scripts\run_intelligence_refresh.bat` (read-only
counterpart: `scripts\check_intelligence_coverage.bat`) catches up the latest session and
self-heals the previous 30 days of price-derived coverage WITHOUT rendering any video - see
docs/INTELLIGENCE_REFRESH.md. Its outcome shows up on the Data Quality page's "Intelligence
refresh" panel automatically; no separate Desk refresh step is needed.

## 2. The header (every page)

- **PRIVATE / LOCAL ONLY**: nothing on this site is ever published.
- **Latest completed market session**: the NSE calendar's answer (a session counts as final
  after 15:40 IST). It is never a provider's latest row.
- **Showing session**: the Radar session on screen. Pick another one with the selector. Every
  panel then shows that session only.
- **Radar / Market data / Market Structure / Canonical report / Official events as of**: the
  session and timestamp each source covers.
  - **FRESH** means the source covers the latest completed session.
  - **STALE** means it covers an older one.
  - **MISSING** means the source has nothing.

  Anything not FRESH also shows as a yellow banner.
- The desk **never mixes sessions**. If a source has nothing for the session on screen, its
  panel says UNAVAILABLE and a banner names it. It does not substitute another day.
- **UNAVAILABLE** (grey) always means "no data". The desk never shows a zero in place of
  missing data.

## 3. Pages

| Page | What it shows |
|---|---|
| **Dashboard** | *What deserves attention first?* Market context → what changed today → today's **attention set** (about 8-12 candidates) → sector pulse. It is a SUBSET. |
| **Radar** | *Show me everything.* The **complete** candidate list, with every column, the filters and a CSV download. It is the authoritative list. |
| **Stock Explorer** | Every NIFTY 200 constituent, searchable. Any stock opens its intelligence page, whether or not it is a candidate. |
| **Regime** | The market regime of the session: the rule applied, supporting and conflicting evidence, every dimension with its numbers, rule and source, the last 30 sessions of regime history, and the full V1 rule set (docs/PRIVATE_MARKET_REGIME.md). |
| **Sectors** | NIFTY 200 breadth, the metric table with coverage and definitions, NSE sectoral indices (a separate universe), the per-sector table with a reconciliation row, and a separate FPI sector flow (NSDL) table. |
| **Institutional** | NSE provisional FII/FPI + DII cash flow (latest + last 10 reported sessions), CDSL's latest depository-reported daily category/route table, NSDL's latest fortnightly sector table with AUC shown separately - docs/INSTITUTIONAL_FLOW_INTELLIGENCE.md. |
| **Market Events** | Earnings/IPO/OFS/government-auction/buyback/open-offer/delisting calendar, TODAY / TOMORROW / NEXT 7 DAYS / RECENTLY ANNOUNCED per family, with a family filter. EARNINGS, OFS and BUYBACK are all live from NSE's own feeds; IPO is a live, read-only projection of the existing IPO Watch data (shown as VIEW_ONLY, never NOT_SUPPORTED_YET); government-auction/open-offer/delisting still show NOT_SUPPORTED_YET - docs/MARKET_EVENTS_ENGINE.md. |
| **History** | Every recorded Radar appearance. Filter by date range, symbol, sector, attention, family, detector, novelty and appearance. Shows how often each stock appeared. |
| **Data Quality** | Source freshness, database health, Radar run markers and issues, detector-replay reconciliation, OHLCV coverage (missing or stale symbols), Market Structure coverage, official snapshot status, institutional-flow status (NSE/CDSL/NSDL + sector-mapping coverage), the regime classifier's status (version, freshness, available dimensions, historical validation), recent DMB runs, and the intelligence-refresh freshness/backfill status (latest `refresh` run, per-session coverage summary, blocking reasons). |

### Dashboard = attention subset, Radar = complete set

The Dashboard shows "**N attention candidates from M Radar candidates**" and a **View all M in
Radar →** link. The candidates it doesn't show are not removed. They are all on the Radar page,
unchanged. **Attention ≠ recommendation.** The attention set is a reading order for owner
review, built from facts the Radar already recorded.

#### The attention-set rule (deterministic, no score)

Candidates go into named tiers. The list is read tier by tier and cut at 10.

| Tier | Label | Condition (existing Radar fact) | Order inside the tier |
|---|---|---|---|
| 1 | STORY SELECTED | chosen by the Radar's own editorial selector (≤ 5 per session) | the selector's own story order |
| 2 | 3 EVIDENCE FAMILIES | HIGH_INTEREST: volume + structure + relative performance all active | existing Radar order |
| 3 | VOLUME + STRUCTURE | unusual volume (VOLUME family) and a structure event on the same session | existing Radar order |
| 4 | NEW · FIRST RECORDED / NEW · BACK AFTER N SESSIONS | Radar novelty NEW_CANDIDATE | first-ever appearances first, then the longest absence first |
| 5 | STATE CHANGE · <novelty> | any other Radar novelty except CONTINUATION | existing Radar order. **Only fills a quiet day up to 8.** |

- Tiers 1-4 fill the set up to **10** (`ATTENTION_MAX`). If they give fewer than **8**
  (`ATTENTION_MIN`), tier 5 fills up to 8.
- An unchanged continuation appears only through tiers 1-3.
- The final tie-breaker is always the existing Radar order: HIGH INTEREST before NOTABLE, then
  symbol.
- The "In attention set because" column shows every tier condition a candidate meets. There is
  no hidden number.
- The rule lives in `private_desk/services/attention.py`. A test forbids any computed "score".

Real 30 Sep result: 31 Radar candidates, 10 in the attention set.
- the 5 story selections: FORTIS, TIINDIA, APLAPOLLO, APOLLOHOSP, BOSCHLTD;
- MAXHEALTH (volume + structure, back after 7 sessions);
- the 4 first-recorded candidates: IDEA, KOTAKBANK, NATIONALUM, NESTLEIND.

#### What changed today

The panel opens with one count per state, read straight from the candidate states. Each count
links to the Radar page with that filter applied. The headline line always adds up:
**candidates = new + state change + unchanged**.

| State | Meaning |
|---|---|
| NEW | Radar novelty NEW_CANDIDATE: no candidate appearance in the last 5 sessions. |
| REAPPEARED | Recorded before, then absent for at least one session. This can overlap NEW (a long gap) or a state change. |
| STATE CHANGE | A candidate before, and something changed: a new evidence family, a new structure event, a persistence or direction transition, or attention escalation (NOTABLE → HIGH INTEREST). |
| NEW STRUCTURE EVENT | A new range break or SMA cross versus its prior appearance, or on a new candidate. |
| NEW UNUSUAL VOLUME | The VOLUME family is newly active. |
| CONTINUING, UNCHANGED | Radar novelty CONTINUATION: the same recorded state as its prior appearance. |
| NO LONGER A CANDIDATE | A candidate on the previous session but not now. Nothing more is claimed about why. |

Below the counts are up to **8 change items**: attention-set members first, then other changed
candidates in the Radar default order. Each item gives the symbol, the change type and one to
three factual lines from existing Radar facts, for example "Back on Radar after 7 sessions",
"Relative volume 6.9× vs prior-20-session average" or "Close 4.5% below prior 20-session low".
Click an item to open the stock page. "All changes by type" expands the full lists.

### Market context (dashboard)

The left card is the **market regime**: BULLISH, BEARISH, NEUTRAL, TRANSITIONAL or
INSUFFICIENT_DATA. It comes from the deterministic classifier in docs/PRIVATE_MARKET_REGIME.md
(no model, no score). The card shows:
- the label and the session it describes;
- the state of every dimension: Index trend, Breadth, Sector participation, Volume
  participation (these four decide), Volatility (a stress check that can only block BULLISH),
  and Relative strength and Institutional flow (context only);
- **Why**: the rule that fired and the numbers each state rests on;
- any unavailable dimension.

`detail →` opens the **Regime** page.

**Universe badges (history only).** The regime history uses NIFTY 200 membership, and exact
historical membership is stored only from 2026-09-24 (the Data Quality page shows the date).
- **PIT** (point-in-time): that session's own constituent list was used.
- **APPROX** / "APPROXIMATE UNIVERSE": an earlier session reused the earliest stored list. Prices
  still stop at that session (no future prices). The label is a rough historical
  reconstruction, useful for context and stability inspection, but not validation.
- **UNKN**: membership could not be established. Example: 29 Sep 2026 had no list, and the
  index was rebalanced between the 28 Sep and 30 Sep lists.

The badge is metadata. It never changes the regime or its WHY, and the current session normally
shows no badge. Tick "point-in-time universe only" on the Regime page to hide approximate rows. The label describes the current environment. It is not a
forecast or a recommendation, and it never changes the Radar candidates, their order or the
attention set.

To the right are the session's plain facts:
- the NIFTY 50 close, change and range, Bank NIFTY, India VIX and FII/DII flows. These come from
  the canonical report of that session, with each number's validation status.
- NIFTY 200 advances, declines and unchanged, unusual volume, range events, and how many sectors
  had more advances than declines. These come from the reconciled Market Structure artifact.
- the deterministic historical-context lines from `output/intelligence/`.

### Radar table columns (Radar page and the dashboard attention set)

| Column | Meaning |
|---|---|
| Attention | **HIGH INTEREST** = all 3 evidence families active. **NOTABLE** = 2. |
| Families | **VOL** = unusual volume (RVOL ≥ 3x). **STRUCT** = a range break or SMA cross. **REL** = persistent relative performance versus NIFTY 50. Hover over a badge for the exact rule. |
| Radar novelty | The Radar's own classification (`radar.novelty`): NEW (no appearance in the last 5 sessions), CONTINUATION (unchanged), or the specific change (NEW EVIDENCE FAMILY, NEW TECHNICAL EVENT, PERSISTENCE / DIRECTION TRANSITION, ATTENTION ESCALATION, MULTIPLE CHANGES). |
| Appearance | Derived by the desk from the recorded candidate history. **FIRST RECORDED** = no earlier appearance since history began. **CONSECUTIVE** = also a candidate on the previous session. **REAPPEARED +Ns** = earlier appearance with a gap; N is trading sessions since that appearance. |
| Day % | The session's price change. Colour follows the actual move, never the internal evidence direction. |
| RVOL | Session volume divided by the mean of the prior 20 sessions. The detector level shows next to it. |
| Structure | ▲20D / ▼20D = close above the prior 20-session high / below the prior 20-session low (same for 50D). ↗SMA20 / ↘SMA20 = an SMA cross. |
| vs NIFTY 5D / 20D | Stock return minus NIFTY 50 return, in percentage points. |
| Story | SELECTED = the editorial selector chose it for a Radar story (on the dashboard this shows as an **S** badge). |
| Official | The stock is on an exchange list for the session (F&O ban, ASM, GSM, IPO). |
| Evidence | **VERIFIED** = the desk reproduced the detector values (see §4). **VALUES UNAVAILABLE** = the recorded reasons are shown without numbers because the replay did not reproduce them. |

**Radar default order** (a UI order only; the stored Radar output is unchanged):
1. NEW candidates;
2. other state changes;
3. unchanged continuations.

Inside each group: HIGH INTEREST first, then symbol. It is **not a ranking of merit**, and no
score is computed. The table header and the Symbol column stay visible while you scroll. Click a
column header to re-sort; unavailable values always sort last. Filter chips combine with AND,
and a link such as `/radar#filter=NEW` opens the page with that chip already selected.

### WHY THIS STOCK?

Open "N reasons" in a row, or go to the stock page. Each recorded reason code is one line:

```
✓ VOLUME UNUSUAL - Volume is 4.8x its prior-20-session average.
    relative volume: 4.84x · session volume: 58.17 L · prior20 avg volume: 12.01 L
    Rule: RVOL >= 3x prior-20 average · data as of 2026-09-30
```

- ✓ means the values were reproduced and reconciled (§4).
- **?** with **VALUES UNAVAILABLE** means the reason was recorded by the evening run but its
  values could not be reproduced, so no number is shown. The stock page's WHY THIS STOCK box
  then shows VALUES UNAVAILABLE instead of EVIDENCE VERIFIED. Example: HINDCOPPER and VAML on
  29 Sep.
- No criterion appears unless the Radar recorded it, and the rule text is read from the Radar's
  own thresholds (`radar/thresholds.py`).

### Stock intelligence page

- **A. Summary**: close, day change, Radar state, first and last appearance, RVOL, WHY THIS
  STOCK.
- **B. Price + volume chart**: stored OHLCV, canonical sessions only. Provider holiday
  placeholder bars are removed before anything is drawn. Toggles add SMA20, SMA50 and the prior
  20/50-session high/low, which are the levels the Radar compares the close against. There is
  no EMA or 200-day line, because no Radar detector computes one for stocks.
- **C. Structure**:
  - prior 20/50-session high and low, which exclude the session itself;
  - position in the range, a desk-derived (close − low) / (high − low) where below 0% or above
    100% means outside the range;
  - SMA distances, the 5-session range against its median, and the session's structure events.
- **D. Volume intelligence**:
  - session volume, the prior-20 average and RVOL;
  - the Radar level and whether the VOLUME family fired;
  - a 60-session RVOL chart with the 2x and 3x lines.

  Market Structure counts "unusual volume" at ≥ 2x. That is a wider net than the Radar's VOLUME
  family (≥ 3x). Both rules are stated on the page.
- **E. Relative performance**: 1D, 5D and 20D versus NIFTY 50.
  - 50D is UNAVAILABLE because the Radar doesn't compute it.
  - Stock versus sector is UNAVAILABLE because the Radar pipeline supplies no sector
    benchmark.
- **F. Sector context**: the stock's NIFTY 200 sector, with breadth, the median 1D change
  (derived by the desk), unusual volume, range events and the Radar candidates in that sector.
- **G. Radar history**: every recorded appearance, with novelty and appearance. No forward
  returns are computed (that is Phase 2).
- **H. Official events**: the stock's entries in that session's persisted NSE snapshots, with
  source and list date.

## 4. Where the numbers come from

All of these are read-only. Nothing is fetched.

| Data | Source |
|---|---|
| Candidate set, reason codes, families, attention, persistence | `output/data/radar_candidate_history.db` (what the evening run recorded) |
| Novelty | `radar.novelty.classify_history` over that recorded history |
| Story selections | `output/data/editorial_selections.db` |
| Detector values (RVOL, range levels, SMAs, relative pp) | **Detector replay**: the Radar's own detector functions re-run on `output/data/market_ohlcv.db` |
| Breadth, unusual volume, range events, sectors | `output/market_structure/market_structure_<SESSION>.json` (reconciled on load) |
| NIFTY, VIX, flows, sectoral indices | the canonical report in `output/reports/` |
| Official lists | `output/official_snapshots/<SESSION>/` (checksum-verified) |
| Institutional flow (NSE/CDSL/NSDL) | `output/institutional_flows/<SOURCE>/` (immutable snapshots, no fetch on page load) |
| Market events (earnings/IPO/OFS/auctions/buyback/open offer/delisting) | `output/market_events/<FAMILY>/` (immutable revisions, no fetch on page load) |
| Pipeline status, issues | `output/radar/daily_radar_<SESSION>.json` |
| Market regime | `private_desk.regime` over the OHLCV store (NIFTY 50 + NIFTY 200), the Market Structure constituent lists and the canonical report, from data up to that session only |

**Detector replay and reconciliation.** The candidate history stores *which* rules fired, but
not the values behind them. The desk recomputes the values by running the same detector chain
the evening Radar ran, on the stored OHLCV. It never fetches, so a symbol the store cannot cover
is skipped. It then compares the replayed reason codes with the recorded ones, and shows values
only for candidates that match exactly (VERIFIED). The Data Quality page lists any mismatch. A
mismatch can happen if the OHLCV store was repaired after the run, or if the universe file
differs. Verified locally: all 128 candidates of 25, 28, 29 and 30 Sep were checked. Every 25,
28 and 30 Sep candidate matched exactly. On 29 Sep, 2 candidates could not be replayed because
the nearest stored universe file is from 28 Sep.

## 5. What the desk does NOT do

- It places no orders and generates none: no BUY, SELL, HOLD, entry, stop-loss, target or
  expected return. A test scans every rendered page for that language.
- It never contacts Zerodha or Kite and has no broker library. See
  docs/PRIVATE_DESK_KITE_INTEGRATION.md for the future contract.
- It uploads nothing, publishes nothing, and needs no YouTube, Gemini or broker credentials.
- It modifies no production store. SQLite is opened read-only (`immutable=1` when no WAL is
  pending, plus `PRAGMA query_only`). Every route is a GET. A test fingerprints the stores
  before and after use.
- It is not part of REPORT / PRE / POST. Nothing in the pipeline imports it, so a desk failure
  cannot affect a video.
- Its market regime label is deterministic context (docs/PRIVATE_MARKET_REGIME.md): no model,
  no score, no forecast, and it never re-ranks or filters the Radar. The desk has no new score,
  no ranking and no hit rates. Historical outcome research is Phase 2.

## 6. Files it writes, and cleanup

Only `output/private_desk/`:
- `cache/replay_<SESSION>.json`: detector-replay results, keyed by a fingerprint of the inputs
  and recomputed automatically when they change;
- `regime/regime_snapshots_<version>.json`: derived market-regime snapshots, rebuilt when the
  inputs or the calculation version change;
- `regime_research/`: distributions, threshold review and historical validation written by
  `python -m private_desk.regime.research` (run it after the evening job when you want a fresh
  validation report);
- `screenshots/`: owner-review captures, if any.

The whole folder is **safe to delete at any time**. Everything in it is regenerated on the next
page load. It holds no Radar history and is not synced by the StateStore. The cache refuses to
write anywhere else in the output root.

## 7. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| Yellow "STALE" banner | The evening job hasn't run for the latest session. Run `scripts\run_evening_full.bat`. |
| "Detector replay unavailable" | The Market Structure universe file or the benchmark is missing for that session. Reasons still show, without values. |
| A page says DATA UNAVAILABLE and "failed safely" | A store was unreadable, typically while the evening job was writing. Reload. |
| Port in use | `scripts\run_private_desk.bat --port 8766` |
