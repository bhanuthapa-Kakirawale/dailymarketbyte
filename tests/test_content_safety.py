"""Content-safety layer: no network, no Gemini, no naive substring false positives."""
from core.content_safety import (SafetyStatus, classify_text, sanitize_field, scan_publication)

FALLBACK = "No major company-specific news; moved with sector trend."


def _status(text):
    return classify_text(text).status


# --------------------------------------------------------------------- required scenarios
def test_factual_order_is_safe():
    assert _status("Company wins Rs 2,000 crore order") is SafetyStatus.SAFE


def test_attributed_brokerage_rating_is_sanitized():
    result = classify_text("Brokerage initiates BUY rating")
    assert result.status is SafetyStatus.SANITIZED
    assert result.sanitized_text == "Brokerage initiated coverage on the company."


def test_stocks_to_buy_listicle_is_blocked():
    assert _status("Top stocks to buy tomorrow") is SafetyStatus.BLOCKED


def test_should_you_buy_sell_hold_is_blocked_and_not_republished():
    text = "Should you buy, sell or hold Airtel?"
    result = classify_text(text)
    assert result.status is SafetyStatus.BLOCKED
    clean, _ = sanitize_field(text, fallback=FALLBACK)
    assert clean != text and "buy" not in clean.lower()


def test_company_buys_stake_is_safe():
    """'buys' is a conjugated verb, not the bare recommendation word 'buy'."""
    assert _status("Company buys 51% stake in subsidiary") is SafetyStatus.SAFE


def test_promoter_sells_stake_is_safe():
    assert _status("Promoter sells 2% stake") is SafetyStatus.SAFE


def test_strong_buy_with_target_and_stop_loss_is_blocked_and_not_republished():
    text = "Strong BUY with target Rs 500 and stop loss Rs 420"
    result = classify_text(text)
    assert result.status is SafetyStatus.BLOCKED
    clean, _ = sanitize_field(text, fallback=FALLBACK)
    assert clean != text
    assert "buy" not in clean.lower() and "target" not in clean.lower()


def test_guaranteed_return_is_blocked():
    assert _status("Guaranteed 20% return") is SafetyStatus.BLOCKED


# --------------------------------------------------------------------- more false positives
def test_company_buying_equipment_is_safe():
    assert _status("The company is buying new equipment for its factory") is SafetyStatus.SAFE


def test_stock_price_or_shares_language_is_safe():
    assert _status("Shares of the company gained 4% in early trade") is SafetyStatus.SAFE


def test_multibagger_history_still_blocks():
    """Deliberately stricter than context-sensitive: MULTIBAGGER is a flatly forbidden term
    in this channel's own output list, so any occurrence blocks rather than trying to infer
    whether a given usage is a recommendation."""
    assert _status("This stock turned multibagger over five years") is SafetyStatus.BLOCKED


def test_upgrade_and_downgrade_ratings_are_sanitized():
    up = classify_text("Brokerage upgrades XYZ Ltd to BUY")
    down = classify_text("Brokerage downgrades ABC Ltd to SELL rating")
    assert up.status is SafetyStatus.SANITIZED and "revised its rating on" in up.sanitized_text
    assert down.status is SafetyStatus.SANITIZED and "revised its rating on" in down.sanitized_text


def test_literal_stock_to_buy_phrasing_blocks_even_when_attributed():
    """Deliberate precedence: the literal phrase "stock to buy" always blocks outright, even
    inside what looks like an attributed rating sentence, because "top stocks to buy" listicle
    headlines use the identical wording and cannot be told apart by pattern alone. Blocking is
    the safer default when the two categories collide."""
    assert _status("Brokerage upgrades stock to BUY") is SafetyStatus.BLOCKED


def test_maintains_reiterates_retains_are_sanitized_with_correct_grammar():
    """Regression guard for a verb-stemming bug: 'reiterates' must map to the root
    'reiterate', not an over-stripped 'reiterat', or the phrase table lookup misses."""
    for verb, expect in [("maintains", "maintained its rating on"),
                         ("reiterates", "reiterated its rating on"),
                         ("retains", "retained its rating on"),
                         ("assigns", "assigned a rating to")]:
        result = classify_text(f"XYZ Securities {verb} ACCUMULATE rating on the stock")
        assert result.status is SafetyStatus.SANITIZED, verb
        assert result.sanitized_text == f"Brokerage {expect} the company.", verb


def test_empty_and_none_text_is_safe():
    assert _status("") is SafetyStatus.SAFE
    assert classify_text(None).status is SafetyStatus.SAFE


def test_target_price_without_attribution_is_blocked():
    assert _status("Target price raised to Rs 1,800") is SafetyStatus.BLOCKED


def test_stop_loss_alone_is_blocked():
    assert _status("Traders advised to keep stop loss at Rs 100") is SafetyStatus.BLOCKED


def test_bare_rating_word_without_verb_is_blocked():
    assert _status("BUY rating on the stock") is SafetyStatus.BLOCKED


# --------------------------------------------------------------------- sanitize_field
def test_sanitize_field_returns_fallback_on_block():
    clean, result = sanitize_field("Top stocks to buy tomorrow", fallback=FALLBACK)
    assert clean == FALLBACK
    assert result.status is SafetyStatus.BLOCKED


def test_sanitize_field_passes_through_safe_text():
    clean, result = sanitize_field("Company wins Rs 2,000 crore order", fallback=FALLBACK)
    assert clean == "Company wins Rs 2,000 crore order"
    assert result.status is SafetyStatus.SAFE


def test_sanitize_field_uses_neutralized_text_when_sanitized():
    clean, result = sanitize_field("MOFSL initiates BUY rating with target Rs 1,500", fallback=FALLBACK)
    assert clean == "Brokerage initiated coverage on the company."
    assert result.status is SafetyStatus.SANITIZED


# --------------------------------------------------------------------- title / description / event paths
def test_youtube_title_and_description_paths():
    title = "Top stocks to buy tomorrow #shorts"
    desc = "Should you buy, sell or hold this stock? Find out."
    scan = scan_publication({"youtube_title": title, "youtube_description": desc})
    assert scan.status is SafetyStatus.BLOCKED
    assert set(scan.blocked_fields) == {"youtube_title", "youtube_description"}


def test_mover_reason_path_is_covered():
    clean, result = sanitize_field("Brokerage initiates BUY rating with target Rs 1,500",
                                   fallback=FALLBACK)
    assert result.status is SafetyStatus.SANITIZED
    assert "target" not in clean.lower()


def test_event_text_path_is_covered():
    clean, result = sanitize_field("Top stocks to buy tomorrow ahead of results", fallback="")
    assert result.status is SafetyStatus.BLOCKED
    assert clean == ""          # caller drops the event rather than showing a blank card


def test_scan_publication_passes_a_fully_safe_document():
    scan = scan_publication({
        "youtube_title": "Daily Market Byte 21 Sep: Nifty 25,140 (+0.29%)",
        "youtube_description": "Nifty 50 closed higher. FII net bought Rs 1,240 crore.",
        "MoversScene.caption[1]": "STOCK-A +4.8%: Company wins large infrastructure order",
    })
    assert scan.status is SafetyStatus.SAFE
    assert scan.blocked_fields == []


def test_sanitized_output_never_reaches_the_final_scan_as_unsafe():
    """The neutralization template itself must be clean, or the pipeline would loop: a
    sanitized mover reason must pass a subsequent scan_publication check untouched."""
    clean, result = sanitize_field("MOFSL initiates BUY rating with target Rs 1,500", fallback="")
    assert result.status is SafetyStatus.SANITIZED
    scan = scan_publication({"mover:TEST": clean})
    assert scan.status is SafetyStatus.SAFE
