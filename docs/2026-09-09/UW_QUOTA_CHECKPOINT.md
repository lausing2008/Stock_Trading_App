# Checkpoint: verify UW quota after the first full US session on the new ingest adapter

**Check on: Thursday 2026-09-10** (after the 2026-09-09 US session closes and its counters settle).

## Why this checkpoint exists

`AUD-ING-POLYGONDELAYED` (2026-09-09, tier 369) made **Unusual Whales the first-choice adapter
for US price ingestion**. Every quota number measured while scoping that change was taken on
**2026-09-08 — a full US trading day that PREDATES the adapter going live**. So the headroom
figures below are a *projection*, not a measurement, and the whole point of this checkpoint is to
replace them with real numbers.

## What was measured BEFORE the change (2026-09-08, full US day)

| Endpoint | Calls |
|---|---|
| `/api/option-trades/flow-alerts` | 6,612 |
| `/api/darkpool/{symbol}` | 4,326 |
| `/api/stock/{symbol}/gex-levels` | 1,462 |
| `/api/shorts/{symbol}/interest-float/v2` | 383 |
| `/api/congress/recent-trades` | 366 |
| `/api/stock/{symbol}/greeks` | 70 |
| `/api/stock/{symbol}/iv-rank` | 39 |
| `/api/etfs/{symbol}/in-outflow` | 21 |
| **TOTAL** | **13,279 = 11.1% of the 120k/day quota** |

Rate-limit events in 48h: **0**.

## What the change was PROJECTED to add

| Scenario | Added | Total | % of 120k |
|---|---|---|---|
| Daily ingest only (131 symbols x 5 refreshes) | +655 | 13,934 | 11.6% |
| Daily + 5-min intraday (131 x 78 cycles) | +10,873 | 24,152 | 20.1% |

Worst case leaves ~96,000 spare (80% headroom).

## How to check (copy-paste)

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71
D=$(date -u +%Y%m%d)   # or the specific day, e.g. 20260909
tot=0
for k in $(docker exec stockai-redis-1 redis-cli --scan --pattern "stockai:metric:uw_calls:*:$D"); do
  v=$(docker exec stockai-redis-1 redis-cli get "$k")
  echo "$v  $(echo "$k" | sed "s|stockai:metric:uw_calls:||; s|:$D||")"
  tot=$((tot + v))
done
echo "TOTAL: $tot / 120000"

# and the 429 gauge
docker exec stockai-redis-1 redis-cli get dq_check:uw_rate_limit_events_48h
```

## Pass / fail criteria

- **PASS**: total < ~30,000 (25% of quota) AND `count_48h` of rate-limit events is 0.
- **INVESTIGATE**: total > 40,000, or ANY 429s appear.

## The thing most likely to go wrong is NOT the daily total

UW enforces a **per-minute** rate in addition to the daily quota. The 5-minute intraday ingest
fires **131 symbol requests in a burst**, which is far more likely to trip a per-minute limit than
the daily budget is to run out. If 429s appear, **look at burst shape, not daily totals** — the
fix would be to stagger or chunk the per-cycle fan-out, not to reduce the number of refreshes.

Precedent: `AUD-DQCHECKS-VISIBILITY` traced **22,031 rate-limit events in 48h** to ONE uncached
1-minute function (`check_options_flow_alerts`), fixed with a 45s cache. That is the failure shape
to expect.

## Why the every-minute jobs are NOT a quota concern (checked 2026-09-09)

The user asked whether the 17 every-minute jobs would blow the budget. They do not, because
**UW responses are Redis-cached per symbol** — `get_dark_pool_prints()` caches 15 min, so a
per-minute loop over ~20 symbols serves 14 of every 15 cycles from cache. That is why dark-pool
shows 4,326 calls/day rather than the ~28,800 an uncached per-minute loop would produce.

**Cache TTLs govern UW spend, not the job interval.** Changing a job from 1 min to 5 min would
barely move the quota; changing a cache TTL would move it a lot.

## A correction worth keeping (my own error, same session)

While answering the every-minute question I reported three miscadenced jobs
(`dark_pool_alert_outcome_eval_daily` running per-minute, `llm_usage_spike_check` per-minute, and
`signal_alert.skipped` as per-minute waste). **All three were wrong and were retracted.** I had
enumerated jobs by scanning backwards from each `minutes=1,` line, which reads the *preceding*
job's `id=` — so every finding was offset by one registration.

The truth: `dark_pool_alert_outcome_eval_daily` is a proper `CronTrigger(hour=18, minute=30)`;
`llm_usage_spike_check` is not an every-minute job at all; and `check_signal_alerts` runs **5x/day**
from `_run_market_refresh()` (the codebase already says so at `scheduler.py:11295`,
`AUD-DQCHECK-WRONGCADENCE`) — the 452 skip lines were two refresh bursts, confirmed by timestamps
clustering at 03:54 and 03:56 with silence between.

**All 17 every-minute jobs are correctly cadenced. No change was made.**

The generalisable lesson is the same one `pg_stat_user_tables.n_live_tup` taught in the gate audit:
**a cheap proxy that looks authoritative is not a measurement.** Parse the whole `add_job(...)`
block, or read the timestamps.
