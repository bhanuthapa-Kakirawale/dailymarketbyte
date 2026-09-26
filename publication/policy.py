"""The publication policy: one deterministic verdict per `PublishableFact` per profile.

PUBLIC_UNREGISTERED rules, in order (every block carries a reason code):

    AI_NOT_A_SOURCE                 origin AI - Gemini may choose/shorten approved candidates,
                                    it is never the source of a published fact
    NEWS_NOT_A_SOURCE               a third-party headline is discovery, not a published fact
    RECOMMENDATION / OPINION        content classes that are never published
    FORWARD_LOOKING                 forecasts of any scope (a SCHEDULED_EVENT is not a forecast)
    SECURITY_TECHNICAL_ANALYSIS     technical analysis of a named security or IPO - removing the
                                    price does not make it safe
    SECURITY_RANKING                named securities placed in an order (top gainer, "watch")
    SECURITY_WITHOUT_OFFICIAL_EVENT a named security needs an OFFICIAL exchange/regulator/company
                                    event (exchange action, corporate event) - market data or
                                    our own analytics alone never name one
    OFFICIAL_REFERENCE_MISSING      an official fact must carry its source reference
    IPO_UNOFFICIAL_SOURCE           IPO facts come from SEBI offer documents / the exchange only
    GMP                             grey-market premium is blocked outright in V1
    UNIVERSE_MISSING                a market-structure aggregate must name its exact universe
    DATA_AS_OF_MISSING              a viewer must be able to see what date/time a fact represents
    RIGHTS_*                        RESTRICTED / UNKNOWN sources; REVIEW_REQUIRED per the
                                    configured rights policy (publication.rights)
    LANGUAGE_*                      the fact's own text fails the public language scan

There is no disclaimer parameter anywhere in this module: a disclaimer cannot turn a BLOCK into
an ALLOW. PRIVATE_ANALYTICS allows everything (private output is never uploaded).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .classification import (OFFICIAL_ORIGINS, ContentClass, Orientation, Origin,
                             PublishableFact, RightsStatus, Scope)
from .language import scan_public_text
from .profile import PublicationProfile, resolve_profile
from .rights import ATTRIBUTED_EOD, review_required_policy

NAMED_SCOPES = (Scope.SECURITY, Scope.IPO)


@dataclass(frozen=True)
class PolicyDecision:
    fact_id: str
    allowed: bool
    reasons: tuple = ()
    notes: tuple = ()          # allowed-with-conditions (e.g. rights review required)

    def to_dict(self) -> dict:
        return {"fact_id": self.fact_id, "allowed": self.allowed, "reasons": list(self.reasons),
                "notes": list(self.notes)}


@dataclass
class _R:
    reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _rules(f: PublishableFact, known_securities) -> _R:
    r = _R()
    block = r.reasons.append
    if f.origin is Origin.AI:
        block("AI_NOT_A_SOURCE")
    if f.origin is Origin.NEWS:
        block("NEWS_NOT_A_SOURCE")
    if f.content_class is ContentClass.RECOMMENDATION:
        block("RECOMMENDATION")
    if f.content_class is ContentClass.OPINION:
        block("OPINION")
    if f.orientation is Orientation.FORWARD_LOOKING:
        block("FORWARD_LOOKING")
    if f.scope in NAMED_SCOPES and f.content_class is ContentClass.TECHNICAL_ANALYSIS:
        block("SECURITY_TECHNICAL_ANALYSIS")
    if f.scope in NAMED_SCOPES and f.ranking:
        block("SECURITY_RANKING")
    if f.scope is Scope.SECURITY:
        if f.origin not in OFFICIAL_ORIGINS or f.content_class not in (
                ContentClass.EXCHANGE_EVENT, ContentClass.CORPORATE_EVENT):
            block("SECURITY_WITHOUT_OFFICIAL_EVENT")
    if f.scope is Scope.IPO:
        if f.origin not in OFFICIAL_ORIGINS:
            block("IPO_UNOFFICIAL_SOURCE")
        if f.content_class not in (ContentClass.IPO_EVENT, ContentClass.FINANCIAL_STATISTIC):
            block("IPO_CONTENT_CLASS")
    if f.origin in OFFICIAL_ORIGINS and not (f.source_reference or f.official_document_reference):
        block("OFFICIAL_REFERENCE_MISSING")
    if "GMP" in f.tags:
        block("GMP")
    counts_stocks = "MARKET_STRUCTURE" in f.tags or (
        f.scope is Scope.MARKET and f.origin is Origin.INTERNAL_ANALYTICS
        and f.content_class is ContentClass.MARKET_AGGREGATE)
    if counts_stocks and not f.universe:
        block("UNIVERSE_MISSING")
    if f.data_as_of in (None, ""):
        block("DATA_AS_OF_MISSING")
    st = f.publication_rights_status
    if st is RightsStatus.RESTRICTED:
        block("RIGHTS_RESTRICTED")
    elif st is RightsStatus.UNKNOWN:
        block("RIGHTS_UNKNOWN")
    elif st is RightsStatus.REVIEW_REQUIRED:
        if review_required_policy() == ATTRIBUTED_EOD and "LIVE" not in f.tags:
            r.notes.append("RIGHTS_REVIEW_REQUIRED: published with visible attribution under "
                           "the ATTRIBUTED_EOD policy")
        else:
            block("RIGHTS_REVIEW_REQUIRED")
    approved = {f.security} if f.security and not r.reasons else set()
    scan = scan_public_text({f.fact_id: f.text}, known_securities, approved,
                            ipo_context=True)
    for check, issues in scan.issues.items():
        if issues:
            block("LANGUAGE_" + check.upper())
    return r


def evaluate(fact: PublishableFact, profile=None, known_securities=None) -> PolicyDecision:
    profile = resolve_profile(profile)
    if profile is PublicationProfile.PRIVATE_ANALYTICS:
        return PolicyDecision(fact.fact_id, True, (), ("PRIVATE_ANALYTICS: not for publication",))
    r = _rules(fact, known_securities)
    reasons = tuple(dict.fromkeys(r.reasons))
    return PolicyDecision(fact.fact_id, not reasons, reasons, tuple(r.notes))


__all__ = ["PolicyDecision", "evaluate", "NAMED_SCOPES"]
