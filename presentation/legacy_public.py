"""The scheduled POST (legacy `video.py` Short) under PUBLIC_UNREGISTERED: per-scene SOURCE / DATA
AS OF from the plan's own canonical fact ids, a compliant title/description/tags, and the scene
view the publication audit checks. The legacy renderer is unchanged apart from drawing the
provenance plate it is handed (video.render(provenance=...)).
"""
from __future__ import annotations

from presentation.provenance_label import ProvenanceLabel, fmt_date, session_label
from publication import disclaimer
from publication.classification import Scope
from publication.classify import report_fact

NO_PROVENANCE = {"HOOK", "OUTRO"}
SESSION_CLOSE_KINDS = {"NIFTY", "SECTORS"}


def plan_provenance(report, plan) -> list:
    """One {"source", "as_of"} (or None) per plan scene, in scene order."""
    session = report.session_date or report.report_date
    out = []
    for sc in plan.scenes:
        kind = sc.scene_type.value
        if kind in NO_PROVENANCE:
            out.append(None)
            continue
        if kind == "CONTEXT":
            lab = ProvenanceLabel(source="Daily Market Byte history · NSE / Yahoo Finance",
                                  data_as_of=fmt_date(session))
        elif kind == "EVENTS":
            lab = ProvenanceLabel(source="NSE expiry rule",
                                  data_as_of=fmt_date(report.report_date or session),
                                  as_of_label="EVENT DATE")
        else:
            ids = list(sc.source_fact_ids or []) or [fid for it in sc.items
                                                     for fid in it.source_fact_ids]
            f = report_fact(report, ids, "", Scope.INDEX, kind, "prov", session)
            src = f.source_label or "NSE"
            lab = (session_label(src, session) if kind in SESSION_CLOSE_KINDS
                   else ProvenanceLabel(source=src, data_as_of=fmt_date(session)))
        lines = lab.lines()
        out.append({"source": lines[0], "as_of": lines[1]})
    return out


def legacy_scene_audit(plan, provs) -> list:
    return [{"kind": sc.scene_type.value, "requires_provenance":
             sc.scene_type.value not in NO_PROVENANCE,
             "provenance": ({"source": p["source"], "data_as_of": p["as_of"]} if p else {}),
             "universe_required": False, "universe": {}}
            for sc, p in zip(plan.scenes, provs)]


def provenance_text(provs) -> dict:
    out = {}
    for i, p in enumerate(provs):
        if p:
            out[f"provenance.{i}.source"] = p["source"]
            out[f"provenance.{i}.as_of"] = p["as_of"]
    return out


def public_metadata(m, plan, info) -> dict:
    """Title / description / tags built ONLY from what the public plan shows. No stock names,
    no rankings ("Top gainers"), no AI-only figures, the standard disclaimer."""
    from config import fmt_in
    d = m["recap_date"].strftime("%d %b")
    title = (f"Nifty {fmt_in(m['close'])} ({m['pct']:+.2f}%): What Changed in India's Market, "
             f"{d} #shorts")[:100]
    L = [f"Daily Market Byte - Indian market recap for {info['recap_str']}.", "",
         f"Nifty 50: {fmt_in(m['close'], 2)} ({m['pct']:+.2f}%)"]
    for sc in plan.scenes:
        kind = sc.scene_type.value
        if kind == "SECTORS" and sc.items:
            L += ["Sectors: " + " | ".join(f"{it.title} {it.value}" for it in sc.items)]
        elif kind == "FLOWS" and sc.items:
            L += ["Institutional flows (provisional): "
                  + " | ".join(f"{it.title} {it.value}" for it in sc.items)]
        elif kind == "GLOBAL" and sc.items:
            L += ["Global markets: " + " | ".join(f"{it.title} {it.value}" for it in sc.items)]
        elif kind == "EVENTS" and sc.items:
            L += ["", f"On the calendar ({info['today_short']}):"] + \
                 [f"  [{it.label}] {it.title}" for it in sc.items]
    session = plan.session_date
    L += ["", f"Sources: NSE and Yahoo Finance end-of-day data. Data as of the "
              f"{session:%d %b %Y} close." if session else "Sources: NSE and Yahoo Finance.",
          "", disclaimer.DESCRIPTION, "",
          "#stockmarket #nifty #sensex #sharemarket #indianstockmarket #dailymarketbyte #shorts"]
    tags = ["stock market", "nifty", "nifty 50", "sensex", "share market", "fii dii data",
            "sector performance", "stock market today", "indian stock market", "daily byte",
            "market recap"]
    return {"title": title, "description": "\n".join(L), "tags": tags}


__all__ = ["plan_provenance", "legacy_scene_audit", "provenance_text", "public_metadata"]
