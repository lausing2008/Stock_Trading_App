## Research: Per-Horizon AI Signal Strategy Tuning (2026-07-16)

**Ask:** tune and find the best strategy for AI Signal, per horizon (SHORT/SWING/LONG/GROWTH).
Research-only pass (no code written yet) — documents current state, gaps, and a phased plan.

### Current per-horizon strategy (`_STYLE_PROFILES`, `services/signal-engine/src/generators/signals.py:1278`)

| Param (hardcoded fallback) | SHORT | SWING | LONG | GROWTH |
|---|---|---|---|---|
| buy_threshold (bull/high_vol/bear/unknown regime) | .63/.65/.68/.62 | .72/.74/.76/.72 | .60/.65/.70/.62 | .60/.65/.68/.60 |
| hold_threshold (bull regime) | .46 | .50 | .46 | .45 |
| ml_weight_cap / ml_weight_floor | .30 / .10 | .65 / .15 | .45 / .12 | .60 / .20 |
| adx_min | 27 | 15 | None | 12 |
| min_pillars_for_buy | — | 3 | 3 | — |
| max_compress_ratio | .70 | .55 | .65 | .60 |
| BUY hold_days (`_OUTCOME_HOLD_DAYS`, routes.py:4958) | 7 | 14 | 28 | 14 |
| SELL hold_days (`_SELL_OUTCOME_HOLD_DAYS`) | 5 | 7 | 10 | 7 |

SELL threshold is a flat `_SELL_THRESHOLD_FALLBACK = 0.35` — no regime tiers (unlike BUY).
Live values are Redis overlays with 30-day TTLs, priority order: `stockai:watchdog:{STYLE}:threshold`
→ `stockai:signal_thresholds:{STYLE}` (+ `:SELL:{STYLE}`) → hardcoded fallback above; separately
`stockai:style_tune:{STYLE}:{param}` for ml_weight_cap/adx_min/high_vol_compression/breadth_compression.
All Redis-written values silently revert to the hardcoded table on TTL expiry with no alert.

### What's scheduled vs. manual-only

**Weekly (Sun 14:00 PT, `market-data/scheduler.py` `_weekly_full_refresh`):** `/ml/tune_all`
(AUC-only, not P&L), `calibrate_ta_weights`, `calibrate_conviction_weights`,
`outcomes/calibrate/apply` (per-horizon BUY+SELL threshold sweep, 0.55–0.85, routes.py:3614),
`tune_style_profiles` (ml_weight_cap 0.15–0.75, adx_min 10–40, compression on/off; routes.py:4031),
`calibrate_entry_weights` (paper-trading), RL training, `calibrate_min_rr_ratio` (see the
SELFIMPROVE-NEVER-CALIBRATED-PARAMS section elsewhere in this file).

**Daily:** `signal_watchdog` (06:10 ET — emergency ±0.02–0.03 nudge, 7-day TTL, max 3 tightenings
before flagging for manual review).

**Manual-only (never scheduled):** `calibrate_ml_weight`, the gate harness
(`GET /paper-portfolio/backtest/min-entry-score` + `/promote`), `gate_backtest`,
`backfill_realized_ev`.

**Never tuned anywhere, permanently hardcoded:** `hold_threshold`, SELL's regime tiers (SELL has
none at all — BUY does), earnings/news/RS/weekly compression maps, `max_compress_ratio`,
`min_pillars_for_buy`, `ml_weight_floor`, the `hold_days` windows themselves, and every regime
tier is applied as a flat delta off the bull baseline (`_get_dynamic_buy_threshold()`) — the
calibration mechanisms themselves are regime-agnostic.

### Data volume — a real constraint

Per `docs/SELF_IMPROVEMENT_LOOP.md` (2026-07-06 snapshot), resolved `is_correct_10d` outcome
rows: SHORT ≈120, SWING ≈115, LONG ≈77, GROWTH ≈94. **LONG and GROWTH fall below
`outcomes/calibrate/apply`'s 100-sample floor and are silently skipped every single week** —
this has presumably been true continuously since that snapshot; a fresher count needs a live DB
query, not available in a read-only research pass.

### Established conventions every existing sweep already follows (any new tuner must too)

Chronological 70/30 split (never random — avoids look-ahead leakage), a per-slice minimum
sample floor, candidate must beat the CURRENT LIVE baseline's own validation-slice EV (never a
fixed number — repeated tuning runs compare against the truth, not a stale target),
`EV = mean(pct_return)` (never `avg_return × win_rate` — T232-OC4, a documented double-counting
bug fixed elsewhere), unconditional rejection of negative EV lift (a real past incident applied
a worse SELL:GROWTH threshold before this gate existed), one `TuneHistory` row per attempt via
`_record_tune_history()` regardless of outcome (promoted or rejected), and Redis reads always
clamp to sane bounds in case of a corrupted/stale cached value.

### Design-doc delta

`docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` (248 lines) plus the living
`docs/SELF_IMPROVEMENT_LOOP.md` — Phases 1–3 are done (walk-forward everywhere, the
`min_entry_score` gate harness, the promotion gate + `tune_history` table). NOT done: Phase 2b
(equity-curve replay for `min_kscore`/`min_ta_score`/`min_volume_z` — needed to test LOOSENING a
parameter, not just tightening), Phase 2c (decision-engine path), Phase 4 (ML-hyperparameter
P&L gate, position sizing), Phase 5 (scheduling the harness itself). **The key gap vs. "find the
best strategy per horizon": every existing mechanism tunes ONE parameter at a time in
isolation — there is no joint per-horizon sweep, no hold_days tuning, and `calibrate_ml_weight` +
the gate harness aren't scheduled or fully `TuneHistory`-integrated.**

### Phased plan (not yet built)

**Phase 1 (one session)** — `POST /signals/tune_strategy` in signal-engine `routes.py`: per
horizon, a joint grid sweep over **(buy_threshold × ml_weight_cap)** — the two highest-leverage
parameters, both re-derivable from already-stored `SignalOutcome.fused_prob` +
`Signal.reasons["ml_weight"]` with NO signal regeneration needed (a real speed advantage — this
is pure re-filtering of history that already happened, not a re-simulation). Keep the grid small
(~31×13 candidates) to limit multiple-comparison overfit risk against an n≈100-120 sample
baseline; require min_samples=15 per slice, validation-beats-current-live-baseline, unconditional
negative-lift rejection — all matching the conventions above exactly. Apply through the EXISTING
Redis keys (`stockai:signal_thresholds:{H}`, `stockai:style_tune:{H}:ml_weight_cap`) so the READ
side (`_decide_style()`, `_get_style_tuned_param()`) needs zero changes. One `TuneHistory` row
per horizon per run. Companion `GET /signals/strategy_status` reporting live-vs-hardcoded values
per horizon side by side. LONG/GROWTH will skip until enough data accumulates — surface that
explicitly in the response rather than silently.

**Phase 2** — sweep `hold_days` per horizon using the ALREADY-POPULATED `return_5d/10d/20d`
columns as three candidate exit windows (vs. today's single hardcoded `_OUTCOME_HOLD_DAYS`
value) — same no-regeneration speed advantage as Phase 1.

**Phase 3** — once a few manual cycles look sane, add to the Sunday scheduler (replacing/
augmenting the existing calibrate/apply + tune_style_profiles steps), and fold in
`calibrate_ml_weight` (currently manual-only) into the same run.

**Phase 4 (honest limitation, not silently glossed over)**: stored-outcome sweeps can only ever
evaluate TIGHTENING an existing parameter (re-filtering signals that already fired under the
CURRENT threshold) — simulating a LOOSER threshold or a different compression-map value would
require actually regenerating signals against historical price data, which is exactly what the
design doc's own deferred Phase 2b (equity-curve replay) is for. Phase 1-3 above are real,
buildable, and valuable, but they are fundamentally a re-filtering exercise, not a full backtest.

**Key files for implementation**: `services/signal-engine/src/api/routes.py` (existing sweep
functions at :3614, :4031, :4302, :4958 — the new tuner should sit alongside these, following
their exact structure), `generators/signals.py:1278-1577` (`_STYLE_PROFILES`, the read side),
`docs/SELF_IMPROVEMENT_LOOP.md`, `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md`,
`services/market-data/src/backtest/gate_harness.py` (the Phase 2b equity-replay precedent).

---


## Feature Reference: T255-STRATEGY-TUNER-PER-HORIZON — Joint Buy-Threshold x ML-Weight-Cap Tuner (Phase 1, Built 2026-07-18)

**Gap this closes**: every self-tuning mechanism in signal-engine (`calibrate_ta_weights`,
`calibrate_ml_weight`, `outcomes_calibrate_apply`, `tune_style_profiles`) tunes exactly ONE
parameter at a time, against its own independent train/validation split. None had ever
searched for the best COMBINATION of `buy_threshold` + `ml_weight_cap` together — a real gap,
since a candidate that looks best for `buy_threshold` alone need not be the best pairing once
`ml_weight_cap` also shifts (a lower cap changes which outcomes even clear a given threshold,
because it changes the effective `fused_prob` population being swept).

**New endpoint**: `POST /signals/tune_strategy` in
`services/signal-engine/src/api/routes.py`, placed right after `tune_style_profiles`. For each
of SHORT/SWING/LONG/GROWTH: joins already-stored `SignalOutcome.fused_prob` to
`Signal.reasons["ml_weight"]` (same join pattern `tune_style_profiles` already uses), then
grid-searches 31 `buy_threshold` levels (0.55-0.85) x 13 `ml_weight_cap` levels (0.15-0.75) —
403 cells — on the chronological OLDER 70% of the joined rows (train), and only applies the
winning cell if it ALSO beats the CURRENT LIVE baseline's own EV on the NEWER 30% (validation)
that the search never saw. This is a **re-filtering exercise, not a re-simulation** — a grid
cell's `fused_prob` still reflects whatever `ml_weight` was ACTUALLY used when the signal was
originally generated, not a replay of what it would have been under a different cap. This
means the sweep can only ever evaluate TIGHTENING an existing threshold/cap combination, never
a looser one — the same explicit limitation the design doc's own deferred Phase 2b
(equity-curve replay) exists to eventually address.

**Reuses every existing convention exactly**, so this new mechanism can't silently violate a
safety property its siblings already enforce:
- Chronological 70/30 split (never random — avoids look-ahead leakage).
- `min_samples=15` per grid cell per slice (looser than `outcomes_calibrate_apply`'s 50,
  deliberately — a 403-cell 2D grid already spreads a smaller outcome pool thin; the
  validation-beats-baseline gate below, not this floor, is what actually protects against a
  noisy cell being promoted).
- Unconditional rejection of negative EV lift, regardless of how large the grid shift looks.
- `EV = mean(pct_return)` (never `avg_return × win_rate` — the T232-OC4 double-counting fix
  documented elsewhere in this file).
- One `TuneHistory` row per horizon per run via `_record_tune_history()`, regardless of
  promoted-or-skipped outcome (`parameter_class="joint_strategy"`,
  `parameter_name="buy_threshold+ml_weight_cap"` — a new value in that column, but it's a plain
  `String(32)`, not an enum, so no schema/migration was needed).
- Sane-bounds clamp on both dimensions before ever writing to Redis.

**Applies through the EXISTING Redis keys** — `stockai:signal_thresholds:{H}` (same key
`outcomes_calibrate_apply` already writes, read via `_get_dynamic_buy_threshold()` as a
bull-baseline-relative delta applied per-regime) and `stockai:style_tune:{H}:ml_weight_cap`
(same key `tune_style_profiles` already writes, read via `_get_style_tuned_param()` as a flat
value). **Zero changes needed anywhere on the read side** — `_decide_style()`, the signal
generator, and the existing `GET /signals/tune_status` status-reporting endpoint all already
handle these keys. Checked whether a new companion status endpoint was warranted (per the
original design doc's Phase 1 sketch, `GET /signals/strategy_status`) and did NOT build one —
`tune_status` already reports `effective`/`redis_overrides` for both `buy_threshold` and
`ml_weight_cap` per horizon, so a dedicated new endpoint would have been pure duplication.

**Tests**: `services/signal-engine/tests/test_tune_strategy.py`, 9 cases, using the
exec()-from-source extraction technique already established for functions in `routes.py` this
environment can't import directly (`conftest.py` stubs `common`/`db` wholesale) — run against a
REAL in-memory SQLite session and the REAL `shared/db/models.py`, with only `_get_redis`/
`_record_tune_history` stubbed, so these tests exercise the actual grid-search/gating logic,
not a hand-copied reimplementation that could silently drift from it.

Adversarially verified twice during implementation: (1) disabled the negative-EV-lift
rejection gate (`if ev_lift < 0:` → `if False:`) and confirmed the validation-slice-loser test
caught a wrongly-promoted candidate (`ev_lift_pct: -7.0` still applied) before reverting;
(2) disabled the min-sample-floor gate and confirmed 4 tests failed with a real `IndexError`
(an empty train/validation split crashing on `train_wr[0][0].signal_date`) before reverting.

**A real test-design trap hit while building the "genuinely better combination" fixture**: an
initial dataset alternated `fused_prob`/`ml_weight` so cleanly that BOTH the candidate's
tighter cap and the baseline's wider cap selected the IDENTICAL subset via their respective
`cap + 0.05` tolerance windows — `ev_lift_pct` came out to exactly `0.0` every time, not
because the code was wrong but because the fixture never actually exercised a cap-driven
distinction (only a threshold-driven one, which both cells shared identically). Fixed by
deliberately placing the losing rows' `ml_weight` WITHIN the baseline's tolerance window but
OUTSIDE the candidate's — only then did the sweep have a real cap-driven signal to find.
**Lesson for any future 2D-grid test fixture in this codebase**: check that each axis of the
grid actually produces a DIFFERENT selected subset between the candidate and the baseline —
two axes that happen to collapse onto the same filtered rows will always show zero lift
regardless of whether the underlying logic is correct.

**A real bug caught by triggering this live against real production data (not just tests) on
first deploy**: the initial version only had the hard `ev_lift < 0` rejection — no soft
min-lift floor like `outcomes_calibrate_apply`'s own `min_ev_lift` + shift-size convention.
Running it live against 2,782 real outcomes immediately surfaced SHORT applying a real
`(0.63->0.55, 0.30->0.25)` shift with `ev_lift_pct` EXACTLY `0.0` — a tie, not an improvement.
Fixed by adding an unconditional `ev_lift <= 0` rejection independent of shift size
(deliberately STRICTER than the sibling mechanism's own shift-size escape hatch — a large
parameter shift with a genuinely-measured zero lift against 2,782 real samples means the
tested parameters don't matter for this outcome distribution, not that measurement noise is
masking a real edge worth keeping anyway), plus the sibling's own soft `min_ev_lift`
+ trivial-shift floor for small-but-positive lifts. The bad pre-fix live write was manually
cleared (`redis-cli del stockai:signal_thresholds:SHORT stockai:style_tune:SHORT:ml_weight_cap`)
before the fixed code was deployed; SWING's write from that same initial run (a genuine
`ev_lift_pct=1.57` improvement) was left in place, confirmed against the corrected gate logic.
**Lesson reinforced**: live-verifying a new self-tuning mechanism against real production data
immediately, rather than trusting a synthetic test suite alone, caught a real gate gap within
minutes of first deploy — the same "verify against live state" discipline documented elsewhere
in this file, applied to a brand-new mechanism's very first run instead of an existing one.

**What to check if this looks wrong**:
```bash
# Confirm the endpoint exists and run it manually (needs a valid JWT — see any other
# _service_token()-style example elsewhere in this file for the pattern):
docker exec stockai-signal-engine-1 curl -s -X POST 'http://localhost:8005/signals/tune_strategy?days=180' \
  -H "Authorization: Bearer <token>" | head -c 500

# Confirm a promoted change is visible via the EXISTING status endpoint (no new endpoint to check):
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/signals/tune_status' \
  -H "Authorization: Bearer <token>"

# Check TuneHistory rows this mechanism wrote:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT style, old_value, new_value, promoted, gate_failures FROM tune_history WHERE parameter_class='joint_strategy' ORDER BY ts DESC LIMIT 10;"
```

**Not yet built (Phases 2 and 4, documented not silently dropped)**: Phase 2 — sweep
`hold_days` per horizon using the already-populated `return_5d/10d/20d` columns (same no-
regeneration speed advantage). Phase 4 — the explicit limitation that any stored-outcome sweep
(this one included) can only evaluate TIGHTENING an existing parameter; testing a genuinely
LOOSER threshold or a different compression map needs the design doc's own deferred Phase 2b
equity-curve replay, a separate and larger project.

**Phase 3 (scheduling) done 2026-07-28** — `tune_strategy` had been manual-HTTP-only since it
shipped 2026-07-18, despite its own "Phase 3: schedule this weekly" note above; every sibling
calibration mechanism (`calibrate_ta_weights`, `calibrate_conviction_weights`,
`outcomes/calibrate/apply`, `tune_style_profiles`, `calibrate_ml_weight`) was already wired into
`_weekly_full_refresh()` (`services/market-data/src/services/scheduler.py`, Sunday 14:00 PT),
just this one was overlooked — same gap class as `calibrate_ml_weight`'s own
`SELFIMPROVE-MISSING-SCHEDULE-REGISTRATIONS` fix (a built, already-gated mechanism with zero
cron entry, not a missing safety check). Fixed by adding one `_post()` call right after
`tune_style_profiles` (its closest sibling — both are per-style gate-parameter sweeps),
following the identical log/post/record-status pattern every other call in that function
already uses. Purely additive: `tune_strategy` applies through the SAME Redis keys
`outcomes_calibrate_apply`/`tune_style_profiles` already write
(`stockai:signal_thresholds:{H}`, `stockai:style_tune:{H}:ml_weight_cap`), so the read side
needed zero changes — this is only a missing cron registration, not new wiring.

**`calibrate_ml_weight` was NOT folded into `tune_strategy` this pass** — it's already
independently scheduled (added by its own earlier fix), and `tune_strategy` sweeps
`ml_weight_cap` jointly with `buy_threshold` per horizon, a materially different scope than
`calibrate_ml_weight`'s own single global fusion-weight sweep; folding one into the other would
be a real behavioral merge, not a scheduling fix, and was correctly left out of this narrow
scope.

**Tests**: `services/market-data/tests/test_tune_strategy_scheduling.py` (5 cases) — source-
text regression checks (matching `test_scheduler_static_names.py`'s established pattern for
this file's Docker-only-dependency constraint): the `_post()` call and `_record_job_status()`
call both land inside `_weekly_full_refresh()` specifically (not a different function — a
copy-paste-to-the-wrong-function mistake is exactly the kind of error this checks for), the
call runs after `tune_style_profiles` (matching the comment's own stated intent), and every
pre-existing sibling calibration call is still present (guards against an accidental removal
while inserting the new one). Adversarially verified twice: removing the new call entirely
(3 of 5 tests correctly failed) and inserting an equivalent call into the WRONG function ahead
of `_weekly_full_refresh` (4 of 5 tests correctly failed, including the dedicated misplacement
guard) — both reverted after confirming the failures. Full 505-test market-data suite green;
`pyflakes` clean (confirmed via `git stash` that all 4 pre-existing warnings in this file
predate this change).

**What to check if this looks wrong**:
```bash
# Confirm the job actually fires on the next Sunday run:
docker logs stockai-market-data-1 --since 24h | grep 'tune_strategy'
# Should show scheduler.tune_strategy_start, then a downstream signal-engine
# tune.ev_gate-style log line (or the real signal-engine equivalent) confirming the POST landed.

# Manually verify the wiring is present in the deployed container:
docker exec stockai-market-data-1 grep -n "tune_strategy" /app/src/services/scheduler.py
```

---


## Feature Reference: RK-D1-SCREENER-FULL-SCAN — Screener Signal Query No Longer a Full Table Scan (Fixed 2026-07-21)

**The gap**: `screen()`'s (`services/ranking-engine/src/api/routes.py`) signal-lookup
subquery (`sig_subq`) aggregated `max(Signal.ts) GROUP BY stock_id` across the **entire**
`Signal` table (filtered only by `horizon == "SWING"`, no `stock_id` restriction at all) to
build `sig_map` — even though the main screener query (`rows`, already filtered by
market/sector/score/etc.) only ever looks up a small, bounded subset of `stock_id`s from that
map. As the `signals` table grows (200+ stocks × 4 horizons × 3 years of history), this
became an unbounded full-table aggregation on every screener request.

**Fix**: scope both the subquery and the outer signal query to
`Signal.stock_id.in_(_screen_stock_ids)`, where `_screen_stock_ids` is built from the
already-filtered `rows` result. A no-op change in behavior — `sig_map`'s contents are
identical either way, since anything outside `rows` was never actually read from it — purely
a performance fix. An `if _screen_stock_ids:` guard also skips the signal queries entirely
when the screener result is empty (no stocks matched the filters), rather than running a
pointless `Signal.stock_id.in_([])` query.

**Tests**: `services/ranking-engine/tests/test_screener_signal_scoping.py`, 4 cases —
source-text regression checks (matching `test_rank_symbol_market_scoping.py`'s established
proportionate-testing precedent, since `screen()` itself has a large, multi-branch body with
heavy DB/session dependencies disproportionate to this fix's actual scope). Confirms:
`_screen_stock_ids` is built from `rows` AFTER it's fetched, both signal queries include the
`stock_id.in_()` filter, the empty-list guard correctly skips the signal queries, and the
pre-existing `horizon == "SWING"` pin survived the change.

**Adversarial verification**: 2 sabotage cycles, both caught and reverted — removing the
`stock_id.in_()` filter from both queries, and removing the empty-list guard entirely.

Full ranking-engine suite green (24 passed + 1 pre-existing unrelated failure in
`test_kscore.py`, confirmed via git-stash earlier this session to already fail identically
before any recent changes); frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-ranking-engine-1 grep -n "_screen_stock_ids" /app/src/api/routes.py
```
Should show the variable built right after `rows = session.execute(stmt).all()`, then used in
both signal-query `.where(...)` clauses.

---


## Feature Reference: SELFIMPROVE-WATCHDOG-SELF-TUNING — Watchdog Self-Tuning Diagnostic Report (Built 2026-07-21)

**The gap**: `signal_watchdog()`'s own meta-parameters (38% win-rate floor, +0.03/-0.02 step
size, 15-sample floor, 3x max-tighten cap) were exactly as hardcoded and never-revisited as
any of the base trading parameters the watchdog exists to correct. Depended on
`SELFIMPROVE-NO-RETRO-FEEDBACK-LOOP` (`backfill_realized_ev()`) existing first, since this
report reads the `realized_ev_pct_after` column that job populates.

**Deliberately a read-only diagnostic report, not an auto-tuning job.** The tracker item's own
fix description ("compute whether tighten actions' realized win-rate improved vs. relax
actions") is analysis for a human to review — matching the existing `GET /tune_status`
precedent — not a decision rule mature enough to safely automate. Auto-tuning the tuner itself
would be a materially bigger, riskier step than the item's Phase-2 framing implies.

**Implementation**: new `GET /watchdog_self_tuning_report` in
`services/signal-engine/src/api/routes.py`. For every `promoted=True`,
`triggered_by="watchdog"` `TuneHistory` row with `realized_ev_pct_after` populated (i.e.
`backfill_realized_ev()` already computed a trustworthy retro-verdict), a new
`_watchdog_action_kind(old_value, new_value)` classifies the row as `tighten` (new threshold
> old) or `relax` (new < old) by comparing the stored `{"threshold": float}` JSON —
`TuneHistory` never recorded the action type as its own field, so it's derived. Per style,
reports:
- Mean `realized_ev_pct_after` for tighten actions vs. relax actions.
- `n_weak_tightens` — how many tighten actions still had **negative** realized EV even after
  applying the +0.03 step, the closest measurable proxy for "the step size might be too
  small." (`max_tighten_reached_manual_review_needed` itself is a no-op branch in
  `signal_watchdog()` that never writes a `TuneHistory` row, so it can't be queried back
  directly — a real, narrower scope than the fix description's literal ask, documented rather
  than silently glossed over.)

**Tests**: `services/signal-engine/tests/test_watchdog_self_tuning_report.py`, 7 cases —
source-text checks confirm the endpoint is `GET`-registered, filters to exactly the 3 required
conditions, and never calls `session.commit()`/`add()`/Redis `.setex()` anywhere in its body
(the read-only property this whole design depends on); behavioral checks against the real,
source-extracted `_watchdog_action_kind()` cover tighten/relax/unchanged classification and
malformed-input safety.

**Adversarial verification**: 2 sabotage cycles, both caught and reverted — swapping the
tighten/relax classification, and removing the `realized_ev_pct_after` filter (which would
have included not-yet-trustworthy rows).

Full signal-engine suite green modulo 2 pre-existing, unrelated failures confirmed via
git-stash to already fail identically before this change (`test_signal_generator.py`'s import
error against current `signals.py`, and 4 `test_analyst_momentum.py` failures — both
pre-existing gaps this fix did not introduce). Frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/watchdog_self_tuning_report' \
  -H "Authorization: Bearer <token>"
```
Should return `by_style` with `n_tighten_actions`/`n_relax_actions` per horizon — likely all
zero for a while, since it depends on `backfill_realized_ev()` having already found and
populated enough aged, promoted watchdog rows.

---


## Feature Reference: T233-SELFIMPROVE-PHASE2b — min_kscore/min_ta_score/min_volume_z Gate Backtest (Built 2026-07-22)

**Closes the gap** `docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md`'s §1c/§4 explicitly
deferred: Phase 2a's `gate_harness.py` only replays `_should_enter()`, parameterized by
`min_entry_score`/`min_confidence`/`min_rr_ratio`/`max_entry_gap_pct` — it deliberately does
NOT test `min_kscore`/`min_ta_score`/`min_volume_z`, since those live in `_scan_for_entries`'s
own candidate loop, upstream of `_should_enter()` entirely. The design doc's own framing was
that testing them would need "a full bar-by-bar equity-curve replay" (tracking open positions,
equity, entry caps, cooldowns evolving day-over-day) — a materially larger, riskier build than
Phase 2a, deferred as Phase 2b.

**Re-scoping finding before writing any code**: re-read the actual gate code for these THREE
SPECIFIC checks (not `_scan_for_entries` as a whole) and found the design doc's own concern too
pessimistic for them specifically. `min_kscore` (`ranking.score` vs. a threshold),
`min_ta_score` (`sig.reasons["ta_score"]` vs. a threshold), and `min_volume_z`
(`sig.reasons["volume_z"]` vs. a threshold) are each a PURE, STATELESS comparison against data
already stored per-signal/per-stock — none of them read open positions, equity, or any other
evolving portfolio state. They only happen to live in the wrong function. This meant they could
be layered onto the EXISTING per-signal `replay_should_enter()` (Phase 2a's own machinery)
without building the full equity-curve engine — a much smaller, lower-risk extension than the
design doc anticipated. The genuinely-stateful gates (drawdown, daily/weekly loss, cooldowns,
entry caps, sector/cluster caps) remain out of scope and would still need the originally-
envisioned full bar-by-bar replay if ever tackled — not attempted here, not silently claimed
as covered either.

**Data depth re-checked before committing to this scope** — the design doc's own §1a flagged
thin history (Jul 2026: US SWING ~34 days, all HK "likely skipped"). Re-queried production
directly rather than trusting the 2-week-old snapshot: US SWING is now 42 days/1,124 resolved
outcomes, and even HK now clears 24-37 days/317-471 rows across every style — the floor this
harness enforces (`MIN_SAMPLES_PER_SPLIT=15`) should now clear for nearly every style/market
combination, not just US SWING/SHORT/LONG as the original doc anticipated.

**Point-in-time correctness — the one real trap in this design, caught before shipping**:
`_scan_for_entries`'s own LIVE `min_kscore` check always joins the MOST RECENT `Ranking` row
(`func.max(Ranking.as_of)`, no date bound) — correct for live trading, where "most recent"
always means "now." A historical replay must NOT reuse that shortcut, or it would silently
look up a K-Score computed AFTER the signal date, leaking future data into a past decision.
New `_historical_kscore(session, stock_id, as_of)` instead finds the most recent `Ranking` row
with `as_of <= the signal's own date` — verified directly with a dedicated test constructing
two Ranking rows (one before, one after the signal date) and confirming only the earlier one
is ever returned.

**Implementation** (`services/market-data/src/backtest/gate_harness.py`):
- `_historical_kscore()` — point-in-time-correct Ranking lookup (above).
- `_passes_prefilter_gates(cfg, kscore, reasons)` — pure function applying all three gates
  in the SAME order and with the SAME fail-open conventions as the live `_scan_for_entries`
  code: a missing `kscore` blocks only when `require_kscore` (default `True`); `min_ta_score`
  only enforces when `> 0` (0.0 = disabled, matching the live gate's own no-op state) and a
  missing `ta_score` defaults to `1.0` (never blocks); a missing `volume_z` is fail-open
  (skips the gate entirely, per the pre-existing T232-DL5 fix) — only an explicitly-present,
  too-low value blocks.
- `replay_extended_gates()` — same per-signal replay as `replay_should_enter()`, but calls
  `_passes_prefilter_gates()` before `_should_enter()`; a candidate must clear all four gates
  (the three pre-filters plus `_should_enter()` itself) to count as entered.
- `walk_forward_extended_gate(param, candidates)` — same chronological 70/30 train/validation
  split and promotion criterion as Phase 2a's `walk_forward_min_entry_score()`, generalized to
  search any ONE of the three params while holding the other two at their base-config values.

**New endpoint**: `GET /paper-portfolio/backtest/extended-gate?style=&market=&param=&window_days=`
(admin-only, mirrors Phase 2a's `/backtest/min-entry-score` exactly). Candidate grids are
deliberately TIGHTER-only from the current value (`min_kscore`: +2/+5/+8/+12 capped at 100;
`min_ta_score`: +0.05/+0.10/+0.15 capped at 1.0; `min_volume_z`: +0.25/+0.5/+1.0) — same
explicit limitation as Phase 2a and every other stored-outcome sweep in this codebase: a
replay can only evaluate TIGHTENING an existing gate (re-filtering signals that already fired
under the CURRENT threshold), never a genuinely looser one, since that would require
regenerating signals against historical price data rather than re-filtering already-computed
ones. Stated explicitly in the endpoint's own response `note` field, not silently omitted.

**Tests**: `services/market-data/tests/test_gate_harness_extended.py` (15 cases) —
`gate_harness.py` can't be imported directly in this test environment (conftest.py stubs
`sqlalchemy` itself as a `MagicMock`), so this uses the established real-sqlalchemy-via-stub-
pop-and-restore technique (`test_correlation_preentry.py`/`test_broker_position_sync.py`) to
build a real in-memory SQLite session against the real `shared/db/models.py`, then extracts
`_historical_kscore()`/`_passes_prefilter_gates()`'s real source via `exec()`. Covers: the
point-in-time-correctness property directly (a Ranking row dated AFTER the signal must never
be returned), exact-date-match inclusion, no-ranking-found handling, all three gates'
individual pass/fail boundaries, each gate's specific fail-open convention (missing kscore
blocks by default but not when `require_kscore=False`; missing `ta_score` never blocks;
missing `volume_z` never blocks; `min_ta_score=0.0` never blocks), gate-ordering (kscore
checked first, short-circuits before ta_score/volume_z), and the all-three-clear pass case.

**A real, previously-undocumented SQLite test-harness quirk hit while writing these** (same
class already documented for `Price`/`SignalOutcome` elsewhere in this file, now confirmed for
`Ranking` too): `Ranking.id` is a `BigInteger` primary key, which doesn't get SQLite's implicit
autoincrement — test fixtures must assign `id` explicitly. Also: `Ranking.volatility` is
`NOT NULL` with no default (unlike `value`/`growth`, deliberately made nullable per an earlier
fix, `T232-RANKSTALE`) — test fixtures must supply a real value or the insert fails.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
`Ranking.as_of <= as_of` date bound from `_historical_kscore()` (caught by the point-in-time
test with a real `90.0 == 40.0` failure — confirming the fix genuinely prevents future-data
leakage, not just looking like it does); disabling the `min_volume_z` comparison in
`_passes_prefilter_gates()` (caught directly). Full 459-test market-data suite (up from 444)
green after every revert.

**Deliberately not built this pass, matching the design doc's own explicit deferral list**:
Phase 2c (decision-engine-path backtesting, blocked on `T232-DL-DUALSCORER-DEBT` resolution
per the design doc's §1d) and the genuinely-stateful gates (drawdown, daily/weekly loss,
cooldowns, entry caps, sector/cluster caps) remain untouched — those still need the originally-
envisioned full bar-by-bar equity-curve replay if ever tackled.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/extended-gate?style=SWING&market=US&param=min_kscore&window_days=60' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool
```
If a specific style/market/param combo always returns `skipped_reason`, check the actual
resolved sample count directly against production Postgres (per-style/market resolved-outcome
counts, same query used to re-verify data depth above) before assuming the endpoint itself is
broken — a genuine data-thinness skip and a real bug produce the identical response shape.

---


## Feature Reference: T233-SELFIMPROVE-PHASE4 — EV Backtest Gate for ML Hyperparameter Tuning (Built 2026-07-22)

**Re-scoping finding before writing any code**: the tracker item's own framing ("ML
hyperparameter tuning today optimizes AUC... not realized trading expected value") was
partially STALE — `T232-ML5-OPTUNA-WRONG-METRIC` (2026-07-09) already changed
`tuner.py`'s Optuna objective from raw AUC to mean top-decile precision (a much closer proxy
for what live trading actually acts on: only the extreme right tail of the probability
distribution, where `buy_threshold` sits). That half of Phase 4's original framing was already
done. What was genuinely still missing: Optuna's own `TimeSeriesSplit` CV folds are entirely
internal to the search — a winning candidate was never checked against a real, untouched
holdout using an actual trading-EV metric before being persisted and used to retrain the live
model. This session built that second, independent gate.

**Second re-scoping finding**: the tracker item's other half — "systematically tune
`sizer.py`'s hand-set confidence-multiplier tiers" — turned out to be **not worth building**.
Checked directly: `sizer.py`'s own module docstring already states it is a "preview/scoring-
only module" — confirmed via grep that `paper_trading_engine.py` (the ONLY code that places
real paper trades) never calls `compute_position()`/imports from `sizer.py` at all; it has its
own, completely independent sizing logic. decision-engine's `sizer.py` output only ever
reaches `/decide/{symbol}`'s JSON response, rendered by `decide.tsx`'s `PositionCard` for
DISPLAY ONLY. Tuning these multipliers against a validation-EV backtest — the whole premise of
promotion_gate.py's pattern — requires a real trade outcome to attribute the parameter choice
to; since nothing ever acts on sizer.py's numbers, there is no real EV signal to tune against.
Building a fake one would be worse than not building it at all. Left undone, documented here
rather than silently dropped.

**Design**: `tune_symbol()` (`services/ml-prediction/src/training/tuner.py`) already carved off
the last 15% of feature rows as `cutoff = int(len(X) * 0.85)` — but discarded it completely
(`X, y_dir = X.iloc[:cutoff], y_dir.iloc[:cutoff]`, `y_ret` wasn't even captured from
`build_features()`'s return). This is real, never-touched holdout data with real forward
returns. No new data source or regeneration needed: keep `y_ret`, keep the holdout slice
(`X_holdout`/`y_ret_holdout`), refit the CANDIDATE params on the full search slice and score
the holdout, refit the CURRENT LIVE params (`_load_best_params(symbol)`) the same way on the
SAME holdout, and only persist/retrain if the candidate's holdout EV beats the live baseline's
— matching every other tuning mechanism's "must beat the current live baseline on data neither
saw" convention (`gate_harness.py`, `outcomes_calibrate_apply`, `tune_style_profiles`).

**New module**: `services/ml-prediction/src/training/ev_gate.py` — pure numpy functions, zero
DB/network dependency:
- `compute_holdout_ev(probs, y_ret, threshold=0.60)` — mean forward return among holdout rows
  crossing a reference probability threshold (0.60, approximating where production's
  `buy_threshold` sits across styles/regimes). Returns `{"ev_pct": None, "n": n}` — not a real
  `0.0` — when fewer than `MIN_HOLDOUT_SIGNALED_ROWS=10` rows cross the threshold; an
  unmeasurable value must never be silently treated as "zero EV."
- `evaluate_candidate_ev(candidate_probs, baseline_probs, y_ret_holdout)` — the comparison
  logic. `baseline_probs=None` (first-ever tune for a symbol) auto-promotes (nothing to beat,
  matches `tune_symbol()`'s pre-existing first-tune behavior) with an explicit
  `"no_baseline_params:first_tune_for_symbol"` gate-failure marker rather than silently passing.
  An unmeasurable candidate EV rejects; an unmeasurable baseline EV (but a measurable candidate)
  promotes with an explicit marker; otherwise requires `ev_lift > 0` STRICTLY — an exact tie
  is rejected, matching `T255-STRATEGY-TUNER-PER-HORIZON`'s own established "unconditional
  rejection of non-positive EV lift" convention.

**Wiring in `tuner.py`**: new `_fit_and_predict_holdout(params, X_arr, y_arr, X_holdout_arr)`
refits using the IDENTICAL scaling/weighting convention as `objective()`'s own per-fold fit
(`_recency_weights` + `_blend_weights`), so the gate compares a model trained the same way
Optuna actually searched, not a differently-weighted one. `_record_tune_history()` writes one
`TuneHistory` row per `tune_symbol()` call regardless of outcome (`parameter_class=
"ml_hyperparams"`, `parameter_name="xgboost_params"`, market derived from the `.HK` symbol
suffix) — reuses the shared model directly (`from db import SessionLocal, TuneHistory`, no
cross-service call, matching the `T233-SELFIMPROVE-PHASE3-EXTENSION` precedent for
signal-engine), wrapped in try/except so a DB hiccup never aborts a real tuning run. A
too-small holdout (`< MIN_HOLDOUT_SIGNALED_ROWS` total rows) skips the gate entirely and falls
back to Optuna's own CV verdict, rather than crashing on the sample floor inside
`compute_holdout_ev` itself.

**A real bug caught by `pyflakes`, not by any test, before this shipped**: the EV-gate call
site added `current_params = _load_best_params(symbol)`, but `_load_best_params` was never
added to `tuner.py`'s own `from .trainer import ...` line — an undefined-name bug that would
only have surfaced as a live `NameError` in production the moment `tune_symbol()` actually ran
past Optuna's search (i.e., every single real invocation). Caught by running `pyflakes` against
the touched files before considering the change done, not by the test suite itself (which,
being source-text-extraction-based for this file, never actually imports/executes the real
module). Fixed by adding `_load_best_params` to the import line, and added a dedicated
regression test (`test_load_best_params_is_actually_imported`) so this specific class of
"undefined name only reachable at runtime" bug can't silently regress again unnoticed.

**Tests**: `services/ml-prediction/tests/test_ev_gate.py` (13 cases) — `ev_gate.py` has zero
DB/network dependency, so it's loaded via a direct file-spec import (bypassing
`src.training.__init__`, which drags in the full model registry including `lightgbm`, not
installed locally — same constraint already documented for `meta_trainer.py`'s own tests in
this directory) and tested behaviorally: EV computation only over signaled rows (poisoned an
adjacent "unsignaled" return with an extreme value and confirmed it never leaks into the
mean), the `None`-not-zero unmeasurable floor at exactly the boundary, custom threshold
respect, all `evaluate_candidate_ev` branches (no-baseline auto-promote, candidate/baseline
each independently unmeasurable, beats/loses/ties baseline).
`services/ml-prediction/tests/test_tuner_ev_gate_wiring.py` (11 cases, source-text regression
checks — `tuner.py` can't be imported directly without real `optuna`/`xgboost`/DB access)
confirm: the holdout slice is captured before search-slice truncation (not after, which would
silently produce garbage), the gate runs and `TuneHistory` is recorded before either exit
branch, a rejected candidate actually `return`s rather than falling through to persist/retrain,
both refits use the identical weighting convention as Optuna's own fit, and the
`_load_best_params` import guard described above.

**Adversarial verification** — 4 sabotage cycles across both test files, all caught and
reverted: disabling the `ev_lift <= 0` rejection (caught by 2 tests: loses-to-baseline and
exact-tie); disabling the `MIN_HOLDOUT_SIGNALED_ROWS` floor inside `compute_holdout_ev` (caught
by 3 tests, and surfaced a real `RuntimeWarning: Mean of empty slice` — confirming the floor
prevents a genuine NaN-producing edge case, not just a defensive nicety); reverting the
`y_ret` capture back to the pre-fix `X, y_dir, _ = build_features(...)` discard (caught by the
holdout-slice regression test); removing `_load_best_params` from the import line (caught by
its own dedicated regression test, described above).

Full 43-test `ml-prediction` suite (up from 19) green after every revert; `pyflakes` clean on
all touched files.

**What to check if this looks wrong**:
```bash
docker logs stockai-ml-prediction-1 --since 24h | grep 'tune.ev_gate\|tune.rejected_by_ev_gate'
# tune.ev_gate logs every attempt's candidate/baseline EV and promoted verdict.
# tune.rejected_by_ev_gate logs specifically when a candidate was found but didn't beat baseline.

# Check tune_history rows this mechanism wrote:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT style, market, old_value, new_value, validation_ev_pct, baseline_validation_ev_pct, promoted, gate_failures FROM tune_history WHERE parameter_class='ml_hyperparams' ORDER BY ts DESC LIMIT 10;"

# Manually trigger a single-symbol tune to see the gate live (writes real params/retrains a
# real model as a side effect — same caveat as every other tune_symbol()/tune_all() trigger):
docker exec stockai-ml-prediction-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.training.tuner import tune_symbol
print(tune_symbol('AAPL', n_trials=20))
"
```
If a candidate never seems to get promoted despite what looks like a real improvement, check
`gate_failures` in the logged/recorded result first — `no_baseline_params` and
`baseline_ev_unmeasurable` both promote automatically and are NOT failures; only
`ev_lift_not_positive` and `candidate_ev_unmeasurable` actually block promotion.

**Not built this pass, documented not silently dropped**: sizer.py multiplier tuning (see the
re-scoping finding above — genuinely not worth building given sizer.py's preview-only status).
Phase 5 (scheduling the whole Phase 1-4 pipeline automatically) remains explicitly deferred
per the original design's own sequencing — this EV gate runs inline inside every
`tune_symbol()` call (manual `/ml/tune` or the existing weekly `/ml/tune_all` cadence), it is
not yet a SEPARATE scheduled job of its own, matching Phase 4's own scope boundary.

---


## Closed: T233-SELFIMPROVE-PHASE2's Trust-Building Cross-Check (2026-07-31)

**What this was**: `docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md`'s own §3 ("Trust-
building step") described a check that was never actually run: before trusting Phase 2a/2b's
`gate_harness.py` numbers for any real decision, verify its reported `win_rate`/`avg_return_pct`
against an independent computation over the same underlying data. This had sat as an open item
across the harness's entire build history (Phase 2a, 2b, and the wall-clock bug fix all
shipped without it) — closed this session on the user's request to "self improve" the codebase.

**Corrected the literal comparison target before running anything**: the design doc's prose
named `GET /signals/outcomes/calibrate` as the comparison, but that endpoint sweeps
`fused_prob >= buy_threshold` — a fundamentally different gate from `min_entry_score`
(`_should_enter()`'s own composite score threshold). The two can never produce identical
`n`/`win_rate` numbers by construction, since they filter to different subsets of the same
signals. The REAL trust-building question is narrower and more useful: **does the harness's
own internal win_rate/EV arithmetic match an independent, from-scratch computation over the
exact same rows it says it entered** — i.e., is the harness's *code* correct, not whether two
different gates agree on a number they were never going to agree on.

**Method**: called `replay_should_enter()` directly (not through the HTTP endpoint, to get the
full `entered_signal_ids` list) at the REAL current production config for each of the 4
styles, over a real 60-day US window. For each style, took the harness's own reported
`entered_signal_ids` and ran a **completely separate, fresh SQL query** against
`SignalOutcome` to independently recompute `win_rate`/`avg_return_pct` from raw
`is_correct_{bucket}`/`return_{bucket}` columns, bypassing every line of the harness's own
computation logic.

**First pass surfaced a real near-miss, self-caught**: an initial independent check used the
PRIMARY `is_correct`/`pct_return` columns and found a real, material disagreement (SWING:
harness reported `win_rate=0.524, avg_return_pct=-0.1613`; my own from-scratch SQL query said
`0.4891` / `-1.0122` — a 6x difference on the return figure). Investigated before concluding
the harness was broken, and found the harness deliberately reads `return_{bucket}`/
`is_correct_{bucket}` (SWING → the `10d` bucket, per `_HORIZON_BUCKET` in `gate_harness.py`) —
a real, documented, INTENTIONAL design choice (each style's forward-return window
approximates its actual trading horizon), not the primary hold-to-exit columns I'd used.
Re-ran the independent check against the CORRECT columns and got an **exact match**.

**Result across all 4 styles, US, 60-day window, real current production config**:

| Style | n_entered | Harness (win_rate, avg_return_pct) | Independent SQL (win_rate, avg_return_pct) | Match |
|---|---|---|---|---|
| SHORT | 12 | `None, None` — correctly below the `MIN_SAMPLES_PER_SPLIT=15` floor | `0.1667, -0.211` (computable, but the harness is RIGHT not to report it) | ✅ (floor working as designed) |
| SWING | 229 | `0.524, -0.1613` | `0.524, -0.1613` | ✅ exact |
| LONG | 462 | `0.3874, -4.857` | `0.3874, -4.857` | ✅ exact |
| GROWTH | 927 | `0.4164, -1.8028` | `0.4164, -1.8028` | ✅ exact |

**Conclusion: the harness's core arithmetic is trustworthy.** 3 of 4 styles matched
byte-for-byte against a from-scratch, independently-written SQL computation; the 4th (SHORT)
correctly declined to report a number rather than fabricate one from too few samples — exactly
the documented, intended behavior of the `MIN_SAMPLES_PER_SPLIT` guard, not a discrepancy.

**Also ran the originally-named comparison** (`GET /signals/outcomes/calibrate?days=60` for
SWING) as a secondary sanity check, even though it can't match numerically by construction:
both endpoints independently reported **negative expected value** for SWING BUY signals in
this window (harness: -0.16% to -4.86% across styles; calibrate: -2.84% at the current
`buy_threshold=0.72`) — directionally consistent, not contradictory, which is the real bar this
secondary check was ever capable of clearing.

**This closes the last open item on `T233-SELFIMPROVE-PHASE2`** besides Phase 2c
(decision-engine path), which remains explicitly, correctly blocked on `T232-DL-DUALSCORER-DEBT`
resolving first (still `todo`) — not attempted this session, per the design doc's own §4.

**What to check if this needs re-running (e.g., after any future change to `gate_harness.py`'s
computation logic)**:
```bash
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from datetime import date, timedelta
from db import SessionLocal, SignalOutcome
from src.backtest.gate_harness import replay_should_enter, _HORIZON_BUCKET
from src.services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES
from sqlalchemy import select

window_end = date.today()
window_start = window_end - timedelta(days=60)
for style in ('SHORT', 'SWING', 'LONG', 'GROWTH'):
    base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
    bucket = _HORIZON_BUCKET[style]
    with SessionLocal() as s:
        r = replay_should_enter(s, style, 'US', base_cfg, window_start, window_end, cfg_label=f'{style}-check')
        if r.n_entered == 0:
            print(f'{style}: n_entered=0'); continue
        rows = s.execute(
            select(getattr(SignalOutcome, f'is_correct_{bucket}'), getattr(SignalOutcome, f'return_{bucket}'))
            .where(SignalOutcome.signal_id.in_(r.entered_signal_ids))
        ).all()
        scoreable = [(c, ret) for c, ret in rows if ret is not None]
        wins = sum(1 for c, ret in scoreable if c)
        ind_wr = round(wins / len(scoreable), 4) if scoreable else None
        ind_ev = round(sum(ret for _, ret in scoreable) / len(scoreable) * 100, 4) if scoreable else None
        print(f'{style}: harness(wr={r.win_rate}, ev={r.avg_return_pct}) independent(wr={ind_wr}, ev={ind_ev})')
"
```
The two must match exactly (or the harness's side must correctly show `None`/`skipped_reason`
below the sample floor) — any OTHER disagreement means a real bug was introduced in either the
harness's own computation or its column/bucket selection since this check last ran.

---


## Full Signal-Testing-Framework Review (2026-07-31) — 4 Critical Fixes

**User ask, verbatim**: "Review the signal testing framework and make sure it's the correct
way of testing the signal accuracy and performance so that we can use it truthfully for
testing the win rates and returns and tuning. AI Signal is the core and the main feature of
this platform." A comprehensive audit, not a single-bug hunt — triggered by, and building on,
the trust-building cross-check documented immediately above.

**Process**: 3 parallel deep-dive reviews (each independently reading the actual code, not
assuming correctness), one per layer of the testing stack: (1) `evaluate_signal_outcomes()`
and every win-rate/EV computation site in `services/signal-engine/src/api/outcomes.py`; (2)
every self-tuning calibration mechanism in `services/signal-engine/src/api/calibration.py`;
(3) `services/market-data/src/backtest/gate_harness.py` + `promotion_gate.py`. Findings were
ranked by REAL-WORLD IMPACT (does this actually corrupt what gets promoted to live trading, or
is it a cosmetic reporting inconsistency nobody acts on) before deciding what to fix. 4 findings
were judged CRITICAL and fixed this session; several HIGH findings were documented but
deliberately deferred (see below).

### Fixed — CRITICAL

**1. `gate_harness.py`'s validation slice was structurally empty at the default window
(BUG233-BACKTESTHARNESS-EMPTYVALIDATION).** All 3 walk-forward functions (`walk_forward_
min_entry_score`, `walk_forward_extended_gate`, and `promotion_gate.py`'s own two independent
re-derivations of the same split) computed the 70/30 train/validation split directly off the
raw `window_end` (usually `date.today()`), with no account for the fact that a `SignalOutcome`
row can't have a resolved `return_{bucket}`/`is_correct_{bucket}` value until enough calendar
days have passed since its own `signal_date` — 7 days for SHORT, 14 for SWING/GROWTH, 20 for
LONG (the same calendar-day cutoffs `paper_trading_engine.py`'s AUD19-DB3 bucket-assignment
logic already uses). At the harness's own documented 60-day default window, this meant the
newest ~30% of the window (the validation slice) contained ZERO resolvable outcomes for 3 of 4
styles — not a rare edge case, the DEFAULT configuration for most of this mechanism's life.
Confirmed live before the fix: `GET /paper-portfolio/backtest/min-entry-score?style=SWING&
window_days=60` returned `n_signals_seen: 0` on the validation slice. This silently defeated
the ENTIRE held-out-validation defense against train-slice overfitting — a candidate could
"win" on the train slice and then trivially "beat" an unmeasurable/empty baseline, or the
mechanism would just always report `skipped_reason` (failing safe, but for the wrong reason —
no data, not a real protective check).

**Fix**: new `_HORIZON_RESOLUTION_LAG_DAYS` map (SHORT=7, SWING=14, LONG=20, GROWTH=14 —
duplicated from signal-engine's own `_OUTCOME_HOLD_DAYS` rather than a cross-service import,
matching this module's own stated reason for living in market-data at all) and a new
`_resolvable_window_end(window_end, style)` helper that pulls `window_end` back by that lag
BEFORE any split happens. All 3 walk-forward functions, plus `promotion_gate.py`'s two
independent re-derivations of the SAME split (`evaluate_and_record`'s worst-trade-check
recompute, and `_write_history`'s own third re-derivation for the persisted TuneHistory row),
now call this helper first — closing all 3 places this exact split math was duplicated, not
just the one the bug was originally found in.

**2. `gate_harness.py`'s promotion criterion was a coin flip under the null hypothesis
(BUG233-BACKTESTHARNESS-COINFLIP).** All 3 walk-forward functions promoted a candidate on a
bare `best_val.avg_return_pct > baseline_val.avg_return_pct` — no minimum lift, no confidence
margin, no correction for the train-slice grid search's own multiple-comparisons exposure.
Simulated directly (best-of-k selection on a train slice, independent validation check, both
slices drawn from the SAME distribution — i.e., no real edge at all): **~50% false-promotion
rate at every sample size tested, n=15 through n=50** — comparing two noisy sample means with
no margin is statistically indistinguishable from noise at any realistic n. Real production
per-trade return SD across all 4 styles is ~9.6-10.6pp (10-day returns); at n=15 that's a
±5.2pp 95% CI on the mean — the harness cannot detect a real edge smaller than its own
measurement error. Fixing Finding 1 without this would have converted a currently-inert
mechanism (mostly `skipped_reason` due to empty validation) into an actively noise-promoting
one the moment real validation data started flowing through.

**Fix**: new `_passes_promotion_margin(best_val, baseline_val)` replacing the bare `>`
comparison everywhere it was used — requires BOTH a minimum absolute EV-lift margin
(`_MIN_PROMOTION_EV_LIFT_PCT = 0.5` percentage points) AND that the lift be at least half
(`_MIN_PROMOTION_LIFT_SD_RATIO = 0.5`) of the combined validation-slice return dispersion (a
crude but real signal-vs-noise check, not a formal significance test — `BacktestResult`
doesn't carry per-candidate SDs separately at every call site, but this is strictly stronger
than no margin at all). Every `note` field returned alongside a `promoted` verdict now states
this margin explicitly, and explicitly states it does NOT correct for the grid search's own
multiple-comparisons exposure (a real, distinct, still-open issue — see below).

**3. `calibrate_ta_weights()` (signal-engine) had zero out-of-sample validation before writing
production TA weights (BUG233-TAWEIGHTS-NOVALIDATION).** Two compounding defects: (a) rows
were fed to `TimeSeriesSplit` in arbitrary DB-heap order — `TimeSeriesSplit` assumes
chronologically-ordered input, so the reported "walk-forward" `in_sample_accuracy` was never a
real walk-forward number, just a cross-validation score computed over what amounted to a
random shuffle; (b) the fitted 16-feature logistic-regression weights were written to
`ta_weights.json`/Redis/the live in-process global UNCONDITIONALLY — fit on the FULL sample
with no held-out check against the CURRENT live weights' own accuracy, no baseline comparison,
no promotion gate, and no `TuneHistory` record at all. This was the only mutation path in the
entire calibration file with zero audit trail and zero safety net — a badly-overfit fit at
n=30-50 could go live with nothing to catch it, unlike every sibling mechanism
(`calibrate_ml_weight`, `outcomes_calibrate_apply`, `tune_style_profiles`, `tune_strategy`,
`tune_sell_pillars`) which all already enforce chronological-split + validation-beats-baseline
+ `TuneHistory` recording.

**Fix**: the DB query now `ORDER BY SignalOutcome.signal_date` (chronological, load-bearing —
`TimeSeriesSplit`'s CV folds are only meaningful on already-ordered input, and the new 70/30
split below depends on it too). A genuine 70/30 chronological train/validation split is now
enforced (`MIN_VAL_SAMPLES = 15`, matching `calibrate_ml_weight`'s own established floor); the
model fits on the TRAIN slice ONLY. A new `_weighted_score_accuracy_and_ev()` helper scores
both the fitted candidate weights AND the CURRENT live weights (read from the real in-process
`_ta_weights` global, not a hardcoded literal — `_current_live_ta_weights`) against the SAME
held-out validation rows (median-score threshold, chosen since this needs only a
weight-scale-agnostic way to compare two vectors' relative accuracy/EV, not to reproduce
`_ta_score()`'s full production blending logic). Weights are only written to disk/Redis/the
live process if the candidate's validation-slice EV beats the live weights' own EV on that
same held-out slice — otherwise the function returns `applied: false` and the live weights are
left completely untouched. Every attempt (promoted or not) now writes a `TuneHistory` row via
`_record_tune_history()`, `parameter_class="ta_weights"`, matching every sibling mechanism's
own convention — `old_value` records the REAL current live weights (not a fixed default), so a
future weights change shows up as a genuine delta in the audit trail.

**4. `_retro_ev_for()` (signal-engine) mixed BUY and SELL `pct_return` with no sign correction
(BUG233-RETROEV-SIGNMIX).** This is the app's ONLY retrospective "did a promoted tuning change
actually help" ground truth — feeding `backfill_realized_ev()`, which populates
`TuneHistory.realized_ev_pct_after`, read by the read-only `GET /watchdog_self_tuning_report`
diagnostic. The function's own docstring already documented that it deliberately pools BOTH
directions' outcomes together (since a `tune_history` row's `style` has no BUY/SELL split of
its own) — but SELL "wins" on a NEGATIVE `pct_return` (`is_correct = ret < -hurdle` for SELL,
per `evaluate_signal_outcomes`), so averaging a SELL row's raw `pct_return` alongside a BUY
row's raw `pct_return` mixes two OPPOSITE sign conventions into one meaningless number. Every
sibling SELL-aware EV computation in this codebase (`outcomes_calibrate_apply`,
`tune_sell_pillars`) already negates `pct_return` for SELL rows before averaging — this was the
one site that hadn't. Live-verified against production before fixing: the un-negated aggregate
inverted sign on 6 of 8 real style/market slices tested (e.g. aggregate across all 4 styles:
harness-style raw average **-3.23%** mixed vs. **+0.34%** sign-corrected — a change from "this
tuning history looks like a net loss" to "it's actually been a net gain").

**Fix**: `signed_returns = [(-o.pct_return if o.signal_direction == "SELL" else o.pct_return)
for o in rows]`, then `ev_pct = (sum(signed_returns) / len(rows)) * 100` — a one-line,
surgical fix matching the sibling functions' own established convention exactly. `win_rate`
was already correct (it reads `is_correct` directly, which `evaluate_signal_outcomes` already
computes with the correct per-direction sign) — only `ev_pct` needed the fix.

### Documented, deliberately deferred (HIGH severity, real, but not fixed this pass)

- **`calibrate_ml_weight()` validates against a fixed neutral `0.5`, not the actual current
  live cap** (`prev_cap` is already in scope and used for `TuneHistory.old_value`, but never
  used as the baseline comparison itself) — a real ratchet risk: if the live cap is already,
  say, 0.70, a candidate of 0.65 that beats a neutral 0.5 but is WORSE than the live 0.70 can
  still promote, repeatedly walking the cap in a bad direction with no requirement to ever beat
  where it actually is.
- **`tune_style_profiles()`'s `ml_weight_cap` baseline is a nested-subset comparison** — the
  candidate's own filtered subset is compared against the full, unfiltered validation slice
  (a strict superset), which is close to structurally rigged to always look like an
  improvement (excluding any below-average tail row from just one side of the comparison).
  `tune_strategy()`'s own sibling grid sweep already shows the correct pattern (compare against
  the CURRENT LIVE cap's own filtered subset) to copy.
- **decision-engine's harness only ever replays the FALLBACK gate** (`_should_enter()`) — per
  `T232-DL-DUALSCORER-DEBT`, `decision_engine_mode="primary"` is the live default, so
  `min_entry_score` (the one parameter this whole Phase 2a/2b harness tunes) only actually
  governs live entries during a decision-engine OUTAGE. Neither the harness's own docstring nor
  its `note` field currently tells a human reading `promoted: true` that they're tuning the
  outage path, not the live one.
- **The harness's replayed inputs systematically diverge from what live scoring sees** — `live_
  regime` is always `None` (can't be reconstructed historically without a stored regime
  time-series), and `replay_should_enter()` (unlike `replay_extended_gates()`) never threads
  `confidence_delta`/correlation either — compressing the replayed score distribution toward
  zero relative to live by up to several points on a threshold whose candidate grid spans a
  similarly narrow range. A `min_entry_score` tuned on this compressed distribution may not
  transfer cleanly to the live one.
- **`gate_backtest()` (signal-engine) uses same-day-close entry** — reintroducing the exact
  SE-F2 look-ahead bias the rest of this file already fixed everywhere else. Currently
  confirmed dead code (no caller anywhere in the codebase, and its own docstring already
  concedes there's nothing left to decide) — zero live impact today, but a landmine if anyone
  ever wires it up; the docstring's own "no look-ahead" claim is also false and should be
  corrected or the endpoint removed outright in a future pass.

None of these four were judged critical enough to bundle into this same session (each needs
either a design decision — e.g. how to correct for decision-engine-outage-only scope — or is a
lower-probability, already-fully-mitigated-by-other-gates risk), but are recorded here so a
future pass doesn't have to re-derive them.

### Categories independently confirmed CLEAN by this review (worth trusting, not just
### absence-of-a-finding)

- **Point-in-time correctness in the primary outcome writer** (`evaluate_signal_outcomes()`) —
  T+1 entry, correct bulk-price-query bounds, an unclosed hold window is never scored. Clean.
- **Multi-window (`return_5d/10d/20d`) T+1 correctness** — anchors on the already-T+1
  `entry_date`, never re-derives from `signal_date`. Clean.
- **Win/loss hurdle consistency** — the premise that `signal_accuracy()`/`rolling_accuracy()`
  still use a bare `> 0` (no cost hurdle) is OUTDATED; both were already fixed under AUD232-047
  and correctly apply `_OUTCOME_WIN_HURDLE_PCT`. The remaining bare-`>0` sites
  (`walkforward_backtest`, `gate_backtest`, `filter_audit`) are all BUY-only, so no sign error
  arises — only a minor overstatement of win rate for sub-hurdle moves, all display-only.
- **SELL sign correctness everywhere EXCEPT `_retro_ev_for()`** — every real tuning sweep in
  `calibration.py` is direction-scoped or correctly negates; `_retro_ev_for()` (fixed above) was
  the only mixed-direction EV aggregate in the codebase.
- **Censoring correctness** — a `skip_reason="no_exit_price"` row (`is_correct=NULL`) is
  genuinely excluded everywhere a win-rate/EV is computed (every site filters
  `is_correct.is_not(None)`); the `delisted_loss` classification correctly counts toward
  win-rate denominators while being excluded from every EV mean (guarded by `pct_return is not
  None`, since `delisted_loss` rows leave `pct_return` NULL).
- **`_historical_kscore()`/`_historical_atr()`/`_entry_as_of()`** (all previously fixed this
  same tracker item, `T233-SELFIMPROVE-PHASE2b`) — re-confirmed correct: no residual
  `datetime.now()` in the replayed decision path, no future-dated Ranking/Price leak.
- **The harness replays the REAL, unmodified `_should_enter()`** — imported directly from
  `paper_trading_engine.py`, not a parallel reimplementation that could drift. (The three
  pre-filter gates in `_passes_prefilter_gates()` ARE a reimplementation of `_scan_for_entries`'
  own logic and could drift from it — documented as such in that function's own docstring, not
  a new finding.)
- **Tightening-only limitation is real and consistently documented** — every stored-outcome
  sweep in this codebase (including the ones fixed this session) can only ever evaluate
  TIGHTENING an existing threshold, never a genuinely looser one, since that would require
  regenerating signals against historical price data rather than re-filtering already-computed
  ones. Now stated explicitly in every walk-forward function's own `note` field (this fix
  extended that disclosure into `walk_forward_extended_gate`, which already had it, and kept it
  worded consistently in `walk_forward_min_entry_score`, which previously lacked any mention).

### Tests

`services/market-data/tests/test_gate_harness_review_fixes.py` (14 cases) — `_resolvable_
window_end()`'s per-style lag mapping (including the GROWTH/SWING-share-a-bucket case and an
unknown-style fallback that must NOT silently resolve to a 0-day lag) and `_passes_promotion_
margin()`'s full decision surface (below-floor lift, at/above both thresholds, above the floor
but small relative to real dispersion, missing/skipped baseline or candidate, negative lift,
degenerate empty-returns input). `services/market-data/tests/test_promotion_gate_review_
fixes.py` (4 cases, source-text regression — `promotion_gate.py` imports `gate_harness.py`,
which pulls in the full Docker-only dependency chain) confirm `_resolvable_window_end` is
imported and used in BOTH of `promotion_gate.py`'s own independent window re-derivations (the
worst-trade-check recompute AND `_write_history`'s persisted-row computation), and that the
persisted `validation_window_end` reflects the adjusted value, not the raw one.
`services/signal-engine/tests/test_backfill_realized_ev.py` gained 3 new cases for the sign
fix (a mixed BUY-winner/SELL-winner fixture that must show a POSITIVE aggregate EV despite half
the raw `pct_return` values being negative; the mirror SELL-loser case; a pure-BUY fixture
confirming zero behavioral change for the common case).
`services/signal-engine/tests/test_calibrate_ta_weights_validation.py` (7 cases, source-text
extraction of the function's computational core — real numpy/sklearn, no DB/FastAPI
dependency) — the chronological-ordering requirement, a genuine promoting case (weights beat
baseline and are applied + `TuneHistory`-recorded with `promoted=True`), a genuine rejecting
case (an oracle baseline no real fit can beat — correctly leaves live weights untouched and
records `promoted=False`), and confirms `old_value` in the recorded row reflects the real
passed-in live weights, not a hardcoded default.

**A real test-design trap hit and fixed while building the ta_weights promoting-case
fixture** — matching this repo's own documented T255-STRATEGY-TUNER-PER-HORIZON lesson
("check that each axis of a 2D-fit test actually produces a DIFFERENT selected subset between
candidate and baseline"): an initial fixture varied only the two features the fit was meant to
learn were predictive, with every other feature held flatly False for every row — both the
uniform-default baseline's median-split AND the fitted candidate's concentrated-weight
median-split ended up selecting the IDENTICAL subset (both were driven entirely by the same
two features either way), showing zero real lift regardless of whether the fit itself was
correct. Fixed by adding NOISE features (uncorrelated with the true label) that the flat-weight
baseline gets pulled off-course by but the fitted candidate correctly learns to down-weight —
only then did the two vectors produce a genuinely different split and a real, measurable EV gap.

**Adversarial verification performed on every fix**, all guards sabotaged and confirmed to
fail correctly before being restored: the min-lift-margin check, the SD-ratio check, and the
`_HORIZON_RESOLUTION_LAG_DAYS` mapping in `gate_harness.py` (3 cycles); `promotion_gate.py`'s
`_write_history` window-recording fix (1 cycle); the SELL-negation line in `_retro_ev_for()`
(1 cycle, caught by 2 of the 3 new tests); `calibrate_ta_weights()`'s validation gate and its
chronological `.order_by()` (2 cycles). Full 697-test market-data suite and 152-in-scope-test
signal-engine suite (excluding the 2 pre-existing, unrelated failure groups already documented
elsewhere in this file — `test_signal_generator.py`'s `_decide` import-collection error and 4
`test_analyst_momentum.py` failures, both reconfirmed via `git stash` to predate this session)
green after every revert. `pyflakes` clean on all 4 touched files (both remaining warnings —
an unused `httpx` import in `outcomes.py`, an unused `MIN_SAMPLES_PER_SPLIT` import in
`promotion_gate.py` — confirmed pre-existing via `git stash`, only line numbers shifted).

**What to check if this looks wrong**:
```bash
# Confirm the validation-slice fix is live — a real backtest call should now show a genuinely
# non-empty validation slice at the default 60-day window for SWING/LONG/GROWTH, not just SHORT:
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/min-entry-score?style=SWING&market=US&window_days=60' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool
# baseline_validation.n_signals_seen should be > 0, not 0.

# Confirm the promotion-margin note is present on any promoted=true result:
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/extended-gate?style=SWING&market=US&param=min_kscore&window_days=60' \
  -H "Authorization: Bearer <admin token>" | python3 -c "import sys, json; print(json.load(sys.stdin).get('note'))"

# Confirm calibrate_ta_weights now requires validation (needs ≥50 evaluated BUY outcomes and
# ≥15 in the validation slice to even attempt a fit; safe to re-run, does nothing if rejected):
docker exec stockai-signal-engine-1 curl -s -X POST 'http://localhost:8005/signals/calibrate_ta_weights' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
# "applied": false with a real reason means the fit was correctly rejected — NOT a bug.

# Confirm _retro_ev_for's sign fix against real production data (compare before/after this
# deploy by re-running the exact independent-verification script from the trust-building
# cross-check entry above, adapted to also negate SELL rows):
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/watchdog_self_tuning_report' \
  -H "Authorization: Bearer <token>"
```

---


## Feature Reference: T288-KSCORE-WEIGHT-SWEEP — Walk-Forward Validated K-Score Factor Weights (2026-08-18)

**Closes the 3rd and final candidate from this session's own "next improvements" survey**
(Alpaca broker portability and `eps_revision_direction` were the other two, both documented
above). `_WEIGHTS` (`services/ranking-engine/src/scoring/kscore.py`) — the 6 factor weights
(technical/momentum/value/growth/volatility/relative_strength) that make up K-Score's
composite 0-100 score — has been a hardcoded, never-empirically-validated guess since this
service shipped. This session built a walk-forward sweep that recomputes historical K-Scores
under alternative weight sets directly from already-persisted data, and only promotes a
candidate if it beats the current live weights on data the sweep never searched.

**The key insight that makes this buildable with NO signal regeneration**: `Ranking`
(`shared/db/models.py`) already stores all 6 individual factor scores — `technical`,
`momentum`, `value`, `growth`, `volatility`, `rs_score` — per `(stock_id, as_of)`, not just the
final composite `score`. A weight sweep can therefore recompute an ALTERNATIVE composite score
for every historical ranking row directly from these already-stored factor values — the exact
same no-re-simulation advantage signal-engine's own `tune_strategy()` already established for
its `(buy_threshold x ml_weight_cap)` grid (re-filtering already-computed data, not replaying
history under different rules).

**Read-side: Redis-overridable weights**. `kscore.py` gained `_load_active_weights()` — reads
a single JSON blob from `stockai:kscore_weights` (all 6 weights TOGETHER, never 6 independent
keys, since they only mean something as a complete set summing to 1.0 — a partial override,
e.g. only `"momentum"` changed, would silently corrupt the other 5 factors' effective share),
falling back to the hardcoded `_WEIGHTS` on any absence/parse/connection failure — the same
fail-open-to-hardcoded-default convention every other Redis-tuned parameter in this codebase
already uses (e.g. signal-engine's `_get_style_tuned_param`). `compute_kscore()`'s existing
active-weight redistribution logic (excluding a factor when its stored value is `None`,
renormalizing the rest — `T234-RANK-KSCORE-PROXY-MIXING`) is completely unchanged; the override
only changes WHICH weights apply, never HOW they're combined.

**A real, session-poisoning bug self-caught during test-writing, not shipped**: the first
version of `compute_kscore()`'s wiring was `_active_weights = _load_active_weights()` (no
`dict()` copy) — but `_load_active_weights()`'s own fallback paths originally returned the
module-level `_WEIGHTS` object DIRECTLY, not a copy. `compute_kscore()` then does
`del _active_weights["value"]` a few lines later to implement the None-exclusion — mutating
that SAME shared object. The very first call to `compute_kscore()` in a process with
`value_score=None` would permanently delete `"value"` from the real `_WEIGHTS` dict, silently
corrupting every subsequent call for the rest of that process's lifetime. Caught when the
pre-existing `test_kscore.py::test_kscore_in_range` — which never touches Redis mocking at
all — started raising a real `KeyError: 'value'` when run in the SAME pytest session as this
feature's new tests, confirmed via `git stash` to NOT happen on the unmodified pre-existing
code. Fixed at BOTH layers defensively: `_load_active_weights()` now always returns
`dict(_WEIGHTS)` (a fresh copy) on every fallback path, AND `compute_kscore()` itself wraps the
call in `dict(...)` too, so a future caller anywhere else in the codebase can't reintroduce this
exact footgun by skipping one layer's own defense. Two dedicated regression tests
(`test_the_fallback_path_never_returns_the_module_level_weights_object_itself`,
`test_deleting_a_key_from_the_returned_weights_does_not_corrupt_the_hardcoded_default`) lock
this in — both were adversarially verified by reverting either layer's `dict()` wrapper and
confirming the corruption reproduces (a real `KeyError` from `compute_kscore()`'s own `del`
line) before restoring the fix.

**The sweep endpoint**: `POST /rankings/tune_kscore_weights` (`services/ranking-engine/src/api/
routes.py`) —
1. Fetches every `Ranking` row in the lookback window (`days`, default 365).
2. Bulk-fetches each involved stock's own chronological `(date, close)` list ONCE (not per
   ranking row), then computes each row's forward return via a BAR-INDEX offset
   (`_KSCORE_SWEEP_FORWARD_BARS = 20`, ~1 trading month) into that same list — never a
   calendar-day computation, matching `gate_harness.py`'s own T196 precedent for exactly why
   a bar-index lookup avoids weekend/holiday special-casing entirely. A row with no exact
   price match for its own `as_of`, or not enough elapsed trading days yet, is skipped, never
   guessed.
3. Chronological 70/30 train/validation split (never random — avoids look-ahead leakage,
   matching every other sweep in this codebase).
4. **Candidate generation**: `_kscore_candidate_weight_sets()` generates 12
   one-factor-perturbed-at-a-time candidates (`_KSCORE_SWEEP_DELTA = 0.05` up/down per factor,
   renormalized to sum to 1.0, floored at 0.01 so no weight can go to/below zero) — a
   coordinate-ascent-style sweep, not a full 6-dimensional grid, which would be combinatorially
   intractable at any reasonable step size. This mirrors the same "search a tractable
   neighborhood, not the full space" judgment `tune_strategy` already made for its own
   2-parameter (not 6-parameter) grid.
5. **EV metric — cross-sectional, not per-stock**: `_kscore_cross_sectional_ev()` ranks all
   stocks on a given `as_of` date by their RECOMPUTED composite score under a candidate weight
   set, takes the top decile, and averages their forward returns — then averages that daily
   figure across every date in the slice. This is a cross-sectional ranking metric, matching
   what K-Score is actually used for (ranking stocks against each other on a given day), not a
   per-stock buy/no-buy threshold the way `buy_threshold` is.
6. Best train-slice candidate is measured on the VALIDATION slice (data the search never saw)
   alongside the CURRENT LIVE weights measured on that SAME slice. Only promotes if the
   candidate's validation EV beats the live baseline's own validation EV — an unconditional
   `ev_lift <= 0` rejection, matching `T232-OC3`'s own established "never promote a candidate
   that doesn't clear a genuinely positive, validation-measured improvement" discipline used
   throughout this codebase. No shift-size escape hatch. The 12-candidate pool here is far
   smaller than `tune_strategy`'s own 403-cell grid, so the noise-inflation risk that motivated
   `gate_harness.py`'s stricter `_passes_promotion_margin()` (a min-lift-AND-min-SD-ratio floor)
   is smaller here — a bare `> 0` floor is judged the correct minimum bar for this pool size,
   not an assumed-safe shortcut.
7. On promotion: writes the new weight set to `stockai:kscore_weights` (30-day TTL, matching
   every other Redis-tuned parameter's own TTL convention) via the shared, pooled
   `common.redis_client.get_redis()` — ranking-engine's first consumer of that established
   pooled-connection helper (per the earlier Redis-connection-pooling audit's own convention;
   every other Redis access in this codebase now goes through it, not a raw
   `redis.Redis.from_url()`).
8. **One `TuneHistory` row per attempt (promoted or rejected)** via a new local
   `_record_kscore_tune_history()` helper — following the SAME per-service-duplication
   convention already established by `ml-prediction/src/training/tuner.py`'s and
   `signal-engine/src/api/signals_shared.py`'s own independent local copies (each service keeps
   its own, rather than a cross-service import — this app's `docker cp`-per-service deployment
   model doesn't otherwise have cross-service Python imports, and a shared import would create
   exactly that coupling). `parameter_class="kscore_weights"`, `style="ALL"`, `market="ALL"`
   (this sweep is deliberately market-agnostic — a future HK-vs-US-specific weight split is a
   real, separately-scoped possibility, not attempted here).

**A new read-only status endpoint**: `GET /rankings/kscore_weights_status` — the currently
EFFECTIVE weight set (Redis override if one has ever been promoted, else the hardcoded
default) alongside the hardcoded default itself, so an admin can see at a glance whether a
sweep has ever changed anything. **Registered BEFORE `GET /{symbol}`** in the file — the exact
same `BUG233-ROUTERORDER` bug class already documented elsewhere in this file (a bare
`GET /{symbol}` catch-all registered first silently swallows any literal-path GET route
registered after it) — placed right next to the pre-existing `/skipped` route, which already
carries its own comment about this exact ordering requirement. `POST /tune_kscore_weights`
needed no such placement care, since there is no POST catch-all in this router.

**Tests**: `services/ranking-engine/tests/test_kscore_weight_override.py` (10 cases) —
`_load_active_weights()`'s full fallback matrix (no key set, valid override, partial override
rejected, malformed JSON, Redis connection failure, non-dict JSON), `compute_kscore()`
genuinely using an override's real values (not just reading it in isolation), the
None-factor-redistribution logic staying unchanged under an override, and the 2
mutation-safety regression tests described above. Because `kscore.py` does
`from common.redis_client import get_redis` INSIDE the function body against a `common`
package `conftest.py` stubs as a bare `MagicMock()`, `unittest.mock.patch("common.redis_client
.get_redis", ...)` does NOT work here — the exact documented gotcha from this codebase's own
Redis-connection-pooling audit (a fresh `import common.redis_client` against a
`MagicMock`-stubbed parent auto-vivifies a DIFFERENT child mock than whatever was patched).
Fixed by registering a fake module directly in `sys.modules["common.redis_client"]`, restored
on context-manager exit — the one thing every `import common.redis_client` statement in the
same process actually shares.

`services/ranking-engine/tests/test_kscore_weight_sweep.py` (21 cases) — the pure
candidate-generation/recompute/redistribution/cross-sectional-EV functions
(`_kscore_active_weights_for_row`, `_kscore_recompute`, `_kscore_candidate_weight_sets`,
`_kscore_cross_sectional_ev`) are directly, behaviorally tested (no source-text extraction
needed — they take plain data with zero DB/session dependency, and `routes.py` imports
cleanly in this test environment per `test_screener_signal_scoping.py`'s own documented
precedent). Covers: exact hand-computed weighted sums, renormalization when a factor is `None`,
a degenerate all-excluded-factor case failing safe to `None` (never a `ZeroDivisionError`), all
12 candidates summing to ~1.0 (tolerance matched to the real 4-decimal rounding precision, not
an unreachably tight bound), no candidate weight ever going to/below zero, a genuine top-decile
ranking test (9 mediocre stocks + 1 highest-scoring stock with a distinctly different forward
return — proves the function ranks by RECOMPUTED score, not insertion order), skipping
unresolvable rows rather than treating them as a 0% return, and averaging across multiple days
rather than just the last one. `tune_kscore_weights()`'s own wiring (heavy DB/session
dependency, disproportionate to a full functional exercise — matching
`test_rank_symbol_market_scoping.py`'s/`test_screener_signal_scoping.py`'s own established
proportionate-testing convention for this test suite) is covered via 5 source-text regression
checks: route registration order, the unconditional non-positive-EV-lift rejection, the
unmeasurable-baseline-skips-rather-than-assumes-zero convention, one `TuneHistory` row per
branch including rejections, and the Redis write happening strictly AFTER every validation
gate (never before).

**Adversarial verification** — 6 sabotage/revert cycles, all caught correctly, each restored
and confirmed byte-identical via `md5sum` before moving on:
1. Reverting `compute_kscore()`'s wiring to the pre-fix `_active_weights =
   _load_active_weights()` (no `dict()` copy) alone — did NOT reproduce the corruption in
   isolation, since `_load_active_weights()`'s OWN fallback paths already independently return
   fresh copies (a real defense-in-depth double-fix, not a redundant one).
2. Reverting BOTH layers together (removing `dict(_WEIGHTS)` from `_load_active_weights()`'s
   own fallback returns AND `compute_kscore()`'s outer `dict()` wrap) — caught by both
   dedicated mutation-safety tests plus 3 downstream tests sharing the same test-order-visible
   global-state corruption, exactly the bug class this fix exists to prevent.
3. Disabling the `ev_lift <= 0` rejection (`if False:`) — caught by the dedicated rejection
   test and the Redis-write-ordering test.
4. Reordering `kscore_weights_status` to AFTER `GET /{symbol}` — caught by the dedicated
   router-ordering test with a real index-comparison failure.
5. Removing the `value is None` exclusion from `_kscore_active_weights_for_row()` — caught by
   2 tests, one via a real `TypeError: unsupported operand type(s) for *: 'float' and
   'NoneType'` (confirming this crashes, not just mismatches, when the redistribution is
   skipped).
6. Reversing the top-decile sort direction (`reverse=False` instead of `reverse=True`) — caught
   by the dedicated top-decile-ranking test, which correctly detected the WORST stock's return
   (1.0%) being selected instead of the top-scoring stock's (50%).

Full 61-test ranking-engine suite green modulo the ONE pre-existing, unrelated
`test_kscore.py::test_kscore_in_range` failure (asserts `0 <= v <= 100` on `c.value`/`c.growth`,
which are legitimately `None` by design when no real fundamentals are supplied — confirmed via
`git stash` to fail identically on the unmodified pre-existing code, predating this session).
`pyflakes` clean on both touched source files (the 2 remaining warnings — `db.SignalType`
imported but unused, `kscore.py`'s local `tr` variable — both confirmed pre-existing via
`git stash`, only line numbers shifted).

**What to check if this looks wrong**:
```bash
# Confirm the currently-effective weight set (Redis override if any, else the hardcoded default):
docker exec stockai-ranking-engine-1 curl -s 'http://localhost:8004/rankings/kscore_weights_status' \
  -H "Authorization: Bearer <token>"

# Run the sweep manually (safe — read-only against Ranking/Price until/unless it promotes):
docker exec stockai-ranking-engine-1 curl -s -X POST 'http://localhost:8004/rankings/tune_kscore_weights?days=365' \
  -H "Authorization: Bearer <token>"

# Check tune_history rows this mechanism wrote (promoted or not):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT ts, old_value, new_value, train_ev_pct, validation_ev_pct, baseline_validation_ev_pct, promoted, gate_failures FROM tune_history WHERE parameter_class='kscore_weights' ORDER BY ts DESC LIMIT 10;"

# Check the raw Redis override directly:
docker exec stockai-redis-1 redis-cli get stockai:kscore_weights
```
If `tune_kscore_weights` always returns `"applied": false, "reason": "only N ranking rows..."`,
check the real row count first — this sweep needs `_KSCORE_SWEEP_MIN_ROWS * 2 = 400` resolvable
rows (a real forward return, not just a `Ranking` row existing) before either slice can be
trusted; a young/thin universe may simply not have accumulated enough history yet, which is
correct, expected behavior, not a bug.

---


## Feature Reference: T230-BACKTESTING-MULTISYMBOL — Portfolio-Level Backtest MVP (Built 2026-08-18)

**Deliberately NOT what this tracker item's own `fix` text originally asked for** ("simulate
`paper_trading_step()` day-by-day using historical signals and prices") — a faithful replay of
that function needs no-historical-persistence regime detection (a permanent gap
`gate_harness.py` already discloses — see the section above), decision-engine calls,
`_scan_for_entries()`'s full candidate loop (staleness/watchlist/cross-portfolio-symbol locks),
and `_monitor_positions()`'s live day-by-day stop/target/trailing-stop/signal-decay exit logic
— a genuine "2+ weeks" build per `docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md` §1c,
which explicitly scopes this out as a still-unbuilt future Phase 2b.

**What shipped instead — an honestly-scoped MVP answering a smaller, real, useful question**:
"if I ran a shared-capital portfolio across N symbols using this app's own real entry/exit
ground truth and real position-sizing math, what would the resulting equity curve/Sharpe/
drawdown/win-rate have looked like?" New `services/market-data/src/backtest/
portfolio_backtest.py`:

1. **Reuses `gate_harness.py`'s ALREADY-PROVEN point-in-time-safe `SignalOutcome` ground
   truth** (real `entry_date`/`entry_price`/`exit_date`/`exit_price` per symbol, already
   computed and persisted by `evaluate_signal_outcomes()` — never a re-simulated exit) instead
   of replaying `_monitor_positions()`'s own live stop/target logic.
2. **Day-steps through a MERGED, chronologically-sorted event timeline across all requested
   symbols** — a shared cash pool, a `max_positions` cap, and a simplified sector-concentration
   cap all interact exactly as they would across a real multi-symbol book. Exits are processed
   BEFORE entries on the same calendar day (frees cash/room before that day's entries are
   sized).
3. **A genuine SUBSET of the real `risk_per_trade_pct`/`max_position_pct` sizing formula**
   from `paper_trading_engine.py`'s `_open_paper_trade()` — deliberately omitting the 6
   independent size multipliers (earnings/regime/confidence/research/consensus/score) the real
   function applies, since those inputs either don't exist historically (`live_regime`) or
   would need the full `_should_enter()` replay this module deliberately doesn't attempt.

**Disclosed, not silently glossed over, in the module's own top-of-file docstring**: no
decision-engine/`_should_enter()` gate replayed at all (every `SignalOutcome` BUY signal in the
window is treated as "the entry signal fired," with only portfolio-level caps as the admission
filter); no aggregate open-risk cap, no cross-symbol correlation cap, no drawdown circuit
breaker (until the same-day extension below), no cooldown/re-entry-lockout logic; no
commission/slippage; exits use the outcome's own resolved hold-window exit, not a simulated
stop/trailing-stop/target a live trade might have taken earlier or later.

**New endpoint**: `GET /paper-portfolio/backtest/portfolio` (admin-only), delegating directly
to `run_portfolio_backtest()`.

**Live-verified against real production data** (2026-08-18): 4 real symbols (AAPL/MSFT/NVDA/
GOOG), SWING/US, 180-day window — 59 signals seen, 12 entered, `win_rate` 41.67%, Sharpe 1.398,
`max_drawdown_pct` 2.63%, `final_equity` $102,210.17.

**Tests**: `services/market-data/tests/test_portfolio_backtest.py` (originally 21 cases, later
extended — see the drawdown-sweep section below) and `test_backtest_portfolio_route.py` (7
cases, source-text regression checks — `paper_portfolio.py` can't be imported directly in this
test environment). Covers the sizing subset formula, max-positions/sector-cap blocking, cash
reuse after an earlier exit, same-day exit-before-entry ordering, win-rate/avg-return
computation, and symbol-scoping (a signal for a symbol NOT in the requested list must never
leak in).

**A real hand-calculation mistake self-caught while writing the sizing test**: the first
version of `test_basic_sizing_uses_risk_per_trade_over_stop_distance` expected `shares=250.0`
from pure risk/stop-distance math alone, but the REAL function ALSO applies the
`max_position_pct` cap (0.10 of equity), which clamps the result to `shares=200.0`. Fixed by
correcting the expected value and adding a sibling test with a smaller `risk_per_trade_pct` to
isolate the pure formula from the cap.

**A `pct_return`/percentage-vs-fraction unit-mismatch bug self-caught while writing tests**:
`SignalOutcome.pct_return`/`return_{bucket}` are stored as FRACTIONS (`0.10` = 10%), not
percentages — initial test fixtures wrongly passed `pct_return=10.0` (meaning 1000%),
producing wildly wrong `avg_return_pct` assertions. Fixed by converting every fixture value to
a fraction.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/paper-portfolio/backtest/portfolio?symbols=AAPL,MSFT&style=SWING&market=US&window_days=60' -H "Authorization: Bearer <admin token>"
```

---


## Feature Reference: T234-CONFIG-UNJUSTIFIED-THRESHOLDS — max_portfolio_drawdown_pct Walk-Forward Sweep (Built 2026-08-18)

**The gap**: `max_portfolio_drawdown_pct` (0.20 — the master portfolio circuit breaker,
suspending new entries once drawdown-from-peak exceeds this fraction) was flagged in this
tracker item's own systemic audit as one of the highest-leverage never-empirically-validated
constants in the codebase. Unlike `gate_harness.py`'s per-signal sweeps (which filter WHICH
signals get admitted, replayable via a lighter per-signal function), the drawdown breaker only
gates NEW ENTRIES once the running portfolio is already underwater — testing a candidate value
means re-running the WHOLE day-stepped simulation with that threshold, since a post-hoc filter
on an already-finished equity curve can't know what the breaker would ACTUALLY have blocked
(blocking one entry changes every later day's cash/position state too).

**Fix, extending `portfolio_backtest.py` (above) same-day**:

1. **`run_portfolio_backtest()` now tracks a running peak** (`max(curve peak, current equity)`,
   updated at the START of each day's processing — mirroring `paper_trading_engine.py`'s real
   PA-D2 circuit breaker exactly) and gates new entries once `(peak - equity) / peak` exceeds
   `cfg["max_portfolio_drawdown_pct"]`. A new `n_skipped_drawdown_breaker` counter reports this
   separately from the pre-existing `n_skipped_no_room` (cash/sector/position-count blocks) —
   a genuinely different rejection reason worth distinguishing.

2. **New `sweep_max_portfolio_drawdown_pct()`** — walk-forward search over candidate threshold
   values, using the SAME chronological 70/30 train/validation split and promotion-margin
   discipline as `gate_harness.py`'s own `walk_forward_extended_gate()`/
   `walk_forward_min_entry_score()`, reusing its EXACT `_MIN_PROMOTION_EV_LIFT_PCT`/
   `_MIN_PROMOTION_LIFT_SD_RATIO` constants (not a second, independently-tuned margin).
   Promotion metric is `total_return_pct` — deliberately NOT `max_drawdown_pct` alone, since a
   breaker tuned purely to minimize drawdown trivially wins by never entering at all; the
   whole point of a circuit breaker is a return/risk TRADE-OFF, so the promotion criterion has
   to weigh the return side, with `max_drawdown_pct` reported alongside for context on what
   that return was bought/sold for.

3. **A real "still passes after sabotage" gap self-caught during adversarial verification, and
   closed by a code-quality improvement, not just a test addition**: the promotion arithmetic
   was initially inlined directly in `sweep_max_portfolio_drawdown_pct()`. Sabotaging the
   absolute-lift-floor guard (`if lift >= _MIN_PROMOTION_EV_LIFT_PCT:` → always-true) passed
   EVERY existing test undetected — none of the constructed scenarios happened to isolate a
   lift that clears the SD-ratio bar but fails the absolute floor specifically. Rather than
   fight the day-stepped simulator to construct that exact scenario (attempted first, found
   genuinely difficult — the breaker's binary block/admit nature tends to produce either a
   large or a zero lift, not a controllably-tiny one), the promotion arithmetic was extracted
   into its own standalone, directly-testable `_passes_return_promotion_margin(candidate_pct,
   baseline_pct, combined_trade_returns)` — a pure function taking plain values instead of
   `BacktestResult` objects. A new direct unit test
   (`test_a_lift_below_the_absolute_floor_never_promotes_even_with_zero_dispersion`) — a lift
   just below the floor, with IDENTICAL (zero-dispersion) combined returns, isolating the
   absolute-floor guard from the SD-ratio guard entirely — immediately caught the same
   sabotage that every inline test had missed.

**New endpoint**: `GET /paper-portfolio/backtest/drawdown-breaker-sweep` (admin-only, 365-day
default window — a walk-forward sweep needs enough history for a real 70/30 split on top of
the outcome-resolution lag, unlike the plain `/backtest/portfolio` route's 180-day default).

**Tests**: 23 new cases total — 15 in `test_portfolio_backtest.py` (the breaker gate itself,
the sweep function end-to-end, and 5 direct unit tests of `_passes_return_promotion_margin()`
in isolation) and 8 in a new `test_backtest_drawdown_sweep_route.py`. Adversarially verified 4
sabotage/revert cycles: disabling the drawdown-gate comparison entirely (caught by 3 tests
depending on real blocking behavior), disabling the SD-ratio guard (caught at BOTH the direct
unit-test level and the end-to-end sweep-test level), disabling the absolute-floor guard (the
gap described above, closed by the extraction), and sabotaging the new route's admin gate
(caught directly). All reverted and confirmed byte-identical via `md5sum` before moving on.

Full 1,736-test market-data suite green.

**Live-verified against real production data** (2026-08-18): a 90-day sweep with 20 real US
tech symbols correctly found a genuine train-slice winner (`candidate_value: 0.1`), correctly
reported `promoted: false` when that candidate's validation-slice result was identical to the
current live baseline's (neither ever tripped in that low-volatility window — real
`max_drawdown_pct` of 3.61%, well below either threshold). A wider window's train slice
correctly returned a clear `skipped_reason` rather than a fabricated result once it extended
past this app's real earliest `signal_outcomes` row (2026-05-25) — confirmed via a direct DB
query before trusting the empty result as correct behavior, not a bug.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "n_skipped_drawdown_breaker\|_passes_return_promotion_margin" /app/src/backtest/portfolio_backtest.py
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/paper-portfolio/backtest/drawdown-breaker-sweep?symbols=AAPL,MSFT,NVDA,GOOG&style=SWING&market=US&window_days=180' -H "Authorization: Bearer <admin token>"
```

---


## Signal-Testing-Framework Improvement Series — Closed Out (2026-08-20)

**User ask, verbatim**: "let's finish the self testing framework improvements." Referred to the
signal-testing-framework audit series that began with the 2026-07-31 review documented above
("Full Signal-Testing-Framework Review — 4 Critical Fixes") and continued through the
`Tier 261` deep audit (2026-08-05) and several later sessions' own fixes.

**Verified every deferred item against current code before assuming anything was still
open** — the exact discipline this file's own history has repeatedly shown is necessary,
since a tracker/CLAUDE.md entry can be stale in either direction (claiming something broken
that's fixed, or something fine that's still broken). Result: **3 of the 4 items the July 31
review deferred were already independently closed by later work**, none of it cross-referenced
back to the original review:

1. **`calibrate_ml_weight()`'s fixed-neutral-0.5 baseline** — already fixed under
   `AUD283-MLWEIGHT-RATCHET` (this same session's own earlier work): `baseline_weight =
   prev_cap if prev_cap is not None else 0.5`, confirmed live in `calibration.py`.
2. **`tune_style_profiles()`'s nested-subset baseline** — already fixed under
   `AUD263-STYLEPROFILES-SUPERSET-BASELINE`: the baseline is now `CURRENT_ML_CAP[style]`'s own
   filtered subset, not the full unfiltered validation slice.
3. **`gate_harness.py`'s missing `confidence_delta`/regime-blind replay** — `confidence_delta`
   was already fixed under `T232-DL-GATEHARNESS-INPUTGAP` (this session's own earlier work),
   confirmed via the module's own current top-of-file docstring, which explicitly tracks both
   gaps: `confidence_delta` marked FIXED, `live_regime` marked a PERMANENT, honestly-disclosed
   limitation (no historical regime-persistence table exists anywhere in this codebase to
   reconstruct "what was the regime on date X" from — building one is a real, separate,
   larger project, not something this pass could close).
4. **`gate_backtest()`'s same-day-close lookahead bias** — already fixed under
   `AUD283-GATEBACKTEST-LOOKAHEAD` (2026-08-16), confirmed via the function's own current
   docstring and a live grep showing it's a real, reachable, frontend-tabbed research tool
   (`gate-backtest-tool` in the tracker) — NOT the dead code the original review described.

**The Tier 261 audit (2026-08-05) had already gone far beyond the July 31 review's own scope**
— all 12 of its items are `defaultStatus: 'done'`, closing the win/loss-hurdle gaps in
`filter_audit()`/`factor_exposure()`/`walkforward_backtest()`, the unsigned-SELL mixing in
`outcomes_summary()`, the mark-to-today mislabeling, the profit-factor decoupling, the
paper-trade panel's false "actual closed trades" claim, the censoring staleness bound, the
drift-alarm threshold, the alpha-decay cherry-picking, the IC quality tiering, and the
by-symbol minimum-count floor — plus a closing `AUD261-CLEAN-VERIFIED` reference item
explicitly restating everything checked and confirmed correct.

**The one genuinely new piece of work built this session**: `AUD261-PAPERTRADE-PANEL-
MISLABEL`'s own fix (2026-08-06) relabeled the "Paper Trade Results" panel's false claim
honestly ("Forward Return by Hold Window · hypothetical") but deliberately did NOT build the
real alternative its own fix description named — "a genuine realized-P&L panel would be
sourced from actual PaperTrade rows, a separate query." Checked the real data volume before
building (96 real closed trades in production: 50 GROWTH, 46 SWING — enough to be meaningful,
not a wasted-effort panel with zero data) and built it: `GET /paper-portfolio/realized-
performance` in `paper_portfolio.py`, aggregating real closed `PaperTrade` rows (real fills,
real stops, real sizing, real exits) via a shared `_real_trade_stats()` pure helper reused
across `overall`/`by_style`/`by_exit_reason` — never 3 independently-drifting hand-rolled
aggregations. `market` scopes by the trade's own symbol suffix (`.HK`, matching this app's
established convention, e.g. `paper_trading_engine.py`'s HK-specific branches) since
`PaperTrade` has no direct `market` column of its own. New `RealizedPerformance` type +
`realizedPerformance()` wrapper in `api.ts`; new "Realized Trade Performance" panel on
`signal-accuracy.tsx`, placed directly below the existing (already-relabeled) hypothetical
panel so a user can compare the two side by side — reusing the page's EXISTING
`lookback`/`outcomesMarket` filter state, no new filter UI needed.

**A real test-writing trap self-caught during development, matching this repo's own "still
passes after sabotage is itself a finding" discipline**: the first version of the market-
scoping regression test checked that the bare string `"PaperPortfolio.config"` was absent from
the function's source — but the function's OWN docstring explains, BY NAME, why it deliberately
does NOT join against `PaperPortfolio.config` (explaining the design choice, not making it) —
so the naive string-absence check false-positived against its own explanatory prose the moment
it was run. Fixed by checking for an actual join/filter usage pattern instead
(`.join(PaperPortfolio` / `PaperPortfolio.config ==` / `PaperPortfolio.config[`) rather than the
bare substring.

**Tests**: `services/market-data/tests/test_realized_performance.py` (12 cases) —
`_real_trade_stats()` is pure with zero DB dependency, extracted via source-text `exec()` and
tested behaviorally: hand-computed win-rate/avg/median assertions, a strict `> 0` win boundary
(matching `kelly_sizing()`'s own established decisive-trades convention — a breakeven exit must
never inflate the win rate), all-`None`-`pct_return` degrading to `None` not a crash, and the
`avg_hold_days` denominator's real (documented, not "fixed") behavior of dividing by the full
trade count rather than just the trades with a non-`None` `hold_days`. `realized_performance()`
itself covered via source-text regression checks matching `test_compare_portfolio_metrics.py`'s
own established pattern for this file's import constraint.

**Adversarial verification** — 2 sabotage/revert cycles, both caught correctly and reverted
(confirmed byte-identical via `diff` before moving on): loosening the win-rate boundary from
`r > 0` to `r >= 0` (a breakeven exit wrongly counted as a win) — caught by the dedicated
boundary test; removing the market-scoping filter entirely — caught by the (corrected) market-
scoping test. Full 1941-test market-data suite green (up from 1929); `pyflakes` clean (all 4
remaining warnings confirmed pre-existing via `git stash`). Frontend: `tsc --noEmit` clean,
full 132-test Vitest suite unaffected (no test imports `signal-accuracy.tsx` directly), a full
`next build` clean (`/signal-accuracy` compiled at 17.2 kB, up from the pre-change baseline,
reflecting the new panel's added content).

**What remains genuinely open, not silently claimed closed**:
- `gate_harness.py`'s `live_regime` gap — a PERMANENT limitation (no historical regime-
  persistence table exists; building one is a separate, larger project).
- The grid-search promotion margin's own disclosed lack of a formal multiple-comparisons
  correction (Bonferroni-style or similar) — `_passes_promotion_margin()` already provides
  real protection against the worst case (a bare coin-flip), but does not formally correct for
  the train-slice grid search's own multiple-comparisons exposure. A statistical-rigor
  enhancement, not a bug, and not attempted this pass.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/paper-portfolio/realized-performance?days=90' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```
If `total: 0` despite knowing real closed trades exist, check the `days` window against
`SELECT trading_style, COUNT(*) FROM paper_trades WHERE stage='closed' GROUP BY trading_style;`
directly — a too-narrow `days` window (default 90) can legitimately exclude real trades closed
before the cutoff.

---


## Live Data Check: RestrictedSymbol Refresh + Walk-Forward Sweep Re-Run Against Larger Dataset (2026-08-26)

**Continues Tier 297's own restricted-symbol population and every prior walk-forward-sweep entry documented at length elsewhere in this file.** A decision-engine/paper-trading survey found `hard_rejects.py`/`_should_enter()` genuinely have NO remaining divergence (the T232-DL-DUALSCORER-DEBT porting series is confirmed complete — every gate cross-checked line-by-line, all present on both sides) — but flagged 2 real, non-code, live-data action items instead of a code bug.

**1. RestrictedSymbol list refresh** — re-ran the exact same query that produced the original 8-symbol AUD297 list (`n>=10` resolved BUY `signal_outcomes`, 0% win rate, all-time). A 9th symbol now clears the floor: **`6088.HK`** (FIT Hon Teng Limited) — 10 real resolved outcomes, all 10 losses, ranging -3.2% to -18.8%, spanning 3 genuinely distinct signal dates (not one clustered bad day). Added via the real admin API (`POST /paper-portfolio/restricted-symbols`), confirmed live in production Postgres — `restricted_symbols` now has 9 rows. No scheduled job exists to auto-refresh this list (confirmed by the survey — a real, if low-urgency, maintenance gap worth a periodic manual re-check rather than a one-time population).

**2. Walk-forward sweep re-run against a larger dataset** — `signal_outcomes` grew from the ~thin dataset available at the last sweep run (2026-08-22, data starting 2026-05-25) to **12,595 resolved rows spanning 2026-05-25 to 2026-08-13** by 2026-08-26. Re-ran all 6 real walk-forward sweep endpoints (`min-entry-score`, `calibration-feedback`, `blocked-entry-scores`, `risk-per-trade-sweep`, `drawdown-breaker-sweep` — 5 endpoints × the 4 real style/market combos from the app's own 4 real portfolios, `blocked-entry-scores` making it 6 distinct endpoint names but effectively the same 4-combo matrix) directly against production. **Zero promotions across all 24 runs** — every single one correctly reports either "no candidate cleared the sample floor," "did not beat the OFF baseline on the train slice," or "no candidate produced any admitted trades on the train slice." This is a genuine, useful confirmation, not a null result to shrug off: with 3x the data now available, every mechanism is STILL correctly declining to promote an unvalidated candidate — the system's own conservative-by-design promotion-margin gates (`_passes_return_promotion_margin()`, `_MIN_PROMOTION_EV_LIFT_PCT`/`_MIN_PROMOTION_LIFT_SD_RATIO`) are working as intended, not silently under-triggering from a code bug.

**What to check if this needs re-verifying**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT symbol FROM restricted_symbols ORDER BY symbol;"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT MIN(signal_date), MAX(signal_date), COUNT(*) FROM signal_outcomes WHERE is_correct_10d IS NOT NULL;"
```
Worth re-running the same 6-sweep × 4-combo matrix again once the resolved-outcome count grows meaningfully further (e.g. another 4-6 weeks) — a promotion becoming viable is a real possibility this system is explicitly designed to surface once the data supports it, not something to force earlier.

---


## T234-CONFIG-UNJUSTIFIED-THRESHOLDS — Full Triage Completed (2026-08-26)

**Closes the original item's own deliverable**: the 2026-07-04 audit found 27 numeric
thresholds/weights across decision-engine (`scorer.py`/`sizer.py`/`hard_rejects.py`),
`kscore.py`'s `_WEIGHTS`, and `paper_trading_engine.py`'s `_DEFAULT_CONFIG` with no empirical
citation. This session re-verified every item's CURRENT status against real code (not the
2026-07-04 snapshot) before triaging, per this file's own repeatedly-demonstrated discipline
that a tracker item's status text can drift stale in either direction.

**6 of 27 items already resolved**, none previously cross-referenced back to this tracker item:
- #1 `min_confidence` cross-file mismatch — `T234-CONFIG-DECIDE-DEFAULT-MISMATCH` (2026-07-23).
- #2 `regime_min_rr_ratio` fallback — now calibration-aware via `_default_min_rr_ratio()`.
- #13 4h/18h vs 72h staleness — investigated 2026-07-20, found NOT a real conflict (different
  mechanisms: a hard reject vs. a soft score nudge on the same already-agreeing 4h/18h values).
- #16 `kscore.py` `_WEIGHTS` — `T288-KSCORE-WEIGHT-SWEEP`, real walk-forward-validated.
- #22 `max_portfolio_drawdown_pct` — `AUD293`'s `sweep_max_portfolio_drawdown_pct()`.

**21 items remain genuinely open**, now explicitly triaged into 3 groups rather than a flat
undifferentiated list — full reasoning in `docs/AUDIT_TRIAGE_TIER234_2026-08-26.md`:

- **Group A (12 items)** — decision-engine `scorer.py`/`sizer.py` soft-score/sizing nudges
  (#3-12, #14-15). Judged lower-priority to sweep individually: each is an additive ±1/±2 score
  layer or a sizing multiplier feeding a downstream threshold (`min_entry_score`) that's ALREADY
  been walk-forward validated — a single-parameter sweep of any one constant in isolation would
  capture only a fraction of the real effect, since these constants interact with each other.
  A meaningful validation needs a joint multi-parameter sweep (like `tune_style_profiles`'s own
  approach in signal-engine), a materially larger project than this codebase's existing
  single-parameter `walk_forward_*` harness pattern.
- **Group B (5 items)** — `kscore.py`'s internal piecewise curve-shape constants (#17-21: RSI
  breakpoints/slopes, ADX-boost normalization, volatility/value/growth scale factors). Untouched
  by the T288 sweep, which only validated the 6 top-level `_WEIGHTS`. These are curve-shape
  parameters, not gate thresholds — validating them needs a genuinely different methodology
  (comparing K-Score's own predictive power against outcome data under alternative curve
  shapes) that doesn't fit the existing sweep harness without real new engineering.
- **Group C (4 items)** — standalone `paper_trading_engine.py` gates (#23 `max_open_risk_pct`,
  #24 `hold_stall_days`/`hold_stall_max_gain`, #26 HK `regime_suspension_days`, #27
  `min_stop_dist` floor). None currently swept. `max_open_risk_pct` is the closest candidate to
  the already-completed drawdown sweep (same "portfolio-wide circuit breaker" class) and is the
  recommended first target if this triage is ever revisited.

**Design invariant reinforced**: none of the 21 open items were touched by this triage — per
this codebase's own standing discipline against unvalidated changes to live-decision-affecting
parameters, "explicitly documented as intentionally arbitrary" is itself the correct, honest
disposition for a constant that hasn't (yet) earned a real walk-forward validation, distinct
from silently leaving it unaddressed.

**What to check if this needs revisiting**:
```bash
cat docs/AUDIT_TRIAGE_TIER234_2026-08-26.md   # full per-item reasoning
grep -n "^def sweep_\|^def walk_forward_" services/market-data/src/backtest/gate_harness.py services/market-data/src/backtest/portfolio_backtest.py
# 8 functions today — any NEW one appearing here means a Group A/B/C item has since been swept;
# cross-reference it back to this list before assuming it's still open.
```

---


## T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #23 — max_open_risk_pct Walk-Forward Sweep (2026-08-26)

**Continues the T234 triage's own recommended next step** — the triage doc
(`docs/AUDIT_TRIAGE_TIER234_2026-08-26.md`) named `max_open_risk_pct` as the highest-leverage
Group C candidate since it's the closest fit to the already-proven `max_portfolio_drawdown_pct`
sweep template. Built the same day.

**Mirrors `_open_paper_trade()`'s real PT-B5 aggregate-open-risk check exactly**: sums
`stop_distance * shares` across every open position, gates a new entry once
`(open_risk + new_trade_risk) / equity > max_open_risk_pct`. Reuses the SAME `stop_distance`
already stored on each open position dict from entry-time sizing — no new field needed. The
real live check uses the CURRENT live price minus the CURRENT (possibly-trailed) stop; this
simulator has neither a trailing-stop mechanism nor an intraday live-price series, so it uses
the fixed entry-time `stop_distance` instead — disclosed explicitly in the sweep's own `note`
field, matching this module's own established honesty convention for every other
disclosed simplification.

**Wired into `run_portfolio_backtest()`'s entry loop** right after the position-cap check,
matching the real function's own PT-B5 ordering. New `n_skipped_open_risk_cap` counter.
`sweep_max_open_risk_pct()` reuses the SAME chronological 70/30 walk-forward split and
promotion-margin machinery (`_passes_return_promotion_margin()`, `_MIN_PROMOTION_EV_LIFT_PCT`/
`_MIN_PROMOTION_LIFT_SD_RATIO`) as `sweep_max_portfolio_drawdown_pct()`/
`sweep_risk_per_trade_pct()` — a third sibling, not a fourth independently-tuned margin. New
`GET /paper-portfolio/backtest/open-risk-cap-sweep` endpoint (admin-only, read-only research
signal, never an automatic config change).

**Two real test-design lessons hit during development**:
1. The first version of the same-day exit-before-entry-ordering test placed the exit and next
   entry on DIFFERENT days — adversarial sabotage (feeding the open-risk sum a stale, pre-exit
   snapshot) still passed, since `open_positions` was naturally already empty of the exited
   symbol by the later day regardless of intra-day ordering, so the test wasn't actually
   exercising the property its own docstring claimed. Fixed by moving both events to the exact
   SAME calendar day — re-verified the same sabotage now correctly fails.
2. The promotion-margin test's own trade-return scenario needed real trial-and-error: an
   initial one-winner/one-loser shape at -60% cleared the absolute lift floor comfortably but
   consistently failed the SD-ratio guard (the single deep loss dominated the combined pool's
   own dispersion faster than the lift grew). Resolved by mirroring the ALREADY-PROVEN "both
   trades lose, tight cap blocks the deeper one" shape the drawdown sweep's own equivalent test
   already uses, rather than inventing a new scenario shape from scratch.

**Tests**: 18 new cases — `TestOpenRiskCircuitBreaker` (5, in `test_portfolio_backtest.py`) and
`TestSweepMaxOpenRiskPct` (5, same file) covering the gate/sweep behavior directly against a
real in-memory SQLite session via this file's own established `exec()`-extraction technique;
8 source-text route-wiring tests in `test_backtest_open_risk_cap_sweep_route.py`, matching
`test_backtest_drawdown_sweep_route.py`'s established pattern exactly.

**Adversarial verification** — 3 sabotage/revert cycles, all caught and reverted (confirmed
byte-identical via `md5sum` before moving on): disabling the gate entirely (`if False and
max_open_risk...`); the route delegating to the WRONG sweep function
(`sweep_max_portfolio_drawdown_pct` aliased as `sweep_max_open_risk_pct`); the same-day-ordering
fix reverted back to different-day placement (masking a stale-snapshot regression).

Full 2,061-test market-data suite green (up from 2,043); `pyflakes` clean (all 4 remaining
warnings confirmed pre-existing via `git stash`).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "max_open_risk_pct\|n_skipped_open_risk_cap" /app/src/backtest/portfolio_backtest.py
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/open-risk-cap-sweep?symbols=AAPL,MSFT,NVDA,GOOG&style=SWING&market=US&window_days=365' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool
```

---


## T234-CONFIG-UNJUSTIFIED-THRESHOLDS — Group C Closed (2026-08-26, same day)

**Closes Group C entirely** — after sweeping `max_open_risk_pct` (#23, documented in the
section immediately above), the remaining 3 Group C items were each individually investigated
rather than deprioritized in bulk, and found structurally NOT sweepable with the current
simulator, each for a distinct, checkable reason:

- **#24 (`hold_stall_days`/`hold_stall_max_gain`)** — `_monitor_positions()`'s "HOLD stall"
  exit only fires when the CURRENT LIVE signal for a symbol is HOLD, re-evaluated fresh on
  every intermediate day of a hold. `portfolio_backtest.py`'s own module docstring already
  discloses this exact gap — no decision-engine/`_should_enter()` gate is replayed at all
  mid-hold, exits use only the outcome's own resolved hold-window exit_date/exit_price. Testing
  this parameter needs the design doc's own still-unbuilt Phase 2b (a genuine day-by-day
  `_monitor_positions()` replay) — not something this triage's smaller sweeps can extend into.
- **#26 (HK `regime_suspension_days`)** — T210's circuit breaker reads `live_regime` fresh on
  every check. `gate_harness.py`'s own module docstring already discloses this as a PERMANENT
  limitation across the whole codebase: no historical regime-persistence table exists anywhere
  to reconstruct "what was the regime on date X." This is a standing, already-documented gap,
  not a scoping decision unique to this one parameter.
- **#27 (`min_stop_dist` floor, `max(price*0.005, 0.05)`)** — re-read both call sites
  (`hard_rejects.py`, `paper_trading_engine.py`) directly; their own comments state the purpose
  explicitly — "prevent infinite/backward R:R." This is a numerical-sanity guard against a
  degenerate divide-by-near-zero computation, not a strategy parameter with a real risk/return
  trade-off. A real, properly-sized stop (2x ATR, several percent of price) never approaches
  this floor in practice — there's no "tighter vs. looser" question a sweep could answer here.

**Result**: 7 of the original 27 items now resolved (up from 6). Group C is closed — 1 item
swept, 3 individually confirmed structurally unsweepable, none silently left open. The
remaining 20 open items are entirely Group A (12, decision-engine scorer/sizer nudges — needs a
joint multi-parameter sweep, a materially larger project than any single-parameter sweep built
so far) and Group B (5, K-Score curve-shape constants — needs a genuinely different validation
methodology than the existing threshold-sweep harness). Full updated reasoning in
`docs/AUDIT_TRIAGE_TIER234_2026-08-26.md`.

**Design invariant reinforced**: "not currently swept" and "cannot be meaningfully swept with
today's tooling" are different claims — this pass distinguished them explicitly for each of the
3 remaining Group C items rather than lumping them into a single "lower priority" bucket the
way the original triage draft did.

---


## T234-CONFIG-UNJUSTIFIED-THRESHOLDS — Group A Fully Closed: Real Walk-Forward Sweep Over
## decision-engine's compute_score() Threshold Constants (2026-08-26)

**Re-investigated all 12 Group A items individually** rather than trusting the earlier same-day
triage's own bulk "needs a joint multi-parameter sweep, deferred" framing — the same "verify a
grouping claim, don't inherit it" discipline this file already applies repeatedly elsewhere.
Found the group is far more heterogeneous than that framing implied:

- **3 items have ZERO tradeable-outcome linkage** — `#5`/`#6`/`#7`, all in `sizer.py`
  (research-score tiers, confidence-mult breakpoints, earnings-DTE size reduction). Confirmed
  via grep: `paper_trading_engine.py` never imports `sizer.py`/`compute_position` at all —
  `sizer.py`'s own module docstring already states it's a preview/scoring-only module whose
  output only ever reaches `/decide`'s response JSON for `decide.tsx`'s illustrative display.
  No `PaperTrade`/`pct_return` outcome can ever be attributed to one of these constants, so
  there's no backtest to build — same class as Group C's already-closed `min_stop_dist`
  finding (a value with no tradeable-outcome linkage, not a sweep waiting to happen).
- **1 item is already moot** — `#15` (`scorer.py`'s old Layer 3h "entry-zone drift" 4-way
  tiering). The CURRENT code's own `T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE` comment confirms this
  was already deleted (it double-scored the same static `entry2`/`breakout` comparison Layer 1
  already covers) — nothing left to sweep.
- **1 item needs a real code prerequisite first** — `#4` (`hard_rejects.py`'s time-of-day gate,
  lines ~548-566). Reads real wall-clock `datetime.now(timezone.utc)` directly with no `as_of`
  injection parameter, mirroring the identical bug class already fixed once in
  `_should_enter()`'s own time-of-day gate under `T232-DL-GATEHARNESS-INPUTGAP`/
  `BUG233-BACKTESTHARNESS-EMPTYVALIDATION`. A walk-forward replay against a historical
  `signal_date`/`entry_date` is structurally impossible until this function accepts an
  injectable "as of when" — a small, real, non-controversial fix mirroring an already-proven
  pattern, but a genuine prerequisite, deferred rather than rushed into this pass.
- **The remaining 7 items genuinely gate the REAL live entry decision** — `#3`
  (`hard_rejects.py`'s `max_breakout_extension_pct`, a HARD reject, confirmed pure with no
  wall-clock dependency of its own despite sitting textually adjacent to the time-of-day gate)
  and 6 inside `scorer.py`'s `compute_score()`: `#8` (chase-ceiling %), `#9` (R:R quality
  tiers), `#10` (volume_z bands), `#11` (bull_prob thresholds), `#12` (confidence-delta
  threshold), `#14` (insider/congress catalyst thresholds). Confirmed via grep: `routes.py`
  imports and calls `compute_score()`/`min_score_for_regime()` directly — this IS the real
  ENTER/BLOCKED verdict on the live `decision_engine_mode="primary"` trading path, not an
  illustrative preview like `sizer.py`. **Built and swept this session.**

### What was built

**1. `compute_score()`'s 6 constants made cfg-driven** (`services/decision-engine/src/api/core/
scorer.py`) — each new `cfg.get(key, <original literal>)` read defaults to the exact value the
function already hardcoded, so every existing caller that never sets these keys gets
byte-identical behavior. This is what makes the values sweepable at all — before this, nothing
short of editing the source could vary any of them. Full 274-test decision-engine suite green
before and after; `test_scorer.py` gained 6 new tests confirming both the default-matches-
original property AND that a non-default cfg value genuinely moves the score (not just an
unused-looking default parameter).

**2. New `POST /decide/score-replay`** (`services/decision-engine/src/api/routes.py`) — a
batched endpoint: N already-resolved historical BUY signals + ONE candidate `cfg`, scored in a
SINGLE request (never one call per signal — avoids an N×M round-trip cost across the
~2,000-2,900 resolved BUY outcomes typical per style). Calls the REAL `compute_score()`/
`min_score_for_regime()` directly — never a re-implementation of the scoring formula in a
second service, the exact anti-pattern this codebase's own repeated prior audits have found and
fixed elsewhere (duplicate business logic that can silently drift). Also applies item #3's
`max_breakout_extension_pct` as a pure, inlined pre-score hard reject that forces
`entered=False` regardless of score — deliberately NOT routed through the full
`check_hard_rejects()` (whose OTHER checks — market-hours, the time-of-day gate — read the real
wall-clock with no `as_of` injection, the exact problem this endpoint's own Layer-3e-freshness
omission already works around; reusing the whole function would reintroduce that same problem
for a check that doesn't actually need it).

`ScoreReplayInput` deliberately omits `ts`/`is_pre_choppy`/`is_pre_risk_off`/`recent_win_rate`
— the SAME disclosed, permanent scope limitation `replay_should_enter()` already carries.
Layer 3e (signal freshness) reads `signal_data["ts"]` against the real wall-clock with no
`as_of` injection; never sending `ts` at all correctly makes `compute_score()`'s own
`if sig_ts is not None:` guard skip that layer entirely (contributes 0, not a penalty) rather
than penalizing every replayed row as maximally stale. `is_pre_choppy`/`is_pre_risk_off`/
`recent_win_rate`/`live_regime` are never reconstructible for a historical replay — no
historical regime-persistence table exists anywhere in this codebase (the same permanent gap
`gate_harness.py`'s own module docstring already discloses for `replay_should_enter()`).

**A genuine, previously-undocumented test-isolation bug found and fixed while writing the new
tests**: `test_entry_gate_params.py`/`test_entry_weights.py` (collected alphabetically before
`test_score_replay.py` in the same pytest process) do
`sys.modules.setdefault("fastapi", MagicMock())` for their own unrelated purpose. Since pytest
imports every test file into one shared process, a later `from src.api.routes import
score_replay` reused the already-cached module object built against the FAKE fastapi — making
the `@router.post(...)`-decorated `score_replay` function a `MagicMock` instead of the real
code, silently discarding it from every assertion. Confirmed via direct bisection
(`pytest fileA.py fileB.py` in both orders). Fixed by forcing a reload:
```python
if isinstance(sys.modules.get("fastapi"), MagicMock):
    del sys.modules["fastapi"]
importlib.import_module("fastapi")  # forces the real package back into sys.modules
if "src.api.routes" in sys.modules:
    importlib.reload(sys.modules["src.api.routes"])
from src.api.routes import score_replay
```
Verified robust to both collection orders. 13 new tests in `test_score_replay.py` (9 basic +
4 for the item #3 hard reject, including a dedicated test confirming the threshold itself is
genuinely read from `cfg`, not a fixed 6.0 literal).

**3. New `walk_forward_scorer_sweep()`** (`services/market-data/src/backtest/gate_harness.py`)
— reuses the SAME point-in-time-safe reconstruction machinery `replay_should_enter()` already
has proven correct (`_fetch_matched_signals()`, `_historical_atr()`,
`_build_game_plan_for_style()`, `_historical_confidence_delta()`, `_historical_kscore()`) to
build `ScoreReplayInput`-shaped dicts, then batches them into `POST /decide/score-replay` calls
via a new `_score_replay_via_http()` helper (authenticated via the established `_svc_token()`
service-to-service JWT pattern, chunking at the endpoint's own 5000-input cap, returning `None`
— never raising — on any failure, matching `_call_decision_engine()`'s own never-raise
contract). Applies the SAME chronological 70/30 split + `_passes_promotion_margin()`
(`BUG233-BACKTESTHARNESS-COINFLIP`'s dual absolute-lift-AND-SD-ratio guard) promotion
discipline every sibling walk-forward function in this module already uses.

**Candidate generation is one-parameter-perturbed-at-a-time**, not a full joint grid — matching
ranking-engine's own `_kscore_candidate_weight_sets()` "search a tractable neighborhood, not
the full N-dimensional space" precedent for the identical reason (a full grid across 12
independent thresholds is combinatorially intractable at any reasonable step size). Each of the
12 candidates varies exactly ONE key from its default (2 candidates per constant, ± a fixed
step, clamped where the underlying quantity has a natural bound — a probability in [0,1], a
percent that can't go negative). Only the single best train-slice winner across the whole pool
is re-measured against the held-out validation slice — avoiding both a wasteful re-score of
every candidate on validation and (partially) the multiple-comparisons risk of validating many
candidates independently.

**A real "still passes after sabotage" finding, self-caught during adversarial verification**
(matching this repo's own standing discipline of treating that exact outcome as a finding, not
a shrug): the first version of the "a clamp that collapses a candidate onto its own default is
dropped, not emitted" test used the REAL production `_SCORER_SWEEP_STEP` table — sabotaging the
guard away (`if val == default: continue` removed) did NOT make the test fail, because checking
every real configured `(default, step, lo, hi)` tuple directly confirmed NONE of them actually
trigger a clamp collapse today (every `default ± step` already lands within its own bound).
Fixed by constructing a synthetic step table specifically engineered to exercise the clamp path
(`default=1.0, step=5.0, hi=1.0` — `1.0+5.0=6.0` clamps to exactly `1.0`, colliding with the
default) inside the test itself, temporarily swapping it into the function's own `__globals__`
and restoring afterward. Re-verified the sabotage is now correctly caught. 22 new tests in
`test_walk_forward_scorer_sweep.py` — a mix of real, direct `exec()`-extracted behavioral tests
for the pure `_scorer_sweep_candidates()` function and source-text structural checks for the
DB/HTTP-dependent functions (matching `test_walk_forward_calibration_feedback.py`'s established
convention for this exact Docker-only-dependency constraint).

**4. New `GET /backtest/scorer-sweep`** admin route (`services/market-data/src/api/
paper_portfolio.py`), matching the established `/backtest/*-sweep` route shape exactly
(style/market validation, 365-day default window — a walk-forward sweep needs enough history
for a real 70/30 split on top of the outcome-resolution lag, not the plain `/backtest/
portfolio` route's shorter 180-day default — `base_cfg` built from `_DEFAULT_CONFIG`/
`_STYLE_OVERRIDES`, admin-only). Unlike its portfolio-scoped siblings (`drawdown-breaker-sweep`,
`open-risk-cap-sweep`), this route takes no `symbols` param — `walk_forward_scorer_sweep()`
operates on ALL resolved BUY signals for a style/market, matching `replay_should_enter()`'s own
signature. 9 new tests in `test_backtest_scorer_sweep_route.py`.

**Adversarial verification across both services** — 6 sabotage/restore cycles total, all caught
correctly, each restore confirmed byte-identical via `md5sum`/`diff` before moving on: one
`compute_score()` constant reverted to a hardcoded literal (caught with a real assertion diff);
the `score_replay()` breakout-extension threshold reverted to a hardcoded 6.0 (caught); the
candidate-collapse-onto-default guard (the finding above, fixed and re-verified); the
`_passes_promotion_margin()` call swapped for a bare `>` comparison — the exact
`BUG233-BACKTESTHARNESS-COINFLIP` regression class this margin exists to prevent (caught); and
the new route's `base_cfg` construction hardcoded to `{}` instead of the real
`_DEFAULT_CONFIG`/`_STYLE_OVERRIDES` merge (caught). Full 274-test decision-engine suite and
2092-test market-data suite green throughout; `pyflakes` clean on every touched file (all
pre-existing warnings confirmed unchanged via `git stash` before this pass began).

**Net result**: 15 of the original 27 T234 items now resolved (up from 7 earlier the same day,
6 from prior sessions). Only 12 items remain genuinely open — Group B's 5 curve-shape constants
(still need a genuinely different validation methodology than the existing threshold-sweep
harness), Group C's 3 already-investigated-and-found-structurally-unsweepable items, and Group
A's own 4 non-sweepable items (3 with zero outcome linkage, 1 — item #4 — deferred behind its
own real, scoped `as_of`-injection prerequisite). Full updated per-item reasoning in
`docs/AUDIT_TRIAGE_TIER234_2026-08-26.md`'s "Group A Scorer Sweep" section.

**A real router-ordering bug found on FIRST live deploy verification, not caught by any test**
(a mistake worth its own entry): `POST /decide/score-replay` was originally registered AFTER the
pre-existing `POST /decide/{symbol}` catch-all — a real POST silently matched the catch-all
instead (`symbol="score-replay"`), returning a 422 instead of ever reaching the new endpoint.
Exactly the `BUG233-ROUTERORDER` class already hit once in signal-engine, and invisible to every
test in `test_score_replay.py` for the same reason it was invisible there: every test calls
`score_replay()` directly as a Python function, bypassing FastAPI's real route dispatch entirely
— registration order simply never enters the picture when a function is called directly. Fixed
by moving `score_replay`'s registration to sit alongside the pre-existing `/decide/batch` route,
before `/decide/{symbol}`, with a new `TestScoreReplayRouterOrdering` source-text regression
test (comparing decorator source-POSITION, the only thing that can actually catch this class of
bug without driving a real FastAPI `TestClient`) — adversarially verified by reverting the
registration order and confirming it fails with a real, meaningful assertion. 276-test
decision-engine suite green after the fix.

**Live-verified end-to-end against real production data after the fix.**
`GET /backtest/scorer-sweep?style=SWING&market=US&window_days=365` correctly returned an honest
`skipped_reason` (real resolved `signal_outcomes` data only spans 2026-05-25 → 2026-08-11 today
— a 365-day window's computed train slice genuinely contains zero real rows, confirmed via a
direct SQL cross-check before trusting the endpoint's own answer). At a realistic `window_days=90`
matching the app's actual data span, the sweep produced a genuine, complete result: 1,126 real
train-slice signals, a real winning train-slice candidate (`rr_excellent_threshold: 3.0`,
beating baseline's `-1.34%` avg return with `-1.26%`), correctly re-measured against the held-out
validation slice where it scored `0.4539%` vs. baseline's `0.4662%` — a real LOSS on validation,
so `promoted: false`, exactly the honest outcome the promotion-margin discipline exists to
produce when a train-slice edge doesn't generalize. Per this codebase's own established
promotion discipline, `promoted: true` from this endpoint is a research signal only; it never
changes any live decision-engine config on its own — applying a winning candidate to real
trading requires a separate, explicit config change.

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 grep -n "def score_replay\|chase_ceiling_pct\|rr_excellent_threshold" /app/src/api/routes.py /app/src/api/core/scorer.py
docker exec stockai-market-data-1 grep -n "def walk_forward_scorer_sweep\|_SCORER_SWEEP_STEP" /app/src/backtest/gate_harness.py

# Confirm score-replay is registered BEFORE the /decide/{symbol} catch-all (the exact bug
# above) — the decorator for /decide/score-replay must appear earlier in the file:
docker exec stockai-decision-engine-1 grep -n '@router.post("/decide' /app/src/api/routes.py

# Run the sweep live for a real style/market combo (needs an admin JWT — safe, read-only
# research call, never writes to any portfolio's live config). window_days should roughly
# match how far back real resolved signal_outcomes data actually goes — check that first if
# the sweep always returns skipped_reason:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT MIN(signal_date), MAX(signal_date) FROM signal_outcomes WHERE is_correct_10d IS NOT NULL;"
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/scorer-sweep?style=SWING&market=US&window_days=90' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool
```
If the sweep always returns `skipped_reason`, check the real resolved-BUY-signal count for that
style/market/window first — `walk_forward_scorer_sweep()` needs at least `MIN_SAMPLES_PER_SPLIT`
(15) resolved signals in BOTH the train and validation slices before it can produce a real
result, matching every sibling walk-forward function's own sample floor.

---


## T234-CONFIG-UNJUSTIFIED-THRESHOLDS — Group B Fully Closed: Real Walk-Forward Sweep Over
## K-Score's Curve-Shape Constants; #20/#21 Found Already Moot by an Uncross-Referenced 2026-07-04
## Deletion (2026-08-26/27)

**Continues Group A's own closure the same session.** Group B's original triage
(`docs/AUDIT_TRIAGE_TIER234_2026-08-26.md`) deferred all 5 items (#17-21, `kscore.py`'s
RSI-to-score piecewise mapping, ADX-boost normalization, volatility scale factor, value-proxy
discount scale, growth-proxy CAGR scale) as needing "a genuinely different methodology" than
the existing `walk_forward_*` threshold-sweep harness, "real new engineering." Investigated
each of the 5 individually rather than accepting that bulk framing wholesale — 2 turned out to
be already moot, and the remaining 3 were genuinely buildable with new-but-tractable
infrastructure.

### #20/#21 — already moot, never cross-referenced back to this tracker

`_value_proxy()`/`_growth_proxy()` — the two functions #20/#21's own scale-factor constants
belonged to — no longer exist anywhere in `kscore.py`. Confirmed via `git show 354f665`
(2026-07-04, `T234-RANK-KSCORE-PROXY-MIXING`) — **the SAME commit that also fixed Group A's own
item #15** (`scorer.py`'s Layer 3h double-count), with the identical "resolved by deletion,
never cross-referenced back to the tracker" gap already documented for #15 in Group A's own
closure. `value_score`/`growth_score` are now excluded entirely (weight redistributed to the
remaining factors) whenever a real fundamental is unavailable — there is no curve-shape formula
left for #20/#21 to sweep. This is the Nth recurrence of this exact staleness pattern in this
codebase's own tracker history: a fix under one tracker id resolves an item filed under a
completely different, unrelated id, with no cross-reference ever added — the fix looks
"unrelated" to the item it actually closes unless someone re-reads the real code directly.

### #17/#18/#19 — swept, real new infrastructure

These constants sit one level BELOW the already-persisted `Ranking.technical`/`.volatility`
values `T288-KSCORE-WEIGHT-SWEEP`'s own `_kscore_recompute()` operates on — validating them
needs recomputing `_technical_score()`/`_volatility_score()` from real historical `Price` bars
under a candidate curve, not just re-weighting already-stored numbers. `T288` genuinely could
not be reused as-is; this required real, new infrastructure, matching the triage's own original
assessment on this specific point.

**Live-override resolution mirrors `_load_active_weights()`'s own established convention
exactly** — new `_load_active_curve_params()`/`_curve_params(cfg)` in
`services/ranking-engine/src/scoring/kscore.py`, a 3-layer resolution (hardcoded
`_CURVE_DEFAULTS` → a live Redis override at `stockai:kscore_curve` if `POST /rankings/
tune_kscore_curve` has ever promoted one → an explicit `cfg` override layered on top).
`curve_cfg=None` means "whatever is currently live," never silently the hardcoded default — a
future re-sweep must build on top of an earlier promotion, not re-search from the original
values every time. Deliberately **allows a partial override**, unlike the weights override's
all-or-nothing rule — each of the 11 curve constants is independently meaningful (unlike
weights, which only mean something as a complete set summing to 1.0), so a single promoted
parameter should apply on its own.

**Raw-input/curve-mapping split for tractable compute cost.** Profiled with `cProfile` before
committing to any design: RSI/ADX EWM computation dominates the cost (~6ms/call) vs. the cheap
curve-shape remap (~0.1ms/call). Split `_technical_score()` into `_technical_raw_inputs(df)`
(expensive: `above_sma50`/`above_sma200`/`sma50_above_sma200`/`rsi`/`adx`) and
`_technical_score_from_raw(raw, cfg)` (cheap: the parameterized piecewise RSI mapping + the
ADX-boost formula), with `_technical_score(df, cfg=None)` as a thin composition of both —
mirrored identically for `_volatility_score()`. `_kscore_curve_raw_cache()`
(`services/ranking-engine/src/api/routes.py`) computes the expensive step ONCE per historical
`Ranking` row (point-in-time correct via `bisect`, mirroring `gate_harness.py`'s own
`_historical_atr()` discipline exactly — only `Price` bars with `ts.date() <= r.as_of` are
visible), and the ~20-candidate sweep pool only pays the cheap remap cost per candidate.
Brought an estimated ~800s full sweep down to ~63s.

**A real formula bug caught via byte-identical-at-defaults verification, before shipping —
not a hypothetical worry.** The original code's own comment for the ADX-boost formula
(`np.clip((adx - 15) / 25, -1, 1) * 10`) loosely implied "strong trend >25" reads as
`adx_ceiling=25` with a 10-point ramp width from a `adx_floor=15`. A first parameterization
attempt built on exactly that assumption and failed a 200-randomized-seed comparison against
an INDEPENDENTLY hand-reimplemented copy of the ORIGINAL formula (never importing from the
module under test): `tech_new: 38.50` vs `tech_old: 34.96` — genuinely different functions.
Direct comparison at sample ADX values (5/10/15/20/25/30/40) confirmed the real math uses TWO
independent constants — `adx_center: 15.0` and `adx_divisor: 25.0` — where the clip only
actually saturates at `adx=40` (`center+divisor`), never at `adx=25` as the comment's own
prose loosely implied. Fixed as `np.clip((adx - p["adx_center"]) / p["adx_divisor"], -1, 1) *
p["adx_boost_scale"]`; re-verified to 0 mismatches across 200 seeds. Adversarially confirmed by
reverting `adx_divisor` back to the original bug and watching the dedicated tests
(`test_technical_score_matches_the_original_formula_at_defaults_across_many_seeds`,
`test_adx_boost_saturates_only_when_the_true_divisor_bound_is_reached_not_at_the_old_ceiling_
name`) fail with a real, meaningful diagnostic, then restoring and confirming byte-identical
via `md5sum`.

**`_kscore_cross_sectional_ev()` generalized to accept a `composite_fn` callable** instead of
hardcoding `_kscore_recompute(weights, row)` internally — the weights sweep's 3 call sites now
pass `lambda row: _kscore_recompute(_BASE_WEIGHTS, row)`, and the curve sweep passes its own
`_kscore_curve_composite_fn(base_weights, curve_cfg, raw_cache)` closure. Avoids writing a
second, parallel EV-measurement function that could silently drift from the weights sweep's
own already-proven one — the exact "duplicate business logic that can silently drift" anti-
pattern this codebase's own prior audits (the Redis-connection-pooling series, the duplicated-
business-logic audit) have repeatedly found and fixed elsewhere, avoided here proactively
rather than discovered later.

**A real, previously-unresolved audit-trail gap found and fixed while wiring this up, not left
for a future session.** `_record_kscore_tune_history()` (the shared `TuneHistory`-writing
helper both sweeps call) had `parameter_class="kscore_weights"`/`parameter_name="factor_
weights"` as HARDCODED LITERALS with no way to vary per-caller — every one of the new curve
sweep's own 6 `TuneHistory` rows would have been silently mistagged `"kscore_weights"`,
indistinguishable in the audit trail from the sibling weights sweep's own real attempts.
Fixed by adding both as keyword-only parameters defaulting to the ORIGINAL weights-sweep
values (`parameter_class: str = "kscore_weights", parameter_name: str = "factor_weights"`) —
`tune_kscore_weights()`'s own 6 existing call sites needed zero changes — with
`tune_kscore_curve()` explicitly passing `parameter_class="kscore_curve",
parameter_name="curve_shape"` at each of its own 6 call sites. Adversarially verified: removing
the override from even 1 of the 6 sites is caught by a dedicated test
(`test_tune_curve_endpoint_tags_every_tune_history_call_with_the_curve_parameter_class`, plus a
mirrored `..._never_leaves_a_call_site_on_the_weights_default` guard against the inverse
mistake), reverted and confirmed byte-identical via `md5sum` before moving on.

**New `POST /rankings/tune_kscore_curve`** — same chronological 70/30 split + unconditional
non-positive-EV-lift rejection (`if ev_lift <= 0:`, no shift-size escape hatch, matching
`T232-OC3`'s established discipline) + unmeasurable-baseline-is-a-skip-never-an-assumed-zero
(the same T232-OC3 convention) + one `TuneHistory` row per attempt regardless of outcome, as
`tune_kscore_weights()`'s own established discipline. `_kscore_curve_candidate_sets()`
generates one-parameter-perturbed-at-a-time candidates (`_KSCORE_CURVE_SWEEP_DELTA`, a
relative-percentage step per constant — 5-20% depending on the constant's own scale, since the
11 constants span wildly different magnitudes, e.g. `rsi_low=30` vs `volatility_scale=1500`),
matching `_kscore_candidate_weight_sets()`'s own "tractable neighborhood, not the full
N-dimensional grid" judgment exactly — never a combinatorial full-grid search. A base constant
that's exactly `0.0` produces no candidates for that key (a relative step of a zero value has
no meaningful magnitude), correctly skipping rather than fabricating a spurious perturbation.

**New `GET /rankings/kscore_curve_status`** — the currently-effective curve params (Redis
override if any, else the hardcoded defaults) alongside the hardcoded defaults themselves,
matching `kscore_weights_status()`'s own established shape. **Registered proactively BEFORE
the `GET /{symbol}` catch-all** — the exact `BUG233-ROUTERORDER` bug class already hit once
live in decision-engine's own `score_replay` deploy earlier this same session — caught this
time by checking route-registration order directly via `grep` before ever deploying, not
discovered via a live 422/404 after the fact.

### Tests and verification

`test_kscore_curve_params.py` (15 cases) — pure curve-function behavior, including the byte-
identical-at-defaults check across 200 randomized seeds (technical) and 50 seeds (volatility)
that caught the ADX bug above, plus the ADX-saturation-boundary proof test, per-item override
tests (deliberately hand-built raw-input fixtures rather than relying on a generated price
series that might not land inside the target curve segment — a real test-writing mistake was
self-caught and fixed here: an earlier draft's `rsi_low` override test used a synthetic price
series whose real computed RSI already fell past `rsi_mid`, making the override genuinely
inert and producing a failure for the WRONG reason — fixed by hand-constructing a deterministic
raw dict with `rsi=35.0` explicitly inside the target segment).

`test_kscore_curve_override.py` (11 cases) — the Redis live-override read side, reusing
`test_kscore_weight_override.py`'s own exact `_patched_get_redis()` `sys.modules`-registration
technique (`kscore.py` does `from common.redis_client import get_redis` INSIDE the function
body against a `common` package `conftest.py` stubs as a bare `MagicMock()` — `unittest.mock
.patch` does not reach a fresh in-function import against a mocked parent, the same documented
gotcha this codebase's Redis-pooling audit already established).

`test_kscore_curve_sweep.py` (15 cases) — the 5 pure `_kscore_curve_candidate_sets()` tests
plus 10 source-text regression checks for `tune_kscore_curve()`'s own wiring (router-order,
unconditional EV-lift rejection, unmeasurable-baseline-is-a-skip, exact `== 6` `TuneHistory`
call count scoped strictly to the function's own body, the `parameter_class` tagging
double-check pair described above, Redis-write-after-all-gates-pass ordering, the shared bar-
index forward-return offset, and — a genuinely meaningful invariant, not a rubber-stamp check —
the expensive raw-cache computation happening EXACTLY ONCE, strictly BEFORE the candidate
loop, never once per candidate).

**A real, self-caught pre-existing test-quality gap found while sabotaging the `TuneHistory`
call-count test**: the original `test_kscore_weight_sweep.py` boundary test (re-anchored to
`'@router.post("/tune_kscore_curve")'` when the curve sweep was first inserted, per this
session's own earlier fix) used a `>= 6` bound. Sabotaging the boundary marker back to the OLD
`"def refresh("` end-point — which would silently sweep `tune_kscore_curve()`'s own 6 calls
into the SAME count, giving 12 — still passed the `>= 6` assertion (`12 >= 6` is True), a real
"still passes after sabotage" red flag this repo's own testing discipline treats as a finding
in its own right, not a coincidence to shrug off. Investigated, confirmed the exact count
(`12 != 6`) directly, and tightened the bound to `== 6` — re-verified the same sabotage now
correctly fails (`12 == 6` is False). This is the same class of "a loose comparison operator
silently accepts more than it should" lesson already documented multiple times elsewhere in
this file (e.g. `BUG233-BACKTESTHARNESS-COINFLIP`'s own bare `>` vs. a real promotion margin),
just recurring in a test's own assertion this time, not in production code.

**Full suite verification**: 101 tests pass (up from 86 pre-Group-B — 15 new curve-shape tests
+ the tightened boundary check), the sole remaining failure
(`test_kscore.py::test_kscore_in_range`) confirmed via `git stash` on clean `prod` HEAD to be
genuinely pre-existing and unrelated (a fixture supplying no fundamentals legitimately produces
`value=None`/`growth=None`, which the test's own `0 <= v <= 100` assertion can't handle —
predates this entire session's work). `pyflakes` clean on both touched files — the 2 remaining
warnings (`db.SignalType` imported but unused in `routes.py`; `kscore.py`'s local `tr`
variable) both confirmed pre-existing via `git stash`, only the `kscore.py` warning's line
number shifted (101→173, exactly reflecting the ~72 lines of new curve-shape code added above
it).

### T234-CONFIG-UNJUSTIFIED-THRESHOLDS is now COMPLETE across all 3 groups

Group A: 12 items, all closed (7 swept via `walk_forward_scorer_sweep`, 3 zero-outcome-linkage,
1 already-moot-by-deletion, 1 deferred behind a real, scoped `as_of`-injection prerequisite).
Group B: 5 items, all closed (3 swept via `tune_kscore_curve`, 2 already-moot-by-deletion).
Group C: 4 items, all closed (1 swept via `sweep_max_open_risk_pct`, 3 individually confirmed
structurally unsweepable for distinct, recorded reasons). Combined with the 6 items already
resolved by prior sessions (never cross-referenced back to this tracker before this session's
own re-verification pass) and item #23 swept the same day as the original triage: **20 of the
original 27 items have an explicit, checkable disposition; 7 remain genuinely open**, each with
a specific, individually-investigated reason recorded in `docs/
AUDIT_TRIAGE_TIER234_2026-08-26.md` — no blanket "lower priority" labels anywhere in the
final disposition.

**What to check if this looks wrong**:
```bash
docker exec stockai-ranking-engine-1 grep -n "def tune_kscore_curve\|def _load_active_curve_params\|adx_divisor" /app/src/api/routes.py /app/src/scoring/kscore.py

# Confirm kscore_curve_status is registered BEFORE the /{symbol} catch-all:
docker exec stockai-ranking-engine-1 grep -n '@router.get("/kscore_curve_status")\|@router.get("/{symbol}")' /app/src/api/routes.py

# Confirm current curve params (live override if any promoted, else hardcoded defaults):
docker exec stockai-ranking-engine-1 curl -s 'http://localhost:8004/rankings/kscore_curve_status' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# Run the sweep live for real production data (safe — read-only until/unless it promotes;
# needs enough real historical Ranking + Price rows to clear the sample floor on both slices):
docker exec stockai-ranking-engine-1 curl -s -X POST \
  'http://localhost:8004/rankings/tune_kscore_curve?days=365' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# Confirm TuneHistory rows from the curve sweep are correctly tagged (never "kscore_weights"):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT ts, parameter_class, parameter_name, promoted, gate_failures FROM tune_history WHERE parameter_class='kscore_curve' ORDER BY ts DESC LIMIT 10;"
```
If `tune_kscore_curve` always returns `skipped_reason`, check the real resolved-`Ranking`-row
count for the requested window first — the sweep needs at least `_KSCORE_SWEEP_MIN_ROWS * 2`
rows with a resolvable forward return in BOTH the train and validation slices, matching every
sibling walk-forward function's own sample floor.

---


## T288-KSCORE-WEIGHT-SWEEP / Group B — Scheduled Weekly + Fixed a Pre-Existing Test Bug in the
## Same File the Fix Involved (2026-08-27)

**Two closely-related follow-ups from the Group B work above, done together.**

### 1. `tune_kscore_weights`/`tune_kscore_curve` had never been scheduled

Both sweeps were built, live-verified, and self-apply their promotion via Redis — but neither
had a cron registration anywhere. The only way either ever ran was a manual HTTP call,
including the one real promotion currently live in production (`rsi_mid: 50→45`,
`volatility_scale: 1500→1200`, promoted 2026-08-27 off a thin 13-day validation sample) — it
had no scheduled path to ever be re-checked against a larger dataset. Same
`SELFIMPROVE-MISSING-SCHEDULE-REGISTRATIONS` gap class already fixed once for
`calibrate_ml_weight` and once for `tune_strategy`.

**Fix**: added both calls to `_weekly_full_refresh()` (`services/market-data/src/services/
scheduler.py`, Sunday 14:00 PST), right after `tune_strategy` — matching every sibling
calibration job's own `_post()` + `_record_job_status()` pattern exactly. **Ordering is
genuinely load-bearing here**, unlike most siblings in this sequence: `tune_kscore_curve`'s
own composite function recomputes the score using WHATEVER weight set is currently live
(`_kscore_curve_composite_fn(current_weights, ...)`), so weights must run first — a
same-cycle weights promotion should be visible to that same cycle's curve sweep, not stale by
a week. This IS the re-check mechanism for the live promotion: each run re-validates against
whatever was promoted last time (never re-searches from the original hardcoded defaults, per
`_load_active_curve_params()`'s own live-override resolution), so next Sunday's run
re-measures the current `rsi_mid`/`volatility_scale` override against a materially larger
dataset and can only keep it if it still beats the (by-then-larger) validation-slice bar.

**Tests**: `services/market-data/tests/test_kscore_sweep_scheduling.py` (7 cases), mirroring
`test_tune_strategy_scheduling.py`'s established pattern exactly — source-text regression
checks (`scheduler.py` can't be imported directly in this test environment). Adversarially
verified: swapped the two calls' order and confirmed the dedicated ordering test failed with
a real, meaningful assertion (`7148 < 6963`), then restored and confirmed byte-identical via
`diff`.

### 2. `test_kscore.py::test_kscore_in_range` — a real, pre-existing test bug, not a code bug

The failure (confirmed via `git stash` multiple times across this whole session to predate
every bit of Group B work) traced to the test's own outdated premise: it range-checked
`c.value`/`c.growth` alongside every other field, but `T234-RANK-KSCORE-PROXY-MIXING`
(2026-07-04) already made those two fields correctly `None` whenever no real fundamentals are
supplied — exactly the case this test's own fixture exercises (no `value_score`/`growth_score`
ever passed). `KScoreComponents`' own dataclass typing (`value: float | None`) already
documents this. The code was correct the whole time; the test simply predated the fix that
made `None` the right answer and was never updated.

**Fix**: split into 3 tests — `test_kscore_in_range_without_fundamentals()` (confirms `value`/
`growth` are correctly `None`, range-checks only the always-real fields), `test_kscore_in_
range_with_fundamentals()` (the original intent, now with real `value_score`/`growth_score`
supplied), and a new `test_value_and_growth_genuinely_participate_in_the_weighted_composite()`
— added after a real, self-caught "still passes after sabotage" gap: the with-fundamentals
test alone doesn't prove `value`/`growth` actually influence `c.score`, since
`KScoreComponents.value`/`.growth` are a pure pass-through of the caller's own input,
independent of the weighting logic. Sabotaging the weighting exclusion logic (`if value_score
is None:` → `if True:`) passed the with-fundamentals test cleanly, since it only checks that
`c.value` echoes back what was supplied — the new third test (varying only `value_score` and
confirming `c.score` changes) correctly catches this class of regression.

**Verification**: full 104-test ranking-engine suite green (up from 101), pyflakes clean. Full
2099-test market-data suite green (up from 2092). Both adversarial sabotage cycles reverted
and confirmed byte-identical via `diff`/`md5sum` before moving on.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "tune_kscore_weights\|tune_kscore_curve" /app/src/services/scheduler.py

# Confirm the weekly job actually fires (next Sunday 14:00 PST) and re-checks the live promotion:
docker logs stockai-market-data-1 --since 24h | grep 'tune_kscore_weights\|tune_kscore_curve'

# Check whether the live rsi_mid/volatility_scale promotion has been re-confirmed or changed
# by a later weekly run:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT ts, parameter_class, old_value, new_value, promoted FROM tune_history WHERE parameter_class IN ('kscore_weights', 'kscore_curve') ORDER BY ts DESC LIMIT 5;"
docker exec stockai-ranking-engine-1 curl -s http://localhost:8004/rankings/kscore_curve_status
```

---

