## Recurring Issue: BUG-VOLANOM-STALEMARKET — Volume-Anomaly Alert Fired on a Closed Market's Frozen Daily Volume (Fixed 2026-07-21)

**Symptom:** a user received an "Abnormal Volume Detected" email for an HK stock (2513.HK) at
17:01 HKT — well before HK's 09:30 HKT market open. Log inspection showed the scan logging
`"triggered": 1` continuously, unchanged, for 30+ minutes straight — the tell that the
underlying input was frozen, not genuinely moving intraday data.

**Root cause:** `check_volume_anomalies()` (T257-VOLUME-ANOMALY-ALERT, `services/market-data/
src/services/scheduler.py`) reads `stockai:live_prices`/`stockai:avg_volume` from Redis with
**no check on whether the market a given row belongs to is actually open**. Its own tracker
entry's `impact` field even claimed the job was "market-hours gated" — it never was. The
scheduled 1-minute refresh job (`_live_price_refresh_job`) correctly no-ops when both US and HK
are closed — but `GET /stocks/latest_prices` (`services/market-data/src/api/routes.py`) has its
OWN cache-miss fallback: if the shared `stockai:live_prices` key has expired, it just re-fetches
from yfinance for the whole universe and rewrites the SAME key with a fresh 90s TTL, completely
independent of market hours. Confirmed via production logs: this fallback fired every ~2
minutes for over 3 hours straight (`"live_prices.ok", "source": "yfinance_bulk"`) during a dead
window with zero scheduled refreshes — almost certainly a frontend tab open somewhere polling a
page that calls this endpoint. `_fetch_live_bulk()` itself uses `yf.download(period="2d",
interval="1d")` — a **daily** bar — so every one of those re-fetches just returned HK's last
COMPLETED session's volume again, stamped with a fresh TTL each time. `check_volume_anomalies()`
then read that frozen daily-bar volume as if it were live "this cycle" data.

**Fix applied:** import and check `paper_trading_engine.py`'s already-established
`_is_market_hours()` helper (the same one `_should_enter()`'s fallback gate uses, correctly
handling HK's lunch break and holidays) — both as a whole-scan short-circuit when NEITHER
market is open, and per-row inside the scan loop, since HK can be closed while US is open (or
vice versa) and the shared cache holds both markets' rows at once:
```python
from .paper_trading_engine import _is_market_hours
_us_market_open = _is_market_hours("US")
_hk_market_open = _is_market_hours("HK")
if not _us_market_open and not _hk_market_open:
    return  # whole scan is a no-op — neither market trading

# inside the per-symbol loop:
if _is_hk_sym and not _hk_market_open:
    continue
if not _is_hk_sym and not _us_market_open:
    continue
```

**Deliberately NOT changed**: `GET /stocks/latest_prices`' own cache-miss fallback still
refreshes on-demand regardless of market hours — that's correct behavior for ITS consumers
(watchlists, screener, stock detail pages all legitimately want to show the last known price
even when markets are closed). The bug was specifically that a DIFFERENT consumer (this alert
scanner) treated any populated cache entry as automatically meaning "fresh, currently-trading
data" — fixed at the point of that incorrect assumption, not by changing the shared cache's
general contract.

**Tests**: 4 new cases in `services/market-data/tests/test_volume_anomaly_alert.py` (now 15
total, source-text regression checks — `scheduler.py` can't be imported in this test
environment) confirm: the real `_is_market_hours()` helper is used (not a hand-rolled second
check), the both-closed short-circuit happens before any threshold computation, and HK/US rows
are independently skipped when their own market is closed even if the other market is open.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
both-closed short-circuit entirely, and removing the per-row market-open checks inside the
loop.

Full 331-test market-data suite (up from 327) green; frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_is_market_hours" /app/src/services/scheduler.py
# Confirm no volume-anomaly email fires outside real trading hours for that symbol's market:
docker logs stockai-market-data-1 --since 6h | grep volume_anomaly.done
# A CONSTANT unchanging "triggered": N across many consecutive minutes is the same tell that
# caught this bug — real intraday volume moves minute to minute; a frozen value means the
# underlying cache/data source isn't actually updating.
```

---


## AUD-DIGEST-HOLIDAYBLIND — 13 Emails Sent on Labor Day Presenting Friday's Prices as Live (Fixed 2026-09-08)

All four digest/brief jobs registered with `CronTrigger(..., day_of_week="mon-fri")` and
**nothing else**. A cron trigger cannot know about holidays, and none of the four functions
carried an internal trading-day check — while `_refresh_market()` **in the same file** gates
correctly on the very same helpers.

**The decisive evidence was a contrast inside one process.** On 2026-09-07 (US Labor Day, present
in the file's *own* `_NYSE_HOLIDAYS` table):

```
13 digest events fired      <- 1 premarket_brief, 2 morning_digest,
                               9 post_open_digest, 1 paper_portfolio_digest (5 recipients)
77 `nyse_holiday` skip logs <- the refresh path, same process, same day
 0 US D1 price bars         <- last bar 2026-09-04
```

So the emails presented **Friday's data as live**: 10 "pre-market movers", 4 "futures" readings,
10 "opportunities", and **"39 signal changes" on a day the exchange never opened**.
`send_post_open_digest`'s movers query uses `MAX(Price.ts)`, which is exactly why it rendered
Friday's intraday move as today's. Blast radius: **~10 US and ~15 HK weekday holidays per year**.

**The guard existed, worked, and was simply never applied to the digest registrations.** This is
invisible to error-log monitoring because nothing fails — the platform's characteristic failure
mode, a silent wrong answer.

Fixed with `_open_markets()` / `_is_trading_day_for()`, filtering **per market** rather than
returning early: `send_morning_digest` runs for `["HK","US"]` in one call, so an early return
would have traded a wrong-content bug for a *missing-email* bug by suppressing the HK digest on a
US holiday. Verified live — Labor Day 2026 now returns US=closed, **HK=open**, and a normal day
still passes both.

### Checklist when adding any scheduled job

1. A `mon-fri` cron is **not** a market-open check. Weekday ≠ trading day.
2. Gate on `_is_trading_day_for(market)`, not `_is_*_holiday()` alone (holiday-only helpers
   return `False` on weekends — see AUD-HKWEEKEND above).
3. If the job serves multiple markets, **filter** the market list; don't return early.
4. Ask what the job's queries do when today has no bar. `MAX(Price.ts)` silently reaches back to
   the last trading day and looks completely normal.

---

## AUD-HOLIDAY-2027GAP — Three Drifted Copies of the Holiday Calendar, Two Expiring in 3.5 Months (Fixed 2026-09-08)

The same constant lived in three places that had **already drifted**, with the drift dated:

| Location | Type | Coverage ended |
|---|---|---|
| `scheduler._NYSE_HOLIDAYS` | `frozenset[tuple[int,int,int]]` | 2026-12-25 |
| `scheduler._HK_HOLIDAYS` | `frozenset[tuple[int,int,int]]` | 2026-12-28 |
| `paper_trading_engine._NYSE_HOLIDAYS` | `frozenset[date]` | **2027-12-24** |

Two same-named frozensets, **different element types**, different coverage. HK had no 2027
coverage anywhere at all.

From **2027-01-01**, the scheduler's `_is_us_trading_day()` / `_is_hk_trading_day()` would have
returned `True` on every 2027 holiday — un-gating ingest, signal refresh, and alert checks against
a closed market (exactly the failure the 2026-09-07 logs show the guard *preventing* today) —
while paper trading kept working off its own table. Not an error: **two subsystems disagreeing
about whether the market is open.**

Each table carried the comment *"Extend each year before January"* with **no test, alarm, or DQ
check enforcing it.**

Consolidated into `shared/common/market_calendar.py`. The 2027 NYSE dates were **independently
re-derived** rather than trusted from the copy they came from:

- Juneteenth 2027-06-19 is a **Saturday** → observed Fri 06-18 ✓
- July 4 2027 is a **Sunday** → observed Mon 07-05 ✓
- Christmas 2027-12-25 is a **Saturday** → observed Fri 12-24 ✓
- Good Friday 03-26 confirmed against the Easter computus (Easter 2027 = Sun 03-28) ✓

`assert_calendar_coverage()` replaces the unenforced comment and fails **with runway** — in
October of the final covered year, not on New Year's Day.

### Two things recorded honestly

1. **The HK 2027 lunar dates are a reconstruction, not a transcription.** Lunar New Year, Ching
   Ming, Buddha's Birthday, Tuen Ng, Mid-Autumn and Chung Yeung cannot be derived from any
   weekday rule. They are marked `PROVISIONAL / VERIFY AGAINST HKEX` **in the source** rather than
   presented as authoritative. Still strictly better than no coverage: a slightly-wrong closure
   date costs one skipped refresh; a *missing* one runs a full cycle against a shut exchange.
2. **The test suite could not have caught this.** `conftest.py` stubs `common` with a blanket
   `MagicMock`, under which `date(...) in NYSE_HOLIDAYS` returns a **truthy Mock** — so every
   holiday test would have passed regardless of what the calendar contained, or whether it
   contained anything at all. The real module is now loaded (same pattern as `common.indicators`)
   and the suite carries a non-vacuity assertion.

**Also corrected a wrong comment:** `_HK_HOLIDAYS` said 2026-12-28 was *"Boxing Day observed (Mon
after Sat+Sun Christmas)"*, but **Christmas 2026 is a Friday** (Boxing Day Dec 26 = Sat). The date
was right and the reasoning wrong — precisely how a maintainer extending the table by copying the
logic gets the next year wrong.

---
