"""SQLite schema for the local raw-OHLCV cache (`market_ohlcv.db`).

A deliberately separate database from `market_history.db` (`storage/schema.py`) - see
docs/OHLCV_STORE.md for why canonical report history and mutable raw market data must never
share a file.
"""
from __future__ import annotations

SCHEMA_VERSION = 1

# symbol/session_date/source is the natural key: the same session can legitimately have one
# row per provider (Yahoo, NSE, ...), and a later retrieval for the same key UPSERTs in place
# rather than creating a duplicate row - see OHLCVStore.upsert_bars.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_ohlcv (
    symbol         TEXT NOT NULL,
    session_date   TEXT NOT NULL,
    source         TEXT NOT NULL,
    open           REAL,
    high           REAL,
    low            REAL,
    close          REAL,
    volume         REAL,
    retrieved_at   TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date, source)
);

CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_symbol_date ON daily_ohlcv (symbol, session_date);
CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_date ON daily_ohlcv (session_date);
"""

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION"]
