# Runtime counters read — UW failures, premarket ingestion, recovery markers

Read 2026-10-03 05:30 UTC (22:30 PDT Oct 2) from production Redis. **Read-only. Nothing was
written, reset or deleted.**

Covers the last two open items: *"Evaluate UW failures and premarket ingestion using the new
runtime counters; inspect current recovery-marker state before considering any reset."*

## 1. UW failures — 119 rate-limits in 48h, all inside market hours

`stockai:metric:uw_rate_limit_count_hourly:*`, hourly UTC buckets, 429s only:

| UTC hour | Oct 1 | Oct 2 |
|---|---:|---:|
| 05 | — | 1 |
| 08 | — | 3 |
| 13 | 5 | 16 |
| 14 | 10 | 6 |
| 15 | **20** | 6 |
| 16 | 8 | 5 |
| 17 | 7 | 8 |
| 18 | 4 | 9 |
| 19 | 4 | 7 |
| **total** | **58** | **61** |

Every bucket from 20:00 UTC Oct 2 onward is **empty** — 9.5 hours with zero 429s. US regular
session is 13:30–20:00 UTC, so the throttling is confined to it and stops dead at the close.

**The cause is NOT the daily budget.** This app's own call counter puts UTC Oct 2 at **75,811**
calls against a 120,000/day limit (63%), in line with the 76.3k previously measured, and UW's own
authoritative response headers reported `daily_count 1423 / daily_limit 120000` with
`minute_remaining 1000000` at the time of reading. So the account had both daily and per-minute
headroom while being refused.

**What these counters cannot tell us is WHICH endpoint was refused.** `_incr_rate_limit_counter()`
takes no path argument — it increments one global hourly bucket, so a 429 on the 67,607-call
option-chains sweep and a 429 on a 5-call max-pain lookup are indistinguishable.

option-chains being 89% of call VOLUME is a reason to look there first; it is **not** an
attribution of 89% of the failures, and nothing in this reading apportions failures by endpoint
at all. Nor does headroom on the daily and per-minute dimensions rule out burst, concurrency,
entitlement or account-scoped limits — those are simply dimensions these counters do not observe.

**DONE since this reading:** the main client now counts failures by endpoint template and cause
(`stockai:metric:uw_failures:{endpoint}:{cause}:{YYYYMMDDHH}`), surfaced on the usage dashboard
as `client_failures_24h`. Labels are bounded — the template, never the resolved path, because
`/api/darkpool/MU` would mint one key per symbol per hour.

### The adapter's new failure counter says nothing yet, for two reasons

`stockai:metric:uw_adapter_failures:*` is **empty**. Both reasons must be stated or the zero
will be misread as good news:

1. It has been live roughly **four hours** (deployed in `151918f3` at 18:24 PDT Oct 2) — below
   the threshold at which an absence means anything.
2. It is written **only** by `adapters/unusual_whales_adapter.py`, which serves the bar/price
   path. The endpoints carrying nearly all UW traffic go through
   `services/unusual_whales.py`, which has never had a failure counter — it records a
   `_record_call_status` snapshot into **one key with a 2-minute TTL**, overwritten by every
   subsequent call. That is a liveness indicator, not a history, and no count of timeouts, 5xx
   or auth failures exists for the main path at all.

The call counter is pooled across **three** writers (`services/unusual_whales.py`,
`adapters/unusual_whales_adapter.py`, `shared/common/uw_congress.py`), which is why it has 37
hours of history while the adapter is four hours old. Worth knowing before attributing a call
count to the adapter's work.

Also noted: `stockai:metric:uw_rate_limit_count_48h`, the rolling key, is **empty** while its
hourly buckets are populated. Traced afterwards: it is a RETIRED sawtooth that the hourly
buckets already replaced, and every consumer sums those buckets at read time — so nothing reads
it and nothing misreports because of it. It has now been renamed to say so in the source, since
this reading had to establish "retired" rather than "zero" from first principles.

## 2. Premarket ingestion — not yet measurable

No yfinance call counter exists anywhere in the codebase; the 8,520 → 2,840 premarket figure was
a projection from the cron change, never a measurement. The only runtime evidence would be
market-data's logs during a premarket window, and those are gone: `docker logs` resets on
container recreation and this service was rebuilt twice today.

**The next US premarket (08:00 UTC / 04:00 ET) is the first chance to measure this.** It cannot
be back-filled.

## 3. Recovery markers — two live grants, neither touched

`paper:consec_loss_recovery:{portfolio_id}`, 7-day TTL, value = the consecutive-loss streak the
grant was issued at:

| Portfolio | Streak at issue | Issued (UTC) | Age | Expires in |
|---|---:|---|---:|---:|
| 2 | 4 | 2026-09-30 01:31 | 73.3 h | 3.9 d |
| 5 | 10 | 2026-09-30 13:36 | 61.2 h | 4.5 d |

Both were issued ~3 days ago and lapse on their own in under 5 days. No other portfolio holds
one.

**Nothing was reset — M02 marker deletion remains unauthorised.**

**CORRECTION (lifecycle review, same day).** An earlier version of this section argued the two
markers were "not stuck artefacts of a loop" because they carry different streak lengths, were
issued 12 hours apart, and are ageing out normally. That inference does not hold: **a declining
TTL establishes expiry behaviour, not whether the original recovery attempt was legitimately
consumed.** Decay is what a marker does whether its grant was used well or wasted. The
provenance question — what each grant was spent on — is unresolved and stays open; nothing here
argues for or against a reset.

## What this leaves open

1. **Path-level 429 attribution** — the one change that would make the next reading diagnostic.
2. **Failure counting on the main UW client** — the 89% path has no failure history.
3. **Premarket verification** at the next open.
4. The rolling 48h rate-limit key reading zero while its buckets are populated.
