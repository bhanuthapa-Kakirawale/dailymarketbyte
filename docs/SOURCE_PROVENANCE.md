# Source provenance and independence

## The distinction that matters

> **SOURCE AUTHORITY IS NOT SOURCE INDEPENDENCE.**

They answer different questions and are stored separately on every observation:

| | Question | Field |
| --- | --- | --- |
| **Authority** | How much is this source's word worth on its own? | `source_type` |
| **Independence** | Are these two readings *separate evidence*? | `independence_group` |

NSE and Yahoo differ in authority (exchange vs. redistributor) *and* independence. Two Yahoo
readings of the same close differ in neither: they are one witness, however they were fetched.
Corroboration requires independence, not authority — so verification counts **groups**, never
observations.

## SourceMetadata

`core/sources.py` holds one record per source:

```
source_name                 stable id: nse_website, yahoo_finance, gemini, google_news_rss
source_type                 PRIMARY | SECONDARY | BROKER | NEWS | AI | DERIVED
source_family               EXCHANGE | MARKET_DATA_AGGREGATOR | NEWS_DISCOVERY |
                            AI_DISCOVERY | INTERNAL | FIXTURE | UNKNOWN
independence_group          which body of evidence this belongs to
upstream_source             who it got the data from, where known
retrieval_method            how it was fetched
reference                   endpoint or URL
market_timestamp_available  whether the source publishes its own "as of" time
display_rights_status       UNREVIEWED by default - a placeholder, not a licence decision
notes                       what to know about its failure modes
```

## The registry

| Source | Type | Family | Independence group |
| --- | --- | --- | --- |
| `nse_website` | PRIMARY | EXCHANGE | `NSE` |
| `nse_index_close_archive` | PRIMARY | EXCHANGE | `NSE` (same exchange, one witness) |
| `yahoo_finance` | SECONDARY | MARKET_DATA_AGGREGATOR | `YAHOO` |
| `gemini` | AI | AI_DISCOVERY | `GEMINI` |
| `google_news_rss` | NEWS | NEWS_DISCOVERY | `PUBLISHER:<publisher>` |
| `expiry_calendar_rule` | DERIVED | INTERNAL | `INTERNAL` |
| `daily_byte_derived` | DERIVED | INTERNAL | `INTERNAL` |
| `demo_fixture` | DERIVED | FIXTURE | `DEMO_FIXTURE` |

An unregistered source resolves to group `UNKNOWN` rather than being given a plausible one.
An invented independence group is precisely the failure this module exists to prevent.

### News is grouped by publisher, not by aggregator

Google News is an aggregator. Two of its entries syndicating one wire story carry different
URLs and often slightly different headlines, but they are **one publisher and one witness**.
`news_source(publisher)` therefore sets the group from the publisher, keeping
`google_news_rss` as the source name so the retrieval path is still recorded.

Residual gap: the same story syndicated across genuinely different publishers still looks
like two groups. Grouping by publisher is the cheap approximation, not a full identity model.

## Provenance is recorded at acquisition, never reconstructed

Phase 1 inferred a mover's catalyst origin downstream by string-matching the finished text
against candidate headlines, and marked the result `inferred: true`. Phase 2 removed that:
only the acquisition layer can still see which branch it took, so that is where the record
is written.

`news.ai_pass` now stamps each item as it produces it:

| Origin | Meaning |
| --- | --- |
| `GEMINI` | the LLM wrote this reason |
| `GEMINI_SEARCH` | the LLM's grounded search produced this event |
| `GOOGLE_NEWS_RSS` | a clipped headline, with its publisher and headline date |
| `RULE_FNO_EXPIRY` | the deterministic expiry-calendar rule |
| `RULE_NO_EVENTS_FOUND` | the internal placeholder when nothing was found |
| `NO_VERIFIED_CATALYST` | no explanation could be established |
| `DEMO_FIXTURE` | synthetic demo content |

Every catalyst and event in the report now carries `origin`, `source`, `source_type`,
`independence_group`, `publisher` and `provenance_resolved: true`. `inferred` is `false`
throughout, because nothing is inferred any more.

If content safety blocks a reason, its attribution is cleared to `NO_VERIFIED_CATALYST` —
keeping the publisher would credit a headline the viewer never sees.

### Second-source index bars

A benchmark or sector index bar recovered from NSE's end-of-day index file
(`NSE_INDEX_CLOSE_ARCHIVE`, docs/VALIDATION_RULES.md section 10) carries its provenance from the
moment it is fetched. The record holds `source`, `source_url`, `session_date`, `retrieval_time`,
`source_published_at`, `validation_status`, `fallback_reason` and the continuity anchor. When the
Nifty previous close came from it, the NIFTY 50 change observation says so in
`previous_close_source`. The observation's own source stays Yahoo, because the displayed close is
Yahoo's.

The recap session itself can come from the file (recap-session recovery, VALIDATION_RULES section
10.1). In that case the displayed close IS NSE's, so the NIFTY 50 INDEX_CLOSE and INDEX_CHANGE_PCT
observations are recorded under `nse_index_close_archive` (PRIMARY) with
`close_source: NSE_INDEX_CLOSE_ARCHIVE`, and are never labelled Yahoo. The same applies to a Bank
Nifty or sector change built on a recovered close. The archive shares NSE's independence group,
so it can never corroborate NSE's own API as a second witness.

## Market timestamps

NSE is the only source here that publishes its own "as of" time. `nse_all_indices` used to
parse that timestamp, use it for a date check and throw it away; it now travels through to
`Observation.observed_at`. That is what makes a freshness check mean *how old is this number*
rather than *when did we happen to fetch it*.

Yahoo and Gemini publish no market timestamp, so their observations carry `observed_at = None`
and fall back to `retrieved_at`. That is recorded honestly rather than filled in with a guess.

### Pre-open readings (PRE-MARKET V1)

`providers/premarket.py` records, for every overnight cue / India VIX / GIFT reading, a
`PreMarketQuote` (or `VixReading`) with `source`, `source_type`, `independence_group`,
`retrieved_at`, the `market_date` of the session it belongs to, the `market_timestamp` of a LIVE
reading (the END of the last 5-minute bar completed before the cutoff) and a `core.freshness`
verdict - all at acquisition. Yahoo publishes no timestamp for a daily bar, so a US close is
dated by its bar and judged FRESH only if that bar is the expected overnight session and was
final at the cutoff. The readings are written to the run's own `pre_acquisition_<date>.json`;
they are not canonical market history and are never written into a MarketReport.

GIFT Nifty is acquired from the exchange that lists it, NSE IX (`providers/gift_nifty.py`):
`nseix_market_rate` (live NIFTY FUTIDX rows; `TIMESTMP` = the contract's last-trade time,
recorded as `market_timestamp`) and `nseix_settlement_file` (the dated Daily Settlement Price
file the change is measured from). Both are PRIMARY / EXCHANGE and share ONE independence group
(`NSE_IX`) - one venue, one witness; its `validation_status` is `SINGLE_SOURCE` (or
`CONFLICT`/`REJECTED`/`UNAVAILABLE`, never shown). The reading's `provenance` block keeps the
contract, every contract seen, the settlement URL and attempts, the implied reference and the
consistency verdict. Gemini's GIFT answer (`news.ai_pass`) is still POST-only context and is
never a PRE source.

Official schedules: `rbi_press_release` and `federal_reserve_calendar` are REGULATOR sources
(schedule only, never a number). RBI entries live in `data/official_events.json` with their
URL, release number, quoted line, `retrieved_at` and `verified_on` - see docs/PRE_MARKET.md.

`presentation/pre_provenance.py` audits every freshness-sensitive PRE fact (US/Asian indices,
GIFT, India VIX, previous-session Nifty/FII-DII/sectors from the report's own observations,
events, news headlines) and `render_pre` BLOCKS if a displayed fact has AI provenance. FII/DII
or a sector whose canonical fact has only AI observations is dropped from PRE upstream.

## Traceability

Every displayed market number resolves:

```
displayed value → report section → fact_id → Fact → Observation → SourceMetadata
```

The report's `sources` block embeds the registry record for every source that contributed, so
an archived report is self-describing: a reader six months later needs nothing but the file.
