"""Local raw-OHLCV cache: `market_ohlcv.db`. All SQL for that database lives in this module.

This is a pure store - it knows how to persist and query `OHLCVBar` rows. It does not decide
which provider to call, does not implement caching/read-through policy, and is not read by any
Radar calculation yet (Phase 4.2 Packet 5.3). See docs/OHLCV_STORE.md.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from dataclasses import dataclass, field

from .ohlcv_migrations import OhlcvSchemaVersionError, current_version, initialise
from .ohlcv_models import OHLCVBar, QualityStatus

DEFAULT_DB_RELPATH = os.path.join("data", "market_ohlcv.db")


def default_db_path(out_dir: str) -> str:
    return os.path.join(out_dir, DEFAULT_DB_RELPATH)


def _iso_date(value) -> str:
    return value.isoformat() if isinstance(value, dt.date) else str(value)


def _iso_dt(value) -> str:
    return value.isoformat() if isinstance(value, dt.datetime) else str(value)


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _parse_dt(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


@dataclass
class OHLCVRow:
    """A stored bar as read back - identical fields to `OHLCVBar`, kept as its own type so a
    query result is never confused with the frozen, not-yet-persisted `OHLCVBar` a caller
    builds before writing."""
    symbol: str
    session_date: dt.date
    source: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    retrieved_at: dt.datetime
    quality_status: QualityStatus


class OHLCVStore:
    """Repository over `daily_ohlcv`. Open one, use it, close it (or use as a context manager)."""

    def __init__(self, db_path: str, timeout: float = 30.0):
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=timeout)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        self.schema_version = initialise(self.conn)
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ writing
    def upsert_bars(self, bars) -> int:
        """Idempotent write for (symbol, session_date, source).

        A later, more complete call for the same key REPLACES that provider's row in place
        (e.g. a `BACKFILL_PENDING` reading corrected once Yahoo backfills Close). A different
        `source` for the same symbol/session is a different row and is never touched - see
        docs/OHLCV_STORE.md's cross-provider example. Returns the number of bars written.
        """
        bars = list(bars)
        if not bars:
            return 0
        with self.conn:
            for bar in bars:
                self.conn.execute(
                    """INSERT INTO daily_ohlcv (symbol, session_date, source, open, high, low,
                                                close, volume, retrieved_at, quality_status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol, session_date, source) DO UPDATE SET
                           open=excluded.open, high=excluded.high, low=excluded.low,
                           close=excluded.close, volume=excluded.volume,
                           retrieved_at=excluded.retrieved_at,
                           quality_status=excluded.quality_status""",
                    (bar.symbol, _iso_date(bar.session_date), bar.source, bar.open, bar.high,
                     bar.low, bar.close, bar.volume, _iso_dt(bar.retrieved_at),
                     bar.quality_status.value if isinstance(bar.quality_status, QualityStatus)
                     else bar.quality_status))
        return len(bars)

    # ------------------------------------------------------------------ reading
    def get_range(self, symbols, start_date, end_date, source: str | None = None) -> list:
        """Bars for `symbols` with `start_date <= session_date <= end_date`, oldest first."""
        symbols = list(symbols)
        if not symbols:
            return []
        sql = (f"SELECT * FROM daily_ohlcv WHERE symbol IN ({','.join('?' * len(symbols))}) "
               "AND session_date >= ? AND session_date <= ?")
        params: list = list(symbols) + [_iso_date(start_date), _iso_date(end_date)]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY symbol, session_date"
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def latest_session(self, symbol: str, source: str | None = None) -> OHLCVRow | None:
        sql = "SELECT * FROM daily_ohlcv WHERE symbol = ?"
        params: list = [symbol]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY session_date DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return self._row(row) if row else None

    def count_rows(self, symbol: str | None = None, source: str | None = None) -> int:
        sql = "SELECT count(*) FROM daily_ohlcv WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if source:
            sql += " AND source = ?"
            params.append(source)
        return int(self.conn.execute(sql, params).fetchone()[0])

    @staticmethod
    def _row(row) -> OHLCVRow:
        return OHLCVRow(
            symbol=row["symbol"], session_date=_parse_date(row["session_date"]),
            source=row["source"], open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], volume=row["volume"],
            retrieved_at=_parse_dt(row["retrieved_at"]),
            quality_status=QualityStatus(row["quality_status"]))


__all__ = ["OHLCVStore", "OHLCVRow", "default_db_path", "DEFAULT_DB_RELPATH",
           "OhlcvSchemaVersionError", "current_version"]
