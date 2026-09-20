# Daily Byte — automated ~75s Indian market recap Short

Every trading day at 7:40 AM IST (so it's live around 8 AM), a free GitHub Actions job:
1. pulls the previous NSE session and today's pre-market global cues,
2. cross-checks Nifty's close against a second source (aborts rather than post wrong numbers),
3. uses the **free Google Gemini API** with Google Search to explain the moves and list today's events,
4. renders an animated 1080×1920 Short with rolling captions and light music,
5. uploads it to your YouTube channel.

No paid services needed.

## Video timeline (75 s)

| Time | Scene |
|---|---|
| 0–2s | Hook intro: "Nifty +0.48% · FIIs sold ₹1,240 cr · Metal led" |
| 2–9s | Global cues: GIFT Nifty, Dow, Nasdaq, Brent, USD/INR, Gold (tiles count up) |
| 9–14s | FII / DII provisional cash flows (bars grow) |
| 14–30s | Nifty chart: candles draw in, support/resistance/trendline sweep in, slow zoom, 6 captions |
| 30–38s | Sector heatmap: 12 Nifty sectoral indices, best/worst highlighted |
| 38–51.5s | Top 5 gainers with news reason for each |
| 51.5–65s | Top 5 losers with news reason for each |
| 65–72s | Events today |
| 72–75s | Subscribe |

Always on screen: today's date, a scrolling ticker strip, a progress bar, and "For information only - not investment advice". The background drifts with floating particles so no frame is ever still. If a data source fails (e.g. FII/DII), that scene is dropped and Nifty/gainers/losers stretch to keep 75 s.

## Step 1 — test on your PC

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py --demo              # sample data, clearly marked "DEMO DATA - NOT REAL"
python main.py                     # REAL data, no upload -> check output/ folder
```

Always compare the first real video against Moneycontrol / NSE before turning on auto-upload.

## Step 2 — free Gemini key

Go to https://aistudio.google.com/apikey → Create API key. For local runs:
`export GEMINI_API_KEY=...` (Windows: `set GEMINI_API_KEY=...`).
The pipeline makes 3 Gemini requests per day, far below free-tier limits. If Google renames the model, set `GEMINI_MODEL` (check the model list in AI Studio). Without a key the video still builds using Google News headlines, but GIFT Nifty is hidden and FII/DII only appears if NSE's site responds.

## Step 3 — YouTube upload access (one time)

1. Google Cloud Console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add your Gmail as test user → then click **Publish app** ("In production"). Important: in "Testing" status Google expires the login after 7 days and uploads stop.
3. Credentials → Create OAuth client ID → **Desktop app** → download JSON as `client_secret.json` into this folder.
4. Run `python upload.py --auth` on your PC and sign in with the channel account. This creates `token.json`.

Until your project passes YouTube's (free) API audit, uploaded videos are forced to **private**; publish them in YouTube Studio or apply for the audit.

## Step 4 — schedule on GitHub Actions (runs even when your PC is off)

1. Create a **private** GitHub repo and push this folder (the `.gitignore` keeps your keys out).
2. Repo → Settings → Secrets and variables → Actions → add:
   - `GEMINI_API_KEY` — your Gemini key
   - `YT_CLIENT_SECRET_JSON` — full contents of `client_secret.json`
   - `YT_TOKEN_JSON` — full contents of `token.json`
3. Actions tab → "Daily Byte" → **Run workflow** once to test. Each run's video + metadata is also saved as a downloadable artifact.

The schedule is `10 2 * * 1-5` (02:10 UTC = 07:40 IST, Mon–Fri). GitHub sometimes starts scheduled jobs 5–20 minutes late; the run itself takes ~4–6 minutes. Monday's video covers Friday. If yesterday was a market holiday, the run skips automatically.

Note: GitHub disables scheduled workflows in repos with no commits for 60 days — just re-enable it from the Actions tab (or push any small change) if that happens.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | – | Free AI for reasons, events, GIFT Nifty, cross-check |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `DAILY_BYTE_UNIVERSE` | `NIFTY100` | Stocks scanned: NIFTY50 / NIFTY100 / NIFTY200 / NIFTY500 |
| `NIFTY_EXPIRY_WEEKDAY` | `1` (Tue) | Weekday of Nifty F&O expiry |
| `YT_PRIVACY` | `public` | public / unlisted / private |

Custom font: drop e.g. `Montserrat-Bold.ttf` + `Montserrat-Regular.ttf` into `assets/fonts/`. Custom music: put a royalty-free track at `assets/music.mp3` (otherwise a copyright-free lo-fi track is generated).

## Data sources and safeguards

| Data | Primary | Fallback |
|---|---|---|
| Nifty, stocks, sectors | Yahoo Finance (yfinance) | – |
| Sector indices | NSE website | Yahoo Finance |
| Nifty cross-check | NSE website | Gemini search (must match within 0.2% or the run stops) |
| FII/DII | NSE website | Gemini search (date must match, sane range) — else scene dropped |
| GIFT Nifty | Gemini search (today's date, within 5% of Nifty) | tile hidden |
| Global cues | Yahoo Finance | tile hidden |
| Stock reasons | Gemini reading Google News + search | top Google News headline |

NSE's website often blocks cloud servers, so on GitHub the fallbacks are used more often. AI-supplied numbers are only shown after passing date and range checks, but treat them as provisional.

## Technicals

Support/resistance: 4-bar swing highs/lows over 100 sessions clustered within 0.6%; nearest two above/below the close. Trendline: last 60 sessions — rising line under swing lows in an uptrend, falling line over swing highs in a downtrend. Pivot/R1/S1 are classic floor pivots. Mechanical context, not signals.

## Files

`main.py` orchestrates · `market.py` data + technicals · `news.py` Google News + Gemini · `chart.py` 3-layer animated chart · `video.py` animated renderer · `music.py` generated music · `upload.py` YouTube.
