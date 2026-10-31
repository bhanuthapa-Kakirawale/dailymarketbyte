"""Regression for the 2026-10-30 POST displayed_claims BLOCK: the post-breadth-divergence hook's
BREADTH_MOVE fact (`structure.divergence`, "133 of 200") cites Market Structure metric keys
(ADVANCES / DECLINES), not report fact ids, and the POST claim resolver had no branch for it -
"00_dynamic_hook: claim '133 of 200' resolves to no fact and no approved derivation".

BREADTH_MOVE now shares STRUCTURE_COUNT's resolution, and resolves only when every referenced
metric is PUBLISHABLE in the session's own snapshot and the displayed numbers are those metrics'
own counts. STRUCTURE_COUNT behaviour is unchanged."""
import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from daily_video.public_storyboard import audit_storyboard
from daily_video.storyboard import SceneSpec
from hooks.sheet_common import count_fact, fact, session_fact
from operations.daily_check import audit_verdict
from products import public_fixtures as PF
from publication.audit import content_checks_passed
from publication.claims import check_claims
from publication.public_hooks import SECURITY_KINDS
from publication.scene_claims import STRUCTURE_SOURCES, post_claims
from test_public_publication import NEXT, PREV, SESSION, _obs, _post

ROOT = Path(__file__).resolve().parents[1]
ADV = f"market_structure:{SESSION}:ADVANCES"
DEC = f"market_structure:{SESSION}:DECLINES"


# --------------------------------------------------------------------------- builders
def _snap(**status):
    """The DIVERGENCE fixture snapshot (ADVANCES 70/200, DECLINES 130/200, both PUBLISHABLE),
    optionally with metric statuses overridden or metrics removed (status=None)."""
    snap, _, _ = PF.structure(SESSION, PREV, "DIVERGENCE")
    metrics = dict(snap.metrics)
    for key, st in status.items():
        if st is None:
            metrics.pop(key, None)
        else:
            metrics[key] = dataclasses.replace(metrics[key], status=st)
    return dataclasses.replace(snap, metrics=metrics)


def _breadth(display="130 of 200", refs=("ADVANCES", "DECLINES"), n=130, cov=200):
    """`structure.divergence` exactly as publication.public_hooks._add_divergence builds it."""
    return fact("structure.divergence", "BREADTH_MOVE", "NIFTY 200",
                f"{n} of {cov} NIFTY 200 stocks closed lower on the session, while Nifty 50 "
                f"closed higher ({n} of {cov}).", display, value=float(n), polarity=-1,
                aliases=("NIFTY 200",), claims={"divergence"}, refs=refs,
                extra_numbers=(str(cov),))


def _sheet(*facts):
    by_id = {f.fact_id: f for f in facts}
    return NS(facts=list(facts), beats=[], fact=by_id.get)


def _hook_sb(*lines):
    spec = SceneSpec(kind="DYNAMIC_HOOK", section="HOOK", duration=4.5,
                     texts={f"line.{i}": t for i, t in enumerate(lines)})
    return NS(session_date=SESSION, scenes=[spec])


def _resolve(f, line=None, report=None, structure=None):
    """(claims, check_claims issues) for one hook fact shown on one hook line."""
    sb = _hook_sb(line or f.display)
    claims = post_claims(sb, report, _sheet(f), structure=structure)
    texts = {"00_dynamic_hook": {"line.0": line or f.display}}
    return claims, check_claims(texts, claims)


def _unresolved(issues, text):
    return f"00_dynamic_hook: claim {text!r} resolves to no fact and no approved derivation" \
        in issues


# --------------------------------------------------------------------------- A. positive
def test_a_breadth_move_resolves_to_the_sessions_publishable_structure_metrics():
    claims, issues = _resolve(_breadth(), structure=_snap())
    (c,) = [c for c in claims if c["metric"] == "breadth_move"]
    assert c["fact_ids"] == [ADV, DEC]                      # session-specific metric ids
    assert c["source_name"] == list(STRUCTURE_SOURCES)
    assert c["derivation"] == "count over the named universe"
    assert c["publication_rights_status"] == "REVIEW_REQUIRED"   # rights unchanged: still gated
    assert issues == []


def test_a_storyboard_passes_the_snapshot_to_the_claim_builder():
    sb = _post(structure_scenario="DIVERGENCE", pct=0.42)
    (c,) = [c for c in sb.claims if c["metric"] == "breadth_move"]
    assert c["visible_text"] == "130 of 200" and c["fact_ids"] == [ADV, DEC]


# --------------------------------------------------------------------------- B. empty refs
def test_b_breadth_move_with_no_source_refs_stays_unresolved():
    claims, issues = _resolve(_breadth(refs=()), structure=_snap())
    (c,) = [c for c in claims if c["metric"] == "breadth_move"]
    assert c["fact_ids"] == [] and c["derivation"] is None
    assert _unresolved(issues, "130 of 200")


# --------------------------------------------------------------------------- C. invalid refs
@pytest.mark.parametrize("why,f,snap", [
    ("no snapshot", _breadth(), None),
    ("metric missing from the snapshot", _breadth(), _snap(DECLINES=None)),
    ("unknown metric key", _breadth(refs=("ADVANCES", "NOT_A_METRIC")), _snap()),
    ("PARTIAL is not PUBLISHABLE", _breadth(), _snap(DECLINES="PARTIAL")),
    ("SUPPRESSED is not PUBLISHABLE", _breadth(), _snap(ADVANCES="SUPPRESSED")),
    ("SUPPRESSED-only metric referenced", _breadth(refs=("NEW_52W_HIGH",)), _snap()),
    ("another session's snapshot", _breadth(),
     dataclasses.replace(_snap(), session_date=str(PREV))),
    ("displayed count the snapshot does not hold", _breadth("140 of 200", n=140), _snap()),
    ("displayed denominator the snapshot does not hold", _breadth("130 of 210", cov=210), _snap()),
])
def test_c_breadth_move_without_publishable_evidence_is_never_approved(why, f, snap):
    claims, issues = _resolve(f, structure=snap)
    (c,) = [c for c in claims if c["metric"] == "breadth_move"]
    assert c["fact_ids"] == [] and c["derivation"] is None, why
    assert _unresolved(issues, f.display), why


def test_c_structure_count_resolution_is_unchanged():
    """STRUCTURE_COUNT keeps its pre-fix behaviour exactly (no snapshot needed)."""
    f = fact("structure.unusual", "STRUCTURE_COUNT", "NIFTY 200",
             "18 NIFTY 200 stocks traded at least 2x their own 20-session average volume "
             "(18 / 200).", "18 NIFTY 200 stocks", value=18.0, aliases=("NIFTY 200",),
             claims={"unusual_volume"}, refs=("UNUSUAL_VOLUME",))
    for snap in (None, _snap(), _snap(UNUSUAL_VOLUME="PARTIAL")):
        claims, issues = _resolve(f, structure=snap)
        (c,) = [c for c in claims if c["metric"] == "structure_count"]
        assert c["fact_ids"] == [f"market_structure:{SESSION}:UNUSUAL_VOLUME"]
        assert c["source_name"] == list(STRUCTURE_SOURCES)
        assert c["derivation"] == "count over the named universe" and issues == []


# --------------------------------------------------------------------------- D. POST integration
def test_d_breadth_divergence_hook_is_rights_block_only_not_content_failure(monkeypatch):
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)   # default: BLOCK
    sb = _post(structure_scenario="DIVERGENCE", pct=0.42)
    assert sb.hook_plan["candidate_id"] == "post-breadth-divergence"
    hook_text = " ".join(v for k, v in sb.public_text().items() if k.startswith("00_"))
    assert "130" in hook_text and "200" in hook_text                # the breadth claim is shown

    audit = audit_storyboard(sb, "POST_UNIFIED")
    hook = [c for c in audit["displayed_claims"] if c["metric"] == "breadth_move"]
    assert hook and all(c["fact_ids"] == [ADV, DEC] for c in hook)
    assert audit["scans"]["displayed_claims"] == {"passed": True, "issues": []}
    # rights are untouched: the REVIEW_REQUIRED sources still block production publication
    assert audit["scans"]["publication_rights"]["passed"] is False
    assert all(c["publication_decision"] == "BLOCKED_RIGHTS_REVIEW_REQUIRED" for c in hook)
    assert audit["failed_checks"] == ["publication_rights"] and audit["final"] == "BLOCK"
    assert audit_verdict(audit["final"], audit["failed_checks"]) == "RIGHTS_BLOCK_ONLY"
    assert content_checks_passed(audit)


def test_d_without_the_snapshot_the_same_hook_still_blocks_as_content(monkeypatch):
    """The guard fails closed: with no snapshot to check against, the hook's claim is unresolved
    and the audit is a CONTENT_FAILURE - exactly the pre-fix outcome, never a silent pass."""
    import publication.scene_claims as scm
    real = scm.post_claims
    monkeypatch.setattr(scm, "post_claims",
                        lambda sb, report, sheet=None, structure=None: real(sb, report, sheet))
    sb = _post(structure_scenario="DIVERGENCE", pct=0.42)
    assert sb.hook_plan["candidate_id"] == "post-breadth-divergence"
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert any("resolves to no fact and no approved derivation" in i
               for i in audit["scans"]["displayed_claims"]["issues"])
    assert audit_verdict(audit["final"], audit["failed_checks"]) == "CONTENT_FAILURE"


# --------------------------------------------------------------------------- E. resolver coverage
class _Report:
    """Canonical facts for every kind the POST hook sheet can resolve."""

    def __init__(self):
        self.session_date, self.report_date = SESSION, NEXT
        self.nifty = {"fact_ids": ["f.nifty"]}
        self.sectors = [{"name": "Metal", "fact_id": "f.metal"}]
        self.institutional_flows = {"fact_ids": ["f.fii", "f.dii"]}
        self.global_cues = [{"label": "Dow Jones", "fact_id": "f.dow"}]
        self.events = []
        self.facts = [NS(fact_id="f.bank", instrument="BANK NIFTY"),
                      NS(fact_id="f.vix", instrument="INDIA VIX")]
        self._ids = {"f.nifty", "f.metal", "f.fii", "f.dii", "f.dow", "f.bank", "f.vix"}

    def fact(self, fid):
        return NS(observations=[_obs()]) if fid in self._ids else None


# kind -> (fact, expected fact ids or None for a derivation-only claim)
SUPPORTED = {
    "SESSION": (session_fact(SESSION), None),
    "INDEX_MOVE": (fact("nifty.move", "INDEX_MOVE", "NIFTY 50", "Nifty 50 closed +0.42%.",
                        "+0.42%", value=0.42), ["f.nifty"]),
    "INDEX_LEVEL": (fact("nifty.close", "INDEX_LEVEL", "NIFTY 50", "Nifty 50 at 24,100.50.",
                         "24,100.50", value=24100.5), ["f.nifty"]),
    "INDEX_CONTEXT": (fact("nifty.context", "INDEX_CONTEXT", "NIFTY 50", "Nifty 50: 0.4% "
                           "below its 20-day high.", "0.4% below its 20-day high"), ["f.nifty"]),
    "INDEX_RANK": (fact("nifty.rank20", "INDEX_RANK", "NIFTY 50", "Bigger than 17 of the last "
                        "20 sessions.", "Bigger than 17 of the last 20 sessions",
                        refs=("ins.index-move-20",)), ["f.nifty"]),
    "VIX": (fact("vix.level", "VIX", "INDIA VIX", "India VIX at 13.20.", "13.20", value=13.2),
            ["f.vix"]),
    "SECTOR_MOVE": (fact("sector.metal", "SECTOR_MOVE", "Metal", "Nifty Metal index +1.20%.",
                         "+1.20%", value=1.2), ["f.metal"]),
    "COUNT": (count_fact("sectors.count", 12, "tracked sector indices",
                         "The report tracks 12 sector indices."), ["f.metal"]),
    "FLOW": (fact("flows.fii", "FLOW", "FII", "FIIs net sold -Rs 6,200 cr.", "-Rs 6,200 cr",
                  value=-6200.0), ["f.fii"]),
    "GLOBAL_CUE": (fact("global.dow", "GLOBAL_CUE", "Dow Jones", "Dow Jones +0.31% overnight.",
                        "+0.31%", value=0.31), ["f.dow"]),
    "STRUCTURE_COUNT": (fact("structure.unusual", "STRUCTURE_COUNT", "NIFTY 200",
                             "18 NIFTY 200 stocks.", "18 NIFTY 200 stocks", value=18.0,
                             refs=("UNUSUAL_VOLUME",)),
                        [f"market_structure:{SESSION}:UNUSUAL_VOLUME"]),
    "BREADTH_MOVE": (_breadth(), [ADV, DEC]),
}


@pytest.mark.parametrize("kind", sorted(SUPPORTED))
def test_e_every_supported_post_hook_kind_resolves(kind):
    f, want = SUPPORTED[kind]
    assert f.kind == kind
    claims, issues = _resolve(f, report=_Report(), structure=_snap())
    (c,) = [c for c in claims if c["metric"] == kind.lower()]
    if want is None:
        assert c["fact_ids"] == [] and c["derivation"], kind
    else:
        assert c["fact_ids"] == want, (kind, c["fact_ids"])
    assert issues == [], (kind, issues)


@pytest.mark.parametrize("f", [
    fact("x.made_up", "MADE_UP_KIND", "SOMETHING UNKNOWN", "Something +4.20%.", "+4.20%"),
    fact("x.vix", "VIX", "SOME OTHER VIX", "Some other VIX at 21.70.", "21.70"),
    fact("x.stock", "STOCK_MOVE", "STOCK-Z", "STOCK-Z +9.10%.", "+9.10%"),
    fact("x.breadth", "UNKNOWN_BREADTH", "NIFTY 200", "130 of 200.", "130 of 200",
         refs=("ADVANCES", "DECLINES")),           # Market Structure refs alone approve nothing
], ids=lambda f: f.kind)
def test_e_unknown_or_unbacked_kinds_stay_rejected(f):
    claims, issues = _resolve(f, report=_Report(), structure=_snap())
    assert _unresolved(issues, f.display), (f.kind, claims)


def test_e_every_kind_the_post_builders_emit_is_classified():
    """A new hook fact kind forces a decision here: it either resolves (SUPPORTED) or is a
    security-scoped kind that restrict_sheet removes from every public render."""
    pat = re.compile(r'\bfact\(\s*[^,\n]+?,\s*"([A-Z_]+)"')
    emitted = set()
    for rel in ("hooks/sheet_post.py", "hooks/sheet_common.py", "publication/public_hooks.py"):
        emitted |= set(pat.findall((ROOT / rel).read_text(encoding="utf-8")))
    assert {"BREADTH_MOVE", "STRUCTURE_COUNT", "INDEX_MOVE", "SESSION"} <= emitted
    assert emitted - set(SUPPORTED) <= SECURITY_KINDS, emitted - set(SUPPORTED) - SECURITY_KINDS


# --------------------------------------------------------------------------- PRE boundary
def test_pre_hook_sheet_never_carries_market_structure_facts():
    """pre_claims has no Market Structure branch (a "NIFTY 200" entity would fall to its
    NIFTY-name lookup), so PRE must never be handed one: its sheet builder emits none and
    only the POST storyboard calls add_structure_facts."""
    src = (ROOT / "hooks/sheet_pre.py").read_text(encoding="utf-8")
    assert "BREADTH_MOVE" not in src and "STRUCTURE_COUNT" not in src
    assert "add_structure_facts" not in (ROOT / "daily_video/pre_storyboard.py").read_text(
        encoding="utf-8")
