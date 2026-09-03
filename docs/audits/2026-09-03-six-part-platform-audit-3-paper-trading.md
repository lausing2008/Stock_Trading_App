## Deep Audit Series (2026-09-03): Paper Trading — 3 of 6

**Scope**: `services/market-data/src/services/paper_trading_engine.py` (6065 lines) — the actual
paper-trading MECHANICS: position exit/monitoring logic (`_monitor_positions`), stop-loss/
target/trailing-stop/breakeven-stop computation, broker order sync/polling
(`poll_broker_order_fills`, `sync_broker_positions`), equity curve / drawdown / vol-targeting,
portfolio-level circuit breakers, scale-in/position-scaling shadow verdicts, and gate-block /
no-entry-summary bookkeeping. Sequential platform audit series (AI Signal → Decision-Making →
**this domain** → Model Training → Short Squeeze Alerts → Options Trading & Alerts), per
`docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.

**Explicitly out of scope** (already covered by Domain 2 — Decision-Making): decision-engine's
own `scorer.py`/`hard_rejects.py`/`sizer.py`/`aggregator.py`, `_call_decision_engine()`'s
request/response wiring, `_should_enter()`'s entry-gate logic, and the full
`T232-DL-DUALSCORER-DEBT` parity sweep (already extensively covered across multiple prior
sessions). The 4-portfolio `min_confidence`/`min_entry_score` config fix from Domain 2 is
already live — not re-flagged here.

### Ground truth (queried directly against production before dispatching)

5 paper portfolios: id=1 GROWTH/US, id=2 SWING/HK, id=3 SWING/US, id=4 GROWTH/HK, id=5 SWING/US
(E*Trade sandbox). Per-portfolio win rate / avg `pct_return` (all trades): P1 35.0%/+0.13, P2
0.0%/-5.66 (only 4 trades total), P3 39.5%/+0.68, P4 46.7%/-0.04, P5 7.7%/-1.86.

Overall `exit_reason` breakdown (all portfolios, closed trades, unsplit by date): `stop_hit` 55
trades/25.5% win rate/-2.55 avg return; `breakeven_stop` 32 trades/12.5% win rate/-0.30 avg
return; `trailing_stop` 11 trades/100% win rate/+4.63; `target_reached` 7 trades/100%/+12.22;
`momentum_exit` 3 trades/0%/-0.55; `hold_stall_timeout` 1 trade/100%/+3.09; `signal_exit` 1
trade/0%/-4.46. The `stop_hit`/`breakeven_stop` numbers looked contradictory at first glance (a
"stop-loss" exit with a >0% win rate; a "breakeven" exit with a negative average return) —
flagged to the subagent as needing investigation, not assumed to be a bug.

### Headline findings

1. **CONFIRMED, FIXED — `poll_broker_order_fills()` never stopped re-polling a broker-entered
   position once its fill was confirmed.** The pending-orders query
   (`services/market-data/src/services/paper_trading_engine.py:317-322`, pre-fix) selected on
   `PaperTrade.broker_order_id.isnot(None)` as its "still needs polling" signal — but
   `broker_order_id` is set once (`_place_broker_entry`, line 219) and never cleared anywhere in
   this file. It can't simply be cleared on fill either: `_place_broker_exit` (line 250,
   `if not trade.broker_order_id: return`) independently relies on its mere PRESENCE to decide
   whether a position was broker-entered and therefore needs a real broker SELL order at exit
   time. Net effect: every broker-entered position, once opened, would be silently re-included
   in `poll_broker_order_fills()`'s pending-orders query and re-queried against the real broker
   API on every scheduler cycle (every 5-10 min) for its ENTIRE remaining holding period (up to
   20-90 days depending on style) — not just until its fill was confirmed. The
   `abs(fill_p - trade.entry_price) > 0.001` guard already in place prevents this from
   re-corrupting cash on each redundant poll, so there was no P&L-correctness impact — but it's
   an unbounded, silently-repeating real external API call per open broker position per cycle,
   for a position that has nothing left to poll for. Independently re-verified by me: read the
   full set/consume chain (`broker_order_id` set at line 219, read at lines 250/320/340/354/2896,
   never reassigned or cleared anywhere) — confirmed exactly as reported.
   **Fixed**: added a new `broker_fill_confirmed: bool` column to `PaperTrade` (defaults `false`),
   set `True` at both fill-detection sites (`_place_broker_entry`'s immediate-fill branch for
   sandbox fills, and `poll_broker_order_fills()`'s own later-fill branch), and added
   `PaperTrade.broker_fill_confirmed.is_(False)` to the pending-orders query.
   `broker_order_id` itself is left completely untouched, preserving `_place_broker_exit`'s
   existing contract. Migration:
   `scripts/migrations/012_add_broker_fill_confirmed_to_paper_trades.sql`.
   **Currently dormant, not yet observed firing live**: production has exactly 1 broker-linked
   portfolio (P5, E*Trade sandbox) and, verified directly, ZERO open trades currently carry a
   `broker_order_id` — so this bug was real and confirmed by code-path tracing, but has not
   actually been observed re-polling in practice. A future session should confirm the fix is
   working once a broker-linked portfolio holds an open position across more than one cycle (via
   logs — no repeated `get_order` call for an already-`broker_fill_confirmed` trade).
   **Deliberately NOT addressed in this fix** (a distinct, unverified question): a broker order
   that reaches a terminal `cancelled`/`rejected` status (rather than `filled`) is still not
   marked `broker_fill_confirmed` and would still be re-polled forever. What SHOULD happen to a
   paper trade whose real broker order never filled at all (does the position stay
   simulated-only permanently? should the user be alerted?) needs its own investigation — left
   as an open question, not folded into this fix, to avoid guessing at intended behavior.

2. **Informational, not a live bug — `paper_trading_step()`'s single try/except wraps all 5
   portfolios, so one portfolio's unguarded exception still aborts monitoring/scanning for every
   other portfolio that cycle.** (`:5977-6065`). This is the exact class of bug the file's own
   `BUG-MONITORPOS-NAIVEAWARE` comment (line 2813) already warns about and fixed for that one
   specific case — the structural risk that a FUTURE unguarded exception anywhere in any single
   portfolio's monitor/scan path takes down the whole cycle for all 5 portfolios remains
   architecturally unchanged. No other live land mine of this shape was found during this audit.
   Not fixed — recorded as a standing design-risk note, not an active defect, since fixing it
   pre-emptively (wrapping each portfolio's block in its own try/except inside
   `paper_trading_step()`) is a real, testable change that deserves its own deliberate pass
   rather than a quick patch bundled into this domain's other work.

### Checked and found CLEAN

- **The `stop_hit`/`breakeven_stop` "contradictory" aggregate stats are stale pre-fix history,
  not a live bug.** Traced to `AUD262-EXITREASON-CONFLATION-ROOT` and
  `AUD262-BREAKEVEN-COOLDOWN-60X-TOO-SHORT`, both already fixed and committed 2026-08-05
  (`d1a557c`) — a stop that had RATCHETED UP above entry before triggering was previously
  mislabeled `stop_hit`/`breakeven_stop` (both by comparing the stop LEVEL to entry, never the
  actual fill), contaminating both buckets with what were really profitable trailing exits.
  Independently re-verified by re-running the split myself directly against production
  (`entry_time` before/after the fix commit):
  - `stop_hit` pre-fix (49 trades): 28.6% win rate, range -16.77% to +13.96% (contaminated).
  - `stop_hit` post-fix (6 trades): **0% win rate, range -12.44% to -0.64%** — every one a
    genuine loss, zero contamination.
  - `breakeven_stop` pre-fix (27 trades): 14.8% win rate, range -5.18% to +2.36%.
  - `breakeven_stop` post-fix (5 trades): **0% win rate, range -0.38% to -0.17%** — small, tight,
    consistent with pure slippage on a near-flat exit, not a bug.
  The aggregate figures used to ground this audit (55/25.5%/-2.55 and 32/12.5%/-0.30) are
  dominated by pre-fix rows that can never be retroactively relabeled (the same
  "left as-is, no retroactive fix possible" pattern as Domain 1's `signal_outcomes` finding) —
  worth this note precisely so a FUTURE audit doesn't re-flag the same unsplit aggregate numbers
  without re-splitting by date first. Re-entry cooldown correctly scopes to
  `stop_hit`/`breakeven_stop` only and correctly excludes `trailing_stop` (a profitable exit),
  consistent with the label-conflation fix.
- P2 (HK SWING)'s 4-trade, 0%-win-rate, 2-month-dormant history: independently traced to a real
  but non-code BREADTH gap, not a bug. `_scan_for_entries`'s candidate-universe query
  (watchlist join filtered by `Watchlist.trading_style == style`, then
  `Signal.signal == 'BUY' AND Stock.market == cfg['market']`, `:4483-4537`) is working exactly
  as designed. Only 6 HK-market stocks total exist across any watchlist tagged
  `trading_style='SWING'` (watchlists id 77 and 181, out of 55 combined US+HK stocks) — verified
  live via `paper:no_entry_summary:2` in Redis (`candidates_seen: 0`, fresh) and confirmed all 6
  of those stocks currently carry non-BUY signals (SELL/WAIT/HOLD) at `horizon=SWING`. Recorded
  as a tracker-worthy coverage gap ("P2 has an effectively empty addressable universe most of
  the time"), not a defect to fix in code.
- `_compute_portfolio_drawdown` / `_compute_portfolio_vol_targeting_mult` — correctly wired and
  consumed (`vol_mult` folds into `regime_size_mult`), not dead code.
- Position-scaling shadow verdicts (`_record_position_scaling_shadow_verdict`,
  `resolve_position_scaling_shadow_verdicts`) — genuinely shadow-only; no live-money path exists
  yet, by design (per its own docstring). Dedup-per-symbol-per-day and the LREM-not-rebuild
  concurrency fix (`T241-AUDIT-WALKFORWARD-VALIDITY`) both correct.
- `_recent_win_rate` / `_consec_loss_streak` — dollar-based `pnl > 0`/`pnl < 0` comparisons, no
  falsy-zero risk.
- `_open_paper_trade`'s risk-dollar sizing math (independent multipliers × equity ×
  risk_per_trade_pct, 25%-of-base floor, HK board-lot rounding applied consistently at both the
  initial and max-position-cap recompute sites) — already extensively self-audited
  (`AUD262-FALLBACK-SIZES-LARGER-THAN-DE`, `AUD232-011`, `AUD262-HK-NO-BOARD-LOTS`), traced
  through again and found internally consistent.
- All portfolio-level circuit breakers (drawdown, daily loss, weekly loss/gain lock,
  consecutive-loss, equity floor, max-entries-per-day) — correctly gated behind
  `_gates_override`, correct fraction-vs-percent units.
- `resolve_entry_gate_params()` values match exactly what Domain 2 applied live — no drift
  between the two services' config sources.
- Broker error redaction (`_sanitize_broker_error`), buying-power pre-flight check
  (`_BROKER_BUYING_POWER_SAFETY_MARGIN = 0.95`), and token-rejection handling
  (`_handle_broker_error_if_token_rejected`) — all correctly fail-open/fail-safe per their
  documented contracts.
- `sync_broker_positions()` — correctly refuses to clobber manually-entered or
  other-connection-owned `UserPosition` rows; correctly removes rows it owns when the broker no
  longer reports them.

### Unusual Whales

No further wiring found in `paper_trading_engine.py` beyond what Domain 2 already established.
`squeeze_score` (short-squeeze composite) is computed and threaded into `_call_decision_engine`
— a live, wired soft-corroboration layer. `pressure_score` (options-pressure) is explicitly,
deliberately NOT computed or wired here either, for the identical reason Domain 2 found on the
decision-engine side (`cp_ratio`/whale-activity inputs would require a live options-chain
yfinance fetch this app's own rate-limit discipline forbids inside the hot per-candidate scan
loop) — a consistent, deliberate design choice across both services, not a divergence.
`congress_score`/`insider_score` (free-tier) are correctly consumed in `_should_enter()`'s
scoring — no UW dependency there either.

### What was NOT independently verified

- The `poll_broker_order_fills` fix's live behavior under a real repeating-poll scenario —
  production currently has zero open broker-order trades, so the fix's correctness rests on
  code-path tracing plus regression tests, not an observed live re-poll stopping. A future
  session should confirm via logs once a broker-linked position stays open across more than one
  cycle.
- Real E*Trade sandbox API rate limits were not benchmarked, so the practical severity of the
  pre-fix bug (trivial extra call vs. a genuine rate-limit risk for long-held positions) was not
  quantified — the fix closes the gap regardless of severity.

### What to check if this needs re-verifying

```bash
# Confirm the new column exists and is being set correctly on a real fill:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT symbol, broker_order_id, broker_fill_confirmed FROM paper_trades WHERE broker_order_id IS NOT NULL;\""

# Confirm poll_broker_order_fills' pending-orders query only returns unconfirmed fills:
grep -n "broker_fill_confirmed" services/market-data/src/services/paper_trading_engine.py

# Re-split stop_hit/breakeven_stop by date to confirm the label-conflation fix is still holding
# as more post-fix trades accumulate:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT exit_reason, CASE WHEN entry_time < '2026-08-05 20:20:18' THEN 'pre' ELSE 'post' END era, \
     COUNT(*), ROUND(100.0*COUNT(*) FILTER (WHERE pnl>0)/COUNT(*),1) win_pct \
     FROM paper_trades WHERE exit_price IS NOT NULL AND exit_reason IN ('stop_hit','breakeven_stop') \
     GROUP BY exit_reason, era ORDER BY exit_reason, era;\""
```
