# UTC-vs-ET Date Boundary — "today" silently means the wrong day for part of every evening

## AUD-T409-UTCDATEBOUNDARY (found 2026-09-18, reported by the user as a confusing staleness banner)

**Symptom.** The Options Income page showed *"Chain data is 2 days old (2026-09-17)"* at
8:40pm ET — right after the daily capture job had run normally and the archive correctly held
Thursday's own settled close. That is one day old, not two. The user asked, reasonably, whether
Unusual Whales simply doesn't have real-time data. It wasn't UW. It was date arithmetic.

**The bug.** `datetime.now(timezone.utc).date()` — and the equivalent `date.today()` on a
UTC-configured server — truncate the current *instant* to a calendar date in UTC. UTC crosses
midnight at **8pm EDT / 7pm EST**, hours before the US trading day these dates are meant to
describe has any reason to be "over". Confirmed live:

```
Frozen at 2026-09-19T00:40:00 UTC  (= 2026-09-18 20:40 ET)
  datetime.now(timezone.utc).date()                              -> 2026-09-19   WRONG
  datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()  -> 2026-09-18   correct
```

`(Sept 19 − Sept 17).days == 2`. The banner's arithmetic was correct; its input was a day ahead.

**The more serious, still-dormant consequence.** The options-income scheduled step fires at
`19:00 America/New_York` — which is **00:00 UTC the following day during EST** (winter,
~Nov–Mar). On a true Friday, the naive UTC date at that exact instant is **Saturday**:

```
Frozen at 2026-01-17T00:00:00 UTC  (= 2026-01-16 19:00 EST, a Friday)
  naive:   weekday() == 5   ->  "if today.weekday() >= 5: skip"  fires
  correct: weekday() == 4   ->  Friday, does NOT skip
```

So `if today.weekday() >= 5: return {"skipped": "weekend"}` would have silently treated **every
EST-season Friday's real settlement/entry run as a weekend and skipped it entirely** — no error,
no log line distinguishing it from an actual non-trading day. This has not happened yet only
because the finding was made and fixed during EDT (summer clock), before DST ends (~Nov 1) made
it live.

**Why this is a date-arithmetic bug and not a UW limitation.** UW's chain endpoint is genuinely
historical by design (`docs/incidents/external-data-source-liveness.md`'s T404 entry covers
that, honestly, as a real limitation with its own disclosure). This bug is upstream of that: it
mis-identifies *which calendar day* to even ask about, for part of every single evening,
regardless of which provider answers.

## Six call sites, one root cause

All six were reached this session while building the options-income and options-chain surface,
none pre-existing:

| File | Sites | Consequence of the bug there |
|---|---|---|
| `options_income_engine.py` | 4 | Chain-staleness gate, DTE-window filtering, per-day entry-budget key, **and the Friday-skip above** |
| `options_income.py` (API) | 1 | `days_to_expiry` on every open position understated by 1 during the affected window |
| `options_strategies.py` | 1 | Same DTE understatement, on the Game Plan / Options Calculator legs |
| `uw_option_chain.py` | 1 | Staleness check and the on-demand fetch window both computed against the wrong day |
| `options_game_plan_snapshot.py` | 2 | DTE selection; and a wrong `as_of` is a **wrong primary-key date on a stored row**, not just a display glitch — though this job runs at 17:30 ET, before the affected window, so it was not yet exercised in practice |

**Fixed with one canonical helper**, `_today_et()` in `options_income_engine.py`, reused or
inlined identically elsewhere — matching how `_is_us_trading_day()` already performs the same
`datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))` conversion elsewhere in
`scheduler.py`. **This was never a missing technique.** The correct pattern already existed in
the same codebase; these six call sites simply never adopted it.

16 tests, using a frozen-clock `datetime` subclass so `_today_et()` exercises its real code
path rather than a parallel reimplementation under test. Sabotage-verified in two ways:
reverting the shared helper to the naive version, and reverting one delegate site independently
— both fail immediately.

## Scoped, deliberately not blitzed: ~16 more files carry the same pattern

A bounded grep of `services/market-data/src/` alone (one service, not a full-codebase sweep)
found the identical `datetime.now(timezone.utc).date()` / `date.today()` pattern in 16 further
files, including `scheduler.py`, `paper_trading_engine.py`, `routes.py`, and
`unusual_whales.py`. **Not every one of those is a bug.** Some are near-certainly fine — a
UTC-keyed Redis TTL, a log timestamp, a context where which side of a midnight boundary a value
falls on genuinely doesn't matter. Distinguishing those from the real ones is a triage task, the
same shape as `docs/audits/2026-09-18-c02-c03-outcome-horizon-scoping.md`'s reference audit: real
work that gates a safe fix, not something to rush through at the tail of an unrelated change.

**Recorded here as the next occurrence of this bug class to look for**, not as a to-do assigned
without a plan: when touching any of the sixteen files above, check whether its `today`/`date`
computation needs to respect market hours, and if so, use `_today_et()` (or the equivalent
inline `ZoneInfo("America/New_York")` conversion) rather than a bare UTC truncation.

**TRIAGED AND FIXED (2026-09-21).** The full sweep across `paper_trading_engine.py`,
`conditional_orders.py`, `routes.py`, three snapshot-upsert modules
(`gex_snapshot.py`/`options_flow_snapshot.py`/`volume_area.py`), and signal-engine's
`analytics.py` found the real/likely-fine/unclear split the section above predicted. Highest
severity: three portfolio-level entry gates in `_scan_for_entries()` (daily realized-loss
circuit breaker, max-entries-per-day cap, choppy/risk_off regime entry throttle) and
`conditional_orders.py`'s own reimplementation of the first all silently saw only the last few
minutes of the day's trades during the evening window — a real risk-limit weakened, not just a
display glitch. Also fixed: the anti-chase drift baseline (reintroducing AUD-LIVEBAR-T196 for
any signal that fired today during the window), an equity-curve upsert mis-dating, three
snapshot-upsert mis-datings, nine `routes.py` sites (earnings calendar dropping a real today-ET
event, two days-to-earnings drifts, a Fundamental upsert key, two Options Game Plan DTE
selections, a countdown, a seasonality filter), and one signal-engine hypothetical-maturity
check. New helpers: `_et_day_start()` (paper_trading_engine.py, returns a tz-aware datetime) and
`_today_et()` (routes.py, returns a date — same name/shape as the sibling copies already in
scheduler.py/options_income_engine.py/signals_shared.py). 38 new tests, all sabotage-verified.
Full detail: `docs/audits/2026-09-21-utc-date-boundary-triage.md`.

Not fixed, and not re-triaged this pass: the remaining ~90 sites (rolling lookback-window
cutoffs, retention purges, training-window leakage guards) the original scoping note already
expected to be mostly benign. A future pass touching any of them should still apply the same
"does this need to respect market hours" check before assuming it's fine.

## The lesson

A naive `datetime.now(timezone.utc).date()` is wrong for **4-5 hours of every single day** for
any purpose tied to the US trading calendar — not a rare edge case, a nightly one. It is
also the *quietest* possible way to be wrong: no exception, no log line, just a number that is
off by exactly one for part of the evening and correct the rest of the time, which is precisely
the kind of intermittent-and-plausible error that survives code review and slips into
production. Anywhere "today" is meant to describe a US market session, it must be computed in
`America/New_York`, never assumed to fall out of the server's own clock for free.
