"""publication_audit.json - the per-video record of what was considered, allowed, blocked and
why, and the final verdict. An upload is impossible without a PASS audit whose recorded video
hash matches the file being uploaded (`require_publication_pass`, called by upload.upload).

    final = PASS only when ALL hold:
        profile is PUBLIC_UNREGISTERED
        every language scan passes (recommendation, IPO recommendation, ranking, forecast,
            security-specific technical analysis, unapproved named security, English only)
        every factual scene shows SOURCE and DATA AS OF        (source_visibility)
        every Market Structure scene shows its universe + denominator (universe_visibility)
        gmp_present is False
        no blocked named security appears anywhere in public text
        every displayed number resolves to approved fact(s) / derivation (displayed_claims)
        every displayed claim's source rights permit production publication
            (publication_rights; PUBLIC_REVIEW_REQUIRED_POLICY, default BLOCK)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

from .claims import check_claims, decision_for
from .disclaimer import strip_registered
from .language import scan_public_text
from .profile import PublicationProfile, uploadable

AUDIT_VERSION = "public-intelligence-1.0"


class PublicationBlocked(RuntimeError):
    """The publication audit did not PASS (or is missing / does not match the artifact)."""


def sha256_file(path: str) -> str | None:
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _visibility(scenes: list) -> tuple:
    source_issues, universe_issues = [], []
    for s in scenes:
        if s.get("requires_provenance"):
            prov = s.get("provenance") or {}
            if not prov.get("source") or not prov.get("data_as_of"):
                source_issues.append(f"{s.get('kind')}: missing SOURCE/DATA AS OF on screen")
        if s.get("universe_required"):
            u = s.get("universe") or {}
            if not u.get("label") or not u.get("denominator_text"):
                universe_issues.append(f"{s.get('kind')}: universe/denominator not visible")
    return source_issues, universe_issues


def build_publication_audit(*, gate, product: str, session_date, public_text: dict,
                            scenes: list, metadata: dict | None = None,
                            market_structure: dict | None = None, ipo: dict | None = None,
                            exchange_watch: dict | None = None, hook: dict | None = None,
                            sources: dict | None = None, video_path: str | None = None,
                            synthetic: bool = False, claims: list | None = None,
                            scene_texts: dict | None = None, omitted_sections: dict | None = None,
                            audio: dict | None = None) -> dict:
    """`public_text`: every on-screen string ({field: text}); `metadata`: title / description /
    tags / thumbnail_text; `scenes`: [{kind, requires_provenance, provenance, universe_required,
    universe}] as the storyboard declared them."""
    metadata = metadata or {}
    texts = dict(public_text)
    for k in ("title", "description", "thumbnail_text"):
        if metadata.get(k):
            texts[f"metadata.{k}"] = strip_registered(metadata[k])
    for i, tag in enumerate(metadata.get("tags") or []):
        texts[f"metadata.tag.{i}"] = tag
    if hook:
        for k in ("curiosity_line", "summary_line", "eyebrow"):
            if hook.get(k):
                texts[f"hook.{k}"] = hook[k]
    approved = set(gate.named_securities())
    scan = scan_public_text(texts, gate.known_securities, approved)
    src_issues, uni_issues = _visibility(scenes)
    gmp_text = any("gmp" in (t or "").lower() or "grey market" in (t or "").lower()
                   or "gray market" in (t or "").lower() for t in texts.values())
    gmp_fact = any("GMP" in f.tags for f in gate.allowed)
    ipo = dict(ipo or {})
    ipo.setdefault("ipo_content_present", False)
    ipo["gmp_present"] = bool(gmp_text or gmp_fact)

    scans = scan.to_dict()
    scans["source_visibility"] = {"passed": not src_issues, "issues": src_issues}
    scans["universe_visibility"] = {"passed": not uni_issues, "issues": uni_issues}
    scans["gmp_absent"] = {"passed": not ipo["gmp_present"],
                           "issues": ["GMP content present"] if ipo["gmp_present"] else []}
    # every displayed number resolves to approved fact(s) or an approved derivation
    displayed = [dict(c) for c in (claims or [])]
    for i, c in enumerate(displayed, start=1):
        c["claim_id"] = f"C{i:03d}.{c['scene_id']}"
        c["publication_decision"] = decision_for(c["publication_rights_status"])
        c.pop("numbers", None)
    claim_issues = (check_claims(scene_texts, claims or []) if scene_texts is not None
                    else ["no displayed-claim trace supplied"])
    scans["displayed_claims"] = {"passed": not claim_issues, "issues": claim_issues[:50]}
    blocked_rights = [c for c in displayed if c["publication_decision"].startswith("BLOCKED")]
    by_source = {}
    for c in blocked_rights:
        key = f"{c['publication_decision']} ({', '.join(c['source_name']) or 'no source'})"
        by_source[key] = by_source.get(key, 0) + 1
    scans["publication_rights"] = {
        "passed": not blocked_rights,
        "issues": [f"{n} displayed claim(s): {k}" for k, n in sorted(by_source.items())]}
    profile_ok = uploadable(gate.profile)
    scans["profile"] = {"passed": profile_ok,
                        "issues": [] if profile_ok else [f"profile {gate.profile.value} is never "
                                                         "published"]}
    failed = sorted(k for k, v in scans.items() if not v["passed"])
    final = "PASS" if not failed else "BLOCK"

    g = gate.to_dict()
    allowed = gate.allowed
    audit = {
        "audit_version": AUDIT_VERSION, "product": product,
        "publication_profile": gate.profile.value,
        "session_date": session_date.isoformat() if hasattr(session_date, "isoformat") else session_date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "synthetic": bool(synthetic),
        "facts_considered": g["facts_considered"], "facts_allowed": g["facts_allowed"],
        "facts_blocked": g["facts_blocked"], "block_reasons": g["block_reasons"],
        "blocked": [{"fact_id": b["fact_id"], "scope": b["scope"], "security": b["security"],
                     "section": b["section"], "reasons": b["reasons"]} for b in g["blocked"]],
        "named_securities": sorted(approved),
        "why_each_named_security_is_allowed": g["named_securities"],
        "market_structure": market_structure or {"present": False},
        "sources": sorted({f.source_label or f.source_name for f in allowed}),
        "source_references": sorted({f.source_reference or f.official_document_reference or ""
                                     for f in allowed} - {""}),
        "data_as_of": sorted({str(f.data_as_of) for f in allowed if f.data_as_of}),
        "retrieved_at": sorted({str(f.retrieved_at) for f in allowed if f.retrieved_at}),
        "publication_rights_status": {f.source_name: f.publication_rights_status.value
                                      for f in allowed},
        "rights_review_required": sorted({f.source_name for f in allowed
                                          if f.publication_rights_status.value == "REVIEW_REQUIRED"}),
        "exchange_watch": exchange_watch or {"present": False},
        "ipo": ipo,
        "gemini": {"used": bool(hook and hook.get("source", "").startswith("GEMINI")),
                   "candidate_set": (hook or {}).get("candidates") or [],
                   "final_selected_claims": {k: (hook or {}).get(k) for k in
                                             ("candidate_id", "archetype", "curiosity_line",
                                              "summary_line", "source")}},
        "displayed_claims": displayed,
        "optional_sections": omitted_sections or {},
        "audio": audio or {"audio_stream": False, "audio_phase_enabled": False},
        "rights_policy": {"PUBLIC_REVIEW_REQUIRED_POLICY": _policy_name()},
        "scans": scans, "failed_checks": failed,
        "metadata": {k: metadata.get(k) for k in ("title", "tags", "thumbnail_text")},
        "source_registry": sources or {},
        "artifact": {"video_path": video_path, "sha256": sha256_file(video_path)},
        "final": final,
    }
    return audit


def _policy_name() -> str:
    from .rights import review_required_policy
    return review_required_policy()


def content_checks_passed(audit: dict) -> bool:
    """Everything but the rights decision passed - the render itself is compliant (PRE and
    no-upload runs proceed; production publication still needs `final == PASS`)."""
    return not (set(audit.get("failed_checks") or []) - {"publication_rights"})


def write_publication_audit(audit: dict, out_dir: str, name: str = "publication_audit.json") -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, indent=2, ensure_ascii=False, default=str)
    return path


def require_publication_pass(audit, video_path: str) -> dict:
    """The upload hard-block. Raises PublicationBlocked unless the audit exists, says PASS,
    is for the public profile, and was computed over THIS video file (sha256)."""
    if audit is None:
        raise PublicationBlocked("no publication audit - upload refused")
    if isinstance(audit, str):
        if not os.path.exists(audit):
            raise PublicationBlocked(f"publication audit not found: {audit}")
        with open(audit, encoding="utf-8") as fh:
            audit = json.load(fh)
    if audit.get("final") != "PASS":
        raise PublicationBlocked("publication audit BLOCK: " + ", ".join(audit.get("failed_checks") or []))
    if audit.get("publication_profile") != PublicationProfile.PUBLIC_UNREGISTERED.value:
        raise PublicationBlocked(f"profile {audit.get('publication_profile')} is never uploaded")
    if audit.get("synthetic"):
        raise PublicationBlocked("synthetic content is never uploaded")
    want = (audit.get("artifact") or {}).get("sha256")
    have = sha256_file(video_path)
    if not want or want != have:
        raise PublicationBlocked("publication audit does not match this video file")
    return audit


__all__ = ["build_publication_audit", "write_publication_audit", "require_publication_pass",
           "content_checks_passed",
           "PublicationBlocked", "sha256_file", "AUDIT_VERSION"]
