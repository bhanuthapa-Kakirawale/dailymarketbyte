"""Deterministic publication-safety layer: blocks recommendation-style language from
being republished as if it were this channel's own advice.

Every rule here is a plain regex. Nothing in this module calls Gemini or any other model -
the guarantee this module exists to provide ("recommendation language cannot reach the
video") must hold even when the AI layer is completely unavailable, and an LLM judging its
own output is not a safety boundary a viewer can rely on.

Three outcomes, always in this priority order:

1. BLOCKED, no rewrite attempted - phrases that are recommendation language with no
   reliable factual core to extract ("stocks to buy", "should you buy", "guaranteed
   return"). Rewriting these risks manufacturing a plausible-sounding sentence that still
   carries the recommendation; refusing to publish is the only safe option.
2. SANITIZED - an attributed third-party rating ("MOFSL initiates BUY rating with target
   Rs 1,500") is neutralized to a fixed factual template ("Brokerage initiated coverage on
   the company.") that keeps the fact (a rating event happened) and drops the direction and
   number (which would read as this channel's own call if left in).
3. BLOCKED, unattributed actionable language ("target price", "stop loss") with no
   brokerage to attribute it to and thus no safe factual rewrite.

Anything matching none of the above is SAFE and passes through unchanged. Ordinary corporate
actions - "Company buys 51% stake", "Promoter sells 2% stake" - do not match any rule: the
patterns below require "buy"/"sell" as a bare word in a recommendation phrasing, which a
word-boundary regex does not match against "buys"/"sold"/"selling".
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum

CONTENT_SAFETY_VERSION = "1.0"


class SafetyStatus(str, Enum):
    SAFE = "SAFE"
    SANITIZED = "SANITIZED"
    BLOCKED = "BLOCKED"


def _now() -> dt.datetime:
    try:
        from config import now_ist
        return now_ist()
    except Exception:
        return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass
class SafetyResult:
    """The verdict for one piece of text, and everything needed to audit it later."""
    status: SafetyStatus
    original_text: str
    sanitized_text: str | None = None
    matched_rules: list = field(default_factory=list)
    reason: str = ""
    checked_at: dt.datetime | None = None
    version: str = CONTENT_SAFETY_VERSION

    @property
    def output_text(self) -> str:
        """The text safe to publish: original when SAFE, the neutralized form when
        SANITIZED, or empty when BLOCKED (callers supply their own fallback for that case)."""
        if self.status is SafetyStatus.SAFE:
            return self.original_text
        if self.status is SafetyStatus.SANITIZED:
            return self.sanitized_text or ""
        return ""

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "original_text": self.original_text,
            "sanitized_text": self.sanitized_text,
            "matched_rules": list(self.matched_rules),
            "reason": self.reason,
            "checked_at": _iso(self.checked_at),
            "version": self.version,
        }


# --------------------------------------------------------------------- rule tables
# Word-boundary (\b) regexes only. "buy"/"sell" as bare words in a recommendation phrasing
# match; "buys"/"sold"/"buying"/"selling" used as ordinary corporate-action verbs do not,
# because \b requires an exact word boundary immediately after "buy"/"sell" and there is
# none before the trailing "s"/"ing".

# Category 1: recommendation language with no safe factual rewrite. Always BLOCKED.
_BLOCK_ALWAYS = [
    ("stocks_to_buy", re.compile(r"\bstocks?\s+to\s+buy\b", re.I)),
    ("buy_sell_tomorrow", re.compile(r"\b(?:buy|sell)\s+tomorrow\b", re.I)),
    ("should_you_trade", re.compile(r"\bshould\s+you\s+(?:buy|sell|hold)\b", re.I)),
    ("best_stock_to_buy", re.compile(r"\bbest\s+stocks?\s+to\s+buy\b", re.I)),
    ("top_picks", re.compile(r"\btop\s+(?:stock\s+)?picks?\b", re.I)),
    ("recommended_stock", re.compile(r"\brecommended\s+stocks?\b", re.I)),
    ("sure_shot", re.compile(r"\bsure[\s-]?shot\b", re.I)),
    ("guaranteed_return", re.compile(r"\bguaranteed\s+(?:\d+(?:\.\d+)?%\s*)?returns?\b", re.I)),
    ("multibagger", re.compile(r"\bmultibaggers?\b", re.I)),
    ("bare_strong_buy_sell", re.compile(r"\bstrong\s+(?:buy|sell)\b", re.I)),
    ("ideal_entry", re.compile(r"\bideal\s+entry\b", re.I)),
    ("good_accumulation_zone", re.compile(r"\baccumulation\s+zone\b", re.I)),
    ("good_buying_opportunity", re.compile(r"\bbuying\s+opportunity\b", re.I)),
]

# Category 2: an attributed third-party rating. One regex, checked as a whole because the
# neutralization needs the matched verb to phrase its replacement sentence.
#
# The optional text between verb and rating ("upgrades STOCK to BUY", "upgrades XYZ Ltd to
# BUY") is a closed whitelist, never a generic wildcard: an open ".*?" here would also match
# unrelated sentences like "upgrades its systems to buy new equipment", turning a
# corporate-action headline into a fabricated brokerage-rating sentence - a worse outcome
# than simply not matching it. The ticker branch is deliberately scoped case-sensitive
# (`(?-i:...)`, overriding the pattern's own re.I) to an ALL-CAPS token: real stock tickers
# in headlines are conventionally all-uppercase, while Title-Case headline words ("Stock",
# "Upgrades") capitalize only their first letter and so do not qualify.
_TICKER = r"(?-i:[A-Z]{2,10})"
_ATTRIBUTED_RATING = re.compile(
    r"\b(?P<verb>initiates?|initiated|maintains?|maintained|reiterates?|reiterated|"
    r"retains?|retained|assigns?|assigned|upgrades?|upgraded|downgrades?|downgraded)\b"
    r"(?:\s+(?:the\s+)?(?:stock|shares?|scrip|counter|company)"
    rf"|(?:\s+{_TICKER}){{1,3}}(?:\s+(?:Ltd\.?|Limited))?)?"
    r"\s+(?:to\s+)?(?:a\s+|an\s+)?"
    r"(?P<rating>strong\s+buy|strong\s+sell|buy|sell|hold|accumulate|reduce|add|"
    r"outperform|underperform|neutral|overweight|underweight)\s*(?:rating)?\b", re.I)

# Explicit inflected-form -> root patterns, rather than algorithmic suffix-stripping: verbs
# ending in a silent "e" (initiate, reiterate, upgrade, downgrade) take "s" for the
# third-person form, not "es" (initiates, not "initiaes"), which a generic "-es/-s" stemmer
# gets wrong. Spelling each root out explicitly is worth the verbosity to avoid that bug.
_VERB_ROOTS = [
    ("initiate", re.compile(r"^initiat(?:e|es|ed)$", re.I)),
    ("maintain", re.compile(r"^maintain(?:s|ed)?$", re.I)),
    ("reiterate", re.compile(r"^reiterat(?:e|es|ed)$", re.I)),
    ("retain", re.compile(r"^retain(?:s|ed)?$", re.I)),
    ("assign", re.compile(r"^assign(?:s|ed)?$", re.I)),
    ("upgrade", re.compile(r"^upgrad(?:e|es|ed)(?:\s+to)?$", re.I)),
    ("downgrade", re.compile(r"^downgrad(?:e|es|ed)(?:\s+to)?$", re.I)),
]

_RATING_VERB_PHRASE = {
    "initiate": "initiated coverage on", "maintain": "maintained its rating on",
    "reiterate": "reiterated its rating on", "retain": "retained its rating on",
    "assign": "assigned a rating to", "upgrade": "revised its rating on",
    "downgrade": "revised its rating on",
}


def _neutralize_rating(match: re.Match) -> str:
    verb_word = match.group("verb").strip().lower()
    root = next((r for r, pattern in _VERB_ROOTS if pattern.match(verb_word)), None)
    phrase = _RATING_VERB_PHRASE.get(root, "issued a rating on")
    return f"Brokerage {phrase} the company."


# Category 3: unattributed but actionable trading instructions. No brokerage to attribute
# to, so - unlike category 2 - there is no safe factual sentence to rewrite it into.
_BLOCK_UNATTRIBUTED = [
    ("target_price", re.compile(r"\btarget\s+price\b|\bprice\s+target\b", re.I)),
    ("target_of_value", re.compile(r"\btarget\s+(?:of\s+)?(?:rs\.?|₹|inr)\s*[\d,]+", re.I)),
    ("stop_loss", re.compile(r"\bstop[\s-]?loss\b|\bstoploss\b", re.I)),
    ("sl_with_price", re.compile(r"\bSL\b[\s:=-]{0,3}(?:rs\.?|₹|inr)?\s*\d")),
    ("bare_rating_word", re.compile(r"\b(?:buy|sell|hold|accumulate)\s+rating\b", re.I)),
]


def _first_match(rules, text: str):
    for name, pattern in rules:
        m = pattern.search(text)
        if m:
            return name, m
    return None


# --------------------------------------------------------------------- public API
def classify_text(text: str, now: dt.datetime | None = None) -> SafetyResult:
    """Classify one piece of text. Pure function, no network, no model call."""
    checked_at = now or _now()
    text = text or ""
    if not text.strip():
        return SafetyResult(SafetyStatus.SAFE, text, text, [], "", checked_at)

    hit = _first_match(_BLOCK_ALWAYS, text)
    if hit:
        name, _ = hit
        return SafetyResult(SafetyStatus.BLOCKED, text, None, [name],
                            f"recommendation-language rule '{name}' matched; no safe rewrite exists",
                            checked_at)

    m = _ATTRIBUTED_RATING.search(text)
    if m:
        sanitized = _neutralize_rating(m)
        recheck = _first_match(_BLOCK_ALWAYS, sanitized) or _first_match(_BLOCK_UNATTRIBUTED, sanitized)
        if recheck:            # defensive: the fixed template should never trip a rule
            return SafetyResult(SafetyStatus.BLOCKED, text, None, ["attributed_rating", recheck[0]],
                                "sanitized output still matched a block rule; discarding", checked_at)
        return SafetyResult(SafetyStatus.SANITIZED, text, sanitized, ["attributed_rating"],
                            "attributed third-party rating neutralized to a factual statement",
                            checked_at)

    hit = _first_match(_BLOCK_UNATTRIBUTED, text)
    if hit:
        name, _ = hit
        return SafetyResult(SafetyStatus.BLOCKED, text, None, [name],
                            f"recommendation-language rule '{name}' matched with no attribution "
                            "to safely rewrite from", checked_at)

    return SafetyResult(SafetyStatus.SAFE, text, text, [], "", checked_at)


def sanitize_field(text: str, fallback: str = "", now: dt.datetime | None = None) -> tuple[str, SafetyResult]:
    """Text safe to publish for one field, plus the classification that produced it.

    SAFE -> the text unchanged. SANITIZED -> the neutralized text. BLOCKED -> `fallback`
    (the caller's neutral placeholder - e.g. the existing "no verified catalyst" text for a
    mover reason, or "" to drop an event/caption line entirely).
    """
    result = classify_text(text, now=now)
    if result.status is SafetyStatus.SAFE:
        return text, result
    if result.status is SafetyStatus.SANITIZED:
        return result.sanitized_text, result
    return fallback, result


@dataclass
class PublicationScan:
    """Result of scanning every finalized public-facing text artifact immediately before
    publication. Unlike `sanitize_field`, this never rewrites anything - by this point text
    is already burned into video frames or written into metadata, so anything other than
    SAFE means publication must not proceed."""
    status: SafetyStatus
    results: dict
    blocked_fields: list

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "blocked_fields": list(self.blocked_fields),
            "results": {name: r.to_dict() for name, r in self.results.items()},
        }


def scan_publication(fields: dict, now: dt.datetime | None = None) -> PublicationScan:
    """`fields`: {label -> text}. Any non-SAFE verdict fails the whole scan - at this stage
    there is no more opportunity to sanitize, only to decide whether to publish."""
    now = now or _now()
    results = {name: classify_text(text, now=now) for name, text in fields.items() if text}
    blocked = [name for name, r in results.items() if r.status is not SafetyStatus.SAFE]
    status = SafetyStatus.BLOCKED if blocked else SafetyStatus.SAFE
    return PublicationScan(status, results, blocked)


__all__ = ["SafetyStatus", "SafetyResult", "PublicationScan", "classify_text",
           "sanitize_field", "scan_publication", "CONTENT_SAFETY_VERSION"]
