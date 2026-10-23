"""Pure comparison of NSE MCP bhavcopy rows against DMB's stored Yahoo OHLCV (research only).

No I/O, no network. Inputs are plain dicts; outputs are deltas and classifications, so a
caller can report the comparison without re-publishing NSE's values.
"""
from __future__ import annotations

PRICE_FIELDS = ("open", "high", "low", "close")
PRICE_ABS_TOL = 0.05          # NSE tick size for most equities; Yahoo stores float32-ish values
VOLUME_REL_TOL = 0.01         # 1%: Yahoo and NSE volume definitions are not guaranteed equal
RATIO_STABLE_TOL = 0.002      # a constant close ratio != 1 across days = an adjustment factor


def nse_rows_by_date(structured: dict) -> dict:
    """`{date: row}` for the EQ-series rows of one `get_stock_history` result. Rows without
    a date, or of another series (e.g. BE/BZ after a series change), are dropped, never
    guessed into EQ."""
    out = {}
    for row in (structured or {}).get("data") or []:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        if row.get("series") not in (None, "EQ"):
            continue
        out[row["date"]] = row
    return out


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def compare_symbol(nse: dict, store: dict) -> dict:
    """`nse` / `store`: `{date: {"open","high","low","close","volume"}}`.
    Returns per-field match counts, worst deltas, coverage differences and an adjustment
    check (is NSE/Yahoo close a constant ratio != 1, i.e. split/bonus-adjusted vs raw)."""
    both = sorted(set(nse) & set(store))
    result = {"sessions_nse": len(nse), "sessions_store": len(store), "sessions_both": len(both),
              "only_in_nse": sorted(set(nse) - set(store)),
              "only_in_store": sorted(set(store) - set(nse)),
              "fields": {}, "mismatches": []}
    for f in PRICE_FIELDS + ("volume",):
        match = worst = 0
        worst_abs = 0.0
        for d in both:
            a, b = _num(nse[d].get(f)), _num(store[d].get(f))
            if a is None or b is None:
                continue
            diff = abs(a - b)
            if f == "volume":
                rel = diff / a if a else (0.0 if b == 0 else 1.0)
                ok = rel <= VOLUME_REL_TOL
                worst = max(worst, rel)
            else:
                ok = diff <= PRICE_ABS_TOL
                worst_abs = max(worst_abs, diff)
            match += ok
            if not ok:
                result["mismatches"].append({"date": d, "field": f, "abs_delta": round(diff, 4),
                                             "rel_delta": round(diff / a, 6) if a else None})
        entry = {"compared": len(both), "matched": match}
        entry["worst_rel_delta" if f == "volume" else "worst_abs_delta"] = (
            round(worst, 6) if f == "volume" else round(worst_abs, 4))
        result["fields"][f] = entry
    ratios = [(_num(store[d].get("close")) or 0) / _num(nse[d].get("close"))
              for d in both if _num(nse[d].get("close"))]
    if ratios:
        lo, hi = min(ratios), max(ratios)
        result["close_ratio_range"] = [round(lo, 6), round(hi, 6)]
        result["adjustment_suspected"] = (abs(lo - 1) > RATIO_STABLE_TOL
                                          and (hi - lo) <= RATIO_STABLE_TOL)
    else:
        result["close_ratio_range"], result["adjustment_suspected"] = None, False
    result["adjustment_step"] = adjustment_step(nse, store)
    result["verdict"] = classify(result)
    return result


def adjustment_step(nse: dict, store: dict) -> dict | None:
    """A split/bonus seen as a STEP: before some date the Yahoo/NSE close ratio is a constant
    factor != 1 (Yahoo back-adjusted, NSE raw), from that date on it is 1. Returns
    `{"first_unadjusted_date", "factor"}` (factor = Yahoo/NSE before the step, e.g. 0.5 for a
    1:1 bonus) or None. Detection only - it says the two series differ by a corporate action,
    never which ex-date is right; NSE's own `get_corporate_actions` is the confirmation."""
    days = sorted(d for d in set(nse) & set(store)
                  if _num(nse[d].get("close")) and _num(store[d].get("close")))
    ratio = {d: _num(store[d]["close"]) / _num(nse[d]["close"]) for d in days}
    for i in range(1, len(days)):
        pre, post = [ratio[d] for d in days[:i]], [ratio[d] for d in days[i:]]
        if (all(abs(r - 1) <= RATIO_STABLE_TOL for r in post)
                and abs(pre[0] - 1) > RATIO_STABLE_TOL
                and max(pre) - min(pre) <= RATIO_STABLE_TOL):
            return {"first_unadjusted_date": days[i], "factor": round(sum(pre) / len(pre), 6)}
    return None


def classify(r: dict) -> str:
    """MATCH / VOLUME_ONLY_DIFF / ADJUSTMENT_DIFF / ADJUSTMENT_STEP / PRICE_MISMATCH /
    NO_OVERLAP."""
    if not r["sessions_both"]:
        return "NO_OVERLAP"
    if r.get("adjustment_suspected"):
        return "ADJUSTMENT_DIFF"
    if r.get("adjustment_step"):
        return "ADJUSTMENT_STEP"
    prices_ok = all(r["fields"][f]["matched"] == r["fields"][f]["compared"] for f in PRICE_FIELDS)
    vol = r["fields"]["volume"]
    if prices_ok:
        return "MATCH" if vol["matched"] == vol["compared"] else "VOLUME_ONLY_DIFF"
    return "PRICE_MISMATCH"
