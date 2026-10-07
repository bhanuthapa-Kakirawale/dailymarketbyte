"""The Dynamic Hook Engine under a publication profile.

`restrict_sheet` runs every security-scoped hook fact (Radar stock moves/events/volume, top
gainer/loser) through the SAME gate as the scenes. In PUBLIC_UNREGISTERED they are removed -
with every teaser beat that shows them and every count built from them - BEFORE candidates are
built, so no blocked named-stock fact can become a hook candidate or reach Gemini.

`add_structure_facts` supplies the public alternative: the Market Structure count over its named
universe ("18 NIFTY 200 stocks saw unusual volume."), licensed like any other count fact.
"""
from __future__ import annotations

from hooks.sheet_common import fact

from .classification import ContentClass, Orientation, Origin, PublishableFact, Scope
from .classification import RightsStatus

SECURITY_KINDS = {"STOCK_MOVE", "RADAR_EVENT", "RADAR_VOLUME", "RADAR_RELATIVE", "RADAR_SIGNALS"}
SECURITY_SECTIONS = {"RADAR", "MOVERS"}


def _as_publishable(f, session) -> PublishableFact:
    radar = f.fact_id.startswith("radar.")
    return PublishableFact(
        fact_id=f"hook.{f.fact_id}", text=f.statement, scope=Scope.SECURITY,
        origin=Origin.INTERNAL_ANALYTICS if radar else Origin.MARKET_DATA,
        content_class=ContentClass.TECHNICAL_ANALYSIS if radar else ContentClass.FINANCIAL_STATISTIC,
        orientation=Orientation.HISTORICAL, source_name="daily_byte_derived" if radar else "yahoo_finance",
        data_as_of=session, publication_rights_status=RightsStatus.APPROVED if radar
        else RightsStatus.REVIEW_REQUIRED, security=f.entity, ranking=not radar or "count" in f.kind.lower(),
        section="HOOK")


def restrict_sheet(sheet, gate) -> list:
    """Mutates `sheet` in place; returns the removed fact ids (recorded as blocked in `gate`)."""
    removed = set()
    for f in list(sheet.facts):
        security = f.kind in SECURITY_KINDS or f.fact_id.startswith(("radar.", "mover."))
        if not security:
            continue
        if not gate.admit(_as_publishable(f, sheet.session_date)):
            removed.add(f.fact_id)
    if not removed:
        return []
    sheet.facts = [f for f in sheet.facts if f.fact_id not in removed]
    sheet.beats = [b for b in sheet.beats if not (set(b.fact_ids) & removed)
                   and not b.beat_id.startswith(("RADAR", "TOP_", "VOLUME_SPIKE"))]
    keep = [(s, lab) for s, lab in zip(sheet.sections, sheet.section_labels)
            if s not in SECURITY_SECTIONS]
    sheet.sections = [s for s, _ in keep]
    sheet.section_labels = [lab for _, lab in keep]
    sheet.metadata = dict(sheet.metadata, radar_count=0, publication_profile=gate.profile.value)
    return sorted(removed)


def add_structure_facts(sheet, insights) -> None:
    """One count fact per chosen UNDER THE SURFACE insight that has a hook form."""
    for ins in insights or []:
        if ins.kind != "UNUSUAL_VOLUME":
            continue
        n = int(ins.hero_value.split(" /")[0].split(" of")[0])
        U = ins.universe_label
        sheet.facts.append(fact(
            "structure.unusual", "STRUCTURE_COUNT", U,
            f"{n} {U} stocks traded at least 2x their own 20-session average volume "
            f"({ins.denominator_text}).", f"{n} {U} stocks", value=float(n), aliases=(U,),
            claims={"unusual_volume"}, refs=tuple(ins.metric_keys)))
        sheet.metadata = dict(sheet.metadata, structure_universe=U,
                              structure_denominator=ins.denominator_text)


__all__ = ["restrict_sheet", "add_structure_facts", "SECURITY_KINDS"]
