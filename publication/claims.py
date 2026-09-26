"""Displayed-claim tracing: every number a viewer can see resolves to approved fact(s) or an
approved deterministic derivation, with its source, role, as-of, rights and decision.

    claim(...)                 one displayed claim (what is visible + where it came from)
    ReportResolver / BriefResolver   fact ids -> non-AI source names, data_as_of, retrieved_at
    post_claims / pre_claims / legacy_claims   claims for every scene of a Short
    check_claims(scene_texts, claims)          -> issues (an uncovered or unresolved number)

Numbers inside names ("NIFTY 200", "S&P 500") are part of a name, and fixed definitions
("20-day", "20-session", "2x") are recorded as DEFINITION claims (an approved deterministic
derivation) - both explicitly, never by ignoring digits in general.
"""
from __future__ import annotations

import re

from core import sources as S

from .classification import RightsStatus
from .classify import SOURCE_LABELS, strictest_rights
from .rights import ATTRIBUTED_EOD, review_required_policy

NAME_NUMBERS = re.compile(r"\b(?:NIFTY|Nifty)\s(?:50|100|200|500)\b|\bS&P\s500\b|"
                          r"\b(?:NIKKEI|Nikkei)\s225\b", re.I)
DEFINITIONS = [
    (re.compile(r"\b(?:20|50)[- ](?:day|session)s?\b", re.I),
     "fixed window definition (prior 20 / 50 sessions)"),
    (re.compile(r"\b(?:prior|last)\s(?:20|50)\ssessions\b", re.I), "fixed window definition"),
    (re.compile(r"\b2x\+?(?=\s|$)", re.I), "unusual-volume threshold: >= 2x the stock's own "
                                           "prior-20-session average (RVOL v2.0)"),
]
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
DERIVED = S.SRC_DERIVED


def _strip(text: str) -> str:
    text = NAME_NUMBERS.sub(" ", text or "")
    for pat, _ in DEFINITIONS:
        text = pat.sub(" ", text)
    return text


def numbers(text: str) -> set:
    """Normalised number tokens a viewer reads (signs, currency, commas, % and x removed)."""
    out = set()
    for m in _NUM.finditer(_strip(text)):
        v = m.group(0).replace(",", "")
        out.add(v.rstrip(".") or v)
    return out


def definitions_in(text: str) -> list:
    return [(m.group(0), why) for pat, why in DEFINITIONS for m in pat.finditer(text or "")]


def decision_for(rights: str, live: bool = False) -> str:
    if rights == RightsStatus.APPROVED.value:
        return "PUBLISHABLE"
    if rights == RightsStatus.REVIEW_REQUIRED.value:
        if review_required_policy() == ATTRIBUTED_EOD and not live:
            return "PUBLISHABLE_WITH_ATTRIBUTION"
        return "BLOCKED_RIGHTS_REVIEW_REQUIRED"
    return f"BLOCKED_RIGHTS_{rights}"


def claim(scene_id, visible_text, metric, fact_ids=(), sources=(), value=None, role="SOURCE",
          data_as_of=None, retrieved_at=None, derivation=None, source_reference=None) -> dict:
    srcs = [s for s in dict.fromkeys(sources) if s]
    rights = (strictest_rights(srcs).value if srcs
              else RightsStatus.APPROVED.value if derivation and not fact_ids
              else RightsStatus.UNKNOWN.value)
    return {"scene_id": scene_id, "visible_text": visible_text, "metric": metric,
            "value": value, "fact_ids": list(dict.fromkeys(str(f) for f in fact_ids if f)),
            "source_name": srcs, "source_label": " · ".join(dict.fromkeys(
                SOURCE_LABELS.get(s, s) for s in srcs)),
            "source_reference": source_reference or " ".join(
                S.source_metadata(s).reference for s in srcs if S.source_metadata(s).reference),
            "source_role": role, "data_as_of": str(data_as_of) if data_as_of else None,
            "retrieved_at": str(retrieved_at) if retrieved_at else None,
            "derivation": derivation, "publication_rights_status": rights,
            "numbers": sorted(numbers(visible_text))}


def definition_claims(scene_id, texts: dict) -> list:
    out, seen = [], set()
    for text in texts.values():
        for phrase, why in definitions_in(text):
            if phrase.lower() not in seen:
                seen.add(phrase.lower())
                out.append(claim(scene_id, phrase, "definition", derivation=why,
                                 sources=(DERIVED,), role="DEFINITION"))
    return out


# --------------------------------------------------------------------------- resolvers
class ReportResolver:
    """Canonical MarketReport facts -> their non-AI sources (a fact backed only by AI keeps
    `gemini`, so its rights are RESTRICTED and the claim blocks)."""

    def __init__(self, report):
        self.report = report

    def fact(self, fid):
        try:
            return self.report.fact(fid) if (self.report is not None and fid) else None
        except Exception:
            return None

    def sources(self, fact_ids) -> list:
        out = []
        for fid in fact_ids or []:
            f = self.fact(fid)
            if f is None:
                continue
            obs = list(f.observations or [])
            non_ai = [o.source_name for o in obs if getattr(o.source_type, "value", "") != "AI"]
            out += non_ai or [o.source_name for o in obs]
        return list(dict.fromkeys(out))

    def retrieved_at(self, fact_ids):
        times = [o.retrieved_at for fid in fact_ids or [] for o in
                 ((self.fact(fid).observations or []) if self.fact(fid) else []) if o.retrieved_at]
        return max(times) if times else None

    def ids_for(self, name: str) -> list:
        """Report fact ids whose instrument matches a displayed name (sector / index label)."""
        r = self.report
        if r is None or not name:
            return []
        key = name.upper().replace("NIFTY ", "").strip()
        for s in getattr(r, "sectors", None) or []:
            if str(s.get("name", "")).upper() == name.upper() and s.get("fact_id"):
                return [s["fact_id"]]
        for c in getattr(r, "global_cues", None) or []:
            if str(c.get("label", "")).upper() == name.upper() and c.get("fact_id"):
                return [c["fact_id"]]
        out = []
        for f in getattr(r, "facts", None) or []:
            inst = str(getattr(f, "instrument", "") or "").upper()
            if inst and (inst == name.upper() or inst.replace("NIFTY ", "") == key):
                out.append(f.fact_id)
        return out


class BriefResolver:
    """PRE: the brief's recorded fact provenance (previous-session facts) + pre-open quotes."""

    def __init__(self, brief):
        self.brief = brief

    def sources(self, fact_ids) -> list:
        out = []
        for fid in fact_ids or []:
            obs = (self.brief.fact_provenance.get(fid) or {}).get("observations") or []
            non_ai = [o["source"] for o in obs if o.get("source_type") != "AI"]
            out += non_ai or [o["source"] for o in obs]
        return list(dict.fromkeys(out))

    def retrieved_at(self, fact_ids):
        t = [o.get("retrieved_at") for fid in fact_ids or []
             for o in (self.brief.fact_provenance.get(fid) or {}).get("observations") or []
             if o.get("retrieved_at")]
        return max(t) if t else None


def _leaves(texts: dict, prefix=""):
    """(field, string) for every string leaf of a scene's texts, provenance excluded."""
    for k, v in (texts or {}).items():
        if k == "provenance":
            continue
        key = f"{prefix}{k}"
        if isinstance(v, str):
            yield key, v
        elif isinstance(v, dict):
            yield from _leaves(v, key + ".")
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield f"{key}.{i}", x
                elif isinstance(x, dict):
                    yield from _leaves(x, f"{key}.{i}.")


def scene_texts(spec) -> dict:
    """Visible strings of one storyboard scene (as `SceneSpec.public_text` declares them),
    without the provenance plate (its dates/times ARE the provenance)."""
    out = {}
    for name in ("headline", "subline", "takeaway"):
        v = getattr(spec, name, "")
        if v:
            out[name] = v
    out.update(dict(_leaves(spec.texts)))
    return out


def _numeric_leaves(texts: dict):
    return [(k, v) for k, v in texts.items() if numbers(v)]


# --------------------------------------------------------------------------- hook sheet
def hook_claims(scene_id, sheet, resolve_fact, texts: dict) -> list:
    """Every sheet fact whose numbers appear in the hook scene -> a claim. The hook validator
    already guarantees each number on a hook line belongs to a sheet fact."""
    shown = set().union(*[numbers(t) for t in texts.values()]) if texts else set()
    out = []
    for f in sheet.facts:
        nums = {n.replace(",", "") for n in (f.numbers or ())} | numbers(f.display)
        if not nums & shown:
            continue
        ids, srcs, deriv = resolve_fact(f)
        c = claim(scene_id, f.display, f.kind.lower(), ids, srcs, value=f.value,
                  derivation=deriv)
        c["numbers"] = sorted(set(c["numbers"]) | nums)
        out.append(c)
    # teaser-beat strings (a cue's "AT 7:40 AM IST", a tile label) resolve through the beat's
    # own facts - a beat may only show facts it declares
    visible = set(texts.values())
    for b in sheet.beats:
        for text in b.strings:
            if text not in visible or not numbers(text):
                continue
            ids, srcs, derivs = [], [], []
            for fid in b.fact_ids:
                f = sheet.fact(fid)
                if f is None:
                    continue
                i, s, d = resolve_fact(f)
                ids += list(i)
                srcs += list(s)
                if d:
                    derivs.append(d)
            out.append(claim(scene_id, text, f"beat:{b.beat_id}", ids, srcs,
                             derivation="; ".join(dict.fromkeys(derivs)) or None))
    return out


def check_claims(scene_texts_by_id: dict, claims: list) -> list:
    """Issues: a displayed number no claim of its scene declares, or a claim resolving to no
    fact and no approved derivation."""
    issues = []
    by_scene = {}
    for c in claims:
        by_scene.setdefault(c["scene_id"], set()).update(c.get("numbers") or [])
        if not c.get("fact_ids") and not c.get("derivation"):
            issues.append(f"{c['scene_id']}: claim {c['visible_text']!r} resolves to no fact "
                          "and no approved derivation")
    for sid, texts in scene_texts_by_id.items():
        declared = by_scene.get(sid, set())
        for field, text in texts.items():
            for n in sorted(numbers(text) - declared):
                issues.append(f"{sid}.{field}: displayed number {n!r} in {text!r} has no claim")
    return issues


__all__ = ["claim", "definition_claims", "hook_claims", "check_claims", "numbers",
           "decision_for", "ReportResolver", "BriefResolver", "scene_texts", "NAME_NUMBERS",
           "DEFINITIONS"]
