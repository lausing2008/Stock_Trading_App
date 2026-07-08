# Design: Extending `tune_history` to the Other 5 Tuning Mechanisms

**Status:** Design + grounding research complete, implementation starting. Extends
`docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md`, which built `tune_history` scoped to exactly one
mechanism (`min_entry_score`, via `services/market-data/src/backtest/promotion_gate.py`). This
document scopes wiring the remaining 5 mechanisms from `docs/SELF_IMPROVEMENT_LOOP.md` §2's table
into the same table.

---

## 1. What's being extended, and where each one actually lives

| # | Mechanism | Service | Function | Has DB session already? |
|---|---|---|---|---|
| 1 | Signal threshold calibration | signal-engine | `outcomes_calibrate_apply` (`routes.py:3400`) | Yes |
| 2 | Style gate-parameter tuning | signal-engine | `tune_style_profiles` (`routes.py:3675`) | Yes |
| 3 | ML fusion weight | signal-engine | `calibrate_ml_weight` (`routes.py:1051`) — already fixed this session (T234-ML-WEIGHT-NO-VALIDATION-GATE) to have a real validation gate | Yes |
| 4 | ML hyperparameters | ml-prediction | `tune_symbol`/`POST /ml/tune_all` | Needs checking |
| 5 | Gate-logic drift check | signal-engine | `gate_backtest` (`routes.py:4800`) | Yes, but this one never *applies* anything — pure comparison tool, no promote/reject decision exists to record |

**Key finding: `TuneHistory` is a shared model (`shared/db/models.py`), importable from any service via
`from db import TuneHistory` — no cross-service HTTP call needed.** Confirmed this is architecturally
consistent: signal-engine already imports directly from `db` (`routes.py:13`), same pattern as
market-data's `promotion_gate.py`. Each service writes its own rows directly via its own
`SessionLocal`/`get_session()` dependency, all landing in the same physical table.

## 2. Granularity mismatch — `market` dimension doesn't apply uniformly

The Phase 3 harness (`gate_harness.py`) is scoped per `(style, market)` — `TuneHistory.market` is a
required column. But mechanisms #1-#3 sweep per `horizon`/`style` ONLY, pooling US+HK signals
together with no market split at all (confirmed: `outcomes_calibrate_apply`'s and
`tune_style_profiles`'s loops are `for h in ("SHORT","SWING","LONG","GROWTH")`, no market dimension
anywhere in the sweep). Writing a fake `"US"` or `"HK"` into `TuneHistory.market` for these would
misrepresent what was actually measured (a US+HK-pooled result, not a US-only or HK-only one).

**Decision: use `market="ALL"` for mechanisms #1-#3** — an explicit sentinel meaning "not
market-split," not a claim about a specific market. `TuneHistory.market` stays a plain string column
(not an enum), so this doesn't require a schema change. Document this convention in the column's
model docstring so a future reader of the table isn't confused by `"ALL"` appearing alongside real
`"US"`/`"HK"` values from the `min_entry_score` mechanism.

## 3. Per-mechanism recording point (where exactly to insert the write)

### `outcomes_calibrate_apply` (#1)

Two independent sweeps (BUY and SELL) over the same 4 horizons, both already loop with multiple
`continue` (skip) branches and one success (`applied.append(...)` / `sell_applied.append(...)`)
branch per horizon. **Write one `tune_history` row per horizon per direction, at the same point
each loop iteration currently appends to `applied`/`skipped`/`sell_applied`/`sell_skipped`** — this
means up to 8 rows per call (4 horizons × 2 directions), each independently promoted or not, matching
the granularity the function already tracks internally. `parameter_class="signal_threshold"`,
`parameter_name="buy_threshold"` or `"sell_threshold"`.

### `tune_style_profiles` (#2)

Per-style loop already tracks 3 independent params (`ml_weight_cap`, `adx_min`,
`breadth_compression`) with separate `applied.append(...)`/`skipped.append(...)` calls for each.
**Write one row per style per param actually evaluated** (up to 4 styles × 3 params = 12 rows per
call, though most calls will produce far fewer since most params skip on insufficient data).
`parameter_class="gate_threshold"` (matches Phase 3's convention for gate-adjacent params),
`parameter_name` = the specific param name.

### `calibrate_ml_weight` (#3)

Single sweep, single decision (`applied: true/false` for one global cap) — already fixed this
session to have `candidate_validation_ev_pct`/`baseline_validation_ev_pct`/`gate_failures`-shaped
data in its return dict. **Write exactly one row per call**, directly reusing the fields already
computed by the T234-ML-WEIGHT-NO-VALIDATION-GATE fix — this mechanism needed the LEAST new code
since its return shape already matches `tune_history`'s columns closely.

### ML hyperparameters (#4) — deferred, not implemented in this pass

`ml-prediction`'s `tune_all`/`tune_symbol` optimizes AUC via Optuna per-symbol (not per-style/market
like the others) — hundreds of symbols, each an independent Optuna study. Writing one `tune_history`
row per symbol per `tune_all` run would add hundreds of rows per weekly run for a mechanism that:
(a) doesn't yet have a validation-EV gate at all (confirmed earlier this session —
`T232-ML5-OPTUNA-WRONG-METRIC`, still open, tracks that Optuna optimizes AUC not P&L), and (b) per
the original design's own Phase 4 sequencing, is deliberately meant to get the backtest-harness
treatment LAST, after gate thresholds are proven. Recording untuned-by-EV Optuna results into the
same history table as the other 3 EV-gated mechanisms would blur two very different kinds of
"tuning" together. **Not extended in this pass** — revisit once Phase 4 (ML hyperparameter
backtesting) exists, so there's an actual EV-based promote/reject decision worth recording, not just
an AUC number.

### `gate_backtest` (#5) — deferred, not implemented, for a different reason

This endpoint is a pure research/comparison tool — it replays OLD vs. NEW conviction-gate logic
against historical signals and reports the difference. It has no "apply" step, no config it writes
to, and no promote/reject decision at all (confirmed: no Redis write, no config mutation anywhere in
the function). There is nothing to record to `tune_history` because nothing is ever tuned by this
endpoint — it's closer to `gate_harness.py`'s `replay_should_enter()` (a building block) than to a
promotion decision. **Not extended** — not a gap, just not applicable to this table's purpose.

## 4. What does NOT change

- `TuneHistory`'s schema (from Phase 3) needs no new columns — `parameter_class`/`parameter_name`
  already generalize across mechanisms, and `old_value`/`new_value` are JSON, flexible enough for
  each mechanism's different shape (a float weight, a threshold, an integer ADX level).
- No mechanism's own internal gating logic changes — this is purely additive recording, inserted at
  the exact point each mechanism already decides "apply" or "skip."
- `approx_worst_trade_pct`/`baseline_worst_trade_pct` (Phase 3's approximate drawdown check) stay
  NULL for mechanisms #1-#3 — none of them have a per-trade returns list to compute a worst-trade
  check from (they operate on aggregate win-rate/EV statistics, not individual trade records like
  `gate_harness.py` does). Leaving these NULL is correct, not a gap — the column is specific to the
  one mechanism that has the underlying data for it.
