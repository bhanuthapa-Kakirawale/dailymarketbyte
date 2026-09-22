# Content safety (Phase 1.1)

`core/content_safety.py` prevents external news/recommendation language from being
republished as if it were this channel's own recommendation. It is a small, deterministic
patch on top of Phase 1 - no architecture change, no provider change.

## Why deterministic, not Gemini

The guarantee this module exists to provide - "recommendation language cannot reach the
video" - must hold even when the AI layer is completely unavailable (see the Gemini 404/503
and 429 known limitations in `CLAUDE.md`). An LLM judging its own output is also not a safety
boundary a viewer can rely on. Every rule is a plain `re` pattern; nothing here makes a
network call.

## Three outcomes, in priority order

1. **BLOCKED, no rewrite attempted** - phrases with no reliable factual core to extract
   ("stocks to buy", "should you buy", "guaranteed return", bare "strong BUY", "multibagger").
   Rewriting these risks manufacturing a plausible sentence that still carries the
   recommendation, so refusing to publish is the only safe option.
2. **SANITIZED** - an attributed third-party rating ("MOFSL initiates BUY rating with target
   Rs 1,500") is neutralized to a fixed template: `"Brokerage {verb phrase} the company."`
   (e.g. "Brokerage initiated coverage on the company."). This keeps the fact - a rating event
   happened - and drops the direction and number, which read as this channel's own call if
   left in.
3. **BLOCKED, unattributed actionable language** ("target price", "stop loss") - no brokerage
   to attribute it to, so no safe factual rewrite exists.

Anything matching none of the above is **SAFE** and passes through unchanged.

### Avoiding false positives

All patterns are `\b`-bounded regexes matching specific phrasings, not bare substring checks
on "buy"/"sell"/"hold". `\bbuy\b` does not match "buys", "buying" or "bought", which is why
"Company buys 51% stake" and "Promoter sells 2% stake" are SAFE - those are conjugated
corporate-action verbs, not the bare recommendation word. The attributed-rating pattern's
optional company reference is a closed whitelist (`stock`/`shares`/`company`, or an ALL-CAPS
ticker-style token matched case-sensitively even inside the otherwise case-insensitive
pattern) rather than a generic wildcard, specifically so it cannot turn an unrelated sentence
like "upgrades its systems to buy new equipment" into a fabricated brokerage-rating claim.

### A deliberate precedence choice

The literal phrase "stock to buy" always blocks outright (category 1), even inside what looks
like an attributed rating sentence such as "Brokerage upgrades stock to BUY" - "top stocks to
buy" listicle headlines use identical wording and the two cannot be told apart by pattern
alone. When the categories collide, BLOCKED is the safer default.

## Where it runs

**Before publication** (`main.apply_content_safety`, called right after `nifty_reason`/
`gainers`/`losers`/`events` are obtained, before any scene or caption is built): every mover
reason, the Nifty move summary, and every event's text is sanitized. BLOCKED text is replaced
with a neutral fallback - the existing "no verified catalyst" text for a mover, or the event
is dropped entirely (a scheduled event with a blanked-out description is not useful; a
missing one is). This is what stops a raw Google News headline from ever reaching
`MoversScene`, `EventsScene`, or `build_metadata`'s title/description.

**Immediately before upload** (`scan_publication`, called after the video is rendered and
metadata is written): every finalized public-facing string is re-scanned - all scene
captions (via each scene's own `.captions()`, since `MoversScene` builds its per-row caption
text dynamically rather than storing it on `.texts`), event card text, and the YouTube title
and description. By this point text is already burned into frames or written to disk, so
nothing is rewritten here: **any non-SAFE result blocks upload**. `args.upload` is forced
`False` and local artifacts (mp4, JSON, report) are left in place for inspection. A non-upload
run just prints the failure and continues, since there is no publication to block.

## Audit trail

`MarketReport.content_safety` (schema 1.1) records:

```json
{
  "status": "SAFE" | "SANITIZED" | "BLOCKED",
  "sanitized_count": 1,
  "blocked_count": 2,
  "findings": [{"field": "mover:SOLARINDS", "status": "SANITIZED",
                "original_text": "...", "sanitized_text": "...", "matched_rules": [...]}],
  "final_scan": {"status": "SAFE", "blocked_fields": []},
  "version": "1.0"
}
```

## Known limitation

Headlines phrased as "upgrades <company name> to BUY" without a ticker-style token or one of
the whitelisted nouns ("stock"/"shares"/"company") are not recognised by the sanitize path and
fall through as SAFE. Extending the whitelist further trades recall for false-positive risk
(see "Avoiding false positives" above); this is deferred rather than solved with a generic
wildcard.
