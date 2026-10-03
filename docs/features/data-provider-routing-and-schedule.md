# Data providers and the ingest schedule: who does what, when

Consolidated 2026-10-02, after a day in which three separate defects all traced back to the
same gap — **nobody could see, in one place, which provider served which market at which
time.** The duplicate 9am fire, the unmetered UW bar path, and paper trading running before
the open were each invisible for the same reason.

This page is that one place. It is descriptive: everything here is read off the code, and the
tests named at the end fail if the code and this page drift apart.

## Who serves what

Adapter priority is global — `_PRIORITY = ["unusual_whales", "yfinance", "alpha_vantage",
"polygon"]` — but each adapter declares what it can serve, so the effective routing is:

| Market | Timeframe | Order tried | Why |
|---|---|---|---|
| US | 1d | UW → yfinance → alpha_vantage → polygon | UW is authenticated and metered; it exists because Polygon's DELAYED plan silently returned empty for 4 symbols for 4 days (`AUD-ING-POLYGONDELAYED`) |
| US | 5m | UW → yfinance → polygon | same priority; UW declares 5m support |
| HK | 1d, 5m | **yfinance only** | UW has no HK coverage — `supported_markets = ("US",)` |

**yfinance is the backup for US and the sole source for HK.** That is already the design; it
is not a change. A UW failure falls through to the next adapter in the list, so no bar is lost
when UW refuses — it costs a wasted request and a delayed bar.

**The fallback is also why both providers throttle together.** Every UW failure becomes a
yfinance call for the same symbol, so one stream of traffic lands on both. When counting
provider load, count the *attempts*, not the successes.

## When ingestion runs

| Job | Window (venue local) | Cadence | Symbols | Calls/day | Also does |
|---|---|---|---|---:|---|
| `us_premarket_5m_early` | 04:00–08:45 ET | **15 min** | 142 US | 2,840 | ingest only |
| `us_5m_intraday` | 09:00–15:55 ET | 5 min | 142 US | 11,928 | + paper trading, **regular session only** |
| `hk_5m_intraday` | 09:00–15:55 HKT | 5 min | 42 HK | 3,024 | + paper trading, **regular session only** |
| daily refreshes | around the close | 5×/day | all | ~710 | rankings, signals |

Two things that are easy to misread, and both caused a defect today:

1. **A cron minute list applies to EVERY hour in the hour list.** `hour="9,…,15"` with
   `minute="30,…,55,0,…,25"` fires at 9:00, not from 9:30. A job written to hand over "at
   9:30" was duplicating six slots a day for months (`AUD-5M-DUPLICATE-9AM`). Expand a
   trigger into its real fire times before reasoning about it.
2. **`us_5m_intraday` ingests from 09:00 but trades only from 09:30.** The ingest is
   deliberately ungated — premarket bars feed the 08:00 gappers brief — while the
   money-moving steps are gated on `is_regular_session()` (`AUD-PAPER-PREMARKET`).

## What is metered, and what used to be invisible

| Path | Counted in `uw_calls` | Feeds the 429 gauge |
|---|---|---|
| `services/unusual_whales.py` (options chains, flow, GEX, dark pool, …) | yes | yes |
| `adapters/unusual_whales_adapter.py` (OHLC bars) | **yes, since 2026-10-02** | **yes, since 2026-10-02** |

Before that date the adapter built its own `httpx.Client` and reported nothing. Measured
consequences: the usage dashboard read 75,811 calls for 2026-10-02 and listed no `ohlc`
endpoint while the adapter was making an estimated ~14,768 (≈20% of real usage unaccounted,
against a 120k budget); and the rate-limit gauge read 5–16/hour while the adapter's own 429s
ran ~232/hour — a ~33× undercount of the very condition it exists to show.

Bar-ingest failures are now classified by cause (`rate_limited`, `http_{status}`, `timeout`,
`no_key`, `unsupported_timeframe`, `other_{Type}`) into hourly counters that survive a
container rebuild, and shown on the admin health page with an at-a-glance indicator in the
section header. The question "what is the failure from UW and why" was previously
unanswerable after a restart; it is now answerable from stored state.

## Where the UW budget goes

Measured 2026-10-02, counted calls only (the ~14,768 adapter calls were not yet counted):

| Endpoint | Calls | Share |
|---|---:|---:|
| `/api/stock/{ticker}/option-chains` | 67,607 | 89.2% |
| `/api/stock/{symbol}/gex-levels` | 3,088 | 4.1% |
| `/api/darkpool/{symbol}` | 2,628 | 3.5% |
| `/api/option-trades/flow-alerts` | 1,506 | 2.0% |
| everything else | ~980 | 1.3% |
| **counted total** | **75,811** | |
| *+ uncounted OHLC (est.)* | *~14,768* | |
| **true total (est.)** | **~90,600** | of a 120k budget |

Option chains dominate. The bar path is ~16% of true usage and was 100% of the invisible part.

## Throttling behaviour

`ingest_universe` walks ~142 US symbols in a tight loop every five minutes with UW first. Once
UW starts refusing, each symbol used to pay a full round trip to be told 429 before falling
through — 142 wasted calls per burst, against the quota that was already exhausted.

A 120-second cooldown now turns that into one wasted call per window: the first 429 arms it,
`supports()` reports False while it holds, and the registry routes straight to yfinance. It is
deliberately short — a step-aside, not a circuit breaker with a long memory — and fail-open,
so a Redis outage cannot remove a working data source.

## What this page does not settle

- **Whether UW should be first for 5-minute bars at all.** The adapter's own docstring budgets
  for daily bars — "131 US symbols × 5 refreshes = 655 requests/day… 0.55% of that budget" —
  and intraday routing makes it ~23× that. The cooldown makes a throttled UW cheap rather than
  making the volume right. The failure counters will show whether UW can sustain it; that is a
  provider-strategy decision, not a defect to fix quietly.
- **Whether premarket ingestion earns its 2,840 calls/day** for one section of one email.
- **Unusual Whales' non-429 failures.** As of writing, ~10,342 of 13,131 failures in a
  measured 12-hour window had no established cause. The counters above exist to answer exactly
  that at the next session; until they have data, the honest answer is that it is unknown.

## Kept honest by

`test_5m_schedule_no_duplicate_fires.py` (triggers expanded into real fire times, verified to
fail against the old schedule), `test_paper_premarket_session_gate.py` (session boundaries
including the HK lunch closure; ingestion explicitly NOT gated),
`test_uw_adapter_instrumentation.py` (every failure mode counted, fail-open reporting,
cooldown present and short), `test_premarket_gappers.py` (window covered with no gap).
