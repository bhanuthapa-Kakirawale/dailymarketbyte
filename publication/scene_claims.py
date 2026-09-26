"""Claim builders: the displayed claims of every scene of a unified POST / PRE storyboard and of
the legacy scheduled POST (plan scenes + the ticker strip). See publication/claims.py.

Each builder knows, per scene kind, which fact ids a visible string comes from - resolved at
the point the storyboard is built, never by parsing rendered pixels.
"""
from __future__ import annotations

from core import sources as S

from .claims import (BriefResolver, ReportResolver, claim, definition_claims, hook_claims,
                     numbers, scene_texts)

STRUCTURE_SOURCES = (S.SRC_YAHOO, S.SRC_NSE_CONSTITUENTS, S.SRC_MARKET_STRUCTURE)
STRUCTURE_ROLE = "PRICES: YAHOO FINANCE EOD / UNIVERSE & SECTORS: NSE"
NO_CLAIM_KINDS = {"CLOSING"}


def _numeric(texts):
    return [(k, v) for k, v in texts.items() if numbers(v)]


def _public_section_claims(sid, spec, texts, session):
    out = []
    kind = spec.kind
    if kind == "STRUCTURE":
        keys = spec.data.get("metric_keys") or []
        base = [f"market_structure:{session}:{k}" for k in keys]
        for k, v in _numeric(texts):
            deriv = ("share = numerator / denominator of the same count" if "%" in v else
                     "count over the named universe's per-constituent observations "
                     "(market_structure.aggregate; reconciled)")
            ids = list(base)
            if k.startswith("rows."):
                name = texts.get(k.rsplit(".", 1)[0] + ".name", "")
                ids = [f"{i}:{name}" for i in base]
            out.append(claim(sid, v, k, ids, STRUCTURE_SOURCES, role=STRUCTURE_ROLE,
                             data_as_of=session, derivation=deriv))
    elif kind == "EXCHANGE_WATCH":
        for k, v in _numeric(texts):
            out.append(claim(sid, v, k, spec.data.get("event_ids") or [],
                             spec.data.get("sources") or [], role="SOURCE", data_as_of=session))
    elif kind in ("IPO_WATCH", "IPO_BOARD"):
        ids = [f"ipo:{c}" for c in spec.data.get("companies") or []]
        for k, v in _numeric(texts):
            out.append(claim(sid, v, k, ids, spec.data.get("sources") or [], data_as_of=session))
    return out


# --------------------------------------------------------------------------- unified POST
def post_claims(sb, report, sheet=None) -> list:
    res = ReportResolver(report)
    session = sb.session_date
    nifty_ids = list((getattr(report, "nifty", None) or {}).get("fact_ids") or []) if report else []
    flow_ids = list((getattr(report, "institutional_flows", None) or {}).get("fact_ids") or []) \
        if report else []
    sector_ids = [s.get("fact_id") for s in (getattr(report, "sectors", None) or [])
                  if s.get("fact_id")] if report else []

    def rc(sid, text, metric, ids, derivation=None, role="SOURCE"):
        return claim(sid, text, metric, ids, res.sources(ids), role=role, data_as_of=session,
                     retrieved_at=res.retrieved_at(ids), derivation=derivation)

    def resolve_fact(f):
        # well-known kinds resolve to THEIR canonical facts first: a hook fact's refs can carry
        # whatever the legacy hook candidate cited (e.g. an FII fact next to Nifty's move)
        kind, ent = f.kind, f.entity
        if kind == "SECTOR_MOVE":
            ids = res.ids_for(ent)
            if ids:
                return ids, res.sources(ids), None
        if kind in ("INDEX_MOVE", "INDEX_LEVEL", "INDEX_CONTEXT", "INDEX_RANK") and                 "NIFTY 50" in ent.upper() and nifty_ids:
            return nifty_ids, res.sources(nifty_ids), (
                "historical rank over canonical sessions" if kind == "INDEX_RANK" else None)
        if kind == "FLOW" and flow_ids:
            ids = [i for i in flow_ids if ent.upper() in i.upper()] or flow_ids
            return ids, res.sources(ids), None
        refs = [r for r in f.source_refs if res.fact(r) is not None]
        if refs:
            return refs, res.sources(refs), ("historical rank over canonical sessions"
                                             if kind == "INDEX_RANK" else None)
        if kind == "STRUCTURE_COUNT":
            return ([f"market_structure:{session}:{k}" for k in f.source_refs],
                    list(STRUCTURE_SOURCES), "count over the named universe")
        if kind in ("INDEX_MOVE", "INDEX_LEVEL", "INDEX_CONTEXT", "INDEX_RANK") and \
                "NIFTY 50" in ent.upper():
            return nifty_ids, res.sources(nifty_ids), (
                "historical rank over canonical sessions" if kind == "INDEX_RANK" else None)
        if kind == "FLOW":
            return flow_ids, res.sources(flow_ids), None
        if kind == "COUNT":
            ids = sector_ids if "sector" in f.statement.lower() else []
            return ids, res.sources(ids) or [S.SRC_DERIVED], "count of the displayed facts"
        if kind == "SESSION":
            return [], [S.SRC_DERIVED], "the session's date (canonical trading calendar)"
        ids = res.ids_for(ent)
        return ids, res.sources(ids), None

    out = []
    for i, spec in enumerate(sb.scenes):
        sid = f"{i:02d}_{spec.kind.lower()}"
        texts = scene_texts(spec)
        if spec.kind in NO_CLAIM_KINDS:
            continue
        out += definition_claims(sid, texts)
        k = spec.kind
        if k == "DYNAMIC_HOOK" and sheet is not None:
            out += hook_claims(sid, sheet, resolve_fact, texts)
        elif k in ("PULSE", "NIFTY"):
            for f, v in _numeric(texts):
                deriv = None
                if "pts" in v:
                    deriv = "change in points = close - previous close (same facts)"
                if f.startswith("reference"):
                    deriv = "prior-20/50-session high or low of the report's own candles"
                out.append(rc(sid, v, f, nifty_ids, deriv))
        elif k == "SECTORS":
            for f, v in _numeric(texts):
                if f.startswith("rows."):
                    name = texts.get(f.rsplit(".", 1)[0] + ".name", "")
                    out.append(rc(sid, v, f"sector:{name}", res.ids_for(name)))
                else:
                    out.append(rc(sid, v, f, sector_ids, "count of the tracked sector indices"))
        elif k == "FLOWS":
            for f, v in _numeric(texts):
                if f.startswith("bars."):
                    who = texts.get(f.rsplit(".", 1)[0] + ".name", "").upper()
                    ids = [i for i in flow_ids if who and who in i.upper()] or flow_ids
                    out.append(rc(sid, v, f"flow:{who}", ids))
                else:
                    out.append(rc(sid, v, f, flow_ids,
                                  "consecutive-session count over canonical history"))
        elif k == "GLOBAL":
            for f, v in _numeric(texts):
                name = texts.get(f.rsplit(".", 1)[0] + ".name", "") if "." in f else ""
                out.append(rc(sid, v, f, res.ids_for(name)))
        elif k in ("STRUCTURE", "EXCHANGE_WATCH", "IPO_WATCH", "IPO_BOARD"):
            out += _public_section_claims(sid, spec, texts, session)
        else:
            # private-only / rare scenes: the scene's declared fact ids, else unresolved
            ids = list((spec.data or {}).get("fact_ids") or [])
            for f, v in _numeric(texts):
                out.append(rc(sid, v, f, ids))
    return out


# --------------------------------------------------------------------------- PRE
_EVENT_SOURCE = (("rbi.org.in", S.SRC_RBI_PRESS), ("federalreserve", S.SRC_FED_CALENDAR))


def pre_claims(sb, brief, sheet=None) -> list:
    res = BriefResolver(brief)
    session = brief.previous_session
    nifty_ids = list(brief.nifty.get("fact_ids") or [])
    prov = brief.fact_provenance
    flow_ids = [f for f, p in prov.items() if p.get("metric") in ("FII_NET_CASH", "DII_NET_CASH")]
    quotes = {q.name.upper(): q for q in brief.global_cues}

    def ids_for(name):
        return [f for f, p in prov.items() if str(p.get("instrument") or "").upper()
                in (name.upper(), f"NIFTY {name.upper()}")]

    def rc(sid, text, metric, ids, derivation=None, sources=None, as_of=None):
        if not ids and brief.synthetic:
            # a synthetic fixture brief carries no canonical fact ids: trace to the fixture
            # itself (RESTRICTED - a synthetic render can never pass the rights check)
            ids, sources = [f"synthetic:{metric}"], [S.SRC_DEMO]
        return claim(sid, text, metric, ids, sources if sources is not None else res.sources(ids),
                     data_as_of=as_of or session, retrieved_at=res.retrieved_at(ids),
                     derivation=derivation)

    def quote_claim(sid, text, metric, name):
        q = quotes.get(name.upper())
        if q is None and "GIFT" in name.upper() and brief.gift is not None:
            q = brief.gift
        if q is None:
            return rc(sid, text, metric, [])
        return claim(sid, text, metric, [f"premarket:{q.key}"], [q.source],
                     data_as_of=q.market_timestamp or q.market_date, retrieved_at=q.retrieved_at)

    def event_sources():
        e = brief.events[0] if brief.events else None
        if e is None:
            return []
        if brief.synthetic:
            return [S.SRC_DEMO]
        for key, src in _EVENT_SOURCE:
            if key in (e.source or ""):
                return [src]
        return [S.SRC_RULE_EXPIRY]

    def resolve_fact(f):
        ent = f.entity.upper()
        if f.source_refs and any(r in prov for r in f.source_refs):
            refs = [r for r in f.source_refs if r in prov]
            return refs, res.sources(refs), None
        if "NIFTY 50" in ent or ent.startswith("NIFTY"):
            return nifty_ids, res.sources(nifty_ids), None
        if ent.upper() in quotes or "GIFT" in ent:
            q = quotes.get(ent) or brief.gift
            return ([f"premarket:{q.key}"], [q.source], None) if q else ([], [], None)
        if f.kind in ("COUNT", "SESSION"):
            return [], [S.SRC_DERIVED], "count / calendar date of the displayed facts"
        if f.kind == "VIX" and brief.vix is not None:
            return ["premarket:india_vix"], [brief.vix.source], None
        if f.kind == "EVENT":
            return [f"event:{f.entity}"], event_sources(), "verified schedule"
        return [], [], None

    out = []
    for i, spec in enumerate(sb.scenes):
        sid = f"{i:02d}_{spec.kind.lower()}"
        texts = scene_texts(spec)
        if spec.kind in NO_CLAIM_KINDS:
            continue
        out += definition_claims(sid, texts)
        k, sec = spec.kind, spec.section
        if k == "DYNAMIC_HOOK" and sheet is not None:
            out += hook_claims(sid, sheet, resolve_fact, texts)
        elif sec == "SETUP":
            for f, v in _numeric(texts):
                out.append(rc(sid, v, f, nifty_ids, "change in points = close - previous close"
                              if "pts" in v else None))
        elif k == "FLOWS":
            for f, v in _numeric(texts):
                out.append(rc(sid, v, f, flow_ids))
        elif k == "SECTORS":
            for f, v in _numeric(texts):
                name = texts.get(f.rsplit(".", 1)[0] + ".name", "") if f.startswith("rows.") else ""
                ids = ids_for(name) if name else [x for s in brief.sectors
                                                  for x in ids_for(s["name"])]
                out.append(rc(sid, v, f, ids, None if name else
                              "count of the tracked sector indices"))
        elif k == "PRE_VIX" and brief.vix is not None:
            for f, v in _numeric(texts):
                out.append(claim(sid, v, f, ["premarket:india_vix"], [brief.vix.source],
                                 data_as_of=brief.vix.session))
        elif k == "PRE_OVERNIGHT":
            for f, v in _numeric(texts):
                row = f.rsplit(".", 1)[0]
                name = texts.get(row + ".name") or ("GIFT NIFTY" if f.startswith("gift") else "")
                if not name:          # headline / takeaway: the named lead or contrast cue
                    name = next((q for q in quotes if q.split()[0].lower() in v.lower()
                                 or q.title() in v), "")
                if name:
                    out.append(quote_claim(sid, v, f, name))
                else:
                    out.append(claim(sid, v, f, [f"premarket:{q.key}" for q in brief.global_cues
                                                 if q.fresh],
                                     list({q.source for q in brief.global_cues}),
                                     derivation="count of the fresh tracked markets"))
        elif k == "PRE_EVENT":
            for f, v in _numeric(texts):
                out.append(claim(sid, v, f, ["event:" + texts.get("title", "")], event_sources(),
                                 data_as_of=brief.pre_date, derivation="verified schedule"))
        elif k == "PRE_WATCH":
            cats = (spec.data or {}).get("categories") or []
            for f, v in _numeric(texts):
                idx = int(f.split(".")[1]) if f.startswith("items.") else 0
                cat = cats[idx] if idx < len(cats) else ""
                if cat == "NIFTY_LEVEL":
                    out.append(rc(sid, v, f, nifty_ids, "prior-20-session level / day range"))
                elif cat == "SECTOR":
                    out.append(rc(sid, v, f, [x for s in brief.sectors for x in ids_for(s["name"])],
                                  "count of the tracked sector indices"))
                elif cat == "FLOWS":
                    out.append(rc(sid, v, f, flow_ids))
                elif cat == "VIX" and brief.vix is not None:
                    out.append(claim(sid, v, f, ["premarket:india_vix"], [brief.vix.source],
                                     data_as_of=brief.vix.session))
                elif cat in ("GIFT", "OVERNIGHT"):
                    title = texts.get(f"items.{idx}.title", "")
                    name = next((q for q in quotes if title.upper().startswith(q)), "GIFT NIFTY")
                    out.append(quote_claim(sid, v, f, name))
                elif cat == "EVENT":
                    out.append(claim(sid, v, f, ["event"], event_sources(),
                                     derivation="verified schedule"))
                else:
                    out.append(rc(sid, v, f, []))
        elif k in ("STRUCTURE", "EXCHANGE_WATCH", "IPO_WATCH", "IPO_BOARD"):
            out += _public_section_claims(sid, spec, texts, brief.pre_date)
        else:
            for f, v in _numeric(texts):
                out.append(rc(sid, v, f, []))
    return out


def storyboard_scene_texts(sb) -> dict:
    return {f"{i:02d}_{s.kind.lower()}": scene_texts(s) for i, s in enumerate(sb.scenes)
            if s.kind not in NO_CLAIM_KINDS}


# --------------------------------------------------------------------------- legacy POST
def legacy_claims(plan, report, ticker=()) -> tuple:
    """(claims, scene_texts) for the legacy plan scenes + the ticker strip."""
    res = ReportResolver(report)
    session = report.session_date or report.report_date
    nifty_ids = list((report.nifty or {}).get("fact_ids") or [])
    claims, texts_by = [], {}
    for sc in plan.scenes:
        sid = sc.scene_id
        texts = {k[len(sid) + 1:]: v for k, v in sc.public_text().items()}
        texts_by[sid] = texts
        claims += definition_claims(sid, texts)
        for f, v in _numeric(texts):
            ids, deriv = list(sc.source_fact_ids or []), None
            if f.startswith("item["):
                it = sc.items[int(f[5:f.index("]")])]
                ids = list(it.source_fact_ids or []) or ids
                if it.source_insight_ids:
                    deriv = "canonical-history insight (intelligence/, deterministic)"
                if f.endswith(".rank"):
                    deriv = "display order of the scene's rows"
            elif sc.source_insight_ids:
                deriv = ("canonical-history insight (intelligence/, deterministic)"
                         if f != "primary_value" else None)
            if sc.scene_type.value == "NIFTY" and not ids:
                ids = nifty_ids
            claims.append(claim(sid, v, f, ids, res.sources(ids) or
                                ([S.SRC_DERIVED] if deriv else []), data_as_of=session,
                                retrieved_at=res.retrieved_at(ids), derivation=deriv))
    if ticker:
        texts = {f"item[{i}]": " ".join(str(x) for x in item[:2] if x) +
                 (f" {item[2]:+.2f}%" if len(item) > 2 and item[2] is not None else "")
                 for i, item in enumerate(ticker)}
        texts_by["ticker"] = texts
        for i, item in enumerate(ticker):
            name = str(item[0])
            ids = nifty_ids if name.upper() == "NIFTY 50" else res.ids_for(name)
            claims.append(claim("ticker", texts[f"item[{i}]"], f"ticker:{name}", ids,
                                res.sources(ids), data_as_of=session))
    return claims, texts_by


__all__ = ["post_claims", "pre_claims", "legacy_claims", "storyboard_scene_texts"]
