# Private Trading Intelligence Desk — roadmap note (NOT built)

This is a future, local-only web application for the owner. It would turn PRIVATE_ANALYTICS
into a personal decision-support dashboard. It is **decision support and informational edge
from systematic analytics**: no promise of returns, no automated trading, and nothing from it is
ever published.

## Principles

- **Local only.** It binds to localhost and is never deployed publicly. It has no upload path
  and is entirely separate from the YouTube product.
- **Reuse, don't recompute.** It reads the existing stores (docs/PRIVATE_INTELLIGENCE.md)
  through the existing repositories and the Radar / Market Structure / intelligence modules.
  It never runs a second implementation of a detector, a relative-volume definition or a
  sector mapping.
- **Same data discipline.** Session-aligned dates, the canonical trading calendar, provenance
  on every number, and "unavailable" rather than guessed.

## Candidate screens

1. **Market regime:** breadth (advances/declines over NIFTY 200), volatility (India VIX), sector
   strength, bullish / bearish / neutral diagnostics with the rule behind each label.
2. **Radar:** ranked private candidates with the detector reason, state changes, freshness and
   historical appearances (`radar_candidate_history`).
3. **Stock intelligence:** for one stock, the OHLCV chart, relative volume, technical structure,
   relative performance, sector context, multi-timeframe view, state changes, historical Radar
   signals, and official events (Exchange Watch snapshots) where available.
4. **Sector intelligence:** breadth, unusual-volume concentration, sector-relative strength.
5. **Watchlist:** owner-defined symbols plus Radar candidates, with state-change alerts.
6. **Position / risk desk (later):** position context, risk sizing, scenario analysis, a
   stop/target framework, options overlays. These are owner-side tools, never public content.

## Possible stack (later)

- FastAPI backend (read-only over the SQLite stores at first).
- Local web UI with Plotly or a lightweight charting library.
- SQLite now. Whether it needs anything else is a question for when it exists.

## Out of scope until a decision

Implementation, any live broker integration, alerts leaving the machine.
