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
- `news.py` — Google News RSS (free) + Gemini free API with Google Search grounding (`ask_gemini`). `market_facts()` returns GIFT Nifty / FII-DII / Nifty close, each validated by date + range.
- `chart.py` — matplotlib chart saved as 3 aligned layers (0 axes, 1 candles, 2 levels) so video.py can animate the reveal. Layers must stay pixel-identical in layout.
- `video.py` — Pillow draws frames, piped as raw RGB to ffmpeg (imageio-ffmpeg binary). Each scene subclasses `Scene` with `key(t)` (cache key; quantize animation so frames can be cached) and `draw(d, L, t)` onto an RGBA layer. `Backdrop`, `Ticker`, header, captions, progress line composed in `render()`.
- `music.py` — procedural copyright-free lo-fi track with swells at scene boundaries; `assets/music.mp3` overrides it.
- `upload.py` — YouTube Data API v3 upload.
- `config.py` — all settings/env vars, colours, `fmt_in()` Indian number formatting.

## Rules / decisions (keep these)
- No paid APIs. AI = Gemini free tier only (`GEMINI_API_KEY`, `GEMINI_MODEL`). Pipeline must still work with no key.
- Accuracy over completeness: never post unverified numbers. Nifty close is cross-checked (NSE or Gemini) and the run aborts if >0.2% off. AI-sourced numbers must pass date + sanity-range checks or be hidden. A failed optional scene (global, fii, sector) is dropped and nifty/gainers/losers stretch so total stays 75s.
- Skip upload if yesterday was a market holiday (latest session != previous weekday).
- Keep the on-screen and description disclaimer ("For information only - not investment advice"). No buy/sell language, no predictions in captions (SEBI).
- Layout must stay in the Shorts safe zone: content between y≈340 and 1195, captions box y 1215–1475, nothing important on the far right below y≈1100. Canvas 1080x1920, 30 fps.
- Text must fit: use `fit()`, `ellipsize()`, `wrap()`; reasons are clipped to ~13 words.
- Rupee sign via `video.RS` (falls back to "Rs " if the font lacks ₹).
- Demo data must use placeholder names (STOCK-A...) so it can never be mistaken for real data.

## Known limitations
- NSE website often blocks cloud IPs (GitHub runners) → FII/DII and sector data fall back to Gemini / Yahoo.
- GIFT Nifty only via Gemini search (hidden if unverifiable).
- YouTube uploads are private until the Google Cloud project passes the API audit. OAuth consent screen must be "In production" or tokens expire after 7 days.
- GitHub cron can start 5–20 min late; runs take ~4–6 min.
- Nifty 50 fallback constituent list in market.py goes stale; the live NSE CSV is preferred.

## Ideas backlog
- Voiceover (free TTS), thumbnails/first frame optimisation, Hindi version, weekly recap, 52-week high/low list, OI/PCR data, Telegram/X cross-posting.
