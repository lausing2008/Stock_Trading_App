# UTC-vs-ET date boundary — triage of the 16 scoped sites (2026-09-21)

**Purpose:** `docs/incidents/utc-vs-et-date-boundary.md`'s own "Scoped, deliberately not
blitzed" section named ~16 files in `services/market-data/src/` carrying the same naive-UTC
date-truncation pattern T409/UW-01 already fixed six real call sites for, explicitly deferring
the triage needed to tell a real bug from a benign one. User asked for "next improvements";
this is that triage, run as a research pass then fixed for every confirmed real finding.

Commits: `10e776c` (pre-existing flaky-test fix, unrelated but found along the way) →
`11382a1` (the fixes below).

## Method

A research pass grepped `datetime.now(timezone.utc).date()`/`date.today()` across
`services/market-data/src/` and `services/signal-engine/src/`, then read each call site's
actual usage to classify it as REAL BUG (the date is compared against, stored as, or used to
window something meant to represent "today" in US-market-session terms, where being off by one
for ~4-5 evening hours produces a wrong/stale/incorrect result), LIKELY FINE (a rolling
lookback window, a leakage guard, a cache TTL where either side of the boundary is harmless), or
UNCLEAR (needs a closer look before deciding). Every REAL BUG finding was independently
re-verified by reading the surrounding function before fixing it — none were fixed on the
research pass's classification alone.

## Fixed — real bugs

### Risk-limit gates (paper_trading_engine.py, conditional_orders.py) — highest severity

Three portfolio-level entry gates in `_scan_for_entries()`, plus `conditional_orders.py`'s own
hand-maintained reimplementation of the first, computed "start of today" as
`datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())`. For ~4-5 hours of
every evening (8pm EDT/7pm EST until market open), that collapses to within about an hour of
the real current instant instead of ~20 hours in the past:

- **Daily realized-loss circuit breaker** — `daily_net_pnl` undercounted, so a genuinely bad
  day's realized losses could fail to trip the breaker.
- **Max-entries-per-day cap** — `entries_today` undercounted, so more entries than the
  configured daily limit could go through.
- **Choppy/risk_off regime entry throttle** — same undercount, so a second throttled entry
  could slip through the "max 1/day" cap.

Fixed with a new `_et_day_start()` helper (paper_trading_engine.py) returning a tz-aware
America/New_York midnight — psycopg2/SQLAlchemy already correctly localizes a tz-aware bind
parameter to UTC when comparing against this app's naive-UTC `DateTime` columns, the same
convention the weekly-loss circuit breaker right below already relied on
(`datetime.now(ZoneInfo("America/New_York")) - timedelta(days=7)`). `conditional_orders.py`
imports and reuses the same helper rather than a second copy.

### Anti-chase drift baseline (paper_trading_engine.py)

`_ref_cutoff = min(_sig_date, date.today() - timedelta(days=1))` — for a signal that fired
today during the bug window, `date.today() - 1` equals the real ET today, so the `min()`
silently picked `_sig_date` (today) right back out, reintroducing the exact AUD-LIVEBAR-T196
look-ahead bug this line's own comment already describes fixing (comparing a signal's reference
price against today's own live, unsettled bar instead of the last settled close). Fixed via
`_et_day_start().date() - timedelta(days=1)`.

### Daily snapshot upsert keys (mis-dating, not a decision bug)

- `PaperEquityCurve`'s daily upsert key (`paper_trading_engine.py`)
- `GexSnapshot`/`OptionsFlowSnapshot`/`VolumeAreaLevel`'s `(stock_id, as_of)` upsert keys
  (`gex_snapshot.py`, `options_flow_snapshot.py`, `volume_area.py`)

All defaulted `as_of`/`today` to a naive UTC date. The three snapshot functions' real EOD batch
job runs at 17:00-17:30 ET, comfortably clear of the evening window — lower practical exposure
than the entry gates — but any late, retried, or manually-triggered run is not, and would
mis-date the row under tomorrow's date. Fixed with the same `ZoneInfo("America/New_York")`
conversion pattern.

### routes.py (9 sites, new `_today_et()` helper added)

- `/earnings_calendar` and `/events/calendar` used `today` as an inclusive lower bound
  (`today <= ned_date <= cutoff`) — a real today-ET earnings/macro event could silently drop
  off the "upcoming" list for part of an evening.
- Two `days_to_earnings` computations (an OpenBB-style payload enrichment helper, and the
  yfinance `ticker.calendar` path) drifted by one day.
- `Fundamental.as_of`'s upsert key — an on-demand fetch endpoint, not a fixed-schedule batch
  job, so more exposed to the bug window than the scheduled snapshot jobs above.
- Both Options Game Plan protective-put/covered-call DTE-window expiry selections
  (`_nearest_expiry_in_dte_window`) — the same bug class `options_income_engine.py` was already
  fixed for (T409), missed at these two separate call sites.
- A price-target days-remaining countdown and a sector-seasonality month filter — lower
  stakes (a display off-by-one; a filter wrong on ~12 evenings/year that straddle a month
  boundary), fixed for consistency since they were already confirmed real.

### signal-engine/analytics.py

A hypothetical time-stop maturity check (`today >= max_exit_date`) in `trade_performance()`'s
backtest/analytics path — the same "has the hold window matured" decision class
`evaluate_signal_outcomes()` (`outcomes.py`) was already fixed for. Reuses the existing shared
`signals_shared._today_et()` rather than a new copy.

## Deliberately not touched this pass

The remaining ~90 sites across `hmm_regime.py`, `hk_connect.py`, `ingestion.py`, most of
`admin.py`/`paper_portfolio.py`'s backtest/equity-curve window cutoffs, and the bulk of
event-intelligence/ml-prediction/portfolio-optimizer/ranking-engine/technical-analysis/
strategy-engine were read and classified LIKELY FINE — rolling lookback-window cutoffs,
retention purges, or training-window leakage guards (`< date.today()` as an upper bound) where
either side of the boundary is harmless (wide windows, N≥7 days) or the bug direction is
actually *more* conservative (excludes one extra day rather than including a wrong one). A
handful of sites in `event-intelligence/earnings.py`, `congress.py`, `political.py`,
`macro_reaction.py`, and `ranking-engine/routes.py`'s `as_of` display labels were flagged
UNCLEAR (not read in depth) — worth the same triage in a future pass, not fixed here.

## Collateral: two pre-existing tests were silently broken by real-clock drift

While running the full suite to check for regressions, `test_t409_utc_date_boundary.py` and
`test_uw01_alert_date_boundary.py` each had one test
(`test_naive_utc_truncation_would_have_gotten_this_wrong`) failing — not from this session's
changes. Both called the TEST file's own real, unpatched `datetime.now(timezone.utc)` instead
of the frozen-clock class the rest of the file uses, so they silently depended on the actual
wall-clock date matching a hardcoded 2026-09-19 literal and started failing the moment the real
system clock moved past it, with zero code regression. Fixed by constructing the frozen instant
directly rather than relying on any patch (commit `10e776c`, separate from the fixes above).

## Testing

38 new tests across 4 new files (`test_aud_t409_scan_gates_date_boundary.py`,
`test_aud_t409_snapshot_upsert_keys.py`, `test_aud_t409_routes_date_boundary.py`,
`test_aud_t409_analytics_time_stop_maturity.py`), every fix sabotage-verified (reverted, test
failed, restored, confirmed green). Migrating two call sites' literal shape required updating 2
pre-existing tests (`test_sector_seasonality_route.py`, `test_t196_livebar_reference_price.py`)
that pinned the old expression in source text — reworded to avoid tripping
`AUD-T401-SOURCETEXTTESTS`'s numeric-assertion ratchet in the process (a `timedelta(days=1)`
literal inside an otherwise-qualitative "which helper is called" check would have counted as a
new numeric threshold pin). Full suite: market-data 4164 passing (0 failures — the two
pre-existing flaky ones now fixed too), signal-engine 504 passing.
