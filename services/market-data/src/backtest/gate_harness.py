"""T233-SELFIMPROVE-PHASE2 (Phase 2a): Backtest Harness for _should_enter()'s gate thresholds.

See docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md for the full design and scoping rationale.

Scope (deliberately narrow — see the design doc §1c/§1d/§2a for why):
  - Replays the REAL, unmodified _should_enter() against historical BUY signals, with a
    candidate config substituted in for min_entry_score / min_confidence / min_rr_ratio /
    max_entry_gap_pct — the thresholds that function actually reads.
  - Uses each signal's own SignalOutcome forward return as realized P&L ground truth, NOT a
    synthetic exit-price simulation (_monitor_positions is out of scope for this phase).
  - Does NOT test min_kscore / min_ta_score / min_volume_z (those live in _scan_for_entries's
    candidate loop, not in _should_enter()) or sizing multipliers or decision-engine's scoring
    path — see Phase 2b/2c in the design doc.

This module lives in market-data (not shared/) because it imports directly from
paper_trading_engine.py — placing it under shared/ would be the first shared->service dependency
in the codebase (checked: no precedent exists).

Trust-and-verify review (2026-08-05, full signal-testing-framework audit): every replayed
_should_enter() call fed a systematically INCOMPLETE view of what a real, LIVE call receives,
compressing the replayed score distribution toward zero relative to live scoring. Two of the
gaps are now closed; one remains, disclosed rather than silently left as an unstated gap:

  - confidence_delta (SA-26) — FIXED. Reconstructed point-in-time-safely via
    _historical_confidence_delta() (queries the most recent PRIOR Signal row strictly before
    the replayed signal's own date — safe because Signal has a real per-calendar-day row
    history, confirmed directly against production; see that function's own docstring).

  - live_regime — STILL None on every replay call, and this remains an HONEST, PERMANENT gap,
    not an oversight left unfixed: the canonical regime classifier (_fetch_market_regime() /
    _fetch_hk_market_regime(), bull/neutral/choppy/risk_off/bear) has NO historical persistence
    anywhere in this codebase — it is Redis-cached, live-only, with no time-series table to
    reconstruct "what was the regime on date X" from. sig.reasons["market_regime"] LOOKS like a
    tempting substitute but is NOT the same classifier — it's signal-engine's own separate,
    independently-computed regime value (a different vocabulary: bull/high_vol/bear/unknown —
    see this repo's own Deep Audit #4 finding on this exact divergence). Silently reusing it
    would feed a wrong-vocabulary value into _should_enter()'s regime-score and pre-regime
    logic, a worse bug than the gap it would "fix". A promotion decision made by this harness
    should be understood as tuned against a regime-blind replay — this is a real, standing
    limitation of Phase 2a/2b, not something a future session should assume was silently
    patched over without a real historical-regime data source first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Market, Price, Ranking, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame

from ..services.paper_trading_engine import (
    _build_game_plan_for_style,
    _ewm_atr_from_ohlc,
    _should_enter,
)

# T234-SIG-INSAMPLE-GATE-TUNING / T232-OC3: both fixes established the same minimum sample
# floor pattern for a chronological train/validation split — kept consistent here.
MIN_SAMPLES_PER_SPLIT = 15

# SignalOutcome's multi-window forward-return buckets — reuse the existing calendar-day
# approximation of each style's trading horizon already established in paper_trading_engine.py
# (AUD19-DB3: 7 calendar days ≈ 5 trading days, 14 ≈ 10, 15+ ≈ 11-20+).
_HORIZON_BUCKET = {
    "SHORT": "5d",
    "SWING": "10d",
    "LONG": "20d",
    "GROWTH": "10d",
}

# BUG233-BACKTESTHARNESS-EMPTYVALIDATION (2026-07-31): the calendar-day count that must have
# elapsed since a signal's own signal_date before that style's _HORIZON_BUCKET column can even
# be non-NULL (AUD19-DB3's own days_held<=7/<=14/else cutoffs in paper_trading_engine.py,
# mirrored from signal-engine's _OUTCOME_HOLD_DAYS — duplicated here rather than a cross-service
# import, matching this module's own stated reason for living in market-data at all: no
# shared/->service precedent exists). Every walk-forward split below MUST pull window_end back
# by this many days before splitting train/validation, or the newest ~30% of the window (the
# validation slice) is guaranteed to contain zero resolved outcomes — a signal from yesterday
# cannot possibly have a populated LONG bucket yet. Live-verified: at the default 60-day window,
# the unadjusted split left SWING/LONG/GROWTH's validation slice with n_signals_seen=0 every
# time, silently defeating the ENTIRE held-out-validation defense against train-slice overfitting
# for 3 of 4 styles — not a rare edge case, the default configuration for most of this harness's
# life.
_HORIZON_RESOLUTION_LAG_DAYS = {
    "SHORT": 7,
    "SWING": 14,
    "LONG": 20,
    "GROWTH": 14,
}


@dataclass
class BacktestResult:
    style: str
    market: str
    cfg_label: str                # human-readable description of what was varied, e.g. "min_entry_score=5"
    window_start: date
    window_end: date
    n_signals_seen: int           # total BUY signals with a resolved outcome in the window
    n_entered: int                # how many _should_enter() said yes to
    win_rate: float | None = None
    avg_return_pct: float | None = None   # == expected value; see T232-OC4 — do not multiply by win_rate
    skipped_reason: str | None = None     # set instead of the above when n_entered < MIN_SAMPLES_PER_SPLIT
    entered_signal_ids: list[int] = field(default_factory=list)
    # T233-SELFIMPROVE-PHASE3: per-trade pct returns for the entered signals, in the same order
    # as entered_signal_ids. Exposed so promotion_gate.py can compute an approximate worst-trade
    # check without a second replay — NOT a portfolio equity curve, see promotion_gate.py's
    # module docstring for why a faithful drawdown check needs Phase 2b instead.
    returns: list[float] = field(default_factory=list)


def _entry_as_of(entry_date: date, market: str) -> datetime:
    """UTC-aware `as_of` for _should_enter()'s replay-mode market-hours/time-of-day/macro-
    blackout checks — a fixed midday-local-market-time on `entry_date`, comfortably clear of
    both the market-hours boundary and the time-of-day gate's open/close edge windows.

    CORRECTION during Phase 2b's own live-verification: an earlier version of this function
    used Signal.ts (the moment the signal was actually GENERATED) directly. Live-checking
    against real production data found this doesn't work — signals are frequently generated
    by the post-close refresh burst (scheduler.py's us_post_close job, ~16:30 ET), so
    Signal.ts is routinely stamped AFTER the market-hours gate's own 16:00 cutoff (confirmed:
    45/45 signals in a real SWING/US window had an out-of-hours ts). This is not a sig.ts
    data-quality problem — it is exactly the same T+1 entry-timing model this file's own
    outcome.entry_price already relies on (SignalOutcome.entry_date is deliberately the day
    AFTER signal_date, precisely to avoid same-day-close lookahead bias — see this repo's own
    SE-F2 fix history). A live trader acting on a signal generated after today's close enters
    on the NEXT trading day — entry_date IS that day. Midday (not exactly market open/close)
    keeps the constructed instant comfortably inside the time-of-day gate's own safe window
    without needing to reason about exact open/close boundaries.
    """
    tz = ZoneInfo("Asia/Hong_Kong") if market == "HK" else ZoneInfo("America/New_York")
    local_midday = datetime(entry_date.year, entry_date.month, entry_date.day, 12, 0, tzinfo=tz)
    return local_midday.astimezone(timezone.utc)


def _historical_atr(session: Session, stock_id: int, as_of: date, period: int = 14) -> float | None:
    """Compute ATR(period) from Price rows strictly BEFORE `as_of` — no look-ahead.

    Mirrors _ewm_atr_from_ohlc's math exactly, but sources historical OHLC from the DB
    instead of _batch_compute_atr's live yfinance call (not usable for a historical replay).
    """
    rows = session.execute(
        select(Price.high, Price.low, Price.close)
        .where(
            Price.stock_id == stock_id,
            Price.timeframe == TimeFrame.D1,
            Price.ts < as_of,
        )
        .order_by(Price.ts.desc())
        .limit(period + 5)
    ).all()
    if len(rows) < period + 1:
        return None
    rows = list(reversed(rows))  # back to chronological order for the EWM calc
    high  = pd.Series([float(r.high)  for r in rows])
    low   = pd.Series([float(r.low)   for r in rows])
    close = pd.Series([float(r.close) for r in rows])
    return _ewm_atr_from_ohlc(high, low, close, period)


def _historical_confidence_delta(
    session: Session, stock_id: int, horizon: str, signal_date: date, current_confidence: float | None,
) -> float | None:
    """Point-in-time-correct reconstruction of SA-26's confidence_delta for a replay.

    _scan_for_entries()'s own live computation (paper_trading_engine.py ~line 5197) finds the
    most recent PRIOR Signal row (Signal.ts < sig.ts, same stock+horizon), then computes
    `round(sig.confidence - prior_conf, 1)`. That query is safe to replay historically ONLY
    because Signal has a real per-calendar-day row history — confirmed directly against
    production: `SELECT stock_id, horizon, COUNT(DISTINCT DATE(ts)), COUNT(*) FROM signals
    GROUP BY stock_id, horizon` shows rows == distinct_days for every (stock, horizon) pair,
    matching the table's own uq_signals_stock_horizon_day unique index — Signal.reasons gets
    overwritten intraday, but the ROW itself (and its final ts/confidence for that day)
    persists as one distinct row per calendar day, so "the prior day's confidence" is a real,
    queryable fact, not a value only ever visible live. `ts < signal_date` (not `<=`) matches
    the live query's own strict-less-than semantics — the CURRENT day's own row must never be
    its own "prior".
    """
    if current_confidence is None:
        return None
    prior_conf = session.execute(
        select(Signal.confidence)
        .where(
            Signal.stock_id == stock_id,
            Signal.horizon == SignalHorizon(horizon),
            Signal.ts < signal_date,
        )
        .order_by(Signal.ts.desc())
        .limit(1)
    ).scalar()
    if prior_conf is None:
        return None
    return round(float(current_confidence) - float(prior_conf), 1)


def _fetch_matched_signals(
    session: Session, style: str, market: str, window_start: date, window_end: date,
) -> list[tuple[Signal, SignalOutcome, Stock]]:
    """BUY signals in [window_start, window_end] for (style, market) that have a resolved
    outcome for that style's hold-horizon bucket — the set this harness can score.
    """
    bucket = _HORIZON_BUCKET[style]
    is_correct_col = getattr(SignalOutcome, f"is_correct_{bucket}")
    return_col = getattr(SignalOutcome, f"return_{bucket}")
    rows = session.execute(
        select(Signal, SignalOutcome, Stock)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .join(Stock, Stock.id == Signal.stock_id)
        .where(
            Signal.horizon == SignalHorizon(style),
            Signal.signal == SignalType.BUY,
            Stock.market == Market(market),
            SignalOutcome.signal_date >= window_start,
            SignalOutcome.signal_date <= window_end,
            is_correct_col.is_not(None),
            return_col.is_not(None),
        )
        .order_by(SignalOutcome.signal_date)
    ).all()
    return list(rows)


def replay_should_enter(
    session: Session,
    style: str,
    market: str,
    cfg: dict,
    window_start: date,
    window_end: date,
    cfg_label: str = "",
) -> BacktestResult:
    """Replay the real _should_enter() over historical BUY signals in the window.

    `cfg` is passed straight through to _should_enter() unmodified — same dict shape
    paper_trading_engine.py already builds (see design doc §1b: no refactor needed, gate
    thresholds are already read from an injectable cfg dict).
    """
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    matched = _fetch_matched_signals(session, style, market, window_start, window_end)

    result = BacktestResult(
        style=style, market=market, cfg_label=cfg_label or "(baseline)",
        window_start=window_start, window_end=window_end,
        n_signals_seen=len(matched), n_entered=0,
    )
    if len(matched) < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {len(matched)} resolved BUY signals in window (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    returns: list[float] = []
    wins = 0
    for sig, outcome, stock in matched:
        live_price = outcome.entry_price
        if not live_price or live_price <= 0:
            continue
        atr = _historical_atr(session, stock.id, outcome.signal_date)
        game_plan = _build_game_plan_for_style(stock.symbol, style, live_price, sig.reasons or {}, atr)
        # T232-DL-GATEHARNESS-INPUTGAP: confidence_delta (SA-26) is now reconstructed the same
        # point-in-time-safe way _scan_for_entries() computes it live — see
        # _historical_confidence_delta()'s own docstring for why this is safe to replay. This
        # closes one real, previously-undisclosed input gap between what this harness replays
        # and what a live call to _should_enter() actually receives. live_regime is NOT
        # threaded in — see this function's own docstring for why that gap remains open.
        confidence_delta = _historical_confidence_delta(
            session, stock.id, style, outcome.signal_date, sig.confidence,
        )
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons or {},
            "confidence_delta": confidence_delta,
        }
        should, _score, _notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime=None, kscore=None,
            as_of=_entry_as_of(outcome.entry_date or outcome.signal_date, market),
        )
        if not should:
            continue
        pct_return = getattr(outcome, f"return_{bucket}")
        is_correct = getattr(outcome, f"is_correct_{bucket}")
        returns.append(float(pct_return))
        if is_correct:
            wins += 1
        result.entered_signal_ids.append(sig.id)

    result.n_entered = len(returns)
    result.returns = returns
    if result.n_entered < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {result.n_entered} signals passed the gate (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    result.win_rate = round(wins / result.n_entered, 4)
    # T232-OC4 convention: avg_return_pct across ALL entered trades (wins and losses) already
    # IS the expected value — do not multiply by win_rate again, that double-counts win
    # probability (the exact bug already fixed in outcomes_calibrate_apply / tune_style_profiles).
    result.avg_return_pct = round(sum(returns) / len(returns) * 100, 4)
    return result


# ── Phase 2b: min_kscore / min_ta_score / min_volume_z ──────────────────────────────────────
# See docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md §1c/§4 for why these were deferred out
# of Phase 2a — they live in _scan_for_entries' own candidate loop, not inside _should_enter().
#
# RE-SCOPED FINDING (2026-07-22): the design doc's own concern was that _scan_for_entries as a
# WHOLE is heavily stateful (open positions, equity, daily/weekly loss caps, cooldowns, all
# evolving day-over-day) and would need a full bar-by-bar equity-curve replay to test anything
# inside it. Re-reading the actual gate code for these THREE SPECIFIC checks found that framing
# too pessimistic for them specifically: min_kscore (Ranking.score vs. a threshold),
# min_ta_score (sig.reasons["ta_score"] vs. a threshold), and min_volume_z (sig.reasons
# ["volume_z"] vs. a threshold) are each a pure, stateless comparison against data already
# stored per-signal/per-stock — none of them read open positions, equity, or any other
# evolving portfolio state. They only happen to live in the wrong function. This means they
# CAN be layered onto the existing per-signal replay_should_enter() without building the full
# equity-curve engine — a materially smaller, lower-risk extension than the design doc
# anticipated. The genuinely-stateful gates (drawdown, daily/weekly loss, cooldowns, entry
# caps, sector/cluster caps) remain out of scope and still need Phase 2b's originally-envisioned
# full replay if ever tackled — not attempted here.
#
# Point-in-time correctness: _scan_for_entries' own LIVE min_kscore check always joins the
# MOST RECENT Ranking row (func.max(Ranking.as_of), no date bound) — correct for live trading,
# where "most recent" always means "now". A historical replay must NOT reuse that shortcut, or
# it would silently look up a K-Score computed AFTER the signal date, leaking future data into
# a past decision. _historical_kscore() below instead finds the most recent Ranking row with
# as_of <= the signal's own date — the point-in-time-correct analogue.

def _historical_kscore(session: Session, stock_id: int, as_of: date) -> float | None:
    """Most recent Ranking.score with as_of <= the signal's date — NOT the live engine's own
    func.max(Ranking.as_of) shortcut (which has no date bound and would leak future K-Score
    data into a past decision during a replay)."""
    row = session.execute(
        select(Ranking.score)
        .where(Ranking.stock_id == stock_id, Ranking.as_of <= as_of)
        .order_by(Ranking.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(row) if row is not None else None


def _passes_prefilter_gates(cfg: dict, kscore: float | None, reasons: dict) -> str | None:
    """Applies min_kscore / min_ta_score / min_volume_z exactly as _scan_for_entries' own
    candidate loop does (paper_trading_engine.py ~line 4126-4235), reading only data already
    stored per-signal/per-stock (no open-position/equity/portfolio state). Returns a skip
    reason string if any gate blocks, or None if the candidate clears all three.

    Mirrors each gate's own fail-open convention exactly:
    - min_kscore: cfg["require_kscore"] (default True) rejects a stock with no Ranking row at
      all; a present-but-low score is rejected via cfg["min_kscore"] — this harness always
      requires a real kscore value to have been resolved (mirrors require_kscore=True, the
      live default) since a replay has no live "unranked stocks are allowed through" concept.
    - min_ta_score: only enforced when cfg.get("min_ta_score", 0.0) > 0 (0.0 = gate disabled,
      matching the live gate's own no-op state); a MISSING ta_score in reasons defaults to 1.0
      (never blocks), matching the live gate's fail-open default exactly.
    - min_volume_z: a MISSING volume_z is fail-open (skips the gate entirely, per T232-DL5) —
      only an explicitly-present, too-low volume_z blocks.
    """
    if kscore is None:
        if cfg.get("require_kscore", True):
            return "no_ranking"
    elif kscore < cfg.get("min_kscore", 0.0):
        return "kscore_below_min"

    min_ta = float(cfg.get("min_ta_score", 0.0))
    if min_ta > 0:
        ta_raw = reasons.get("ta_score")
        ta = float(ta_raw) if ta_raw is not None else 1.0
        if ta < min_ta:
            return "ta_score_below_min"

    vol_z_raw = reasons.get("volume_z")
    if vol_z_raw is not None:
        vol_z = float(vol_z_raw)
        min_vol_z = float(cfg.get("min_volume_z", -1.5))
        if vol_z < min_vol_z:
            return "volume_z_below_min"

    return None


def replay_extended_gates(
    session: Session,
    style: str,
    market: str,
    cfg: dict,
    window_start: date,
    window_end: date,
    cfg_label: str = "",
) -> BacktestResult:
    """Same replay as replay_should_enter(), but ALSO applies the min_kscore/min_ta_score/
    min_volume_z pre-filters before calling _should_enter() — the three gates Phase 2a
    deliberately left untested (see module docstring above). A candidate must clear all four
    gates (the three pre-filters plus _should_enter() itself) to count as entered.
    """
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    matched = _fetch_matched_signals(session, style, market, window_start, window_end)

    result = BacktestResult(
        style=style, market=market, cfg_label=cfg_label or "(baseline, extended gates)",
        window_start=window_start, window_end=window_end,
        n_signals_seen=len(matched), n_entered=0,
    )
    if len(matched) < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {len(matched)} resolved BUY signals in window (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    returns: list[float] = []
    wins = 0
    for sig, outcome, stock in matched:
        live_price = outcome.entry_price
        if not live_price or live_price <= 0:
            continue

        kscore = _historical_kscore(session, stock.id, outcome.signal_date)
        reasons = sig.reasons or {}
        if _passes_prefilter_gates(cfg, kscore, reasons) is not None:
            continue

        atr = _historical_atr(session, stock.id, outcome.signal_date)
        game_plan = _build_game_plan_for_style(stock.symbol, style, live_price, reasons, atr)
        # T232-DL-GATEHARNESS-INPUTGAP: same point-in-time confidence_delta reconstruction as
        # replay_should_enter() above — see _historical_confidence_delta()'s own docstring.
        confidence_delta = _historical_confidence_delta(
            session, stock.id, style, outcome.signal_date, sig.confidence,
        )
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": reasons,
            "confidence_delta": confidence_delta,
        }
        should, _score, _notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime=None, kscore=kscore,
            as_of=_entry_as_of(outcome.entry_date or outcome.signal_date, market),
        )
        if not should:
            continue
        pct_return = getattr(outcome, f"return_{bucket}")
        is_correct = getattr(outcome, f"is_correct_{bucket}")
        returns.append(float(pct_return))
        if is_correct:
            wins += 1
        result.entered_signal_ids.append(sig.id)

    result.n_entered = len(returns)
    result.returns = returns
    if result.n_entered < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {result.n_entered} signals passed the gate (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    result.win_rate = round(wins / result.n_entered, 4)
    result.avg_return_pct = round(sum(returns) / len(returns) * 100, 4)
    return result


def walk_forward_extended_gate(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    param: str,
    candidates: list[float],
) -> dict:
    """Walk-forward search over candidate values of ONE of min_kscore/min_ta_score/
    min_volume_z, using replay_extended_gates() (all three gates active; only `param` varies
    across candidates, the other two stay at base_cfg's values). Same chronological 70/30
    train/validation split and promotion criterion as walk_forward_min_entry_score().
    """
    if param not in ("min_kscore", "min_ta_score", "min_volume_z"):
        return {"style": style, "market": market, "skipped_reason": f"unknown param: {param}"}

    style = style.upper()
    current_value = base_cfg.get(param, 0.0)

    # BUG233-BACKTESTHARNESS-EMPTYVALIDATION: pull the resolvable window back BEFORE splitting,
    # so the validation slice (the newest ~30%) contains signals old enough to have a resolved
    # outcome for this style's horizon bucket. Splitting on the raw, unadjusted window_end
    # produces a validation slice of entirely too-recent signals with zero resolved outcomes.
    resolvable_end = _resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market,
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = replay_extended_gates(
        session, style, market, base_cfg, val_start, resolvable_end,
        cfg_label=f"baseline {param}={current_value} (validation)",
    )

    train_results = []
    for cand in candidates:
        cand_cfg = {**base_cfg, param: cand}
        train_results.append((cand, replay_extended_gates(
            session, style, market, cand_cfg, window_start, train_end,
            cfg_label=f"{param}={cand} (train)",
        )))

    best_cand, best_train = None, None
    for cand, res in train_results:
        if res.skipped_reason is not None or res.avg_return_pct is None:
            continue
        if best_train is None or res.avg_return_pct > best_train.avg_return_pct:
            best_cand, best_train = cand, res

    if best_cand is None:
        return {
            "style": style, "market": market, "param": param,
            "skipped_reason": "no candidate cleared the sample floor on the train slice",
            "baseline_validation": _result_dict(baseline_val),
        }

    best_val = replay_extended_gates(
        session, style, market, {**base_cfg, param: best_cand}, val_start, resolvable_end,
        cfg_label=f"{param}={best_cand} (validation)",
    )

    promoted = _passes_promotion_margin(best_val, baseline_val)

    return {
        "style": style, "market": market, "param": param,
        "current_value": current_value,
        "candidate_value": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": _result_dict(best_train),
        "candidate_validation": _result_dict(best_val),
        "baseline_validation": _result_dict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate beat baseline on the held-out validation slice "
            "by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the validation slice's own return dispersion (BUG233-BACKTESTHARNESS-COINFLIP — a "
            "bare 'any positive difference' comparison was found to be a ~50% false-promotion "
            "coin flip at realistic sample sizes and was replaced with this margin). This is a "
            "Phase 2b research signal, NOT an automatic config change, and does not correct for "
            "the train-slice grid search's own multiple-comparisons exposure. Like Phase 2a, "
            "this can only evaluate TIGHTENING an existing gate (re-filtering signals that "
            "already fired under the CURRENT threshold) — testing a genuinely LOOSER value "
            "would require regenerating signals against historical price data, which this "
            "replay does not do. IMPORTANT SCOPE NOTE: this harness only ever replays "
            "_should_enter() (the DE-outage fallback gate) — decision_engine_mode='primary' is "
            "the live default, so this parameter only actually governs real entries during a "
            "decision-engine outage; tuning it here does NOT tune the live primary trading "
            "path (Phase 2c, still todo). Replayed candidates also see live_regime=None on "
            "every call — the canonical regime classifier has no historical persistence to "
            "reconstruct from — so this promotion decision is regime-blind (see this module's "
            "own docstring for why)."
        ),
    }


def walk_forward_min_entry_score(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    candidates: list[int] | None = None,
) -> dict:
    """Search candidate min_entry_score values on the train slice (older 70%), then only
    report a candidate as beating baseline if it ALSO wins on the validation slice (newer 30%,
    never seen during the search) — same chronological split pattern as outcomes_calibrate_apply
    (T232-OC3) and tune_style_profiles (T234-SIG-INSAMPLE-GATE-TUNING).
    """
    style = style.upper()
    current_score = base_cfg.get("min_entry_score", 4)
    candidates = candidates if candidates is not None else sorted(set([3, 4, 5, 6, current_score]))

    # BUG233-BACKTESTHARNESS-EMPTYVALIDATION: see walk_forward_extended_gate's identical fix
    # above for the full explanation — pull window_end back by this style's own outcome
    # resolution lag BEFORE splitting, or the validation slice is guaranteed empty.
    resolvable_end = _resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market,
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = replay_should_enter(
        session, style, market, base_cfg, val_start, resolvable_end, cfg_label="baseline (validation)",
    )

    train_results = []
    for cand in candidates:
        cand_cfg = {**base_cfg, "min_entry_score": cand}
        train_results.append((cand, replay_should_enter(
            session, style, market, cand_cfg, window_start, train_end,
            cfg_label=f"min_entry_score={cand} (train)",
        )))

    best_cand, best_train = None, None
    for cand, res in train_results:
        if res.skipped_reason is not None or res.avg_return_pct is None:
            continue
        if best_train is None or res.avg_return_pct > best_train.avg_return_pct:
            best_cand, best_train = cand, res

    if best_cand is None:
        return {
            "style": style, "market": market,
            "skipped_reason": "no candidate cleared the sample floor on the train slice",
            "baseline_validation": _result_dict(baseline_val),
        }

    best_val = replay_should_enter(
        session, style, market, {**base_cfg, "min_entry_score": best_cand}, val_start, resolvable_end,
        cfg_label=f"min_entry_score={best_cand} (validation)",
    )

    promoted = _passes_promotion_margin(best_val, baseline_val)

    return {
        "style": style, "market": market,
        "current_min_entry_score": current_score,
        "candidate_min_entry_score": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": _result_dict(best_train),
        "candidate_validation": _result_dict(best_val),
        "baseline_validation": _result_dict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate beat baseline on the held-out validation slice "
            "by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the validation slice's own return dispersion (BUG233-BACKTESTHARNESS-COINFLIP — a "
            "bare 'any positive difference' comparison was found to be a ~50% false-promotion "
            "coin flip at realistic sample sizes and was replaced with this margin). This is a "
            "Phase 2a research signal, NOT an automatic config change, and does not correct for "
            "the train-slice grid search's own multiple-comparisons exposure. No promotion gate "
            "or tune_history table exists yet for this specific endpoint (Phase 3, still todo). "
            "IMPORTANT SCOPE NOTE: this harness only ever replays _should_enter() (the "
            "DE-outage fallback gate) — decision_engine_mode='primary' is the live default, so "
            "min_entry_score only actually governs real entries during a decision-engine "
            "outage; tuning it here does NOT tune the live primary trading path (Phase 2c, "
            "still todo). Replayed candidates also see live_regime=None on every call — the "
            "canonical regime classifier has no historical persistence to reconstruct from — "
            "so this promotion decision is regime-blind (see this module's own docstring for "
            "why)."
        ),
    }


# ── AUD298-BLOCKED-ENTRY-SCORES-VALIDATE-FIRST ──────────────────────────────────────────────
# PAPER_TRADING_DEEP_AUDIT_2025-08-22.md observed that min_entry_score is a pure `score >=
# threshold` comparison — structurally unable to express "exclude 5 and 6 specifically, but
# allow 7+" the way its own per-score win-rate table superficially suggests is needed (score 4
# best, 5/6 disasters, 7-9 recovering). walk_forward_min_entry_score() above only ever searches
# THRESHOLD candidates, never a discrete exclusion set — this is the sibling sweep that can.
#
# Reviewed against the SAME dataset before building anything: entry_score shows almost NO
# winner/loser differentiation on average (winners avg 5.0, losers avg 5.1) — a real signal
# that the "5/6 disaster" pattern in the doc's own table could be driven by a handful of
# outsized losing trades rather than a genuine per-score effect, at n=18-29 per bucket. This
# sweep exists specifically to let real, held-out validation data settle that question rather
# than trusting the doc's own reflexive hardcode.
#
# Design note on why this can't just reuse replay_should_enter() unmodified: _should_enter()'s
# min_entry_score comparison is INTERNAL (score >= cfg["min_entry_score"]) — there's no cfg
# key to inject an exclusion SET through. The trick: call _should_enter() with a floor cfg key
# equal to the CURRENT live threshold (so a genuine below-floor signal is still correctly
# rejected exactly as it is live), then additionally reject via score IF the returned score
# falls in the exclusion set. This composes correctly with PT-3's calibrated-logistic-
# regression branch too (which bypasses the additive score entirely once >=100 closed trades
# exist — none of today's real portfolios have reached that yet) — should is already False in
# hard-reject/calibrated-no cases regardless of what the exclusion check does, so this can only
# ever REJECT trades the plain-threshold baseline would have entered, never admit extra ones.

def replay_should_enter_excluding_scores(
    session: Session,
    style: str,
    market: str,
    cfg: dict,
    excluded_scores: frozenset[int],
    window_start: date,
    window_end: date,
    cfg_label: str = "",
) -> BacktestResult:
    """Sibling to replay_should_enter() — identical per-signal replay, except a candidate
    whose returned score falls in `excluded_scores` is rejected even when the plain
    `score >= cfg["min_entry_score"]` comparison alone would have admitted it. `cfg` should
    already carry the real, current min_entry_score floor — excluded_scores narrows what that
    floor already admits, it never widens it."""
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    matched = _fetch_matched_signals(session, style, market, window_start, window_end)

    result = BacktestResult(
        style=style, market=market, cfg_label=cfg_label or "(baseline)",
        window_start=window_start, window_end=window_end,
        n_signals_seen=len(matched), n_entered=0,
    )
    if len(matched) < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {len(matched)} resolved BUY signals in window (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    returns: list[float] = []
    wins = 0
    for sig, outcome, stock in matched:
        live_price = outcome.entry_price
        if not live_price or live_price <= 0:
            continue
        atr = _historical_atr(session, stock.id, outcome.signal_date)
        game_plan = _build_game_plan_for_style(stock.symbol, style, live_price, sig.reasons or {}, atr)
        confidence_delta = _historical_confidence_delta(
            session, stock.id, style, outcome.signal_date, sig.confidence,
        )
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons or {},
            "confidence_delta": confidence_delta,
        }
        should, score, _notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime=None, kscore=None,
            as_of=_entry_as_of(outcome.entry_date or outcome.signal_date, market),
        )
        if not should or score in excluded_scores:
            continue
        pct_return = getattr(outcome, f"return_{bucket}")
        is_correct = getattr(outcome, f"is_correct_{bucket}")
        returns.append(float(pct_return))
        if is_correct:
            wins += 1
        result.entered_signal_ids.append(sig.id)

    result.n_entered = len(returns)
    result.returns = returns
    if result.n_entered < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {result.n_entered} signals passed the gate (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result

    result.win_rate = round(wins / result.n_entered, 4)
    result.avg_return_pct = round(sum(returns) / len(returns) * 100, 4)
    return result


def walk_forward_blocked_entry_scores(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
    candidate_exclusion_sets: list[frozenset[int]] | None = None,
) -> dict:
    """Search candidate entry-score EXCLUSION sets on the train slice (older 70%), then only
    report a candidate as beating baseline if it ALSO wins on the validation slice (newer 30%,
    never seen during the search) — same chronological split pattern and promotion-margin gate
    as walk_forward_min_entry_score() above.

    The baseline is the CURRENT live min_entry_score threshold with NO exclusions — i.e. "does
    excluding any of these score sets beat doing nothing beyond today's plain threshold."
    Default candidate sets are single- and paired-score exclusions drawn from the doc's own
    observed pattern (5, 6, and {5,6} together) plus an empty-set sanity baseline — deliberately
    NOT an exhaustive powerset search (a 9-way score range would produce hundreds of candidate
    sets at this sample size, pure overfitting bait; the doc's own specific claim is what this
    sweep is built to check, not a blind search for whatever exclusion looks best in-sample).
    """
    style = style.upper()
    current_min_score = base_cfg.get("min_entry_score", 4)
    if candidate_exclusion_sets is None:
        candidate_exclusion_sets = [
            frozenset(), frozenset({5}), frozenset({6}), frozenset({5, 6}),
        ]

    resolvable_end = _resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market,
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = replay_should_enter_excluding_scores(
        session, style, market, base_cfg, frozenset(), val_start, resolvable_end,
        cfg_label="baseline, no exclusions (validation)",
    )

    train_results = []
    for excl in candidate_exclusion_sets:
        label = "no exclusions" if not excl else f"exclude {sorted(excl)}"
        train_results.append((excl, replay_should_enter_excluding_scores(
            session, style, market, base_cfg, excl, window_start, train_end,
            cfg_label=f"{label} (train)",
        )))

    best_excl, best_train = None, None
    for excl, res in train_results:
        if res.skipped_reason is not None or res.avg_return_pct is None:
            continue
        if best_train is None or res.avg_return_pct > best_train.avg_return_pct:
            best_excl, best_train = excl, res

    if best_excl is None:
        return {
            "style": style, "market": market,
            "current_min_entry_score": current_min_score,
            "skipped_reason": "no exclusion-set candidate cleared the sample floor on the train slice",
            "baseline_validation": _result_dict(baseline_val),
        }

    best_val = replay_should_enter_excluding_scores(
        session, style, market, base_cfg, best_excl, val_start, resolvable_end,
        cfg_label=f"exclude {sorted(best_excl)} (validation)" if best_excl else "no exclusions (validation)",
    )
    promoted = _passes_promotion_margin(best_val, baseline_val)

    return {
        "style": style, "market": market,
        "current_min_entry_score": current_min_score,
        "candidate_exclusion_set": sorted(best_excl),
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": _result_dict(best_train),
        "candidate_validation": _result_dict(best_val),
        "baseline_validation": _result_dict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means excluding this specific score set beat the plain-threshold "
            "baseline on the held-out validation slice by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the validation slice's own return dispersion (same BUG233-BACKTESTHARNESS-COINFLIP "
            "margin every other walk-forward endpoint in this module enforces). A promoted "
            "empty-set candidate simply means no exclusion beat doing nothing, not that scores "
            "5/6 are fine to keep exactly as-is if a NON-empty set also promoted with a larger "
            "lift — always compare against every candidate's own train-slice avg_return_pct, "
            "not just whichever one happened to win the search. This is a Phase 2a-style "
            "research signal, NOT an automatic config change, and does not correct for the "
            "train-slice search's own multiple-comparisons exposure across the (small, "
            "deliberately non-exhaustive) candidate list. Same standing scope note as "
            "walk_forward_min_entry_score(): this only replays _should_enter() (the DE-outage "
            "fallback gate), is regime-blind (live_regime=None on every call), and composes "
            "correctly with PT-3's calibrated-logistic-regression branch once >=100 closed "
            "trades exist for a portfolio (a hard-reject or calibrated-no stays excluded "
            "regardless of the score-exclusion check)."
        ),
    }


def walk_forward_calibration_feedback(
    session: Session,
    style: str,
    market: str,
    base_cfg: dict,
    window_start: date,
    window_end: date,
) -> dict:
    """AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK: validates whether _should_enter()'s new
    calibration-feedback score layer (reads Signal.reasons["calibrated_win_rate"], gated behind
    cfg["calibration_feedback_enabled"]) actually improves outcomes before it is ever turned on
    for real trading.

    Unlike walk_forward_min_entry_score()/walk_forward_extended_gate(), this is not a search
    over a continuous parameter — the score layer's own thresholds (>=0.55 boosts, <=0.35
    penalizes) are fixed constants, matching every other score layer in _should_enter(). The
    only real question is binary: does turning the layer ON beat the current OFF baseline on
    data the comparison never saw. So "train slice" here just confirms turning it on is a
    genuine train-slice improvement (not a coin flip already resolvable at zero cost) before
    spending the validation slice on it — the validation-slice comparison against baseline is
    the one that actually decides promotion, exactly as in the other two walk-forward
    functions.
    """
    style = style.upper()
    resolvable_end = _resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market,
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    off_cfg = {**base_cfg, "calibration_feedback_enabled": False}
    on_cfg = {**base_cfg, "calibration_feedback_enabled": True}

    train_off = replay_should_enter(
        session, style, market, off_cfg, window_start, train_end, cfg_label="calibration OFF (train)",
    )
    train_on = replay_should_enter(
        session, style, market, on_cfg, window_start, train_end, cfg_label="calibration ON (train)",
    )
    if (
        train_off.skipped_reason is not None or train_off.avg_return_pct is None
        or train_on.skipped_reason is not None or train_on.avg_return_pct is None
    ):
        return {
            "style": style, "market": market,
            "skipped_reason": "insufficient train-slice samples for either the ON or OFF variant",
            "train_off": _result_dict(train_off),
            "train_on": _result_dict(train_on),
        }

    if train_on.avg_return_pct <= train_off.avg_return_pct:
        return {
            "style": style, "market": market,
            "promoted": False,
            "train_window": [str(window_start), str(train_end)],
            "train_off": _result_dict(train_off),
            "train_on": _result_dict(train_on),
            "note": (
                "calibration feedback did not even beat the OFF baseline on the TRAIN slice — "
                "no reason to spend the validation slice checking it further. Not promoted."
            ),
        }

    baseline_val = replay_should_enter(
        session, style, market, off_cfg, val_start, resolvable_end, cfg_label="calibration OFF (validation)",
    )
    candidate_val = replay_should_enter(
        session, style, market, on_cfg, val_start, resolvable_end, cfg_label="calibration ON (validation)",
    )

    promoted = _passes_promotion_margin(candidate_val, baseline_val)

    return {
        "style": style, "market": market,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_off": _result_dict(train_off),
        "train_on": _result_dict(train_on),
        "baseline_validation": _result_dict(baseline_val),
        "candidate_validation": _result_dict(candidate_val),
        "promoted": promoted,
        "note": (
            "promoted=True means turning ON the calibration-feedback score layer beat the OFF "
            "baseline on the held-out validation slice by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the validation slice's own return dispersion (same BUG233-BACKTESTHARNESS-"
            "COINFLIP margin every other walk-forward function in this module enforces). This "
            "is a research signal for whether cfg['calibration_feedback_enabled'] should ever "
            "be set True for real trading — it is NOT itself a live config change; turning the "
            "feature on for a real portfolio still requires the portfolio's own cfg to set this "
            "flag explicitly, exactly like every other opt-in cfg key in this app. Same scope "
            "caveats as every other function in this module: only ever replays _should_enter() "
            "(the DE-outage fallback gate, not the live primary decision-engine path), and "
            "live_regime=None on every replayed call (no historical regime data source exists)."
        ),
    }


def _resolvable_window_end(window_end: date, style: str) -> date:
    """Pull window_end back by the style's own resolution lag (_HORIZON_RESOLUTION_LAG_DAYS) so
    a subsequent 70/30 split's validation slice actually contains signals old enough to have a
    resolved SignalOutcome for that style's bucket. See BUG233-BACKTESTHARNESS-EMPTYVALIDATION."""
    return window_end - timedelta(days=_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14))


# BUG233-BACKTESTHARNESS-COINFLIP (2026-07-31): a bare `best_val.avg_return_pct >
# baseline_val.avg_return_pct` promotion criterion is a coin flip under the null hypothesis of
# no real edge — simulated directly (best-of-k selection on train, independent validation
# check, both slices drawn from the SAME distribution): ~50% false-promotion rate at every
# sample size from n=15 to n=50, because comparing two noisy sample means with no margin is
# statistically indistinguishable from noise at any n. Real production per-trade return SD
# across all 4 styles is ~9.6-10.6pp (10-day returns) — at n=15 that is a +-5.2pp 95% CI on the
# mean; the harness cannot detect a real edge smaller than its own measurement error, so "any
# positive difference, however small" is not evidence.
#
# Fix: require BOTH (a) a minimum absolute EV-lift margin, AND (b) that the lift is large
# relative to the combined slices' own return dispersion (a crude but real signal-vs-noise
# check — not a formal significance test, since BacktestResult doesn't carry per-trade SDs
# separately per candidate at this call site, but strictly stronger than no margin at all).
# This does not eliminate the multiple-comparisons risk from the train-slice grid search (that
# would need a formal correction across candidates), but it closes the specific, simulated-and-
# confirmed ~50% coin-flip failure mode of the bare `>` comparison.
_MIN_PROMOTION_EV_LIFT_PCT = 0.5   # candidate must beat baseline by at least this many pct points
_MIN_PROMOTION_LIFT_SD_RATIO = 0.5  # ...and by at least this fraction of the validation slice's own return SD


def _passes_promotion_margin(best_val: "BacktestResult", baseline_val: "BacktestResult") -> bool:
    """Stricter replacement for a bare `best_val.avg_return_pct > baseline_val.avg_return_pct`
    check — see BUG233-BACKTESTHARNESS-COINFLIP above for why the bare comparison is a coin
    flip. Requires both slices to be genuinely measurable, a minimum absolute EV-lift margin,
    and the lift to be a meaningful fraction of the validation slice's own return dispersion."""
    if (
        best_val.skipped_reason is not None
        or baseline_val.skipped_reason is not None
        or best_val.avg_return_pct is None
        or baseline_val.avg_return_pct is None
    ):
        return False
    lift = best_val.avg_return_pct - baseline_val.avg_return_pct
    if lift < _MIN_PROMOTION_EV_LIFT_PCT:
        return False
    combined_returns = list(best_val.returns) + list(baseline_val.returns)
    if len(combined_returns) < 2:
        return False
    mean = sum(combined_returns) / len(combined_returns)
    variance = sum((r - mean) ** 2 for r in combined_returns) / (len(combined_returns) - 1)
    sd_pct = (variance ** 0.5) * 100  # returns are stored as fractions; result is in pct points
    if sd_pct <= 0:
        return True  # zero dispersion means the lift (already >= the absolute floor) is real
    return lift >= _MIN_PROMOTION_LIFT_SD_RATIO * sd_pct


def _result_dict(r: BacktestResult) -> dict:
    return {
        "cfg_label": r.cfg_label,
        "window": [str(r.window_start), str(r.window_end)],
        "n_signals_seen": r.n_signals_seen,
        "n_entered": r.n_entered,
        "win_rate": r.win_rate,
        "avg_return_pct": r.avg_return_pct,
        "skipped_reason": r.skipped_reason,
    }
