# Private intelligence (PRIVATE_ANALYTICS)

The public Short (`PUBLIC_UNREGISTERED`) deliberately shows no stock-level technical
intelligence. That intelligence is **not removed**: the public restriction applies only at the
publication boundary (`publication.PublicationGate`, before a storyboard exists). Everything
below is still computed every evening, stored, and renderable privately.

Verified 2026-09-27:
- `tests/test_private_intelligence.py`: the same inputs render named Radar stories under
  PRIVATE_ANALYTICS and none in public (blocked with reason codes); the Radar engine and the
  private history schemas are present.
- A private frames-only render of the stored 21 Sep session drew named-stock technical stories
  (MANKIND, ETERNAL, OFSS: range breaks with volume evidence, a 20-day high, 4.1x volume).

**Browse it:** the Private Trading Intelligence Desk (`scripts\run_private_desk.bat`,
http://127.0.0.1:8765) is a local, read-only web dashboard over everything below - Radar
candidates with WHY THIS STOCK, stock pages, sectors, history and data quality. See
docs/PRIVATE_DESK_USER_GUIDE.md.

## 1. What exists

| Intelligence | Content |
|---|---|
| Market Radar candidates | Named NIFTY 200 stocks with detector evidence: unusual volume (relative volume vs the prior 20 sessions), technical events (range breaks, 20-day highs/lows, close location), relative performance vs the benchmark; composite candidates, novelty / continuation, the editorial selection |
| Radar stories | Chart-first stock stories (real OHLC candles, event annotation, volume histogram, takeaway) |
| Candidate history | Every candidate per session with its state, for novelty and continuation |
| Market Structure observations | Per-constituent observations behind the public counts (the internal artifact names every security; the public video shows counts only) |
| OHLCV history | Daily bars for the universe (benchmark included) |
| Movers | Ranked gainers / losers with coverage audit (private only since the publication boundary) |
| PRE stock watch | Carry-forward of the previous session's published Radar stories (private only) |

## 2. Where it is stored

| Store | Path | Tables / files |
|---|---|---|
| OHLCV | `output/data/market_ohlcv.db` | `daily_ohlcv` |
| Radar candidate history | `output/data/radar_candidate_history.db` | `radar_candidate_history`, `candidate_history_runs` |
| Editorial selection / publication | `output/data/editorial_selections.db` | `editorial_selections`, `radar_publications` |
| Canonical history | `output/data/market_history.db` | `reports`, `facts`, `observations`, `publication_runs`, ... |
| Daily Radar result | `output/radar/daily_radar_<SESSION>.json` | detector counts, candidates, stories, diagnostics |
| Radar presentation | `output/radar/presentation/radar_presentation_<SESSION>.json`, `radar_visual_evidence_<SESSION>.json` | story models + chart evidence |
| Radar validation runs | `output/radar/radar_<SESSION>.json`, `output/radar_validation*/` | standalone validation artifacts |
| Market Structure (named observations) | `output/market_structure/market_structure_<SESSION>.json` | `observations[]` per constituent |
| Private renders | `output/private_radar/<SESSION>/`, `output/radar_phase2/` | frames / MP4 |

All of these are protected from cleanup (docs/CLEANUP_POLICY.md) and synced by the StateStore
when one is configured (docs/STATE_STORE.md). Back up `output/data/` and `output/radar/`.

## 3. Modules that generate it

- `radar/`: `acquisition`, `ohlcv_service` / `relative_acquisition` / `technical_acquisition`
  (data), `volume`, `technical`, `relative` (detectors), `composite` (candidates), `novelty`,
  `editorial_selector`, `daily_pipeline.run_daily_radar` (the production pipeline),
  `presentation_planner` / `presentation` / `visual_evidence`, and `run` / `*_validation`
  (standalone validation tools).
- `market_structure/`: observations and aggregates (the anonymous public counts come from here).
- `storage/`: `candidate_history_*`, `editorial_*`, `ohlcv_*` repositories.
- `presentation/radar_story.py`, `daily_video/radar_story_scene.py`: private story rendering.
- Documentation: docs/MARKET_INTELLIGENCE_RADAR.md.

## 4. How to run it locally

- **Every evening, automatically:** `scripts\run_evening_full.bat` (its REPORT step,
  `scripts\run_evening.bat`'s command) runs the Radar for the session and
  writes everything in section 2.
- **Private video / frames of a session** (never uploaded; own folder):
  ```
  python render_daily_market_byte.py --report output\reports\premarket_<EDITION>.json ^
      --profile PRIVATE_ANALYTICS --no-hook-ai --out-dir output\private_radar\<SESSION> ^
      --out output\private_radar\<SESSION>\private_<SESSION>.mp4
  ```
  Add `--frames-only` for stills only (seconds instead of minutes). Do **not** use
  `python main.py --profile PRIVATE_ANALYTICS` for this: it writes into the day's public POST run
  folder.
- **Standalone Radar validation run** (live data, intelligence-only): `python -m radar.run`
  writes `output\radar\radar_<SESSION>.json`.
- **Rebuild one session's Market Structure:** `python -m market_structure.build --session YYYY-MM-DD`.
- **Browse it (read-only):** `scripts\run_private_desk.bat` -> http://127.0.0.1:8765
  (docs/PRIVATE_DESK_USER_GUIDE.md); `scripts\check_private_desk.bat` for a freshness check.

## 5. Deliberately excluded from public YouTube output

Under `PUBLIC_UNREGISTERED`, the publication gate refuses, with reason codes recorded in the audit:
- named-stock technical analysis from the Radar (`SECURITY_TECHNICAL_ANALYSIS`);
- any named stock without an official exchange event (`SECURITY_WITHOUT_OFFICIAL_EVENT`);
- rankings / top gainers-losers (`SECURITY_RANKING`);
- stock watch, and the Radar hook archetypes.

A Radar-only stock may still count anonymously in Market Structure. PRIVATE_ANALYTICS output
always fails the publication audit and can never be uploaded.
