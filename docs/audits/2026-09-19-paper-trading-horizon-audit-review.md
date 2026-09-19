# Review: Paper Trading Engine Horizon Thresholds and Mechanism Audit

**Reviewed 2026-09-19.** Subject: [2026-09-19-paper-trading-horizon-threshold-audit.md](2026-09-19-paper-trading-horizon-threshold-audit.md)
(review dates September 18-19, checkout `7fb03da`). This is an external, independent audit —
not one this session commissioned — that surfaced in the repository and was reviewed on
request. Method: direct re-derivation of PT-H01/H02/H03 against current running code and live
production data (not just reading the audit's own claims), plus a spot-check of PT-H04.
PT-H05-H09 were read in full and found internally consistent with the rest of this codebase's
own documented history, but were not independently re-derived line-by-line against source —
flagged below wherever that distinction matters.

## Verdict: PT-H01 is confirmed, and it corrects work this session shipped and deployed earlier today

This is the most important outcome of this review. The audit's PT-H01 finding — that the
calibrated `min_rr_ratio` is not actually the effective floor used by any real portfolio scan —
directly contradicts the root-cause diagnosis behind **AUD-MINRR-STYLEBLIND**, a fix built,
tested, and deployed to production earlier in this same session. That correction is now
recorded in full at
[docs/incidents/self-tuning-job-performance-bugs.md](../incidents/self-tuning-job-performance-bugs.md)
(the "CORRECTION" block appended to that entry) and in the improvements tracker. Summary:

- `_DEFAULT_CONFIG["min_rr_ratio"] = 2.0` is a literal. No entry in `_STYLE_OVERRIDES` or
  `_HK_MARKET_OVERRIDES` sets `min_rr_ratio` — verified by grep, zero occurrences outside
  `_DEFAULT_CONFIG` and the handful of read sites.
- All 11 active production portfolios store `min_rr_ratio: 2.0` explicitly — verified live:
  `SELECT id, config->>'min_rr_ratio' FROM paper_portfolios WHERE is_active` returned `2.0` for
  every row.
- `resolve_entry_config()`'s merge therefore always leaves the key **present** in the resolved
  config at `2.0`. `cfg.get("min_rr_ratio", _default_min_rr_ratio(...))` only falls through to
  its default argument when the key is **absent** — never when it's merely equal to what the
  fallback would have produced. Verified directly against the running container:
  `resolve_entry_config({"trading_style": "SWING", "market": "US", "min_rr_ratio": 2.0})["min_rr_ratio"]`
  returns `2.0`.
- The one place the by-style fix has a genuine live effect is `regime_min_rr_ratio` (the
  choppy/risk_off-only stiffened floor), which IS genuinely absent from `_DEFAULT_CONFIG` and so
  correctly falls through to the calibrated value. The base-floor half of the fix — the half
  that would have mattered in the bull/neutral regime SWING was actually in — never took effect
  on any real scan, before or after deployment.
- This session's own "confirmation" of the 2.25 theory (a direct `_decide()` call showing
  `"R:R 2.18 below minimum 2.2"`) used `config_overrides={"market": "US"}` with no
  `min_rr_ratio` key at all — exactly decision-engine's documented "standalone caller, no
  overrides" fallback path (`routes.py`'s own `T234-CONFIG-DECIDE-DEFAULT-MISMATCH` comment),
  which DOES read calibration. The real trading path (`_call_decision_engine()`) never reaches
  that branch, since it always sends `min_rr_ratio` explicitly. The reproduction was real and
  reproducible — just not representative of what a live scan actually does.

**AUD-MINRR-STYLEBLIND is not being reverted.** Its own logic is correct and it remains a real
improvement to the calibration diagnostic and to the regime-stiffened floor. But it did not
resolve — and could not have resolved — the reported symptom (US SWING portfolios not trading).
Why SWING actually stopped entering on 2026-09-03/04 is now **unestablished**: the
`paper:gate_block:{id}`/`paper:no_entry_summary:{id}` Redis diagnostics that would show the real
per-scan rejection reason carry a 4-hour TTL and had long since expired by the time either this
session or the new audit looked (PT-H08 makes the identical observation independently).

## PT-H02 — confirmed by direct source read

`hard_rejects.py`'s R:R check is exactly as described: `min_rr = cfg.get("min_rr_ratio", 2.0)`,
and the candidate-cap/regime-floor reconciliation (`_candidate_rr_ceiling`, the
`min(_regime_floor, _ceiling)` logic) only executes inside
`if regime_state in ("choppy", "risk_off")`. In a neutral/bull regime — confirmed to be the
regime state at the time this session investigated SWING — the check collapses to
`rr < cfg.get("min_rr_ratio", 2.0)`, i.e. `rr < 2.0` given PT-H01. Both real candidates this
session found "blocked" earlier today (MU, NBIS, R:R 2.18) would **pass** a 2.0 floor with room
to spare. R:R was very likely never the actual blocker for those specific candidates in a
neutral/bull regime — reinforcing PT-H01's point that the true rejection reason is unrecovered,
not simply "a slightly different R:R number."

## PT-H03 — confirmed by direct source read; a real, structural, NOT-tuning bug

`_monitor_positions()` builds its own config with a plain, unconditional merge:

```python
cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(portfolio.config.get("trading_style", "GROWTH"), {}), **portfolio.config}
```

This is the exact merge `resolve_entry_config()` (used by the entry path) had until
`AUD-DE1-CONFIGMERGE` (2026-09-07) replaced it — the one where an HK/style override could be
silently defeated by a stored value that merely echoes the generic default, and where
`_HK_MARKET_OVERRIDES` is applied at all. `_monitor_positions()` never received that fix: it
still has the old merge, and it **never applies `_HK_MARKET_OVERRIDES`** — confirmed by grep,
there is no reference to that dict anywhere in the function. This is a genuine
parallel-implementation-drift bug (the same anti-pattern flagged elsewhere this session for the
broker close-flow paths): two functions computing what should be the same effective config,
one already fixed, one silently left on the old logic.

**Why this is not fixed in this pass despite being confirmed:** unlike a typical
parallel-implementation-drift fix, swapping `_monitor_positions()` onto `resolve_entry_config()`
would immediately change real stop/trailing/breakeven behavior for every **currently open**
position (the audit's own examples: portfolio 1 moves from 3%/5% to 4%/7%; portfolio 3 from
3%/5% to 1.5%/3%; every HK portfolio's trailing ATR multiplier from 2.0 to 1.5). The audit's own
solution explicitly flags this: "Define whether later settings apply to open trades; changes to
existing stops must be deliberate." Fixing the resolver without first deciding (and probably
without freezing each trade's own exit-policy snapshot at entry time, per the audit's own
recommendation) risks silently moving real stops under open positions the moment the image
rebuilds. This is exactly the kind of change that needs its own deliberate decision, not a
same-pass bundle with the merge-consistency fix.

## PT-H04 — spot-checked, confirmed

Portfolio 891's `stop_pct_override`/`atr_stop_mult_override` are `0.925`/`2.5` as stated,
confirmed via direct query. The nominal-R:R-drops-to-1.6-when-the-width-cap-binds arithmetic
follows from the same stop-geometry math this session already verified for the standard styles
this session's other calibration work.

## PT-H05 through PT-H09 — read, internally consistent, not independently re-derived

These describe: UW expected-move horizon mismatch (PT-H05), exit-threshold reachability gaps
(PT-H06), calibration methodology limitations — pooling, split design, sample-size floors
(PT-H07), the same gate-block-cache-expiry observation this review's PT-H01 section also makes
independently (PT-H08), and scale-in/accounting complications for threshold attribution
(PT-H09). Each names specific functions/fields (`options_game_plan_snapshot.py`'s `IV *
sqrt(30/365)`, `_default_game_plan`'s partial-TP levels vs `max_hold_days`, `scale_in_enabled`
defaulting `True` with no whitelisted portfolio setting it explicitly) that are plausible given
this session's own reading of adjacent code, but were not re-traced end-to-end the way
PT-H01-H04 were. Treat these as credible leads, not re-confirmed findings, until someone walks
the same source-and-live-data verification this review applied to PT-H01-H04.

## What this review does NOT do, and why

The audit's own top-line recommendation is explicit: **"There is not yet a demonstrated
profit-maximizing threshold for any horizon... Do not raise every threshold to increase
'accuracy,' or lower every threshold merely to produce trades."** Its section 6-8 propose a
whole experiment framework (frozen candidate streams, isolated virtual books, purged
train/validation splits, promotion gates requiring 100+ resolved trades and 30+ distinct entry
sessions per policy) before any threshold change should be trusted. Implementing PT-H01's own
recommended fix (a canonical, explicit manual/calibrated threshold resolver) would, by itself,
change the effective base R:R floor for all 11 live portfolios — a real live-trading behavior
change with no experiment/validation framework in place to tell whether the result is better or
worse. That is precisely the unvetted-threshold-change risk this document, this session's own
`AUD-MINRR-STYLEBLIND` mistake, and this project's broader audit history all converge on. It is
being scoped, not built, in this pass — matching this session's established practice for
findings that need their own deliberate process (see the A01-A03 broker lifecycle scoping and
the four-audit implementation batch's own deferred items for precedent).

## What is left as a going-forward pointer

- **PT-H01's real fix** (explicit manual/calibrated threshold mode, resolved once, before all
  entry/exit/reporting consumers) is real, high-priority, and unbuilt.
- **PT-H03's real fix** (`_monitor_positions()` reusing `resolve_entry_config()`) is real and
  bounded in code size, but needs an explicit decision about existing open positions before it
  ships — not a same-pass bundle.
- **PT-H08's gate-block persistence** (durable per-scan decision records, not a 4-hour Redis
  cache) would make the NEXT "why isn't this portfolio trading" question answerable in minutes
  instead of unrecoverable after a few hours — arguably the single highest-leverage item in the
  whole audit, since it is infrastructure that makes every other threshold question testable
  going forward, rather than a threshold value itself.
- The full section-6/7/8 experiment framework remains the audit's own stated prerequisite for
  any future threshold change, and is not attempted here.
