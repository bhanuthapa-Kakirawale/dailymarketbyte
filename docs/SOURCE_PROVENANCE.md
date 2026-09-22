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

## Market timestamps

NSE is the only source here that publishes its own "as of" time. `nse_all_indices` used to
parse that timestamp, use it for a date check and throw it away; it now travels through to
`Observation.observed_at`. That is what makes a freshness check mean *how old is this number*
rather than *when did we happen to fetch it*.

Yahoo and Gemini publish no market timestamp, so their observations carry `observed_at = None`
and fall back to `retrieved_at`. That is recorded honestly rather than filled in with a guess.

## Traceability

Every displayed market number resolves:

```
displayed value → report section → fact_id → Fact → Observation → SourceMetadata
```

The report's `sources` block embeds the registry record for every source that contributed, so
an archived report is self-describing: a reader six months later needs nothing but the file.
