# T234 Threshold Triage — 2026-08-26

Re-verified all 27 items from `AUDIT_REPORT_TIER234_2026-07-04.md` Part 2 against current code
before deciding disposition. 6 already resolved by prior sessions (not previously cross-referenced
back to this list — the same recurring "stale tracker in the fixed direction" pattern documented
throughout `.claude/CLAUDE.md`). The remaining 21 are genuinely still open.

## Already resolved (verified against current code, not assumed)

| # | Item | Resolution |
|---|---|---|
| 1 | `min_confidence` cross-file mismatch (45.0 vs 62.0) | `T234-CONFIG-DECIDE-DEFAULT-MISMATCH` — real endpoint (`GET /stocks/entry-gate-params`) resolves the correct per-style/market value; `hard_rejects.py`'s 62.0 fallback is now confirmed-dead code with a comment explaining why |
| 2 | `regime_min_rr_ratio` fallback = 3.0 | Now resolved via `_default_min_rr_ratio()`, calibration-aware (reads `min_rr_calibration.json` when a real calibration exists, falls back to 3.0 only when none does) — not a bare uncited literal |
| 13 | Signal freshness 4h/18h vs 72h order-of-magnitude mismatch | Investigated and found NOT actually a conflict: 72h is a hard reject (`max_signal_age_hours`), 4h/18h is a *soft ±1 score nudge* on a completely different axis — confirmed via the T232-DL series' own re-verification, documented in CLAUDE.md |
| 16 | `kscore.py` `_WEIGHTS` (the 6 top-level factor weights) | `T288-KSCORE-WEIGHT-SWEEP` — real walk-forward-validated, held-out-tested weight search with a promotion gate; ships a validated override via Redis when the sweep finds a real edge |
| 22 | `max_portfolio_drawdown_pct` = 0.20 | `AUD293`'s `sweep_max_portfolio_drawdown_pct()` — real walk-forward sweep with the standard chronological train/validation split + promotion-margin gate |
| — | `risk_per_trade_pct` (not itemized individually in the original list but same class) | `sweep_risk_per_trade_pct()` — same walk-forward discipline |

## Still genuinely open — 21 items, none have any sweep infrastructure

Confirmed via a full listing of every `sweep_*`/`walk_forward_*` function in
`services/market-data/src/backtest/` — only 8 functions exist total, covering exactly the 6
items above plus `min_entry_score`, `min_kscore`/`min_ta_score`/`min_volume_z`,
`min_pillars_for_sell`/blocked-score-sets, and calibration-feedback-on/off. None of the
remaining 21 constants below have ever been swept.

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

### Group C — paper_trading_engine.py standalone constants (items 23,24,26,27)

`max_open_risk_pct` = 0.12 (#23), `hold_stall_days`/`hold_stall_max_gain` = 30d/5% (#24),
HK `regime_suspension_days` = 7 (#26), `min_stop_dist` floor (#27).

**Disposition: documented as intentionally arbitrary, individually lower-leverage than Group A.**
None of these 4 currently has sweep infrastructure. `max_open_risk_pct` is the closest candidate
to warrant a future sweep (it's a real portfolio-wide circuit breaker, same class as the
already-swept `max_portfolio_drawdown_pct`) — worth prioritizing FIRST if this triage is
revisited, using `sweep_max_portfolio_drawdown_pct()` as the direct template. The other 3 are
narrower single-purpose gates (a stall-exit timer, an HK-specific suspension window, a stop-
distance floor) with less capital at stake per occurrence.

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

Of the original 27 items: **6 resolved** (verified against current code), **21 remain
genuinely open**, all now explicitly documented as intentionally-arbitrary starting values
rather than silently-unaddressed gaps. None were changed in this triage — per this session's
own established discipline, no live-decision-affecting parameter gets touched without a real
walk-forward validation, and building that validation for all 21 remaining items in one pass
was judged disproportionate to the task (most are low-leverage soft-score nudges, not hard
gates). The single highest-leverage NEXT candidate, if this work is picked up again, is
`max_open_risk_pct` (#23) — same class and template as the already-completed
`max_portfolio_drawdown_pct` sweep.
