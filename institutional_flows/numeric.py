"""Shared numeric text parsing for CDSL/NSDL table cells - Indian-grouped or plain, signed,
optionally parenthesised negatives. Returns None (never 0) for anything that is not a number,
so a blank/placeholder cell can never be silently treated as a real zero flow."""
from __future__ import annotations

import re

_NUM_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$")


def parse_number(text: str) -> float | None:
    t = (text or "").strip().replace("–", "-").replace("−", "-")
    if not t or not _NUM_RE.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


__all__ = ["parse_number"]
