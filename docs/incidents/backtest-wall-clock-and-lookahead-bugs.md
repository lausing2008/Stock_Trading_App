## Recurring Issue: BUG233-BACKTESTWALLCLOCK — Phase 2a/2b Backtest Harness Always Returned Zero Trades Unless Run During Real Live Market Hours (Fixed 2026-07-22)

**Found via live-verification** of the newly-deployed Phase 2b endpoint — the exact "does this
actually work" trust-building step the design doc's own §3 calls for. `GET /paper-portfolio/
backtest/min-entry-score?style=SWING&market=US&window_days=60` (Phase 2a, already shipped
2026-07-17) returned `n_entered: 0` out of `n_signals_seen: 45` — every single historical
signal in the window was rejected. Tracing directly into `_should_enter()` found the cause:
`should_enter=False, score=-99, notes=['Market closed — outside regular trading session or a
market holiday']` — for EVERY signal, regardless of style/market/date.

**Root cause**: `_should_enter()`'s first hard reject calls `_is_market_hours(cfg.get("market",
"US"))`, which reads REAL wall-clock `datetime.now()` unconditionally — there was no way to
tell it "evaluate market-hours as of THIS historical date," so a backtest replay run at any
moment outside real live trading hours would have every candidate rejected by this gate before
ever reaching the scoring logic it's meant to test. Two more wall-clock reads inside the same
function (the AUD232-005 time-of-day gate, and the macro-blackout `economic_events` window
query) have the identical problem. This was a genuine, pre-existing bug in the ALREADY-SHIPPED
Phase 2a harness — not something introduced by Phase 2b's own new code — that had simply never
been caught because Phase 2a's own original "verified live on local dev" check (2026-07-17,
per that tracker entry's `implementedNote`) happened to be run during real market hours, so the
bug's symptom (always-zero results outside those hours) was never actually exercised before now.

**Fix (chosen over 2 alternatives — a monkeypatch-based harness approach, or documenting the
limitation without fixing it — after evaluating all 3)**: added an optional `as_of: datetime |
None = None` parameter to both `_is_market_hours()` and `_should_enter()`. Defaults to `None`
→ `datetime.now(timezone.utc)`, so every EXISTING live caller (the real paper-trading scan,
which never passes `as_of`) is completely unaffected — this is purely additive. All 3 wall-
clock reads inside `_should_enter()` now resolve through one `_now = as_of or datetime.now
(timezone.utc)` computed once at the top of the function. `gate_harness.py`'s two replay
functions now pass a real historical `as_of` instead of leaving it `None`.

**A second, deeper bug found while fixing the first**: the harness's first attempt used
`Signal.ts` (the moment the signal was actually GENERATED) as `as_of`. This looked reasonable
but STILL produced `n_entered: 0` — live-checking found 45 of 45 real SWING/US signals in the
test window had `Signal.ts` stamped AFTER 16:00 ET, because signals are frequently (re)computed
by the post-close refresh burst (`scheduler.py`'s `us_post_close` job, ~16:30 ET). This isn't a
`Signal.ts` data-quality problem — it's the exact same T+1 entry-timing model this file's own
`outcome.entry_price` already relies on (`SignalOutcome.entry_date` is deliberately the day
AFTER `signal_date`, precisely to avoid same-day-close lookahead bias — see the SE-F2 fix
history elsewhere in this file). A live trader acting on a signal generated after today's close
enters on the NEXT trading day. Fixed by replacing the `Signal.ts`-based `_signal_as_of()` with
`_entry_as_of(entry_date, market)` — a fixed midday-local-market-time on `SignalOutcome.
entry_date`, comfortably clear of both the market-hours boundary and the time-of-day gate's
open/close edge windows.

**Tests**: 4 new cases in `services/market-data/tests/test_should_enter_de_parity.py` covering
`as_of`'s two guarantees — `None` defaults to unchanged wall-clock behavior, and a real
historical `as_of` overrides both a mocked-closed "real now" AND the time-of-day gate
correctly. 4 new cases in `test_gate_harness_extended.py` for `_entry_as_of()` itself
(US/HK timezone construction, UTC-awareness, and a direct boundary-math check that the
constructed midday instant clears BOTH the market-hours window and the time-of-day gate's
edge windows, not just an `hour == 12` sanity check).

**Two real adversarial-verification near-misses caught during THIS session, not shipped**:
1. The first version of `test_as_of_overrides_the_real_wall_clock_for_the_market_hours_gate`
   passed even after sabotaging the real `as_of=_now` call site — investigated why (the "still
   passes after sabotage" red flag this repo's own testing discipline treats as a finding in
   its own right) and found the test was unknowingly exercising this file's OWN autouse
   `_always_market_hours` fixture, which unconditionally stubs `_is_market_hours` to always
   return `True` for every other test in the file — never the real function at all. Fixed by
   explicitly restoring the real `_is_market_hours` (captured at module-import time, BEFORE the
   autouse fixture ever patches it) inside this one specific test.
2. A related, independent false-confidence trap in the SAME test: an even earlier draft used a
   mocked "real now" UTC timestamp that happened to convert to a SUNDAY in ET — failing the
   market-hours gate for an unrelated reason (weekend closure) regardless of whether the
   `as_of` override logic worked at all. Fixed by deliberately using a weekday off-hours time
   instead. Both traps were only caught by verifying the SABOTAGE actually changed the test's
   outcome, not just that the test passed once.

**Live re-verification after both fixes, against real production data** (not just synthetic
test fixtures): `GET .../backtest/min-entry-score?style=SWING&market=US&window_days=60` now
returns `n_entered: 62` on the train slice with a real `win_rate: 0.6452`, `avg_return_pct:
1.09`. `GET .../backtest/extended-gate?param=min_volume_z` even found a genuinely-promoted
candidate (beat baseline's -3.06% validation-slice return with -1.36%). Full 467-test
market-data suite (up from 459) green after every revert.

**Design invariant, generalized beyond this one bug**: any function meant to be REPLAYED
against historical data must accept an injectable "as of when" parameter for every wall-clock
read inside it — a function that calls `datetime.now()` internally with no way to override it
is fundamentally unreplayable, and this defect can hide silently (the function still "works,"
it just silently produces wrong/empty results) unless a live-verification pass happens to be
run at a moment where the wall-clock coincidentally lines up. **Any future backtest/replay
harness in this codebase must audit every function it calls for internal `datetime.now()`
reads before trusting its results** — this exact bug class (Phase 2a's harness) escaped an
earlier "verified live" claim purely because that verification happened to run during real
market hours, masking the defect until this session's own independent live-check happened to
run outside them.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "as_of=" /app/src/services/paper_trading_engine.py | head -5
docker exec stockai-market-data-1 grep -n "_entry_as_of\|def _is_market_hours" /app/src/backtest/gate_harness.py /app/src/services/paper_trading_engine.py
```
If a backtest endpoint returns `n_entered: 0` again despite a real, non-thin data window,
directly call `_should_enter()` with the exact `signal_data`/`game_plan`/`as_of` the harness
would construct and read `notes` — a `"Market closed"` or `"Time-of-day gate"` reason there
means this exact bug class has recurred (check whether a new wall-clock read was added inside
`_should_enter()` without going through `_now`).

---

