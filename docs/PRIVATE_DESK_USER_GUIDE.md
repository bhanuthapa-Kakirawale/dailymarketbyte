# Private Trading Intelligence Desk: user guide (Phase 1)

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
| **Dashboard** | A. market regime diagnostics. B. today's Radar (compact). C. what changed today. D. sector pulse. |
| **Radar** | The full candidate table, with every column, the filters and a CSV download. |
| **Stock Explorer** | Every NIFTY 200 constituent, searchable. Any stock opens its intelligence page, whether or not it is a candidate. |
| **Sectors** | NIFTY 200 breadth, the metric table with coverage and definitions, NSE sectoral indices (a separate universe), and the per-sector table with a reconciliation row. |
| **History** | Every recorded Radar appearance. Filter by date range, symbol, sector, attention, family, detector, novelty and appearance. Shows how often each stock appeared. |
| **Data Quality** | Source freshness, database health, Radar run markers and issues, detector-replay reconciliation, OHLCV coverage (missing or stale symbols), Market Structure coverage, official snapshot status, and recent DMB runs. |

### Market regime (dashboard A)

DMB has **no regime classifier**, so the desk shows no BULLISH or BEARISH label. It shows the
facts instead:
- the NIFTY 50 close, change and range, Bank NIFTY, India VIX and FII/DII flows. These come from
  the canonical report of that session, with each number's validation status.
- NIFTY 200 advances, declines and unchanged, unusual volume, range events, and how many sectors
  had more advances than declines. These come from the reconciled Market Structure artifact.
- the deterministic historical-context lines from `output/intelligence/`.

### Today's Radar: the columns

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
| Evidence | **VERIFIED** = the desk reproduced the detector values (see §4). Otherwise it shows the mismatch status. |

The table order is the recorded order (attention, then symbol). It is **not a ranking of
merit**. Click a column header to sort; unavailable values always sort last. Filter chips
combine with AND.

### WHY THIS STOCK?

Open "N reasons" in a row, or go to the stock page. Each recorded reason code is one line:

```
✓ VOLUME UNUSUAL - Volume is 4.8x its prior-20-session average.
    relative volume: 4.84x · session volume: 58.17 L · prior20 avg volume: 12.01 L
    Rule: RVOL >= 3x prior-20 average · data as of 2026-09-30
```

- ✓ means the values were reproduced and reconciled (§4).
- **?** means the reason was recorded by the evening run but its values could not be
  reproduced, so no number is shown.
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
| Pipeline status, issues | `output/radar/daily_radar_<SESSION>.json` |

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
- It has no regime label, no new score, no ranking and no hit rates. Historical outcome research
  is Phase 2.

## 6. Files it writes, and cleanup

Only `output/private_desk/`:
- `cache/replay_<SESSION>.json`: detector-replay results, keyed by a fingerprint of the inputs
  and recomputed automatically when they change;
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
