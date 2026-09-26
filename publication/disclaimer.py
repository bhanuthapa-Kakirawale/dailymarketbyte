"""The standard disclaimer - informational only, NEVER the compliance system.

The publication gate and the audit must pass with these lines removed; conversely a line that is
not EXACTLY one of these registered strings gets no exemption from the language scan (a
"not advice" sentence that also says "buy" is still blocked). The exemption exists only because
the disclaimer necessarily names the words it disclaims.
"""
from __future__ import annotations

ON_SCREEN = "For information only - not investment advice"

DESCRIPTION = ("Disclaimer: Educational market information only. Not investment advice or a "
               "research recommendation. No buy/sell/hold call, target or stop-loss is provided. "
               "The creator is not a SEBI-registered investment adviser or research analyst. "
               "Please do your own research or consult a registered adviser.")

REGISTERED = (ON_SCREEN, DESCRIPTION)


def strip_registered(text: str) -> str:
    """The text with every registered disclaimer string removed (exact match only)."""
    out = text or ""
    for d in REGISTERED:
        out = out.replace(d, "")
    return out


__all__ = ["ON_SCREEN", "DESCRIPTION", "REGISTERED", "strip_registered"]
