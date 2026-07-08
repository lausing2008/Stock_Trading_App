"""T233-SELFIMPROVE-PHASE3: Promotion Gate + tune_history recording for the gate-threshold
backtest harness.

See docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md for the full design and scoping rationale.

Scope (deliberately narrower than the original 4-rule design — see the design doc §1 for why):
  1. Positive EV lift on the validation slice — already computed by gate_harness.py's
     walk_forward_min_entry_score() as `promoted`. Reused here, not recomputed.
  2. Minimum sample size — already enforced by gate_harness.py's MIN_SAMPLES_PER_SPLIT.
  3. Approximate worst-single-trade check (THIS MODULE) — NOT a portfolio equity-curve
     drawdown. It only asks "is the candidate's worst individual trade meaningfully worse
     than the current config's worst individual trade" on the validation slice. A faithful
     portfolio drawdown check needs Phase 2b's full bar-by-bar equity-curve replay (position
     sizing, concurrent positions, cash drag) — not built yet. Labeled "approx" throughout,
     including in the tune_history column name, so nobody mistakes this for the real thing.
  4. SignalOutcome/PaperTrade agreement — NOT IMPLEMENTED. No PaperTrade-based backtest
     exists yet to compare against. Always recorded as an explicit gate_failures entry
     ("not_yet_available") rather than silently omitted, so a future reader of tune_history
     knows this promotion was never cross-validated against real trade outcomes.

Still manually triggered — this module does not write to portfolio.config and does not run on
a schedule (Phase 5). It writes one tune_history row per call, promoted or not, and returns a
verdict for a human to act on.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.orm import Session

from db import TuneHistory

from .gate_harness import MIN_SAMPLES_PER_SPLIT, replay_should_enter, walk_forward_min_entry_score

# Tolerance for the approximate worst-trade check: reject a candidate whose worst single
# validation-slice trade is more than this many percentage points worse than the baseline's
# worst trade. Matches the "10% relative" example tolerance from the original design doc's
# rule #3 — kept as an absolute percentage-point gap here since these are already pct returns,
# not a starting-equity-relative figure a true drawdown check would use.
DEFAULT_MAX_WORST_TRADE_REGRESSION_PCT = 10.0


def evaluate_and_record(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    max_worst_trade_regression_pct: float = DEFAULT_MAX_WORST_TRADE_REGRESSION_PCT,
    triggered_by: str = "manual",
) -> dict:
    """Run the Phase 2a harness, apply the approximate worst-trade check, and write ONE
    tune_history row regardless of outcome. Returns the harness result plus the gate verdict.
    """
    style = style.upper()
    market = market.upper()
    run_id = str(uuid.uuid4())

    harness_result = walk_forward_min_entry_score(session, style, market, base_cfg, window_start, window_end)

    gate_failures: list[str] = []
    # SignalOutcome/PaperTrade agreement (rule #4) is never checkable yet — always record its
    # absence rather than silently omitting it, per this module's own docstring.
    gate_failures.append("not_yet_available:signal_outcome_papertrade_agreement")

    if harness_result.get("skipped_reason"):
        gate_failures.append(f"harness_skipped:{harness_result['skipped_reason']}")
        _write_history(
            session, run_id, style, market, base_cfg, window_start, window_end,
            harness_result=harness_result, worst_trade_check=None,
            promoted=False, gate_failures=gate_failures, triggered_by=triggered_by,
        )
        return {**harness_result, "run_id": run_id, "promoted": False, "gate_failures": gate_failures}

    if not harness_result["promoted"]:
        gate_failures.append("ev_lift_not_positive_on_validation")

    # Rule #3: recompute just the two validation-slice replays to get their raw per-trade
    # returns (walk_forward_min_entry_score's dict output doesn't carry BacktestResult.returns).
    # Same window/cfg as what the harness already validated — deterministic, not a new search.
    total_days = (window_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    candidate_score = harness_result["candidate_min_entry_score"]
    candidate_val = replay_should_enter(
        session, style, market, {**base_cfg, "min_entry_score": candidate_score}, val_start, window_end,
        cfg_label=f"min_entry_score={candidate_score} (validation, worst-trade check)",
    )
    baseline_val = replay_should_enter(
        session, style, market, base_cfg, val_start, window_end,
        cfg_label="baseline (validation, worst-trade check)",
    )

    worst_trade_check = None
    if candidate_val.returns and baseline_val.returns:
        candidate_worst = min(candidate_val.returns)
        baseline_worst = min(baseline_val.returns)
        regression = baseline_worst - candidate_worst  # positive = candidate's worst trade is worse
        worst_trade_check = {
            "candidate_worst_trade_pct": round(candidate_worst, 4),
            "baseline_worst_trade_pct": round(baseline_worst, 4),
            "regression_pct": round(regression, 4),
            "within_tolerance": regression <= max_worst_trade_regression_pct,
        }
        if not worst_trade_check["within_tolerance"]:
            gate_failures.append(
                f"worst_trade_regression:{regression:.2f}pp exceeds tolerance "
                f"{max_worst_trade_regression_pct:.2f}pp"
            )
    else:
        gate_failures.append("worst_trade_check_unavailable:insufficient_entered_trades")

    promoted = (
        harness_result["promoted"]
        and worst_trade_check is not None
        and worst_trade_check["within_tolerance"]
    )

    _write_history(
        session, run_id, style, market, base_cfg, window_start, window_end,
        harness_result=harness_result, worst_trade_check=worst_trade_check,
        promoted=promoted, gate_failures=gate_failures, triggered_by=triggered_by,
    )

    return {
        **harness_result,
        "run_id": run_id,
        "promoted": promoted,
        "worst_trade_check": worst_trade_check,
        "gate_failures": gate_failures,
    }


def _write_history(
    session: Session,
    run_id: str,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    harness_result: dict,
    worst_trade_check: dict | None,
    promoted: bool,
    gate_failures: list[str],
    triggered_by: str,
) -> None:
    total_days = (window_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    current_score = base_cfg.get("min_entry_score", 4)
    candidate_score = harness_result.get("candidate_min_entry_score")
    train_result = harness_result.get("train_result") or {}
    candidate_validation = harness_result.get("candidate_validation") or {}
    baseline_validation = harness_result.get("baseline_validation") or {}

    row = TuneHistory(
        run_id=run_id,
        parameter_class="gate_threshold",
        parameter_name="min_entry_score",
        style=style,
        market=market,
        old_value={"min_entry_score": current_score},
        new_value={"min_entry_score": candidate_score} if candidate_score is not None else {},
        train_window_start=window_start,
        train_window_end=train_end,
        validation_window_start=val_start,
        validation_window_end=window_end,
        train_ev_pct=train_result.get("avg_return_pct"),
        validation_ev_pct=candidate_validation.get("avg_return_pct"),
        baseline_validation_ev_pct=baseline_validation.get("avg_return_pct"),
        validation_n=candidate_validation.get("n_entered"),
        approx_worst_trade_pct=(worst_trade_check or {}).get("candidate_worst_trade_pct"),
        baseline_worst_trade_pct=(worst_trade_check or {}).get("baseline_worst_trade_pct"),
        promoted=promoted,
        gate_failures=gate_failures,
        triggered_by=triggered_by,
    )
    session.add(row)
    session.commit()
