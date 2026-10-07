"""GOVT_SECURITIES_AUCTION adapter (P2F): RBI's own live press-release feed for Government of
India dated-securities and Treasury Bill auctions
(`https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=2757`).

Discovery (P2F, 2026-10-07): the listing page is plain, un-scripted HTML (confirmed reachable
from this environment via a direct HTTP GET, no cookie warm-up unlike nseindia.com) with one
`<a href=FS_PressRelease.aspx?prid=NNNN&fn=2757>Title</a>` row per press release under a
`<th>Mon DD, YYYY</th>` date header. Each individual press-release page (same `prid` link)
carries the SUBSTANTIVE notification/result table directly in its own HTML - security name,
notified amount, auction/settlement date for an announcement; notified amount, amount accepted,
cut-off price/yield (YTM), weighted-average price/yield (WAY) for a result - never PDF-only
(the PDF link is supplementary). This is the opposite finding from DELISTING's NSE XLSX rosters
or OPEN_OFFER's SEBI PDFs, which were rejected for exactly this reason; here the HTML itself is
the primary structured evidence. The real `dbie.rbihub.in` domain surfaced during discovery is
NOT RBI's own site (RBI's actual Database on Indian Economy domain is `data.rbi.org.in`,
formerly `dbie.rbi.org.in` - verified via RBI's own URL-change announcement) and was NOT used.

V1 SCOPE (owner-preferred, narrowest useful slice the evidence actually supports):
  SUPPORTED: central government dated securities (`GSEC_NEW`/`GSEC_REISSUE`) and the three
    standard Treasury Bill tenors (`TBILL_91`/`TBILL_182`/`TBILL_364`) - both the announcement
    ("Auction of Government of India Dated Securities" / "Auction of 91-Day, 182-Day and
    364-Day Treasury Bills") and the matching result ("Government Stock - Full Auction Results"
    / "Treasury Bills: Full Auction Result") lifecycle stages, merged into ONE MarketEvent per
    auction cycle.
  DEFERRED: State Development Loans (the SAME feed, under "Auction of State Government
    Securities" / "State Government Securities - Full Auction Result" / "Result of Yield/Price
    Based Auction of State Government Securities" titles - same source/schema, deliberately left
    out of V1 to keep scope narrow: each notification can name several states at once, which is
    a materially different grouping shape from one security/tenor per row); Cash Management
    Bills; switches; government buybacks of its own debt; underwriting-auction press releases;
    the half-yearly/quarterly issuance-calendar PDFs (those are calendar-level documents, not
    individual auction events); RBI liquidity operations (WMA limits, floating-rate-bond
    interest resets). The abbreviated "...Auction Results: Cut-off" result variant (published
    alongside the "Full Auction Result" release, same day, a strict subset of its fields) is
    also skipped - only the Full Auction Result is read, to avoid a redundant second parse of
    the same auction.

EVENT IDENTITY (P2F.1, hardened): this family is market-wide (`symbol=None`, per
`models.MarketEvent`'s own documented invariant - never a security), so the `_lookup_open_event`/
`needs_lookup` symbol-keyed reconciliation EARNINGS/OFS/BUYBACK/OPEN_OFFER/DELISTING share cannot
apply here (and forcing it to, via a pseudo-symbol, would also silently break
`watch.select_market_events`'s "if it names a security, that security is in the tracked
universe" check - never done). No RBI-stated identifier was found that reliably survives a
reschedule either - a press-release reference (`"2026-2027/1252"`) and a GoI notification
reference (`"F.No.4(1)-B(W&M)/2026..."`) are both per-filing, and a correction notice would
plausibly get a new one of either (no live example to check against). So, per the project's own
fallback rule for "no stable ID survives a reschedule", this reuses that EXACT reconciliation
PATTERN via a small, family-specific analogue:
`MarketEventsService._lookup_open_by_instrument(family, instrument_slug)` (`service.py`) matches
the latest NON-TERMINAL stored event by its event_key's own instrument-slug segment instead of
`.symbol` (since `.symbol` is always `None` here), reached through a new opt-in
`needs_instrument_lookup` marker parallel to `needs_lookup`. For a freshly-seen
`(instrument_slug, auction_date)` group: no open prior (none stored yet, or the only one is
already COMPLETED/terminal) -> mint the direct natural key
`f"GOVT_SECURITIES_AUCTION:MARKET:{instrument_slug}:{auction_date_iso}"`, exactly as before; an
open (non-terminal) prior exists -> REUSE its event_key regardless of whether this candidate's
own auction_date matches it, with `status` = `COMPLETED` if this fetch has a result, else
`REVISED_DATE` if the date actually changed (mirrors `sources/earnings.py`'s identical
`REVISED_DATE` logic exactly), else `SCHEDULED`; facts are merged label-by-label via
`_merge_facts()` so a label this fetch doesn't re-observe (e.g. the original announcement's
`settlement_date`, once only a bare result row is seen) is carried forward rather than dropped,
mirroring `open_offer.py`'s `stage_evidence` carry-forward philosophy. Since results publish
same-day, a genuinely new, later auction of the same instrument is never mistaken for a
reschedule of an old one - by the time it exists, the prior cycle is already COMPLETED
(terminal), so the lookup correctly finds nothing and mints a fresh key.

KNOWN, NARROWER RESIDUAL LIMITATION (no live evidence either way): the per-fetch grouping that
separates genuinely different, sequential auction cycles of the SAME instrument within one
lookback window still keys by `(instrument_slug, auction_date)` - dropping date from that
WITHIN-FETCH grouping would wrongly merge e.g. last week's already-published result with next
week's brand-new announcement, which legitimately differ only by date. This means the
cross-fetch `_lookup_open_by_instrument` reconciliation above only kicks in once the ORIGINAL
announcement has actually been persisted by an earlier capture; if a stale original notice and
its correction both appear for the very FIRST time within one single fetch (before either was
ever stored), that one fetch still builds two momentary candidates under their own dates. Given
results publish same-day and a genuine reschedule is rare, this is accepted as a narrow,
explicitly-documented edge case rather than built around.

ANNOUNCEMENT + RESULT MERGE: a single fetch groups every qualifying row from the trailing
`lookback_days` window by `(instrument_slug, auction_date_iso)` BEFORE building any `MarketEvent`
- so an auction seen with both its announcement and its result in the same pass becomes one
COMPLETED event carrying both sets of facts, never two separate events for one auction cycle.
`DEFAULT_LOOKBACK_DAYS = 21` is chosen generously against the real observed announcement-to-
result gap (4-6 days) so a daily capture almost never sees a result without its announcement
already in the same window.

POINT-IN-TIME REPLAY (P2F.1): making a reschedule/result reuse the SAME event_key means a
replay must show the exact revision that was active as of its own cutoff, not whatever the
LATEST on-disk revision happens to be today - `market_events/store.py`'s `list_events`/
`load_latest` were hardened generically (not just for this family) to select, for each
event_key, the highest-numbered revision whose own `retrieved_at` is <= the cutoff, rather than
always the absolute latest. See `docs/MARKET_EVENTS_ENGINE.md`'s "Replay / historical
behaviour" section.
"""
from __future__ import annotations

import datetime as dt
import html
import re

from core import sources as S

from ..models import (COMPLETED, GSEC_REISSUE, PARSE_ERROR, REVISED_DATE, SCHEDULED,
                      SCHEMA_VERSION, SOURCE_UNAVAILABLE, SUCCESS, TBILL_91, TBILL_182,
                      TBILL_364, GOVT_SECURITIES_AUCTION, FamilyFetchResult, MarketEvent)
from institutional_flows.html_tables import extract_tables

LISTING_URL = "https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=2757"
DETAIL_URL_TMPL = "https://www.rbi.org.in/scripts/FS_PressRelease.aspx?prid={prid}&fn=2757"
PARSER_VERSION = "govt-securities-auction-rbi-press-1.0"
DEFAULT_LOOKBACK_DAYS = 21
ISSUER = "Government of India"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DailyByteBot/1.0"}

_GSEC_ANNOUNCE_TITLE = "Auction of Government of India Dated Securities"
_GSEC_RESULT_TITLE = "Government Stock - Full Auction Results"
_TBILL_RESULT_TITLE = "Treasury Bills: Full Auction Result"
_TBILL_ANNOUNCE_RE = re.compile(
    r"^Auction of \d+-Day, \d+-Day and \d+-Day Treasury Bills$", re.I)

_TENOR_TO_SUBTYPE = {"91-DAY": TBILL_91, "182-DAY": TBILL_182, "364-DAY": TBILL_364}

_LISTING_ROW_RE = re.compile(
    r"<th colspan=\"4\" align=\"left\">([^<]+)</th>"
    r"|href=FS_PressRelease\.aspx\?prid=(\d+)&fn=2757>([^<]+)</a>")
_PRESS_REF_RE = re.compile(r"Press Release:\s*([\d]{4}-[\d]{4}/\d+)")
_YTM_RE = re.compile(r"YTM:\s*([\d.]+)\s*%", re.I)
_WAY_RE = re.compile(r"WAY:\s*([\d.]+)\s*%", re.I)
_REISSUE_RE = re.compile(r"re-?issue", re.I)
_DATE_HEADER_RE = re.compile(r"Date\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})")
_TABLE_OPEN_RE = re.compile(r"<table\b", re.I)
_TABLE_CLOSE_RE = re.compile(r"</table\s*>", re.I)


def _http_get(url: str, timeout: float = 20.0) -> str:
    import requests
    r = requests.get(url, headers=_UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text or "")).strip("_").upper()


def _clean(s) -> str:
    return html.unescape(str(s or "")).strip()


def _parse_amount(s) -> int | None:
    s = _clean(s).replace("₹", "").replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_number(s) -> float | None:
    s = _clean(s).replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_long_date(s) -> str | None:
    """'October 09, 2026 (Friday)' or 'Jul 27, 2041' -> ISO date, else None - never guessed."""
    s = re.sub(r"\([^)]*\)", "", _clean(s)).strip()
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _table_blocks(page_html: str) -> list:
    """Every complete `<table>...</table>` substring in the document, nested or not, each
    self-contained so `extract_tables()` (which only trusts its own top-level table) parses it
    correctly regardless of how deeply it was nested in the original page. RBI's own page chrome
    reuses `class="tablebg"` for an outer wrapper AND the inner data table, so a single
    non-greedy regex pairs the wrong open/close tags - a balanced stack scan is required."""
    stack, blocks = [], []
    opens = [(m.start(), True) for m in _TABLE_OPEN_RE.finditer(page_html)]
    closes = [(m.end(), False) for m in _TABLE_CLOSE_RE.finditer(page_html)]
    for pos, is_open in sorted(opens + closes, key=lambda t: t[0]):
        if is_open:
            stack.append(pos)
        elif stack:
            start = stack.pop()
            blocks.append(page_html[start:pos])
    return blocks


def _find_grid(detail_html: str, predicate) -> list | None:
    """The first table block (innermost tables first, since they close first) whose parsed
    grid satisfies `predicate` - e.g. carries the expected header cell text."""
    for block in _table_blocks(detail_html):
        grids = extract_tables(block)
        if grids and predicate(grids[0]):
            return grids[0]
    return None


def _is_schedule_grid(grid: list) -> bool:
    if not grid:
        return False
    header = grid[0]
    return any(c.strip() in ("Security", "Treasury Bill") for c in header)


def _is_result_grid(grid: list) -> bool:
    return any(c.strip() == "Auction Results" for row in grid for c in row)


def _press_release_ref(detail_html: str) -> str | None:
    m = _PRESS_REF_RE.search(detail_html)
    return m.group(1) if m else None


def _result_date(detail_html: str) -> str | None:
    m = _DATE_HEADER_RE.search(detail_html)
    return _parse_long_date(m.group(1)) if m else None


def _parse_gsec_announcement(detail_html: str, prid: str, title: str) -> list:
    grid = _find_grid(detail_html, _is_schedule_grid)
    if not grid or len(grid) < 2:
        return []
    ref = _press_release_ref(detail_html)
    reissue = bool(_REISSUE_RE.search(detail_html))
    out = []
    for row in grid[1:]:
        if len(row) < 7 or not row[0].strip().isdigit():
            continue
        security = _clean(row[1])
        if not security:
            continue
        repayment = _parse_long_date(row[2])
        amount = _parse_amount(row[3])
        notification_ref = _clean(row[4])
        auction_date = _parse_long_date(row[5])
        settlement_date = _parse_long_date(row[6])
        if not auction_date:
            continue
        facts = []
        if amount is not None:
            facts.append({"label": "notified_amount_crore", "value": amount})
        if repayment:
            facts.append({"label": "repayment_date", "value": repayment})
        if settlement_date:
            facts.append({"label": "settlement_date", "value": settlement_date})
        if notification_ref:
            facts.append({"label": "goi_notification_reference", "value": notification_ref})
        if ref:
            facts.append({"label": "announcement_press_release_ref", "value": ref})
        facts.append({"label": "announcement_source_url", "value": DETAIL_URL_TMPL.format(prid=prid)})
        out.append({"instrument_slug": _slug(security), "security_name": security,
                    "sub_type": GSEC_REISSUE if reissue else None,
                    "auction_date": auction_date, "facts": facts,
                    "source_text": title})
    return out


def _parse_tbill_announcement(detail_html: str, prid: str, title: str) -> list:
    grid = _find_grid(detail_html, _is_schedule_grid)
    if not grid or len(grid) < 2:
        return []
    ref = _press_release_ref(detail_html)
    out = []
    for row in grid[1:]:
        if len(row) < 5 or not row[0].strip().isdigit():
            continue
        tenor = _clean(row[1]).upper()
        sub_type = _TENOR_TO_SUBTYPE.get(tenor)
        if sub_type is None:
            continue
        amount = _parse_amount(row[2])
        auction_date = _parse_long_date(row[3])
        settlement_date = _parse_long_date(row[4])
        if not auction_date:
            continue
        facts = []
        if amount is not None:
            facts.append({"label": "notified_amount_crore", "value": amount})
        if settlement_date:
            facts.append({"label": "settlement_date", "value": settlement_date})
        if ref:
            facts.append({"label": "announcement_press_release_ref", "value": ref})
        facts.append({"label": "announcement_source_url", "value": DETAIL_URL_TMPL.format(prid=prid)})
        out.append({"instrument_slug": sub_type, "security_name": tenor.title(),
                    "sub_type": sub_type, "auction_date": auction_date, "facts": facts,
                    "source_text": title})
    return out


def _result_columns(grid: list) -> tuple:
    """(header_row_index, {col_index: instrument_name}) - the row whose cells include
    'Auction Results' names every instrument column from index 2 onward."""
    for i, row in enumerate(grid):
        if any(c.strip() == "Auction Results" for c in row):
            return i, {ci: _clean(c) for ci, c in enumerate(row) if ci >= 2 and c.strip()}
    return -1, {}


def _parse_result_rows(grid: list, columns: dict) -> dict:
    """{instrument_name: {fact_label: value}} read by row label - tolerant of rowspan-expanded
    duplicate labels (the YTM/WAY sub-row shares its parent row's label after expansion)."""
    per_instrument = {name: {} for name in columns.values()}
    for row in grid:
        if len(row) < 2:
            continue
        label = _clean(row[1])
        for ci, name in columns.items():
            if ci >= len(row):
                continue
            cell = row[ci]
            d = per_instrument[name]
            if label.startswith("Notified Amount"):
                v = _parse_amount(cell)
                if v is not None:
                    d.setdefault("notified_amount_crore", v)
            elif label.startswith("Cut-off price"):
                m = _YTM_RE.search(cell)
                if m:
                    d["cutoff_yield_pct"] = float(m.group(1))
                else:
                    v = _parse_number(cell)
                    if v is not None:
                        d.setdefault("cutoff_price", v)
            elif label.startswith("Weighted Average Price"):
                m = _WAY_RE.search(cell)
                if m:
                    d["weighted_avg_yield_pct"] = float(m.group(1))
                else:
                    v = _parse_number(cell)
                    if v is not None:
                        d.setdefault("weighted_avg_price", v)
            elif label.startswith("Devolvement on Primary Dealers"):
                txt = _clean(cell)
                if txt and txt.upper() != "NIL":
                    v = _parse_number(cell)
                    if v is not None:
                        d["devolvement_crore"] = v
    return per_instrument


def _parse_gsec_result(detail_html: str, prid: str, title: str) -> list:
    grid = _find_grid(detail_html, _is_result_grid)
    if not grid:
        return []
    header_idx, columns = _result_columns(grid)
    if header_idx < 0 or not columns:
        return []
    auction_date = _result_date(detail_html)
    if not auction_date:
        return []
    ref = _press_release_ref(detail_html)
    per_instrument = _parse_result_rows(grid, columns)
    out = []
    for name, fields in per_instrument.items():
        facts = [{"label": k, "value": v} for k, v in fields.items()]
        if ref:
            facts.append({"label": "result_press_release_ref", "value": ref})
        facts.append({"label": "result_source_url", "value": DETAIL_URL_TMPL.format(prid=prid)})
        out.append({"instrument_slug": _slug(name), "security_name": name, "sub_type": None,
                    "auction_date": auction_date, "facts": facts,
                    "source_text": title})
    return out


def _parse_tbill_result(detail_html: str, prid: str, title: str) -> list:
    grid = _find_grid(detail_html, _is_result_grid)
    if not grid:
        return []
    header_idx, columns = _result_columns(grid)
    if header_idx < 0 or not columns:
        return []
    auction_date = _result_date(detail_html)
    if not auction_date:
        return []
    ref = _press_release_ref(detail_html)
    per_instrument = _parse_result_rows(grid, columns)
    out = []
    for name, fields in per_instrument.items():
        sub_type = _TENOR_TO_SUBTYPE.get(name.strip().upper())
        if sub_type is None:
            continue
        facts = [{"label": k, "value": v} for k, v in fields.items()]
        if ref:
            facts.append({"label": "result_press_release_ref", "value": ref})
        facts.append({"label": "result_source_url", "value": DETAIL_URL_TMPL.format(prid=prid)})
        out.append({"instrument_slug": sub_type, "security_name": name, "sub_type": sub_type,
                    "auction_date": auction_date, "facts": facts,
                    "source_text": title})
    return out


def _classify_title(title: str) -> str | None:
    t = title.strip()
    if t == _GSEC_ANNOUNCE_TITLE:
        return "GSEC_ANNOUNCE"
    if t == _GSEC_RESULT_TITLE:
        return "GSEC_RESULT"
    if t == _TBILL_RESULT_TITLE:
        return "TBILL_RESULT"
    if _TBILL_ANNOUNCE_RE.match(t):
        return "TBILL_ANNOUNCE"
    return None


def parse_listing(listing_html: str) -> list:
    """[(date, title, prid), ...] in document order - a date header applies to every title row
    beneath it until the next one."""
    out = []
    cur_date = None
    for m in _LISTING_ROW_RE.finditer(listing_html):
        if m.group(1) is not None:
            try:
                cur_date = dt.datetime.strptime(_clean(m.group(1)), "%b %d, %Y").date()
            except ValueError:
                cur_date = None
            continue
        if cur_date is not None:
            out.append((cur_date, _clean(m.group(3)), m.group(2)))
    return out


def _merge_facts(prior_facts: list, new_facts: list) -> list:
    """Carries a prior revision's facts forward and overlays whatever this fetch newly
    observed, keyed by `label` - a label this fetch re-states overwrites the prior value (e.g.
    an amount revision); a label this fetch doesn't re-observe (e.g. the original
    announcement's own facts, once a bare result row with no accompanying schedule row is all
    that's seen this pass) is preserved rather than silently dropped. Mirrors
    `open_offer.py`'s `stage_evidence` carry-forward philosophy."""
    merged = {f["label"]: f["value"] for f in (prior_facts or []) if isinstance(f, dict)}
    for f in new_facts or []:
        if isinstance(f, dict):
            merged[f["label"]] = f["value"]
    return [{"label": k, "value": v} for k, v in merged.items()]


def group_events(rows: list, http_get, now_iso: str, lookback_days: int,
                 lookup_fn=None) -> tuple:
    """(events, notes). `rows` = parse_listing() output already filtered to the lookback
    window. One detail-page fetch per qualifying row; grouped by (instrument, auction date)
    before any MarketEvent is built, so one auction cycle with both its announcement and its
    result in this pass becomes a single COMPLETED event. `lookup_fn` (when given - see
    `MarketEventsService._lookup_open_by_instrument`) reconciles a reschedule: an open
    (non-terminal) prior event for the SAME instrument reuses its event_key regardless of
    whether this candidate's own auction_date matches it - see the module docstring's
    "EVENT IDENTITY" section."""
    groups: dict = {}
    notes = []
    for date_, title, prid in rows:
        kind = _classify_title(title)
        if kind is None:
            continue
        try:
            detail_html = http_get(DETAIL_URL_TMPL.format(prid=prid))
        except Exception as exc:
            notes.append(f"prid {prid}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        parser = {"GSEC_ANNOUNCE": _parse_gsec_announcement, "GSEC_RESULT": _parse_gsec_result,
                 "TBILL_ANNOUNCE": _parse_tbill_announcement,
                 "TBILL_RESULT": _parse_tbill_result}[kind]
        items = parser(detail_html, prid, title)
        if not items:
            notes.append(f"prid {prid}: table shape not recognized ({title})")
            continue
        slot = "schedule" if kind.endswith("ANNOUNCE") else "result"
        for item in items:
            key = (item["instrument_slug"], item["auction_date"])
            groups.setdefault(key, {})[slot] = item

    events = []
    for (instrument_slug, auction_date), g in groups.items():
        sched, res = g.get("schedule"), g.get("result")
        base = res or sched
        new_facts = list((sched or {}).get("facts", [])) + list((res or {}).get("facts", []))
        sub_type = (sched or {}).get("sub_type") or (res or {}).get("sub_type")
        prior = (lookup_fn(GOVT_SECURITIES_AUCTION, instrument_slug) if lookup_fn else None)
        if prior is not None:
            event_key = prior.event_key
            facts = _merge_facts(prior.facts, new_facts)
            sub_type = sub_type or prior.sub_type
            if res:
                status = COMPLETED
            elif prior.data_as_of != auction_date:
                status = REVISED_DATE
            else:
                status = SCHEDULED
        else:
            event_key = f"{GOVT_SECURITIES_AUCTION}:MARKET:{instrument_slug}:{auction_date}"
            facts = new_facts
            status = COMPLETED if res else SCHEDULED
        events.append(MarketEvent(
            schema_version=SCHEMA_VERSION, family=GOVT_SECURITIES_AUCTION, event_key=event_key,
            symbol=None, company=ISSUER, status=status, sub_type=sub_type,
            data_as_of=auction_date, source_name=S.SRC_RBI_AUCTIONS, source_reference=LISTING_URL,
            source_text=base.get("source_text", ""), retrieved_at=now_iso or "",
            status_capture=SUCCESS, facts=facts, parser_version=PARSER_VERSION))
    return events, notes


def fetch_govt_securities_auction(now_iso: str, lookup_fn=None, http_get=None,
                                  lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> FamilyFetchResult:
    from operations.connectivity import classify_exception
    http_get = http_get or _http_get
    try:
        today = dt.datetime.fromisoformat(now_iso).date()
    except (TypeError, ValueError):
        today = dt.date.today()
    try:
        listing_html = http_get(LISTING_URL)
    except Exception as exc:
        return FamilyFetchResult(status=SOURCE_UNAVAILABLE, events=[],
                                 connectivity=classify_exception(exc),
                                 reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    rows = parse_listing(listing_html)
    if not rows:
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="listing page shape changed - no press-release rows found")
    cutoff = today - dt.timedelta(days=lookback_days)
    candidates = [r for r in rows if r[0] >= cutoff]
    events, notes = group_events(candidates, http_get, now_iso, lookback_days,
                                 lookup_fn=lookup_fn)
    qualifying = [r for r in candidates if _classify_title(r[1]) is not None]
    if qualifying and not events and notes and len(notes) >= len(qualifying):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], connectivity="REACHABLE",
                                 reason="; ".join(notes)[:300])
    return FamilyFetchResult(status=SUCCESS, events=events, connectivity="REACHABLE",
                             reason="; ".join(notes)[:300])


fetch_govt_securities_auction.needs_instrument_lookup = True


__all__ = ["LISTING_URL", "DETAIL_URL_TMPL", "PARSER_VERSION", "DEFAULT_LOOKBACK_DAYS",
           "parse_listing", "group_events", "fetch_govt_securities_auction"]
