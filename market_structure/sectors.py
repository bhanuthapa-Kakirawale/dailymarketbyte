"""Deterministic sector buckets from NSE Indices' own 'Industry' classification.

The mapping is a fixed table - no model, no inference from a company's name. The display label
only shortens NSE's wording ("Fast Moving Consumer Goods" -> "FMCG"); it never re-labels a
bucket as something narrower than NSE's category (NSE's "Healthcare" is not called "Pharma":
it also contains hospitals and diagnostics). An industry this table does not know is
"Unclassified" - explicitly, never guessed.
"""
from __future__ import annotations

UNCLASSIFIED = "Unclassified"

INDUSTRY_TO_SECTOR = {
    "Financial Services": "Financials",
    "Information Technology": "IT",
    "Healthcare": "Healthcare",
    "Automobile and Auto Components": "Auto",
    "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "Oil & Gas",
    "Metals & Mining": "Metals",
    "Capital Goods": "Capital Goods",
    "Power": "Power",
    "Consumer Durables": "Consumer Durables",
    "Construction Materials": "Construction Materials",
    "Construction": "Construction",
    "Chemicals": "Chemicals",
    "Realty": "Realty",
    "Telecommunication": "Telecom",
    "Services": "Services",
    "Consumer Services": "Consumer Services",
    "Textiles": "Textiles",
    "Media Entertainment & Publication": "Media",
    "Diversified": "Diversified",
    "Forest Materials": "Forest Materials",
}
MAPPING_VERSION = "nse-industry-1.0"


def sector_for(industry: str | None) -> str:
    return INDUSTRY_TO_SECTOR.get((industry or "").strip(), UNCLASSIFIED)


__all__ = ["INDUSTRY_TO_SECTOR", "UNCLASSIFIED", "MAPPING_VERSION", "sector_for"]
