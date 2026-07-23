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
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons or {},
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
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": reasons,
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

    total_days = (window_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > window_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} days)",
        }

    baseline_val = replay_extended_gates(
        session, style, market, base_cfg, val_start, window_end,
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
        session, style, market, {**base_cfg, param: best_cand}, val_start, window_end,
        cfg_label=f"{param}={best_cand} (validation)",
    )

    promoted = (
        best_val.skipped_reason is None
        and baseline_val.skipped_reason is None
        and best_val.avg_return_pct is not None
        and baseline_val.avg_return_pct is not None
        and best_val.avg_return_pct > baseline_val.avg_return_pct
    )

    return {
        "style": style, "market": market, "param": param,
        "current_value": current_value,
        "candidate_value": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(window_end)],
        "train_result": _result_dict(best_train),
        "candidate_validation": _result_dict(best_val),
        "baseline_validation": _result_dict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate beat baseline on the held-out validation slice "
            "with all three pre-filter gates active — this is a Phase 2b research signal, NOT "
            "an automatic config change. Like Phase 2a, this can only evaluate TIGHTENING an "
            "existing gate (re-filtering signals that already fired under the CURRENT "
            "threshold) — testing a genuinely LOOSER value would require regenerating signals "
            "against historical price data, which this replay does not do."
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

    total_days = (window_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > window_end:
        return {
            "style": style, "market": market,
            "skipped_reason": f"window too short to split ({total_days} days)",
        }

    baseline_val = replay_should_enter(
        session, style, market, base_cfg, val_start, window_end, cfg_label="baseline (validation)",
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
        session, style, market, {**base_cfg, "min_entry_score": best_cand}, val_start, window_end,
        cfg_label=f"min_entry_score={best_cand} (validation)",
    )

    promoted = (
        best_val.skipped_reason is None
        and baseline_val.skipped_reason is None
        and best_val.avg_return_pct is not None
        and baseline_val.avg_return_pct is not None
        and best_val.avg_return_pct > baseline_val.avg_return_pct
    )

    return {
        "style": style, "market": market,
        "current_min_entry_score": current_score,
        "candidate_min_entry_score": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(window_end)],
        "train_result": _result_dict(best_train),
        "candidate_validation": _result_dict(best_val),
        "baseline_validation": _result_dict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate beat baseline on the held-out validation slice — "
            "this is a Phase 2a research signal, NOT an automatic config change. No promotion "
            "gate or tune_history table exists yet (Phase 3, still todo)."
        ),
    }


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
