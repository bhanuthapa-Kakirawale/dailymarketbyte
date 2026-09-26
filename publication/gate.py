"""The publication gate: every candidate fact is classified and judged BEFORE a storyboard is
built. Scenes are then made only from admitted facts - the renderer never decides compliance.

    gate = PublicationGate(profile)            # default: PUBLIC_UNREGISTERED
    if gate.admit(fact): ...build the scene...
    gate.to_dict()                             # facts considered / allowed / blocked + reasons

The gate is also where a named security's public justification is recorded: every SECURITY /
IPO fact that is admitted carries the official event that allowed it
(`named_securities` -> {name: why}). A security the Radar merely selected never reaches it.
"""
from __future__ import annotations

from .classification import PublishableFact, Scope
from .policy import NAMED_SCOPES, evaluate
from .profile import PublicationProfile, resolve_profile


class PublicationGate:
    def __init__(self, profile=None, known_securities=None):
        self.profile: PublicationProfile = resolve_profile(profile)
        self.known_securities = dict(known_securities or {})
        self.records: list = []       # [(fact, decision)]

    @property
    def public(self) -> bool:
        return self.profile.is_public

    def admit(self, fact: PublishableFact) -> bool:
        decision = evaluate(fact, self.profile, self.known_securities)
        self.records.append((fact, decision))
        return decision.allowed

    def filter(self, facts) -> list:
        return [f for f in facts if self.admit(f)]

    # ------------------------------------------------------------------ views
    @property
    def allowed(self) -> list:
        return [f for f, d in self.records if d.allowed]

    @property
    def blocked(self) -> list:
        return [(f, d) for f, d in self.records if not d.allowed]

    def named_securities(self) -> dict:
        """{security: why it may be named} for every ADMITTED security/IPO fact."""
        out = {}
        for f, d in self.records:
            if d.allowed and f.scope in NAMED_SCOPES and f.security:
                why = (f"{f.content_class.value} from {f.source_label or f.source_name} "
                       f"({f.source_reference or f.official_document_reference})")
                out.setdefault(f.security, [])
                if why not in out[f.security]:
                    out[f.security].append(why)
        return out

    def block_reasons(self) -> dict:
        out = {}
        for _, d in self.blocked:
            for r in d.reasons:
                out[r] = out.get(r, 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict:
        return {
            "publication_profile": self.profile.value,
            "facts_considered": len(self.records),
            "facts_allowed": len(self.allowed),
            "facts_blocked": len(self.blocked),
            "block_reasons": self.block_reasons(),
            "allowed": [dict(f.to_dict(), notes=list(d.notes)) for f, d in self.records if d.allowed],
            "blocked": [dict(f.to_dict(), reasons=list(d.reasons)) for f, d in self.blocked],
            "named_securities": self.named_securities(),
        }


def private_gate(known_securities=None) -> PublicationGate:
    return PublicationGate(PublicationProfile.PRIVATE_ANALYTICS, known_securities)


__all__ = ["PublicationGate", "private_gate", "Scope"]
