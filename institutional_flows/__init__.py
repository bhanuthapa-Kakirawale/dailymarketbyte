"""Institutional Flow Intelligence V1.

Three official institutional-flow sources, each kept in its OWN time semantics rather than
forced into one shared "session date":

    NSE   - provisional same-day net FII/FPI + DII cash-market flow (exchange_provisional)
    CDSL  - daily depository-reported FPI flow, dated by its own reporting date, with the
            period it actually covers recorded as the source states it (often UNKNOWN)
    NSDL  - fortnightly sector-wise FPI net investment, the latest and previous ACTUALLY
            PUBLISHED fortnight discovered from NSDL's own selection page - never hard-coded

This is a read layer alongside the existing `market.fii_dii_nse` / canonical-report FII/DII
path, not a replacement for it. See docs/INSTITUTIONAL_FLOW_INTELLIGENCE.md.
"""
