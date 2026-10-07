"""NSDL fortnightly sector label -> Daily Market Byte display sector.

NSDL classifies FPI sector flow using BSE's "Common Industry Classification" (per its own
report footnote), while Market Structure's NIFTY 200 universe is classified from NSE Indices'
own 'Industry' column (`market_structure.sectors.INDUSTRY_TO_SECTOR`). The two taxonomies use
mostly identical sector NAMES (occasionally with different punctuation, e.g. NSDL's "Oil, Gas &
Consumable Fuels" vs NSE's "Oil Gas & Consumable Fuels") but are NOT the same classification
system at the company level - this table maps NSDL's LABEL to the same display sector the NSE
taxonomy would produce for the equivalent industry name, which is label-level equivalence, not
proof that the two systems classify every individual company identically.

The table is explicit and reviewed by hand against NSDL's live report (verified 2026-10-05,
fortnight ending 15 Sep 2026: 22 named sectors + Sovereign + Others). No fuzzy matching, no
LLM assignment: an NSDL label not in this table is UNMAPPED, never guessed.
"""
from __future__ import annotations

from market_structure.sectors import INDUSTRY_TO_SECTOR

MAPPING_VERSION = "nsdl-bse-cic-1.0"

UNMAPPED = "UNMAPPED"
NOT_A_SECTOR = "NOT_A_SECTOR"

# NSDL label (verbatim, as its own "Sectors" column prints it) -> display sector.
# Most entries are NSDL's label text mapped through the same display sector NSE's Industry
# taxonomy already uses for the equivalent industry; a handful differ only in punctuation
# (comma vs no comma, "Oil, Gas" vs "Oil Gas") and are listed explicitly rather than matched.
NSDL_TO_SECTOR = {
    "Automobile and Auto Components": INDUSTRY_TO_SECTOR["Automobile and Auto Components"],
    "Capital Goods": INDUSTRY_TO_SECTOR["Capital Goods"],
    "Chemicals": INDUSTRY_TO_SECTOR["Chemicals"],
    "Construction": INDUSTRY_TO_SECTOR["Construction"],
    "Construction Materials": INDUSTRY_TO_SECTOR["Construction Materials"],
    "Consumer Durables": INDUSTRY_TO_SECTOR["Consumer Durables"],
    "Consumer Services": INDUSTRY_TO_SECTOR["Consumer Services"],
    "Diversified": INDUSTRY_TO_SECTOR["Diversified"],
    "Fast Moving Consumer Goods": INDUSTRY_TO_SECTOR["Fast Moving Consumer Goods"],
    "Financial Services": INDUSTRY_TO_SECTOR["Financial Services"],
    "Forest Materials": INDUSTRY_TO_SECTOR["Forest Materials"],
    "Healthcare": INDUSTRY_TO_SECTOR["Healthcare"],
    "Information Technology": INDUSTRY_TO_SECTOR["Information Technology"],
    "Media, Entertainment & Publication": INDUSTRY_TO_SECTOR["Media Entertainment & Publication"],
    "Metals & Mining": INDUSTRY_TO_SECTOR["Metals & Mining"],
    "Oil, Gas & Consumable Fuels": INDUSTRY_TO_SECTOR["Oil Gas & Consumable Fuels"],
    "Power": INDUSTRY_TO_SECTOR["Power"],
    "Realty": INDUSTRY_TO_SECTOR["Realty"],
    "Services": INDUSTRY_TO_SECTOR["Services"],
    "Telecommunication": INDUSTRY_TO_SECTOR["Telecommunication"],
    "Textiles": INDUSTRY_TO_SECTOR["Textiles"],
    # NSDL-only bucket with no NSE-Industry equivalent in the current taxonomy - documented
    # limitation, never silently folded into another sector.
    "Utilities": UNMAPPED,
    # rows that are not an industry sector at all
    "Sovereign": NOT_A_SECTOR,
    "Others": NOT_A_SECTOR,
    "Grand Total": NOT_A_SECTOR,
}


def sector_for_nsdl(label: str) -> str:
    """Display sector, UNMAPPED, or NOT_A_SECTOR for one NSDL 'Sectors' cell. Only whitespace is
    normalised (collapsed, trimmed) - never fuzzy matching."""
    import re
    key = re.sub(r"\s+", " ", (label or "").strip())
    return NSDL_TO_SECTOR.get(key, UNMAPPED)


__all__ = ["MAPPING_VERSION", "UNMAPPED", "NOT_A_SECTOR", "NSDL_TO_SECTOR", "sector_for_nsdl"]
