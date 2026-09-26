"""Small, pure text helpers shared by the fact-sheet builders (which declare the number strings
a fact may be written as) and the validator (which reads them back out of a hook line).
Both sides use the same normaliser, so "what a fact allows" and "what a line says" can never
drift apart."""
from __future__ import annotations

import re

from .policy import NUMBER_WORDS

# A number as written in a hook line: optional sign, optional currency, digits with Indian or
# western grouping, optional decimals. A clock time is one token ("10:00").
_NUM = re.compile(r"(?P<sign>[+\-−–])?\s?(?:Rs\.?\s?|₹\s?)?(?P<num>\d{1,2}:\d{2}|\d[\d,]*(?:\.\d+)?)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&'’/\-]*")


def norm_number(raw: str) -> str:
    return raw.replace(",", "")


def numbers_in(text: str) -> list:
    """`[(normalised, sign)]` for every number in `text`; sign is "+", "-" or None. A digit
    glued to letters on its left (a ticker like "STOCK-A1") is not a number."""
    out = []
    for m in _NUM.finditer(text or ""):
        start = m.start("num")
        if start > 0 and text[start - 1].isalpha():
            continue
        sign = m.group("sign")
        if sign in ("−", "–"):
            sign = "-"
        out.append((norm_number(m.group("num")), sign))
    return out


def number_tokens(*texts: str) -> tuple:
    """The normalised numbers a fact's display strings contain - what a line may cite."""
    seen = []
    for t in texts:
        for n, _ in numbers_in(t):
            if n not in seen:
                seen.append(n)
    return tuple(seen)


def words(text: str) -> list:
    return _WORD.findall(text or "")


def number_words_in(text: str) -> list:
    """Spelled-out small numbers ("five stocks") as digit strings, so they are checked exactly
    like digits are."""
    return [NUMBER_WORDS[w.lower()] for w in words(text) if w.lower() in NUMBER_WORDS]


def spell(n: int) -> str:
    """Small counts are written as words at the start of a sentence; the validator reads them
    back through `number_words_in`."""
    inv = {int(v): k for k, v in NUMBER_WORDS.items()}
    return inv.get(n, str(n)) if n != 1 else "one"


def sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?:;—])\s+|\s[—–-]\s", text or "") if s.strip()]


def mentions(text: str, alias: str) -> bool:
    """Whole-word, case-insensitive mention - except all-caps aliases of 2-3 letters ("IT"),
    which must match case-sensitively so the pronoun "it" is never read as the IT sector."""
    if not alias:
        return False
    flags = 0 if (alias.isupper() and len(alias) <= 3) else re.I
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", text or "",
                     flags) is not None


def strip_aliases(text: str, aliases) -> str:
    """Remove entity names before number extraction, so "Nifty 50" or "20-day" inside a name
    is never mistaken for a cited figure."""
    for a in sorted(aliases, key=len, reverse=True):
        if any(ch.isdigit() for ch in a):
            text = re.sub(re.escape(a), " ", text, flags=re.I)
    return text


def clean(text: str) -> str:
    """Normalise model output typography: curly quotes, runs of spaces, stray edges."""
    text = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["norm_number", "numbers_in", "number_tokens", "words", "number_words_in", "spell",
           "sentences", "mentions", "strip_aliases", "clean"]
