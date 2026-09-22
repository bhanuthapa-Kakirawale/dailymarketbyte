"""Presentation boundary: MarketReport in, renderer-shaped structures out.

The renderer does not acquire market data. Everything it draws arrives through here, from a
report that has already been validated, content-checked and cleared for publication.
"""
from .report_adapter import ReportPresentation

__all__ = ["ReportPresentation"]
