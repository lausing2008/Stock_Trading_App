# T234 Threshold Triage — 2026-08-26 (updated same day: Group C closed)

Re-verified all 27 items from `AUDIT_REPORT_TIER234_2026-07-04.md` Part 2 against current code
before deciding disposition. 7 resolved (6 by prior sessions, never previously cross-referenced
back to this list — the same recurring "stale tracker in the fixed direction" pattern documented
throughout `.claude/CLAUDE.md` — plus #23, swept same-day as this triage). The remaining 20 are
genuinely open, each with a specific, checkable reason recorded below.

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

## Still genuinely open — 20 items

Confirmed via a full listing of every `sweep_*`/`walk_forward_*` function in
`services/market-data/src/backtest/` — 9 functions exist total (up from 8 with #23's addition),
covering exactly the 7 items above plus `min_entry_score`, `min_kscore`/`min_ta_score`/
`min_volume_z`, `min_pillars_for_sell`/blocked-score-sets, and calibration-feedback-on/off.
The remaining 20 constants below either have no sweep (Groups A/B) or were individually
investigated and found structurally unsweepable with this codebase's current tooling
(Group C's 3 remaining items).

### Group A — decision-engine scoring/sizing constants (items 3,4,5,6,7,8,9,10,11,12,14,15)

`hard_rejects.py`'s breakout-extension floor (#3) and time-of-day gate windows (#4);
`sizer.py`'s research-score tiers/multipliers (#5), confidence-mult breakpoints (#6),
earnings-DTE size reduction (#7); `scorer.py`'s chasing penalty (#8), R:R quality tiers (#9),
volume_z asymmetric bands (#10), bull_prob thresholds (#11), confidence-delta thresholds (#12),
catalyst-score thresholds (#14), entry-zone drift tiering (#15).

**Why these are lower priority to sweep individually:** every one of these lives inside
`scorer.py`'s additive ±1/±2 SCORE layers or `sizer.py`'s illustrative-only position sizing —
neither of these functions gates a live entry outright (that's `hard_rejects.py`'s job); they
nudge a score that later crosses a THRESHOLD which itself has already been swept
(`min_entry_score`). A sweep of any ONE of these 12 constants in isolation, holding every other
scorer constant fixed, would only capture a fraction of the real effect — these constants
interact (e.g. the R:R tier boundary and the chasing penalty both fire on overlapping setups).
A meaningful validation here needs either (a) a joint multi-parameter sweep across the whole
scorer, which is a materially larger project than any single-parameter walk-forward sweep this
codebase has built so far, or (b) accepting these as **intentionally-arbitrary, hand-tuned
starting points** that the ALREADY-VALIDATED downstream `min_entry_score` threshold sweep
implicitly compensates for (a threshold set too loose/tight because of a slightly-off internal
scorer constant still gets corrected by the threshold's own validated value).

**Disposition: documented as intentionally arbitrary, not swept individually.** Revisit only if
a future joint scorer-calibration project is scoped (analogous to `tune_style_profiles`'s own
multi-parameter approach in signal-engine, which DOES jointly sweep 2+ parameters together) —
a single-parameter sweep here would not be worth the engineering cost for the statistical power
it would realistically deliver at current data volumes.

### Group B — kscore.py internal piecewise constants (items 17,18,19,20,21)

RSI-to-score breakpoints/slopes (#17), ADX-boost normalization (#18), volatility scale factor
(#19), value-proxy discount scale (#20), growth-proxy CAGR scale (#21). All 5 are UNTOUCHED by
`T288-KSCORE-WEIGHT-SWEEP`, which only validated the 6 top-level `_WEIGHTS` values, never the
internal formulas each sub-score is computed from.

**Disposition: documented as intentionally arbitrary.** These are curve-shape parameters
(piecewise slopes, scale factors converting a raw indicator into a 0-100 sub-score) rather than
gate thresholds — validating them needs a genuinely different methodology (comparing K-Score's
OWN predictive power against `signal_outcomes`/`squeeze_alert_outcomes` under alternative curve
shapes, not a simple threshold sweep) that doesn't fit the existing `walk_forward_*` harness
pattern without real new engineering. Lower priority than Group A since K-Score is already one
layer removed from `_should_enter()`'s own gates (it flows through `min_kscore`, which HAS
already been swept via `walk_forward_extended_gate`).

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
- `min_rr_ratio` (2.0 pass/fail) vs `scorer.py` tiers (2.5/3.5 "Acceptable"/"Excellent"): **still
  open**, same reasoning as Group A above — a scorer-tier constant, not swept individually.

## Summary

Of the original 27 items: **7 resolved** (verified against current code, one — #23 — swept the
same day as this triage), **Group C fully closed** (all 4 of its items now have a specific,
individually-investigated disposition — 1 swept, 3 found structurally unsweepable for distinct,
recorded reasons rather than merely deprioritized), **17 items remain genuinely open** across
Groups A (12) and B (5). All 17 are explicitly documented as intentionally-arbitrary starting
values rather than silently-unaddressed gaps. None were changed in this pass — per this
session's own established discipline, no live-decision-affecting parameter gets touched without
a real walk-forward validation, and building that validation for Group A/B in one pass was
judged disproportionate (Group A needs a joint multi-parameter sweep across `scorer.py`, a
materially larger project than any single-parameter sweep this codebase has built so far; Group
B needs a genuinely different validation methodology than the existing threshold-sweep harness).
If this work is picked up again, Group A's joint scorer sweep is the next real candidate — Group
C has no more low-hanging items left, and Group B needs new methodology design first.
