"""News + AI layer using the FREE Google Gemini API (with Google Search grounding).

Without GEMINI_API_KEY everything still works: reasons fall back to Google News headlines,
and AI-only items (GIFT Nifty, FII/DII fallback) are simply skipped.
"""
import datetime as dt
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

from config import GEMINI_MODEL, EXPIRY_WEEKDAY, fmt_in

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


# ----------------------------------------------------------------------------- helpers
def _parse_pubdate(s):
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", s or "")
    if not m:
        return None
    try:
        return dt.datetime.strptime(" ".join(m.groups()), "%d %b %Y").date()
    except Exception:
        return None


def google_news(query: str, days: int = 2, n: int = 6) -> list:
    """Google's relevance ranking often surfaces old "roundup" articles that merely mention
    the query (e.g. a stale "stocks to watch" listicle) ahead of same-day coverage - callers
    that need a specific day's news should filter/sort by the returned 'date', not just take [0]."""
    url = ("https://news.google.com/rss/search?q=" + quote(f"{query} when:{days}d")
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[news] RSS failed for '{query}': {e}")
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"title": title, "source": src, "date": _parse_pubdate(it.findtext("pubDate") or "")})
        if len(out) >= n:
            break
    return out


_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE_IN_TITLE = re.compile(rf"\b(?:(\d{{1,2}})\s+({_MONTHS})\w*|({_MONTHS})\w*\s+(\d{{1,2}}))\b")


def _title_date_ok(title: str, recap_date: dt.date) -> bool:
    """Recurring 'Stocks to Watch Today, <date>: ...' columns get republished daily with a
    fresh pubDate but an unrelated headline - e.g. one dated (published) 19 Sep still titled
    'Aug 3'. A date embedded in the title itself must match the session (or the morning after)
    or the headline is stale/irrelevant regardless of when it was crawled."""
    m = _DATE_IN_TITLE.search(title)
    if not m:
        return True
    day = m.group(1) or m.group(4)
    mon = m.group(2) or m.group(3)
    try:
        d, mo = int(day), dt.datetime.strptime(mon[:3], "%b").month
    except Exception:
        return True
    return any(cand.day == d and cand.month == mo for cand in (recap_date, recap_date + dt.timedelta(days=1)))


def _same_day(headlines: list, recap_date: dt.date) -> list:
    """Headlines actually published on (or the morning after) the recap session - filters out
    stale listicles and forward-looking "stocks to watch tomorrow" pieces that merely mention
    the query but don't explain that session's move."""
    return [h for h in headlines if h["date"] and recap_date <= h["date"] <= recap_date + dt.timedelta(days=1)
            and _title_date_ok(h["title"], recap_date)]


def short_name(name: str) -> str:
    return re.sub(r"\s+(Ltd\.?|Limited)$", "", name.strip(), flags=re.I)


def clip_words(s: str, n: int = 13) -> str:
    w = str(s).split()
    return s if len(w) <= n else " ".join(w[:n]).rstrip(",;:-") + "..."


def ask_gemini(prompt: str, search: bool = True, tries: int = 5, timeout: int = 180,
               generation_config: dict | None = None):
    """`generation_config` (optional) replaces the default `{"temperature": 0.2}` - the hook
    engine uses it to request structured JSON (`responseMimeType`/`responseSchema`).

    404/503 on generateContent is a documented, unresolved server-side Gemini API issue
    (confirmed on Google's own developer forum, Sep 2026): the exact same request intermittently
    404s or 503s even with a valid key/model/project, and can succeed on a later retry. So both
    are treated as transient and retried with backoff, not as "this model/key is wrong".

    429 is different: it means the free-tier RPD/RPM quota is exhausted (Google's "check your
    plan and billing" quota-exceeded error), not a transient server hiccup - retrying within the
    same run can't get more quota, so it fails fast (no backoff loop) straight to the same
    graceful no-AI-facts fallback every other failure mode already uses."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config or {"temperature": 0.2}}
    if search:
        body["tools"] = [{"google_search": {}}]
    for i in range(tries):
        try:
            r = requests.post(url, json=body, timeout=timeout,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            if r.status_code == 429:
                print(f"[gemini] 429 quota exceeded - not retrying: {r.text[:300]}")
                return None
            if r.status_code in (404, 500, 503):
                print(f"[gemini] {r.status_code} (attempt {i + 1}/{tries}): {r.text[:300]}")
                time.sleep(min(15 * (i + 1), 30))
                continue
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except Exception as e:
            print(f"[gemini] request failed (attempt {i + 1}/{tries}): {e}")
            time.sleep(5)
    print("[gemini] all retries exhausted - continuing without AI facts/reasons "
          "(known intermittent Gemini API issue, not a pipeline bug)")
    return None


def extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


# Where a narrative item actually came from. Recorded at acquisition, never inferred later by
# string-matching the finished text against candidate headlines - only this layer can still
# see which branch produced a reason, and a reconstruction downstream is a guess.
ORIGIN_GEMINI = "GEMINI"
ORIGIN_GEMINI_SEARCH = "GEMINI_SEARCH"
ORIGIN_GOOGLE_NEWS = "GOOGLE_NEWS_RSS"
ORIGIN_RULE_EXPIRY = "RULE_FNO_EXPIRY"
ORIGIN_RULE_PLACEHOLDER = "RULE_NO_EVENTS_FOUND"
ORIGIN_FIXTURE = "DEMO_FIXTURE"
ORIGIN_NONE = "NO_VERIFIED_CATALYST"


# ----------------------------------------------------------------------------- events (rules/fallback)
def _rule_events(today: dt.date) -> list:
    if today.weekday() != EXPIRY_WEEKDAY:
        return []
    monthly = (today + dt.timedelta(days=7)).month != today.month
    return [{"tag": "F&O", "text": f"Nifty {'monthly' if monthly else 'weekly'} F&O expiry today",
             "source": ORIGIN_RULE_EXPIRY, "publisher": None}]


_GENERIC_ROUNDUP = re.compile(
    r"\b(ahead of market|things (to know|that will)|market wrap|closing bell|opening bell|"
    r"pre-?market|stocks to watch|top \d+ stocks|share market news|stock market (news|today)|"
    r"nifty (50 )?outlook)\b", re.I)


def _specific(headlines: list) -> list:
    """Drops generic daily roundup columns ('Ahead of Market: 10 things...', 'Stocks to watch
    today', etc.) - these aren't events, just a catch-all title reused every day."""
    return [h for h in headlines if not _GENERIC_ROUNDUP.search(h["title"])]


# category, search query, tag, day window - used only when Gemini is unavailable/returns nothing
_FALLBACK_QUERIES = [
    ("IPO opening today", 'IPO opens OR "IPO subscription opens" India', "IPO", 1),
    ("IPO GMP", "IPO GMP grey market premium today", "IPO", 1),
    ("company results/events", '"quarterly results" OR "board meeting" India company today', "RESULTS", 1),
    ("RBI/govt decision", "RBI OR government policy decision India economy", "RBI", 2),
    ("Fed/global", "Fed rate decision Federal Reserve OR US inflation data", "GLOBAL", 2),
]


def _fallback_events(today: dt.date) -> list:
    out, seen = [], set()
    for _label, query, tag, days in _FALLBACK_QUERIES:
        hits = _specific(google_news(query, days=days, n=4))
        for h in hits:
            key = h["title"].lower()
            if key not in seen:
                seen.add(key)
                out.append({"tag": tag, "text": clip_words(h["title"], 11),
                            "source": ORIGIN_GOOGLE_NEWS, "publisher": h.get("source") or None})
                break
    return out


# ----------------------------------------------------------------------------- combined AI pass
def ai_pass(recap_date: dt.date, today: dt.date, nifty_close: float,
            gainers: list, losers: list, mkt: dict) -> tuple:
    """Market facts (GIFT Nifty/FII-DII/Brent/independent Nifty close), mover/Nifty move reasons,
    and today's scheduled events - all in ONE Gemini call instead of three. Free-tier Gemini quota
    is per-request (RPD/RPM, and grounding requests count separately), so merging what used to be
    market_facts()+explain_moves()+todays_events() into a single prompt cuts requests-per-run from
    3 to 1. Returns (facts: dict, nifty_reason: str, events: list) - same validation/fallback
    behaviour as the three separate functions this replaces, including full graceful degradation
    to non-AI paths if the call fails entirely (ask_gemini returns None -> data stays {})."""
    movers = gainers + losers
    for r in movers:
        headlines = google_news(f'"{short_name(r["name"])}" shares', days=3, n=8)
        # same-day coverage first, so both Gemini and the no-key fallback prefer it over
        # a stale/irrelevant but higher-"relevance" result from Google's ranking
        same_day = _same_day(headlines, recap_date)
        r["headlines"] = same_day + [h for h in headlines if h not in same_day]
        r["_same_day_count"] = len(same_day)
    nifty_news = google_news("Sensex Nifty closing", days=2, n=8)
    nifty_same_day = _same_day(nifty_news, recap_date)

    block = "\n".join(
        f"- {r['symbol']} ({short_name(r['name'])}): {r['pct']:+.2f}% | headlines: "
        + (" || ".join(h["title"] for h in r["headlines"]) or "none")
        for r in movers)

    prompt = f"""Use Google Search. You are gathering facts for "Daily Market Byte", a YouTube Short recapping
the Indian (NSE) stock market session of {mkt['recap_date']:%d %b %Y}. Nifty 50 closed at
{fmt_in(mkt['close'], 2)} ({mkt['pct']:+.2f}%). Market headlines: {" || ".join(h['title'] for h in nifty_news) or "none"}

Answer all three of the following and reply with ONE JSON object only, no other text.

1) MARKET FACTS - find these, using null for anything you cannot confirm from a search result (never estimate):
   a) Provisional FII/FPI and DII net cash-market figures in Rs crore for the NSE session on {recap_date:%d %B %Y}.
   b) The latest GIFT Nifty level and % change this morning, {today:%d %B %Y} IST.
   c) The official Nifty 50 closing value on {recap_date:%d %B %Y}.
   d) The Brent crude oil closing price in USD/barrel and % change for {recap_date:%d %B %Y}.

2) MOVE REASONS - for each stock below, the most likely reason for its move that session: max 12 words, plain
   English, factual. Use the headlines first and Google Search to verify or fill gaps. Never invent news. If
   nothing stock-specific exists, say it moved with its sector or the broader market (name the sector). No
   advice, no predictions. Also give one line (max 14 words) on what drove Nifty that day.
   Stock moves with recent headlines:
{block}

3) TODAY'S EVENTS - up to 5 important scheduled events for Indian stock market/retail traders on
   {today:%A, %d %B %Y} (IST), from these categories only: IPOs opening/closing today and GMP (grey market
   premium) for popular ongoing IPOs; corporate events (quarterly results, board meetings, scheduled corporate
   actions of large/popular Indian companies); RBI policy decisions or major Indian government/economic
   decisions and data releases; major global events that matter for India (US Fed rate decisions, US economic
   data, etc). Only include items confirmed for today. Do NOT include generic daily "market wrap", "ahead of
   market", "stocks to watch" or "things to know" roundup articles - only specific, named events. Each text
   max 10 words. Tag is one short word: IPO, RESULTS, RBI, GLOBAL, DATA, F&O.

Reply with JSON only:
{{"fii_dii": {{"date": "YYYY-MM-DD", "fii": number, "dii": number}} or null,
 "gift_nifty": {{"date": "YYYY-MM-DD", "value": number, "pct": number}} or null,
 "nifty_close": {{"date": "YYYY-MM-DD", "value": number}} or null,
 "brent": {{"date": "YYYY-MM-DD", "value": number, "pct": number}} or null,
 "nifty_reason": "...",
 "stock_reasons": {{"SYMBOL": "reason", ...}},
 "events": [{{"tag": "RESULTS", "text": "..."}}]}}"""
    data = extract_json(ask_gemini(prompt)) or {}

    # --- facts (same validation as before: date match + sanity range, else dropped) ---
    facts = {}
    fd = data.get("fii_dii") or {}
    fii, dii = _num(fd.get("fii")), _num(fd.get("dii"))
    if fd.get("date") == str(recap_date) and fii is not None and dii is not None \
            and abs(fii) < 60000 and abs(dii) < 60000:
        facts["fii_dii"] = {"fii": fii, "dii": dii, "source": "web"}
    g = data.get("gift_nifty") or {}
    gv, gp = _num(g.get("value")), _num(g.get("pct"))
    if g.get("date") == str(today) and gv and gp is not None and abs(gv / nifty_close - 1) < 0.05 and abs(gp) < 5:
        facts["gift"] = {"value": gv, "pct": gp}
    nc = data.get("nifty_close") or {}
    nv = _num(nc.get("value"))
    if nc.get("date") == str(recap_date) and nv:
        facts["nifty_close"] = nv
    b = data.get("brent") or {}
    bv, bp = _num(b.get("value")), _num(b.get("pct"))
    if b.get("date") == str(recap_date) and bv and 20 < bv < 300 and bp is not None and abs(bp) < 15:
        facts["brent"] = {"value": bv, "pct": bp}
    print(f"[gemini] facts accepted: {list(facts)}")

    # --- reasons ---
    # Each mover records HOW its reason was obtained, at the moment the branch is taken.
    # Downstream cannot tell a Gemini sentence from a clipped headline by looking at the
    # text, so provenance that is not captured here is provenance that is lost.
    reasons = data.get("stock_reasons") if isinstance(data.get("stock_reasons"), dict) else {}
    for r in movers:
        reason = reasons.get(r["symbol"])
        origin, publisher, headline_date = ORIGIN_GEMINI, None, None
        # no-key/no-answer fallback: only trust Google's top result if it's actually dated
        # to this session - otherwise an old or forward-looking headline would misattribute
        # the move, so admit we don't have a verified reason instead of guessing.
        if not reason and r["_same_day_count"]:
            head = r["headlines"][0]
            reason = head["title"]
            origin, publisher = ORIGIN_GOOGLE_NEWS, head.get("source") or None
            headline_date = head.get("date")
        if not reason:
            origin, publisher = ORIGIN_NONE, None
            reason = "No major company-specific news; moved with sector trend."
        r["reason"] = clip_words(reason, 13)
        r["reason_source"] = origin
        r["reason_publisher"] = publisher
        r["reason_headline_date"] = str(headline_date) if headline_date else None
        del r["_same_day_count"]

    nifty_text = data.get("nifty_reason")
    nifty_origin, nifty_publisher = ORIGIN_GEMINI, None
    if not nifty_text and nifty_same_day:
        nifty_text = nifty_same_day[0]["title"]
        nifty_origin = ORIGIN_GOOGLE_NEWS
        nifty_publisher = nifty_same_day[0].get("source") or None
    if not nifty_text:
        nifty_origin = ORIGIN_NONE
    nifty_reason = {"text": clip_words(nifty_text, 15) if nifty_text else "",
                    "source": nifty_origin, "publisher": nifty_publisher}

    # --- events ---
    events = _rule_events(today)
    llm = [{"tag": str(e.get("tag", "EVENT"))[:8].upper(), "text": clip_words(str(e.get("text", "")), 11),
            "source": ORIGIN_GEMINI_SEARCH, "publisher": None}
           for e in data.get("events", []) if isinstance(e, dict) and e.get("text")]
    if not llm:
        llm = _fallback_events(today)
    if any("expiry" in e["text"].lower() for e in llm):
        events = []
    events = (events + llm)[:5]
    events = events or [{"tag": "INFO", "text": "No major scheduled events; track global cues",
                         "source": ORIGIN_RULE_PLACEHOLDER, "publisher": None}]

    return facts, nifty_reason, events
