# NSE IX (GIFT Nifty): data-rights review checklist

Status: **OPEN. Public display is NOT approved.** `GIFT_NIFTY_PUBLICATION_ENABLED` stays false.
This document records facts and open questions. It is **not** a legal conclusion. Whether an
undocumented website endpoint answers says nothing about whether its data may be republished.

Last reviewed: 2026-09-25 (engineering review, not legal advice).

## Sources in use

| Item | Value |
|---|---|
| Live quote | `GET https://www.nseix.com/api/market-rate?type=derivative`. This is an undocumented JSON endpoint behind the public website. The pipeline uses the NIFTY `FUTIDX` rows: LASTPRICE, DAYCHANGE, PERCHANGE, TIMESTMP. |
| Reference | `GET https://www.nseix.com/api/content/daily_report/G_T_DSP_PRICE_<DDMMYYYY>.CSV`. This is NSE IX's Daily Settlement Price file. |
| Registry | `core/sources.py` `nseix_market_rate` / `nseix_settlement_file`, group `NSE_IX`, `display_rights_status="UNREVIEWED"` |
| What we would display | one derived figure: GIFT Nifty's % move since its previous settlement, with its IST time. The level is optional. No order book, no tick data. |

## Exchange ownership

- NSE International Exchange IFSC Ltd (NSE IX) is a subsidiary of the National Stock Exchange of
  India. It operates in GIFT City IFSC and is regulated by IFSCA.
- The GIFT Nifty contract trades on NSE IX. The NIFTY 50 index itself is owned by NSE Indices Ltd.

## What the terms say (as far as could be checked)

| Question | Finding | Evidence |
|---|---|---|
| Do nseix.com's own website terms mention redistribution or display? | **Not verified.** The terms page (`https://www.nseix.com/terms-of-use`) did not load during the review (timeout). | - |
| Does NSE group policy restrict redistribution? | **Yes (parent-exchange policy).** NSE's data policy says redistribution and display of market data are subject to terms and fees set by NSE Data & Analytics Ltd (NDAL). Separate terms apply to externally redistributed derived data. | NSE Data Sharing & Usage Policy, https://www.nseindia.com/static/market-data/nse-data-policy ; https://nsearchives.nseindia.com/web/sites/default/files/inline-files/NSE_Data_Sharing&Usage_Policy.pdf |
| Does public video display need a licence or permission? | **Unresolved.** Under NSE's policy, displaying real-time data generally needs a vendor or display agreement. Whether a delayed, derived single-figure mention in a video is covered, and whether NSE IX (a separate IFSC venue) follows the same policy, is not established. | - |
| Is the endpoint documented or offered for third-party use? | **No.** It is a website-internal API. Its shape can change without notice. | observed |

## Unresolved questions (for the owner or the exchange)

1. Do NSE IX's own website terms of use permit reproducing prices from nseix.com in a public
   YouTube video, with or without a delay?
2. Does NSE IX market data fall under NDAL's licensing, or under a separate NSE IX data policy?
3. Is a derived figure (the % change since settlement, stated in the past tense with a timestamp)
   treated differently from a price?
4. Is there a delay after which display is unrestricted? The PRE reading is ~0-20 minutes old
   when shown.
5. Is attribution required, such as "Source: NSE IX", and in what form?
6. Is an automated request to an undocumented endpoint acceptable, or is a licensed feed or a
   data vendor required?

## Publication rights registry

`publication/rights.py` records both NSE IX sources as `RESTRICTED` (stricter than their
`display_rights_status`), so the publication gate refuses a GIFT fact on its own; PRE also
withholds GIFT in public output unless `operations.gift_policy` explicitly allowed it
(`presentation/pre_public.py`). The rest of NSE's sources are `REVIEW_REQUIRED` - reachable is
not licensed - see docs/PUBLICATION_POLICY.md.

## Decision

The terms do not clearly establish permission for public display. So:

- `GIFT_NIFTY_PUBLICATION_ENABLED=false` (the default).
- PRE runs **without** GIFT in publication mode and never fetches it there.
- Shadow runs may fetch and validate GIFT for engineering evaluation. The reading is audited
  (`gift_audit_<date>.json`) and never displayed.
- Real NSE IX payloads are **not** committed to `tests/fixtures`. Tests use synthetic responses
  with the same structure.

## To enable (only after written permission or a licence)

1. Record the approval or licence reference here, with its date, scope and any attribution
   requirement.
2. Set `GIFT_NIFTY_PUBLICATION_ENABLED=true` and
   `GIFT_NIFTY_PUBLICATION_APPROVAL=<reference>` in the production environment. The flag alone
   does nothing.
3. Update `display_rights_status` for both NSE IX sources in `core/sources.py`.
4. If attribution is required, add it to the GIFT strip before enabling.
