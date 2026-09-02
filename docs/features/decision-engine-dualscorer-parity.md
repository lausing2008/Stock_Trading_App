## Feature Reference: `_should_enter()` / decision-engine Score Parity (T232-DL-DUALSCORER-DEBT, partial)

**Built 2026-07-17.** `T232-DL-DUALSCORER-DEBT` documents ~34 dimensions where
`paper_trading_engine._should_enter()` (the fallback gate, used only when decision-engine is
unreachable — `decision_engine_mode="primary"` is the live default, so DE's `/decide/{symbol}`
verdict drives real entries whenever it responds) diverges from decision-engine's
`scorer.py`/`hard_rejects.py`. That item remains open as a whole; this was a narrow, verified
slice of it.

**Corrected assumption before writing any code:** research-recommendation gating looked like a
live divergence at first read (DE's `hard_rejects.py`/`scorer.py` accept a `research_rec`
param that `_should_enter()`'s signature doesn't have at all) — but decision-engine's `/decide`
route independently fetches research itself via `aggregator.py`'s `fetch_all()` ->
`_fetch_research()`, rather than relying on `paper_trading_engine` to forward it in the
request body. So DE's research hard-reject and research-score layer already work correctly
whenever DE is reachable — not a real gap, despite how it read on first pass.

**Three genuinely-open gaps, all safely portable (pure functions of data `_should_enter()`
already receives), ported into `paper_trading_engine.py`'s `_should_enter()`:**
1. **Pre-regime early-warning score (F11)** — `-1` for `is_pre_choppy`/`is_pre_risk_off`.
   `_should_enter()` previously only used these flags one level up in `_scan_for_entries` (for
   `min_entry_score`/sizing), never as a direct score component the way DE's `scorer.py` does.
2. **Market regime as a direct score layer** — bull `+1` / choppy `-1` / risk_off `-2`.
   Previously `_should_enter()` only used `regime_state` to raise thresholds (`min_entry_score`,
   `min_rr`) and dampen sizing — a different mechanism from DE's direct score adjustment that
   does not necessarily land on the same pass/fail boundary for a borderline candidate.
3. **K-Score as a direct ±1 layer** — `_should_enter()` already received `kscore` (used inside
   its RL-adjustment and calibrated-logistic-bypass branches) but never scored it directly like
   DE does. A portfolio without 100+ closed trades' calibration got zero adjustment for a weak
   K-Score during exactly the DE-outage window when the fallback's quality matters most.

**Deliberately NOT ported** (per the same research pass's own recommendation): RL policy
adjustment and the calibrated-logistic-regression bypass remain `_should_enter()`-only — both
depend on `market-data`-local file state (`rl_agent.py`'s trained Q-function, `entry_weights.json`)
that decision-engine has no access to as a separate service. Porting either would mean a new
cross-service callback on DE's hot path or duplicating model-loading logic in a second service
— both worse than documenting the asymmetry. `sizer.py` also untouched — it's explicitly
illustrative-only and never consumed by real trades (its own module docstring says so).

**Tests:** `services/market-data/tests/test_should_enter_de_parity.py` (13 tests) isolates
exactly the three new layers using otherwise-neutral inputs (a candidate that clears every
hard reject and scores 0 on every pre-existing layer). Adversarially verified: temporarily
disabled the K-Score layer and confirmed 3 tests correctly failed before re-enabling it. Full
existing 174-test `market-data` suite stays green.

**A real test-writing gotcha hit along the way:** `conftest.py` stubs `SessionLocal` as a bare
`MagicMock()` — its chained `.execute().fetchone()` is truthy by default, which silently trips
`_should_enter()`'s macro-blackout hard reject in every test unless `signal_data["reasons"]`
explicitly sets `"macro_blackout": False` to hit the fast-path check before the DB fallback
query ever runs. Also: choppy/risk_off regimes raise the R:R hard-reject floor and separately
trigger the pre-existing cross-horizon-consensus score penalty — a naive "neutral baseline"
input isn't actually regime-neutral in this function, so isolating just the new regime-score
layer required bumping `take_profit` (to clear the raised R:R floor) and setting
`cross_style_buys=2` (to neutralize the unrelated pre-existing consensus layer) in those
specific tests.

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — 4 DE-Only Hard Rejects, Test Coverage Added (2026-07-18)

**What this session found**: the 4 "decision-engine-only hard rejects" this tracker item
listed as a safe next porting step (market-hours/holiday guard, time-of-day gate, extended-
move 6% block, regime-based R:R stiffening) were ALL already present in `_should_enter()` —
ported in an earlier session, tagged `AUD232-021`/`AUD232-005`/`AUD232-060` in code comments.
The tracker text describing them as still-portable was stale in the "code doesn't exist yet"
direction — the mirror image of the SE-F2/aud14 staleness pattern already documented elsewhere
in this file (where a tracker entry claimed something was fixed that wasn't). **Always verify
against the actual current code before assuming a tracker's "todo" status is accurate — in
either direction.**

**The real remaining gap was test coverage, not code** — `test_should_enter_de_parity.py` only
had tests for the 3 score-layer ports from the 2026-07-17 partial fix; none of the 4 hard
rejects had a single dedicated test. Writing those tests is what surfaced BUG232-DEADCODE
(above) — 2 of the 4 "already-ported" hard rejects were actually silently non-functional.

**Test additions** (`services/market-data/tests/test_should_enter_de_parity.py`, 27 tests
total now, 17 new): market-hours (mocks `_is_market_hours` directly via monkeypatch, since
real wall-clock time can't be safely controlled from a test); time-of-day gate (a custom
`datetime` subclass overriding `.now()` to return a fixed instant in the target market's
timezone — this is the exact mechanism that caught BUG232-DEADCODE); extended-move 6% block
(above/at/below the threshold, plus a configurable-threshold case); regime-based R:R
stiffening (choppy/risk_off raising the floor from 2.0 to 3.0, clearable with a wider
take_profit). All adversarially verified by sabotaging each condition (`if <cond>:` →
`if False:`) one at a time and confirming exactly the expected test subset fails, then
reverting.

**Two real test-writing gotchas hit along the way** (both fixed in the final test file, worth
knowing if extending these tests further): (1) changing `live_price` to exercise the
extended-move/time-of-day checks without also re-deriving `stop`/`take_profit` for that new
price causes the EARLIER R:R hard-reject to fire first and mask the check actually under test
— every new fixture explicitly recomputes stop/take_profit to keep R:R comfortably clear at
its own live_price. (2) floating-point imprecision: `(105.999.../100.0 - 1) * 100` can compute
to `6.000000000000005`, not exactly `6.0` — a test asserting "exactly at the threshold does
not reject" is inherently flaky on an exact boundary; use a comfortably-below value instead of
chasing an exact float boundary.

**What to check if this looks wrong**: run
`docker exec stockai-market-data-1 python3 -m pytest tests/test_should_enter_de_parity.py -v`
inside the container — all 27 should pass. If any of the 4 hard-reject tests fail after a
future edit to `_should_enter()`, that's a real regression in DE parity, not a flaky test (all
4 groups were adversarially confirmed to fail correctly when their underlying condition is
disabled).

---


## Feature Reference: AUD250-DECISIONENGINE-GAMEPLAN-SHARED-EXECUTOR — Dedicated Thread Pool (Built 2026-07-19)

**The gap**: `services/decision-engine/src/api/core/aggregator.py`'s `abuild_game_plan()` (added
in `T247-DECISIONENGINE-STYLEPARAMS-BLOCKING`, 2026-07-07, to move a blocking `httpx.get()` off
the event loop) reused `_yf_executor` — a 4-worker `ThreadPoolExecutor` originally built for a
completely unrelated purpose, the yfinance price-fallback path in the same file. Two distinct
kinds of blocking work sharing one small pool means a burst of one kind can queue behind the
other, undercutting (though not fully defeating) the parallelism a batch `POST /decide/batch`
request is supposed to get. `regime.py` already hit and fixed the identical pattern for its own
blocking regime fetch, via a dedicated `_regime_executor` — `aggregator.py` just never got the
same treatment when `abuild_game_plan()` was added later.

**Fix**: added `_game_plan_executor = ThreadPoolExecutor(max_workers=2,
thread_name_prefix="game_plan")` to `aggregator.py`, matching `regime.py`'s exact pattern, and
switched `abuild_game_plan()`'s `run_in_executor()` call to use it instead of `_yf_executor`.

**Tests**: 2 new cases in `services/decision-engine/tests/test_aggregator.py`:
- **Identity test** — spies on the actual executor object passed to `run_in_executor()` inside
  a running event loop (patching `aggregator.asyncio.get_running_loop` to return a wrapper that
  records the executor argument before delegating to the real loop), then asserts
  `abuild_game_plan()` submitted work to `_game_plan_executor`, not `_yf_executor`.
- **Contention test** — saturates every one of `_yf_executor`'s workers with self-releasing
  blocking tasks, then confirms a concurrent `abuild_game_plan()` call still completes promptly
  rather than queuing behind them.

**Two real bugs caught in my own first-draft tests, both via adversarial verification**
(temporarily reverting the fix and confirming the tests still passed — a red flag caught before
either test shipped with false confidence):
1. The first version of the identity test only asserted `_game_plan_executor is not
   _yf_executor` — true regardless of which executor the CODE actually uses, since it's just
   comparing two objects that exist side by side. Fixed by spying on the real argument passed to
   `run_in_executor()` instead of comparing unrelated objects.
2. The first version of the contention test ran only ONE concurrent task against
   `_yf_executor`'s 4 workers — comfortably fits without contention even if `abuild_game_plan()`
   WERE still using the shared pool, so the sabotage silently passed. Fixed by submitting one
   saturating task per `_yf_executor` worker (reading `_max_workers` directly rather than
   hardcoding a count) before making the concurrent call under test, forcing genuine contention
   to become observable if the pools were ever shared again. The saturating tasks self-release
   after a fixed short delay (rather than waiting on a manually-set flag) specifically so this
   test can never hang even if its own assertion were to fail — a hung test is a worse failure
   mode than a fast, clear assertion error.

Re-verified after both fixes: reverting `abuild_game_plan()` to use `_yf_executor` again made
both tests fail cleanly (no hang) before restoring the real fix.

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.core import aggregator
print('game_plan_executor is yf_executor:', aggregator._game_plan_executor is aggregator._yf_executor)
print('game_plan_executor workers:', aggregator._game_plan_executor._max_workers)
"
```

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — Conviction Gate + Signal Staleness Hard Rejects Ported to decision-engine (Built 2026-07-20)

**Continues the ongoing dual-scorer reconciliation** (see the T232-DL-DUALSCORER-DEBT entries
elsewhere in this file for the full 34-dimension background) — this session ported 2 more of
the 18 fallback-only hard rejects into `decision-engine`'s `hard_rejects.py`, both chosen
because they're binary safety/data-quality gates (not scoring judgment calls the item's own
`what` field warns against blind-porting).

**1. Conviction gate cross-check** — reads the same `conv_gate:{symbol}:{style}` Redis key
`paper_trading_engine.py`'s `_scan_for_entries()` already writes (1-day TTL, from the alert
system's own 7-layer conviction check). If that check already evaluated this BUY and failed
it, decision-engine now blocks too, instead of silently approving an entry the alert system
itself would never have notified on. Reads Redis directly (decision-engine already depends on
`redis` for `llm_scorer.py`/`risk_agent.py`, and shares the same `redis_url` as every other
service) rather than requiring the caller to pre-compute and forward it — this specific check
now makes `/decide/{symbol}` self-sufficient regardless of caller, directly closing part of
the item's own group-(e) "pipeline-topology gap" for this one gate.

**2. Signal-staleness hard reject (T222-C)** — a genuinely separate finding from what
`T234-CONFIG-UNJUSTIFIED-THRESHOLDS` originally claimed. That item described `paper_trading_
engine.py`'s 72h staleness cutoff and `decision-engine`'s scorer.py Layer 3e (4h/18h bands) as
"the same conceptual threshold set to different values" — re-verified before touching anything
and found this framing wrong: Layer 3e's 4h/18h bands are a SOFT scoring adjustment that
already correctly matches `_should_enter()`'s own identical SA-24 soft-scoring thresholds
(confirmed via grep — both literally use 4/18). The 72h value is a completely different,
EARLIER, HARD cutoff in `_scan_for_entries()` that decision-engine had no equivalent of at
all — meaning `/decide/{symbol}` would silently accept a signal so old that
`paper_trading_engine` would have discarded it before ever reaching a scorer. Ported as a new
hard reject (not a threshold reconciliation, since there was never a real numeric mismatch to
reconcile).

**Implementation**: `check_hard_rejects()` gained 3 new optional parameters (`symbol`, `style`,
`sig_ts`, all defaulting to `None`) so every pre-existing call site keeps working unchanged. The
conviction-gate check only runs `if symbol and style:`; the staleness check only runs `if
sig_ts is not None:` — both fail open on any error (malformed timestamp, Redis unavailable),
matching every other gate in this file. `routes.py`'s `_decide()` already had `sig_ts` computed
at line 99 and `symbol`/`style` in scope well before the `check_hard_rejects()` call at line
158 — no new data-fetching needed, just threading already-available values through.

**A real test-writing bug of my own, caught via adversarial verification, not shipped**: the
first version of "conviction gate skipped when symbol/style missing" relied on leaving Redis
completely UNMOCKED, reasoning "if the code tried to reach Redis without symbol/style it would
hit a real connection attempt and presumably fail." This test still passed even after
temporarily removing the `if symbol and style:` guard entirely — investigated why (the
"sabotage still passes" red flag this repo's testing discipline treats as a finding in its own
right, not a shrug) and found: with `common.config` stubbed as `MagicMock` (this test file's
own established convention for this Docker-only dependency, matching `test_risk_agent.py`),
`get_settings().redis_url` is itself a `MagicMock`, and `redis.Redis.from_url()` raises a real
`TypeError` trying to use it — caught by the SAME outer `except Exception` that handles
genuine Redis failures elsewhere in the same function. Removing the guard just swapped which
exception path produced the identical `result=None`, invisible to a test that only checks the
final return value. Fixed with a call-counting mock (`_TrackedRedis.get()` increments a
counter) that asserts the Redis lookup was never attempted at all — this version correctly
fails when the guard is removed.

**Tests**: 17 new cases in `services/decision-engine/tests/test_hard_rejects.py` (now 47 total,
up from 35 before AUD232-005/060's earlier session and 41 immediately before this one) — 6 for
the conviction gate (failed/passed/missing-key/redis-error/non-BUY-cached-signal/missing-
symbol-or-style), 6 for signal staleness (beyond-max-age/within-max-age/custom-max-age/absent-
ts/malformed-ts/real-datetime-object-not-just-string). Adversarially verified 3 guards by
sabotage, all caught and reverted: disabling the conviction-gate `if` condition, disabling the
staleness age comparison, and the call-counting-mock fix described above. Full 108-test
decision-engine suite green (up from 96 at the start of this session's work).

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.core.hard_rejects import check_hard_rejects
import inspect
print(inspect.signature(check_hard_rejects))
"
# Confirm the conviction-gate Redis key format matches what paper_trading_engine.py writes:
docker exec stockai-redis-1 redis-cli keys 'conv_gate:*' | head -5
docker exec stockai-redis-1 redis-cli get 'conv_gate:<SYMBOL>:<STYLE>'
```

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — K-Score Floor Hard Reject Ported to decision-engine (2026-07-20)

**Gap closed**: one more of the ~28 remaining `_should_enter()`-vs-decision-engine divergences
tracked under T232-DL-DUALSCORER-DEBT — the K-Score floor. `_scan_for_entries()`'s `min_kscore`
(per-style hard pre-filter, `_DEFAULT_CONFIG["min_kscore"] = 48.0`, GROWTH=48, LONG=50, SWING=52
via `_STYLE_OVERRIDES`) discards a candidate entirely before it's ever scored. decision-engine's
`scorer.py` already has AUD232-042's soft ±1 K-Score layer (fixed 55 boundary) — a genuinely
different mechanism (a scoring nudge, never a block) at a genuinely different threshold, so a
candidate the soft layer barely penalizes could still be one `_scan_for_entries` would have
discarded outright. `/decide/{symbol}` had no equivalent hard floor at all.

**Two-sided fix** (the threshold itself, not just the candidate's kscore value which was
already threaded, had to start reaching decision-engine):
1. `paper_trading_engine.py`'s `_call_decision_engine()` — added
   `**( {"min_kscore": cfg.get("min_kscore", _DEFAULT_CONFIG["min_kscore"])} if kscore is not None else {} )`
   to the `config_overrides` dict, conditional on `kscore` also being sent (same pattern as the
   existing `kscore` inclusion and the `llm_scoring_enabled` block).
2. `hard_rejects.py`'s `check_hard_rejects()` — needed zero new function parameters (`cfg`
   already carries both `min_kscore` and `kscore` via its existing merge mechanism):
   ```python
   if cfg.get("min_kscore") is not None:
       _kscore_val = cfg.get("kscore")
       if _kscore_val is not None and float(_kscore_val) < float(cfg["min_kscore"]):
           return f"K-Score {float(_kscore_val):.0f} below minimum {float(cfg['min_kscore']):.0f} — fundamental/momentum quality gate not met"
   ```
   Fail-open exactly like every other optional gate in this file — an older caller not sending
   `min_kscore` (or `kscore`) is unaffected.

**Tests**: `services/market-data/tests/test_min_kscore_config_wiring.py` (new, 3 cases) guards
the write side via source-text extraction (matching `test_llm_scoring_config_wiring.py`'s
established technique, since `paper_trading_engine.py` can't be imported directly in this test
environment) — confirms `min_kscore` actually appears in `config_overrides`, falls back to the
real `_DEFAULT_CONFIG` value rather than a hardcoded literal, and is conditional on `kscore`'s
own presence. `services/decision-engine/tests/test_hard_rejects.py` gained 5 cases (47→52):
below/at-or-above the floor, gate skipped when `min_kscore` or `kscore` itself is absent, and
the real per-style thresholds (a candidate clearing GROWTH's 48 but not SWING's 52 is blocked
under SWING's).

**Adversarial verification** — 3 separate guards sabotaged and reverted:
1. The comparison logic (`if False:`) — caught by the below-floor and per-style tests.
2. The outer `cfg.get("min_kscore") is not None` guard (`if True:`) — produced a genuine
   `KeyError: 'min_kscore'` in the absent-threshold test, confirming the guard prevents a real
   crash, not just redundant defensive code.
3. The write-side `config_overrides` line in `paper_trading_engine.py` (replaced with a bare
   comment) — confirmed all 3 new wiring tests correctly failed (2 via assertion, 1 via a real
   `ValueError` from `.index()` no longer finding the string) before reverting.

Full market-data suite (316 tests) and decision-engine suite (113 tests) green after every
revert; frontend typecheck clean (no frontend files touched).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"min_kscore":' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'min_kscore' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If a low-K-Score candidate is still approved by
`/decide/{symbol}` after confirming both, check whether the caller (e.g. `decide.tsx`) is
actually sending a `kscore` in `config_overrides` at all — the gate is a no-op without one.

---


## Feature Reference: AUD256 — regime_min_rr_ratio Now Forwarded to decision-engine (Built 2026-07-20)

**The gap**: `_call_decision_engine()`'s `config_overrides` had two related problems, both
flagged but deliberately deferred during the 2026-07-17 AUD256 deep audit. (a) `min_rr_ratio`
WAS sent, but its own fallback was a bare `2.0` literal — bypassing
`SELFIMPROVE-NEVER-CALIBRATED-PARAMS`' calibration entirely. `_should_enter()` resolves the
same key via `_default_min_rr_ratio("neutral")`, which returns the calibrated value from
`min_rr_calibration.json` once one exists. (b) `regime_min_rr_ratio` was never sent AT ALL —
decision-engine's `hard_rejects.py` already correctly reads `cfg.get("regime_min_rr_ratio",
3.0)` for choppy/risk_off regimes (T190), confirmed working via its own pre-existing
`test_custom_regime_min_rr_ratio_is_respected` test — but with nothing ever sending the key,
DE always silently used its own hardcoded 3.0, completely blind to calibration, even though
`_should_enter()` has been correctly regime-aware here since AUD232-060.

**Fix — write side only, decision-engine's read side was already correct**:
1. `_call_decision_engine()` gained a `regime_state: str = "neutral"` parameter.
2. `min_rr_ratio`'s fallback changed from `2.0` to `_default_min_rr_ratio("neutral")`.
3. Added `"regime_min_rr_ratio": cfg.get("regime_min_rr_ratio",
   _default_min_rr_ratio(regime_state))` to `config_overrides`.
4. The one real call site (inside `_scan_for_entries()`) now passes
   `regime_state=(live_regime.get("state", "neutral") if live_regime else "neutral")` —
   `live_regime` was already in scope there.

`_default_min_rr_ratio(regime_state)` only returns the `regime_min_rr_ratio` calibrated value
when `regime_state` is `"choppy"`/`"risk_off"`; otherwise it returns `min_rr_ratio`'s value —
which `hard_rejects.py` ignores anyway outside those two regimes, since it only consults
`regime_min_rr_ratio` inside that same branch. This exactly matches `_should_enter()`'s own
usage of the same resolver.

**Tests**: `services/market-data/tests/test_regime_min_rr_config_wiring.py` (new, 5 cases,
source-text extraction matching `test_min_kscore_config_wiring.py`'s established technique) —
`min_rr_ratio` routes through the calibrated resolver rather than a bare literal,
`regime_min_rr_ratio` is actually threaded into `config_overrides`, it resolves via
`_default_min_rr_ratio(regime_state)` rather than a hardcoded literal, `_call_decision_engine()`
accepts a `regime_state` parameter, and the real call site derives it from `live_regime` rather
than a hardcoded value.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. Reverting `min_rr_ratio`'s fallback to a bare `2.0` literal.
2. Removing `regime_min_rr_ratio` from `config_overrides` entirely.
3. Hardcoding `regime_state="neutral"` at the call site instead of deriving it from
   `live_regime`.

Full 323-test market-data suite (up from 318) and 113-test decision-engine suite (unchanged —
no decision-engine code was touched) green; frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n 'regime_min_rr_ratio' /app/src/services/paper_trading_engine.py
```
Should show both the `config_overrides` entry and the call-site `regime_state=` argument. If
decision-engine still seems to use a stale 3.0 regardless of calibration, confirm
`min_rr_calibration.json` actually exists and has a real `regime_min_rr_ratio` value:
```bash
docker exec stockai-market-data-1 cat /data/models/min_rr_calibration.json 2>/dev/null
```

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — TA-Score Floor Hard Reject Ported to decision-engine (2026-07-22)

**Gap closed**: one more of the remaining `_should_enter()`-vs-decision-engine divergences —
the TA-score floor. `_scan_for_entries()`'s `min_ta_score` (T224-C/T225-A hard pre-filter, no
`_DEFAULT_CONFIG` entry at all — disabled by default via the read side's own `cfg.get(
"min_ta_score", 0.0)` fallback; 0.50 for SWING via `_STYLE_OVERRIDES`, 0.65 for HK via
`_HK_MARKET_OVERRIDES`) discards a candidate before it's ever scored. decision-engine had no
equivalent at all — `/decide/{symbol}` called standalone (e.g. `decide.tsx`, which never runs
`_scan_for_entries`' own pre-filter) could silently accept a candidate below the real
`min_ta_score` floor. Same shape as the K-Score floor port (2026-07-20) — this session ported
the next domino in that same, well-proven pattern.

**A real mistake caught before shipping**: `min_kscore` DOES have a `_DEFAULT_CONFIG` entry
(48.0), so its write-side fallback correctly reads `_DEFAULT_CONFIG["min_kscore"]`. `min_ta_score`
has NO `_DEFAULT_CONFIG` entry anywhere in this file — copying the K-Score pattern verbatim
(`_DEFAULT_CONFIG["min_ta_score"]`) would have raised a `KeyError` on every single call, since
that key doesn't exist. Caught by tracing the READ side's own fallback (`_scan_for_entries`,
line ~4218: `cfg.get("min_ta_score", 0.0)`) before writing the send side, and matching that
exact fallback (`cfg.get("min_ta_score", 0.0)`) instead of blindly mirroring the sibling gate's
literal code shape.

**Implementation**:
1. `paper_trading_engine.py`'s `_call_decision_engine()` — gained a `ta_score: float | None =
   None` parameter; the real call site inside `_scan_for_entries()` now computes `ta_score_f =
   float(_ta_score_raw) if _ta_score_raw is not None else None` from `(sig.reasons or {}).get(
   "ta_score")` — the SAME `sig.reasons` dict the pre-existing TA-score hard-reject (a few
   lines earlier in the same function) already reads from, not a re-fetch. Added
   `"ta_score": ta_score` and `"min_ta_score": cfg.get("min_ta_score", 0.0)` to
   `config_overrides`, both conditional on `ta_score is not None` (matching the existing
   `kscore`/`min_kscore` conditional-inclusion pattern exactly).
2. `hard_rejects.py`'s `check_hard_rejects()` — needed zero new function parameters (`cfg`
   already carries both keys via its existing merge mechanism, same as `min_kscore`):
   ```python
   if cfg.get("min_ta_score") is not None:
       _ta_val = cfg.get("ta_score")
       if _ta_val is not None and float(_ta_val) < float(cfg["min_ta_score"]):
           return f"TA score {float(_ta_val):.2f} below minimum {float(cfg['min_ta_score']):.2f} — technical-analysis quality gate not met"
   ```
   A `min_ta_score` of `0.0` (the upstream gate's own disabled state) never rejects, since
   `ta_score` can't be below `0.0` — matches `_scan_for_entries`' own `_min_ta > 0` no-op check.

**Tests**: `services/decision-engine/tests/test_hard_rejects.py` gained 6 cases (133 total, up
from 127) — below/at-or-above the floor, gate skipped when `min_ta_score` or `ta_score` itself
is absent, per-market thresholds (SWING's 0.50 vs. HK's 0.65), and the `min_ta_score=0.0`
disabled-gate case. `services/market-data/tests/test_min_ta_score_config_wiring.py` (new, 4
cases) guards the write side via source-text extraction (matching
`test_min_kscore_config_wiring.py`'s established technique) — confirms both `ta_score` and
`min_ta_score` actually reach `config_overrides`, confirms the fallback is the literal
`cfg.get("min_ta_score", 0.0)` (NOT a `_DEFAULT_CONFIG` key reference — the exact mistake
caught above), confirms both keys are conditional on `ta_score is not None`, and confirms
`ta_score_f` is derived from the same `sig.reasons` dict the pre-existing gate reads from.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted:
1. The comparison logic in `hard_rejects.py` (`if False:`) — caught by 2 of the 6 new tests
   (the below-floor and per-market-threshold cases).
2. The write-side `config_overrides` conditional inclusion of `"ta_score"` in
   `paper_trading_engine.py` (removed) — caught by 2 of the 4 new wiring tests (a real
   `ValueError` from `.index()` no longer finding the string, matching the exact failure mode
   the equivalent `min_kscore` sabotage produced in the earlier session).

Full 436-test market-data suite (up from 432) and 133-test decision-engine suite (up from 127)
green after every revert; frontend untouched (backend-only fix, no UI change).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"min_ta_score":' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'min_ta_score' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If a low-TA-score candidate is still approved by
`/decide/{symbol}` after confirming both, check whether the caller (e.g. `decide.tsx`) is
actually sending a `ta_score` in `config_overrides` at all — the gate is a no-op without one.

---


## Design Reference: T232-SIG-ENTRYTIMING — Why BUY Signals Tend to Fire Near a Local Peak, Not a Bottom

**User asked directly (2026-07-23)**: "Why AI signal always asked me to buy from the top of
the peak not from the bottom or lower position. How to improve it?" — a real, structural
property of the signal design, not a bug. Documented here as 4 options before building
anything, per this repo's standing scope-first discipline.

**Root cause**: `_ta_score()`'s 4 pillars (`services/signal-engine/src/generators/signals.py`,
SA-19/SA-30 architecture) are built almost entirely from TREND-CONFIRMATION evidence, and
confirmation by definition only exists once a move has already happened:
- **TREND pillar** (line ~1160): rewards price above its 50/200-day averages, a golden cross,
  an established ADX uptrend — none of which can be true at a genuine bottom, where price is
  still below its own averages.
- **MOMENTUM pillar** (line ~1187): `rsi_score` is a flat **0.0** for `rsi_val < 35` — the
  model actively zeroes out the exact oversold zone where real bottoms form — and instead
  rewards RSI 45-65 with MACD already positive and expanding (mid-rally, not "just turned up").
- **VOLUME pillar** (line ~1212): wants OBV trend + volume expansion together (SA-32 AND
  logic) — this usually confirms a move already in progress, not quiet accumulation at a low.
- **The pillar-count gate compounds it** (`min_pillars_for_buy`, `_STYLE_PROFILES` — 3 for
  SWING/LONG, 2 default for SHORT/GROWTH, `_apply_style_signal()` line ~1813): since trend/
  momentum pillars are near-zero at a bottom, a genuine dip gets compressed toward neutral
  (`fused = 0.5 + (fused - 0.5) * 0.70` when below the style's minimum), while a stock already
  mid-rally clears the gate easily and gets a `+0.03` confluence boost instead.
- **`_pullback_recovery()`** (SA-14, line ~890) exists specifically to reward a healthy dip +
  recovery (5-25% pullback, 2+ green days, volume confirmation), but its bonus (`pr_delta`,
  0.04-0.07) is deliberately gated **behind** the pillar check
  (`if _pr_delta > 0 and _pillars >= _min_pillars:`, line 1854) — the comment at line 1848-1852
  states this is intentional ("the boost only rewards setups that already have sufficient
  independent TA confirmation... a pullback recovery on a 2-pillar setup should not bypass that
  gate"). The practical effect: the ONE mechanism built to reward early dip entries can't fire
  until the trend/momentum pillars have already confirmed — but those pillars are structurally
  weak immediately after a pullback, so the bonus arrives too late to help the early entry it
  was designed to reward.

**What already exists downstream to work around this** (none of it feeds back into the BUY
label or confidence number itself): the Fair Value Gap Trade Plan (entry at the midpoint of the
nearest unfilled gap below current price), Volume Profile POC/VAH/VAL (explicitly documented
elsewhere in this file as a better pullback-entry reference than chasing a breakout), the
Position Sizer's independent ATR/support-based entry, and `paper_trading_engine.py`'s
extended-move guards (`max_entry_gap_pct`/`max_breakout_extension_pct` hard-rejects, plus a
soft score bonus for an "optimal entry zone" vs. a penalty for chasing). A user has to know to
check these separate cards — the signal's own BUY/confidence display doesn't reflect any of it.

**Four options, ranked by risk**:

1. **(Small, safe — BUILT 2026-07-23)** Stop scoring RSI 28-35 as a flat zero in the momentum
   pillar. Give it partial credit as an "early recovery zone" — mirroring how the BEARISH
   pillar (line ~1298) already treats its own mirrored range (`0.5 if 28 <= rsi_val <= 35`) as
   a real, distinct zone rather than a flat cutoff. A pure scoring-table change, no gating logic
   touched, no change to when signals fire — only how much credit an already-computed RSI value
   receives at the exact bottom-recovery zone the bearish pillar already models on its own side.

2. **(Small, safe — BUILT 2026-07-23)** Let `_pullback_recovery()`'s bonus apply even when the
   pillar gate hasn't cleared the style minimum, specifically for the RSI 30-45 recovery band —
   i.e., treat "genuine 2-green-day, volume-confirmed recovery off a real dip" as legitimate
   evidence in its OWN right, not something that can only ever pad an already-strong setup.
   Deliberately narrower than "always bypass the gate" (which the original SA-14/SA-32 comment
   correctly warns against for setups with zero real TA support) — only unlocks the bypass when
   the RSI evidence itself indicates a real, not-yet-fully-confirmed recovery is underway.

3. **(Medium, needs backtesting before promotion)** Pull `paper_trading_engine.py`'s
   "distance from a good entry price" extended-move judgment UPSTREAM into the signal's own
   fused probability, so "this is a good price to enter" becomes part of what BUY actually
   means, not a separate downstream trade-gate that fires after the signal already said BUY.
   Concretely: a distance-from-20/50-day-high penalty (mirroring `_pullback_recovery()`'s own
   `high_20d` reference) applied as a genuine negative pillar-adjacent score, not just a hard
   reject at the trade-execution layer. This changes the actual probability users see, not just
   whether a trade executes — needs a real train/validation EV comparison
   (`gate_harness.py`-style) before promoting, matching this repo's standing convention for any
   change to core signal probability.

4. **(Bigger, needs backtesting)** Split "is this a real trend" from "is this a good entry
   price" into two genuinely separate scores that BOTH must clear, instead of one blended
   `fused_prob` where trend strength alone can push a stock over `buy_threshold` regardless of
   how extended the current price is. This is effectively what FVG/Volume-Profile/Position-
   Sizer already do as separate, disconnected cards — the deeper fix is making that judgment
   part of the actual signal decision, not a supplementary panel a user has to know to check
   separately. A structural change to `_decide_style()`'s gating logic, not a scoring-table
   tweak — the largest-effort, highest-payoff option, and the one most likely to need a
   dedicated design doc + phased rollout (matching how `T233-SELFIMPROVE-DESIGN`'s own phases
   were sequenced) rather than a same-session build.

**Chosen for this session**: options 1 and 2 (both narrow, reversible, scoring-only changes
with no gating-logic risk). Options 3 and 4 deliberately deferred — both would change the core
signal probability broadly and need the same train/validation-beats-baseline discipline every
other signal-probability change in this codebase already follows before being trusted live.

---


## Feature Reference: T234-CONFIG-DECIDE-DEFAULT-MISMATCH — Real Entry-Gate Defaults for Standalone /decide (Built 2026-07-23)

**The gap**: decision-engine's `routes.py`'s own `_DEFAULT_CFG["min_confidence"] = 62.0` (and
`hard_rejects.py`'s matching inline fallback of the same literal) is a value that exists
**nowhere** in the real trading engine's actual style/market matrix
(`paper_trading_engine.py`'s `_DEFAULT_CONFIG`/`_STYLE_OVERRIDES`/`_HK_MARKET_OVERRIDES`):
SHORT/GROWTH=45, SWING=50, LONG=40 in the US, ALL raised to 65 in HK. A caller going through
the real trading path (`_call_decision_engine()`, called from `_scan_for_entries()`) always
sends the real resolved value explicitly via `config_overrides`, so this never mattered on that
path — but `decide.tsx`'s standalone `GET /decide/{symbol}/explain` (which builds a bare
`DecisionRequest(style=style)` with **no** `config_overrides` at all) silently used the
disconnected `62.0` literal instead of the real value a live portfolio of that style/market
would actually gate on.

**Investigated the "best fix," not just the easy patch, per explicit user instruction.**
Considered simply changing the one literal to something more defensible — rejected, since NO
single number can correctly represent this: the real value depends on BOTH style (4 different
US values) AND market (a uniform HK override on top). The real fix needed to resolve the
correct value dynamically, the same way `_scan_for_entries()` itself does, not encode a second,
inevitably-still-wrong guess.

**Design — reused an EXISTING, already-proven pattern rather than inventing a new one.**
`GET /stocks/style-params` (market-data) already solves the identical class of problem for
game-plan geometry (entry/breakout/stop/target percentages) — decision-engine's `aggregator.py`
already has a working fetch-cache-fallback (`_get_style_params()`) plus an async wrapper
(`abuild_game_plan()`) that runs the blocking fetch in a dedicated executor
(`_game_plan_executor`) so a cache-miss `httpx.get()` never stalls the shared event loop
(`T247-DECISIONENGINE-STYLEPARAMS-BLOCKING`). Built the exact same shape for entry-gate
thresholds instead of a one-off literal fix:

1. **New market-data function**: `resolve_entry_gate_params(style, market)`
   (`paper_trading_engine.py`) — replicates `_scan_for_entries()`'s own exact merge order
   (`_DEFAULT_CONFIG` → `_STYLE_OVERRIDES[style]` → HK override if `market == "HK"`),
   restricted to the 5 real entry-gate keys (`min_confidence`, `min_kscore`,
   `min_entry_score`, `min_ta_score`, `min_rr_ratio` — distinct from `_STYLE_PARAMS`'s game-plan
   geometry keys, a genuinely different dict for a genuinely different purpose).
   `min_rr_ratio` is resolved via the existing calibration-aware `_default_min_rr_ratio()`
   rather than a frozen `2.0`, so this stays correct even after a future calibration run
   changes the real default. `min_ta_score` correctly defaults to `0.0` (gate disabled) when no
   style/market override set it — it has no `_DEFAULT_CONFIG` entry at all, matching every
   other read site's own established convention for this specific key.
2. **New endpoint**: `GET /stocks/entry-gate-params?style=&market=` — thin wrapper, unauthenticated
   (read-only, no sensitive data, matching `/style-params`'s own posture).
3. **New decision-engine fetcher**: `_get_entry_gate_params(style, market)` +
   `aget_entry_gate_params()` in `aggregator.py` — identical fetch/cache(15min)/fallback shape
   as `_get_style_params()`, cached per `(style, market)` pair (the two dimensions genuinely
   produce different values), reusing `_game_plan_executor` for the async wrapper (the same
   class of infrequent, short-lived cache-refresh call as the game-plan fetch, not a new
   contention source needing its own dedicated pool).
4. **Wired into `_decide()`** (`routes.py`): fetches the real defaults right after `market` is
   finalized (i.e. AFTER the `.HK`-suffix auto-upgrade, not before — a `.HK` symbol must get
   HK-adjusted defaults, not US ones) and BEFORE `check_hard_rejects()` consumes `cfg`. Only
   fills in a key if the caller didn't already explicitly set it via `config_overrides` — the
   real trading path's own explicit overrides always still win, this only closes the gap for
   callers (like `decide.tsx`) that never set them at all.
5. **`hard_rejects.py`'s own `62.0` fallback literal** is now effectively unreachable in
   production (routes.py's `_decide()` always fills a real value into `cfg` before this
   function runs) — left in place as a safety net for a direct test-only caller that
   constructs `cfg` without the key, with a comment explaining why it's dead in practice.

**Tests**: `services/market-data/tests/test_entry_gate_params.py` (14 cases) — cross-checks
every returned value directly against `_scan_for_entries()`'s own source dicts (not a
hand-copied expectation, which could silently drift from the real merge), the exact reported
real-world case (SWING/US resolves to 50, not decision-engine's stale 62), HK's uniform 65
override across all 4 styles, `min_ta_score`'s correct 0.0-when-unset default,
`min_rr_ratio`'s calibration-awareness, graceful degradation for an unknown style, and the
route itself delegating to (not reimplementing) the resolver.
`services/decision-engine/tests/test_entry_gate_params.py` (13 cases) — the fetch/cache/
fallback shape (cache hit skips the HTTP call entirely, distinct `(style, market)` pairs cache
independently, a fetch failure degrades to the stale cache before the generic fallback, matching
`_get_style_params()`'s own precedent), the async wrapper's executor-not-event-loop property, and
4 source-text regression checks on `_decide()`'s actual wiring: the fetch call exists, the
`config_overrides`-always-wins guard exists, the fetch happens before `check_hard_rejects()`,
and — the one genuinely subtle ordering bug this class of fix can introduce — the fetch happens
AFTER `market`'s `.HK` auto-upgrade, not before.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: removing the HK
override merge from `resolve_entry_gate_params()` (3 market-data tests caught it); removing the
`config_overrides`-precedence guard in `_decide()` (1 decision-engine test caught it, with a
real `AssertionError` diff showing the source no longer contained the guard); reordering the
fetch to run BEFORE `market`'s `.HK` upgrade (1 decision-engine test caught it via a real index
comparison, `4134 < 3904` failing correctly).

Full 500-test market-data suite and 146-test decision-engine suite green after every revert;
`pyflakes` clean on every touched file (confirmed via `git stash` that all pre-existing warnings
in these files predate this change).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/entry-gate-params?style=SWING&market=HK'
# Should show min_confidence: 65.0 (HK override), not decision-engine's old stale 62.0.

docker exec stockai-decision-engine-1 grep -n "aget_entry_gate_params" /app/src/api/routes.py /app/src/api/core/aggregator.py
```
If `GET /decide/{symbol}/explain` still shows a confidence-gate result inconsistent with what
`/stocks/entry-gate-params` reports for the same style/market, confirm the fetch is actually
succeeding — check `docker logs stockai-decision-engine-1 --since 10m | grep
entry_gate_params_fetch_failed` for a silent fallback-to-hardcoded-literal condition (market-data
unreachable, DNS issue, etc.).

**Extension 2026-08-18 (T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #2) — `regime_min_rr_ratio`
was never surfaced by this endpoint at all.** `resolve_entry_gate_params()` already resolved
`min_rr_ratio` (the neutral-regime R:R floor) via the calibration-aware `_default_min_rr_ratio
("neutral")` — but decision-engine's `hard_rejects.py` separately reads a SECOND key,
`regime_min_rr_ratio` (the stricter floor applied in choppy/risk_off regimes, per T190), from
its own disconnected bare `3.0` fallback with no way to pick up a calibrated value the way
`min_rr_ratio` already could. `_should_enter()` itself already resolves this correctly via
`_default_min_rr_ratio("choppy")` — the gap was purely that `resolve_entry_gate_params()` never
threaded the second key through, so decision-engine's standalone `/decide` callers (anything
that doesn't go through the real `_scan_for_entries()` scan, e.g. `decide.tsx`) always fell back
to the disconnected literal regardless of any real calibration.

**Fix**: one new line —
```python
result["regime_min_rr_ratio"] = _default_min_rr_ratio("choppy")
```
right after the existing `min_rr_ratio` line in `resolve_entry_gate_params()`
(`paper_trading_engine.py`) — no decision-engine changes needed, since `hard_rejects.py`
already reads `cfg.get("regime_min_rr_ratio")` and `_decide()` already merges whatever
`resolve_entry_gate_params()` returns into `cfg` before `check_hard_rejects()` runs.

**A real "still passes after sabotage" gap caught during test-writing**: no calibration file
exists in this local test environment, so `_default_min_rr_ratio("choppy")` currently degrades
to the SAME hardcoded `3.0` literal a naive re-hardcode sabotage would also produce — asserting
plain equality against the live value alone would NOT distinguish "genuinely calls the
function" from "hardcodes 3.0." Fixed by monkeypatching the module's own `_min_rr_override_cache`
directly to force a distinctive fake calibrated value (e.g. `2.73`) through, which only a real
function call can reflect — re-verified the sabotage now correctly fails 3 of 4 dedicated tests.

**Tests**: 4 new cases in `test_entry_gate_params.py`'s `TestRegimeMinRrRatioIsCalibrationAwareToo`
class — the real-default match, the monkeypatched-override tracking, confirming `min_rr_ratio`/
`regime_min_rr_ratio` resolve independently (not the same call duplicated under two names —
would stay always-equal even with a real calibration file setting them differently), and HK
market coverage. Full 1,713-test market-data suite green at the time.

**Live-verified** (2026-08-18): `GET /stocks/entry-gate-params?style=SWING&market=HK` now
returns `regime_min_rr_ratio: 3.0` (previously absent from the response entirely).

```bash
docker exec stockai-market-data-1 grep -n '"regime_min_rr_ratio"' /app/src/services/paper_trading_engine.py
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/entry-gate-params?style=SWING&market=HK'
```

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — Declining-Confidence (T202) Hard Reject Ported to decision-engine (2026-07-28)

**Gap closed**: another divergence in the ongoing dual-scorer reconciliation — T202's
declining-confidence gate. `_scan_for_entries()`'s SA-26 trajectory query computes
`confidence_delta` (current signal's confidence minus the most recent PRIOR signal's
confidence for the same symbol+style) and hard-discards a candidate whose confidence has
dropped more than `max_confidence_decline` (default `-8.0`) points before it's ever scored.
decision-engine only had the SAME value's SOFT ±1 scoring layer (`scorer.py`'s own SA-26
mirror) — never a hard block — so `/decide/{symbol}` called standalone (e.g. `decide.tsx`,
which never runs `_scan_for_entries`' own pre-filter) could silently approve a degrading
setup `_scan_for_entries` would have discarded outright. Same shape as the `min_kscore`/
`min_ta_score` ports (2026-07-20/22) — this session ported the next domino in that proven
pattern.

**One structural difference from those two priors, confirmed before writing code**:
`confidence_delta` needed ZERO new DB query — it's already computed earlier in the SAME
`_scan_for_entries()` loop iteration (by T202's own gate, via a `SELECT Signal.confidence
WHERE stock_id=X AND horizon=style AND ts < sig.ts ORDER BY ts DESC LIMIT 1` query) and stays
live as a local variable all the way to the `_call_decision_engine()` call site ~90 lines
later — a pure "thread an existing local through as a new kwarg," not a "compute+thread"
port like `kscore`/`ta_score` were.

**A real sign-direction trap avoided**: `min_kscore`/`min_ta_score` are POSITIVE floors
(`value < min` blocks). `max_confidence_decline` is a NEGATIVE threshold and the gate blocks
when the delta falls BELOW it (`confidence_delta < max_confidence_decline`, e.g. `-12.0 <
-8.0` blocks; exactly `-8.0` does not). Blindly copy-pasting the positive-floor idiom's
comparison direction would have silently inverted the gate's behavior — every comment and
test in this port explicitly calls out the sign to guard against exactly that mistake.

**Implementation**:
1. `paper_trading_engine.py`'s `_call_decision_engine()` — gained a `confidence_delta: float
   | None = None` parameter; the real call site inside `_scan_for_entries()` passes
   `confidence_delta=confidence_delta` (the same local T202's own gate already computed).
   Added `"confidence_delta"` and `"max_confidence_decline"` to `config_overrides`, both
   conditional on `confidence_delta is not None` (matching the `kscore`/`min_kscore` and
   `ta_score`/`min_ta_score` conditional-inclusion pattern exactly). The threshold's fallback
   (`cfg.get("max_confidence_decline", -8.0)`) matches `_scan_for_entries`' own real fallback
   verbatim.
2. `hard_rejects.py`'s `check_hard_rejects()` — needed zero new function parameters (`cfg`
   already carries both keys via its existing merge mechanism, same as `min_kscore`/
   `min_ta_score`):
   ```python
   if cfg.get("max_confidence_decline") is not None:
       _conf_delta_val = cfg.get("confidence_delta")
       if _conf_delta_val is not None and float(_conf_delta_val) < float(cfg["max_confidence_decline"]):
           return f"Confidence declined {float(_conf_delta_val):.1f} pts since prior signal, exceeds max decline {float(cfg['max_confidence_decline']):.1f} pts — setup degrading, wait for stabilisation"
   ```

**Tests**: `services/market-data/tests/test_declining_confidence_config_wiring.py` (new, 5
cases, source-text extraction — matching `test_min_ta_score_config_wiring.py`'s established
technique) — confirms both keys actually reach `config_overrides`, the threshold's fallback
matches the real `-8.0` literal, both keys are conditional on `confidence_delta is not None`,
`confidence_delta` is derived from the same SA-26 trajectory query T202's own gate computes
(not a second independent derivation), and the call site passes the same local variable
through. `services/decision-engine/tests/test_hard_rejects.py` gained 5 cases (68 total, up
from 63) — below/at-exactly/above the threshold (confirming the strict `<` boundary),
gate skipped when either `max_confidence_decline` or `confidence_delta` itself is absent, and
a dedicated sign-safety test confirming a RISING (positive) confidence delta never blocks
regardless of how tight the threshold is set.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted:
1. The comparison logic in `hard_rejects.py` (`if False:`) — caught by exactly 1 of the 5 new
   tests (the below-threshold-blocks case), the other 4 (which test the surrounding guard
   conditions, not the comparison itself) correctly stayed green — confirming the sabotage
   was isolated to the right code path.
2. The write-side `config_overrides` conditional inclusion in `paper_trading_engine.py`
   (removed both dict-spread lines) — caught by 3 of the 5 new wiring tests (the 2 unrelated
   ones — the SA-26 query-derivation check and the call-site kwarg check, neither of which
   depends on the `config_overrides` dict — correctly stayed green).

Full 547-test market-data suite (up from 542) and 151-test decision-engine suite (up from
146) green after every revert; `pyflakes` clean on both touched files (confirmed via `git
stash` that the 3 pre-existing `paper_trading_engine.py` warnings predate this change — one
warning's line number shifted by exactly the number of lines this fix added, nothing new).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"confidence_delta":\|"max_confidence_decline":' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'max_confidence_decline' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If a degrading-confidence candidate is still approved by
`/decide/{symbol}` after confirming both, check whether the caller (e.g. `decide.tsx`) is
actually sending a `confidence_delta` in `config_overrides` at all — the gate is a no-op
without one (correctly — there's nothing to compare against for a brand-new symbol with no
prior signal).

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — HK Stock-Connect Flow Gate (T224-A) Ported, Zero Write-Side Changes Needed (2026-07-28)

**Gap closed**: another divergence in the ongoing dual-scorer reconciliation — the HK
mainland-flow gate. `_scan_for_entries()`'s T224-A gate blocks an HK entry when
`sig.reasons["flow_5d_net_hkd"] <= 0` (mainland money net-selling the stock via Southbound
Stock Connect) before it's ever scored. decision-engine had zero equivalent, so
`/decide/{symbol}` called standalone (e.g. `decide.tsx`) could silently approve an HK entry
against confirmed mainland outflow the fallback gate would reject outright.

**A genuine structural difference from every prior port in this series**: this one needed
**zero write-side changes**. `sig.reasons` (the full dict, which already carries
`flow_5d_net_hkd` when present) is ALREADY sent to decision-engine wholesale as the request's
`"reasons"` field — confirmed by checking `_call_decision_engine()`'s existing
`"reasons": sig.reasons or {}` line, which predates this fix entirely. `check_hard_rejects()`
already receives both `reasons: dict | None = None` and `market: str = "US"` as real
parameters, and already builds `_reasons = reasons or {}` locally for the pre-existing
T171/T220-D gates. The entire fix was a single new `if market.upper() == "HK": ...` block
reading `_reasons.get("flow_5d_net_hkd")` — no new kwarg on `_call_decision_engine()`, no new
`config_overrides` entry, no `paper_trading_engine.py` changes at all.

**Comparison detail preserved exactly**: the real gate uses `<= 0` (not `< 0`) — exactly zero
net flow blocks too. Confirmed and tested explicitly, since a naive port might have assumed a
strict `<` floor like `min_kscore`/`min_ta_score`.

**Tests**: 5 new cases in `services/decision-engine/tests/test_hard_rejects.py` (156 total, up
from 151) — negative flow blocks, exactly-zero flow blocks (the `<=` boundary), positive flow
does not block, missing flow data fails open (not all HK stocks are Stock Connect eligible,
matching `_scan_for_entries`' own fail-open behavior), and a dedicated market-scoping test
confirming a US portfolio is never blocked by this gate even with a negative
`flow_5d_net_hkd` present. 4 of the 5 needed their own frozen HK-local-time fixture
(`_FrozenHKDateTime`, 11:00 HKT) — the file's default `_frozen_market_hours` autouse fixture
freezes 11:00 ET (23:00 HKT), outside HK's trading session, which would otherwise mask this
gate behind the earlier market-closed check whenever a test passes `market="HK"`.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted:
1. The comparison logic (`if False:`) — caught by exactly the 2 blocking tests (negative and
   zero flow), while the does-not-block and fail-open tests correctly stayed green.
2. The `market.upper() == "HK"` gate (`if True:`) — caught by the dedicated market-scoping
   test, which found the gate firing for a US portfolio.

Full 156-test decision-engine suite green after every revert; `pyflakes` clean on the touched
file (confirmed via `git stash` — zero warnings either before or after).

**Two tracker corrections made in the same pass** (`frontend/src/pages/improvements.tsx`):
1. `T232-OC6-SURVIVORSHIP-IN-OUTCOMES` gained an `implementedNote` cross-referencing the
   2026-07-28 fix (see the T232-OC6 Revisited entry above) — its own `fix` text still
   described the delisted-loss scoring as deliberately deferred, which is now stale.
2. `T232-DL-DUALSCORER-DEBT`'s own running "dimensions remain open" tally (which still listed
   "declining-confidence" as unported, despite that gate being ported earlier in this same
   session) got a new `UPDATE 2026-07-28` paragraph correcting the count and documenting this
   HK-flow-gate port, dropping the open-dimension count from ~26 to ~24 (declining-confidence
   and HK Stock-Connect flow both now closed).

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 grep -n 'flow_5d_net_hkd\|mainland outflow' /app/src/api/core/hard_rejects.py
```
Should show the gate present. If an HK entry with confirmed negative flow is still approved
by `/decide/{symbol}` after confirming this, check whether the caller is actually sending
`reasons.flow_5d_net_hkd` and `market="HK"` in the request body at all — the gate is a no-op
without both.

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — Low-Volume Gate (T200) Ported; Price-Drift Investigated, Deliberately Deferred (2026-07-28)

**Gap closed**: another divergence in the ongoing series — the low-volume gate.
`_scan_for_entries()`'s T200 gate hard-skips a candidate when `volume_z < min_volume_z`
(default `-1.5`) before it's ever scored — a thin-market safety check (higher slippage/exit
risk). decision-engine only had the same value's SOFT scoring layer (`scorer.py`'s Layer 3a:
`+1` above `z=1.0`, `-1` below `z=-0.5`) — never a hard block, and materially looser than the
`-1.5` floor. Same zero-write-side-threading shape as the HK flow gate: `sig.reasons` already
carries `volume_z` when present, and `hard_rejects.py` already builds a `_reasons = reasons or
{}` local for the T171/T220-D gates — the entire fix was one new read-side block.

**A real scope-boundary finding, not built**: investigated porting price-drift (T196,
`max_price_drift_pct`, default 3.0%) in the same pass and found it does NOT fit the
zero-write-side pattern every other port in this series has used. Its reference price
(`_sig_ref_prices[stock.id]`, a separately bulk-prefetched signal-date close) is genuinely NOT
part of `sig.reasons` — unlike every field ported so far. Faithfully porting it needs either
(a) new write-side threading of `_sig_ref_prices[stock.id]` into the request payload, or (b) a
documented semantic substitution reusing the ALREADY-ported T171 gate's `reasons["last_price"]`
field instead (a different, arguably softer reference point than the source gate uses). Both
are real design decisions requiring their own scoped pass, not a rushed addition alongside a
genuinely-free port — correctly deferred rather than built into this session.

**Tests**: 4 new cases in `services/decision-engine/tests/test_hard_rejects.py` (72 total, up
from 68; decision-engine suite 160 total, up from 156) — below/at-or-above the `-1.5` floor,
gate skipped when `volume_z` itself is absent (T232-DL5 fail-open — a missing value must never
be treated as `0`/average, matching `_scan_for_entries` exactly), and a custom-threshold case.
Market-agnostic (unlike the HK-only flow gate) — no special HK-hours fixture needed, the
file's default US-hours fixture covers every test.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted:
1. The comparison logic (`if False:`) — caught by the 2 dedicated blocking tests.
2. The fail-open presence check (hardcoded `_vol_z_raw = 0.0`, `if True:`) — caught by the
   same 2 tests via a real value-mismatch (0.0 is above -1.5, so it never blocks — confirming
   the guard's removal changes behavior even when it doesn't crash).

Full 160-test decision-engine suite green after every revert; `pyflakes` clean.

**Tracker correction made in the same pass**: `T232-DL-DUALSCORER-DEBT`'s own running
"dimensions remain open" tally still listed HK Stock-Connect flow as unported from the
PREVIOUS update, even though it was actually closed in the very next commit after that update
was written (this session's earlier round). Corrected via a new `UPDATE 2026-07-28b`
paragraph — both the HK-flow tally error and this session's low-volume port are now reflected,
dropping the open-dimension count from ~25 to ~23.

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 grep -n 'min_volume_z\|Volume z-score' /app/src/api/core/hard_rejects.py
```
Should show the gate present. If a thin-market entry is still approved by `/decide/{symbol}`
after confirming this, check whether the caller is actually sending `reasons.volume_z` in the
request body at all — the gate is a no-op without one (correctly — not all symbols have a
computed volume z-score at every moment).

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — Index-Trend Gate (T221) Ported — First Genuine Write-Side-Only Change in This Series (2026-07-28)

**Gap closed**: another divergence — the index-trend gate. `_scan_for_entries()`'s T221 gate
hard-blocks all new entries when the market index (SPY for US, `^HSI` for HK) is down more
than `index_trend_gate_pct` (default `-1.5%`) same-day — a single-day macro-shock catch (FOMC
surprise, CPI print, an HSI circuit-breaker) distinct from the regime filter's sustained
multi-day bear/risk_off classification. decision-engine had zero equivalent, so
`/decide/{symbol}` could approve an entry right after a sharp index selloff the fallback gate
would reject outright.

**A genuine departure from the last 3 ports in this series** (HK flow, low-volume,
declining-confidence) — all three were "free" because their values were ALREADY flowing to
decision-engine somewhere (`sig.reasons`, already sent wholesale). Index-return was not — it's
a live, uncached, single yfinance `fast_info` call made once per scan cycle, with no existing
path to decision-engine (not in `sig.reasons`, not in `/stocks/regime`, not in any existing
`config_overrides` key). This required a real, if small, write-side change.

**Design decision made**: rather than adding a NEW decision-engine-side fetch (which would
need a new market-data endpoint field, plus the established cache+fallback+executor pattern
`regime.py`/`aggregator.py` already use for cross-service reads — real but avoidable extra
surface), the fix reuses the value `_scan_for_entries()` ALREADY computes for free once per
scan cycle, before the candidate loop even starts. `_idx_ret` was hoisted from a
block-scoped variable (previously only assigned inside the threshold-tripped branch, which
returns early) to a properly-initialized `_idx_ret: float | None = None` that survives to the
per-candidate `_call_decision_engine()` call site regardless of outcome — computed once,
reused for every candidate in the cycle, never re-fetched per-candidate.

**Implementation**:
1. `paper_trading_engine.py`'s `_call_decision_engine()` gained an `index_return_pct: float |
   None = None` parameter; the real call site passes `index_return_pct=_idx_ret` (the same
   hoisted local). Added `"index_return_pct"`/`"index_trend_gate_pct"` to `config_overrides`,
   both conditional on `index_return_pct is not None` (matching every other gate's
   conditional-inclusion pattern in this series exactly).
2. `hard_rejects.py`'s `check_hard_rejects()` — placed the new gate right after the
   `regime_state == "bear"` check, NOT alongside the `_reasons`-derived gates below (T171,
   T220-D, HK-flow, low-volume) — this gate is the only one in the file that's purely a
   function of `(market, index_return)` with zero per-symbol/per-portfolio state, so it
   belongs with the other market-wide gate (bear regime), not the reasons-derived cluster:
   ```python
   if cfg.get("index_trend_gate_pct") is not None:
       _idx_ret_val = cfg.get("index_return_pct")
       if _idx_ret_val is not None and float(_idx_ret_val) < float(cfg["index_trend_gate_pct"]):
           return f"Index down {abs(float(_idx_ret_val))*100:.1f}% today, exceeds {abs(float(cfg['index_trend_gate_pct']))*100:.1f}% threshold — macro shock, no new entries (T221)"
   ```

**Tests**: `services/market-data/tests/test_index_trend_config_wiring.py` (new, 5 cases,
source-text extraction) — confirms both keys reach `config_overrides`, the threshold's
fallback matches the real `-0.015` literal, both keys are conditional on presence, `_idx_ret`
is properly hoisted with a typed `None` default BEFORE the conditional block (guarding against
a real `NameError` — the bare `except Exception: pass` inside the block would otherwise
silently leave the name unbound on any exception path), and the call site passes the same
hoisted local through rather than re-fetching. `services/decision-engine/tests/
test_hard_rejects.py` gained 5 cases (165 total, up from 160) — below/at-exactly/above the
threshold (the real gate's strict `<`, not `<=`), gate skipped when either
`index_trend_gate_pct` or `index_return_pct` itself is absent, and a dedicated sign-safety
test confirming a RISING index return never blocks regardless of threshold.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. The comparison logic in `hard_rejects.py` (`if False:`) — caught by exactly the 1 dedicated
   blocking test.
2. The write-side `config_overrides` conditional inclusion in `paper_trading_engine.py`
   (removed both dict-spread lines) — caught by 3 of the 5 wiring tests (the 2 unrelated ones
   — the hoisting check and the call-site kwarg check — correctly stayed green).
3. The `_idx_ret` hoisting fix itself (reverted to the pre-fix block-scoped assignment) —
   caught by exactly the dedicated hoisting-order test.

Full 552-test market-data suite (up from 547) and 165-test decision-engine suite (up from 160)
green after every revert; `pyflakes` clean on both touched files (confirmed via `git stash`
that the 3 pre-existing `paper_trading_engine.py` warnings predate this change — only a line
number shifted).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"index_return_pct":\|_idx_ret: float' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'index_trend_gate_pct' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If an entry is still approved by `/decide/{symbol}` right
after a sharp index selloff, check whether the caller is actually sending
`index_return_pct`/`index_trend_gate_pct` in `config_overrides` — the gate is a no-op without
both (correctly — a `/decide/{symbol}` call made outside the real `_scan_for_entries()` scan
cycle, e.g. `decide.tsx`, has no way to supply a fresh index-return value of its own; this port
only closes the gap for the REAL trading path, which always goes through
`_call_decision_engine()`).

---


## Feature Reference: T232-DL-DUALSCORER-DEBT — Price-Drift Gate (T196) Ported — a Look-Alike Free Port That Turned Out Not To Be (2026-07-30)

**Gap closed**: another divergence in the ongoing dual-scorer reconciliation series — the
price-drift gate. `_scan_for_entries()`'s T196 gate hard-blocks a BUY candidate whose live
price has already drifted more than `max_price_drift_pct` (default 3%) above the daily close
recorded as-of the signal's own date — don't chase a stock that has already rallied hard since
the signal was computed. decision-engine had zero equivalent, so `/decide/{symbol}` called
standalone (e.g. `decide.tsx`) could silently approve an entry already extended well past the
signal's own reference price.

**Before writing any code, verified whether this could be a "free" port** (like the HK-flow/
low-volume gates, whose values were already flowing to decision-engine via `sig.reasons`) —
the natural-looking candidate was `reasons["last_price"]`, which the ALREADY-ported T171
gap-filter gate immediately below T196 in `hard_rejects.py` already uses for a materially
similar purpose (a looser 4% gap-vs-signal-close bar). A dedicated investigation traced where
`reasons["last_price"]` is set (`signal-engine/src/generators/signals.py`'s `_last_price =
float(_close.iloc[-1])`, the tail of the same daily-bar HTTP fetch T196 independently
re-queries) and found the two are **not provably equivalent**: `reasons["last_price"]` is a
**frozen snapshot captured once, at signal-generation time**, while T196's own `_sig_ref_prices`
lookup (`paper_trading_engine.py:4001-4022`) is **re-derived fresh, as-of-the-signal's-own-date**,
every time `_scan_for_entries()` runs. The two values only coincide when a candidate is
evaluated in the SAME refresh cycle that generated its signal — true in the common case, but
`_scan_for_entries()` evaluates freshly-fetched `buy_signals` every cycle with no guarantee a
given candidate's signal was generated in that same cycle (an older still-pending candidate
carried over from an earlier cycle is a real, non-hypothetical case). Reusing the frozen
`reasons["last_price"]` snapshot would have silently reintroduced exactly the staleness gap
this gate exists to guard against. **Decision made: thread a genuine fresh value through,
matching the index-trend gate's (T221) write-side pattern, not the free-port pattern.**

**Implementation**:
1. `paper_trading_engine.py`'s `_call_decision_engine()` gained a `sig_ref_price: float | None
   = None` parameter (appended last, matching every prior gate-parity param's ordering
   convention). Added `"sig_ref_price"`/`"max_price_drift_pct"` to `config_overrides`, both
   conditional on `sig_ref_price is not None` — the same conditional-inclusion pattern as every
   other gate in this series. The real call site inside `_scan_for_entries()` passes
   `sig_ref_price=_sig_ref_prices.get(stock.id)` — a **fresh, per-candidate** lookup from the
   same dict T196's own gate reads from a few lines earlier in the loop (unlike
   `index_return_pct`, a once-per-scan value; this is per-candidate like `kscore`/`ta_score`).
2. `hard_rejects.py`'s `check_hard_rejects()` — placed directly before the T171 gap-filter gate
   (matching `_scan_for_entries()`'s own gate ordering — T196 runs before T171 in the fallback
   engine), needing zero new function parameters (`cfg` already carries both keys):
   ```python
   if cfg.get("max_price_drift_pct") is not None:
       _ref_price = cfg.get("sig_ref_price")
       if _ref_price is not None and float(_ref_price) > 0:
           _drift_pct = (live_price / float(_ref_price) - 1) * 100
           _max_drift = float(cfg["max_price_drift_pct"])
           if _drift_pct > _max_drift:
               return f"Price drifted {_drift_pct:.1f}% above signal reference ${float(_ref_price):.2f} exceeds max drift {_max_drift:.0f}% — chasing blocked (T196)"
   ```
   A zero/negative `sig_ref_price` (a degenerate reference) fails open rather than dividing by
   a meaningless value.

**A real floating-point boundary flake caught before it could recur** (the same class already
documented for the earlier time-of-day/extended-move hard-reject tests): a test asserting
"exactly 3.0% drift does not block" computed `(103.0/100.0-1)*100 == 3.0000000000000027` in
real Python floating-point arithmetic — not exactly `3.0` — making `3.0000000000000027 > 3.0`
true and the test fail against CORRECT code. Fixed by using a comfortably-below value (2.5%)
instead of chasing the exact float boundary, matching this repo's own established convention
for this exact gotcha.

**Tests**: `services/market-data/tests/test_price_drift_config_wiring.py` (new, 6 cases,
source-text extraction matching `test_index_trend_config_wiring.py`'s established technique)
— confirms both keys reach `config_overrides`, the threshold's fallback matches the real `3.0`
literal, both keys are conditional on presence, the call site passes the fresh
`_sig_ref_prices.get(stock.id)` lookup (not the frozen `reasons["last_price"]` snapshot), and a
dedicated regression guard specifically confirming the call site never substitutes
`reasons["last_price"]`/`reasons.get("last_price")` for `sig_ref_price`. `services/
decision-engine/tests/test_hard_rejects.py` gained 6 cases (177 total, up from 171) —
below/within/above the threshold, gate skipped when either `max_price_drift_pct` or
`sig_ref_price` itself is absent, a custom-threshold case, and a degenerate zero/negative
reference price failing open.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. The comparison logic in `hard_rejects.py` (`if False:`) — caught by exactly the 2 dedicated
   blocking tests (beyond-threshold and custom-threshold), the other 4 skip/edge-case tests
   correctly stayed green.
2. The write-side `config_overrides` conditional inclusion in `paper_trading_engine.py`
   (removed both dict-spread lines) — caught by 3 of the 6 wiring tests via a real `ValueError`
   from `.index()` no longer finding the string, matching the exact failure mode of prior
   similar sabotages.
3. The call site's fresh lookup, swapped for the frozen `reasons["last_price"]` snapshot (the
   exact regression this port was designed to avoid) — caught by both dedicated call-site
   tests, one via a real assertion failure, one via a real `ValueError`.

Full 630-test market-data suite (up from 624) and 177-test decision-engine suite (up from 171)
green after every revert; `pyflakes` clean on both touched files (confirmed via `git stash`
that all 3 pre-existing `paper_trading_engine.py` warnings predate this change — only a line
number shifted, nothing new).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"sig_ref_price":\|sig_ref_price=_sig_ref_prices' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'max_price_drift_pct\|sig_ref_price' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If a chasing entry (well above its signal's own reference
close) is still approved by `/decide/{symbol}` after confirming both, check whether the caller
is actually sending `sig_ref_price`/`max_price_drift_pct` in `config_overrides` — the gate is a
no-op without both (correctly — like the index-trend gate, this only closes the gap for the
REAL trading path via `_call_decision_engine()`, not a standalone caller with no fresh
reference price of its own).

**Design invariant reinforced**: a gate that "looks like" it should be a free port (its
description sounds identical to an already-ported gate's data source) still needs the same
divergence check the genuinely-new-data gates got — don't assume equivalence between two
similarly-named values just because they're both "the signal's reference price" in prose;
trace where each is actually computed and confirm they resolve to the same query, at the same
moment, before reusing one for the other.

---


## Feature Reference: T232-DL-DUALSCORER-DEBT (cont'd) + AUD283 — 3 More Verified "Next
## Improvements" Fixes (2026-08-15)

**Trigger**: a plain "next improvements" request — surveyed `improvements.tsx` for genuine,
still-open candidates via a research agent, then personally re-verified the top candidates
directly against current code AND live production before proposing anything to the user. Two
candidates the agent's first pass flagged turned out to be false leads on closer inspection: a
claimed morning-digest error-isolation gap was already fixed, and a feared "the EC2 image is
stale" concern was unfounded (SA-33/`tune_sell_pillars`/`calibration.py` all confirmed present
and current on the live container). User authorized all 3 real, verified candidates as a batch
("fix them all with your best").

### 1. AUD283-GATEBACKTEST-LOOKAHEAD — `gate_backtest()`'s same-day-close look-ahead bias

**Symptom**: none live — confirmed via repo-wide grep that `gate_backtest()` (signal-engine,
`services/signal-engine/src/api/outcomes.py`) has zero live callers, a pure read-only research
endpoint per its own docstring. Found by inspection, not a bug report.

**Root cause**: the per-signal loop computed `entry = _price_at(row.stock_id, sig_date)` — the
signal's own calendar date — instead of T+1. This is the exact SE-F2 same-day-close look-ahead
bias already fixed everywhere else in this codebase (a live trader acting on a signal generated
during/after today's close can only enter the NEXT trading day). Every OTHER entry-price lookup
in this same file already used `signal_date + timedelta(days=1)` for exactly this reason — this
one function never received the same treatment.

**Fix applied**: `entry_date = sig_date + timedelta(days=1)`; `exit_date = entry_date +
timedelta(days=hold_days)` — matching this file's own established convention used elsewhere in
the same file. `_price_at()`'s own forward-only (`d >= target`) nearest-future-price semantics
are unchanged.

**Tests**: `services/signal-engine/tests/test_gate_backtest_lookahead.py` (4 cases) — the date
logic and `_price_at()` lookup are extracted via source-text and exercised against a real,
hand-built price series (not a hand-copied reimplementation), since `gate_backtest()` itself is
250+ lines of DB query construction not easily isolated as a whole. Adversarially verified 2
sabotage/revert cycles: reverting `entry_date` to the bare `sig_date` (caught by the dedicated
date-logic test), and removing `_price_at()`'s forward-only guard (`d >= target`) — a dedicated
behavioral test with a real 2-price fixture (a gap day between them) caught this via a wrong-
price assertion (100.0 instead of the correct forward-nearest 105.0), confirming the test
exercises real semantic drift, not just literal source text.

### 2. AUD283-MLWEIGHT-RATCHET — `calibrate_ml_weight()` validated against a hardcoded 0.5, not the real live cap

**Symptom**: none live — found by inspection while reviewing signal-engine's calibration
mechanisms for validation-gate consistency with their siblings.

**Root cause**: `calibrate_ml_weight()` (`services/signal-engine/src/api/calibration.py`)
already fetched `prev_cap` (the real, currently-live `ml_weight_global_cap`) at the top of the
function — but used it ONLY for `TuneHistory.old_value` bookkeeping. The actual validation gate
compared the candidate weight's held-out-slice EV against a fixed `0.0` (an implicit "beat a
coin flip" bar), with zero reference to `prev_cap`. A candidate genuinely WORSE than the live
cap, but better than a neutral blend, could still walk this parameter in a bad direction with no
requirement to ever beat where it actually already is — silently repeatable every time this
mechanism runs (this app's ML-fusion-weight tuning has no fixed schedule, so this could recur
indefinitely without detection).

**Fix applied**: `baseline_weight = prev_cap if prev_cap is not None else 0.5` (neutral fallback
ONLY on a true first-ever tune, when there's no real cap to beat). Both the candidate weight AND
`baseline_weight` are scored on the SAME held-out validation slice via `_accuracy_and_return()`.
`validated` now requires `candidate_ev > baseline_ev` — strict, an exact tie is correctly
rejected — whenever `prev_cap` exists; the `prev_cap is None` case auto-promotes against the
neutral baseline and records an explicit `"no_baseline_cap:first_tune"` gate-failure marker
(matching ml-prediction's own `ev_gate.py` `"no_baseline_params:first_tune_for_symbol"`
convention for the identical situation) rather than silently passing with no annotation.

**Tests**: `services/signal-engine/tests/test_calibrate_ml_weight_ratchet.py` (6 cases) — the
function's computational core is extracted via source-text `exec()` with every side-effecting
dependency (`set_ml_weight_global_cap`, `_record_tune_history`, `log`) injected as a fake, run
against real synthetic `Signal`/`Price` rows spanning a real chronological 70/30 train/
validation split.

**A real chronological-split discovery made during test-writing**: the function's own 70/30
split is computed over ALL observations pooled together (calibration + validation rows combined
BEFORE splitting), not independently per slice — "50 calibration rows + 20 validation rows"
does NOT reliably produce exactly 20 validation rows in the final split; spillover across the
70% boundary can shift the count in either direction. Several test assertions were loosened from
exact hand-computed literals to real inequality checks once this was confirmed, rather than
chasing an exact percentage the real split math doesn't actually guarantee.

**A self-caught "still passes after sabotage" trap, per this repo's own testing discipline**:
adversarial sabotage cycle 2 (removing the `prev_cap is None or` bypass from the `validated`
condition) initially produced a FALSE "still passes" result on the auto-promote test — the
original fixture's candidate EV happened to coincidentally still beat the neutral-0.5 fallback's
own EV even under the stricter, un-bypassed comparison. Recognized this as the exact red-flag
pattern this repo's discipline explicitly calls out (investigate, don't shrug), and re-engineered
the fixture so the neutral-0.5 fallback deliberately realizes a BETTER return (+10%) than the
candidate's own optimal weight (+9.76%) — re-run against the sabotage then correctly failed,
proving the bypass itself (not a coincidental win) drives promotion in that case. Both sabotages
reverted and confirmed byte-identical via md5 before moving on.

### 3. AUD283-DUALSCORER-SECTORCAP-OPENRISK — sector-exposure cap + open-risk cap ported into decision-engine

**Symptom**: none live directly, but this closes a real gap in the T232-DL-DUALSCORER-DEBT
series — `decision_engine_mode` defaults to `"primary"` in production, meaning any gate NOT
ported to decision-engine is silently bypassed on the live trading path whenever decision-engine
is reachable (the normal case). decision-engine's own `hard_rejects.py` already carried an
explicit comment naming this exact gap as unclosed before this fix.

**Root cause**: `_scan_for_entries()`'s own fallback gate (`paper_trading_engine.py`) already
enforces both the real dollar-exposure sector cap (`max_sector_pct` against a symbol's existing
open-position dollar exposure) and the aggregate open-risk cap (`(price-stop)*shares` summed
across every open trade, vs `max_open_risk_pct`) — but decision-engine had no equivalent of
either. The blocker was structural, not an oversight: decision-engine has no live per-position
price/stop data of its own, and the candidate's OWN not-yet-sized contribution (stop_distance/
shares) isn't computed until AFTER the decision-engine call in the real function — so a naive
port would need either a risky reorder of a large, delicate function, or sending the whole
open-position list across the service boundary.

**Fix applied — the "worst-case upper-bound approximation" pattern**: both real, portfolio-wide
aggregates (`_open_sector_values` keyed by sector, `_open_risk_total`) are computed ONCE per scan
cycle from the ALREADY-prefetched open book (no new per-candidate DB query, matching the
fallback gate's own established AUD19-PERF2 no-N+1-query discipline) and threaded through
`_call_decision_engine()`'s `config_overrides` as `open_sector_value`/`open_risk_total`. The
candidate's own not-yet-sized contribution is approximated on the decision-engine side using
`max_position_pct`/`max_loss_per_trade_pct` (both already sent) — the SAME worst-case ceilings
the real sizing logic itself caps against (confirmed at `paper_trading_engine.py`'s own PA-C1
max-dollar-loss-per-trade cap) — so the approximation can only ever be as-or-more conservative
than the real fallback gate, never less permissive. `hard_rejects.py` gained two new gates
reading these fields directly, both fail-open when `equity` or the aggregate itself is absent.

**Tests**: 10 new cases in `services/decision-engine/tests/test_hard_rejects.py` (220 total, up
from 210) covering both gates' block/pass/skip/fail-open behavior and the real default
thresholds (`max_sector_pct=0.25`, `max_open_risk_pct=0.12`). New
`services/market-data/tests/test_sector_open_risk_cap_config_wiring.py` (8 cases, source-text
extraction — `paper_trading_engine.py` can't be imported directly in this test environment)
confirms both new `config_overrides` keys, their conditional-inclusion guards, the function
signature, that the real call site passes THIS candidate's own sector (not a different one) and
the portfolio-wide open-risk total, and that both aggregates are built once per cycle from the
prefetched open book rather than re-derived per candidate or via a new DB query.

**Adversarially verified 5 sabotage/revert cycles across both files, all caught**: (1)
`hard_rejects.py` — disabling the sector-cap comparison (`if False:`) — caught by exactly 1 of
10 new tests; (2) `hard_rejects.py` — disabling the open-risk-cap gate entirely — caught by
exactly 2 of 10 new tests; (3) `paper_trading_engine.py` — removing both new `config_overrides`
entries — caught by 3 of 8 wiring tests (the signature/call-site/build-once tests correctly
stayed green, since they don't depend on this dict); (4) `paper_trading_engine.py` — swapping the
call site's per-candidate sector lookup for a sum across ALL sectors — caught by 2 of 8 wiring
tests; (5) `paper_trading_engine.py` — reintroducing a per-cycle DB query in place of the
prefetched-open-book derivation — caught by the dedicated no-new-query test. All 5 sabotages
reverted and confirmed byte-identical via md5 before moving on.

**Verification**: full 1,429-test market-data suite and 220-test decision-engine suite green;
pyflakes clean on all touched files (confirmed via `git stash` that every pre-existing warning
predates this change — line numbers only shifted from the new code added earlier in the same
files). Committed `75c362b`, deployed to EC2 (all 4 touched containers restarted clean, `/decide`
functionally exercised against a real symbol post-deploy, `_scan_for_entries()` directly invoked
against real production data with a rollback — zero writes, confirming the new computation runs
cleanly end-to-end).

**Tracker**: `improvements.tsx` Tier 284 / ids `AUD283-GATEBACKTEST-LOOKAHEAD`,
`AUD283-MLWEIGHT-RATCHET`, `AUD283-DUALSCORER-SECTORCAP-OPENRISK`.

---


## Feature Reference: T232-DL-GATEHARNESS-INPUTGAP — gate_harness.py's Replay Never Populated confidence_delta (Fixed 2026-08-17)

**The gap**: `gate_harness.py`'s own module docstring already discloses a trust-and-verify
review (2026-08-05) found every replayed `_should_enter()` call fed a systematically
INCOMPLETE view of what a real, live call receives. `_should_enter()` reads `confidence_delta`
directly off `signal_data` at the TOP LEVEL (not nested in `reasons`) — but
`replay_should_enter()`/`replay_extended_gates()` never populated that key at all, so every
replayed candidate silently scored as if confidence had never changed since the prior signal.
This isn't cosmetic: every walk-forward promotion decision this harness has EVER produced
(`min_entry_score`, `min_kscore`, `min_ta_score`, `min_volume_z` sweeps) was tuned against a
replayed score compressed toward zero relative to a real live call by up to several points, on
thresholds whose own candidate grids span a similarly narrow range.

**Fix — `confidence_delta` (SA-26), reconstructed point-in-time-safely**: new
`_historical_confidence_delta(session, stock_id, horizon, signal_date, current_confidence)` —
mirrors `_scan_for_entries()`'s own live computation (`paper_trading_engine.py` ~line 5197:
find the most recent PRIOR `Signal` row, `Signal.ts < sig.ts`, same stock+horizon, then
`round(sig.confidence - prior_conf, 1)`), but with `ts < signal_date` (strict less-than,
matching the live query's own semantics — the CURRENT day's own row must never be its own
"prior"). **Verified safe to replay historically BEFORE writing the code**, not assumed:
confirmed directly against production that `Signal` has a real per-calendar-day row history —
`SELECT stock_id, horizon, COUNT(DISTINCT DATE(ts)), COUNT(*) FROM signals GROUP BY stock_id,
horizon` shows `rows == distinct_days` for every `(stock, horizon)` pair, matching the table's
own `uq_signals_stock_horizon_day` unique index. `Signal.reasons` gets overwritten intraday
(a known, separately-documented gap elsewhere in this file), but the ROW itself — and its final
`ts`/`confidence` for that calendar day — persists as one distinct row per day, so "the prior
day's confidence" is a real, queryable historical fact, not something only ever visible live.
Both `replay_should_enter()` and `replay_extended_gates()` now call this and thread the real
value through `signal_data["confidence_delta"]`.

**`live_regime` investigated as a second candidate for the same treatment, found to be a
genuinely PERMANENT gap, not a fixable oversight**: the canonical regime classifier
(`_fetch_market_regime()`/`_fetch_hk_market_regime()`, bull/neutral/choppy/risk_off/bear) has
NO historical persistence anywhere in this codebase — it's Redis-cached, live-only, with no
time-series table to reconstruct "what was the regime on date X" from. `sig.reasons
["market_regime"]` LOOKS like a tempting substitute but is NOT the same classifier — it's
signal-engine's own separate, independently-computed regime value (a different vocabulary:
bull/high_vol/bear/unknown, per this repo's own Deep Audit #4 finding on this exact
divergence). Silently reusing it would feed a wrong-vocabulary value into `_should_enter()`'s
regime-score and pre-regime logic — a worse bug than the gap it would "fix." Left as `None`,
but now explicitly disclosed in the module's own docstring AND both walk-forward endpoints'
own `note` field — alongside a NEW disclosure that this whole harness only ever tunes the
decision-engine-OUTAGE fallback path (`decision_engine_mode="primary"` is the live default),
not the live primary trading path, which neither the docstring nor either `note` field had
previously stated.

**Tests**: `services/market-data/tests/test_gate_harness_confidence_delta.py` (new, 12 cases)
— `_historical_confidence_delta()`'s point-in-time correctness (a Signal row dated AFTER the
replayed date must never be picked up as "prior"), the strict `<` boundary (same-day rows are
correctly excluded), a missing prior row degrading to `None` rather than crashing, and both
`replay_should_enter()`/`replay_extended_gates()` actually threading the reconstructed value
into `signal_data`. Adversarially verified via 3 sabotage/revert cycles: removing the
point-in-time date bound, reverting the `confidence_delta` threading in each of the two replay
functions independently — all caught.

**A real SQLite-vs-Postgres comparison-semantics gap hit and worked around while writing the
boundary test**: SQLite lexicographically compares a tz-aware DATETIME string against a bare
DATE string as ALWAYS greater, regardless of `<` vs `<=` — a real quirk of the in-memory test
harness (not a bug in the real Postgres-backed production behavior), documented directly in
the test itself rather than silently worked around with no explanation.

Full 1,662-test market-data suite green at the time.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_historical_confidence_delta\|confidence_delta.*=" /app/src/backtest/gate_harness.py
```
Should show `_historical_confidence_delta()` defined and called from both
`replay_should_enter()` and `replay_extended_gates()`. If a walk-forward promotion result looks
suspiciously different from before this fix, that's expected — the replayed score distribution
genuinely shifted once `confidence_delta` started being populated; re-run the same sweep and
compare against a fresh baseline rather than assuming a regression.

---


## Design Reference: AUD288-REGIME-HARD-SUPPRESS-DEFERRED — Core Signal Engine Keeps a Soft Threshold-Raise for Regime, Not a Hard Suppress (Resolved 2026-08-18, no code change)

**The question the audit raised**: `signals.py`'s regime filter (bull/neutral/choppy/risk_off/
bear) only ever RAISES the BUY confidence threshold in bear/risk_off regimes — it never hard-
blocks a counter-trend signal outright. The squeeze-alert family (`T264-SQUEEZEFAMILY-REGIME-
FLAG`, 2026-08-15) already deliberately keeps regime a SOFT, informational-only flag for
itself, reasoning that hiding an alert risks silently withholding the one setup a user would
most want to see. The audit asked whether the CORE signal engine should behave the same way,
or should hard-suppress instead.

**Decision: keep the soft threshold-raise for the core engine too** — considered explicitly,
not defaulted to. Reasoning, generalized beyond just matching the squeeze-alert precedent:

1. **A threshold-raise is proportional, a hard suppress is binary.** A genuinely strong
   counter-trend setup (high confluence, high K-Score, real fundamentals) can still clear a
   RAISED bar, while a marginal one can't — a hard suppress discards both indiscriminately,
   with no way for a strong signal to still get through.
2. **This app's regime classifier is itself imperfect and lagging** (HMM/breadth-derived, not
   instantaneous truth). A hard suppress compounds classifier error into a silent,
   undetectable loss of visibility — the user never even sees the signal to evaluate it
   themselves. A threshold-raise degrades gracefully instead.
3. **Hard-suppressing the core BUY/SELL decision is a live-decision-affecting parameter
   change** — this codebase's own established discipline (walk-forward train/validation
   splits, promotion-margin gates — `gate_harness.py`, `outcomes_calibrate_apply`,
   `tune_strategy`) requires real validated evidence before shipping a change like this, not a
   hand-picked binary rule applied from an audit's own unvalidated recommendation.

**What to check if this looks wrong / how to see the current behavior**:
```bash
# See the regime-based threshold ADJUSTMENT (not suppression) directly:
docker exec stockai-signal-engine-1 grep -n "regime.*threshold\|_get_dynamic_buy_threshold" /app/src/generators/signals.py | head -10

# Check a real signal's own regime context (still generated even in a bear/risk_off regime,
# just against a higher bar):
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/signals/AAPL?style=SWING' | python3 -m json.tool
```
**If ever revisited**: the correct path is a walk-forward validated sweep of a hard-suppress
candidate against the current soft-raise baseline (matching this repo's own `gate_harness.py`
precedent — chronological train/validation split, must beat the live baseline's own validation-
slice EV, unconditional rejection of non-positive lift) — never an unvalidated binary flip.

---


## Design Reference: PT-MONITOR-NO-MARKET-HOURS-GATE — Position Monitoring Runs 24/7, Only New Entries Are Gated by Market Hours

**User asked directly** why a `"[Paper Trade] 🛑 Stop Loss Triggered — DFNS"` email arrived while
the US market was closed. Traced fully against real production logs and the DB before answering
— **the exit was correct and no false trigger occurred**; the interesting part is a genuine,
previously-undocumented design asymmetry.

**What actually happened**: Trade #105 (DFNS, entered $27.5475 on 2026-08-18) had already
trailed its stop to breakeven after a 12.5% gain. `paper.stop_to_breakeven` logged at
`2026-08-19T05:05:19Z` (01:05 AM ET), then `paper.exit` closed it 37 seconds later at
`exit_price=$27.1728` — computed from `_fetch_live_prices()`'s daily-close fetch. **Independently
re-ran the exact same `yf.download(["DFNS"], period="5d", interval="1d")` call live against
production and got the identical number**: DFNS's real 2026-08-18 close was `$27.200001`,
matching the DB's `current_price` field exactly. The price was real and correct — this was
Tuesday's already-final regular-session close, not a stale or fabricated value.

**The real finding**: in the SAME log window, `paper.entry_scan_skip` fired repeatedly with
`reason="outside_market_hours"` — confirming `_scan_for_entries()` correctly checks
`_is_market_hours("US")` before considering any NEW entry. `_monitor_positions()` (the function
that moved the stop and then closed the trade) has **no equivalent check anywhere in its body**
and runs on its own unconditional ~5-minute cycle regardless of session state. So a genuinely
correct stop-hit, computed from a real end-of-day close, can fire — and email a user — hours into
the overnight, worded identically to a live intraday trigger.

**Why this is NOT simply "add the market-hours gate everywhere"**: closing a position the moment
a stop is genuinely breached is usually the CORRECT behavior even outside regular hours — a
position sitting unprotected until the next open is a worse default than closing it promptly.
Gating `_monitor_positions()` behind market hours the way entries are gated would be a strictly
worse design, not a fix.

**The narrower, real gap is about framing, not suppression**: when an exit fires outside regular
market hours, the resulting email should say so explicitly ("this reflects Tuesday's
regular-session close — the market is currently closed") rather than reading identically to a
live intraday trigger. Tracked as **Tier 290** / `PT-MONITOR-NO-MARKET-HOURS-GATE` in
`improvements.tsx`, not yet built.

**What to check if a similar overnight email looks confusing**:
```bash
# Confirm the exit price matches the real prior-session close (not stale/fabricated):
docker exec stockai-market-data-1 python3 -c "
import yfinance as yf
raw = yf.download(['<SYMBOL>'], period='5d', interval='1d', auto_adjust=True, progress=False, group_by='ticker')
print(raw['<SYMBOL>']['Close'].dropna())
"

# Confirm the trade's own recorded exit_price against the DB:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, entry_price, current_stop, exit_price, exit_time, exit_reason FROM paper_trades WHERE symbol = '<SYMBOL>' ORDER BY id DESC LIMIT 3;"

# Confirm the entry-scan-vs-monitor asymmetry directly in logs (should show entry skips
# alongside real monitor/exit activity in the same window, outside market hours):
docker logs stockai-market-data-1 --since <window> | grep 'entry_scan_skip\|paper.exit\|paper.stop_to_breakeven'
```

---


## Deep Audit Series (2026-08-31): Decision Making — 4 of 5

**Fourth area of the requested sequential 5-area deep audit** (AI Signal, Short Squeeze,
Model Training already done — each found real bugs). Scope: `services/decision-engine/`,
the service producing the final ENTER/BLOCKED/HOLD verdict, separate from AI Signal
generation and Paper Trading execution. A dedicated audit agent (grounded in a pre-extracted
~80KB briefing of every decision-engine-relevant CLAUDE.md section, since the full ~20K-line
file itself exceeds a single prompt's budget) found 2 genuine, previously-undocumented
divergences, both personally re-verified against real current code before building anything.

### Finding 1 — `open_exposure_pct` sent to decision-engine used a DIFFERENT formula base than the local T194 gate it's supposed to give parity with

**Files**: `services/market-data/src/services/paper_trading_engine.py` — the value SENT to
decision-engine (`_open_exposure_pct`, built from `_open_sector_values` at line ~4962) vs.
the pre-existing local T194 hard-reject check a few dozen lines below it (line ~5009).

`_open_exposure_pct` was computed as `sum(_open_sector_values.values()) / equity * 100` —
reusing `_open_sector_values`, which sums `_best_price(trade, live_prices) * trade.shares`
(**live market value**) across every open position. But T194's own, older, pre-existing local
check computes `sum(float(t.entry_price) * float(t.shares) for t, _ in _prefetched_open)`
(**cost basis**). The commit that introduced the sent value (`T232-DL-DUALSCORER-WEEKLYPNL-
EXPOSURE`) reasoned that reusing `_open_sector_values` was safe because it "already uses the
SAME `_best_price()` convention" as the sibling sector-$ cap — true, but that reasoning never
checked the ACTUAL gate this value is meant to mirror, which uses a genuinely different base.

**Concrete failure scenario**: a portfolio whose open positions have appreciated ~30% since
entry (a realistic, non-degenerate state) would have `_open_exposure_pct` read ~30% higher
than what the local T194 check computes for the identical state — if the true entry-cost
exposure is 32% (T194 does NOT block, below the 40% default), the live-value version could
read ~42%, and decision-engine's ported T194 gate would **BLOCK an entry the fallback gate
itself would approve**, for the same portfolio at the same moment — the opposite of this
whole port's stated goal.

**Fix applied**: `_open_exposure_pct` recomputed as its own genuine sum over
`_prefetched_open` using `entry_price * shares`, matching T194's own formula exactly (T194 is
the older, authoritative, pre-existing local definition this port was built to mirror, so the
SENT value was corrected to match it, not the reverse). The two sibling ports in the same
code block (`_open_sector_values` for the sector-$ cap, `_open_risk_total` for the open-risk
cap) were independently checked against their own respective local formulas and confirmed
genuinely consistent — the bug was isolated specifically to the open-exposure port.

**A pre-existing test had codified the bug as a requirement** —
`test_open_exposure_pct_reuses_the_already_summed_sector_values_not_a_second_pass`
(`test_weekly_pnl_and_open_exposure_config_wiring.py`) explicitly asserted the WRONG formula
must be used. Corrected to assert the fixed formula and added a direct textual-parity test
confirming the two formulas (sent value vs. T194's own local check) are now byte-identical in
shape, not just "also mentions entry_price somewhere."

### Finding 2 — a real score layer existed only in the DE-outage fallback gate, never ported to decision-engine's own scorer

**Files**: `services/market-data/src/services/paper_trading_engine.py`'s `_should_enter()`
(the `AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK` calibrated-win-rate feedback layer) vs.
`services/decision-engine/src/api/core/scorer.py`'s `compute_score()` (confirmed via grep:
zero references to `calibration_feedback_enabled`/`calibrated_win_rate` anywhere in
decision-engine).

This layer is currently inert on every real portfolio (the flag defaults `False`, and its own
walk-forward validation sweep already found no measurable benefit in the tested window) — so
zero live impact TODAY. But it's a real architectural gap: if the flag is ever turned on for
a real portfolio in the future, decision-engine's `/decide/{symbol}` (the live
`decision_engine_mode="primary"` path) would silently ignore it while the fallback would
apply it — exactly the class of divergence the whole `T232-DL-DUALSCORER-DEBT` series exists
to close, introduced after the last comprehensive parity sweep.

**Fix applied**: `reasons["calibrated_win_rate"]`/`["calibrated_win_rate_count"]` are already
forwarded to decision-engine wholesale (`paper_trading_engine.py` sends the FULL `reasons`
dict — a genuine free port, zero write-side change needed). Added a new Layer 8 to
`compute_score()`, mirroring `_should_enter()`'s own logic exactly (`>=0.55` boosts `+1`,
`<=0.35` penalizes `-1`, gated behind `cfg.get("calibration_feedback_enabled")`, defaulting to
a strict no-op when absent) — 6 new dedicated tests, including a boundary test proving the
flag's ABSENCE is a strict no-op even with a real, strongly-positive `calibrated_win_rate`
present (not an implicit opt-in).

### Verification (both findings)

Adversarially verified both fixes independently: reverted each source change, confirmed the
new/corrected tests fail with clean, real diagnostics (Finding 1's test showed the wrong
formula's real computed values; Finding 2's 3 sabotage-targeted tests correctly failed while
the 3 absence/no-op tests correctly stayed green), restored and confirmed byte-identical via
`diff`. Full 282-test decision-engine suite green (up from 276); pyflakes clean on both
touched files. Deployed and live-verified against real production data: both containers
restarted clean, checksums confirmed byte-identical, a real `/decide/AAPL` call with
`calibration_feedback_enabled: True` set completed end-to-end with no crash, and a real
current signal row for AAPL confirmed carrying a genuine `calibrated_win_rate: 0.389` (n=1163)
already flowing through `reasons` exactly as the fix assumes.

**Everything else checked and confirmed genuinely clean**: route registration order
(`/decide/batch`/`/decide/score-replay` correctly registered before the `/decide/{symbol}`
catch-all), the falsy-zero/`or`-vs-`is not None` pattern (checked every `x or default`
occurrence in scorer.py/sizer.py/hard_rejects.py — the 2 candidates found are both currently
safe, though not by design), all ~23 hard-reject gates and all 7 pre-existing score layers
cross-checked line-by-line against `_should_enter()`'s own equivalents with no further
divergence found, `/decide/score-replay`'s historical-replay reconstruction (confirmed it
calls the real `compute_score()`/`min_score_for_regime()`, never a re-implementation),
`aggregator.py`'s config-fetch caching, and `llm_scorer.py`/`risk_agent.py`'s fail-open logic.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "AUD-DECIDE-OPENEXPOSURE-BASEMISMATCH" /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n "Layer 8: Calibrated win-rate feedback" /app/src/api/core/scorer.py
```

---

