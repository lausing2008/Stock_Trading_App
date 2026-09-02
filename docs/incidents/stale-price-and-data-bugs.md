## Recurring Issue: BUG-MONITORPOS-STALEPRICE — `_monitor_positions()` Could Run Exit Checks Against a Frozen Price Forever (Fixed 2026-07-21)

**Symptom:** none reported yet — caught during a routine AUD256 follow-up audit, before it
could produce a real incident.

**Root cause:** `_monitor_positions()`'s missing-live-quote fallback
(T234-PT-MONITOR-MISSING-PRICE-FALLBACK, `services/market-data/src/services/
paper_trading_engine.py`) used the standard 3-tier fallback (live → cached `current_price` →
`entry_price`) whenever a live quote was missing for a symbol — correct in principle, but it
then unconditionally overwrote `trade.current_price` with that SAME fallback value every
cycle (this loop runs every 5-10 minutes per the module's own docstring), with **no tracking
of how many consecutive cycles a real quote had failed to arrive**. A single
`log.warning()` per cycle looked identical whether it was the first missed tick or the
fiftieth — a genuinely bad multi-cycle data outage (feed issue, exchange halt, delisting)
could leave a position's stop/target/trailing-stop checks running against an increasingly
frozen price for an unbounded time with zero visibility or escalation.

**Fix applied:** track consecutive stale cycles in Redis
(`stockai:monitor_stale_price:{trade.id}`, 1h TTL — deliberately transient/diagnostic state,
not a new DB column, since this doesn't need to survive a restart and a schema change would be
a heavier, riskier fix than this bug warrants). The counter increments each cycle the fallback
fires and is cleared the moment a real quote arrives again, so one missed tick followed by a
healthy cycle doesn't carry a false streak into a later, unrelated gap. Once the streak
crosses 5 consecutive cycles (~25-50 minutes of missing quotes at this loop's cadence), the
log escalates from `warning` to `log.error("paper.monitor_price_stale_escalation")` with the
actual `stale_cycles` count included:
```python
_stale_count = 0
try:
    _stale_redis = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    _stale_count = int(_stale_redis.incr(f"stockai:monitor_stale_price:{trade.id}"))
    _stale_redis.expire(..., 3600)
except Exception:
    _stale_count = 0  # fail-open

if _stale_count >= 5:
    log.error("paper.monitor_price_stale_escalation", stale_cycles=_stale_count, ...)
else:
    log.warning("paper.monitor_price_fallback", stale_cycles=_stale_count, ...)
```

**Deliberately NOT changed**: which price is actually used for exit math (the existing
live → cached → entry_price fallback stays exactly as-is), and no automatic force-close/halt
behavior was added on a stale streak — both would be separate, larger, more consequential
decisions than this fix, which is diagnostic-visibility-only.

**A real test-writing gotcha caught while building this**: the first draft's Redis-failure
guard only wrapped `.incr()` in `try/except`, with `int(...)` applied OUTSIDE that block —
under this test environment's stubbed `redis` module (`MagicMock()`), `int(MagicMock())`
actually succeeds and returns `1` by default rather than raising, so this specific stub
wouldn't have surfaced the gap — but a genuinely malformed Redis response in production (or a
differently-behaving stub) could still have crashed past the guard. Fixed by moving the
`int(...)` conversion inside the same `try` as the `.incr()` call itself, so both failure
modes are caught by the identical `except Exception: _stale_count = 0` fallback.

**Tests**: `services/market-data/tests/test_monitor_positions_stale_price.py`, 8 cases —
source-text regression checks (matching `test_scheduler_static_names.py`'s established
pattern for this exact risk class; `_monitor_positions()` itself has 200+ lines with heavy
Signal/RSI/regime dependencies that would need a disproportionately large fixture harness for
what is an additive, self-contained change). Confirms: the Redis counter is tracked and
TTL'd, the `int()` conversion sits inside the same try/except as the Redis call, a Redis
failure falls back to `0` rather than crashing, the escalation threshold correctly gates
`log.error` vs `log.warning`, both log lines include the actual `stale_cycles` count, a real
quote arriving clears the streak, and the staleness tracking never changes the actual
fallback price computation or its ordering.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: removing the TTL,
removing the escalation branch entirely (always warning), and removing the streak-clearing on
a real quote.

Full 339-test market-data suite (up from 331) and frontend typecheck green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n 'monitor_stale_price\|monitor_price_stale_escalation' /app/src/services/paper_trading_engine.py
docker exec stockai-redis-1 redis-cli keys 'stockai:monitor_stale_price:*'
# If a real stale-price escalation is suspected, check for the error log directly:
docker logs stockai-market-data-1 --since 2h | grep 'monitor_price_stale_escalation'
```

---


## Recurring Issue: BUG-TALEVELS-EMPTYPIVOTS-FLOATIDX — GET /ta/{symbol}/levels Crashed for
## Any Thin-History Stock (Fixed 2026-08-20)

**Found in the same log-sweep pass as the decision-engine bug above.**

**Symptom**: `GET /ta/{symbol}/levels` 500'd repeatedly (86 occurrences in 8h) exclusively for
`SSNLF`/`SKHYV` — both already independently flagged elsewhere as possibly-delisted, thin-
history symbols. Confirmed `SSNLF` has only 62 real daily price bars in production.

**Root cause**: `trendlines.py`'s `_find_pivots()` returned `np.array(highs), np.array(lows)`
where `highs`/`lows` are plain Python lists built by appending indices during a local-max/min
scan. `np.array([])` (numpy's own default dtype inference for an EMPTY list, with no dtype
hint) produces a **float64** array, not an integer one. `_cluster_pivots()` then does
`df["high"].values[highs_idx]` — indexing a real array with a float64 array raises a raw,
unhandled `IndexError: arrays used as indices must be of integer (or boolean) type`. This
triggers whenever the pivot-detection loop finds ZERO local extrema — either a too-short
history (fewer bars than `2×order+1`, so the loop's own `range(order, n - order)` never
iterates at all) or a genuinely strictly-monotonic series (no interior bar is ever a local
max/min of its own window). `detect_support_resistance()` calls `_cluster_pivots()` TWICE
(once on the last-90-bars `local_df`, once on the full `df`) — either call could
independently trigger this.

**Fix applied**: `np.array(highs, dtype=int), np.array(lows, dtype=int)` — a no-op for the
normal (non-empty) case, since a real list of Python ints already produces an int64 array
regardless of an explicit dtype hint; this only changes behavior for the empty-list edge
case, where it now correctly returns an empty INTEGER array instead of an empty FLOAT array,
letting the downstream fancy-indexing succeed (zero levels found, correctly) instead of
raising.

**Tests**: `services/technical-analysis/tests/test_find_pivots_empty_dtype.py` (5 cases) —
directly imports and exercises the real `_find_pivots()`/`_cluster_pivots()`/
`detect_support_resistance()`. Covers both real ways to trigger a zero-pivot result (too-short
history; strictly monotonic series), confirms the fix doesn't change the already-correct
non-empty case, and a full end-to-end call through `detect_support_resistance()` against the
exact input shape that crashed in production. Adversarially verified: reverted to the
original dtype-less `np.array()` calls and confirmed 4 of 5 tests failed with the EXACT real
production `IndexError`, then restored and confirmed byte-identical via `diff`. Full 66-test
technical-analysis suite green; `pyflakes` clean.

**What to check if this recurs**:
```bash
docker exec stockai-technical-analysis-1 grep -n "dtype=int" /app/src/indicators/trendlines.py
docker logs stockai-technical-analysis-1 --since 8h 2>&1 | grep -c "arrays used as indices"
```

---

