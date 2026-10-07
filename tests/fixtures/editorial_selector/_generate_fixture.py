"""PK-B: one-off generator for `ohlcv_60_session_2026-09-21.csv`.

NOT run automatically by tests or by anything in the pytest collection path (this file's name
does not match `test_*.py`/`*_test.py`, and it is never imported by a test module). It is kept
here, tracked, purely for provenance: this is exactly how the frozen fixture below was produced,
so a future rebuild (e.g. extending the window) can be done the same way rather than guessed at.

Reads the REAL local `output/data/market_ohlcv.db` (year-long local accumulation from running
the pipeline, untracked, never committed) and writes the slice
`tests/test_editorial_selector_equivalence.py`'s 60-session dataset actually needs: every
symbol present in that store, every row with `session_date <= 2026-09-21` (the pinned
`editorial_policy_validation_60.END_SESSION`). No upper bound on how far back a symbol's data
goes, since the earliest real data (2026-03-23 for the stock universe) already sits comfortably
above the 55-trading-session lookback any detector requires before the first of the 60 selected
sessions (2026-06-29) - see the PK-B plan for the lookback trace
(`radar/ohlcv_service.py:REQUIRED_LOOKBACK_SESSIONS`).

Run manually, once, from the repo root:
    python tests/fixtures/editorial_selector/_generate_fixture.py

Provenance (fixture hygiene, docs/TESTING_GUIDE.md):
    source:    this developer workspace's output/data/market_ohlcv.db (real Yahoo OHLCV,
               accumulated by running `main.py`/the Radar pipeline locally)
    frozen on: 2026-10-07
    real or synthetic: REAL recorded market data (prices/volumes), not synthetic
    safe to commit: yes - no credentials, no account data, no PII; plain OHLCV bars and
               symbol names, the same shape already written to docs/examples elsewhere
    size: ~26.5k rows, ~208 symbols (207 NIFTY200 constituents + ^NSEI), 2026-03-23..2026-09-21
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
REAL_DB = os.path.join(REPO_ROOT, "output", "data", "market_ohlcv.db")
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ohlcv_60_session_2026-09-21.csv")
END_SESSION = "2026-09-21"  # radar.editorial_policy_validation_60.END_SESSION, pinned
FIELDS = ["symbol", "session_date", "source", "open", "high", "low", "close", "volume",
          "retrieved_at", "quality_status"]


def main() -> int:
    if not os.path.exists(REAL_DB):
        print(f"real db not found at {REAL_DB} - nothing to extract from; this script only "
              "runs in a dev workspace that has accumulated local OHLCV history", file=sys.stderr)
        return 1

    conn = sqlite3.connect(REAL_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {', '.join(FIELDS)} FROM daily_ohlcv WHERE session_date <= ? "
        "ORDER BY symbol, session_date", (END_SESSION,)).fetchall()
    conn.close()

    if not rows:
        print("no rows found <= END_SESSION - is the real db populated?", file=sys.stderr)
        return 1

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for r in rows:
            writer.writerow([r[f] for f in FIELDS])

    symbols = {r["symbol"] for r in rows}
    print(f"wrote {len(rows)} rows, {len(symbols)} symbols, to {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
