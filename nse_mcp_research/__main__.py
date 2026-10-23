"""NSE official MCP feasibility probe (research only) -> output/nse_mcp_research/<run>/.

    python -m nse_mcp_research tools                       # initialize + tools/list, both servers
    python -m nse_mcp_research probe --session 2026-10-22  # the bounded comparison sample

docs/NSE_MCP_FEASIBILITY.md. Manual, never scheduled, never imported by the pipeline. Reads
DMB's OHLCV store only from a byte copy (`storage.readonly.copy_database`), never writes to
`output/data/` or any production store, and writes only derived comparison results (deltas,
counts, timings) - not NSE's raw values - under its own output folder.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

from . import client as C
from .compare import compare_symbol, nse_rows_by_date

# ~10 equities: large / mid caps, different sectors, varied recent behaviour, plus an `&`
# symbol (M&MFIN) to exercise symbol mapping.
SAMPLE = ("RELIANCE", "HDFCBANK", "INFY", "ITC", "MARUTI", "SUNPHARMA", "POLYCAB", "MAZDOCK",
          "IDEA", "HINDCOPPER", "M&MFIN")
PAUSE_S = 0.5                 # polite spacing between calls; never a load test
BHAV_BUDGET = 45
CM_BUDGET = 10


def _out_dir(root: str | None) -> str:
    import config
    base = root or os.path.join(config.OUT_DIR, "nse_mcp_research")
    path = os.path.join(base, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    os.makedirs(path, exist_ok=True)
    return path


def _store_rows(db_copy: str, symbol: str, start: str, end: str) -> dict:
    con = sqlite3.connect(db_copy)
    try:
        rows = con.execute(
            "SELECT session_date, open, high, low, close, volume FROM daily_ohlcv "
            "WHERE symbol=? AND source='yahoo' AND quality_status='OK' "
            "AND session_date BETWEEN ? AND ?", (symbol, start, end)).fetchall()
    finally:
        con.close()
    return {d: {"open": o, "high": h, "low": l, "close": c, "volume": v}
            for d, o, h, l, c, v in rows}


def _call(cl, name, args, log):
    time.sleep(PAUSE_S)
    t0 = time.monotonic()
    try:
        r = cl.call_tool(name, args)
        log.append({"tool": name, "args": args, "ok": not r["is_error"],
                    "elapsed_s": round(time.monotonic() - t0, 3)})
        return r
    except C.McpResearchError as exc:
        log.append({"tool": name, "args": args, "ok": False, "error": str(exc)[:200],
                    "elapsed_s": round(time.monotonic() - t0, 3)})
        return None


def _keys(obj, depth=0):
    """The shape of a response (keys and list lengths), never its values."""
    if isinstance(obj, dict) and depth < 3:
        return {k: _keys(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return f"list[{len(obj)}]" + (f" of {_keys(obj[0], depth + 1)}" if obj and depth < 3 else "")
    return type(obj).__name__


def cmd_tools(out: str) -> dict:
    report = {}
    for ep in (C.BHAVCOPY_CM, C.CM_MARKET):
        cl = C.McpResearchClient(ep, max_requests=8)
        try:
            info = cl.initialize()
            tools = cl.list_tools()
            report[ep] = {"server": info, "tool_count": len(tools),
                          "tools": [{"name": t["name"],
                                     "required": t["inputSchema"].get("required"),
                                     "annotations": t["annotations"]} for t in tools]}
        except C.McpResearchError as exc:
            report[ep] = {"error": str(exc)}
        report[ep]["calls"] = [vars(c) for c in cl.calls]
    return report


def cmd_probe(session: dt.date, out: str) -> dict:
    from storage.readonly import copy_database
    import config
    src = os.path.join(config.OUT_DIR, "data", "market_ohlcv.db")
    db_copy = os.path.join(out, "ohlcv_copy", "market_ohlcv.db")
    copy_database(src, db_copy)
    start = (session - dt.timedelta(days=31)).isoformat()
    log, report = [], {"session": session.isoformat(), "sample": list(SAMPLE)}

    bh = C.McpResearchClient(C.BHAVCOPY_CM, max_requests=BHAV_BUDGET)
    bh.initialize()
    bh.list_tools()
    per_symbol = {}
    for sym in SAMPLE:
        r = _call(bh, "get_stock_history", {"symbol": sym, "months": 1,
                                            "endDate": session.isoformat()}, log)
        ca = _call(bh, "get_corporate_actions", {"symbol": sym, "fromDate": start,
                                                 "toDate": session.isoformat()}, log)
        if r is None or r["is_error"] or not isinstance(r["structured"], dict):
            per_symbol[sym] = {"verdict": "NSE_UNAVAILABLE",
                               "text": (r or {}).get("text", "")[:200]}
            continue
        nse = nse_rows_by_date(r["structured"])
        store = _store_rows(db_copy, sym, min(nse) if nse else start, session.isoformat())
        cmp_ = compare_symbol(nse, store)
        cmp_["nse_source_field"] = r["structured"].get("source")
        cmp_["nse_window"] = [r["structured"].get("from_date"), r["structured"].get("to_date")]
        cmp_["latest_nse_date"] = max(nse) if nse else None
        cas = (ca or {}).get("structured") if ca else None
        cmp_["corporate_actions"] = (
            [{"exDate": e.get("exDate"), "actionType": e.get("actionType")}
             for e in (cas.get("actions") or [])]
            if isinstance(cas, dict) else None)
        cmp_["corporate_actions_shape"] = _keys(cas) if cas is not None else None
        per_symbol[sym] = cmp_
    report["symbols"] = per_symbol

    # Breadth / index / bulk: shapes + dates only (session alignment), never re-published.
    probes = {}
    for name, args in (("get_market_breadth", {"date": session.isoformat()}),
                       ("get_market_breadth", {"date": "2026-10-20"}),          # NSE holiday
                       ("get_index_snapshot", {"date": session.isoformat(), "filter": "nifty 50"}),
                       ("get_bulk_quote", {"symbols": list(SAMPLE)})):
        r = _call(bh, name, args, log)
        s = (r or {}).get("structured")
        probes[f"{name}:{json.dumps(args)[:60]}"] = {
            "ok": bool(r) and not r["is_error"], "shape": _keys(s) if s is not None else None,
            "dates_returned": sorted({v for k, v in _walk(s) if k in ("date", "tradeDate")})[:12]}
    # History depth: the documented window is "latest 5 years".
    for end in ("2021-11-30", "2021-09-30"):
        r = _call(bh, "get_stock_history", {"symbol": "RELIANCE", "months": 1, "endDate": end}, log)
        s = (r or {}).get("structured")
        rows = nse_rows_by_date(s) if isinstance(s, dict) else {}
        probes[f"depth:{end}"] = {"ok": bool(r) and not r["is_error"], "rows": len(rows),
                                  "first": min(rows) if rows else None,
                                  "text": "" if rows else (r or {}).get("text", "")[:200]}
    report["bhavcopy_probes"] = probes
    report["bhavcopy_calls"] = [vars(c) for c in bh.calls]

    cm = C.McpResearchClient(C.CM_MARKET, max_requests=CM_BUDGET)
    cm_rep = {}
    try:
        cm.initialize()
        cm.list_tools()
        for name, args in (("cm_get_data_status", {}), ("cm_get_allstocks_status", {}),
                           ("cm_get_stock_quote", {"symbol": "RELIANCE"})):
            r = _call(cm, name, args, log)
            s = (r or {}).get("structured")
            cm_rep[name] = {"ok": bool(r) and not r["is_error"], "shape": _keys(s),
                            "timestamps": {k: v for k, v in _walk(s)
                                           if isinstance(v, str) and any(
                                               t in k.lower() for t in ("time", "crawl", "date",
                                                                        "updated", "asof"))}}
    except C.McpResearchError as exc:
        cm_rep["error"] = str(exc)
    report["cm_market"] = cm_rep
    report["cm_calls"] = [vars(c) for c in cm.calls]
    report["tool_log"] = log
    os.remove(db_copy)
    return report


def _walk(obj, depth=0):
    if depth > 4:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:60]:
            yield from _walk(v, depth + 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nse_mcp_research")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools")
    p = sub.add_parser("probe")
    p.add_argument("--session", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = _out_dir(a.out)
    rep = cmd_tools(out) if a.cmd == "tools" else cmd_probe(a.session, out)
    path = os.path.join(out, f"{a.cmd}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, ensure_ascii=False, default=str)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
