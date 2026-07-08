# Design: Promotion Gate + Tune History — Phase 3 Scoping (Self-Improvement Loop)

**Status:** Design + grounding research complete, implementation starting. Supersedes §3c/§3d/
§4-Phase-3 of `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` with concrete findings from reading
the actual harness code, following the same "verify before building" discipline as
`docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md`. Phase 2a (the harness itself) is done —
`services/market-data/src/backtest/gate_harness.py`.

---

## 1. What the original design proposed vs. what's actually buildable now

The Phase 1 design doc (§3c) proposed 4 promotion-gate rules:

1. Positive expected-value lift on the held-out validation window.
2. Minimum trade-count sample size.
3. No increase in max drawdown beyond a tolerance.
4. Agreement between `SignalOutcome`-based and `PaperTrade`-based backtests where both apply.

Checking `gate_harness.py` directly (not from memory of the design doc):

- **Rule #1 is already implemented.** `walk_forward_min_entry_score()`'s `promoted` field is exactly
  this rule — `best_val.avg_return_pct > baseline_val.avg_return_pct` on the validation slice.
- **Rule #2 is already implemented**, as a floor rather than a comparison — `MIN_SAMPLES_PER_SPLIT =
  15` gates every `replay_should_enter()` call.
- **Rule #3 is NOT buildable as originally scoped.** `replay_should_enter()` tracks only a flat list
  of per-trade percentage returns, discarded after computing the mean — there is no equity curve,
  no concept of position sizing, and no concurrent-position accounting. The real
  `max_portfolio_drawdown_pct` circuit breaker in `paper_trading_engine.py` (line ~2511) computes
  drawdown against a running PEAK of actual dollar equity across a whole portfolio with concurrent
  positions and cash drag — a fundamentally different computation than "the biggest peak-to-trough
  dip in a list of independent per-trade returns." A faithful version needs the full bar-by-bar
  equity-curve replay already deferred to **Phase 2b** in the harness design doc. Building a
  synthetic proxy now (e.g. treating each entered trade as if compounded sequentially) would be
  a genuinely different, weaker statistic wearing the same name — worth having as an approximate
  signal, but must be labeled as such, not presented as the real portfolio drawdown check.
- **Rule #4 is NOT buildable at all yet.** There is no `PaperTrade`-based backtest — only the
  `SignalOutcome`-based one (Phase 2a). "Agreement between the two" requires the second backtest to
  exist first, which is Phase 2b, not Phase 3.

**Consequence for scope:** Phase 3 (this document) implements rules #1/#2 (already proven, just
needs a place to be recorded) plus a clearly-labeled APPROXIMATE version of rule #3 (return-sequence
drawdown, not portfolio-equity drawdown). Rule #4 is deferred until Phase 2b exists — tracked, not
silently dropped, matching this session's established pattern for scope corrections.

---

## 2. `tune_history` table

Per the original design's proposed shape (§3d), adjusted to match what's actually produced by
`walk_forward_min_entry_score()`'s return dict rather than an idealized shape:

```python
class TuneHistory(Base):
    __tablename__ = "tune_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)   # uuid4, groups multi-style runs
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    parameter_class: Mapped[str] = mapped_column(String(32))      # "gate_threshold" for Phase 3
    parameter_name: Mapped[str] = mapped_column(String(64))       # "min_entry_score"
    style: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(8))
    old_value: Mapped[dict] = mapped_column(JSON)                 # {"min_entry_score": 4}
    new_value: Mapped[dict] = mapped_column(JSON)                 # {"min_entry_score": 3}
    train_window_start: Mapped[date] = mapped_column(Date)
    train_window_end: Mapped[date] = mapped_column(Date)
    validation_window_start: Mapped[date] = mapped_column(Date)
    validation_window_end: Mapped[date] = mapped_column(Date)
    train_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_validation_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approx_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # see §3, labeled approximate
    promoted: Mapped[bool] = mapped_column(Boolean)
    gate_failures: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["min_sample_size", "drawdown_regression"]
    triggered_by: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled (Phase 5)
```

Every call to the Phase 3 wrapper writes exactly one row — promoted or not — mirroring the design
doc's explicit requirement that a rejected candidate is still recorded (so "we tried X and it didn't
help" is visible, not silently discarded). This directly targets the CAL-1 failure mode: a corrupted
threshold that reached production with no trace of what changed or why.

---

## 3. Promotion Gate logic (Phase 3 cut)

New function, `services/market-data/src/backtest/promotion_gate.py`:

```python
def evaluate_and_record(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    max_drawdown_regression_pct: float = 10.0,  # tolerance, matches the design doc's "10% relative" example
    triggered_by: str = "manual",
) -> dict:
    """Run walk_forward_min_entry_score(), apply the drawdown check, and write ONE
    tune_history row regardless of outcome. Returns the harness result plus the gate verdict.
    """
```

Gate checks, run in this order (matches rule numbering above):

1. **EV lift** (already computed by the harness) — `promoted` from `walk_forward_min_entry_score`.
2. **Min sample size** (already enforced by the harness) — surfaces as `skipped_reason` if either
   slice fails; treated as a gate failure, not silently absent from history.
3. **Approximate drawdown regression** (NEW, labeled approximate): compute the largest single-step
   percentage decline within the candidate's validation-slice per-trade returns list vs. the
   baseline's — reject if the candidate's worst single-trade loss is more than
   `max_drawdown_regression_pct` percentage points worse than baseline's worst single-trade loss.
   This is deliberately NOT a compounded equity-curve drawdown (see §1) — it only asks "does this
   candidate's worst individual trade look meaningfully worse than the current config's worst
   individual trade," a much narrower question than true portfolio drawdown, but a real signal
   worth having until Phase 2b makes the real one possible.
4. **SignalOutcome/PaperTrade agreement** — NOT IMPLEMENTED, explicit `"not_yet_available"` marker
   in `gate_failures` output whenever this would apply, rather than silently omitting the check.

`promoted` in the final output requires checks #1 AND #2 AND #3 to all pass. Check #4 never blocks
promotion in Phase 3 (it can't — no second backtest exists to compare against) but its
unavailability is always recorded in the row, so a future reader of `tune_history` knows this
promotion was NOT cross-validated against `PaperTrade` outcomes, rather than assuming silently that
it was.

**Still manually triggered.** Per the original design's explicit sequencing (§4, Phase 5 comes only
after Phases 1-4 are proven manually), this Phase 3 cut adds NO scheduler wiring and does NOT
auto-apply the promoted config to `portfolio.config` — it writes history and reports a verdict; a
human still decides whether to hand-edit the live config. This matches Phase 2a's existing posture
(`GET /paper-portfolio/backtest/min-entry-score` — read-only research tool) exactly.

---

## 4. Endpoint

```
POST /paper-portfolio/backtest/min-entry-score/promote?style=SWING&market=US&window_days=60
```

Admin-only (same `get_admin_user` dependency as the existing harness endpoint). Runs
`evaluate_and_record()`, returns the same JSON shape `walk_forward_min_entry_score` already
returns, plus the drawdown-check result and the `tune_history` row id that was written.

```
GET /paper-portfolio/tune-history?style=SWING&market=US&limit=50
```

Read-only browse of the `tune_history` table — the piece that directly answers "what changed, when,
and did it help" without log spelunking, per the original design's core motivation.

---

## 5. Explicitly deferred (tracked, not forgotten)

- **Real portfolio-equity drawdown check** (rule #3, faithful version) — blocked on Phase 2b's full
  `_scan_for_entries` bar-by-bar equity-curve replay.
- **`SignalOutcome`/`PaperTrade` agreement check** (rule #4) — blocked on a `PaperTrade`-based
  backtest existing at all; not proposed until Phase 2b or a dedicated Phase 2b-adjacent build.
- **Scheduler automation** (Phase 5) — deliberately not touched in Phase 3; still requires a human
  to trigger and to act on a promoted result.
- **Extending `tune_history`/the gate to the other 5 tuning mechanisms** (signal thresholds, style
  gate params, ML fusion weight, ML hyperparameters, gate-logic drift check) — this document scopes
  Phase 3 to the ONE mechanism that already has a backtest harness (`min_entry_score` via Phase 2a).
  The other mechanisms already have their own walk-forward gates (fixed earlier this session) but
  don't yet write to a shared history table — a natural follow-up once this pattern is proven here.
