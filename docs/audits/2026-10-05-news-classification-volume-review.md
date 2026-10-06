# News classification volume: production review

Measured 2026-10-06 00:28–00:33 UTC (October 5 Pacific). Read-only production PostgreSQL queries over SSH; no settings, runtime code, deployments, or production records changed.

## Findings

Fixed measurement window: `[2026-10-05 00:28:09.921121, 2026-10-06 00:28:09.921121)` UTC. API calls use `created_at`; article rows use `ingested_at`. Current active stock membership is measured now, not reconstructed historically.

The first query exactly reproduced the dashboard: 941 news-classification calls, 202,706 input tokens and 52,937 output tokens (255,643 total), handling 1,075 headline inputs. Two earnings-forecast calls used another 3,249 tokens. News therefore accounts for 98.7% of displayed tokens.

| Source | Distinct stored URLs | Classified URLs | Classified with an active tracked symbol | Classified without any active tracked symbol |
|---|---:|---:|---:|---:|
| Alpaca | 968 | 968 | 134 | 834 |
| SEC EDGAR | 3,043 | 106 | 106 | 0 |
| PR Newswire | 162 | 0 | 0 | 0 |
| Business Wire | 21 | 0 | 0 | 0 |

There were 189 active stocks. An article counts as tracked when **any** of its symbol rows joins an active stock; multi-symbol articles are counted once per source/URL. The 834 out-of-universe Alpaca articles are 86.2% of classified Alpaca URLs and 77.7% of all classified URLs in this window. This is not a measured token-saving percentage: token logs do not identify source or article.

Out-of-universe does not mean useless. Examples of symbol mentions include SPY (33 distinct URLs), USO (22), BTCUSD (19), GOOGL (14), PTC and PCVX (11 each). These counts can overlap across articles. Of the 834 out-of-universe articles, 58 were classified as macro and 597 as material; these are model labels, not independent evidence of utility. Preserve an explicit market/sector context scope rather than blindly deleting all non-watchlist news.

## Root cause in the running code

The production SHA-256 values matched local code for `storage.py`, `alpaca_source.py`, and `classify.py`:

- storage: `b888170647df706eadb5dd9d8731635414943f85ed6cf2f461196432efff81aa`
- Alpaca source: `234c0ca8ce427e848405f457041c99af1a570e24a9f8829ae2fe89690e5b5644`
- classifier: `3301bca38bafdf7e4695c059888346f68636fd0030560be0958bda9ef8f97f78`

`alpaca_source.py` subscribes to `news: ["*"]`. In `persist_news_items`, tagged mode takes the provider's `symbols` list as-is. `_to_classify` accepts any nonempty list; it does not intersect those tags with the active universe. Thus provider-tagged identity is treated as relevance. RSS resolves against the active universe; EDGAR uses the stored CIK map. The intended untracked-headline saving does not apply equally across sources.

The available container logs contained zero `resolver_unavailable_classifying_all` occurrences over the requested 24 hours; this is scoped to retained logs, not proof of complete historical log coverage.

## Batching and repeat checks

| Headlines per API call | Calls |
|---|---:|
| 1 | 834 |
| 2 | 88 |
| 3 | 12 |
| 4 | 6 |
| 5 | 1 |

88.6% of calls contain one headline; average batch size is 1.14. Single-headline calls account for 219,247 tokens (85.8% of news tokens). The classifier supports eight items, but Alpaca flushes when its buffer reaches five or a receive times out after five seconds. That timeout is an inactivity timeout, not an oldest-item age deadline; buffering changes should use an explicit latency bound.

No `(source, URL, symbol)` duplicate groups with classification were found in this window. There were 35 excess EDGAR rows with NULL symbol; those duplicate rows were unclassified. No article rows had missing URLs in the initial measurement. Multiple symbol rows (1,849 Alpaca rows for 968 URLs) are not evidence of repeated LLM calls.

1,075 logged headline inputs versus 1,074 classified source/URL identities is not an exact article-level reconciliation. Call logs contain headline count, but no article IDs/hashes or source. Different call/ingestion timestamps, failures, or repeated inputs cannot be separated for that one-item difference. Do not claim zero repeated API classifications from database uniqueness alone.

## Is today unusual?

News-classification tokens by UTC date: Sep 30 244,094; Oct 1 240,104; Oct 2 215,181; Oct 3 7,054; Oct 4 8,613; Oct 5 255,019. The measured rolling window adds 624 tokens just after Oct 6 midnight. This is sustained weekday usage with a large weekend drop, not evidence of a new runaway incident. This review does not verify the economic value of those classifications.

## Suggestions, in order

1. Define eligibility before classification: tracked stocks plus an explicit market/sector/macro context scope. Retain all raw news; skip or defer LLM labels outside that scope. Keep a visible unavailable state if the scope resolver fails, with a bounded fallback budget rather than unbounded classify-all. Test mixed-symbol articles and market stories without tracked-company tags.
2. Instrument each batch with source, stable article/version identifiers or content hashes, eligibility reason, batch size, and classifier policy version. Show unique eligible/classified articles, duplicate skips, tracked/context/out-of-scope counts, and tokens per headline. Avoid logging credentials or unnecessary article bodies.
3. Improve microbatching within an explicit maximum article age. Preserve prompt handling for material-news risk checks; do not promise eightfold savings or trade away latency silently. Measure delay and tokens per headline before and after.
4. Add per-source/classifier token budgets and daily-cost alerts alongside spike alerts. A steady high weekday baseline will not necessarily trigger a spike detector. Budget exhaustion must preserve ingestion and expose classification as deferred/unavailable, not neutral or non-material.
5. Evaluate low-value filing categories separately: only two of 106 classified EDGAR articles were labelled material, but that alone is insufficient to drop filings. Measure downstream use and missed-event risk before introducing deterministic filtering.

Unusual Whales 429s are a separate market-data issue. These news measurements do not diagnose that provider's quota or pacing.

## Reproduction query for source relevance

Run in a read-only transaction with a statement timeout. Use the fixed window above to avoid moving comparisons:

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '25s';
WITH articles AS (
  SELECT source, url,
         bool_or(sentiment_score IS NOT NULL) AS classified,
         bool_or(EXISTS (
           SELECT 1 FROM stocks s WHERE s.symbol = n.symbol AND s.active
         )) AS tracked
  FROM realtime_news_items n
  WHERE ingested_at >= timestamp '2026-10-05 00:28:09.921121'
    AND ingested_at < timestamp '2026-10-06 00:28:09.921121'
  GROUP BY source, url
)
SELECT source, count(*) AS articles,
       count(*) FILTER (WHERE classified) AS classified,
       count(*) FILTER (WHERE classified AND tracked) AS classified_tracked,
       count(*) FILTER (WHERE classified AND NOT tracked) AS classified_untracked
FROM articles GROUP BY source;
COMMIT;
```
