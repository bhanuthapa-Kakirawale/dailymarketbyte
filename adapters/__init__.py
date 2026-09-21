"""Migration boundary between the working pipeline and the canonical domain.

Providers still live in market.py and news.py and are not moved in this phase. Adapters read
what those modules already return and rebuild the provenance their plain dicts discard, so
the canonical layer can exist before any provider is rewritten.
"""
from .market_adapter import MarketAdapter
from .news_adapter import NewsAdapter
from .report_builder import build_and_save_report, build_premarket_report, facts_from, save_report

__all__ = ["MarketAdapter", "NewsAdapter", "build_premarket_report",
           "build_and_save_report", "save_report", "facts_from"]
