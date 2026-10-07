"""Public-language scan: deterministic word lists, no model (the guarantee must hold with the AI
layer down, exactly like core.content_safety).

Six checks, each reported separately in the publication audit:

    recommendation_language           buy/sell/hold, target, stop-loss, entry/exit, accumulate,
                                      "stocks to buy/watch", conviction, model portfolio ...
    ipo_recommendation_language       apply/avoid, subscribe-to-the-IPO, good/best IPO, listing
                                      gain/pop, fair value, cheap/expensive, GMP / grey market ...
    ranking_language                  top stocks / top gainers / stock watch / best stocks
    forecast_language                 will/likely/expected-to/could rise, outlook, forecast ...
    security_specific_technical_analysis
                                      a NAMED security in the same sentence as a technical
                                      term (breakout, 20-day high, moving average, support,
                                      unusual volume, RSI ...). Removing the price does not help.
    unapproved_named_security         a named security the gate did not approve
    english_only                      only English characters and no Hindi/Hinglish words

A disclaimer changes nothing here: a line that says "not advice" and "buy" is still blocked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RECOMMENDATION = [
    r"\bbuy\b", r"\bsell\b", r"\bhold\b", r"\baccumulate\b", r"\btarget", r"\bstop[- ]?loss",
    r"\b(?:entry|exit) (?:price|point|level|zone)s?\b", r"\bbook(?:ing)? profits?\b",
    r"\bstocks? to (?:buy|sell|watch|own|pick)\b", r"\bstock watch\b", r"\bbreakout stocks?\b",
    r"\bconviction\b", r"\bmodel portfolio\b", r"\binvestment score\b", r"\brecommend",
    r"\bshould (?:you|investors|traders) (?:buy|sell|invest|hold|apply|subscribe)\b",
    r"\bmultibagger", r"\bstrong buy\b", r"\bworth (?:buying|owning)\b",
    r"\b(?:bullish|bearish)\b", r"\bexpected returns?\b", r"\battractive (?:stock|entry|valuation)",
]
IPO_RECOMMENDATION = [
    r"\bapply\b", r"\bavoid\b", r"\b(?:don'?t|do not) subscribe\b",
    r"\bsubscribe (?:to|for|in) (?:the|this|an|that) (?:ipo|issue)\b",
    r"\b(?:good|bad|best|worst|hot|top) ipos?\b", r"\blisting (?:gains?|pops?|momentum)\b",
    r"\b(?:expected|strong|bumper) listing\b", r"\bfair value\b", r"\bcheap\b", r"\bexpensive\b",
    r"\b(?:under|over)valued\b", r"\bgmp\b", r"\bgr[ea]y market\b", r"\bkostak\b",
    r"\b(?:successful|failed) (?:ipo|listing)\b", r"\blisting probability\b",
]
RANKING = [r"\btop (?:\d+ )?(?:stocks?|picks?|gainers?|losers?|shares?)\b",
           r"\bbest (?:stocks?|shares?|picks?)\b", r"\bstocks? in focus\b"]
FORECAST = [
    r"\bwill (?:rise|fall|gain|drop|open|list|rally|surge|crash|recover|climb|slide|move|trade)\b",
    r"\blikely to\b", r"\bexpected to\b", r"\bset to (?:rise|fall|gain|open|list)\b",
    r"\bcould (?:rise|fall|gain|drop|rally|surge|crash)\b", r"\bpoised\b", r"\boutlook\b",
    r"\bforecast", r"\bpredict", r"\bupside\b", r"\bdownside\b", r"\bnext (?:target|leg)\b",
]
TECHNICAL = [
    r"\bbreak(?:s|ing)? ?(?:out|down)\b", r"\bbroke (?:out|down)\b", r"\bbreakout\b",
    r"\bbreakdown\b", r"\bcross(?:ed|es|ing)? (?:above|below)\b", r"\bmoving average\b",
    r"\b\d+[- ](?:day|session) (?:high|low|average|range)\b", r"\bsupport\b", r"\bresistance\b",
    r"\brsi\b", r"\bunusual(?:ly high)? volume\b", r"\bvolume spike\b", r"\brelative strength\b",
    r"\b(?:closed|moved|slipped|fell) (?:above|below) its\b", r"\bchart pattern\b",
    r"\boutperform", r"\bunderperform", r"\btrading range\b", r"\b\d+(?:\.\d+)?x normal volume\b",
    r"\bnormal volume\b", r"\bmarket radar\b", r"\bradar\b",
]
_ALLOWED = re.compile(r"^[\x20-\x7E₹×—–·’‘“”…•▲▼↑↓°\n]*$")
HINGLISH = {"hai", "kya", "aaj", "bhai", "paisa", "bazaar", "bazar", "shaandaar", "zabardast",
            "tezi", "teji", "mandi", "gira", "giri", "chadha", "dekho", "yaar", "nahi", "accha",
            "acha", "kal", "sabse", "wala", "wale", "karo", "kijiye", "samjho", "dhamaka"}

_C = {name: [re.compile(p, re.I) for p in pats] for name, pats in (
    ("recommendation_language", RECOMMENDATION), ("ipo_recommendation_language", IPO_RECOMMENDATION),
    ("ranking_language", RANKING), ("forecast_language", FORECAST))}
_TECH = [re.compile(p, re.I) for p in TECHNICAL]
_SENT = re.compile(r"(?<=[.!?;:])\s+|\n|\s[·|]\s")


@dataclass
class LanguageScan:
    issues: dict = field(default_factory=dict)       # check -> [ "field: detail" ]

    def add(self, check: str, detail: str) -> None:
        self.issues.setdefault(check, []).append(detail)

    @property
    def passed(self) -> bool:
        return not any(self.issues.values())

    def check_passed(self, check: str) -> bool:
        return not self.issues.get(check)

    def to_dict(self) -> dict:
        checks = ("recommendation_language", "ipo_recommendation_language", "ranking_language",
                  "forecast_language", "security_specific_technical_analysis",
                  "unapproved_named_security", "english_only")
        return {c: {"passed": self.check_passed(c), "issues": list(self.issues.get(c, []))}
                for c in checks}


def _security_index(known_securities) -> list:
    """[(compiled pattern, canonical name)] - symbols matched case-sensitively as whole tokens,
    company names (without Ltd/Limited) case-insensitively."""
    out = []
    items = known_securities.items() if isinstance(known_securities, dict) else \
        ((s, None) for s in known_securities or ())
    for sym, company in items:
        if sym and len(sym) >= 3:
            out.append((re.compile(r"(?<![A-Za-z0-9&\-])" + re.escape(sym) + r"(?![A-Za-z0-9&\-])"), sym))
        if company:
            base = re.sub(r"\s*\b(?:Ltd\.?|Limited)\s*$", "", company.strip(), flags=re.I)
            if len(base) >= 5:
                out.append((re.compile(r"(?<![A-Za-z])" + re.escape(base) + r"(?![A-Za-z])", re.I), sym))
    return out


def named_securities_in(text: str, known_securities) -> set:
    return {sym for pat, sym in _security_index(known_securities) if pat.search(text or "")}


def is_english(text: str) -> tuple:
    if not _ALLOWED.match(text or ""):
        bad = sorted({ch for ch in text if not _ALLOWED.match(ch)})
        return False, f"non-English characters {bad!r}"
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", text or "")}
    hit = sorted(words & HINGLISH)
    if hit:
        return False, f"Hindi/Hinglish words {hit}"
    return True, ""


def scan_public_text(texts: dict, known_securities=None, approved_securities=(),
                     ipo_context: bool = True) -> LanguageScan:
    """`texts`: {field: string}. `known_securities`: {symbol: company} the universe knows.
    `approved_securities`: symbols the gate approved (an official event) - they may be NAMED,
    but a technical term next to them is still blocked."""
    scan = LanguageScan()
    idx = _security_index(known_securities or {})
    approved = set(approved_securities or ())
    for key, text in texts.items():
        if not isinstance(text, str) or not text.strip():
            continue
        for check, pats in _C.items():
            if check == "ipo_recommendation_language" and not ipo_context:
                continue
            for p in pats:
                m = p.search(text)
                if m:
                    scan.add(check, f"{key}: {m.group(0)!r} in {text!r}")
        ok, why = is_english(text)
        if not ok:
            scan.add("english_only", f"{key}: {why} in {text!r}")
        if idx:
            for sentence in _SENT.split(text):
                named = {sym for pat, sym in idx if pat.search(sentence)}
                if not named:
                    continue
                for sym in sorted(named - approved):
                    scan.add("unapproved_named_security", f"{key}: {sym} named in {text!r}")
                for p in _TECH:
                    m = p.search(sentence)
                    if m:
                        scan.add("security_specific_technical_analysis",
                                 f"{key}: {', '.join(sorted(named))} with {m.group(0)!r} in {text!r}")
                        break
    return scan


__all__ = ["LanguageScan", "scan_public_text", "named_securities_in", "is_english",
           "RECOMMENDATION", "IPO_RECOMMENDATION", "RANKING", "FORECAST", "TECHNICAL"]
