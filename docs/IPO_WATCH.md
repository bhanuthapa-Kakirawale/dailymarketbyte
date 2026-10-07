# IPO WATCH - official primary-market facts

"What is happening in India's primary market", from official sources. Not Apply/Avoid, not a
rating, not a listing-gain prediction, no GMP, no valuation.

## Sources (Gemini is never an IPO source; grey-market sites are never read)

1. **SEBI offer documents** (DRHP / RHP / prospectus / corrigenda): `data/ipo_offer_documents.json`,
   entered by hand from the document filed on sebi.gov.in (or the exchange). Every figure
   carries its document and **page reference**; `load_offer_documents` rejects an entry that is
   incomplete, off-host or unreferenced. The file ships empty.
2. **NSE issue lists** (website API, undocumented): `/api/ipo-current-issue`,
   `/api/all-upcoming-issues?category=ipo` - company, symbol, board (EQ -> MAINBOARD, SME),
   bidding dates, price band. The same issue appearing in two lists is shown once.

## The model (`ipo_watch/models.py`)

`IPOEvent`: company, symbol, board, status, issue open/close/allotment/listing dates, issue
type, fresh issue / OFS / issue size (crore, from the offer document), price band, lot size,
`Subscription` (total / retail / QIB / NII / employee + the exchange's own `as_of`), objects of
the issue, `FinancialRow`s and `RiskFact`s (each with a page reference), `ListingOutcome`
(issue / listing / close price - only after the listing session), source, reference,
data_as_of, retrieved_at, rights and validation status.

There is **no field** for GMP, a rating, a fair value, an expected listing price or a score:
the model cannot hold them, so it cannot publish them. Missing values are omitted, never
inferred - e.g. NSE's current-issue rows carry no update timestamp, so their bid multiples are
recorded as a note and **not published**; the issue size in shares is never converted to rupees.

## Snapshot, then events

The REPORT job stores the complete validated NSE issue-list state for the session
(`ipo_snapshot.json`): company, symbol, board, issue dates, status, price band, and bid multiples
exactly as listed. The list carries no timestamp, so bid multiples are stored as
`unpublished_bid_multiples` and are never shown. It also records `source_date` and `retrieved_at`.
A partially read state (one list failed) is never used. Offer-document figures are applied at
planning time from the hand-verified SEBI file.

`ipo_watch.derive_events` derives the day's events from the stored snapshot:
`OPENS_TODAY`, `CLOSES_TODAY`, `LISTING_TODAY`, `ALLOTMENT_EVENT`, and `SUBSCRIPTION_UPDATE`
(only with an exchange timestamp). They are recorded in the audit and carry no classification.

## Selection (deterministic - never "popularity")

An IPO qualifies only with a dated event: LISTS / CLOSES / OPENS / ALLOTMENT today (POST: the
listing session that just ended -> `LISTED` with historical listing facts). Order: event kind,
Mainboard before SME, larger official issue size, company name. One IPO -> an IPO WATCH card
(<= 5 official fact rows); 2-4 -> one PRIMARY MARKET board. Never "Top IPOs".

PRE speaks in the present ("CLOSES TODAY", "Two IPOs close today"); POST in the past
("BIDDING CLOSED", "Two IPOs closed for bidding"). Listing prices appear only for `LISTED`.

## Publication

IPO facts are IPO scope, OFFICIAL_EXCHANGE / OFFICIAL_COMPANY origin, IPO_EVENT /
FINANCIAL_STATISTIC class; the gate blocks everything else, and the language scan blocks
Apply/Avoid/subscribe-to/good/best IPO/listing gain/fair value/cheap/expensive/GMP. The audit's
`ipo` block records content present, companies, events, sources, subscription as-of, the
official facts used and `gmp_present` (must be false).

Verified live 2026-09-26: the NSE lists parsed (A-One Steels, Moneyview, Green Asia Impex close
28 Sep; Shah Investor's Home opens) - no bid multiple published (no exchange timestamp).
