# Daily Byte — project guide for Claude Code

Automated ~75s faceless YouTube Short: recap of the previous Indian (NSE) market session for traders.
Runs Mon–Fri 07:40 IST on GitHub Actions, uploads to YouTube. Informational only, never advice.

## Commands
- `python main.py --demo` — offline render with synthetic data (red "DEMO DATA - NOT REAL" badge). Use this to test any visual change.
- `python main.py` — real data, no upload. `--upload` uploads; `--force` ignores holiday/already-posted checks.
- `python upload.py --auth` — one-time YouTube OAuth (creates token.json).
- Verify a render: `ffprobe` duration must be 75.0s; extract frames with `ffmpeg -ss <t> -i out.mp4 -frames:v 1 f.png` and look at them.

## Architecture
- `main.py` — orchestration, captions text, scene durations (`BASE_DUR`, stretching via `durations()`), hook line, ticker items, YouTube metadata, demo data, safety checks.
- `market.py` — yfinance data (`history`, `get_movers`, `get_globals`, `get_sectors`), NSE website client (`NSE`, `nse_all_indices`, `fii_dii_nse`), technicals in `analyze()` (EMA20/50, RSI14, swing-based support/resistance, trendline, floor pivots).
- `news.py` — Google News RSS (free) + Gemini free API with Google Search grounding (`ask_gemini`). `ai_pass()` is the single combined Gemini call per run (market facts: GIFT Nifty/FII-DII/Brent/independent Nifty close, each validated by date + range; mover/Nifty move reasons; today's events) - kept as one request instead of three separate calls to stay under the free-tier RPD/grounding quota.
- `chart.py` — matplotlib chart saved as 3 aligned layers (0 axes, 1 candles, 2 levels) so video.py can animate the reveal. Layers must stay pixel-identical in layout.
- `video.py` — Pillow draws frames, piped as raw RGB to ffmpeg (imageio-ffmpeg binary). Each scene subclasses `Scene` with `key(t)` (cache key; quantize animation so frames can be cached) and `draw(d, L, t)` onto an RGBA layer. `Backdrop`, `Ticker`, header, captions, progress line composed in `render()`.
- `music.py` — procedural copyright-free track with swells at scene boundaries; rotates through 5 mood/tempo presets by calendar date (`_variant_index`) so consecutive sessions don't sound identical. `assets/music.mp3` overrides with one fixed track every day; `assets/music/1.mp3`..`5.mp3` overrides one real track per rotation slot. Playback volume is `config.MUSIC_VOLUME` (env `MUSIC_VOLUME`, default low ~0.22), applied in `video.render()`'s ffmpeg audio filter.
- `upload.py` — YouTube Data API v3 upload.
- `config.py` — all settings/env vars, colours, `fmt_in()` Indian number formatting.
- `core/` — canonical domain (Phase 1 of the market-intelligence migration): `Observation` (what one source said, frozen), `Fact` (normalized fact + its observations), `ValidationResult`, `MarketReport`, plus the validator framework. No fetching, no drawing.
- `adapters/` — strangler boundary: turns `market.py`/`news.py`'s plain dicts into canonical Observations, rebuilding the provenance those dicts discard. `report_builder.build_and_save_report()` writes `output/reports/premarket_YYYY-MM-DD.json`.
- `tests/` — `pytest`, fully offline. `python -m pytest tests/ -q`.
- `docs/` — ARCHITECTURE, DATA_MODEL, VALIDATION_RULES, MIGRATION_PLAN. Update these in the same commit as any architecture-affecting change.

## Rules / decisions (keep these)
- No paid APIs. AI = Gemini free tier only (`GEMINI_API_KEY`, `GEMINI_MODEL`). Pipeline must still work with no key.
- An LLM is never a source of truth for a critical numeric market fact. Everything Gemini returns is `SourceType.AI`, and `core.validation` refuses to mark a critical metric (index/stock/sector moves, closes, levels, FII/DII, commodity, FX) `VERIFIED` when only AI observations back it — its ceiling is `PROVISIONAL`, and two agreeing Gemini answers are still `PROVISIONAL`. Passing a range check is NOT verification: a plausible wrong number passes. AI values reach `VERIFIED` only when an acceptable non-AI source independently agrees within tolerance. The rule lives in `CrossSourceValidator` so no new caller can skip it.
- The canonical layer is a tee, not a gate: `adapters`/`core` observe what the pipeline already collected and write the report artifact, while `video.py` keeps consuming the legacy dicts. That is what makes the video equivalent by construction. `build_and_save_report` swallows every exception on purpose — new architecture code must never cost a publication. Don't wire the report into the render path without deciding to change production behaviour.
- Don't rewrite a legacy module to make new code prettier — adapt at the boundary. Provenance must be honest: an unavailable source is `None`, never a plausible guess.
- Accuracy over completeness: never post unverified numbers. Nifty close is cross-checked (NSE or Gemini) and the run aborts if >0.2% off. AI-sourced numbers must pass date + sanity-range checks or be hidden. A failed optional scene (global, fii, sector) is dropped and nifty/gainers/losers stretch so total stays 75s.
- Don't trust a single data source blindly just because the fetch succeeded - verified 2026-09-18: yfinance's `BZ=F` (Brent crude) ticker returned -5.28% vs an independently-reported -1.54% real move (a ~4x error, likely a thin/mismatched contract). DOW/NASDAQ/GOLD/USD-INR were cross-checked the same day and matched real sources closely, so this isn't a general yfinance problem - it's specific to that ticker. Brent is now sourced only via `news.market_facts()`'s Gemini-verified `"brent"` fact (same hide-if-unverified pattern as GIFT Nifty), not `market.GLOBALS`. Before trusting any new single-sourced number in this pipeline, cross-check it against an independent source first.
- Mover "reason" headlines (`news.explain_moves`) must be filtered by both the headline's RSS `pubDate` (`_same_day`) and a date pattern embedded in the title text itself (`_title_date_ok`) before being used - verified that recurring "Stocks to Watch Today, <date>:" listicle columns get republished daily with a fresh `pubDate` but keep a stale headline (e.g. crawled 19 Sep, title still says "Aug 3"), and Google News' relevance ranking put that ahead of genuinely same-day coverage. Never take `headlines[0]` un-filtered as a mover's reason.
- Never compute a day-over-day % change as `Close.iloc[-1] / Close.iloc[-2]` without first checking `iloc[-2]`'s DATE matches the expected previous trading session - verified 2026-09-18: yfinance's per-stock bulk download (`get_movers`) silently dropped 17 Sep entirely for ~25% of the Nifty100 universe (e.g. ADANIGREEN), so `[-2]` quietly pointed at 16 Sep, turning a 2-day move into a false 1-day one (shown +5.06%, real +4.73%). `market.get_movers`/`get_sectors`/`get_market`'s Bank Nifty calc now all take the actual previous session date (`analyze()`'s `prev_date`, from the Nifty index's own reliable calendar) and drop/skip anything whose own previous-close date doesn't match it, rather than risk a silently-wrong multi-day change. Apply the same date-alignment check to any new day-over-day calculation added to this file.
- Skip upload if yesterday was a market holiday (latest session != previous weekday).
- Keep the on-screen and description disclaimer ("For information only - not investment advice"). No buy/sell language, no predictions in captions (SEBI).
- Layout must stay in the Shorts safe zone: content between y≈340 and 1195, captions box y 1215–1475, nothing important on the far right below y≈1100. Canvas 1080x1920, 30 fps. The strip below the disclaimer (y≈1524–1920) is left empty on purpose - reserved for the YouTube Shorts app's own UI overlay (title/channel/audio-track/like-comment-share). Don't add content there - this is a financial/informational video, not a music-video style visualizer.
- `SECTION_Y` (video.py) is load-bearing: `NiftyScene` hardcodes its chips row (y=446) and chart paste (y=508) relative to it rather than deriving them, so don't shift `SECTION_Y` without re-checking the chart still fits above y≈1195.
- Text must fit: use `fit()`, `ellipsize()`, `wrap()`; reasons are clipped to ~13 words.
- Rupee sign via `video.RS` (falls back to "Rs " if the font lacks ₹).
- Demo data must use placeholder names (STOCK-A...) so it can never be mistaken for real data.

- "Events today" (`news.todays_events`) must be specific, named events only - IPO opens/GMP, company results/corporate actions, RBI/govt decisions, Fed/global data - never generic daily "roundup" columns (Ahead of Market, Stocks to Watch, Market Wrap, etc., which just reuse the same catch-all title every day and aren't actual events). Both the Gemini prompt and the no-key fallback (`_fallback_events`, targeted per-category queries filtered through `_GENERIC_ROUNDUP`) must keep excluding those.

## Known limitations
- NSE website often blocks cloud IPs (GitHub runners) → FII/DII and sector data fall back to Gemini / Yahoo.
- GIFT Nifty only via Gemini search (hidden if unverifiable).
- YouTube uploads are private until the Google Cloud project passes the API audit. OAuth consent screen must be "In production" or tokens expire after 7 days.
- GitHub cron can start 5–20 min late; runs take ~4–6 min.
- Nifty 50 fallback constituent list in market.py goes stale; the live NSE CSV is preferred.
- Gemini's `generateContent` (`news.ask_gemini`) intermittently 404s/503s even with a valid key/model/project - confirmed as a known, unresolved server-side Gemini API bug (Google's own developer forum, Sep 2026 - same request sometimes succeeds, sometimes 404s, across different projects/keys). Not a sign the model name or key is wrong. `ask_gemini` retries those (5x, capped backoff) and logs the response body on failure; if it still fails, `ai_pass` already degrades gracefully - AI-only facts (GIFT Nifty, Brent, verified FII/DII) are simply omitted and reasons/events fall back to the non-AI paths, never a guess. This is by design, not a bug to "fix" further without a real Google-side change.
- A Gemini 429 ("You exceeded your current quota...") is a different failure from the 404/503 bug above - it's the free-tier RPD/RPM (or grounding) quota exhausted, not a transient server hiccup, so retrying within the same run can't succeed. `ask_gemini` fails fast on 429 (no backoff loop, unlike 404/503) straight to the same graceful no-AI-facts fallback. `news.ai_pass()` already merges what used to be three separate Gemini calls per run (`market_facts`/`explain_moves`/`todays_events`) into one request to stay under the free-tier quota; if 429s still recur daily, the remaining fix is upgrading the API plan/billing, not retrying harder or adding calls back.

## Ideas backlog
- Voiceover (free TTS), thumbnails/first frame optimisation, Hindi version, weekly recap, 52-week high/low list, OI/PCR data, Telegram/X cross-posting.
