"""Every threshold, word list and limit the hook engine uses, in one place.

Word lists are deliberately blunt. A hook that trips one of them is not rewritten - it is
rejected and the deterministic fallback opens the Short instead. A false rejection costs a
slightly plainer opening; a false acceptance costs a misleading one.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- timing
# 2-3 beats, then the hook settles and holds long enough to be read as a still frame.
BEAT_SECONDS = {2: 0.95, 3: 0.85}
SETTLE_SECONDS = {2: 2.3, 3: 2.05}
MAX_TOTAL_SECONDS = 5.0

# --------------------------------------------------------------------------- text limits
CURIOSITY_MAX_CHARS = 64
CURIOSITY_MAX_WORDS = 11
SUMMARY_MAX_CHARS = 84
SUMMARY_MAX_WORDS = 14
MIN_BEATS, MAX_BEATS = 2, 3
MAX_CANDIDATES, MIN_CANDIDATES = 5, 1

# --------------------------------------------------------------------------- thresholds
# Deliberately conservative: a claim word ("quiet", "big") is only licensed when the number
# is clearly on that side of the line - never for a borderline session.
QUIET_INDEX_PCT = 0.40          # |Nifty| below this -> "quiet"
BIG_INDEX_PCT = 1.00            # |Nifty| at/above this -> "big"
BIG_STOCK_PCT = 5.00
HIDDEN_ACTION_STOCK_PCT = 2.50  # a stock this far from zero on a quiet day is "action"
CONTRAST_SPREAD_PP = 1.50       # leader - laggard, with opposite signs
BIG_GLOBAL_PCT = 1.00
UNUSUAL_RVOL = 2.00
FLOW_CONTRAST_CRORE = 1000.0

# POST freeze - major index day hook priority (applied in candidates.build_candidates, before
# Gemini sees anything, so the model can never make this call). When |Nifty| >= MAJOR_INDEX_PCT
# the BIG_MOVE hook outranks a single-stock UNUSUAL_ACTIVITY hook: the non-exceptional stock
# candidate is not offered at all. The ONE exception is an objectively exceptional stock event,
# ALL of these on the sheet's validated (move-guard-passed) Radar facts:
#   |stock move| >= EXCEPTIONAL_STOCK_PCT
#   relative volume >= EXCEPTIONAL_RVOL
#   |stock move| >= EXCEPTIONAL_INDEX_MULTIPLE x |Nifty move|
# An exceptional candidate is then ranked by its ordinary score, like any other.
MAJOR_INDEX_PCT = 1.50
EXCEPTIONAL_STOCK_PCT = 10.0
EXCEPTIONAL_RVOL = 5.0
EXCEPTIONAL_INDEX_MULTIPLE = 5.0

# --------------------------------------------------------------------------- vocab
# Claim -> the words it licenses. A word from any of these sets appearing in a hook line
# needs the matching claim on one of the facts the line may cite.
CLAIM_WORDS = {
    "quiet": {"quiet", "quietly", "flat", "calm", "muted", "barely", "just", "only",
              "little", "sleepy", "subdued", "small"},
    "big": {"big", "bigger", "sharp", "sharply", "steep", "large", "heavy", "strong"},
    "leader": {"led", "leader", "leading", "strongest", "top", "best"},
    "laggard": {"lagged", "laggard", "lagging", "weakest", "worst", "bottom"},
    "top_gainer": {"biggest", "top", "largest"},
    "top_loser": {"biggest", "largest", "worst"},
    "breakout": {"broke", "breakout", "cleared"},
    "breakdown": {"broke", "breakdown", "slipped"},
    "cross_above": {"crossed", "reclaimed"},
    "cross_below": {"crossed", "slipped"},
    "unusual_volume": {"unusual", "heavy", "spike"},
    "convergence": {"together", "aligned", "stacked"},
    "opposite": {"split", "opposite", "divided", "battle", "versus", "vs"},
    "extreme_rank": {"biggest", "largest", "highest", "lowest", "most"},
    "weak_technical": {"weak", "weaker", "weakening"},
    "strong_fundamental": {"strong", "stronger", "solid"},
}
# Words that are claims but never licensed by anything: hype has no factual threshold.
HYPE_WORDS = {"massive", "huge", "crash", "crashed", "crashes", "surge", "surged", "surges",
              "soar", "soared", "soars", "plunge", "plunged", "plunges", "skyrocket",
              "skyrocketed", "explode", "exploded", "rocket", "rocketed", "stunning", "shocking",
              "insane", "crazy", "bloodbath", "carnage", "mayhem", "frenzy", "meltdown",
              "euphoria", "panic", "jackpot", "secret", "guaranteed", "unbelievable",
              "incredible", "record", "historic", "unprecedented", "ever", "all-time"}
SUPERLATIVES = {"biggest", "largest", "highest", "lowest", "best", "worst", "strongest",
                "weakest", "sharpest", "steepest", "top", "most", "least"}

CAUSAL_PATTERNS = [
    r"\bbecause\b", r"\bdue to\b", r"\bowing to\b", r"\bon the back of\b", r"\bdriven by\b",
    r"\bthanks to\b", r"\bamid\b", r"\bas a result\b", r"\bresult of\b", r"\btrigger(?:ed|s)?\b",
    r"\bfu?ell?ed by\b", r"\bspark(?:ed|s)?\b", r"\bcaus(?:e|ed|es|ing)\b", r"\bled by\b",
    r"\bdragged\b", r"\bweigh(?:ed|s)? (?:on|down)\b", r"\bpowered by\b", r"\bboosted by\b",
    r"\bon (?:hopes|fears|worries|news)\b", r"\bafter\b", r"\bfollowing\b", r"\bwhy\b",
    r"\breasons?\b", r"\bbehind\b", r"\bexplain(?:s|ed)?\b", r"\bhence\b",
]
PREDICTION_PATTERNS = [
    r"\bwill\b", r"\w'll\b", r"\bwon'?t\b", r"\bcould\b", r"\bmight\b", r"(?-i:\bmay\b)",
    r"\bshould\b", r"\bwould\b", r"\blikely\b", r"\bunlikely\b", r"\bexpect(?:s|ed|ing)?\b",
    r"\bpoised\b", r"\bset to\b", r"\bgoing to\b", r"\bgonna\b", r"\babout to\b",
    r"\bforecast\w*\b", r"\bpredict\w*\b", r"\boutlook\b", r"\bnext move\b", r"\btomorrow\b",
    r"\bupside\b", r"\bdownside\b", r"\bpotential\b", r"\bheaded\b", r"\bheading\b",
    r"\bon track\b", r"\bbound to\b", r"\bpoints? to\b", r"\bsignals? a\b", r"\bgap[- ]?(?:up|down)\b",
    r"\bcandidates?\b", r"\bnext\b", r"\bsoon\b", r"\bcoming\b",
]
RECOMMENDATION_PATTERNS = [
    r"\bbuy\b", r"\bsell\b", r"\baccumulat\w*\b", r"\binvest\b", r"\binvest in\b",
    r"\bworth (?:watching|seeing|buying|owning|tracking|a look|it)\b", r"\bopportunit\w*\b",
    r"\bbargain\b", r"\bcheap\b", r"\bundervalued\b", r"\bovervalued\b", r"\bmultibagger\w*\b",
    r"\bpicks?\b", r"\bbets?\b", r"\btrade idea\b", r"\bentry\b", r"\bexit\b",
    r"\bstop[- ]?loss\b", r"\btargets?\b", r"\bdon'?t miss\b", r"\bmust[- ](?:own|watch|buy)\b",
    r"\bhot stocks?\b", r"\byou should\b", r"\bbook (?:profit|gains)\b", r"\badd(?:ing)? to\b",
    r"\bportfolio\b", r"\breturns?\b", r"\bprofit\w*\b",
]

# Proper nouns any line may use without a fact behind them (brand and product words).
GENERIC_PROPER = {"nifty", "radar", "market", "daily", "byte", "inside", "here", "here's",
                  "fii", "fiis", "dii", "diis", "india", "indian", "nse", "ist", "i", "today",
                  "fii/dii", "vs", "am", "pm"}   # "am"/"pm": a PRE reading's clock time

# Capitalised words a sentence may START with without naming an entity. Anything else that
# is capitalised must be an entity from the facts - so "Sensex fell" cannot slip through as
# an ordinary sentence opening.
STARTER_WORDS = {
    "a", "an", "the", "and", "but", "yet", "or", "plus", "also", "all", "both", "every", "each",
    "same", "inside", "before", "bigger", "smaller", "under", "underneath", "beneath", "below",
    "above", "beyond", "meanwhile", "while", "what", "where", "here", "here's", "today",
    "today's", "tonight", "overnight", "not", "only", "just", "one", "its", "it", "it's",
    "this", "that", "these", "those", "their", "your", "quiet", "calm", "flat", "big", "small",
    "on", "in", "at", "with", "from", "for", "of", "to", "by", "no", "none", "nothing", "few",
    "most", "some", "many", "several", "more", "less", "then", "now", "still", "even",
    "surface", "session", "chart", "price", "volume", "stocks", "sectors", "weak", "strong",
    "headline", "three", "two", "four", "five", "six", "seven", "eight", "nine", "ten",
    "first", "second", "third", "down", "up", "higher", "lower", "opposite", "different",
    "split", "things", "news", "numbers", "calendar", "global", "closed", "moved", "trading",
    "broke", "slipped", "crossed", "activity", "action", "breadth", "money", "flows",
    "prices", "markets", "traders", "underneath", "look", "zoom", "meanwhile", "elsewhere",
    "beneath", "inside", "yesterday's", "overnight's", "same", "different", "busy", "busier",
    "quieter", "calmer", "louder", "nothing", "everything", "something",
}

# Direction words, for the polarity check next to an entity.
UP_WORDS = {"rose", "gained", "up", "higher", "climbed", "jumped", "advanced", "rallied",
            "added", "firmer", "bought", "buyers"}
DOWN_WORDS = {"fell", "lost", "down", "lower", "dropped", "slipped", "declined", "sank", "slid",
              "shed", "sold", "sellers"}

# "one" is left out on purpose: "in one session" is idiom, not a cited figure.
NUMBER_WORDS = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
                "twelve": "12", "twenty": "20", "fifty": "50"}

# Words a line uses to PROMISE a section of the Short. Promising a section the Short does
# not contain is rejected - the summary line is a contract with the viewer.
SECTION_PROMISES = {
    "SECTORS": ("sector",),
    "MOVERS": ("mover", "gainer", "loser"),
    "RADAR": ("radar",),
    "FLOWS": ("fii", "dii", "flows", "institution"),
    "AHEAD": ("overnight", "global", "cues"),
    "GLOBAL": ("overnight", "global", "cues"),
    "EVENTS": ("event", "calendar"),
    "NIFTY": ("chart",),
    "PULSE": ("close",),
    "TECHNICAL": ("trend", "technical", "chart"),
    "VOLUME": ("volume",),
    "FUNDAMENTALS": ("fundamental", "earnings", "revenue"),
    "GIFT": ("gift",),
    "LEVELS": ("levels", "support", "resistance"),
    "PREV": ("previous session", "last session"),
    "RELATIVE": ("vs nifty", "relative"),
    "WATCH": ("watch",),
}
# Sector names that count as entities wherever they appear (even when not in the sheet), so
# a line cannot name a sector the facts never mentioned.
KNOWN_SECTORS = ("Pharma", "IT", "Bank", "Auto", "Metal", "FMCG", "Energy", "Realty", "Media",
                 "PSU Bank", "Private Bank", "Financial Services", "Healthcare", "Infra",
                 "Oil & Gas", "Consumer Durables", "Commodities", "Services")

__all__ = [n for n in dir() if n.isupper()]
