"""GIFT Nifty publication policy: may a GIFT reading be DISPLAYED in a PRE Short?

The NSE IX integration (`providers/gift_nifty.py`) is technically complete, but whether NSE IX
permits its prices to be shown in a public video has NOT been established (see
docs/NSEIX_RIGHTS_REVIEW.md). That the endpoint answers is not permission. So display is behind
an explicit, default-OFF gate:

    GIFT_NIFTY_PUBLICATION_ENABLED   "true" to enable (anything else, or unset: disabled)
    GIFT_NIFTY_PUBLICATION_APPROVAL  a non-empty reference to the written approval/licence
                                     (e.g. "NDAL licence 2026-11-02, ref ...") - required too:
                                     the flag alone never enables display

Behaviour per run mode:

    PUBLICATION (`--mode premarket`)   gate closed -> GIFT is not fetched and not displayed
                                       gate open   -> fetched, displayed if fresh + valid
    SHADOW (`--mode premarket --shadow`) GIFT is always fetched and validated (engineering
                                       evaluation) and fully audited; it is displayed only if
                                       the gate is open - a shadow render is a dress rehearsal of
                                       exactly what publication would show

A withheld reading is removed from the brief (`brief.gift = None`), so no downstream consumer
(overnight scene, watch card, hook sheet) can show it; the reason travels in `brief.gift_policy`.
The gate never blocks PRE: without GIFT the Short simply runs without the strip.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENV_FLAG = "GIFT_NIFTY_PUBLICATION_ENABLED"
ENV_APPROVAL = "GIFT_NIFTY_PUBLICATION_APPROVAL"
POLICY_VERSION = "gift-policy-1.0"
_TRUE = {"1", "true", "yes", "on"}

MODE_PUBLICATION = "PUBLICATION"
MODE_SHADOW = "SHADOW"


@dataclass(frozen=True)
class GiftPolicy:
    publication_allowed: bool
    flag_value: str | None
    approval_reference: str | None
    reason: str
    version: str = POLICY_VERSION

    def to_dict(self) -> dict:
        return {"publication_allowed": self.publication_allowed, "env_flag": ENV_FLAG,
                "flag_value": self.flag_value, "approval_env": ENV_APPROVAL,
                "approval_reference": self.approval_reference, "reason": self.reason,
                "default": False, "version": self.version}


def gift_publication_policy(env=None) -> GiftPolicy:
    """Read the gate at call time (never cached at import). Fail-closed."""
    env = os.environ if env is None else env
    raw = env.get(ENV_FLAG)
    approval = (env.get(ENV_APPROVAL) or "").strip() or None
    if raw is None or raw.strip() == "":
        return GiftPolicy(False, None, approval,
                          f"{ENV_FLAG} is not set - GIFT publication defaults to disabled "
                          "(NSE IX display rights not approved)")
    if raw.strip().lower() not in _TRUE:
        return GiftPolicy(False, raw, approval,
                          f"{ENV_FLAG}={raw!r} - GIFT publication disabled")
    if not approval:
        return GiftPolicy(False, raw, None,
                          f"{ENV_FLAG} is true but {ENV_APPROVAL} is empty - display needs a "
                          "recorded approval/licence reference, so it stays disabled")
    return GiftPolicy(True, raw, approval, f"enabled under approval: {approval}")


def should_fetch(policy: GiftPolicy, shadow: bool) -> bool:
    """Shadow always evaluates GIFT; publication only touches NSE IX once display is approved."""
    return bool(shadow or policy.publication_allowed)


def gift_reading_summary(quote) -> dict | None:
    """The fields a morning GIFT validation needs (contract, expiry, live price, exchange time,
    previous settlement, computed move, exchange change, freshness, verdict) - compact: the raw
    exchange rows (`contracts_seen`) are deliberately not repeated here."""
    if quote is None:
        return None
    p = dict(quote.provenance or {})
    implied, settle = p.get("implied_reference"), quote.reference_value
    reconciles = (None if implied is None or settle is None
                  else abs(float(implied) - float(settle)) <= 0.51)
    return {
        "contract": quote.ticker, "expiry": p.get("contract_expiry"),
        "live_price": quote.value,
        "exchange_timestamp": quote.market_timestamp.isoformat() if quote.market_timestamp else None,
        "retrieved_at": quote.retrieved_at.isoformat() if quote.retrieved_at else None,
        "previous_settlement": settle,
        "settlement_date": quote.reference_date.isoformat() if quote.reference_date else None,
        "calculated_pct": None if quote.change_pct is None else round(quote.change_pct, 4),
        "exchange_day_change": p.get("exchange_day_change"),
        "exchange_pct_change": p.get("exchange_pct_change"),
        "implied_reference": implied, "consistency": p.get("consistency"),
        "reconciles_with_settlement": reconciles,
        "freshness": quote.freshness.value, "freshness_reason": quote.freshness_reason,
        "validation_status": quote.validation_status,
        "validation_reason": quote.validation_reason,
        "vs_canonical_nifty_close_pct": p.get("vs_canonical_nifty_close_pct"),
        "settlement_attempts": p.get("settlement_attempts"),
    }


def gift_audit(acquisition, policy: GiftPolicy, shadow: bool, fetched: bool,
               displayed: bool | None = None) -> dict:
    """The per-run GIFT record: available / valid / allowed / displayed, and why."""
    from providers.premarket import UNUSABLE_VALIDATION
    q = getattr(acquisition, "gift", None)
    status = dict(getattr(acquisition, "gift_status", None) or {})
    available = None if not fetched else q is not None
    valid = None if not fetched else bool(
        q is not None and q.fresh and q.change_pct is not None
        and q.validation_status not in UNUSABLE_VALIDATION)
    if not fetched:
        reason = f"not fetched: {policy.reason}"
    elif not available:
        reason = f"unavailable: {status.get('status')} - {status.get('reason')}"
    elif not valid:
        reason = f"invalid: {status.get('status')} - {status.get('reason')}"
    elif not policy.publication_allowed:
        reason = f"valid but withheld: {policy.reason}"
    else:
        reason = "valid and publication allowed"
    return {"mode": MODE_SHADOW if shadow else MODE_PUBLICATION, "fetched": fetched,
            "gift_data_available": available, "gift_data_valid": valid,
            "gift_publication_allowed": policy.publication_allowed,
            "gift_displayed": displayed, "reason": reason, "status": status,
            "policy": policy.to_dict(), "reading": gift_reading_summary(q)}


__all__ = ["GiftPolicy", "gift_publication_policy", "should_fetch", "gift_audit",
           "gift_reading_summary", "ENV_FLAG", "ENV_APPROVAL", "MODE_PUBLICATION", "MODE_SHADOW",
           "POLICY_VERSION"]
