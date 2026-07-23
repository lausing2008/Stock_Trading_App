"""T233-SELFIMPROVE-PHASE4: EV backtest gate for ML hyperparameter tuning.

See docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md for the promotion-gate precedent this
mirrors (market-data's gate_harness.py / promotion_gate.py), and the T233-SELFIMPROVE-PHASE4-5
tracker entry for why this was deferred until Phases 1-3 were proven.

Scope: tuner.py's Optuna search already optimizes a trading-relevant proxy (top-decile
precision, T232-ML5) rather than raw AUC — that half of Phase 4's original framing was already
done before this module existed. What was still missing: Optuna's own CV folds are internal to
the search itself, so a candidate's hyperparameters were never checked against a genuinely
held-out slice using an actual trading-EV metric before being persisted and used to retrain the
live model. This module adds that second, independent gate.

Design: tune_symbol() already carves off the last 15% of feature rows entirely
(`X, y_dir = X.iloc[:cutoff], y_dir.iloc[:cutoff]`, cutoff = 0.85) and never touches them again
— this is real, unused holdout data with real forward returns (`y_ret`, already computed by
build_features() and previously discarded at that same slice). No new data source or
regeneration is needed: refit the candidate params on the search slice, score the holdout, and
compute EV as mean forward return among holdout rows the model would have signaled (predicted
probability >= a fixed reference threshold approximating the live buy_threshold tail). Compare
against the CURRENT LIVE params refit and scored the exact same way on the exact same holdout —
matching this codebase's "must beat the current live baseline on data neither saw" convention
used by every other tuning mechanism (outcomes_calibrate_apply, tune_style_profiles,
calibrate_ml_weight, gate_harness.py).

Deliberately NOT a full portfolio backtest — no position sizing, no concurrent-position cap, no
transaction costs. It only asks "does this candidate's holdout EV among its own would-be-BUY
rows beat the live model's holdout EV among ITS would-be-BUY rows." A candidate that clears
this is a strictly better second opinion than Optuna's internal CV alone; it does not replace
gate_harness.py's own separate, later gate on live SignalOutcome rows.
"""
from __future__ import annotations

import numpy as np

# Reference probability threshold approximating production's buy_threshold tail (SWING/bull
# sits around 0.60-0.63 per _STYLE_PROFILES in signal-engine) — fixed here rather than sourced
# per-style/regime, since this gate only needs A consistent bar to compare candidate vs.
# baseline under, not to reproduce the live threshold's own tuning.
REFERENCE_PROB_THRESHOLD = 0.60

# A candidate must have at least this many holdout rows crossing the reference threshold for
# its EV to be considered measurable at all — otherwise both "beats baseline" and "loses to
# baseline" are equally noise from a tiny sample. Matches this codebase's established floor
# style (gate_harness.py's MIN_SAMPLES_PER_SPLIT=15) rather than inventing a new number.
MIN_HOLDOUT_SIGNALED_ROWS = 10


def compute_holdout_ev(probs: np.ndarray, y_ret: np.ndarray, threshold: float = REFERENCE_PROB_THRESHOLD) -> dict:
    """Given holdout predicted probabilities and their real forward returns, compute the mean
    forward return among rows that would have been signaled (prob >= threshold).

    Returns {"ev_pct": float | None, "n": int}. ev_pct is None when fewer than
    MIN_HOLDOUT_SIGNALED_ROWS rows cross the threshold — not a real 0.0, an unmeasurable value.
    """
    probs = np.asarray(probs)
    y_ret = np.asarray(y_ret)
    signaled = probs >= threshold
    n = int(signaled.sum())
    if n < MIN_HOLDOUT_SIGNALED_ROWS:
        return {"ev_pct": None, "n": n}
    return {"ev_pct": float(np.mean(y_ret[signaled]) * 100.0), "n": n}


def evaluate_candidate_ev(
    candidate_probs: np.ndarray,
    baseline_probs: np.ndarray | None,
    y_ret_holdout: np.ndarray,
    threshold: float = REFERENCE_PROB_THRESHOLD,
) -> dict:
    """Compare a candidate hyperparameter set's holdout EV against the current live params'
    holdout EV, both scored on the SAME holdout rows (so this is an apples-to-apples
    comparison, not two different samples).

    baseline_probs=None means no live model/params exist yet for this symbol (first-ever tune)
    — there is nothing to beat, so the candidate is promoted automatically (matches
    tune_symbol()'s own pre-existing behavior of always persisting on a first tune) and
    gate_failures records this explicitly rather than silently treating it as a pass.

    Returns a dict with candidate_ev, baseline_ev (each {"ev_pct", "n"} or None), promoted
    (bool), and gate_failures (list[str]).
    """
    candidate_ev = compute_holdout_ev(candidate_probs, y_ret_holdout, threshold)
    gate_failures: list[str] = []

    if baseline_probs is None:
        gate_failures.append("no_baseline_params:first_tune_for_symbol")
        return {
            "candidate_ev": candidate_ev,
            "baseline_ev": None,
            "promoted": True,
            "gate_failures": gate_failures,
        }

    baseline_ev = compute_holdout_ev(baseline_probs, y_ret_holdout, threshold)

    if candidate_ev["ev_pct"] is None:
        gate_failures.append(f"candidate_ev_unmeasurable:only_{candidate_ev['n']}_signaled_rows")
        return {
            "candidate_ev": candidate_ev,
            "baseline_ev": baseline_ev,
            "promoted": False,
            "gate_failures": gate_failures,
        }

    if baseline_ev["ev_pct"] is None:
        # Baseline params exist but signal too rarely on this holdout to compare against —
        # a candidate that DOES clear the sample floor is a strict improvement in measurability
        # alone; promote, but record why no head-to-head comparison was possible.
        gate_failures.append(f"baseline_ev_unmeasurable:only_{baseline_ev['n']}_signaled_rows")
        return {
            "candidate_ev": candidate_ev,
            "baseline_ev": baseline_ev,
            "promoted": True,
            "gate_failures": gate_failures,
        }

    ev_lift = candidate_ev["ev_pct"] - baseline_ev["ev_pct"]
    if ev_lift <= 0:
        gate_failures.append(f"ev_lift_not_positive:{ev_lift:.4f}pp")
        return {
            "candidate_ev": candidate_ev,
            "baseline_ev": baseline_ev,
            "promoted": False,
            "gate_failures": gate_failures,
        }

    return {
        "candidate_ev": candidate_ev,
        "baseline_ev": baseline_ev,
        "promoted": True,
        "gate_failures": gate_failures,
    }
