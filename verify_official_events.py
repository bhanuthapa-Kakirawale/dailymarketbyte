"""Re-verify the controlled official-event file against the organisers' own pages.

    python verify_official_events.py                 # check, write the audit, change nothing
    python verify_official_events.py --stamp         # ... and, ONLY if every entry of a source
                                                     # matches, stamp verified_on/retrieved_at

The daily PRE run never fetches these pages (a regulator's website being slow or blocked must
not cost a morning's Short); it reads data/official_events.json, which this tool keeps honest.
What it checks, per source URL (RBI's press release for the MPC schedule):
- the page is reachable and is the named publication (its reference number appears on it);
- every entry's meeting dates appear on the page as one meeting line, exactly;
- every meeting line on the page is in the file (a meeting RBI added is reported, not guessed).
A mismatch is reported and the file is left unchanged - fixing it is a human decision.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys

from core.event_calendar import OFFICIAL_EVENTS_PATH, load_official_events, parse_meeting_line
from core.freshness import IST

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml"}


def page_text(raw_html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw_html, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</tr>|</p>|</td>|</div>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\r\xa0]+", " ", t)


def compare_source(entries: list, text: str) -> dict:
    """Pure: does the organiser's page (as text) carry exactly the file's meetings?"""
    page_meetings = [tuple(m) for m in parse_meeting_line(text)]
    ref_ok = all(e["source_reference"].split()[-1] in text for e in entries)
    results, file_meetings = [], set()
    for e in entries:
        want = tuple(sorted(dt.date.fromisoformat(d) for d in e["meeting_dates"]))
        file_meetings.add(want)
        results.append({"event_id": e["event_id"], "meeting_dates": e["meeting_dates"],
                        "on_page": want in page_meetings})
    extra = [[d.isoformat() for d in m] for m in page_meetings if m not in file_meetings]
    ok = ref_ok and all(r["on_page"] for r in results) and not extra
    return {"reference_found": ref_ok, "entries": results,
            "page_meetings_not_in_file": extra, "matches": ok}


def verify(path: str = OFFICIAL_EVENTS_PATH, fetch=None, stamp: bool = False) -> dict:
    import requests
    fetch = fetch or (lambda url: requests.get(url, headers=HEADERS, timeout=30))
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    loaded = load_official_events(path)
    now = dt.datetime.now(IST).replace(microsecond=0)
    by_url: dict = {}
    for e in doc.get("events", []):
        by_url.setdefault(e.get("source_url"), []).append(e)
    sources = []
    for url, entries in by_url.items():
        rec = {"source_url": url, "source_reference": entries[0].get("source_reference"),
               "checked_at": now.isoformat(), "entries": len(entries)}
        try:
            r = fetch(url)
            rec["http_status"] = r.status_code
            if r.status_code != 200:
                rec.update(matches=False, error=f"HTTP {r.status_code}")
            else:
                rec.update(compare_source(entries, page_text(r.text)))
        except Exception as exc:
            rec.update(matches=False, error=f"{type(exc).__name__}: {exc}")
        if stamp and rec.get("matches"):
            for e in entries:
                e["verified_on"] = now.date().isoformat()
                e["retrieved_at"] = now.isoformat()
            rec["stamped"] = True
        sources.append(rec)
    if stamp and any(s.get("stamped") for s in sources):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return {"checked_at": now.isoformat(), "file": path,
            "loader": {"status": loaded.status, "accepted": len(loaded.events),
                       "rejected": loaded.rejected},
            "sources": sources, "all_match": all(s.get("matches") for s in sources)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=OFFICIAL_EVENTS_PATH)
    ap.add_argument("--stamp", action="store_true")
    ap.add_argument("--audit", default=os.path.join("output", "pre_data_sources",
                                                    "official_events_verification.json"))
    args = ap.parse_args(argv)
    res = verify(args.file, stamp=args.stamp)
    os.makedirs(os.path.dirname(args.audit), exist_ok=True)
    with open(args.audit, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(json.dumps({"all_match": res["all_match"], "loader": res["loader"],
                      "sources": [{k: s.get(k) for k in ("source_reference", "http_status",
                                                         "matches", "page_meetings_not_in_file",
                                                         "error", "stamped")}
                                  for s in res["sources"]]}, indent=2))
    return 0 if res["all_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
