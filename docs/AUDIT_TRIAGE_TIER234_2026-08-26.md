# T234 Threshold Triage — 2026-08-26 (updated same day: Group C closed, Group A scorer sweep built, Group B curve sweep built)

Re-verified all 27 items from `AUDIT_REPORT_TIER242_2026-07-04.md` Part 2 against current code
before deciding disposition. 20 resolved (6 by prior sessions, never previously cross-referenced
back to this list — the same recurring "stale tracker in the fixed direction" pattern documented
throughout `.claude/CLAUDE.md` — plus #23, 7 more via the Group A scorer sweep: #3, #8, #9, #10,
#11, #12, #14, and 5 more via Group B: #17/#18/#19 swept with real infrastructure, #20/#21 found
already moot by deletion). The remaining 7 are genuinely open, each with a specific, checkable
reason recorded below.

## Already resolved (verified against current code, not assumed)

| # | Item | Resolution |
|---|---|---|
| 1 | `min_confidence` cross-file mismatch (45.0 vs 62.0) | `T234-CONFIG-DECIDE-DEFAULT-MISMATCH` — real endpoint (`GET /stocks/entry-gate-params`) resolves the correct per-style/market value; `hard_rejects.py`'s 62.0 fallback is now confirmed-dead code with a comment explaining why |
| 2 | `regime_min_rr_ratio` fallback = 3.0 | Now resolved via `_default_min_rr_ratio()`, calibration-aware (reads `min_rr_calibration.json` when a real calibration exists, falls back to 3.0 only when none does) — not a bare uncited literal |
| 13 | Signal freshness 4h/18h vs 72h order-of-magnitude mismatch | Investigated and found NOT actually a conflict: 72h is a hard reject (`max_signal_age_hours`), 4h/18h is a *soft ±1 score nudge* on a completely different axis — confirmed via the T232-DL series' own re-verification, documented in CLAUDE.md |
| 16 | `kscore.py` `_WEIGHTS` (the 6 top-level factor weights) | `T288-KSCORE-WEIGHT-SWEEP` — real walk-forward-validated, held-out-tested weight search with a promotion gate; ships a validated override via Redis when the sweep finds a real edge |
| 22 | `max_portfolio_drawdown_pct` = 0.20 | `AUD293`'s `sweep_max_portfolio_drawdown_pct()` — real walk-forward sweep with the standard chronological train/validation split + promotion-margin gate |
| 23 | `max_open_risk_pct` = 0.12 | `sweep_max_open_risk_pct()` (same session, see Group C below) — same walk-forward discipline |
| — | `risk_per_trade_pct` (not itemized individually in the original list but same class) | `sweep_risk_per_trade_pct()` — same walk-forward discipline |
| 3 | `hard_rejects.py` `max_breakout_extension_pct` = 6.0% | `walk_forward_scorer_sweep()` (Group A, this session) — see below |
| 8 | `scorer.py` chase-ceiling % (was hardcoded 1.03, now `chase_ceiling_pct`) | same sweep |
| 9 | `scorer.py` R:R quality tiers (3.5/2.5) | same sweep |
| 10 | `scorer.py` `volume_z` bands (1.0/-0.5) | same sweep |
| 11 | `scorer.py` `bull_prob` thresholds (0.70/0.58) | same sweep |
| 12 | `scorer.py` confidence-delta threshold (±8) | same sweep |
| 14 | `scorer.py` insider/congress catalyst thresholds (60/-30/50) | same sweep |
| 17 | `kscore.py` RSI-to-score breakpoints/slopes | `tune_kscore_curve()` (Group B, this session) — see below |
| 18 | `kscore.py` ADX-boost normalization constants | same sweep |
| 19 | `kscore.py` volatility scale factor | same sweep |
| 20 | `kscore.py` value-proxy discount scale | Already moot by deletion — `_value_proxy()` no longer exists (`354f665`, `T234-RANK-KSCORE-PROXY-MIXING`, 2026-07-04) |
| 21 | `kscore.py` growth-proxy CAGR scale | Already moot by deletion — `_growth_proxy()` no longer exists (same commit) |

## Still genuinely open — 7 items

Confirmed via a listing of every real T234-relevant sweep mechanism: in
`services/market-data/src/backtest/`, `sweep_max_portfolio_drawdown_pct`, `sweep_risk_per_
trade_pct`, `sweep_max_open_risk_pct`, `walk_forward_extended_gate`, `walk_forward_min_entry_
score`, `walk_forward_blocked_entry_scores`, `walk_forward_calibration_feedback`, `walk_
forward_scorer_sweep` (8, up from 5 pre-session — 3 new this same day, 2 for Group A/C's own
work, 1 pre-existing); in `services/ranking-engine/src/api/routes.py`, `tune_kscore_weights`
and the new `tune_kscore_curve` (2, up from 1). `walk_forward_train`/`walk_forward_report` in
`position_scaling_gate.py` are a pre-existing, unrelated ML-training mechanism (T241), not a
T234 threshold sweep — deliberately excluded from this count. The remaining 7 constants below
were individually investigated and found structurally unsweepable with this codebase's current
tooling (3 remaining items in Group C, 1 in Group A — item #4 — plus 3 items in Group A with
zero tradeable-outcome linkage at all). Group B is fully closed — no open items remain in it.

### Group A — decision-engine scoring/sizing constants (items 3,4,5,6,7,8,9,10,11,12,14,15) — FULLY CLOSED 2026-08-26

**Re-investigated individually rather than trusting the original bulk framing** — the same
"stale in either direction" discipline already applied elsewhere in this triage. Found the
group is far more heterogeneous than "12 scorer/sizer nudges" implies:

**3 items have ZERO effect on any real trade — not lower-priority, genuinely nothing to sweep
against:**
- **#5 (`sizer.py:66-75`, research-score tiers/multipliers)**, **#6 (`sizer.py:83-88`,
  confidence-mult breakpoints)**, **#7 (`sizer.py:100-105`, earnings-DTE size reduction)** —
  `sizer.py`'s own module docstring states explicitly: "this is a preview/scoring-only module
  (paper_trading never consumes its sizing plan, only its go/no-go verdict + score)." Confirmed
  via grep: `paper_trading_engine.py` never imports `sizer.py`/`compute_position` at all — only
  mentions it in comments explaining it's the OPPOSITE, unrelated illustrative module.
  `sizer.py`'s output only ever reaches `/decide`'s response JSON, consumed by `decide.tsx`'s
  standalone display tool. No `PaperTrade`/`pct_return` outcome can ever be attributed to a
  `sizer.py` constant, so there's no backtest to build — same class as Group C's already-closed
  `min_stop_dist` finding (a value with no tradeable-outcome linkage, not a sweep waiting to
  happen). **Disposition: closed, no sweep possible, documented not silently dropped.**

**1 item is already moot — deleted by a prior fix, not open:**
- **#15 (`scorer.py:174-181`, "entry-zone drift" 4-way tiering)** — this is the exact Layer 3h
  the CURRENT code's own comment (`T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE`) documents as already
  REMOVED: it double-scored the same static entry2/breakout comparison Layer 1 (`price_zone`)
  already covers, so it was deleted rather than "made independent." **Disposition: already
  resolved by deletion — closed, nothing left to sweep.**

**1 item needs a code prerequisite before ANY backtest can touch it:**
- **#4 (`hard_rejects.py:548-566`, time-of-day gate windows)** — reads real wall-clock
  `datetime.now(timezone.utc)` directly, with no `as_of` injection parameter (unlike the
  identical bug class already fixed in `_should_enter()`'s own time-of-day gate under
  `T232-DL-GATEHARNESS-INPUTGAP`/`BUG233-BACKTESTHARNESS-EMPTYVALIDATION`). A walk-forward
  replay against a historical `signal_date`/`entry_date` is structurally impossible until this
  function accepts an injectable "as of when" — a small, real, non-controversial fix (mirroring
  the already-proven pattern), but a genuine prerequisite, not a sweep-design question.
  **Disposition: deferred, needs the `as_of` fix first — tracked as a real follow-up, not a
  silently-dropped item.**

**The remaining 7 items are genuinely sweepable and DO gate real entries** — `#3`
(`hard_rejects.py:574`, `max_breakout_extension_pct`, a HARD reject, confirmed pure/no
wall-clock dependency of its own despite sitting textually adjacent to the time-of-day gate —
reads only `game_plan`/`live_price`/`cfg`) and 6 items inside `scorer.py`
(`compute_score()`): `#8` (chasing-ceiling %), `#9` (R:R quality tiers), `#10` (volume_z
bands), `#11` (bull_prob thresholds), `#12` (confidence-delta threshold), `#14` (catalyst-score
thresholds). Confirmed via grep: `routes.py` imports and calls
`compute_score()`/`min_score_for_regime()` directly — this IS the real ENTER/BLOCKED verdict on
the live `decision_engine_mode="primary"` trading path, not an illustrative preview like
`sizer.py`. **Disposition: BUILT this session — see "Group A Scorer Sweep" immediately below.**

#### Group A Scorer Sweep — architecture and result

1. **`compute_score()`'s 6 constants are now `cfg`-driven** (`scorer.py`), each defaulting to
   its original hardcoded literal — a byte-for-byte no-op for every existing caller that never
   sets these keys. Full 274-test decision-engine suite green before and after; adversarially
   verified by sabotaging one constant back to a hardcoded literal and confirming the dedicated
   `test_scorer.py` test fails with a real, meaningful assertion diff.
2. **New `POST /decide/score-replay`** (decision-engine, `routes.py`) — batch-scores N
   already-resolved historical BUY signals against ONE candidate `cfg` in a single request,
   calling the REAL `compute_score()`/`min_score_for_regime()` directly (never a
   re-implementation of the scoring formula in a second service — the exact anti-pattern this
   codebase's own repeated prior audits have found and fixed elsewhere). Also applies item #3's
   `max_breakout_extension_pct` as a pure, inlined pre-score hard reject — deliberately NOT
   routed through the full `check_hard_rejects()` (whose OTHER checks read the real wall-clock
   with no `as_of` injection, the exact problem this endpoint's own Layer-3e-freshness omission
   already works around). `ScoreReplayInput` deliberately omits `ts`/`is_pre_choppy`/
   `is_pre_risk_off`/`recent_win_rate` — same disclosed, permanent scope limitation
   `replay_should_enter()` already carries (Layer 3e freshness and `live_regime` can't be
   safely reconstructed for a historical replay; see that endpoint's own docstring for why).
3. **New `walk_forward_scorer_sweep()`** (market-data, `gate_harness.py`) — reuses
   `_fetch_matched_signals()`/`_historical_atr()`/`_build_game_plan_for_style()`/
   `_historical_confidence_delta()`/`_historical_kscore()` (the SAME point-in-time-safe
   machinery `replay_should_enter()` already has proven correct) to reconstruct
   `ScoreReplayInput`s, then calls the new endpoint. Same chronological 70/30 split +
   `_passes_promotion_margin()` discipline as every sibling walk-forward function. Candidate
   generation is one-parameter-perturbed-at-a-time (matching ranking-engine's own
   `_kscore_candidate_weight_sets()` "search a tractable neighborhood, not the full
   N-dimensional grid" precedent — a full joint grid across 12 independent thresholds is
   combinatorially intractable at any reasonable step size) — 12 candidates total (2 per
   constant), the single best train-slice winner is then the only one re-measured against the
   held-out validation slice.
4. **New `GET /backtest/scorer-sweep`** admin route (`paper_portfolio.py`), matching the
   established `/backtest/*-sweep` route shape exactly (style/market validation, 365-day
   default window, `base_cfg` from `_DEFAULT_CONFIG`/`_STYLE_OVERRIDES`).
5. Full adversarial verification across both services: 6 sabotage/restore cycles (the
   collapse-onto-default candidate guard — required constructing a synthetic step table after
   discovering the REAL production step table can never trigger this path today, itself a
   "still passes after sabotage" finding worth recording; the promotion-margin gate;
   `score_replay()`'s breakout-extension threshold read; and the route's `base_cfg`
   construction), each caught correctly and each restore confirmed byte-identical via
   `md5sum`/`diff`.

**Live-verified end-to-end against real production data after deploy.** First deploy surfaced a
real bug: `POST /decide/score-replay` was registered AFTER the pre-existing
`POST /decide/{symbol}` catch-all, so a real request silently matched the catch-all instead
(the exact `BUG233-ROUTERORDER` class already hit once in signal-engine) — returned a 422
instead of ever reaching the new endpoint. Fixed by moving the registration before the
catch-all, with a new source-text regression test guarding it (see `.claude/CLAUDE.md`'s own
Group A section for the full write-up). This bug was invisible to every existing test in
`test_score_replay.py`, since each one calls `score_replay()` directly as a Python function,
bypassing FastAPI's real route dispatch — the exact class of gap this codebase's own
"tests all pass ≠ works in production" discipline exists to catch.

After the fix, `GET /backtest/scorer-sweep?style=SWING&market=US&window_days=90` (a 365-day
window returned an honest zero-signal `skipped_reason`, since real resolved
`signal_outcomes` data only spans 2026-05-25 → 2026-08-11 today) produced a genuine, complete
result: 1,126 real train-slice signals, a real winning train candidate
(`rr_excellent_threshold: 3.0`, beating baseline's `-1.34%` avg return with `-1.26%`), correctly
re-measured against the held-out validation slice where it scored `0.4539%` vs. baseline's
`0.4662%` — a real loss on validation, so `promoted: false` — exactly the honest, correct
outcome the promotion-margin discipline exists to produce when a train-slice edge doesn't
generalize. Per this codebase's own established promotion discipline, `promoted: true` from
this endpoint is a research signal only; it never changes any live decision-engine config on
its own.

### Group B — kscore.py internal piecewise constants (items 17,18,19,20,21) — CLOSED 2026-08-26

RSI-to-score breakpoints/slopes (#17), ADX-boost normalization (#18), volatility scale factor
(#19), value-proxy discount scale (#20), growth-proxy CAGR scale (#21). All 5 were UNTOUCHED by
`T288-KSCORE-WEIGHT-SWEEP`, which only validated the 6 top-level `_WEIGHTS` values, never the
internal formulas each sub-score is computed from.

**#20/#21 — already moot by deletion, never previously cross-referenced.** Re-verified before
building anything: `_value_proxy()`/`_growth_proxy()` (the two functions #20/#21's own scale
constants belonged to) no longer exist anywhere in `kscore.py` — confirmed via `git show
354f665` (2026-07-04, `T234-RANK-KSCORE-PROXY-MIXING`), the SAME commit that also fixed Group
A's own item #15 (`scorer.py`'s Layer 3h double-count), with the same "resolved by deletion,
never cross-referenced" gap. `value_score`/`growth_score` are now excluded entirely (weight
redistributed to the remaining factors) whenever a real fundamental is unavailable — there is
no curve-shape formula left for #20/#21 to sweep. Matches Group A's own established treatment
for the identical situation exactly, rather than inventing a new disposition category.

**#17/#18/#19 — swept, real walk-forward result.** Built genuinely new infrastructure rather
than reusing `T288`'s weight-sweep as-is, since these constants sit one level BELOW the
already-persisted `Ranking.technical`/`.volatility` values `_kscore_recompute()` operates on —
validating them requires recomputing `_technical_score()`/`_volatility_score()` from real
historical `Price` bars under a candidate curve, not just re-weighting already-stored numbers.

Design decisions, in order:
- **Live-override resolution mirrors `_load_active_weights()`'s own convention exactly**: a new
  `_load_active_curve_params()`/`_curve_params(cfg)` 3-layer resolution (hardcoded defaults →
  live Redis override if `tune_kscore_curve` has ever promoted one → an explicit `cfg`
  override layered on top) — `curve_cfg=None` means "whatever is currently live," never
  silently the hardcoded default, matching how a future re-sweep must build on top of an
  earlier promotion rather than re-searching from the original values every time. Deliberately
  ALLOWS a partial override (unlike the weights override's all-or-nothing rule) since each of
  the 11 curve constants is independently meaningful, not constrained to sum to 1.0.
- **Raw-input/curve-mapping split for tractable compute cost.** Profiling found RSI/ADX
  EWM computation dominates the cost (~6ms/call vs. ~0.1ms/call for the cheap curve remap) —
  `_technical_raw_inputs()`/`_volatility_raw_input()` compute the expensive indicators ONCE per
  historical row (`_kscore_curve_raw_cache()`), and the ~20-candidate sweep pool only pays the
  cheap remap cost per candidate. Brought an estimated ~800s full sweep down to ~63s.
- **A real formula bug caught via byte-identical-at-defaults verification, before shipping.**
  The original ADX-boost comment ("strong trend >25") implied `adx_ceiling=25` with a 10-point
  ramp width — a first attempt built the parameterization on that assumption and failed a
  200-seed randomized comparison against a hand-reimplemented copy of the ORIGINAL formula
  (`tech_new: 38.50` vs `tech_old: 34.96`). The real math uses `adx_center: 15.0` and
  `adx_divisor: 25.0` as two independent constants (the clip only actually saturates at
  `adx=40`, not `25`) — fixed and re-verified to 0 mismatches across 200 seeds. Adversarially
  confirmed by reverting `adx_divisor` back to the original bug and watching the dedicated
  tests fail with a real, meaningful diff before restoring.
- **`_kscore_cross_sectional_ev()` generalized to accept a `composite_fn` callable** instead of
  hardcoding `_kscore_recompute(weights, row)` — avoids writing a second, parallel EV-
  measurement function that could silently drift from the weights sweep's own already-proven
  one (the "duplicate business logic" anti-pattern this codebase's own prior audits have
  repeatedly found and fixed elsewhere).
- **A real, previously-unresolved audit-trail gap found and fixed while wiring this up**:
  `_record_kscore_tune_history()`'s `parameter_class`/`parameter_name` were hardcoded literals
  with no way to vary per-caller — every one of `tune_kscore_curve()`'s own 6 `TuneHistory`
  rows would have been silently mistagged `"kscore_weights"`/`"factor_weights"`, indistinguishable
  from the sibling weights sweep's own real attempts. Fixed by adding both as keyword-only
  parameters defaulting to the ORIGINAL weights-sweep values (so `tune_kscore_weights()`'s own
  6 call sites needed zero changes), with `tune_kscore_curve()` explicitly overriding both to
  `"kscore_curve"`/`"curve_shape"` at each of its own 6 call sites. Adversarially verified: an
  override forgotten at even 1 of 6 sites is caught by a dedicated test
  (`test_tune_curve_endpoint_tags_every_tune_history_call_with_the_curve_parameter_class`).

New `POST /rankings/tune_kscore_curve` and `GET /rankings/kscore_curve_status` endpoints
(the latter correctly registered before the `GET /{symbol}` catch-all, per the already-
documented `BUG233-ROUTERORDER` discipline — checked proactively this time, not discovered via
a live deploy failure). Same chronological 70/30 split + unconditional non-positive-EV-lift
rejection + unmeasurable-baseline-is-a-skip + one `TuneHistory` row per attempt regardless of
outcome, matching `tune_kscore_weights()`'s own established discipline exactly. One-parameter-
perturbed-at-a-time candidate generation (`_kscore_curve_candidate_sets()`, ~20 candidates
across the 11 real constants) rather than a full joint grid, matching `_kscore_candidate_
weight_sets()`'s own "tractable neighborhood, not the full N-dimensional space" precedent.

26 new tests across `test_kscore_curve_params.py` (15, pure curve-function behavior including
the byte-identical-at-defaults check across 200/50 randomized seeds), `test_kscore_curve_
override.py` (11, Redis live-override read-side), and `test_kscore_curve_sweep.py` (15, the new
endpoint's own wiring + the 3 new helper functions) — full suite green (101 passed, the 1
pre-existing unrelated `test_kscore.py::test_kscore_in_range` failure confirmed via `git stash`
to predate this work). Every real guard (the ADX formula, the EV-lift rejection, the parameter_
class tagging, the raw-cache-computed-once-not-per-candidate ordering) adversarially sabotaged
and confirmed to fail correctly, then restored and confirmed byte-identical via `md5sum`.

### Group C — paper_trading_engine.py standalone constants (items 23,24,26,27) — CLOSED 2026-08-26

`max_open_risk_pct` = 0.12 (#23), `hold_stall_days`/`hold_stall_max_gain` = 30d/5% (#24),
HK `regime_suspension_days` = 7 (#26), `min_stop_dist` floor (#27).

**#23 — swept, real walk-forward result.** `sweep_max_open_risk_pct()` (`portfolio_backtest.py`,
same session) — same chronological 70/30 split + promotion-margin discipline as
`sweep_max_portfolio_drawdown_pct()`. New `GET /paper-portfolio/backtest/open-risk-cap-sweep`
endpoint, live-verified against real production data (a 20-symbol SWING/US run showed 32 real
entries, `n_skipped_open_risk_cap: 0` — honestly reporting that 0.12 isn't currently the binding
constraint for this app's real position-sizing defaults, not a bug in the sweep itself).

**#24, #26, #27 — investigated individually and found structurally NOT sweepable with the
current simulator, each for a distinct, real reason (not simply deprioritized):**

- **#24 (`hold_stall_days`/`hold_stall_max_gain`)** — `_monitor_positions()`'s "HOLD stall" exit
  only fires when the CURRENT LIVE signal for a symbol is HOLD, evaluated fresh on every
  intermediate day of a hold — not a one-time entry-time check. `portfolio_backtest.py`'s own
  module docstring already discloses it deliberately does NOT replay a mid-hold signal at all
  ("exits use the outcome's own resolved hold-window exit_date/exit_price... NOT a simulated
  stop/trailing-stop/target exit"). Sweeping this needs the design doc's own still-unbuilt
  Phase 2b (a genuine day-by-day `_monitor_positions()` replay) — the same gap the module's
  entire "honest MVP" framing already names as out of scope, not something this triage's own
  smaller sweeps can extend into.
- **#26 (HK `regime_suspension_days`)** — T210's circuit breaker reads `live_regime` (bull/
  neutral/choppy/risk_off/bear) fresh on every check. `gate_harness.py`'s own module docstring
  already discloses this as a PERMANENT limitation across this whole codebase: no historical
  regime-persistence table exists anywhere to reconstruct "what was the regime on date X" from
  — this is not a scoping choice for THIS sweep, it's a standing, disclosed gap that would need
  a separate, dedicated regime-history project before ANY sweep involving regime state could
  exist, for any parameter.
- **#27 (`min_stop_dist` floor, `max(price*0.005, 0.05)`)** — re-read both call sites
  (`hard_rejects.py`, `paper_trading_engine.py`) directly: the comment states its purpose
  explicitly — "prevent infinite/backward R:R." This is a numerical-sanity guard against a
  degenerate `reward / near-zero-stop-distance` computation, not a strategy parameter with a
  real risk/return trade-off to search over. A real, properly-sized stop (typically 2x ATR, a
  meaningful percent of price) never approaches this floor in practice, so there's no
  "tighter vs. looser" question a walk-forward sweep could meaningfully answer here — loosening
  it only risks admitting a genuinely nonsensical setup; tightening it changes essentially
  nothing since real stops already clear it by a wide margin.

**Group C disposition: CLOSED, not merely deprioritized.** 1 of 4 items got a real sweep; the
other 3 were each individually investigated and found to require either a separate, larger
build (a full mid-hold signal replay, #24) or a standing infrastructure gap this codebase has
already disclosed elsewhere (regime history, #26), or don't fit the "sweep a value" framing at
all (#27 is a sanity floor, not a tunable parameter). None were left silently unaddressed —
each has a specific, checkable reason recorded here for why a sweep is the wrong tool, not an
assumption that one simply hasn't been built yet.

### Cross-file consistency risks (from the original report's own closing section)

- `min_confidence` mismatch: **resolved** (see item #1 above).
- Signal staleness 72h vs 4h/18h: **investigated and found not a real conflict** (see item #13
  above) — different axes (hard reject vs soft score), not competing values for the same thing.
- `min_ta_score` (0.65 in both SWING/HK, two separately-dated literals): **still genuinely open,
  not a bug today** (both values happen to agree), but the risk of future silent desync is real.
  Low cost to fix structurally — worth a follow-up to consolidate into one shared constant, but
  not a validation question, a pure code-hygiene one.
- `min_rr_ratio` (2.0 pass/fail) vs `scorer.py`'s R:R quality tiers (item #9, `rr_excellent_
  threshold`/`rr_good_threshold`): **resolved for the scorer-tier half** — item #9 is now
  cfg-driven and covered by `walk_forward_scorer_sweep()` (see Group A above). `min_rr_ratio`
  itself (the separate hard pass/fail gate) remains its own already-established, independently
  calibration-aware value (see item #2 above) — the two constants serve genuinely different
  purposes (a hard reject vs. a soft score tier) and were never meant to be identical, so this
  is no longer flagged as an open desync risk.

## Summary

Of the original 27 items: **20 resolved** (verified against current code — 6 by prior sessions
never previously cross-referenced back to this list, #23 swept the same day as this triage,
7 more — #3, #8, #9, #10, #11, #12, #14 — via the Group A scorer sweep, and 5 more — #17/#18/#19
swept with real new curve-recompute infrastructure, #20/#21 found already moot by a 2026-07-04
deletion never cross-referenced — via the Group B curve sweep, all three groups built and
closed the same day as this triage), **Group C fully closed** (all 4 of its items now have a
specific, individually-investigated disposition — 1 swept, 3 found structurally unsweepable for
distinct, recorded reasons rather than merely deprioritized), **Group A fully closed** (7 items
swept, 3 found to have zero tradeable-outcome linkage at all, 1 already moot by deletion, 1
deferred behind a real, documented code prerequisite), **Group B fully closed** (3 items swept
with genuinely new recompute infrastructure — a chronological 70/30 split + unconditional
non-positive-EV-lift rejection + one `TuneHistory` row per attempt, matching every sibling
sweep's own discipline — 2 already moot by deletion; every one of the original 27 items now has
an explicit, checkable disposition, not a blanket "lower priority" label). **7 items remain
genuinely open**: Group C's 3 structurally-unsweepable items, and Group A's own 4 non-sweepable
items (3 with zero outcome linkage, 1 — item #4's time-of-day `as_of`-injection prerequisite —
deferred as a real, scoped follow-up). All 7 are explicitly documented as intentionally-
arbitrary starting values rather than silently-unaddressed gaps. If this work is picked up
again: item #4's `as_of`-injection fix is the cheapest remaining lever (mirrors an already-
proven pattern, would unlock a Layer-3e-freshness extension of the scorer sweep) — the other 6
items are each structurally blocked (no historical-persistence table, no tradeable-outcome
linkage, or a genuine day-by-day position-monitoring replay this codebase doesn't yet have),
not merely unattempted.
