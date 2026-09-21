# Deferred audit items batch — 2026-09-21

**Purpose:** this session's 2026-09-19 work (see
[docs/audits/2026-09-19-session-index.md](2026-09-19-session-index.md)) deliberately deferred
several real findings — PT-H01's and PT-H03's own recommended fixes (both would have changed
live-trading behavior with no validation framework in place), and the broader UW/broker/email
roadmap items (B03/B06-B12, UW-02-09, E05/E07/E09/E11/E13/E14). On 2026-09-21 the user asked to
build the deferred items after all. This doc records what was actually built, and what remains
deliberately unbuilt and why.

Commits, in order: `4a64d7b` (PT-H08) → `8f576f6` (A01-A03 doc fold-in) → `00de461` (PT-H03) →
`3062e94` (PT-H01) → `19ffcea` (UW-07) → `86ba59b` (E09).

## 1. PT-H08 — persistent gate-block/no-entry decision records

**[docs/audits/2026-09-19-paper-trading-horizon-audit-review.md](2026-09-19-paper-trading-horizon-audit-review.md)**
(the review that first flagged this as "arguably the single highest-leverage item in the whole
audit")

The only record of why a paper-trading scan blocked entries lived in
`paper:gate_block:{id}`/`paper:no_entry_summary:{id}` — Redis keys with a 4-hour TTL. By the
time anyone actually asks "why isn't this trading," the evidence is gone — this session hit that
exact wall twice already (investigating portfolio 6, and re-verifying AUD-MINRR-STYLEBLIND's own
claimed impact). `PaperEntryScanLog` (migration 016) makes the same data durable: `_write_gate_
block()`/`_write_no_entry_summary()` now also persist a row, fail-silent exactly like the Redis
writes they accompany. 90-day retention purge added to `scheduler.py`'s existing
`_purge_old_data()`. 7 new tests, 3 sabotage cycles, all caught.

## 2. PT-H03 — `_monitor_positions()`'s stale config merge, fixed without moving open positions' stops

**[docs/audits/2026-09-19-paper-trading-horizon-audit-review.md](2026-09-19-paper-trading-horizon-audit-review.md)**

Real, confirmed bug: `_monitor_positions()` built its own config every monitoring cycle with the
exact merge `resolve_entry_config()` replaced on the entry side on 2026-09-07
(`AUD-DE1-CONFIGMERGE`) — never applying `_HK_MARKET_OVERRIDES` at all, and letting a style
override be silently defeated by a portfolio.config value that merely echoes the generic
default. Not fixed by simply swapping the resolver in place — that would have silently moved
trailing-stop distance, partial-TP triggers, and hold-day exits for every currently OPEN
position the instant the fix deployed, since those are recomputed from `cfg` every monitoring
cycle, not frozen at entry (the audit's own example: an HK trailing ATR multiplier moving
2.0x → 1.5x mid-trade). `PaperTrade.exit_config_snapshot` (migration 017) freezes
`resolve_entry_config()`'s output at the moment each trade opens; `_monitor_positions()` reads a
per-trade `_trade_cfg = trade.exit_config_snapshot or cfg` for every exit-relevant decision (20
call sites migrated). Every trade already open before this deploy has no snapshot and keeps the
exact old merge via that fallback — byte-identical monitoring behavior. 7 new tests (plus 2
pre-existing tests updated for the renamed variable, same underlying assertion), 3 sabotage
cycles, all caught.

## 3. PT-H01 real fix — explicit `min_rr_ratio_mode`, not accidental dict-key-presence

**[docs/audits/2026-09-19-paper-trading-horizon-audit-review.md](2026-09-19-paper-trading-horizon-audit-review.md)**

The audit's own recommended remediation for PT-H01 (make the R:R-floor resolution explicit
rather than an accident of `dict.get()` semantics) was deliberately NOT taken as originally
proposed — swapping the resolver so calibration always wins would change the effective R:R
floor for all 11 live portfolios at once, with no experiment/validation framework in place,
exactly the class of unvetted live-trading change this session's own `AUD-MINRR-STYLEBLIND`
mistake and the audit review both warned against.

Built instead: `resolve_min_rr_ratio(cfg)` / `resolve_regime_min_rr_ratio(cfg, regime_state)`, a
new `min_rr_ratio_mode` config key. `"manual"` (default — every existing portfolio, since none
set this key) is byte-identical to today's actual resolved values. `"calibrated"` is new and
opt-in: ignores any stored numeric value outright and always uses the calibrated default. **No
live portfolio's resolved floor changes as a result of this commit** — the single most important
guarantee, verified directly by `test_the_exact_pt_h01_scenario_all_11_live_portfolios_resolve_
unchanged` (the real live config shape against a real calibrated override value on disk,
asserting the resolved value stays 2.0). 12 new tests, 2 sabotage cycles — the second (flip the
default mode from `"manual"` to `"calibrated"`) is the single most important sabotage of this
whole session, since it's exactly the failure mode the fix exists to prevent, and it was caught
cleanly.

## 4. UW-07 — a broken UW feed no longer looks identical to a genuinely empty response

**[docs/audits/2026-09-18-uw-and-broker-report-review.md](2026-09-18-uw-and-broker-report-review.md)**

A research pass over the full remaining deferred list (B03/B06-B12, UW-02-09,
E05/E07/E09/E11/E13/E14) confirmed nearly all of it is correctly scoped as large — each needs a
new durable data model (order-intent/fill lifecycle, outbox pattern, feature mart, immutable
delivery ledger) or real broker/vendor testing. Two items were genuinely small, additive slices.

UW-07: every one of `unusual_whales.py`'s ~20 public getters wraps its own `_get()` call in a
bare `except Exception: return None`/`[]`, collapsing "the feed is broken"
(disabled/rate_limited/unauthorized/error) and "this symbol legitimately has nothing to say"
(no_data) into the exact same shape — `_get()` itself already distinguishes these internally.
Deliberately NOT a rewrite of all ~20 consumers (real, larger, separate work). `_get()` now also
records the outcome of its own most recent real call (mirrors `_record_usage_headers()`'s own
pattern exactly), and `get_uw_last_call_status()` reads it back. No existing function's
signature, return type, or behavior changes. 28 tests (9 new), 2 sabotage cycles — one caught a
real self-introduced bug in the restore step itself (a `sed` replace accidentally reverted the
200-success path's own "ok" label instead of only the sabotaged 404 line), fixed and
re-verified.

## 5. E09 — a failed alert email no longer silently burns its own retry window

**[docs/audits/2026-09-18-alert-email-accuracy-and-uw-playbooks-audit.md](2026-09-18-alert-email-accuracy-and-uw-playbooks-audit.md)**

`check_options_flow_alerts()`/`check_dark_pool_alerts()` both claim their per-(user,
symbol[, direction/print]) Redis cooldown key via `nx=True` SET *before* attempting the actual
send. `send_email()` never raises on failure — it returns a plain bool — so a failed send left
the cooldown key standing exactly as if the alert had been delivered, silently suppressing a
real notification for the full cooldown window with no way to tell the difference from "already
sent." Both jobs now track exactly which cooldown keys the current attempt actually claimed (a
real, successful `nx=True` SET only) and delete them when `send_ok` is `False`, so the very next
scheduler cycle can retry. A successful send leaves the cooldown in place, unchanged. 9 new
tests, 1 sabotage cycle, caught cleanly.

## What remains deliberately unbuilt

- **B03, B06–B12** (broker order lifecycle: client-order-IDs, scale-in/out reconciliation,
  partial-fill tracking, account reservation, options adapter support) — each needs a durable
  order-intent/fill data model and real broker-paper testing, not a bounded code change.
- **UW-02 through UW-06, UW-08, UW-09** (options-flow data-source migration, outcome
  measurement redesign, feature-mart design, archive partitioning, calibration methodology) —
  data-migration and measurement-design roadmap items, not single-function bugs.
- **E05, E07, E11, E13, E14** — each needs a genuine new data model (evidence schema, immutable
  delivery ledger, subscription/preference model) to fix correctly, not a label change.

All for the same reason: building any of these without their own real design/validation pass
would repeat the exact mistake `AUD-MINRR-STYLEBLIND` already made once this session — shipping
a plausible-sounding fix that either doesn't address the real problem or silently changes live
behavior with nothing in place to tell whether the result is better or worse.
