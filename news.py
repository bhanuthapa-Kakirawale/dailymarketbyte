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
def google_news(query: str, days: int = 2, n: int = 6) -> list:
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
        out.append({"title": title, "source": src})
        if len(out) >= n:
            break
    return out


def short_name(name: str) -> str:
    return re.sub(r"\s+(Ltd\.?|Limited)$", "", name.strip(), flags=re.I)


def clip_words(s: str, n: int = 13) -> str:
    w = str(s).split()
    return s if len(w) <= n else " ".join(w[:n]).rstrip(",;:-") + "..."


def ask_gemini(prompt: str, search: bool = True, tries: int = 3):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}}
    if search:
        body["tools"] = [{"google_search": {}}]
    for i in range(tries):
        try:
            r = requests.post(url, json=body, timeout=180,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            if r.status_code in (429, 500, 503):
                print(f"[gemini] {r.status_code}, retrying...")
                time.sleep(15 * (i + 1))
                continue
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except Exception as e:
            print(f"[gemini] request failed: {e}")
            time.sleep(5)
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


# ----------------------------------------------------------------------------- market facts (AI, validated)
def market_facts(recap_date: dt.date, today: dt.date, nifty_close: float) -> dict:
    """GIFT Nifty, FII/DII (fallback) and an independent Nifty close for cross-checking.
    Every number is validated (date + sanity range) before use; anything doubtful is dropped."""
    prompt = f"""Use Google Search. Find these facts for the Indian stock market:
1) Provisional FII/FPI and DII net cash-market figures in Rs crore for the NSE session on {recap_date:%d %B %Y}.
2) The latest GIFT Nifty level and % change this morning, {today:%d %B %Y} IST.
3) The official Nifty 50 closing value on {recap_date:%d %B %Y}.
Use null for anything you cannot confirm from a search result. Never estimate.
Reply with JSON only:
{{"fii_dii": {{"date": "YYYY-MM-DD", "fii": number, "dii": number}} or null,
 "gift_nifty": {{"date": "YYYY-MM-DD", "value": number, "pct": number}} or null,
 "nifty_close": {{"date": "YYYY-MM-DD", "value": number}} or null}}"""
    data = extract_json(ask_gemini(prompt)) or {}
    out = {}
    fd = data.get("fii_dii") or {}
    fii, dii = _num(fd.get("fii")), _num(fd.get("dii"))
    if fd.get("date") == str(recap_date) and fii is not None and dii is not None \
            and abs(fii) < 60000 and abs(dii) < 60000:
        out["fii_dii"] = {"fii": fii, "dii": dii, "source": "web"}
    g = data.get("gift_nifty") or {}
    gv, gp = _num(g.get("value")), _num(g.get("pct"))
    if g.get("date") == str(today) and gv and gp is not None and abs(gv / nifty_close - 1) < 0.05 and abs(gp) < 5:
        out["gift"] = {"value": gv, "pct": gp}
    nc = data.get("nifty_close") or {}
    nv = _num(nc.get("value"))
    if nc.get("date") == str(recap_date) and nv:
        out["nifty_close"] = nv
    print(f"[gemini] facts accepted: {list(out)}")
    return out


# ----------------------------------------------------------------------------- reasons
def explain_moves(gainers: list, losers: list, mkt: dict) -> str:
    """Adds r['reason'] to every mover. Returns a one-line reason for Nifty's move (or '')."""
    movers = gainers + losers
    for r in movers:
        r["headlines"] = google_news(f'"{short_name(r["name"])}" shares', days=3, n=5)
    nifty_news = google_news("Sensex Nifty closing", days=2, n=8)

    block = "\n".join(
        f"- {r['symbol']} ({short_name(r['name'])}): {r['pct']:+.2f}% | headlines: "
        + (" || ".join(h["title"] for h in r["headlines"]) or "none")
        for r in movers)
    prompt = f"""You write on-screen captions for "Daily Byte", a YouTube Short recapping the Indian stock market
session of {mkt['recap_date']:%d %b %Y}. Nifty 50 closed at {fmt_in(mkt['close'], 2)} ({mkt['pct']:+.2f}%).
Market headlines: {" || ".join(h['title'] for h in nifty_news) or "none"}

Stock moves with recent headlines:
{block}

For each stock give the most likely reason for its move that session: max 12 words, plain English, factual.
Use the headlines first and Google Search to verify or fill gaps. Never invent news. If nothing stock-specific
exists, say it moved with its sector or the broader market (name the sector). No advice, no predictions.
Also give one line (max 14 words) on what drove Nifty that day.
Reply with JSON only: {{"nifty": "...", "stocks": {{"SYMBOL": "reason", ...}}}}"""
    data = extract_json(ask_gemini(prompt)) or {}
    reasons = data.get("stocks") if isinstance(data.get("stocks"), dict) else {}

    for r in movers:
        reason = reasons.get(r["symbol"])
        if not reason and r["headlines"]:
            reason = r["headlines"][0]["title"]
        r["reason"] = clip_words(reason or "No major company-specific news; moved with sector trend.", 13)
    nifty_reason = data.get("nifty") or (nifty_news[0]["title"] if nifty_news else "")
    return clip_words(nifty_reason, 15) if nifty_reason else ""


# ----------------------------------------------------------------------------- events
def _rule_events(today: dt.date) -> list:
    if today.weekday() != EXPIRY_WEEKDAY:
        return []
    monthly = (today + dt.timedelta(days=7)).month != today.month
    return [{"tag": "F&O", "text": f"Nifty {'monthly' if monthly else 'weekly'} F&O expiry today"}]


def todays_events(today: dt.date) -> list:
    events = _rule_events(today)
    prompt = f"""Use Google Search. List up to 5 important scheduled events for Indian stock market traders on
{today:%A, %d %B %Y} (IST): quarterly results of large or popular Indian companies, RBI policy or Indian economic data,
F&O expiry, notable IPO openings or listings, and major global events (US data, Fed, etc.) that matter for India today.
Only include items confirmed for today. Each text max 10 words. Tag is one short word: RESULTS, IPO, DATA, GLOBAL, RBI, F&O.
Reply with JSON only: {{"events": [{{"tag": "RESULTS", "text": "..."}}]}}"""
    data = extract_json(ask_gemini(prompt)) or {}
    llm = [{"tag": str(e.get("tag", "EVENT"))[:8].upper(), "text": clip_words(str(e.get("text", "")), 11)}
           for e in data.get("events", []) if isinstance(e, dict) and e.get("text")]
    if not llm:
        llm = [{"tag": "NEWS", "text": clip_words(h["title"], 11)}
               for h in google_news("stocks to watch today India", days=1, n=4)]
    if any("expiry" in e["text"].lower() for e in llm):
        events = []
    events = (events + llm)[:5]
    return events or [{"tag": "INFO", "text": "No major scheduled events; track global cues"}]
