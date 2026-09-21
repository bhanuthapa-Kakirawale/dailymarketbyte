"""Canonical enums for the market-intelligence layer.

These names are part of the report JSON contract: they are serialised by value, so
renaming a member is a breaking change to every archived report. Add members freely;
rename or remove them only with a schema version bump.
"""
from enum import Enum


class SourceType(str, Enum):
    """Where an observation came from, ranked by how much independent weight it carries.

    PRIMARY   - the exchange/regulator that actually produced the number (NSE, BSE, RBI).
    SECONDARY - an established redistributor (Yahoo Finance, a news agency's data desk).
    BROKER    - broker/API feed. Accurate, but publication rights usually forbid display.
    NEWS      - a news article. Fine for narrative, weak for numbers.
    AI        - an LLM, including one using web search. Never verification on its own.
    DERIVED   - computed by this pipeline from other observations (EMA, RSI, pivots).
    """
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    BROKER = "BROKER"
    NEWS = "NEWS"
    AI = "AI"
    DERIVED = "DERIVED"


class ValidationStatus(str, Enum):
    """Outcome of validating a fact (or a single observation within it).

    UNVALIDATED   - no validator has run yet. Never publishable.
    VERIFIED      - corroborated by at least two acceptable independent sources
                    that agree inside tolerance, at least one of them non-AI.
    SINGLE_SOURCE - exactly one acceptable non-AI source. Usable, but uncorroborated.
    PROVISIONAL   - only AI-sourced observations back a critical numeric fact, or the
                    source itself labels the number provisional. Usable with care.
    CONFLICT      - two or more sources disagree beyond tolerance. Blocks publication.
    STALE         - the value is real but too old for the session being reported.
    MISSING       - no usable observation exists.
    REJECTED      - an observation failed a hard check (wrong session date, impossible range).
    """
    UNVALIDATED = "UNVALIDATED"
    VERIFIED = "VERIFIED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    PROVISIONAL = "PROVISIONAL"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    MISSING = "MISSING"
    REJECTED = "REJECTED"


class ReportType(str, Enum):
    """PRE_MARKET is the only edition implemented. POST_MARKET exists so archived
    reports and downstream consumers already carry the discriminator when the 6 PM
    edition is built; nothing in this phase produces one."""
    PRE_MARKET = "PRE_MARKET"
    POST_MARKET = "POST_MARKET"


class Metric(str, Enum):
    """What is being measured. Instrument says which thing it was measured on."""
    INDEX_CLOSE = "INDEX_CLOSE"
    INDEX_CHANGE_PCT = "INDEX_CHANGE_PCT"
    INDEX_LEVEL = "INDEX_LEVEL"            # a live/indicative level, e.g. GIFT Nifty
    STOCK_CLOSE = "STOCK_CLOSE"
    STOCK_CHANGE_PCT = "STOCK_CHANGE_PCT"
    STOCK_RELATIVE_VOLUME = "STOCK_RELATIVE_VOLUME"
    SECTOR_CHANGE_PCT = "SECTOR_CHANGE_PCT"
    FII_NET_CASH = "FII_NET_CASH"
    DII_NET_CASH = "DII_NET_CASH"
    VOLATILITY_INDEX = "VOLATILITY_INDEX"
    COMMODITY_PRICE = "COMMODITY_PRICE"
    FX_RATE = "FX_RATE"
    TECHNICAL_LEVEL = "TECHNICAL_LEVEL"


# Numeric facts a viewer could act on, where being wrong is materially misleading.
# An AI-only observation can never mark one of these VERIFIED (see validation.py).
CRITICAL_METRICS = frozenset({
    Metric.INDEX_CLOSE,
    Metric.INDEX_CHANGE_PCT,
    Metric.INDEX_LEVEL,
    Metric.STOCK_CLOSE,
    Metric.STOCK_CHANGE_PCT,
    Metric.SECTOR_CHANGE_PCT,
    Metric.FII_NET_CASH,
    Metric.DII_NET_CASH,
    Metric.COMMODITY_PRICE,
    Metric.FX_RATE,
})

# Facts without which a pre-market report is not publishable at all.
REQUIRED_METRICS = frozenset({
    Metric.INDEX_CLOSE,
    Metric.INDEX_CHANGE_PCT,
})

# Statuses a fact may carry and still be shown on screen.
PUBLISHABLE_STATUSES = frozenset({
    ValidationStatus.VERIFIED,
    ValidationStatus.SINGLE_SOURCE,
    ValidationStatus.PROVISIONAL,
})


def is_critical(metric: Metric) -> bool:
    return metric in CRITICAL_METRICS
