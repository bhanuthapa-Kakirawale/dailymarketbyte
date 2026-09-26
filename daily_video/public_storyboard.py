"""Scene specs for the public-intelligence sections (shared by the POST and PRE storyboards), and
the per-scene view the publication audit checks (is SOURCE / DATA AS OF on screen? is the
universe + denominator on screen?).

Every string a scene draws is declared in `texts` - including the provenance bar's two lines -
so the content scan, the language scan and the audit see exactly what the viewer sees.
"""
from __future__ import annotations

from .storyboard import SceneSpec

NO_PROVENANCE_KINDS = {"HOOK", "DYNAMIC_HOOK", "CLOSING", "RADAR_INTRO"}


def _prov(lines) -> dict:
    return {"source": lines[0], "as_of": lines[1]}


def structure_spec(ins, prov_lines) -> SceneSpec:
    rows = [{"name": r["name"], "value": r["value"]} for r in ins.rows]
    split = [{"label": s["label"], "value": s["value"]} for s in ins.split]
    texts = {"headline": ins.headline, "hero_value": ins.hero_value, "hero_label": ins.hero_label,
             "definition": ins.definition, "rows": rows, "split": split,
             "takeaway": ins.takeaway, "provenance": _prov(prov_lines)}
    dur = round(5.4 + 0.25 * len(rows) + (0.6 if ins.takeaway else 0.0) + 0.3 * bool(split), 2)
    return SceneSpec(
        kind="STRUCTURE", section="STRUCTURE", duration=dur, headline=ins.headline,
        takeaway=ins.takeaway, texts=texts,
        data={"kind": ins.kind, "rows_n": [r["n"] for r in ins.rows],
              "split_n": [int(s["value"]) for s in ins.split if s["value"].isdigit()],
              "universe": {"label": ins.universe_label, "denominator_text": ins.denominator_text,
                           "coverage_pct": ins.coverage_pct, "status": ins.coverage_status}},
        freeze={"t": round(dur - 0.5, 2), "what": f"{ins.hero_value} {ins.hero_label}",
                "where": "the count over its named universe, the sector rows / split below it, "
                         "SOURCE and DATA AS OF in the bar at the bottom",
                "why": ins.reason, "mode": "STRUCTURE"})


def exchange_spec(model, mode: str = "POST") -> SceneSpec:
    cards = [{k: c[k] for k in ("tag", "name", "company", "status", "line", "change")}
             for c in model["cards"]]
    sub = "From the exchange's own lists"
    dur = round(3.6 + 1.7 * len(cards), 2)
    return SceneSpec(
        kind="EXCHANGE_WATCH", section="EXCHANGE", duration=dur, headline=model["headline"],
        subline=sub, texts={"headline": model["headline"], "subline": sub, "cards": cards,
                            "provenance": _prov(model["provenance_lines"])},
        data={"event_ids": model["event_ids"]},
        freeze={"t": round(dur - 0.5, 2), "what": model["headline"],
                "where": "one card per security: the exchange status chip, the security, one "
                         "fixed factual line", "why": "official exchange status - not a signal",
                "mode": "EXCHANGE_WATCH"})


def ipo_spec(model) -> SceneSpec:
    if model["layout"] == "CARD":
        c = model["card"]
        texts = {"headline": model["headline"], "name": c["name"], "board": c["board"],
                 "chip": c["chip"], "rows": c["rows"], "provenance": _prov(model["provenance_lines"])}
        dur = round(4.4 + 0.5 * len(c["rows"]), 2)
        return SceneSpec(kind="IPO_WATCH", section="IPO", duration=dur, headline=model["headline"],
                         texts=texts, data={"kind": c["kind"]},
                         freeze={"t": round(dur - 0.5, 2), "what": model["headline"],
                                 "where": "the IPO card: dated event, board, official fact rows",
                                 "why": "a dated primary-market event today", "mode": "IPO_WATCH"})
    texts = {"headline": model["headline"], "rows": model["rows"],
             "provenance": _prov(model["provenance_lines"])}
    dur = round(3.8 + 1.0 * len(model["rows"]), 2)
    return SceneSpec(kind="IPO_BOARD", section="IPO", duration=dur, headline=model["headline"],
                     texts=texts, data={},
                     freeze={"t": round(dur - 0.5, 2), "what": model["headline"],
                             "where": "one row per IPO with its dated event",
                             "why": "several dated primary-market events today",
                             "mode": "IPO_BOARD"})


def scene_audit(storyboard) -> list:
    out = []
    for s in storyboard.scenes:
        prov = (s.texts or {}).get("provenance")
        uni = (s.data or {}).get("universe") if s.kind == "STRUCTURE" else None
        out.append({"kind": s.kind, "section": s.section,
                    "requires_provenance": s.kind not in NO_PROVENANCE_KINDS,
                    "provenance": {"source": (prov or {}).get("source"),
                                   "data_as_of": (prov or {}).get("as_of")} if prov else {},
                    "universe_required": s.kind == "STRUCTURE",
                    "universe": uni or {}})
    return out


def audit_storyboard(sb, product: str, metadata: dict | None = None,
                     video_path: str | None = None, synthetic: bool = False) -> dict:
    """publication_audit.json for a unified POST / PRE storyboard: the gate's record, every
    on-screen string (hook included), the per-scene provenance/universe view, the public
    section audits (Market Structure, Exchange Watch, IPO Watch) and the Gemini candidate set."""
    from publication import PublicationGate, build_publication_audit
    gate = sb.gate or PublicationGate(sb.publication_profile)
    hook = dict(sb.hook_plan or {})
    if hook:
        hook["candidates"] = [dict(zip(("candidate_id", "archetype", "score"), c))
                              if isinstance(c, (list, tuple)) else c
                              for c in (hook.get("candidates") or [])]
    pa = sb.public_audit or {}
    return build_publication_audit(
        gate=gate, product=product, session_date=sb.session_date, public_text=sb.public_text(),
        scenes=scene_audit(sb), metadata=metadata or {},
        market_structure=pa.get("market_structure"), ipo=pa.get("ipo"),
        exchange_watch=pa.get("exchange_watch"), hook=hook or None, sources=sb.sources,
        video_path=video_path, synthetic=synthetic)



__all__ = ["structure_spec", "exchange_spec", "ipo_spec", "scene_audit", "audit_storyboard",
           "NO_PROVENANCE_KINDS"]
