"""Strict validation of a hook - Gemini's, or our own template's.

The same checks run on both, so a deterministic template that could not pass them is never
offered as a candidate, and the fallback is held to exactly the standard the model is.

Text checks (per line):
    length (chars and words), allowed characters
    content safety (core.content_safety, no model) must be SAFE
    no causal language, no prediction, no recommendation wording, no hype
    every number - digits or spelled out - belongs to a fact in the sheet AND to the entity
        it is written next to (or to an entity-free count), with a matching sign
    every capitalised name is an entity in the sheet or a brand/product word
    no sector named that the sheet does not contain
    characterisations and superlatives ("just", "led", "biggest") need a fact that carries
        the matching deterministic claim
    direction words next to an entity ("Nifty fell") match that entity's fact
    the curiosity line anchors on the chosen candidate's facts
    the summary line promises only sections the Short really contains

Structure checks: the candidate, archetype, hero visual and teaser beats are all ones that
were supplied, and the teaser has 2-3 distinct beats.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.content_safety import SafetyStatus, classify_text

from . import diversity, policy
from .models import HookCandidate, HookFactSheet
from .text import clean

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&'’/\-]*")
_NUM = re.compile(r"(?P<sign>[+\-−–])?\s?(?:Rs\.?\s?|₹\s?)?(?P<num>\d{1,2}:\d{2}|\d[\d,]*(?:\.\d+)?)")
_SENT_BREAK = re.compile(r"[.!?:;—]|\s[–-]\s")
_ALLOWED_CHARS = re.compile(r"^[\x20-\x7E×—–₹’]*$")
MOVE_KINDS = ("INDEX_MOVE", "SECTOR_MOVE", "STOCK_MOVE", "FLOW", "GLOBAL_CUE", "GIFT_NIFTY")

_COMPILED = {
    "causal": [re.compile(p, re.I) for p in policy.CAUSAL_PATTERNS],
    "prediction": [re.compile(p, re.I) for p in policy.PREDICTION_PATTERNS],
    "recommendation": [re.compile(p, re.I) for p in policy.RECOMMENDATION_PATTERNS],
}
_TERMS = {}
for _sec, _terms in policy.SECTION_PROMISES.items():
    for _t in _terms:
        _TERMS.setdefault(_t, set()).add(_sec)


@dataclass
class Verdict:
    ok: bool
    issues: list = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _alias_index(sheet: HookFactSheet) -> list:
    idx = {}
    for f in sheet.facts:
        for a in f.aliases:
            if a and a.lower() not in idx:
                idx[a.lower()] = (a, f.entity)
    return sorted(idx.values(), key=lambda t: -len(t[0]))


def _find_mentions(text: str, index: list) -> list:
    """Non-overlapping entity mentions, longest alias first: `[(start, end, entity)]`."""
    taken = [False] * len(text)
    out = []
    for alias, entity in index:
        flags = 0 if (alias.isupper() and len(alias) <= 3) else re.I
        pat = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
        for m in re.finditer(pat, text, flags):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            out.append((m.start(), m.end(), entity))
    return sorted(out)


def _inside(pos: int, spans) -> bool:
    return any(s <= pos < e for s, e, _ in spans)


def _sentence_bounds(text: str, pos: int) -> tuple:
    start = 0
    for m in _SENT_BREAK.finditer(text):
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            return start, m.start()
    return start, len(text)


def _sentence_initial(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    return not before or before[-1] in ".!?:;—–-"


def _numbers(text: str) -> list:
    """`[(pos, normalised, sign)]` for digits and spelled-out small numbers."""
    out = []
    for m in _NUM.finditer(text):
        start = m.start("num")
        if start > 0 and text[start - 1].isalpha():
            continue
        s = m.group("sign")
        out.append((start, m.group("num").replace(",", ""),
                    "-" if s in ("-", "−", "–") else s))
    for m in _WORD.finditer(text):
        w = m.group().lower()
        if w in policy.NUMBER_WORDS:
            out.append((m.start(), policy.NUMBER_WORDS[w], None))
    return out


def _anchor_entity(text: str, pos: int, mentions: list) -> str | None:
    """The entity a number at `pos` is written about: the nearest mention before it in its
    sentence, else the nearest after it in its sentence, else the nearest before it at all."""
    s0, s1 = _sentence_bounds(text, pos)
    before = [m for m in mentions if s0 <= m[0] < pos]
    if before:
        return before[-1][2]
    after = [m for m in mentions if pos < m[0] < s1]
    if after:
        return after[0][2]
    earlier = [m for m in mentions if m[0] < pos]
    return earlier[-1][2] if earlier else None


def _polarity(sheet: HookFactSheet, entity: str):
    for kind in MOVE_KINDS:
        for f in sheet.facts:
            if f.entity == entity and f.kind == kind and f.polarity is not None:
                return f.polarity
    return None


# --------------------------------------------------------------------------- one line
def check_line(text: str, role: str, sheet: HookFactSheet, candidate: HookCandidate | None,
               beat_ids=()) -> list:
    """Every reason `text` may not be published as the hook's `role` line ("curiosity" or
    "summary"). An empty list means it passes."""
    issues = []
    if not text or not text.strip():
        return [f"{role}: empty"]
    max_c = policy.CURIOSITY_MAX_CHARS if role == "curiosity" else policy.SUMMARY_MAX_CHARS
    max_w = policy.CURIOSITY_MAX_WORDS if role == "curiosity" else policy.SUMMARY_MAX_WORDS
    if len(text) > max_c:
        issues.append(f"{role}: {len(text)} chars > {max_c}")
    if len(text.split()) > max_w:
        issues.append(f"{role}: {len(text.split())} words > {max_w}")
    if not _ALLOWED_CHARS.match(text):
        issues.append(f"{role}: characters outside the allowed set")
    if "\n" in text:
        issues.append(f"{role}: line break")

    safety = classify_text(text)
    if safety.status is not SafetyStatus.SAFE:
        issues.append(f"{role}: content safety {safety.status.value} {safety.matched_rules}")
    for family, pats in _COMPILED.items():
        for p in pats:
            m = p.search(text)
            if m:
                issues.append(f"{role}: {family} language '{m.group(0)}'")

    index = _alias_index(sheet)
    ments = _find_mentions(text, index)
    mentioned = {e for _, _, e in ments}

    # numbers: in the sheet, attributable, sign-consistent
    by_number = {}
    for f in sheet.facts:
        for n in f.numbers:
            by_number.setdefault(n, []).append(f)
    cited = set()
    for pos, num, sgn in _numbers(text):
        if _inside(pos, ments):
            continue            # a digit inside a name ("Nifty 50", "20-day" in an alias)
        owners = by_number.get(num, [])
        if not owners:
            issues.append(f"{role}: number '{num}' is not in any supplied fact")
            continue
        anchor = _anchor_entity(text, pos, ments)
        after = [w.lower() for w in _WORD.findall(text[pos:])][:4]
        fits = [f for f in owners if f.entity == anchor or
                (f.anchor_free and (not f.unit_words or
                                    any(w.startswith(u) for w in after for u in f.unit_words)))]
        if not fits:
            issues.append(f"{role}: number '{num}' is not a fact about "
                          f"{anchor or 'the entity it is written next to'}")
            continue
        if sgn in ("+", "-"):
            want = 1 if sgn == "+" else -1
            if not any(f.polarity in (None, 0, want) for f in fits):
                issues.append(f"{role}: sign of '{sgn}{num}' contradicts the fact")
        cited.update(f.fact_id for f in fits)

    # names: every capitalised word is an entity, a brand word, or a plain sentence opener
    for m in _WORD.finditer(text):
        w = m.group()
        if not any(c.isupper() for c in w) or _inside(m.start(), ments):
            continue
        lw = re.sub(r"['’]s$", "", w.lower())
        if lw in policy.GENERIC_PROPER:
            continue
        opener = _sentence_initial(text, m.start()) and not w.isupper()
        if opener and (lw in policy.STARTER_WORDS or lw in policy.NUMBER_WORDS or
                       any(lw in ws for ws in policy.CLAIM_WORDS.values()) or
                       lw in policy.UP_WORDS or lw in policy.DOWN_WORDS):
            continue
        issues.append(f"{role}: '{w}' is not an entity in the supplied facts")
    masked = "".join(" " if _inside(i, ments) else ch for i, ch in enumerate(text))
    for sector in policy.KNOWN_SECTORS:
        flags = 0 if (sector.isupper() and len(sector) <= 3) else re.I
        if re.search(r"(?<![A-Za-z])" + re.escape(sector) + r"(?![A-Za-z])", masked, flags):
            issues.append(f"{role}: names sector '{sector}', which is not in the supplied facts")

    # characterisations and superlatives need a deterministic claim behind them
    if role == "summary":
        licensing = list(sheet.facts)
    else:
        ids = set(candidate.fact_ids if candidate else ()) | cited
        for bid in beat_ids:
            b = sheet.beat(bid)
            if b:
                ids |= set(b.fact_ids)
        licensing = [f for f in sheet.facts if f.fact_id in ids or f.entity in mentioned]
    claims = set().union(*(f.claims for f in licensing)) if licensing else set()
    for m in _WORD.finditer(text):
        lw = re.sub(r"['’]s$", "", m.group().lower())
        if lw in policy.HYPE_WORDS:
            issues.append(f"{role}: hype word '{m.group()}'")
            continue
        need = [c for c, ws in policy.CLAIM_WORDS.items() if lw in ws]
        if need and not (claims & set(need)):
            issues.append(f"{role}: '{m.group()}' needs a {'/'.join(sorted(need))} fact")
        elif not need and lw in policy.SUPERLATIVES:
            issues.append(f"{role}: unsupported superlative '{m.group()}'")

    # direction words next to an entity must agree with that entity's fact
    for s, e, entity in ments:
        s0, s1 = _sentence_bounds(text, s)
        tail = [w.lower() for w in _WORD.findall(text[e:s1])][:4]
        word = next((w for w in tail if w in policy.UP_WORDS or w in policy.DOWN_WORDS), None)
        if word is None:
            continue
        pol = _polarity(sheet, entity)
        if pol is None:
            issues.append(f"{role}: '{word}' about {entity}, which has no directional fact")
        elif (word in policy.UP_WORDS and pol < 0) or (word in policy.DOWN_WORDS and pol > 0):
            issues.append(f"{role}: '{word}' contradicts {entity}'s fact")

    if role == "curiosity" and candidate is not None:
        own = [sheet.fact(fid) for fid in candidate.fact_ids]
        own_entities = {f.entity for f in own if f}
        if not (own_entities & mentioned or cited & set(candidate.fact_ids)):
            issues.append("curiosity: does not name any fact of the chosen candidate")

    if role == "summary":
        low = text.lower()
        promised = [t for t in _TERMS if re.search(r"(?<![a-z])" + re.escape(t) + r"(?:s|es)?(?![a-z])", low)]
        if not promised:
            issues.append("summary: does not say what the Short shows")
        for t in promised:
            if not (_TERMS[t] & set(sheet.sections)):
                issues.append(f"summary: promises '{t}', which this Short does not contain")
    return issues


def check_text(curiosity: str, summary: str, sheet: HookFactSheet,
               candidate: HookCandidate | None, beat_ids=()) -> list:
    return (check_line(curiosity, "curiosity", sheet, candidate, beat_ids) +
            check_line(summary, "summary", sheet, candidate, beat_ids))


# --------------------------------------------------------------------------- structure
REQUIRED_KEYS = ("candidate_id", "archetype", "hero_visual", "teaser_beats", "curiosity_line",
                 "summary_line")


def check_structure(obj, sheet: HookFactSheet, candidates: list) -> tuple:
    """`(issues, candidate)` for a parsed Gemini response."""
    if not isinstance(obj, dict):
        return ["response is not a JSON object"], None
    issues = [f"missing '{k}'" for k in REQUIRED_KEYS if k not in obj]
    cand = next((c for c in candidates if c.candidate_id == obj.get("candidate_id")), None)
    if cand is None:
        issues.append(f"unknown candidate_id {obj.get('candidate_id')!r}")
    else:
        if obj.get("archetype") != cand.archetype.value:
            issues.append(f"archetype {obj.get('archetype')!r} is not candidate "
                          f"{cand.candidate_id}'s ({cand.archetype.value})")
        if obj.get("hero_visual") not in [h.value for h in cand.heroes]:
            issues.append(f"hero_visual {obj.get('hero_visual')!r} not approved for {cand.candidate_id}")
    beats = obj.get("teaser_beats")
    if not isinstance(beats, list) or not all(isinstance(b, str) for b in beats):
        issues.append("teaser_beats is not a list of beat ids")
    else:
        # POST (freeze): fewer beats are allowed only when the sheet cannot offer more
        # entity-distinct ones; PRE/CUSTOM keep the fixed 2-3.
        lo = (min(policy.MIN_BEATS, diversity.max_diverse(sheet, policy.MAX_BEATS))
              if diversity.applies(sheet) else policy.MIN_BEATS)
        if not lo <= len(beats) <= policy.MAX_BEATS:
            issues.append(f"teaser_beats has {len(beats)} beats (need "
                          f"{lo}-{policy.MAX_BEATS})")
        if len(set(beats)) != len(beats):
            issues.append("teaser_beats repeats a beat")
        if diversity.applies(sheet):
            for a, b, e in diversity.entity_clashes(sheet, beats):
                issues.append(f"teaser beats {a} and {b} both show {e} (one beat per entity)")
        for b in beats:
            if sheet.beat(b) is None:
                issues.append(f"unknown teaser beat {b!r}")
    for k in ("curiosity_line", "summary_line"):
        if k in obj and not isinstance(obj[k], str):
            issues.append(f"{k} is not a string")
    used = obj.get("fact_ids_used") or []
    if not isinstance(used, list):
        issues.append("fact_ids_used is not a list")
    else:
        for fid in used:
            if sheet.fact(str(fid)) is None:
                issues.append(f"unknown fact id {fid!r}")
    return issues, cand


def validate_response(obj, sheet: HookFactSheet, candidates: list) -> tuple:
    """`(structure_issues, text_issues, candidate, curiosity, summary)`."""
    s_issues, cand = check_structure(obj, sheet, candidates)
    cur = clean(obj.get("curiosity_line", "")) if isinstance(obj, dict) else ""
    summ = clean(obj.get("summary_line", "")) if isinstance(obj, dict) else ""
    t_issues = []
    if cand is not None and isinstance(obj, dict):
        beats = [b for b in (obj.get("teaser_beats") or []) if isinstance(b, str)]
        t_issues = check_text(cur, summ, sheet, cand, beats)
    return s_issues, t_issues, cand, cur, summ


__all__ = ["check_line", "check_text", "check_structure", "validate_response", "Verdict",
           "REQUIRED_KEYS"]
