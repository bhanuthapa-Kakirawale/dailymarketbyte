"""Canonical market-intelligence domain: Observation -> Fact -> Validation -> MarketReport.

Nothing in this package fetches data or draws anything. It holds the shapes and the rules,
so that data acquisition (market.py, news.py) and presentation (video.py) can be replaced
independently without either one defining what a trustworthy fact is.
"""
from .content_safety import (CONTENT_SAFETY_VERSION, PublicationScan, SafetyResult,
                             SafetyStatus, classify_text, sanitize_field, scan_publication)
from .enums import (CRITICAL_METRICS, PUBLISHABLE_STATUSES, REQUIRED_METRICS, Metric,
                    ReportType, SourceType, ValidationStatus, is_critical)
from .models import (UNIT_INR, UNIT_INR_CRORE, UNIT_PERCENT, UNIT_POINTS, UNIT_RATIO,
                     UNIT_USD, Fact, Observation, ValidationResult)
from .report import REPORT_SCHEMA_VERSION, MarketReport, ValidationSummary, summarize
from .validation import (CrossSourceValidator, DateValidator, FreshnessValidator,
                         RangeValidator, RequiredFieldValidator, ValidationPolicy,
                         policy_for, preferred_source, validate_fact)

__all__ = [
    "SafetyStatus", "SafetyResult", "PublicationScan", "classify_text",
    "sanitize_field", "scan_publication", "CONTENT_SAFETY_VERSION",
    "Metric", "ReportType", "SourceType", "ValidationStatus", "is_critical",
    "CRITICAL_METRICS", "REQUIRED_METRICS", "PUBLISHABLE_STATUSES",
    "Observation", "Fact", "ValidationResult",
    "UNIT_INR", "UNIT_INR_CRORE", "UNIT_USD", "UNIT_PERCENT", "UNIT_RATIO", "UNIT_POINTS",
    "ValidationPolicy", "policy_for", "validate_fact", "preferred_source",
    "RequiredFieldValidator", "DateValidator", "FreshnessValidator",
    "RangeValidator", "CrossSourceValidator",
    "MarketReport", "ValidationSummary", "summarize", "REPORT_SCHEMA_VERSION",
]
