"""Signal-engine self-tuning/calibration routes.

T233-ARCH-INSERVICE-SPLITS: extracted from routes.py's original 6,289 lines (see routes.py's
own module docstring for the full split rationale). This file holds every mechanism that
tunes/calibrates signal-generation parameters against historical outcomes: ML weight
calibration, TA/conviction weight calibration, style-profile/strategy grid tuning, the
watchdog and its self-tuning diagnostic report, and outcome-threshold calibration. Verbatim
extraction — no logic changes; a bug found here was already present before the split.
"""
import os as _os
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.jwt_auth import get_current_username
from db import Price, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame, TuneHistory, get_session

from .signals_shared import (
    _CONF_CAL_CACHE_KEY, _CONF_CAL_MIN_COUNT, _OUTCOME_HOLD_DAYS,
    _get_confidence_calibration, _get_redis, _mark_tuned, _record_tune_history, _redis_get_float,
    _tuning_staleness, log,
)

router = APIRouter(prefix="/signals", tags=["signals"])


def _clear_watchdog_override(style: str) -> None:
    """AUD263-WATCHDOG-MASKS-VALIDATED-THRESHOLD: _get_dynamic_buy_threshold() reads
    stockai:watchdog:{style}:threshold BEFORE stockai:signal_thresholds:{style} — a validated,
    walk-forward-tested threshold write from outcomes_calibrate_apply/tune_strategy could
    previously be silently shadowed by a stale, unvalidated watchdog emergency nudge until
    that nudge's own 7-day TTL happened to expire. Concrete scenario this fixes: Sunday
    outcomes_calibrate_apply validates SWING at 0.68 on a held-out slice; Monday the watchdog
    sees a rolling win-rate dip and writes 0.71; Wednesday tune_strategy validates and writes
    0.66 — under the old read priority, that 0.66 would never take effect until the watchdog's
    key expired on its own.

    A validated recalibration is a genuinely new baseline for the watchdog to react from, not
    something it should keep silently overriding — call this immediately after any fresh,
    validated buy_threshold write so the watchdog's prior state (both the threshold override
    and its escalation counter) doesn't outlive the recalibration it was reacting to. The
    watchdog can always re-tighten from this new baseline on its own next daily run if the
    underlying win-rate problem is still real — this only clears a now-stale premise, it does
    not disable the watchdog going forward.
    """
    try:
        redis_client = _get_redis()
        had_override = redis_client.get(f"stockai:watchdog:{style}:threshold") is not None
        redis_client.delete(f"stockai:watchdog:{style}:threshold")
        redis_client.delete(f"stockai:watchdog:{style}:tighten_count")
        if had_override:
            log.info("signal_watchdog.override_cleared_by_validated_recalibration", style=style)
    except Exception as exc:
        log.warning("signal_watchdog.override_clear_failed", style=style, error=str(exc))

# ── SELFIMPROVE-WATCHDOG-SELF-TUNING ────────────────────────────────────────────
# signal_watchdog()'s own meta-parameters (38% win-rate floor, +0.03/-0.02 step size, 15
# min-samples, 3x max-tighten) are exactly as hardcoded and never-revisited as any of the base
# trading parameters the watchdog exists to correct. Depended on SELFIMPROVE-NO-RETRO-FEEDBACK-
# LOOP (backfill_realized_ev(), above) existing first — this report reads the realized_ev_pct_
# after column that job populates.
#
# Deliberately a READ-ONLY diagnostic report, not an auto-tuning job that mutates the watchdog's
# actual parameters. The tracker's own fix description asks to "compute whether tighten actions'
# realized win-rate improved vs. relax actions, and whether max_tighten_review is hit often with
# win rate still below floor" — that's diagnostic analysis for a human to review (matching the
# existing GET /tune_status precedent), not a decision rule mature enough to safely automate.
# Auto-tuning the tuner is a materially bigger, riskier step than this item's own Phase-2 framing
# implies — better to surface the data first and let a human decide if/how to act on it.
_WATCHDOG_STEP = 0.03      # signal_watchdog()'s hardcoded tighten step, mirrored here for context
_WATCHDOG_RELAX_STEP = 0.02


def _watchdog_action_kind(old_value: dict, new_value: dict) -> str | None:
    """tighten (threshold went up) vs relax (went down) vs unknown (malformed row)."""
    try:
        old_thr = float(old_value.get("threshold"))
        new_thr = float(new_value.get("threshold"))
    except (TypeError, ValueError, AttributeError):
        return None
    if new_thr > old_thr:
        return "tighten"
    if new_thr < old_thr:
        return "relax"
    return None


@router.get("/watchdog_self_tuning_report")
def watchdog_self_tuning_report(
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Diagnostic report on signal_watchdog()'s own historical effectiveness, grouped by style.

    For every promoted=True, triggered_by="watchdog" TuneHistory row with realized_ev_pct_after
    populated (i.e. enough real SignalOutcome data has accumulated since the change to trust a
    verdict — see backfill_realized_ev()'s own wait-floor logic), classifies the action as
    tighten or relax (by comparing old_value/new_value's threshold) and reports:
      - mean realized EV% for tighten actions vs. relax actions, per style
      - how often max_tighten_reached_manual_review_needed fires with the 14d win rate still
        below the 38% floor at the time (suggesting +0.03 may be too small a step)
    Read-only — never mutates any watchdog state. See this endpoint's own module comment above
    for why this is diagnostic-only rather than an auto-tuning job.
    """
    rows = session.execute(
        select(TuneHistory).where(
            TuneHistory.triggered_by == "watchdog",
            TuneHistory.parameter_name == "watchdog_buy_threshold",
            TuneHistory.promoted.is_(True),
            TuneHistory.realized_ev_pct_after.is_not(None),
        )
    ).scalars().all()

    by_style: dict[str, dict] = {}
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        style_rows = [r for r in rows if r.style == style]
        tighten_evs = []
        relax_evs = []
        for r in style_rows:
            kind = _watchdog_action_kind(r.old_value or {}, r.new_value or {})
            if kind == "tighten":
                tighten_evs.append(r.realized_ev_pct_after)
            elif kind == "relax":
                relax_evs.append(r.realized_ev_pct_after)

        # max_tighten_reached_manual_review_needed is logged as an `action` in signal_watchdog's
        # in-memory response but NEVER written to TuneHistory (it's a no-op branch — no
        # threshold actually changes when the cap is hit) — so it can't be queried back from
        # this table. Report what CAN be measured from TuneHistory: how many of this style's
        # promoted tighten actions themselves indicate the step size struggled — i.e. a tighten
        # action whose OWN realized_ev_pct_after is still negative, meaning even after applying
        # +0.03 the resulting period was still a net loser on average.
        weak_tightens = sum(1 for ev in tighten_evs if ev < 0)

        by_style[style] = {
            "n_tighten_actions": len(tighten_evs),
            "n_relax_actions": len(relax_evs),
            "mean_realized_ev_pct_after_tighten": round(sum(tighten_evs) / len(tighten_evs), 3) if tighten_evs else None,
            "mean_realized_ev_pct_after_relax": round(sum(relax_evs) / len(relax_evs), 3) if relax_evs else None,
            "n_weak_tightens": weak_tightens,
            "weak_tighten_note": (
                f"{weak_tightens}/{len(tighten_evs)} tighten actions still had negative realized EV "
                f"after applying the +{_WATCHDOG_STEP} step — may indicate the step size is too small"
            ) if weak_tightens else None,
        }

    return {
        "watchdog_step": _WATCHDOG_STEP,
        "watchdog_relax_step": _WATCHDOG_RELAX_STEP,
        "by_style": by_style,
        "n_total_realized_rows": len(rows),
    }


@router.get("/ml-weight-validation")
def ml_weight_validation(
    lookback_days: int = Query(180, ge=30, le=730),
    session: Session = Depends(get_session),
):
    """Empirically sweep ML fusion weights 0→1 to find which blend best predicted price direction.

    For each BUY signal in the lookback window, reads ml_probability and ta_score from the
    reasons JSON, pairs with the actual price outcome, then tries 21 weight values (0.00 to 1.00
    in 0.05 steps). Returns accuracy and avg_return_pct at each weight so the caller can see
    the empirical optimum vs the current formula range (0.40–0.75).
    """
    import bisect

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"lookback_days": lookback_days, "signal_count": 0, "curve": [], "optimal_weight": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def _first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None, None
        return _pclose[sid][idx], ts_list[idx]

    def _most_recent_close(sid):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        return _pclose[sid][-1], ts_list[-1]

    # Build list of (ml_prob, ta_score, pct_change) for signals with complete data
    observations: list[tuple[float, float, float]] = []
    seen: set[tuple] = set()

    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)

        reasons = sig.reasons or {}
        ml_prob = reasons.get("ml_probability")
        ta_score = reasons.get("ta_score")
        if ml_prob is None or ta_score is None:
            continue

        entry, entry_date = _first_close_after(sig.stock_id, signal_date)
        exit_p, exit_date = _most_recent_close(sig.stock_id)
        if entry is None or exit_p is None or exit_date is None or exit_date <= signal_date:
            continue
        if entry <= 0:
            continue

        pct = (exit_p - entry) / entry * 100
        observations.append((float(ml_prob), float(ta_score), pct))

    if not observations:
        return {"lookback_days": lookback_days, "signal_count": 0, "curve": [], "optimal_weight": None}

    # Sweep weight from 0.0 to 1.0 in 0.05 steps
    weights = [round(w / 20, 2) for w in range(21)]  # 0.00, 0.05, ..., 1.00
    curve = []
    best_acc = -1.0
    optimal_weight = 0.5

    for w in weights:
        correct = 0
        returns = []
        fired = 0
        for ml_p, ta_s, pct in observations:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                fired += 1
                if pct > 0:
                    correct += 1
                returns.append(pct)

        acc = round(correct / fired * 100, 1) if fired else None
        avg_ret = round(sum(returns) / len(returns), 2) if returns else None

        curve.append({"weight": w, "accuracy": acc, "avg_return_pct": avg_ret})

        if acc is not None and acc > best_acc:
            best_acc = acc
            optimal_weight = w

    return {
        "lookback_days": lookback_days,
        "signal_count": len(observations),
        "optimal_weight": optimal_weight,
        "optimal_accuracy": round(best_acc, 1),
        "current_formula_range": [0.40, 0.75],
        "curve": curve,
    }


@router.post("/calibrate_ml_weight")
def calibrate_ml_weight(
    lookback_days: int = Query(180, ge=30, le=730),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Find the empirically optimal ML fusion weight and apply it as the global cap.

    Runs the same weight sweep as /ml-weight-validation, searches for the weight with the
    highest BUY accuracy on the calibration (train) slice, then only applies it if it ALSO
    beats a neutral baseline (weight=0.5) on the held-out validation slice — writes to
    ml_weight_override.json and updates the in-process value only when validated.
    Returns the chosen weight (or None if nothing validated) and the full accuracy curve.
    """
    from ..generators.signals import set_ml_weight_global_cap, _ml_weight_global_cap as prev_cap
    import bisect

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"applied": False, "reason": "no_signals", "optimal_weight": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def _first_close_at_or_after(sid, target_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_left(ts_list, target_date)
        if idx >= len(ts_list):
            return None
        return _pclose[sid][idx]

    def _first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None
        return _pclose[sid][idx]

    # T234-ML-WEIGHT-NO-VALIDATION-GATE: exit_p previously used whatever the MOST RECENT close
    # happened to be, mixing holding periods from days to ~180 days (lookback_days) into the same
    # sweep — a signal evaluated the day it fired and one evaluated 6 months later were treated as
    # equally-measured observations. Now uses each signal's own style-specific fixed hold window
    # (_OUTCOME_HOLD_DAYS, the same convention outcomes_calibrate_apply/tune_style_profiles already
    # use), so every observation measures the same kind of thing: return AFTER the horizon this
    # signal was actually meant to be held for.
    observations: list[tuple[float, float, float, object]] = []
    seen: set[tuple] = set()
    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)
        reasons = sig.reasons or {}
        ml_prob = reasons.get("ml_probability")
        ta_score = reasons.get("ta_score")
        if ml_prob is None or ta_score is None:
            continue
        ts_list = _pts.get(sig.stock_id)
        if not ts_list:
            continue
        entry = _first_close_after(sig.stock_id, signal_date)
        if entry is None or entry <= 0:
            continue
        hold_days = _OUTCOME_HOLD_DAYS.get(sig.horizon.value, 14)
        target_exit_date = signal_date + timedelta(days=hold_days)
        exit_p = _first_close_at_or_after(sig.stock_id, target_exit_date)
        if exit_p is None:
            continue  # hold window hasn't closed yet — not a resolved observation
        pct = (exit_p - entry) / entry * 100
        observations.append((float(ml_prob), float(ta_score), pct, signal_date))

    if not observations:
        return {"applied": False, "reason": "no_observations", "optimal_weight": None}

    # Sort by date, split older 70% for calibration, newer 30% for validation
    observations.sort(key=lambda x: x[3])
    split = max(1, int(len(observations) * 0.7))
    calib_obs = observations[:split]
    val_obs = observations[split:]

    MIN_VAL_SAMPLES = 15  # same floor already proven in T232-OC3 / T234-SIG-INSAMPLE-GATE-TUNING

    weights = [round(w / 20, 2) for w in range(21)]
    best_acc = -1.0
    optimal_weight = 0.5
    curve = []

    def _accuracy_and_return(obs, w):
        correct = fired = 0
        returns = []
        for ml_p, ta_s, pct, _ in obs:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                fired += 1
                returns.append(pct)
                if pct > 0:
                    correct += 1
        acc = correct / fired if fired else None
        avg_ret = sum(returns) / len(returns) if returns else None
        return acc, fired, avg_ret

    for w in weights:
        # Select weight using calibration set only
        calib_acc, _, _ = _accuracy_and_return(calib_obs, w)
        if calib_acc is not None and calib_acc > best_acc:
            best_acc = calib_acc
            optimal_weight = w

        # Curve accuracy shown on validation set (display only, same as before)
        v_acc, v_fired, v_avg_ret = _accuracy_and_return(val_obs, w)
        curve.append({
            "weight": w,
            "accuracy": round(v_acc * 100, 1) if v_acc is not None else None,
            "avg_return_pct": round(v_avg_ret, 2) if v_avg_ret is not None else None,
        })

    # T234-ML-WEIGHT-NO-VALIDATION-GATE: only apply optimal_weight if it ALSO beats a neutral
    # baseline (0.5 — equal TA/ML blend) on the validation slice the search never saw. Previously
    # set_ml_weight_global_cap() ran unconditionally regardless of what validation showed.
    import uuid as _uuid
    _run_id = str(_uuid.uuid4())
    _train_end = calib_obs[-1][3] if calib_obs else date.today()
    _train_start = calib_obs[0][3] if calib_obs else date.today()
    _val_end = val_obs[-1][3] if val_obs else date.today()
    _val_start = val_obs[0][3] if val_obs else date.today()

    if len(val_obs) < MIN_VAL_SAMPLES:
        _record_tune_history(
            session, _run_id, "ml_fusion_weight", "ml_weight_global_cap", "ALL", "ALL",
            old_value={"ml_weight_global_cap": prev_cap}, new_value={"ml_weight_global_cap": optimal_weight},
            train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
            train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
            validation_n=len(val_obs), promoted=False,
            gate_failures=[f"insufficient_validation_samples:{len(val_obs)}<{MIN_VAL_SAMPLES}"],
        )
        return {
            "applied": False,
            "reason": f"only {len(val_obs)} validation-slice observations (need {MIN_VAL_SAMPLES})",
            "optimal_weight": optimal_weight,
            "signal_count": len(observations),
            "lookback_days": lookback_days,
            "curve": curve,
        }

    candidate_acc, candidate_fired, candidate_avg_ret = _accuracy_and_return(val_obs, optimal_weight)
    baseline_acc, baseline_fired, baseline_avg_ret = _accuracy_and_return(val_obs, 0.5)

    candidate_ev = (candidate_avg_ret or 0.0)
    baseline_ev = (baseline_avg_ret or 0.0)
    validated = (
        candidate_fired >= MIN_VAL_SAMPLES
        and candidate_acc is not None
        and candidate_ev > baseline_ev
    )

    if not validated:
        _record_tune_history(
            session, _run_id, "ml_fusion_weight", "ml_weight_global_cap", "ALL", "ALL",
            old_value={"ml_weight_global_cap": prev_cap}, new_value={"ml_weight_global_cap": optimal_weight},
            train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
            train_ev_pct=None, validation_ev_pct=round(candidate_ev, 2) if candidate_fired else None,
            baseline_validation_ev_pct=round(baseline_ev, 2) if baseline_fired else None,
            validation_n=candidate_fired, promoted=False,
            gate_failures=["ev_lift_not_positive_on_validation"],
        )
        return {
            "applied": False,
            "reason": "candidate weight did not beat the 0.5 baseline on the validation slice",
            "optimal_weight": optimal_weight,
            "candidate_validation_ev_pct": round(candidate_ev, 2) if candidate_fired else None,
            "baseline_validation_ev_pct": round(baseline_ev, 2) if baseline_fired else None,
            "signal_count": len(observations),
            "lookback_days": lookback_days,
            "curve": curve,
        }

    set_ml_weight_global_cap(optimal_weight)
    log.info("calibrate_ml_weight: applied cap=%.2f (val_acc=%.1f%%, val_ev=%.2f%%, n=%d, lookback=%dd)",
             optimal_weight, (candidate_acc or 0.0) * 100, candidate_ev, len(observations), lookback_days)
    _record_tune_history(
        session, _run_id, "ml_fusion_weight", "ml_weight_global_cap", "ALL", "ALL",
        old_value={"ml_weight_global_cap": prev_cap}, new_value={"ml_weight_global_cap": optimal_weight},
        train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
        train_ev_pct=None, validation_ev_pct=round(candidate_ev, 2),
        baseline_validation_ev_pct=round(baseline_ev, 2), validation_n=candidate_fired,
        promoted=True, gate_failures=[],
    )

    return {
        "applied": True,
        "optimal_weight": optimal_weight,
        "optimal_accuracy": round((candidate_acc or 0.0) * 100, 1),
        "candidate_validation_ev_pct": round(candidate_ev, 2),
        "baseline_validation_ev_pct": round(baseline_ev, 2),
        "signal_count": len(observations),
        "lookback_days": lookback_days,
        "previous_cap": prev_cap,
        "curve": curve,
    }


@router.post("/calibrate_ta_weights")
def calibrate_ta_weights(
    lookback_days: int = Query(365, ge=60, le=730),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Fit logistic regression on historical BUY signal_outcomes to derive data-driven TA weights.

    AUD232-048/049: previously recomputed forward returns independently (its own bisect-based
    entry/exit lookup against a single fixed `hold_days` param applied uniformly to every
    horizon, with a bare fwd_ret > 0 win rule) instead of reading the already-computed,
    already-persisted signal_outcomes table — the same source calibrate_conviction_weights (in
    this same file) correctly uses. Now reads SignalOutcome.is_correct directly, so the label
    matches evaluate_signal_outcomes' per-horizon hold window and cost-hurdle definition
    (_OUTCOME_WIN_HURDLE_PCT) exactly, instead of silently disagreeing with every other win-rate
    number in the calibration loop.

    Extracts TA boolean features from the signal's stored reasons JSON, then fits a logistic
    regression model. The resulting coefficients (clipped to [0, ∞]) become the new TA weights
    and are written to ta_weights.json next to the ML models directory.

    BUG233-TAWEIGHTS-NOVALIDATION (2026-07-31): this function previously (a) fed rows in
    arbitrary DB-heap order into TimeSeriesSplit, which assumes chronological order — the
    reported "walk-forward" in_sample_accuracy was therefore not a walk-forward number in any
    real sense, just a cross-validation score computed over an unordered shuffle; and (b) wrote
    production weights to disk/Redis/the live process UNCONDITIONALLY, fit on the FULL sample
    with zero held-out check against the CURRENT live weights' own accuracy — the only mutation
    path in this file with no baseline comparison, no promotion gate, and no TuneHistory record
    at all. A badly-overfit 16-feature fit at n=30-50 could go live with nothing to catch it.
    Fixed to match every sibling mechanism's own chronological-split + validation-beats-baseline
    + TuneHistory convention (calibrate_ml_weight above is the closest precedent: an accuracy-
    based fit using validation-slice mean return as its comparable EV-shaped metric, recorded
    via _record_tune_history exactly like every threshold sweep in this file).

    Returns the fitted weights (if validated) and validation-slice accuracy for review.
    """
    import json
    from pathlib import Path

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        raise HTTPException(status_code=500, detail="scikit-learn not installed in signal-engine")

    from ..generators.signals import (
        _TA_WEIGHTS_DEFAULT,
        _TA_WEIGHTS_PATH,
        _ta_weights as _current_live_ta_weights,
        set_ta_weights,
    )

    cutoff = date.today() - timedelta(days=lookback_days)
    rows = session.execute(
        select(SignalOutcome.is_correct, SignalOutcome.pct_return, Signal.reasons, SignalOutcome.signal_date)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.pct_return.is_not(None),
            Signal.reasons.is_not(None),
        )
        # Chronological order is load-bearing: TimeSeriesSplit assumes the input is already
        # time-ordered — feeding it unordered rows (the pre-fix behavior) makes its CV folds
        # meaningless, and this fix's own train/validation split below depends on it too.
        .order_by(SignalOutcome.signal_date)
    ).all()

    if len(rows) < 50:
        raise HTTPException(status_code=400, detail=f"Need ≥50 evaluated BUY outcomes, found {len(rows)}")

    # TA boolean feature names (positive weights only — penalties excluded from regression)
    # AUD232-045: "volume_z" here (not the legacy "volume_surge") — matches _TA_WEIGHTS_DEFAULT's
    # key name directly instead of relying on set_ta_weights()'s migration fallback to rename it
    # after the fact. Fitting/writing the correct key name at the source means the freshly
    # calibrated volume weight is never at risk of being silently dropped (the migration only
    # renames "volume_surge" -> "volume_z" when "volume_z" isn't already present).
    TA_FEATURES = [
        "above_sma50", "sma50_above_sma200", "golden_cross_event",
        "rsi_sweet_spot", "rsi_mild_oversold", "rsi_mild_overbought",
        "stoch_oversold", "stoch_cross_up",
        "macd_strong", "macd_positive", "macd_zero_cross_up",
        "bb_mid_zone", "price_above_vwap",
        "bullish_trend", "obv_trend_bullish", "volume_z",
    ]

    # Map feature name → extractor from stored reasons JSON.
    # Keys must match what signals.py stores, not the weight-dict names.
    REASONS_MAP = {
        "above_sma50":            lambda r: bool(r.get("trend_above_sma50")),
        "sma50_above_sma200":     lambda r: bool(r.get("sma50_above_sma200")),
        "golden_cross_event":     lambda r: bool(r.get("golden_cross_event")),
        "rsi_sweet_spot":         lambda r: 45 < (r.get("rsi") or 0) < 65,
        "rsi_mild_oversold":      lambda r: 35 < (r.get("rsi") or 0) <= 45,
        "rsi_mild_overbought":    lambda r: 65 <= (r.get("rsi") or 0) < 72,
        "stoch_oversold":         lambda r: bool(r.get("stoch_rsi_oversold")),
        "stoch_cross_up":         lambda r: bool(r.get("stoch_rsi_cross_up")),
        "macd_strong":            lambda r: (r.get("macd_hist") or 0) > 0 and bool(r.get("macd_hist_expanding")),
        "macd_positive":          lambda r: (r.get("macd_hist") or 0) > 0 and not bool(r.get("macd_hist_expanding")),
        "macd_zero_cross_up":     lambda r: bool(r.get("macd_zero_cross_up")),
        "bb_mid_zone":            lambda r: 0.2 < (r.get("bb_pct_b") or 0) < 0.8,
        "price_above_vwap":       lambda r: r.get("price_above_vwap") is True,
        "bullish_trend":          lambda r: bool(r.get("adx_bullish")),
        "obv_trend_bullish":      lambda r: bool(r.get("obv_trend_bullish")),
        "volume_z":               lambda r: (r.get("volume_z") or 0) > 0.5,
    }

    X_rows, y_rows, ret_rows, date_rows, skipped = [], [], [], [], 0
    for is_correct, pct_return, reasons_raw, signal_date in rows:
        try:
            reasons = json.loads(reasons_raw) if isinstance(reasons_raw, str) else (reasons_raw or {})
        except Exception:
            skipped += 1
            continue

        y_rows.append(int(is_correct))
        ret_rows.append(float(pct_return))
        date_rows.append(signal_date)
        X_rows.append([float(REASONS_MAP[f](reasons)) for f in TA_FEATURES])

    if len(X_rows) < 30:
        raise HTTPException(status_code=400, detail=f"Only {len(X_rows)} usable rows after reasons parsing (skipped {skipped})")

    # BUG233-TAWEIGHTS-NOVALIDATION: chronological 70/30 split (rows are already ordered by
    # signal_date from the query above) — the OLDER slice trains the model, the NEWER slice
    # (never seen during fitting) validates it, matching every sibling calibration mechanism.
    MIN_VAL_SAMPLES = 15  # same floor already established by calibrate_ml_weight/T232-OC3
    split = max(1, int(len(X_rows) * 0.7))
    X_train, y_train = X_rows[:split], y_rows[:split]
    X_val, y_val, ret_val = X_rows[split:], y_rows[split:], ret_rows[split:]

    if len(X_val) < MIN_VAL_SAMPLES:
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(X_val)} validation-slice rows after a 70/30 chronological split (need {MIN_VAL_SAMPLES})",
        )

    X = np.array(X_train)
    y = np.array(y_train)

    # AUD263-TAWEIGHTS-CV-SCALER-LEAK: scaler.fit_transform(X) was previously called ONCE on
    # the full train slice before cross_val_score ran TimeSeriesSplit CV on the already-scaled
    # matrix — every fold's validation rows had already contributed their mean/variance to the
    # scaler that scored them, optimistically biasing the reported train_cv_accuracy. Wrapping
    # scaler+classifier in a Pipeline makes cross_val_score refit the scaler on ONLY each
    # fold's own train rows, so no fold's validation rows ever leak into its own scaling.
    # Cannot affect promotion (X_val is scored separately below via
    # _weighted_score_accuracy_and_ev(), never through this scaler/pipeline at all) — this
    # only corrects the diagnostic number reported back to the caller.
    pipeline = Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(max_iter=500, C=1.0, random_state=42))])

    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    cv_scores = cross_val_score(pipeline, X, y, cv=TimeSeriesSplit(n_splits=5), scoring="accuracy")
    train_cv_accuracy = float(np.mean(cv_scores))

    # Fit on the TRAIN slice only — the validation slice below is held out from this fit.
    # This final production fit still scales the FULL train set once (the normal, correct
    # thing to do for a model that will be applied to genuinely new data afterward) — only the
    # CV *diagnostic* above needed the per-fold refit; this fit was never the leaking step.
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    clf = LogisticRegression(max_iter=500, C=1.0, random_state=42)
    clf.fit(X_scaled, y)
    coefs = clf.coef_[0]

    # Map coefficients → weight dict (clip negatives to 0 for positive-weight features)
    fitted = {feat: float(max(0.0, coef)) for feat, coef in zip(TA_FEATURES, coefs)}

    # Rescale so the sum of positive weights equals the sum of defaults (preserve scale)
    default_sum = sum(_TA_WEIGHTS_DEFAULT[k] for k in TA_FEATURES if k in _TA_WEIGHTS_DEFAULT)
    fitted_sum  = sum(fitted.values()) or 1.0
    scale_factor = default_sum / fitted_sum
    fitted_scaled = {k: round(v * scale_factor, 4) for k, v in fitted.items()}

    # Merge with defaults: keep penalty weights from defaults unchanged
    candidate_weights = dict(_TA_WEIGHTS_DEFAULT)
    candidate_weights.update(fitted_scaled)

    def _weighted_score_accuracy_and_ev(weights: dict, X_rows_: list, y_rows_: list, ret_rows_: list):
        """Score each validation row as sum(weight[f] * feature_value), threshold at the median
        score (a simple, weight-scale-agnostic decision rule — this function only needs to
        compare TWO weight vectors' relative accuracy/EV on the SAME rows, not reproduce
        _ta_score()'s full production blending logic), and report accuracy/EV among rows that
        clear that threshold. Mirrors calibrate_ml_weight's own _accuracy_and_return() shape."""
        scores = [sum(weights.get(f, 0.0) * x for f, x in zip(TA_FEATURES, row)) for row in X_rows_]
        if not scores:
            return None, 0, None
        median_score = sorted(scores)[len(scores) // 2]
        fired_idx = [i for i, s in enumerate(scores) if s >= median_score]
        if not fired_idx:
            return None, 0, None
        correct = sum(1 for i in fired_idx if y_rows_[i])
        acc = correct / len(fired_idx)
        avg_ret = sum(ret_rows_[i] for i in fired_idx) / len(fired_idx)
        return acc, len(fired_idx), avg_ret

    candidate_acc, candidate_n, candidate_ev = _weighted_score_accuracy_and_ev(
        candidate_weights, X_val, y_val, ret_val,
    )
    baseline_acc, baseline_n, baseline_ev = _weighted_score_accuracy_and_ev(
        dict(_current_live_ta_weights), X_val, y_val, ret_val,
    )

    import uuid as _uuid
    _run_id = str(_uuid.uuid4())
    _train_start, _train_end = date_rows[0], date_rows[split - 1]
    _val_start, _val_end = date_rows[split], date_rows[-1]

    validated = (
        candidate_ev is not None
        and baseline_ev is not None
        and candidate_n >= MIN_VAL_SAMPLES
        and candidate_ev > baseline_ev
    )

    if not validated:
        _record_tune_history(
            session, _run_id, "ta_weights", "ta_weights_vector", "ALL", "ALL",
            old_value={"ta_weights": dict(_current_live_ta_weights)},
            new_value={"ta_weights": candidate_weights},
            train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
            train_ev_pct=None,
            validation_ev_pct=round(candidate_ev, 2) if candidate_ev is not None else None,
            baseline_validation_ev_pct=round(baseline_ev, 2) if baseline_ev is not None else None,
            validation_n=candidate_n, promoted=False,
            gate_failures=["ev_lift_not_positive_on_validation"] if candidate_ev is not None else ["candidate_unmeasurable_on_validation"],
        )
        return {
            "applied": False,
            "reason": "candidate weights did not beat the current live weights on the held-out validation slice",
            "n_signals": len(rows),
            "n_usable": len(X_rows),
            "n_skipped": skipped,
            "train_cv_accuracy": round(train_cv_accuracy, 4),
            "candidate_validation_accuracy": round(candidate_acc, 4) if candidate_acc is not None else None,
            "candidate_validation_ev_pct": round(candidate_ev, 2) if candidate_ev is not None else None,
            "baseline_validation_ev_pct": round(baseline_ev, 2) if baseline_ev is not None else None,
            "candidate_weights": candidate_weights,
        }

    Path(_TA_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
    _tmp = Path(_TA_WEIGHTS_PATH).with_suffix(".tmp")
    _tmp.write_text(json.dumps(candidate_weights, indent=2))
    _os.replace(str(_tmp), str(_TA_WEIGHTS_PATH))
    # T228: also persist to Redis so weights survive Docker rebuilds (90-day TTL)
    try:
        _get_redis().setex("stockai:ta_weights", 90 * 86400, json.dumps(candidate_weights))
    except Exception:
        pass
    _mark_tuned("stockai:ta_weights")
    # T232-SIG6: the persistence writes above only affect the NEXT process restart unless the
    # in-process globals are also refreshed here — this used to be the entire bug (calibration
    # reported success but the running process kept scoring signals against the old weights
    # until it happened to restart for an unrelated reason).
    set_ta_weights(candidate_weights)
    log.info(
        "calibrate_ta_weights: applied %s (val_acc=%.3f, val_ev=%.2f%%, baseline_ev=%.2f%%, n=%d)",
        _TA_WEIGHTS_PATH, candidate_acc or 0.0, candidate_ev or 0.0, baseline_ev or 0.0, len(X_rows),
    )
    _record_tune_history(
        session, _run_id, "ta_weights", "ta_weights_vector", "ALL", "ALL",
        old_value={"ta_weights": dict(_current_live_ta_weights)},
        new_value={"ta_weights": candidate_weights},
        train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
        train_ev_pct=None, validation_ev_pct=round(candidate_ev, 2),
        baseline_validation_ev_pct=round(baseline_ev, 2), validation_n=candidate_n,
        promoted=True, gate_failures=[],
    )

    return {
        "applied":          True,
        "n_signals":        len(rows),
        "n_usable":         len(X_rows),
        "n_skipped":        skipped,
        "train_cv_accuracy": round(train_cv_accuracy, 4),
        "validation_accuracy": round(candidate_acc, 4),
        "candidate_validation_ev_pct": round(candidate_ev, 2),
        "baseline_validation_ev_pct": round(baseline_ev, 2),
        "weights":          candidate_weights,
    }


@router.post("/calibrate_conviction_weights")
def calibrate_conviction_weights(
    lookback_days: int = Query(365, ge=90, le=730),
    min_count: int = Query(10),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """AL-3: Fit logistic regression on conviction layer flags from signal_outcomes.

    For each boolean reason flag, computes edge = presence_in_winners − presence_in_losers.
    Writes conviction_weights.json with per-flag accuracy and edge data.
    Flags with accuracy < 52% are marked as noise layers.

    AUD263-CONVICTION-WEIGHTS-UNGATED: this function previously fit on the FULL sample and
    wrote conviction_weights.json/Redis UNCONDITIONALLY — no chronological split, no baseline
    comparison, no TuneHistory record — the only mutation path in this file with zero audit
    trail, confirmed empty in production (0 tune_history rows for parameter_class LIKE
    '%conviction%'). Separately, its output (edge_pct) had NO consumer anywhere in the codebase
    — load_conviction_weights() existed with zero callers. Both fixed together: (1)
    market-data's _is_conviction_buy() now reads edge_pct and uses it to extend its soft-fail
    allowance to any layer whose underlying flag has near-zero/negative calibrated edge — a
    real, additive consumer; (2) this function now uses the same chronological 70/30 split +
    validation-beats-baseline + TuneHistory pattern as its sibling calibrate_ta_weights (fixed
    2026-07-31), scored against the SAME thing its new consumer actually uses the data for:
    whether the candidate's calibrated edges correctly separate winners from losers on a
    held-out slice, vs. the currently-live edges (or a neutral "no calibration" baseline for
    an actual first-ever run).
    """
    import json
    from pathlib import Path

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        raise HTTPException(500, "scikit-learn not installed")

    from ..generators.signals import _CONVICTION_WEIGHTS_PATH

    cutoff = date.today() - timedelta(days=lookback_days)

    rows = session.execute(
        select(SignalOutcome.is_correct, SignalOutcome.pct_return, Signal.reasons, SignalOutcome.signal_date)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.pct_return.is_not(None),
            Signal.reasons.is_not(None),
        )
        # Chronological order is load-bearing: the train/validation split below assumes the
        # OLDER slice trains and the NEWER slice (never seen while fitting) validates.
        .order_by(SignalOutcome.signal_date)
    ).all()

    if len(rows) < 30:
        raise HTTPException(400, f"Need ≥30 evaluated BUY outcomes, found {len(rows)}")

    MIN_VAL_SAMPLES = 15  # same floor established by calibrate_ml_weight/calibrate_ta_weights
    split = max(1, int(len(rows) * 0.7))
    train_rows, val_rows = rows[:split], rows[split:]

    if len(val_rows) < MIN_VAL_SAMPLES:
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(val_rows)} validation-slice rows after a 70/30 chronological split (need {MIN_VAL_SAMPLES})",
        )

    def _fit_layer_stats(fit_rows: list) -> dict[str, dict]:
        n_win = sum(1 for r in fit_rows if r.is_correct)
        n_los = sum(1 for r in fit_rows if not r.is_correct)
        key_wins: dict[str, int] = {}
        key_los: dict[str, int] = {}
        for r in fit_rows:
            reasons = r.reasons or {}
            bucket = key_wins if r.is_correct else key_los
            for k, v in reasons.items():
                if isinstance(v, bool) and v:
                    bucket[k] = bucket.get(k, 0) + 1
        all_keys = set(key_wins) | set(key_los)
        stats: dict[str, dict] = {}
        for k in all_keys:
            wc = key_wins.get(k, 0)
            lc = key_los.get(k, 0)
            if wc + lc < min_count:
                continue
            wp = wc / n_win if n_win > 0 else 0.0
            lp = lc / n_los if n_los > 0 else 0.0
            accuracy = wc / (wc + lc) if (wc + lc) > 0 else 0.5
            stats[k] = {
                "win_pct": round(wp * 100, 1),
                "los_pct": round(lp * 100, 1),
                "edge_pct": round((wp - lp) * 100, 1),
                "accuracy": round(accuracy * 100, 1),
                "is_noise": accuracy < 0.52,
                "win_count": wc,
                "los_count": lc,
            }
        return stats

    # Fit on the TRAIN slice only — the validation slice below is held out from this fit.
    layer_stats = _fit_layer_stats(train_rows)

    # Fit logistic regression for coefficient-based weights (reference only, not used in the
    # gate below — mirrors calibrate_ta_weights' own train_cv_accuracy, a diagnostic figure).
    features = sorted(layer_stats.keys())
    if len(features) >= 3 and len(train_rows) >= 50:
        X = np.array([[int(bool((r.reasons or {}).get(f))) for f in features] for r in train_rows])
        y = np.array([int(r.is_correct) for r in train_rows])
        try:
            lr = LogisticRegression(max_iter=500, C=1.0, random_state=42)
            lr.fit(X, y)
            for feat, coef in zip(features, lr.coef_[0]):
                if feat in layer_stats:
                    layer_stats[feat]["logistic_coef"] = round(float(coef), 4)
        except Exception:
            pass

    candidate_edges = {k: v["edge_pct"] for k, v in layer_stats.items()}

    from ..generators.signals import load_conviction_weights as _current_live_edges_fn
    current_live_edges = _current_live_edges_fn()

    def _edge_separation_ev(edges: dict[str, float], scored_rows: list) -> tuple[float | None, int]:
        """The property _is_conviction_buy() actually uses this data for: does a NEGATIVE-edge
        flag correctly predict a loser more often than a POSITIVE-edge flag predicts a winner?
        Scores each row by sum(edge_pct for present boolean flags with calibration data),
        splits at the median (weight-scale-agnostic, mirrors calibrate_ta_weights' own
        _weighted_score_accuracy_and_ev shape), and reports the EV among the top half — a
        candidate whose edges genuinely separate winners from losers should show a materially
        better top-half EV than one whose edges are noise."""
        if not edges:
            return None, 0
        scores = []
        for r in scored_rows:
            reasons = r.reasons or {}
            s = sum(edges.get(k, 0.0) for k, v in reasons.items() if isinstance(v, bool) and v and k in edges)
            scores.append(s)
        if not scores:
            return None, 0
        median_score = sorted(scores)[len(scores) // 2]
        fired_idx = [i for i, s in enumerate(scores) if s >= median_score]
        if not fired_idx:
            return None, 0
        avg_ret = sum(scored_rows[i].pct_return for i in fired_idx) / len(fired_idx)
        return avg_ret, len(fired_idx)

    candidate_ev, candidate_n = _edge_separation_ev(candidate_edges, val_rows)
    baseline_ev, baseline_n = _edge_separation_ev(current_live_edges, val_rows)

    import uuid as _uuid
    _run_id = str(_uuid.uuid4())
    _train_start, _train_end = train_rows[0].signal_date, train_rows[-1].signal_date
    _val_start, _val_end = val_rows[0].signal_date, val_rows[-1].signal_date

    # First-ever calibration (no live edges yet) has nothing to beat — auto-promote, matching
    # every sibling mechanism's own first-run convention (e.g. calibrate_ta_weights,
    # tune_strategy), rather than blocking the feature from ever turning on.
    no_baseline = not current_live_edges
    validated = no_baseline or (
        candidate_ev is not None
        and baseline_ev is not None
        and candidate_n >= MIN_VAL_SAMPLES
        and candidate_ev > baseline_ev
    )

    payload = {
        "as_of": date.today().isoformat(),
        "lookback_days": lookback_days,
        "total_winners": sum(1 for r in train_rows if r.is_correct),
        "total_losers": sum(1 for r in train_rows if not r.is_correct),
        "layer_count": len(layer_stats),
        "noise_count": sum(1 for s in layer_stats.values() if s["is_noise"]),
        "layers": layer_stats,
        "edge_pct": candidate_edges,
    }

    if not validated:
        _record_tune_history(
            session, _run_id, "conviction_weights", "edge_pct_vector", "ALL", "ALL",
            old_value={"edge_pct": current_live_edges},
            new_value={"edge_pct": candidate_edges},
            train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
            train_ev_pct=None,
            validation_ev_pct=round(candidate_ev, 2) if candidate_ev is not None else None,
            baseline_validation_ev_pct=round(baseline_ev, 2) if baseline_ev is not None else None,
            validation_n=candidate_n, promoted=False,
            gate_failures=["ev_lift_not_positive_on_validation"] if candidate_ev is not None else ["candidate_unmeasurable_on_validation"],
        )
        return {
            "applied": False,
            "reason": "candidate edges did not beat the current live edges on the held-out validation slice",
            "n_signals": len(rows),
            "n_train": len(train_rows),
            "n_val": len(val_rows),
            "candidate_validation_ev_pct": round(candidate_ev, 2) if candidate_ev is not None else None,
            "baseline_validation_ev_pct": round(baseline_ev, 2) if baseline_ev is not None else None,
            "layers": layer_stats,
        }

    try:
        Path(_CONVICTION_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_CONVICTION_WEIGHTS_PATH).write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("conviction_weights.write_failed", error=str(exc))
    # T228: also persist to Redis so weights survive Docker rebuilds (90-day TTL)
    try:
        _get_redis().setex("stockai:conviction_weights", 90 * 86400, json.dumps(payload))
    except Exception:
        pass
    _mark_tuned("stockai:conviction_weights")

    _record_tune_history(
        session, _run_id, "conviction_weights", "edge_pct_vector", "ALL", "ALL",
        old_value={"edge_pct": current_live_edges},
        new_value={"edge_pct": candidate_edges},
        train_window=(_train_start, _train_end), validation_window=(_val_start, _val_end),
        train_ev_pct=None,
        validation_ev_pct=round(candidate_ev, 2) if candidate_ev is not None else None,
        baseline_validation_ev_pct=round(baseline_ev, 2) if baseline_ev is not None else None,
        validation_n=candidate_n, promoted=True, gate_failures=[],
    )

    log.info("conviction_weights.calibrated", layers=len(layer_stats), noise=payload["noise_count"],
              validation_ev_pct=candidate_ev, baseline_validation_ev_pct=baseline_ev)
    return payload


@router.get("/outcomes/calibration")
def outcomes_calibration(
    days: int = Query(180, ge=30, le=365, description="Look-back window in calendar days"),
    session: Session = Depends(get_session),
):
    """Calibration curve data for the reliability diagram.

    For each horizon × confidence band combination, returns the actual win rate
    vs expected (midpoint of the band). Used to assess whether confidence scores
    are well-calibrated and to recommend minimum confidence thresholds.
    """
    import statistics
    cutoff = date.today() - timedelta(days=days)

    outcomes = session.execute(
        select(SignalOutcome)
        .where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    if not outcomes:
        return {"total": 0, "horizons": [], "overall": {}, "message": "No evaluated BUY outcomes yet"}

    bands = [
        (50, 60, "50-60", 55.0),
        (60, 65, "60-65", 62.5),
        (65, 70, "65-70", 67.5),
        (70, 75, "70-75", 72.5),
        (75, 80, "75-80", 77.5),
        (80, 101, "80+", 85.0),
    ]

    horizons = ["SHORT", "SWING", "LONG", "GROWTH"]
    horizon_stats = []

    for hor in horizons:
        hor_outcomes = [o for o in outcomes if o.horizon == hor or o.horizon == SignalHorizon(hor)]
        if not hor_outcomes:
            continue

        band_data = []
        for lo, hi, label, midpoint in bands:
            bucket = [o for o in hor_outcomes if lo <= o.confidence < hi]
            if len(bucket) < 3:
                continue
            wins = sum(1 for o in bucket if o.is_correct)
            rets = [o.pct_return for o in bucket if o.pct_return is not None]
            band_data.append({
                "band": label,
                "midpoint": midpoint,
                "count": len(bucket),
                "win_rate": round(wins / len(bucket), 3),
                "win_rate_pct": round(wins / len(bucket) * 100, 1),
                "avg_return_pct": round(statistics.mean(rets) * 100, 2) if rets else None,
                "calibration_gap": round((wins / len(bucket)) - (midpoint / 100), 3),
            })

        if not band_data:
            continue

        # Suggest min_confidence: lowest band with win_rate >= 0.52
        suggested_min = None
        for bd in sorted(band_data, key=lambda x: x["midpoint"]):
            if bd["win_rate"] >= 0.52 and bd["count"] >= 5:
                suggested_min = bd["midpoint"] - 5  # use band start
                break

        hor_wins = sum(1 for o in hor_outcomes if o.is_correct)
        hor_rets = [o.pct_return for o in hor_outcomes if o.pct_return is not None]
        horizon_stats.append({
            "horizon": hor,
            "total": len(hor_outcomes),
            "win_rate_pct": round(hor_wins / len(hor_outcomes) * 100, 1),
            "avg_return_pct": round(statistics.mean(hor_rets) * 100, 2) if hor_rets else None,
            "suggested_min_confidence": suggested_min,
            "bands": band_data,
        })

    # Overall
    all_wins = sum(1 for o in outcomes if o.is_correct)
    all_rets = [o.pct_return for o in outcomes if o.pct_return is not None]

    return {
        "total": len(outcomes),
        "days": days,
        "overall": {
            "win_rate_pct": round(all_wins / len(outcomes) * 100, 1),
            "avg_return_pct": round(statistics.mean(all_rets) * 100, 2) if all_rets else None,
        },
        "horizons": horizon_stats,
    }


@router.get("/outcomes/calibrate")
def outcomes_calibrate(
    days: int = Query(180, description="Look-back window in calendar days"),
    min_samples: int = Query(15, description="Minimum signals required to suggest a threshold"),
    session: Session = Depends(get_session),
):
    """Sweep confidence thresholds per horizon to find the empirically optimal buy_threshold.

    For each horizon × BUY, finds the confidence level (0-100 scale) that maximises
    expected_value = win_rate × avg_return, subject to n >= min_samples.
    Compares the suggested threshold against the current hardcoded thresholds in
    _STYLE_PROFILES so you can see whether signal tuning is needed.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES

    # Current bull-regime thresholds, fused-probability scale (T232-CAL1: sweep/report on the
    # same scale _decide_style actually compares against — previously this used a 0-100
    # confidence scale here while POST /apply wrote a misinterpreted 0-100 value too).
    CURRENT: dict[str, float] = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    cutoff = date.today() - timedelta(days=days)
    all_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    # T232-OC3: walk-forward split — mirrors the fix in POST /outcomes/calibrate/apply so this
    # preview endpoint reports the SAME methodology that actually gets applied, instead of a
    # more optimistic in-sample number that would disagree with what apply's response shows.
    calibrations = []
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        bucket = sorted(
            [o for o in all_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )
        current_t = CURRENT.get(h, 0.65)

        def _stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob >= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [o.pct_return for o in sub if o.pct_return is not None]
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret is already the mean return across ALL trades (wins and
            # losses) in `sub` — it already IS the expected value per trade. Multiplying by
            # acc (win rate) again double-counts win probability, understating true EV.
            return {
                "n": len(sub),
                "win_rate": round(acc, 3),
                "avg_return_pct": round(avg_ret * 100, 2),
                "expected_value_pct": round(avg_ret * 100, 2),
            }

        if len(bucket) < min_samples * 2:
            calibrations.append({
                "horizon": h,
                "current_threshold": current_t,
                "suggested_threshold": None,
                "n_total": len(bucket),
                "note": f"Insufficient data (need ≥{min_samples * 2} evaluated BUY outcomes for a valid train/validation split)",
            })
            continue

        split = max(1, int(len(bucket) * 0.7))
        train_bucket = bucket[:split]
        val_bucket = bucket[split:]

        # Search on the train slice only.
        best_ev = -999.0
        best_t: float | None = None
        for t_i in range(55, 86):
            st = _stats_at(t_i / 100.0, train_bucket)
            if st is not None and st["expected_value_pct"] > best_ev:
                best_ev = st["expected_value_pct"]
                best_t = t_i / 100.0

        # Report stats on the validation slice — data the search never saw.
        best_stats = _stats_at(best_t, val_bucket) if best_t is not None else None
        at_current = _stats_at(current_t, val_bucket)
        ev_lift = None
        if best_stats and at_current:
            ev_lift = round(best_stats["expected_value_pct"] - at_current["expected_value_pct"], 2)

        calibrations.append({
            "horizon": h,
            "current_threshold": current_t,
            "suggested_threshold": round(best_t, 2) if best_t else None,
            "ev_lift_pct": ev_lift,
            "n_total": len(bucket),
            "train_n": len(train_bucket),
            "validation_n": len(val_bucket),
            "at_current_threshold": at_current,
            "at_suggested_threshold": best_stats,
        })

    return {
        "days": days,
        "min_samples": min_samples,
        "calibrations": calibrations,
    }


@router.post("/outcomes/calibrate/apply")
def outcomes_calibrate_apply(
    days: int = Query(180, description="Look-back window in calendar days"),
    min_samples: int = Query(50, description="Minimum signals required to apply a new threshold"),
    min_ev_lift: float = Query(0.1, description="Minimum expected-value lift (%) before applying"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Apply empirically-optimal buy/sell thresholds to Redis so signal generator picks them up live.

    T232-CAL1/CAL3 fix: sweeps and writes directly on the fused-probability (0-1) scale that
    _decide_style actually compares against — previously this swept SignalOutcome.confidence
    (a 0-100 distance-from-neutral scale) and wrote best_t/100, which was silently misapplied
    as a fused-probability threshold (confidence 62 ≡ fused 0.81, was written+read as 0.62).

    Reads the same calibration data as GET /outcomes/calibrate and, for each horizon
    where the suggested threshold has a positive EV lift and sufficient sample size,
    writes `stockai:signal_thresholds:{HORIZON}` to Redis with a 30-day TTL. The value
    written is a delta from the hardcoded bull baseline, applied per-regime by
    _get_dynamic_buy_threshold (T232-CAL2) rather than overriding all regimes with one flat
    number, and is bounds-checked before being written (defense in depth alongside the
    reader-side clamp).

    The signal generator reads these keys at signal decision time (falls back to the
    hardcoded _STYLE_PROFILES values if absent).  Run this weekly via the scheduler.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES, _SELL_THRESHOLD_FALLBACK

    # Bull-regime buy thresholds — source of truth is _STYLE_PROFILES (T232-SIG12: no more
    # independently-drifting hardcoded copies).
    CURRENT: dict[str, float] = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }
    _BUY_BOUNDS = (0.55, 0.85)
    _SELL_BOUNDS = (0.15, 0.45)

    cutoff = date.today() - timedelta(days=days)
    all_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    applied: list[dict] = []
    skipped: list[dict] = []
    redis_client = _get_redis()
    _REDIS_TTL = 30 * 86400  # 30 days

    # T232-OC3 / T233-SELFIMPROVE-PHASE1: the threshold search used to sweep 31 overlapping
    # cumulative subsets of ONE sample and take the argmax — an in-sample search evaluated on
    # the exact data it was fit to. At min_samples=50 the win-rate standard error is still ~7pp,
    # so an unvalidated argmax over 31 correlated subsets is prone to surfacing an upward-biased
    # fluke as "optimal" (the same failure mode that produced the CAL-1 incident documented
    # elsewhere in this report). Fixed with a genuine walk-forward split: search for the best
    # threshold on the OLDER 70% of the window (train), then only apply it if the EV lift ALSO
    # holds up on the NEWER, never-searched 30% (validation) — mirrors the chronological
    # train/validation split calibrate_ml_weight() already uses correctly for the sibling
    # ML-weight calibration.
    import uuid as _uuid
    _run_id = str(_uuid.uuid4())

    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        bucket = sorted(
            [o for o in all_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )
        current_t = CURRENT.get(h, 0.65)  # fused-probability scale
        _bucket_dates = (bucket[0].signal_date, bucket[-1].signal_date) if bucket else (date.today(), date.today())

        if len(bucket) < min_samples * 2:
            # Need enough for BOTH a train slice and a validation slice to each independently
            # clear min_samples — otherwise the split itself produces two under-powered halves.
            skipped.append({"horizon": h, "reason": f"only {len(bucket)} samples (need {min_samples * 2} for a valid train/validation split)"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={},
                train_window=_bucket_dates, validation_window=_bucket_dates,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=None, promoted=False,
                gate_failures=[f"insufficient_total_samples:{len(bucket)}<{min_samples * 2}"],
            )
            continue

        split = max(1, int(len(bucket) * 0.7))
        train_bucket = bucket[:split]
        val_bucket = bucket[split:]

        def _stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob >= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [o.pct_return for o in sub if o.pct_return is not None]
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret already IS the expected value (mean return across all trades
            # in `sub`, wins and losses) — multiplying by acc double-counts win probability.
            ev = avg_ret * 100
            return {"n": len(sub), "win_rate": round(acc, 3), "ev_pct": round(ev, 2)}

        # Search for the best threshold on the TRAIN slice only.
        best_ev = -999.0
        best_t: float | None = None
        for t_i in range(55, 86):
            t = t_i / 100.0
            st = _stats_at(t, train_bucket)
            if st is not None and st["ev_pct"] > best_ev:
                best_ev = st["ev_pct"]
                best_t = t

        _train_window = (train_bucket[0].signal_date, train_bucket[-1].signal_date)
        _val_window = (val_bucket[0].signal_date, val_bucket[-1].signal_date)

        if best_t is None:
            skipped.append({"horizon": h, "reason": "no threshold met EV/sample criteria on the train slice"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_bucket), promoted=False,
                gate_failures=["no_candidate_met_train_criteria"],
            )
            continue

        # Validate: both the suggested threshold and the current baseline must be independently
        # measurable on the VALIDATION slice — a candidate that never sees this data.
        best_stats = _stats_at(best_t, val_bucket)
        current_stats = _stats_at(current_t, val_bucket)

        if best_stats is None:
            skipped.append({"horizon": h, "reason": "suggested threshold unmeasurable on the validation slice (insufficient samples)"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_bucket), promoted=False,
                gate_failures=["candidate_unmeasurable_on_validation"],
            )
            continue

        if current_stats is None:
            # T232-OC3: no honest baseline measurable at the current threshold — do not assume
            # EV 0 (that overstates lift and applies too eagerly). Skip instead.
            skipped.append({"horizon": h, "reason": "baseline threshold unmeasurable on the validation slice (insufficient samples)"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"], baseline_validation_ev_pct=None,
                validation_n=best_stats["n"], promoted=False,
                gate_failures=["baseline_unmeasurable_on_validation"],
            )
            continue

        ev_lift = round(best_stats["ev_pct"] - current_stats["ev_pct"], 2)

        # T232-OC3-FOLLOWUP: never apply a threshold with negative validated EV lift, regardless
        # of how large the threshold shift is — see the SELL-side comment above for the live
        # incident (a large shift previously bypassed the lift check entirely via the old
        # `ev_lift < min AND shift < 3pt` AND-logic).
        if ev_lift < 0:
            skipped.append({
                "horizon": h,
                "reason": f"validation-slice EV lift {ev_lift}% is negative — never apply a worse threshold",
                "suggested": best_t,
                "current": current_t,
            })
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
                baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
                promoted=False, gate_failures=["ev_lift_negative"],
            )
            continue

        if ev_lift < min_ev_lift and abs(best_t - current_t) < 0.03:
            skipped.append({
                "horizon": h,
                "reason": f"validation-slice EV lift {ev_lift}% below min {min_ev_lift}% and threshold shift <3pt",
                "suggested": best_t,
                "current": current_t,
            })
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
                baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
                promoted=False, gate_failures=["ev_lift_below_min_and_shift_too_small"],
            )
            continue

        if not (_BUY_BOUNDS[0] <= best_t <= _BUY_BOUNDS[1]):
            skipped.append({"horizon": h, "reason": f"suggested {best_t} outside sane bounds {_BUY_BOUNDS}"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
                old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
                baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
                promoted=False, gate_failures=["suggested_outside_sane_bounds"],
            )
            continue

        # Write to Redis — signal generator reads this at decision time (fused-probability scale)
        redis_key = f"stockai:signal_thresholds:{h}"
        redis_client.setex(redis_key, _REDIS_TTL, str(round(best_t, 4)))
        _mark_tuned(redis_key)
        # AUD263-WATCHDOG-MASKS-VALIDATED-THRESHOLD: a fresh, validated recalibration must not
        # be silently shadowed by a stale watchdog emergency nudge that predates it.
        _clear_watchdog_override(h)
        _record_tune_history(
            session, _run_id, "signal_threshold", "buy_threshold", h, "ALL",
            old_value={"buy_threshold": current_t}, new_value={"buy_threshold": best_t},
            train_window=_train_window, validation_window=_val_window,
            train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
            baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
            promoted=True, gate_failures=[],
        )
        applied.append({
            "horizon": h,
            "previous_threshold": current_t,
            "new_threshold": round(best_t, 4),
            "ev_lift_pct": ev_lift,
            "train_n": len(train_bucket),
            "validation_stats": best_stats,
        })

    # T228-SELL-CALIBRATION (T232-CAL3 fix): sweep SELL threshold per horizon.
    # For SELL, LOWER fused_prob = stronger conviction (confidence = (0.5-fused)*200), so the
    # sweep selects fused_prob <= t — the mirror image of the BUY sweep above — and uses signed
    # SELL profit (a SELL is profitable when price falls, i.e. -pct_return), not abs().
    sell_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "SELL",
        )
    ).scalars().all()

    sell_applied: list[dict] = []
    sell_skipped: list[dict] = []
    # AUD232-051: read the shared constant from signals.py instead of an independently
    # hardcoded copy that had to be kept in sync by hand (fused-probability scale).
    _CURRENT_SELL = _SELL_THRESHOLD_FALLBACK

    # T232-OC3: same walk-forward fix as the BUY sweep above — train on the older 70%,
    # validate the chosen threshold's EV lift on the newer, never-searched 30%.
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        s_bucket = sorted(
            [o for o in sell_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )

        _s_bucket_dates = (s_bucket[0].signal_date, s_bucket[-1].signal_date) if s_bucket else (date.today(), date.today())

        if len(s_bucket) < min_samples * 2:
            sell_skipped.append({"horizon": h, "reason": f"only {len(s_bucket)} SELL samples (need {min_samples * 2} for a valid train/validation split)"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={},
                train_window=_s_bucket_dates, validation_window=_s_bucket_dates,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=None, promoted=False,
                gate_failures=[f"insufficient_total_samples:{len(s_bucket)}<{min_samples * 2}"],
            )
            continue

        s_split = max(1, int(len(s_bucket) * 0.7))
        s_train_bucket = s_bucket[:s_split]
        s_val_bucket = s_bucket[s_split:]
        _s_train_window = (s_train_bucket[0].signal_date, s_train_bucket[-1].signal_date)
        _s_val_window = (s_val_bucket[0].signal_date, s_val_bucket[-1].signal_date)

        def _sell_stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob <= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [-o.pct_return for o in sub if o.pct_return is not None]  # signed: SELL wins on price decline
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret already IS the expected value — see fix note above.
            ev = avg_ret * 100
            return {"n": len(sub), "win_rate": round(acc, 3), "ev_pct": round(ev, 2)}

        s_best_ev = -999.0
        s_best_t: float | None = None
        for t_i in range(15, 41):
            t = t_i / 100.0
            st = _sell_stats_at(t, s_train_bucket)
            if st is not None and st["ev_pct"] > s_best_ev:
                s_best_ev = st["ev_pct"]
                s_best_t = t

        if s_best_t is None:
            sell_skipped.append({"horizon": h, "reason": "no SELL threshold met criteria on the train slice"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(s_val_bucket), promoted=False,
                gate_failures=["no_candidate_met_train_criteria"],
            )
            continue

        s_best_stats = _sell_stats_at(s_best_t, s_val_bucket)
        s_current_stats = _sell_stats_at(_CURRENT_SELL, s_val_bucket)

        if s_best_stats is None:
            sell_skipped.append({"horizon": h, "reason": "suggested SELL threshold unmeasurable on the validation slice"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=s_best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(s_val_bucket), promoted=False,
                gate_failures=["candidate_unmeasurable_on_validation"],
            )
            continue

        if s_current_stats is None:
            sell_skipped.append({"horizon": h, "reason": "SELL baseline threshold unmeasurable on the validation slice"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=s_best_ev, validation_ev_pct=s_best_stats["ev_pct"], baseline_validation_ev_pct=None,
                validation_n=s_best_stats["n"], promoted=False,
                gate_failures=["baseline_unmeasurable_on_validation"],
            )
            continue

        s_ev_lift = round(s_best_stats["ev_pct"] - s_current_stats["ev_pct"], 2)

        # T232-OC3-FOLLOWUP: this used to be `ev_lift < min_ev_lift AND shift < 3pt` — an AND
        # meant a large threshold shift could bypass the EV check entirely even with NEGATIVE
        # validated lift (caught live: a run applied SELL:GROWTH 0.35->0.30 with a validated
        # ev_lift of -0.01%, because the 5pt shift satisfied "not small" while the lift check
        # was skipped). Never apply a threshold with negative validated EV lift regardless of
        # shift size; the small-shift-plus-small-lift skip is now a separate, narrower check.
        if s_ev_lift < 0:
            sell_skipped.append({
                "horizon": h, "direction": "SELL",
                "reason": f"validation-slice EV lift {s_ev_lift}% is negative — never apply a worse threshold",
                "suggested": s_best_t,
            })
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=s_best_ev, validation_ev_pct=s_best_stats["ev_pct"],
                baseline_validation_ev_pct=s_current_stats["ev_pct"], validation_n=s_best_stats["n"],
                promoted=False, gate_failures=["ev_lift_negative"],
            )
            continue
        if s_ev_lift < min_ev_lift and abs(s_best_t - _CURRENT_SELL) < 0.03:
            sell_skipped.append({
                "horizon": h, "direction": "SELL",
                "reason": f"validation-slice EV lift {s_ev_lift}% below min and shift <3pt",
                "suggested": s_best_t,
            })
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=s_best_ev, validation_ev_pct=s_best_stats["ev_pct"],
                baseline_validation_ev_pct=s_current_stats["ev_pct"], validation_n=s_best_stats["n"],
                promoted=False, gate_failures=["ev_lift_below_min_and_shift_too_small"],
            )
            continue

        if not (_SELL_BOUNDS[0] <= s_best_t <= _SELL_BOUNDS[1]):
            sell_skipped.append({"horizon": h, "reason": f"suggested {s_best_t} outside sane bounds {_SELL_BOUNDS}"})
            _record_tune_history(
                session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
                old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
                train_window=_s_train_window, validation_window=_s_val_window,
                train_ev_pct=s_best_ev, validation_ev_pct=s_best_stats["ev_pct"],
                baseline_validation_ev_pct=s_current_stats["ev_pct"], validation_n=s_best_stats["n"],
                promoted=False, gate_failures=["suggested_outside_sane_bounds"],
            )
            continue

        _sell_redis_key = f"stockai:signal_thresholds:SELL:{h}"
        redis_client.setex(_sell_redis_key, _REDIS_TTL, str(round(s_best_t, 4)))
        _mark_tuned(_sell_redis_key)
        _record_tune_history(
            session, _run_id, "signal_threshold", "sell_threshold", h, "ALL",
            old_value={"sell_threshold": _CURRENT_SELL}, new_value={"sell_threshold": s_best_t},
            train_window=_s_train_window, validation_window=_s_val_window,
            train_ev_pct=s_best_ev, validation_ev_pct=s_best_stats["ev_pct"],
            baseline_validation_ev_pct=s_current_stats["ev_pct"], validation_n=s_best_stats["n"],
            promoted=True, gate_failures=[],
        )
        sell_applied.append({
            "horizon": h,
            "direction": "SELL",
            "previous_threshold": _CURRENT_SELL,
            "new_threshold": round(s_best_t, 4),
            "ev_lift_pct": s_ev_lift,
            "train_n": len(s_train_bucket),
            "validation_stats": s_best_stats,
        })

    return {
        "buy_applied": applied,
        "buy_skipped": skipped,
        "sell_applied": sell_applied,
        "sell_skipped": sell_skipped,
        "redis_ttl_days": 30,
    }


@router.post("/tune_sell_pillars")
def tune_sell_pillars(
    min_samples: int = Query(50, description="Minimum SELL outcomes required per train/validation slice"),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """T232-SIG10-SELLGATE: sweep min_pillars_for_sell per horizon against real, backfilled
    SELL outcome history — the symmetric SELL-side counterpart to the LIVE min_pillars_for_buy
    gate (SA-19/SA-30, signals.py's _apply_style_signal(), fused > 0.5 only). Requires
    POST /signals/backfill_bearish_pillars to have already run — this sweep only reads rows
    where bearish_pillars_active IS NOT NULL, never treating a missing value as "0 pillars".

    Deliberately does NOT reuse min_pillars_for_buy's own values (2 default, 3 for SWING/LONG)
    — the bearish pillar sub-scores are NOT calibrated to the same base rate as the bullish
    ones (e.g. pb_volume gives 0.6 for `not obv_trend_bullish` ALONE, a much weaker bar than
    the bullish side's own AND-logic), so assuming symmetry would repeat the exact "overfit
    argmax on unvalidated assumption" mistake already documented at T232-OC3. Sweeps 1-4 and
    lets the validation slice decide, mirroring outcomes_calibrate_apply's own walk-forward
    discipline exactly: chronological 70/30 split, per-slice min_samples floor, candidate must
    beat the CURRENT LIVE gate (no gate at all — pillars=0, i.e. never compress) on the
    validation slice's own never-searched EV, unconditional rejection of negative EV lift.

    Applies through the SAME stockai:style_tune:{H}:min_pillars_for_sell Redis key
    _get_style_tuned_param() already knows how to read generically — the read side needs no
    new code once a caller in _apply_style_signal() actually asks for this key.
    """
    import statistics as _stats

    _REDIS_TTL = 30 * 86400  # 30 days, matching every sibling calibration mechanism
    redis_client = _get_redis()

    all_sell_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_direction == "SELL",
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.bearish_pillars_active.is_not(None),
        )
    ).scalars().all()

    import uuid as _uuid
    _run_id = str(_uuid.uuid4())

    applied: list[dict] = []
    skipped: list[dict] = []

    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        bucket = sorted(
            [o for o in all_sell_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )
        _bucket_dates = (bucket[0].signal_date, bucket[-1].signal_date) if bucket else (date.today(), date.today())

        if len(bucket) < min_samples * 2:
            skipped.append({"horizon": h, "reason": f"only {len(bucket)} backfilled SELL samples (need {min_samples * 2} for a valid train/validation split)"})
            _record_tune_history(
                session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
                old_value={"min_pillars_for_sell": 0}, new_value={},
                train_window=_bucket_dates, validation_window=_bucket_dates,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=None, promoted=False,
                gate_failures=[f"insufficient_total_samples:{len(bucket)}<{min_samples * 2}"],
            )
            continue

        split = max(1, int(len(bucket) * 0.7))
        train_bucket = bucket[:split]
        val_bucket = bucket[split:]
        _train_window = (train_bucket[0].signal_date, train_bucket[-1].signal_date)
        _val_window = (val_bucket[0].signal_date, val_bucket[-1].signal_date)

        def _stats_at(min_pillars: int, samples: list, _min_n: int = min_samples) -> dict | None:
            # A candidate gate blocks (compresses) any SELL with FEWER than min_pillars active
            # bearish pillars — the "would this SELL have fired" set is the ones that pass, i.e.
            # bearish_pillars_active >= min_pillars. min_pillars=0 means every SELL passes (the
            # current live behavior — no gate at all), the true baseline to beat.
            sub = [o for o in samples if o.bearish_pillars_active >= min_pillars]
            if len(sub) < _min_n:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            # T232-OC4 / the SELL threshold sweep's own established convention (above): a SELL
            # wins on a NEGATIVE pct_return, so EV must be the negated mean, never the raw mean.
            rets = [-o.pct_return for o in sub if o.pct_return is not None]
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            ev = avg_ret * 100
            return {"n": len(sub), "win_rate": round(acc, 3), "ev_pct": round(ev, 2)}

        # AUD263-SELLPILLAR-GATE-UNMEASURABLE-AND-UNSCHEDULED: a candidate p_i that only barely
        # clears min_samples on the (larger) TRAIN slice can easily fall BELOW min_samples on
        # the (smaller, ~30%) VALIDATION slice purely from the split ratio — nothing here forced
        # a candidate's train-slice subset to actually be large enough to survive the shrink.
        # Live-verified against production: min_pillars=4's train subset (72-86 rows across all
        # 4 horizons) always looked best by EV on the train slice (a classic small-sample
        # overfit — the narrowest, most cherry-picked subset), then correctly failed
        # candidate_unmeasurable_on_validation every single time once its ~30-46-row validation
        # subset fell under the 50-sample floor. _train_min_n scales min_samples by the actual
        # train/validation ratio (not a fixed constant), so a candidate is only ever considered
        # on the train slice if a PROPORTIONAL subset would still be expected to clear
        # min_samples after the same split ratio shrinks it on validation.
        _train_min_n = int(min_samples * (len(train_bucket) / max(1, len(val_bucket))))

        current_pillars = 0  # no gate — the real current live behavior for SELL
        best_ev = -999.0
        best_p: int | None = None
        for p_i in (1, 2, 3, 4):
            st = _stats_at(p_i, train_bucket, _train_min_n)
            if st is not None and st["ev_pct"] > best_ev:
                best_ev = st["ev_pct"]
                best_p = p_i

        if best_p is None:
            skipped.append({"horizon": h, "reason": "no min_pillars_for_sell value met criteria on the train slice"})
            _record_tune_history(
                session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
                old_value={"min_pillars_for_sell": current_pillars}, new_value={},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_bucket), promoted=False,
                gate_failures=["no_candidate_met_train_criteria"],
            )
            continue

        best_stats = _stats_at(best_p, val_bucket)
        current_stats = _stats_at(current_pillars, val_bucket)

        if best_stats is None:
            skipped.append({"horizon": h, "reason": "suggested min_pillars_for_sell unmeasurable on the validation slice"})
            _record_tune_history(
                session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
                old_value={"min_pillars_for_sell": current_pillars}, new_value={"min_pillars_for_sell": best_p},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_bucket), promoted=False,
                gate_failures=["candidate_unmeasurable_on_validation"],
            )
            continue

        if current_stats is None:
            # T232-OC3: no honest baseline measurable at the current (no-gate) setting on this
            # validation slice — do not assume EV 0, that overstates lift. Skip instead.
            skipped.append({"horizon": h, "reason": "baseline (no gate) unmeasurable on the validation slice"})
            _record_tune_history(
                session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
                old_value={"min_pillars_for_sell": current_pillars}, new_value={"min_pillars_for_sell": best_p},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"], baseline_validation_ev_pct=None,
                validation_n=best_stats["n"], promoted=False,
                gate_failures=["baseline_unmeasurable_on_validation"],
            )
            continue

        ev_lift = round(best_stats["ev_pct"] - current_stats["ev_pct"], 2)

        # T232-OC3-FOLLOWUP: never apply a gate with negative or zero validated EV lift,
        # regardless of how large the pillar-count shift looks — matches every sibling
        # mechanism's own unconditional-rejection convention exactly.
        if ev_lift <= 0:
            skipped.append({
                "horizon": h,
                "reason": f"validation-slice EV lift {ev_lift}% is not positive — never apply a non-improving gate",
                "suggested": best_p,
                "current": current_pillars,
            })
            _record_tune_history(
                session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
                old_value={"min_pillars_for_sell": current_pillars}, new_value={"min_pillars_for_sell": best_p},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
                baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
                promoted=False, gate_failures=["ev_lift_not_positive"],
            )
            continue

        _pillars_redis_key = f"stockai:style_tune:{h}:min_pillars_for_sell"
        redis_client.setex(_pillars_redis_key, _REDIS_TTL, str(best_p))
        _mark_tuned(_pillars_redis_key)
        _record_tune_history(
            session, _run_id, "signal_gate", "min_pillars_for_sell", h, "ALL",
            old_value={"min_pillars_for_sell": current_pillars}, new_value={"min_pillars_for_sell": best_p},
            train_window=_train_window, validation_window=_val_window,
            train_ev_pct=best_ev, validation_ev_pct=best_stats["ev_pct"],
            baseline_validation_ev_pct=current_stats["ev_pct"], validation_n=best_stats["n"],
            promoted=True, gate_failures=[],
        )
        applied.append({
            "horizon": h,
            "previous_min_pillars_for_sell": current_pillars,
            "new_min_pillars_for_sell": best_p,
            "ev_lift_pct": ev_lift,
            "train_n": len(train_bucket),
            "validation_stats": best_stats,
        })

    return {
        "applied": applied,
        "skipped": skipped,
        "redis_ttl_days": 30,
        "note": "Requires POST /signals/backfill_bearish_pillars to have run first — this sweep only reads rows with bearish_pillars_active already backfilled.",
    }


@router.post("/tune_style_profiles")
def tune_style_profiles(
    days: int = Query(120, description="Look-back window in calendar days"),
    min_samples: int = Query(10, description="Minimum outcomes required per bucket"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Sweep style-specific gate parameters against live signal_outcomes and apply optimal values.

    For each style × parameter combination, groups outcomes by the relevant field in
    signal.reasons, finds the value that maximises expected-value (win_rate × avg_return),
    and writes it to Redis (stockai:style_tune:{STYLE}:{param}, 30-day TTL).

    Parameters tuned:
      - ml_weight_cap: optimal maximum ML fusion weight per style
      - adx_min: optimal ADX minimum threshold below which signals are compressed
      - high_vol_compression: whether high-vol compression is helping or hurting
      - breadth_compression: whether breadth compression threshold is calibrated

    Signal generator reads these from Redis via _get_style_tuned_param().
    Run weekly (Sunday) alongside TA and conviction weight calibration.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES

    # AUD263-STYLEPROFILES-SUPERSET-BASELINE: the baseline must be the CURRENT LIVE cap's own
    # filtered subset (tune_strategy's own established pattern, :2328), not every validation
    # row unfiltered — see the fix below for the full reasoning.
    CURRENT_ML_CAP: dict[str, float] = {
        h: _STYLE_PROFILES[h]["ml_weight_cap"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    cutoff = date.today() - timedelta(days=days)
    outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    # Fetch reasons JSON for each outcome's signal
    signal_ids = [o.signal_id for o in outcomes if o.signal_id]
    signals_map: dict[int, dict] = {}
    if signal_ids:
        rows = session.execute(
            select(Signal.id, Signal.reasons).where(Signal.id.in_(signal_ids))
        ).all()
        for row in rows:
            if row.reasons:
                signals_map[row.id] = row.reasons

    redis_client = _get_redis()
    _REDIS_TTL = 30 * 86400
    applied: list[dict] = []
    skipped: list[dict] = []
    import uuid as _uuid
    _run_id = str(_uuid.uuid4())

    def _ev_at(subset):
        if not subset:
            return None
        wins = sum(1 for o in subset if o.is_correct)
        rets = [o.pct_return for o in subset if o.pct_return is not None]
        acc = wins / len(subset)
        avg_ret = _stats.mean(rets) if rets else 0.0
        # T232-OC4: avg_ret already IS the expected value — see fix note near the OC3
        # calibration functions above. Multiplying by acc double-counts win probability.
        return avg_ret * 100, acc, avg_ret

    # T234-SIG-INSAMPLE-GATE-TUNING: this function used to sweep every candidate directly
    # against the full sample and apply whatever scored best — an in-sample argmax with no
    # validation, the exact failure mode outcomes_calibrate_apply's own comments document as
    # the cause of a prior live incident (CAL-1). Now uses the same chronological 70/30
    # train/validation split as that sibling endpoint: search for the best candidate on the
    # OLDER 70% (train), then only apply it if it ALSO shows a real edge on the NEWER, never-
    # searched 30% (validation) — a candidate that only looks good in-sample won't survive this.
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        style_outcomes = sorted(
            [o for o in outcomes if o.horizon.value == style],
            key=lambda o: o.signal_date,
        )
        if len(style_outcomes) < min_samples * 4:
            # need enough for train AND validation to each independently clear min_samples * 2
            # (min_samples * 2 was already this function's own per-sweep floor before this fix)
            skipped.append({"style": style, "reason": f"only {len(style_outcomes)} outcomes (need {min_samples * 4} for a valid train/validation split)"})
            continue

        style_with_reasons = [
            (o, signals_map.get(o.signal_id, {}))
            for o in style_outcomes
            if o.signal_id and o.signal_id in signals_map
        ]
        if len(style_with_reasons) < min_samples * 2:
            skipped.append({"style": style, "reason": f"only {len(style_with_reasons)} outcomes with reasons JSON"})
            continue

        split = max(1, int(len(style_with_reasons) * 0.7))
        train_sr = style_with_reasons[:split]
        val_sr = style_with_reasons[split:]
        _train_window = (train_sr[0][0].signal_date, train_sr[-1][0].signal_date)
        _val_window = (val_sr[0][0].signal_date, val_sr[-1][0].signal_date)

        # ── ml_weight_cap: sweep 0.15–0.75, find cap where EV is maximised on TRAIN,
        #    then require the SAME cap to beat the effectively-uncapped baseline on VALIDATION ──
        best_ml_ev, best_ml_cap = -999.0, None
        for cap_int in range(15, 76, 5):
            cap = cap_int / 100.0
            sub = [o for o, r in train_sr if r.get("ml_weight", 0) <= cap + 0.05]
            if len(sub) < min_samples:
                continue
            ev_result = _ev_at(sub)
            if ev_result and ev_result[0] > best_ml_ev:
                best_ml_ev = ev_result[0]
                best_ml_cap = cap

        if best_ml_cap is not None:
            val_sub = [o for o, r in val_sr if r.get("ml_weight", 0) <= best_ml_cap + 0.05]
            # AUD263-STYLEPROFILES-SUPERSET-BASELINE: the baseline used to be EVERY validation
            # outcome, unfiltered — a strict SUPERSET of val_sub (which is always a subset of
            # the same rows, filtered tighter). "Does removing some rows raise the mean" is
            # close to a coin flip whenever the excluded high-ml_weight rows happen to have
            # below-average returns in that window, so the candidate beat its own superset by
            # chance on a regular basis, with no real edge required. Now filtered by the
            # CURRENT LIVE cap instead (tune_strategy's own already-correct pattern, :2328) —
            # a real, comparably-sized subset the candidate must genuinely beat, not something
            # it structurally contains.
            baseline_sub = [o for o, r in val_sr if r.get("ml_weight", 0) <= CURRENT_ML_CAP[style] + 0.05]
            val_result = _ev_at(val_sub)
            baseline_result = _ev_at(baseline_sub)
            _ml_promoted = bool(
                val_result and baseline_result and len(val_sub) >= min_samples and val_result[0] > baseline_result[0]
            )
            if _ml_promoted:
                _ml_cap_redis_key = f"stockai:style_tune:{style}:ml_weight_cap"
                redis_client.setex(_ml_cap_redis_key, _REDIS_TTL, str(round(best_ml_cap, 2)))
                _mark_tuned(_ml_cap_redis_key)
                applied.append({"style": style, "param": "ml_weight_cap", "value": best_ml_cap,
                                "train_ev_pct": round(best_ml_ev, 2), "validation_ev_pct": round(val_result[0], 2),
                                "validation_baseline_ev_pct": round(baseline_result[0], 2)})
            else:
                skipped.append({"style": style, "param": "ml_weight_cap",
                                "reason": "did not beat baseline (or insufficient samples) on the validation slice",
                                "train_best_cap": best_ml_cap})
            _record_tune_history(
                session, _run_id, "gate_threshold", "ml_weight_cap", style, "ALL",
                old_value={"ml_weight_cap": CURRENT_ML_CAP[style]}, new_value={"ml_weight_cap": best_ml_cap},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=round(best_ml_ev, 2), validation_ev_pct=round(val_result[0], 2) if val_result else None,
                baseline_validation_ev_pct=round(baseline_result[0], 2) if baseline_result else None,
                validation_n=len(val_sub), promoted=_ml_promoted,
                gate_failures=[] if _ml_promoted else ["did_not_beat_baseline_or_insufficient_validation_samples"],
            )

        # ── adx_min: find ADX level below which accuracy < 45% on TRAIN, then confirm the
        #    same threshold still shows below/above separation on VALIDATION ──
        adx_train = [(o, r) for o, r in train_sr if r.get("adx") is not None]
        adx_val = [(o, r) for o, r in val_sr if r.get("adx") is not None]
        if len(adx_train) >= min_samples:
            best_adx = None
            for adx_thresh in range(10, 40, 2):
                below = [o for o, r in adx_train if r.get("adx", 99) < adx_thresh]
                above = [o for o, r in adx_train if r.get("adx", 0) >= adx_thresh]
                if len(below) < min_samples or len(above) < min_samples:
                    continue
                below_acc = sum(1 for o in below if o.is_correct) / len(below)
                above_acc = sum(1 for o in above if o.is_correct) / len(above)
                if below_acc < 0.45 and above_acc > below_acc + 0.05:
                    best_adx = adx_thresh
                    break
            if best_adx is not None:
                val_below = [o for o, r in adx_val if r.get("adx", 99) < best_adx]
                val_above = [o for o, r in adx_val if r.get("adx", 0) >= best_adx]
                # T233-SELFIMPROVE-PHASE3 extension: adx_min/breadth_compression compare
                # accuracy, not EV — TuneHistory's EV columns stay NULL for these two params
                # rather than force a misleading number; see docs/DESIGN_TUNE_HISTORY_EXTENSION.
                #
                # TUNE-VALIDATION-BAR-INCONSISTENT: this validation-slice sample bar is
                # deliberately looser than ml_weight_cap's (min_samples, checked in full above)
                # — min_samples // 2 per side here, min_samples // 4 per side for
                # breadth_compression below. This is intentional, not an oversight: ml_weight_cap
                # is gated on an EV comparison (a continuous, noisier statistic that needs more
                # data to trust), while adx_min/breadth_compression are gated on a simple
                # below-vs-above accuracy split (a coarser, less sample-hungry comparison) that
                # is ALSO checked twice — once on train (this file, ~15 lines up) and again here
                # on validation — so the validation bar only needs to confirm a direction the
                # train slice already found, not discover one from scratch. If you're tightening
                # or loosening one of these three bars, this is deliberate asymmetry to preserve,
                # not a bug to "fix" into consistency.
                if len(val_below) >= min_samples // 2 and len(val_above) >= min_samples // 2:
                    val_below_acc = sum(1 for o in val_below if o.is_correct) / len(val_below)
                    val_above_acc = sum(1 for o in val_above if o.is_correct) / len(val_above)
                    _adx_promoted = val_below_acc < val_above_acc
                    if _adx_promoted:
                        _adx_redis_key = f"stockai:style_tune:{style}:adx_min"
                        redis_client.setex(_adx_redis_key, _REDIS_TTL, str(best_adx))
                        _mark_tuned(_adx_redis_key)
                        applied.append({"style": style, "param": "adx_min", "value": best_adx,
                                        "validation_below_acc": round(val_below_acc, 3),
                                        "validation_above_acc": round(val_above_acc, 3)})
                    else:
                        skipped.append({"style": style, "param": "adx_min",
                                        "reason": "below/above separation did not replicate on validation slice",
                                        "train_threshold": best_adx})
                    _record_tune_history(
                        session, _run_id, "gate_threshold", "adx_min", style, "ALL",
                        old_value={}, new_value={"adx_min": best_adx},
                        train_window=_train_window, validation_window=_val_window,
                        train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                        validation_n=len(val_below) + len(val_above), promoted=_adx_promoted,
                        gate_failures=[] if _adx_promoted else ["below_above_separation_did_not_replicate"],
                    )
                else:
                    skipped.append({"style": style, "param": "adx_min",
                                    "reason": "insufficient validation-slice samples to confirm train threshold",
                                    "train_threshold": best_adx})
                    _record_tune_history(
                        session, _run_id, "gate_threshold", "adx_min", style, "ALL",
                        old_value={}, new_value={"adx_min": best_adx},
                        train_window=_train_window, validation_window=_val_window,
                        train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                        validation_n=len(val_below) + len(val_above), promoted=False,
                        gate_failures=["insufficient_validation_samples"],
                    )

        # ── breadth_compression: verify compression is justified on TRAIN (breadth<40
        #    underperforms), then confirm the same direction holds on VALIDATION ──
        # TUNE-VALIDATION-BAR-INCONSISTENT: validation bar here is min_samples // 4 per side,
        # looser still than adx_min's // 2 above — see the comment at adx_min's validation
        # check for why this asymmetry across all three parameters is intentional.
        breadth_train = [(o, r) for o, r in train_sr if r.get("breadth_pct") is not None]
        breadth_val = [(o, r) for o, r in val_sr if r.get("breadth_pct") is not None]
        if len(breadth_train) >= min_samples:
            low_breadth  = [o for o, r in breadth_train if r.get("breadth_pct", 100) < 40]
            high_breadth = [o for o, r in breadth_train if r.get("breadth_pct", 0) >= 40]
            if len(low_breadth) >= min_samples // 2 and len(high_breadth) >= min_samples // 2:
                lb_acc = sum(1 for o in low_breadth if o.is_correct) / len(low_breadth)
                hb_acc = sum(1 for o in high_breadth if o.is_correct) / len(high_breadth)
                val_low  = [o for o, r in breadth_val if r.get("breadth_pct", 100) < 40]
                val_high = [o for o, r in breadth_val if r.get("breadth_pct", 0) >= 40]
                _val_ok = len(val_low) >= min_samples // 4 and len(val_high) >= min_samples // 4
                if lb_acc < hb_acc - 0.08:
                    if _val_ok:
                        val_lb_acc = sum(1 for o in val_low if o.is_correct) / len(val_low)
                        val_hb_acc = sum(1 for o in val_high if o.is_correct) / len(val_high)
                        _bc_promoted = val_lb_acc < val_hb_acc
                        if _bc_promoted:
                            new_bc = 0.88  # tighter than default 0.90
                            _bc_redis_key = f"stockai:style_tune:{style}:breadth_compression"
                            redis_client.setex(_bc_redis_key, _REDIS_TTL, str(new_bc))
                            _mark_tuned(_bc_redis_key)
                            applied.append({"style": style, "param": "breadth_compression", "value": new_bc,
                                            "train_low_acc": round(lb_acc, 3), "train_high_acc": round(hb_acc, 3),
                                            "validation_low_acc": round(val_lb_acc, 3), "validation_high_acc": round(val_hb_acc, 3)})
                        else:
                            skipped.append({"style": style, "param": "breadth_compression",
                                            "reason": "low-breadth underperformance did not replicate on validation slice"})
                        _record_tune_history(
                            session, _run_id, "gate_threshold", "breadth_compression", style, "ALL",
                            old_value={"breadth_compression": 0.90}, new_value={"breadth_compression": 0.88},
                            train_window=_train_window, validation_window=_val_window,
                            train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                            validation_n=len(val_low) + len(val_high), promoted=_bc_promoted,
                            gate_failures=[] if _bc_promoted else ["low_breadth_underperformance_did_not_replicate"],
                        )
                    else:
                        skipped.append({"style": style, "param": "breadth_compression",
                                        "reason": "insufficient validation-slice samples to confirm train finding"})
                        _record_tune_history(
                            session, _run_id, "gate_threshold", "breadth_compression", style, "ALL",
                            old_value={"breadth_compression": 0.90}, new_value={"breadth_compression": 0.88},
                            train_window=_train_window, validation_window=_val_window,
                            train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                            validation_n=len(val_low) + len(val_high), promoted=False,
                            gate_failures=["insufficient_validation_samples"],
                        )
                elif lb_acc > hb_acc - 0.02:
                    # Breadth not predictive on train — restore default without needing validation
                    new_bc = 0.95
                    _bc_redis_key = f"stockai:style_tune:{style}:breadth_compression"
                    redis_client.setex(_bc_redis_key, _REDIS_TTL, str(new_bc))
                    _mark_tuned(_bc_redis_key)
                    applied.append({"style": style, "param": "breadth_compression", "value": new_bc,
                                    "note": "low-breadth underperformance not significant on train slice"})
                    _record_tune_history(
                        session, _run_id, "gate_threshold", "breadth_compression", style, "ALL",
                        old_value={"breadth_compression": 0.90}, new_value={"breadth_compression": 0.95},
                        train_window=_train_window, validation_window=_val_window,
                        train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                        validation_n=len(low_breadth) + len(high_breadth), promoted=True,
                        gate_failures=[],
                    )

    return {"applied": applied, "skipped": skipped, "n_outcomes_analyzed": len(outcomes), "redis_ttl_days": 30}


# T255-STRATEGY-TUNER-PER-HORIZON: every existing calibration mechanism above
# (outcomes_calibrate_apply, tune_style_profiles) tunes exactly ONE parameter at a time in
# isolation, against its own independent train/validation split. Neither has ever searched for
# the best COMBINATION of buy_threshold + ml_weight_cap together — a candidate that looks best
# for buy_threshold alone need not be the best pairing once ml_weight_cap also shifts which
# outcomes clear the bar (a lower ml_weight_cap changes fused_prob for every outcome in the
# sweep, which changes which of them clear any given buy_threshold). Phase 1 of the design in
# .claude/CLAUDE.md's "Research: Per-Horizon AI Signal Strategy Tuning" section — the grid is
# kept small (31 buy_threshold levels x 13 ml_weight_cap levels = 403 cells) specifically to
# bound multiple-comparison overfit risk against the ~n=100-120-outcome baseline documented
# there for SHORT/SWING; LONG/GROWTH are expected to skip until more data accumulates, and the
# response says so explicitly rather than silently omitting them.
_TUNE_STRATEGY_BUY_GRID = [i / 100.0 for i in range(55, 86)]        # 0.55-0.85 step 0.01 (31)
_TUNE_STRATEGY_ML_CAP_GRID = [i / 100.0 for i in range(15, 76, 5)]  # 0.15-0.75 step 0.05 (13)
_TUNE_STRATEGY_MIN_SAMPLES = 15  # per train/validation slice at a given grid cell — looser than
# outcomes_calibrate_apply's 50 (single-parameter sweep) since a 2D grid already spreads a
# smaller pool of outcomes across 403 cells; this is the floor for a cell to be considered at
# all, not a claim that 15 is as reliable as 50 — the validation-beats-baseline gate below is
# what actually protects against a noisy cell being promoted.
_TUNE_STRATEGY_BUY_BOUNDS = (0.55, 0.85)
_TUNE_STRATEGY_ML_CAP_BOUNDS = (0.15, 0.75)

# AUD263-TUNESTRATEGY-NO-MULTIPLE-COMPARISONS-CORRECTION: a bare `ev_lift <= 0` floor (below)
# is not enough protection for a 403-cell grid argmax at n=15/cell. With real per-trade return
# SD around 10pp, the SE of a cell mean is ~2.6pp; the max of ~403 correlated draws sits
# roughly 2-3 SE above the true mean, implying an expected upward bias of 5-8pp of EV on the
# TRAIN slice from noise alone — and the single validation check has the same n/SE, so it
# rejects only about half of pure-noise "winners" (a near coin-flip, the exact failure mode
# gate_harness.py's own BUG233-BACKTESTHARNESS-COINFLIP already found and fixed for a SMALLER
# search space). Ported here verbatim (same two constants, same two-part test: an absolute
# floor AND a fraction of the validation slice's own return dispersion) rather than reusing
# gate_harness.py's _passes_promotion_margin() directly — that function's signature is built
# around market-data's BacktestResult dataclass (a different service, a different data shape);
# this operates on the same raw per-outcome pct_return lists tune_strategy already has in hand.
_MIN_PROMOTION_EV_LIFT_PCT = 0.5    # candidate must beat baseline by at least this many pct points
_MIN_PROMOTION_LIFT_SD_RATIO = 0.5  # ...and by at least this fraction of the validation slice's own return SD


def _passes_promotion_margin(candidate_rets: list[float], baseline_rets: list[float], ev_lift: float) -> bool:
    """Stricter replacement for a bare `ev_lift > 0`/`ev_lift <= 0` floor — see the module-level
    comment above for why. Requires the lift to clear an absolute pp floor AND be a meaningful
    fraction of the combined validation-slice return dispersion, matching gate_harness.py's own
    _passes_promotion_margin() exactly (same two constants, same two-part test)."""
    if ev_lift < _MIN_PROMOTION_EV_LIFT_PCT:
        return False
    combined = list(candidate_rets) + list(baseline_rets)
    if len(combined) < 2:
        return False
    mean = sum(combined) / len(combined)
    variance = sum((r - mean) ** 2 for r in combined) / (len(combined) - 1)
    sd_pct = (variance ** 0.5) * 100  # pct_return is stored as a fraction; result is in pct points
    if sd_pct <= 0:
        return True  # zero dispersion means the lift (already >= the absolute floor) is real
    return ev_lift >= _MIN_PROMOTION_LIFT_SD_RATIO * sd_pct


@router.post("/tune_strategy")
def tune_strategy(
    days: int = Query(180, description="Look-back window in calendar days"),
    min_samples: int = Query(_TUNE_STRATEGY_MIN_SAMPLES, description="Minimum outcomes required per grid cell, per slice"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Joint per-horizon grid sweep over (buy_threshold x ml_weight_cap) — Phase 1 of
    T255-STRATEGY-TUNER-PER-HORIZON. Re-filters ALREADY-STORED SignalOutcome.fused_prob +
    Signal.reasons["ml_weight"] — no signal regeneration needed, matching outcomes_calibrate_apply
    and tune_style_profiles' own no-regeneration speed advantage.

    For each horizon, searches the (buy_threshold, ml_weight_cap) grid cell that maximises
    expected value on the OLDER 70% of the window (train), then only applies it if it ALSO
    beats the CURRENT LIVE baseline's own EV on the NEWER, never-searched 30% (validation) —
    the same chronological walk-forward split, negative-lift rejection, and per-attempt
    TuneHistory recording every other mechanism in this file uses. Applies through the SAME
    Redis keys outcomes_calibrate_apply/tune_style_profiles already write
    (stockai:signal_thresholds:{H}, stockai:style_tune:{H}:ml_weight_cap) — the read side
    (_get_dynamic_buy_threshold, _get_style_tuned_param) needs zero changes.

    A grid cell's fused_prob depends on ml_weight_cap only through Signal.reasons["ml_weight"]
    already recorded at signal-generation time (the ORIGINAL ml_weight actually used, not a
    replay of what it would have been under a different cap) — this sweep answers "if we had
    filtered to signals whose actual ml_weight was already <= this cap, which (threshold, cap)
    combination would have looked best," a re-filtering exercise, not a full re-simulation.
    See the design doc's own Phase 4 for why this can only ever evaluate TIGHTENING, never a
    looser cap or threshold than what was actually live when the outcome was recorded.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES

    CURRENT_BUY: dict[str, float] = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }
    CURRENT_ML_CAP: dict[str, float] = {
        h: _STYLE_PROFILES[h]["ml_weight_cap"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    cutoff = date.today() - timedelta(days=days)
    all_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    # Join to Signal.reasons for ml_weight — same pattern tune_style_profiles already uses.
    signal_ids = [o.signal_id for o in all_outcomes if o.signal_id]
    signals_map: dict[int, dict] = {}
    if signal_ids:
        rows = session.execute(
            select(Signal.id, Signal.reasons).where(Signal.id.in_(signal_ids))
        ).all()
        for row in rows:
            if row.reasons:
                signals_map[row.id] = row.reasons

    redis_client = _get_redis()
    _REDIS_TTL = 30 * 86400
    applied: list[dict] = []
    skipped: list[dict] = []
    import uuid as _uuid
    _run_id = str(_uuid.uuid4())

    def _ev_at(subset: list) -> dict | None:
        if len(subset) < min_samples:
            return None
        wins = sum(1 for o in subset if o.is_correct)
        rets = [o.pct_return for o in subset if o.pct_return is not None]
        acc = wins / len(subset)
        avg_ret = _stats.mean(rets) if rets else 0.0
        # T232-OC4 convention: avg_ret already IS the expected value (mean return across all
        # trades, wins and losses) — do not multiply by acc again (double-counts win probability).
        # AUD263-TUNESTRATEGY-NO-MULTIPLE-COMPARISONS-CORRECTION: `rets` (the raw per-trade
        # return fractions, not just the mean) is kept alongside ev_pct so
        # _passes_promotion_margin() can compute the validation slice's own return dispersion —
        # a bare EV-lift comparison alone can't distinguish a real edge from grid-search noise.
        return {"n": len(subset), "win_rate": round(acc, 3), "ev_pct": round(avg_ret * 100, 2), "rets": rets}

    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        current_buy = CURRENT_BUY[h]
        current_cap = CURRENT_ML_CAP[h]

        with_reasons = sorted(
            [
                (o, signals_map[o.signal_id])
                for o in all_outcomes
                if o.horizon.value == h and o.signal_id in signals_map and o.fused_prob is not None
            ],
            key=lambda pair: pair[0].signal_date,
        )
        # Need enough for BOTH a train slice and a validation slice to each independently have a
        # chance at clearing min_samples in the best cell — mirrors outcomes_calibrate_apply's
        # own "insufficient_total_samples" floor, doubled for the same reason (a lopsided split
        # of an already-small pool produces two under-powered halves).
        if len(with_reasons) < min_samples * 2:
            skipped.append({
                "horizon": h,
                "reason": f"only {len(with_reasons)} outcomes with reasons JSON (need {min_samples * 2} for a valid train/validation split)",
            })
            _bucket_dates = (
                (with_reasons[0][0].signal_date, with_reasons[-1][0].signal_date)
                if with_reasons else (date.today(), date.today())
            )
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value={"buy_threshold": current_buy, "ml_weight_cap": current_cap}, new_value={},
                train_window=_bucket_dates, validation_window=_bucket_dates,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=None, promoted=False,
                gate_failures=[f"insufficient_total_samples:{len(with_reasons)}<{min_samples * 2}"],
            )
            continue

        split = max(1, int(len(with_reasons) * 0.7))
        train_wr = with_reasons[:split]
        val_wr = with_reasons[split:]
        _train_window = (train_wr[0][0].signal_date, train_wr[-1][0].signal_date)
        _val_window = (val_wr[0][0].signal_date, val_wr[-1][0].signal_date)

        # Search the full (buy_threshold x ml_weight_cap) grid on TRAIN only. A cell's subset is
        # every outcome whose recorded ml_weight was already <= cap AND whose fused_prob clears
        # threshold — both filters applied on the SAME already-recorded fused_prob (see docstring
        # for why this is a re-filter, not a re-simulation).
        best_ev = -999.0
        best_buy: float | None = None
        best_cap: float | None = None
        for cap in _TUNE_STRATEGY_ML_CAP_GRID:
            cap_subset = [o for o, r in train_wr if r.get("ml_weight", 0) <= cap + 0.05]
            if len(cap_subset) < min_samples:
                continue
            for buy_t in _TUNE_STRATEGY_BUY_GRID:
                cell = [o for o in cap_subset if o.fused_prob >= buy_t]
                st = _ev_at(cell)
                if st is not None and st["ev_pct"] > best_ev:
                    best_ev = st["ev_pct"]
                    best_buy = buy_t
                    best_cap = cap

        if best_buy is None or best_cap is None:
            skipped.append({"horizon": h, "reason": "no grid cell met sample/EV criteria on the train slice"})
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value={"buy_threshold": current_buy, "ml_weight_cap": current_cap}, new_value={},
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_wr), promoted=False,
                gate_failures=["no_candidate_met_train_criteria"],
            )
            continue

        # Validate: both the candidate cell AND the current live baseline must be independently
        # measurable on the VALIDATION slice — data the grid search never saw.
        val_cap_subset = [o for o, r in val_wr if r.get("ml_weight", 0) <= best_cap + 0.05]
        val_cell = [o for o in val_cap_subset if o.fused_prob >= best_buy]
        candidate_stats = _ev_at(val_cell)

        baseline_cap_subset = [o for o, r in val_wr if r.get("ml_weight", 0) <= current_cap + 0.05]
        baseline_cell = [o for o in baseline_cap_subset if o.fused_prob >= current_buy]
        baseline_stats = _ev_at(baseline_cell)

        _new_value = {"buy_threshold": best_buy, "ml_weight_cap": best_cap}
        _old_value = {"buy_threshold": current_buy, "ml_weight_cap": current_cap}

        if candidate_stats is None:
            skipped.append({"horizon": h, "reason": "candidate cell unmeasurable on validation slice (insufficient samples)"})
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value=_old_value, new_value=_new_value,
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
                validation_n=len(val_wr), promoted=False,
                gate_failures=["candidate_unmeasurable_on_validation"],
            )
            continue

        if baseline_stats is None:
            # T232-OC3 convention: no honest baseline measurable on validation — do not assume
            # EV 0 (would overstate lift and apply too eagerly). Skip instead.
            skipped.append({"horizon": h, "reason": "current live baseline unmeasurable on validation slice (insufficient samples)"})
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value=_old_value, new_value=_new_value,
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"], baseline_validation_ev_pct=None,
                validation_n=candidate_stats["n"], promoted=False,
                gate_failures=["baseline_unmeasurable_on_validation"],
            )
            continue

        ev_lift = round(candidate_stats["ev_pct"] - baseline_stats["ev_pct"], 2)

        # AUD263-TUNESTRATEGY-NO-MULTIPLE-COMPARISONS-CORRECTION: replaces the old two-stage
        # `ev_lift < 0` hard floor + `ev_lift < _MIN_EV_LIFT` soft floor (with a shift-size
        # escape hatch) with gate_harness.py's own stricter, simulation-verified margin — an
        # absolute pp floor AND a fraction of the validation slice's own return dispersion. This
        # is STRICTER than the old logic in every case (a shift-size escape hatch could
        # previously let a large-but-zero-lift shift through; that path no longer exists), so no
        # previously-rejected candidate can newly pass under this change.
        if not _passes_promotion_margin(candidate_stats["rets"], baseline_stats["rets"], ev_lift):
            skipped.append({
                "horizon": h,
                "reason": (
                    f"validation-slice EV lift {ev_lift}% does not clear the promotion margin "
                    f"(>= {_MIN_PROMOTION_EV_LIFT_PCT}pp AND >= {_MIN_PROMOTION_LIFT_SD_RATIO}x "
                    "the validation slice's own return SD)"
                ),
                "candidate": _new_value, "current": _old_value,
            })
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value=_old_value, new_value=_new_value,
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
                baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n"],
                promoted=False, gate_failures=["ev_lift_below_promotion_margin"],
            )
            continue

        if not (_TUNE_STRATEGY_BUY_BOUNDS[0] <= best_buy <= _TUNE_STRATEGY_BUY_BOUNDS[1]) or \
           not (_TUNE_STRATEGY_ML_CAP_BOUNDS[0] <= best_cap <= _TUNE_STRATEGY_ML_CAP_BOUNDS[1]):
            skipped.append({"horizon": h, "reason": f"candidate {_new_value} outside sane bounds"})
            _record_tune_history(
                session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
                old_value=_old_value, new_value=_new_value,
                train_window=_train_window, validation_window=_val_window,
                train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
                baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n"],
                promoted=False, gate_failures=["suggested_outside_sane_bounds"],
            )
            continue

        # Apply through the EXISTING Redis keys — buy_threshold as a bull-baseline-relative
        # write (read side applies per-regime via _get_dynamic_buy_threshold), ml_weight_cap as
        # a flat value (read side via _get_style_tuned_param) — exact same keys/semantics the
        # single-parameter mechanisms already write, so _decide_style()/signal generation code
        # needs zero changes to pick this up.
        _buy_thresh_redis_key = f"stockai:signal_thresholds:{h}"
        _ml_cap_redis_key = f"stockai:style_tune:{h}:ml_weight_cap"
        redis_client.setex(_buy_thresh_redis_key, _REDIS_TTL, str(round(best_buy, 4)))
        redis_client.setex(_ml_cap_redis_key, _REDIS_TTL, str(round(best_cap, 2)))
        _mark_tuned(_buy_thresh_redis_key)
        _mark_tuned(_ml_cap_redis_key)
        # AUD263-WATCHDOG-MASKS-VALIDATED-THRESHOLD: same reasoning as outcomes_calibrate_apply's
        # own call — a fresh, validated recalibration must not stay shadowed by a stale watchdog
        # emergency nudge that predates it.
        _clear_watchdog_override(h)
        _record_tune_history(
            session, _run_id, "joint_strategy", "buy_threshold+ml_weight_cap", h, "ALL",
            old_value=_old_value, new_value=_new_value,
            train_window=_train_window, validation_window=_val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n"],
            promoted=True, gate_failures=[],
        )
        applied.append({
            "horizon": h,
            "previous": _old_value,
            "new": _new_value,
            "train_ev_pct": best_ev,
            "validation_ev_pct": candidate_stats["ev_pct"],
            "validation_baseline_ev_pct": baseline_stats["ev_pct"],
            "ev_lift_pct": ev_lift,
            "validation_n": candidate_stats["n"],
        })

    return {
        "applied": applied,
        "skipped": skipped,
        "n_outcomes_analyzed": len(all_outcomes),
        "grid_size": len(_TUNE_STRATEGY_BUY_GRID) * len(_TUNE_STRATEGY_ML_CAP_GRID),
        "redis_ttl_days": 30,
    }


@router.post("/watchdog")
def signal_watchdog(
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Self-healing threshold watchdog: monitor rolling win rates and auto-adjust.

    Checks the last 14 days of RESOLVABLE signals per style (a signal only becomes
    resolvable once its own hold window has elapsed — see the window note below). If
    win rate drops below 38%, applies an emergency threshold tightening (+0.03). If
    signal count drops to zero for 7+ consecutive days, relaxes the threshold by 0.02
    (floor: hardcoded default).

    Writes to stockai:watchdog:{STYLE}:threshold (Redis, 7-day TTL) — this key is
    read by _get_dynamic_buy_threshold() BEFORE the calibrated key, ensuring the
    watchdog's response is immediate.

    Caps adjustments at 3 tightenings before requiring a manual review (prevents
    the system from silencing itself completely).

    Schedule: daily (06:00 ET) from market-data scheduler.
    """
    from ..generators.signals import _STYLE_PROFILES
    import uuid as _uuid

    _7D  = date.today() - timedelta(days=7)
    _REDIS_TTL_7D = 7 * 86400
    _MAX_TIGHTEN = 3
    # AUD232-018: every other threshold-mutation path in this file (outcomes_calibrate_apply,
    # tune_style_profiles, calibrate_ml_weight) requires 2x-4x min_samples plus a walk-forward
    # validation split before applying a change. signal_watchdog previously acted on as few as
    # 5 fourteen-day samples with none of that — raised the floor to reduce acting on pure
    # noise (a full walk-forward split isn't a fit here: watchdog is deliberately a fast, daily
    # reactive nudge with fixed +0.03/-0.02 deltas, not a threshold search) and every action now
    # gets a TuneHistory row so it's at least auditable after the fact, matching what every
    # other mutation path already records.
    _MIN_SAMPLES = 15
    _run_id = str(_uuid.uuid4())

    # Bull-regime thresholds as floors — source of truth is _STYLE_PROFILES (T232-SIG12).
    _DEFAULT_THRESHOLDS = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    redis_client = _get_redis()
    actions: list[dict] = []
    status: list[dict] = []

    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        # T243-TUNE-WINDOW: a flat 14-day signal_date cutoff can never surface outcomes for
        # any style whose hold window is >=14 days — a signal only resolves hold_days after
        # signal_date, so by the time it CAN resolve, signal_date has already aged out of a
        # bare 14-day window (LONG's 28-day hold makes this a mathematical impossibility, not
        # just unlikely — it would show 0 outcomes forever, no matter how long the system
        # ran). Widen the signal_date floor by that style's own hold window so "last 14 days"
        # means "14 days of signals that have HAD TIME to resolve," not "14 days of raw age."
        _hold_days = _OUTCOME_HOLD_DAYS.get(style, 14)
        _win_start = date.today() - timedelta(days=14 + _hold_days)
        outcomes_14d = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.signal_date >= _win_start,
                SignalOutcome.is_correct.is_not(None),
                SignalOutcome.signal_direction == "BUY",
                SignalOutcome.horizon == SignalHorizon[style],
            )
        ).scalars().all()

        # 7-day signal count (regardless of evaluation status)
        signals_7d = session.execute(
            select(func.count(Signal.id)).where(
                Signal.ts >= _7D,
                Signal.signal == SignalType.BUY,
                Signal.horizon == SignalHorizon[style],
            )
        ).scalar() or 0

        win_rate_14d = None
        if outcomes_14d:
            wins = sum(1 for o in outcomes_14d if o.is_correct)
            win_rate_14d = wins / len(outcomes_14d)

        # Current watchdog adjustment
        current_key = f"stockai:watchdog:{style}:threshold"
        tighten_count_key = f"stockai:watchdog:{style}:tighten_count"
        current_adj = redis_client.get(current_key)
        tighten_count = int(redis_client.get(tighten_count_key) or 0)

        floor_threshold = _DEFAULT_THRESHOLDS.get(style, 0.65)

        # SELFIMPROVE-CROSS-MECHANISM-BLINDNESS: before acting, check whether some OTHER
        # tuning mechanism (calibrate_ta_weights, calibrate_conviction_weights,
        # calibrate_ml_weight, outcomes_calibrate_apply, tune_style_profiles — anything NOT
        # triggered_by="watchdog") already changed this style within its own hold window. A
        # recalibration shifts the style's effective scoring, so a win-rate dip right after one
        # could be "still absorbing the recalibration," not "genuinely getting worse" — the
        # watchdog has no way to tell those apart today. Rather than skip acting outright (a
        # real emergency win-rate crash still deserves an immediate response — that's the whole
        # point of a fast reactive nudge, not a search), flag it: the action still fires, but
        # the TuneHistory row records that a coupled change landed recently so a human reviewing
        # the history later can see two changes close together rather than treating the
        # watchdog's action as an independent, uncorrelated signal.
        _recent_coupled_change = session.execute(
            select(TuneHistory.parameter_class, TuneHistory.parameter_name, TuneHistory.ts)
            .where(
                TuneHistory.style == style,
                TuneHistory.promoted.is_(True),
                TuneHistory.triggered_by != "watchdog",
                TuneHistory.ts >= datetime.now(timezone.utc) - timedelta(days=_hold_days),
            )
            .order_by(TuneHistory.ts.desc())
            .limit(1)
        ).first()
        _coupled_note = (
            f"{_recent_coupled_change.parameter_class}.{_recent_coupled_change.parameter_name} "
            f"promoted {_recent_coupled_change.ts.isoformat()}"
        ) if _recent_coupled_change else None
        if _coupled_note:
            log.info("signal_watchdog.recent_coupled_change_detected", style=style, note=_coupled_note)

        action = None
        _tune_window = (_win_start, date.today())
        if win_rate_14d is not None and win_rate_14d < 0.38 and len(outcomes_14d) >= _MIN_SAMPLES:
            if tighten_count >= _MAX_TIGHTEN:
                action = "max_tighten_reached_manual_review_needed"
                actions.append({"style": style, "action": action, "win_rate_14d": round(win_rate_14d, 3)})
            else:
                # Tighten by 0.03 from the current adjustment (or calibrated base)
                current_val = float(current_adj) if current_adj else (
                    float(redis_client.get(f"stockai:signal_thresholds:{style}") or 0) or floor_threshold
                )
                new_val = min(current_val + 0.03, floor_threshold + 0.12)  # max +12pp above floor
                redis_client.setex(current_key, _REDIS_TTL_7D, str(round(new_val, 4)))
                redis_client.setex(tighten_count_key, _REDIS_TTL_7D, str(tighten_count + 1))
                action = "tightened"
                actions.append({"style": style, "action": action, "from": round(current_val, 4),
                                 "to": round(new_val, 4), "win_rate_14d": round(win_rate_14d, 3),
                                 "tighten_count": tighten_count + 1,
                                 "recent_coupled_change": _coupled_note})
                _record_tune_history(
                    session, _run_id, "signal_threshold", "watchdog_buy_threshold", style, "ALL",
                    old_value={"threshold": current_val}, new_value={"threshold": new_val},
                    train_window=_tune_window, validation_window=_tune_window,
                    train_ev_pct=None, validation_ev_pct=round(win_rate_14d, 4),
                    baseline_validation_ev_pct=None, validation_n=len(outcomes_14d),
                    promoted=True,
                    gate_failures=[f"recent_coupled_change:{_coupled_note}"] if _coupled_note else [],
                    triggered_by="watchdog",
                )

        elif signals_7d == 0 and current_adj:
            # No signals for 7 days — the threshold may be too tight; relax
            current_val = float(current_adj)
            if current_val > floor_threshold + 0.01:
                new_val = max(current_val - 0.02, floor_threshold)
                redis_client.setex(current_key, _REDIS_TTL_7D, str(round(new_val, 4)))
                redis_client.delete(tighten_count_key)  # reset tighten count on relax
                action = "relaxed"
                _record_tune_history(
                    session, _run_id, "signal_threshold", "watchdog_buy_threshold", style, "ALL",
                    old_value={"threshold": current_val}, new_value={"threshold": new_val},
                    train_window=_tune_window, validation_window=_tune_window,
                    train_ev_pct=None, validation_ev_pct=None,
                    baseline_validation_ev_pct=None, validation_n=signals_7d,
                    promoted=True,
                    gate_failures=[f"recent_coupled_change:{_coupled_note}"] if _coupled_note else [],
                    triggered_by="watchdog",
                )
                actions.append({"style": style, "action": action, "from": round(current_val, 4),
                                 "to": round(new_val, 4), "signals_7d": signals_7d,
                                 "recent_coupled_change": _coupled_note})

        # T243-TUNE-SILENT-EXPIRY: if an override is active but neither the tighten nor the
        # relax branch fired this run, the key is coasting toward its 7-day TTL with no
        # explicit "still active" or "about to lapse" signal anywhere — an operator watching
        # only the dashboard's "Nominal" pill has no way to tell "healthy" from "override just
        # expired with nobody reviewing whether the underlying condition actually improved."
        # Log it so at least a log-based alert/dashboard could catch it; not changed: whether
        # the override auto-renews (deliberately not renewing here — a stale win-rate read
        # that keeps re-tightening every day without ever re-validating is its own risk).
        if action is None and current_adj is not None:
            ttl_remaining = redis_client.ttl(current_key)
            log.info(
                "signal_watchdog.override_active_no_action",
                style=style, ttl_remaining_s=ttl_remaining,
                current_threshold=float(current_adj), win_rate_14d=win_rate_14d,
                n_outcomes_14d=len(outcomes_14d),
            )

        status.append({
            "style": style,
            "win_rate_14d": round(win_rate_14d, 3) if win_rate_14d is not None else None,
            "n_outcomes_14d": len(outcomes_14d),
            "signals_7d": signals_7d,
            "current_watchdog_threshold": float(current_adj) if current_adj else None,
            "tighten_count": tighten_count,
            "action": action,
        })

    return {"actions": actions, "status": status}


@router.get("/tune_status")
def tune_status(
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Read-only snapshot of all self-tuning system state (TIER88).

    Returns per-style: hardcoded defaults, Redis overrides (watchdog/calibrated/
    auto-tuner), effective values (priority: watchdog > calibrated > default),
    14-day rolling win rate, 7-day BUY signal count, and watchdog state.
    No side effects — safe to poll from the frontend.
    """
    from ..generators.signals import _STYLE_PROFILES

    redis_client = _get_redis()
    _7D  = date.today() - timedelta(days=7)

    styles_out: dict = {}
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        p = _STYLE_PROFILES[style]

        # Read all Redis overrides
        watchdog_threshold     = _redis_get_float(f"stockai:watchdog:{style}:threshold")
        calibrated_threshold   = _redis_get_float(f"stockai:signal_thresholds:{style}")
        ml_weight_cap_tuned    = _redis_get_float(f"stockai:style_tune:{style}:ml_weight_cap")
        adx_min_tuned          = _redis_get_float(f"stockai:style_tune:{style}:adx_min")
        breadth_comp_tuned     = _redis_get_float(f"stockai:style_tune:{style}:breadth_compression")
        tighten_count          = int(redis_client.get(f"stockai:watchdog:{style}:tighten_count") or 0)

        # Effective values — priority: watchdog > calibrated > hardcoded
        eff_threshold = watchdog_threshold or calibrated_threshold or p["buy_threshold"]["bull"]
        eff_ml_cap    = ml_weight_cap_tuned if ml_weight_cap_tuned is not None else p["ml_weight_cap"]
        eff_adx_min   = adx_min_tuned       if adx_min_tuned is not None       else p.get("adx_min")
        eff_breadth   = breadth_comp_tuned  if breadth_comp_tuned is not None  else p.get("breadth_compression")

        # T243-TUNE-WINDOW: same style-aware window widening as signal_watchdog() — a bare
        # 14-day signal_date cutoff can never show outcomes for styles whose hold window is
        # >=14 days (LONG's 28-day hold makes "14d win rate" mathematically always empty, not
        # just usually empty). Widen by that style's own hold window so this reports "the last
        # 14 days of signals that have HAD TIME to resolve."
        _hold_days = _OUTCOME_HOLD_DAYS.get(style, 14)
        _win_start = date.today() - timedelta(days=14 + _hold_days)
        outcomes_14d = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.signal_date >= _win_start,
                SignalOutcome.is_correct.is_not(None),
                SignalOutcome.signal_direction == "BUY",
                SignalOutcome.horizon == SignalHorizon[style],
            )
        ).scalars().all()

        win_rate_14d: float | None = None
        if outcomes_14d:
            wins = sum(1 for o in outcomes_14d if o.is_correct)
            win_rate_14d = round(wins / len(outcomes_14d), 3)

        # 7-day BUY signal count
        signals_7d = session.execute(
            select(func.count(Signal.id)).where(
                Signal.ts >= _7D,
                Signal.signal == SignalType.BUY,
                Signal.horizon == SignalHorizon[style],
            )
        ).scalar() or 0

        # Watchdog status label
        if watchdog_threshold is not None:
            watchdog_status = "max_tighten_review" if tighten_count >= 3 else f"tightened_{tighten_count}x"
        else:
            watchdog_status = "nominal"

        styles_out[style] = {
            "defaults": {
                "buy_threshold_bull": p["buy_threshold"]["bull"],
                "ml_weight_cap": p["ml_weight_cap"],
                "adx_min": p.get("adx_min"),
                "breadth_compression": p.get("breadth_compression"),
            },
            "redis_overrides": {
                "watchdog_threshold": watchdog_threshold,
                "calibrated_threshold": calibrated_threshold,
                "ml_weight_cap": ml_weight_cap_tuned,
                "adx_min": adx_min_tuned,
                "breadth_compression": breadth_comp_tuned,
            },
            "effective": {
                "buy_threshold_bull": round(eff_threshold, 4),
                "ml_weight_cap": round(eff_ml_cap, 4),
                "adx_min": round(eff_adx_min, 1) if eff_adx_min is not None else None,
                "breadth_compression": round(eff_breadth, 3) if eff_breadth is not None else None,
            },
            "performance": {
                "win_rate_14d": win_rate_14d,
                "n_outcomes_14d": len(outcomes_14d),
                "signals_7d": signals_7d,
            },
            "watchdog": {
                "status": watchdog_status,
                "tighten_count": tighten_count,
                "current_threshold": watchdog_threshold,
            },
            # AUD263-TUNED-PARAMS-SILENTLY-REVERT-ON-TTL: each of these 4 per-style tuned
            # values sits behind a 30-day-TTL Redis key — on expiry the read side above
            # silently falls back to the hardcoded default, indistinguishable from "never
            # tuned" without this. reverted=True means this parameter WAS validated/promoted
            # at some point but its value has since expired back to the hardcoded default.
            "staleness": {
                "calibrated_threshold": _tuning_staleness(f"stockai:signal_thresholds:{style}"),
                "ml_weight_cap": _tuning_staleness(f"stockai:style_tune:{style}:ml_weight_cap"),
                "adx_min": _tuning_staleness(f"stockai:style_tune:{style}:adx_min"),
                "breadth_compression": _tuning_staleness(f"stockai:style_tune:{style}:breadth_compression"),
            },
        }

    return {
        "as_of": date.today().isoformat(),
        "styles": styles_out,
        # Global (not per-style) tuned mechanisms — same silent-reversion risk, 90-day TTL.
        "global_staleness": {
            "ta_weights": _tuning_staleness("stockai:ta_weights"),
            "conviction_weights": _tuning_staleness("stockai:conviction_weights"),
        },
    }


@router.get("/confidence-calibration")
def confidence_calibration_map(
    refresh: bool = Query(False, description="Force rebuild from DB, bypassing Redis cache"),
    session: Session = Depends(get_session),
):
    """Return actual win rate by (horizon, direction, market, confidence band) from the
    last 180 days of signal_outcomes.

    T223/T232-OC5: Makes signal confidence auditable, keyed narrowly enough that the
    comparison is meaningful. Confidence is direction-agnostic, and BUY/SELL, different
    horizons, and US/HK have documented divergent base rates — pooling them into a single
    band-only win rate mixed populations that shouldn't be compared. Keys are
    "HORIZON|DIRECTION|MARKET|BAND" (market-specific, preferred) or "HORIZON|DIRECTION|BAND"
    (pooled across markets, used when the market-specific bucket doesn't reach the
    min-count of 30). Use this to compare confidence bands within the same
    horizon+direction(+market) and tune entry filters accordingly — comparing across
    different horizons/directions/markets is exactly the mistake this keying prevents.

    T232-OC5: this route MUST be registered before /{symbol} below — FastAPI matches
    routes in registration order, and /{symbol} would otherwise swallow this path,
    treating "confidence-calibration" as a stock symbol (this bug existed from when the
    route was first added and made the endpoint completely unreachable).
    """
    if refresh:
        try:
            _get_redis().delete(_CONF_CAL_CACHE_KEY)
        except Exception:
            pass
    cal = _get_confidence_calibration(session)
    if not cal:
        return {"message": f"Insufficient signal_outcomes data (need >={_CONF_CAL_MIN_COUNT} evaluated outcomes per bucket)", "buckets": {}}
    # AUD232-003: surface how fresh the underlying signal_outcomes data actually is —
    # the Redis cache rebuilding hourly (its TTL) is indistinguishable from evaluate_signal_
    # outcomes having silently stopped running (e.g. the jose-missing-library pattern already
    # seen repeatedly in this repo) unless something reports the real data's own age. This is
    # a cheap MAX(signal_date) query, not part of the cached bucket computation itself.
    latest_outcome_date = session.execute(
        select(func.max(SignalOutcome.signal_date))
    ).scalar()
    return {
        "buckets": cal,
        "note": "win_rate = fraction of signals in this (horizon, direction[, market]) confidence band that were correct within the hold window",
        "min_count": _CONF_CAL_MIN_COUNT,
        "lookback_days": 180,
        "latest_signal_outcome_date": latest_outcome_date.isoformat() if latest_outcome_date else None,
    }

