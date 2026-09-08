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
    blackout checks — a fixed mid-session local time on `entry_date`, clear of both the
    market-hours boundaries and the time-of-day gate's open/close edge windows.

    AUD-BT-HKLUNCHBREAK: this used to say "midday" for BOTH markets and claim the instant was
    "comfortably clear" of the boundaries. That was FALSE for HK — see the inline comment at
    the bottom of this function. 12:00 HKT is the exclusive end of HKEX's morning session, so
    it cleared nothing; it sat in the lunch break. US remains 12:00 ET; HK is now 11:00 HKT.

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
    # AUD-BT-HKLUNCHBREAK: "midday" is NOT safe for HK. HKEX has a split session, and
    # _is_market_hours() defines it as
    #     (09:30 <= t < 12:00)  or  (13:00 <= t < 16:00)
    # so 12:00 HKT falls in NEITHER window — the morning close is exclusive and the afternoon
    # has not opened. Every HK replay therefore landed inside the lunch break and was rejected
    # by the very first hard gate. Verified live: _is_market_hours("HK", as_of=<12:00 HKT>)
    # returned False.
    #
    # This is INDEPENDENT of AUD-BT-HKCFGDEFAULT (cfg["market"] defaulting to "US"): fixing
    # either one alone still yields zero HK entries, which is why both had to be found.
    #
    # 11:00 HKT sits an hour inside the morning session, clear of the 09:30 open and the 12:00
    # close, and remains "comfortably clear of both boundaries" in the sense this docstring
    # promises. US keeps 12:00 ET, which is genuinely mid-session for a 09:30-16:00 market.
    if market == "HK":
        tz = ZoneInfo("Asia/Hong_Kong")
        hour = 11
    else:
        tz = ZoneInfo("America/New_York")
        hour = 12
    local_midday = datetime(entry_date.year, entry_date.month, entry_date.day, hour, 0, tzinfo=tz)
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


# ── Phase 2c: decision-engine's compute_score()/min_score_for_regime() ──────────────────────
# T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A: 7 previously-unvalidated constants gating the
# REAL live decision_engine_mode="primary" entry path (unlike everything above, which only
# ever replays _should_enter(), the DE-OUTAGE fallback) — item #3 (hard_rejects.py's
# max_breakout_extension_pct) and 6 inside scorer.py's compute_score()
# (chase_ceiling_pct/rr_*_threshold/volume_z_*_threshold/ml_bull_prob_*_threshold/
# confidence_delta_threshold/insider_score_*_threshold/congress_score_threshold — see
# scorer.py's own item #3/#8/#9/#10/#11/#12/#14 comments for exactly which literal each one
# replaced). compute_score() itself lives in decision-engine, a SEPARATE service/container —
# rather than duplicate its scoring formula here (the exact anti-pattern this codebase's own
# repeated prior audits have found and fixed elsewhere), this sweep calls decision-engine's own
# real POST /decide/score-replay endpoint, batched (all N resolved signals for one candidate
# cfg in ONE request) to avoid an N x M round-trip cost across ~2,000-2,900 resolved BUY
# outcomes per style.
#
# Deliberately does NOT replay is_pre_choppy/is_pre_risk_off/recent_win_rate/live_regime (same
# permanent gap as replay_should_enter() above) or signal freshness (Layer 3e reads the real
# wall-clock with no as_of injection — never sending "ts" at all correctly skips that layer
# rather than penalizing every row as maximally stale; item #4's own as_of-injection fix is a
# separate, not-yet-built prerequisite, tracked in the T234 triage doc, not silently folded in
# here).

_SCORER_SWEEP_STEP = {
    # (cfg_key, default, step) — one candidate tries default+step, one tries default-step,
    # floored/ceilinged where the underlying quantity has a natural bound (a probability in
    # [0, 1], a percent that can't go negative).
    "chase_ceiling_pct":              (3.0, 1.5, 0.0, None),
    "rr_excellent_threshold":         (3.5, 0.5, 0.0, None),
    "rr_good_threshold":              (2.5, 0.5, 0.0, None),
    "volume_z_strong_threshold":      (1.0, 0.5, None, None),
    "volume_z_weak_threshold":        (-0.5, 0.5, None, None),
    "ml_bull_prob_strong_threshold":  (0.70, 0.05, 0.0, 1.0),
    "ml_bull_prob_weak_threshold":    (0.58, 0.05, 0.0, 1.0),
    "confidence_delta_threshold":     (8.0, 2.0, 0.0, None),
    "insider_score_strong_threshold": (60.0, 10.0, None, None),
    "insider_score_weak_threshold":   (-30.0, 10.0, None, None),
    "congress_score_threshold":       (50.0, 10.0, None, None),
    "max_breakout_extension_pct":     (6.0, 2.0, 0.0, None),
}


def _scorer_sweep_candidates() -> list[dict]:
    """One-parameter-perturbed-at-a-time candidates (matches ranking-engine's own
    _kscore_candidate_weight_sets() "search a tractable neighborhood, not the full 12-dimensional
    space" judgment for the identical reason — a full joint grid across 12 independent
    thresholds is combinatorially intractable at any reasonable step size). Each candidate
    dict varies exactly ONE key from its default; every other key is simply absent, so
    score_replay's own cfg.get(key, <original literal>) fallback applies for the rest — a
    candidate is never a full 12-key dict, only the one delta under test."""
    candidates = []
    for key, (default, step, lo, hi) in _SCORER_SWEEP_STEP.items():
        for sign in (1, -1):
            val = default + sign * step
            if lo is not None:
                val = max(lo, val)
            if hi is not None:
                val = min(hi, val)
            if val == default:
                continue  # a clamp collapsed this candidate onto the baseline — not a real test
            candidates.append({key: round(val, 4)})
    return candidates


def _fetch_score_replay_inputs(
    session: Session, style: str, market: str, window_start: date, window_end: date,
) -> list[dict]:
    """Reconstruct ScoreReplayInput-shaped dicts for every resolved BUY signal in the window —
    reuses the SAME point-in-time-safe machinery replay_should_enter() already relies on
    (_historical_atr, _build_game_plan_for_style, _historical_confidence_delta) rather than a
    second, independently-drifting reconstruction. Returns plain dicts (not pydantic objects —
    this service has no dependency on decision-engine's own models module), one per resolvable
    signal; a signal with no usable entry_price is silently skipped, matching
    replay_should_enter()'s own `if not live_price or live_price <= 0: continue`."""
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    matched = _fetch_matched_signals(session, style, market, window_start, window_end)
    out: list[dict] = []
    for sig, outcome, stock in matched:
        live_price = outcome.entry_price
        if not live_price or live_price <= 0:
            continue
        atr = _historical_atr(session, stock.id, outcome.signal_date)
        game_plan = _build_game_plan_for_style(stock.symbol, style, live_price, sig.reasons or {}, atr)
        confidence_delta = _historical_confidence_delta(
            session, stock.id, style, outcome.signal_date, sig.confidence,
        )
        reasons = dict(sig.reasons or {})
        if confidence_delta is not None:
            reasons["confidence_delta"] = confidence_delta
        out.append({
            "signal_id": sig.id,
            "live_price": float(live_price),
            "game_plan": game_plan,
            "confidence": float(sig.confidence) if sig.confidence is not None else 0.0,
            "bullish_probability": sig.bullish_probability,
            "reasons": reasons,
            "research_rec": None,   # not point-in-time reconstructible here (no historical
            "research_score_val": None,  # research-report table to replay against — same
                                          # honest omission as every other unavailable input.
            "regime_state": "neutral",   # live_regime is a permanent gap — see module docstring.
            "kscore": _historical_kscore(session, stock.id, outcome.signal_date),
            "pct_return": float(getattr(outcome, f"return_{bucket}")),
        })
    return out


def _score_replay_via_http(inputs: list[dict], cfg: dict) -> list[dict] | None:
    """One POST to decision-engine's /decide/score-replay per candidate cfg, batching every
    input for that cfg into a single request (never one call per signal). Returns None (never
    raises) on any network/HTTP failure — the caller must treat that candidate as unmeasurable,
    matching _call_decision_engine()'s own never-raise/return-None-on-DE-unreachable contract."""
    try:
        import httpx
        from common.config import get_settings

        from ..services.paper_trading_engine import _svc_token
        de_url = get_settings().decision_engine_url
        # ScoreReplayRequest caps a single request at 5000 inputs — chunk if the window's own
        # resolved-signal count exceeds that (a real possibility at a wide window/style with
        # thousands of resolved BUY outcomes).
        results: list[dict] = []
        for i in range(0, len(inputs), 5000):
            chunk = inputs[i:i + 5000]
            r = httpx.post(
                f"{de_url}/decide/score-replay",
                json={"inputs": chunk, "cfg": cfg},
                headers={"Authorization": f"Bearer {_svc_token()}"},
                timeout=60.0,
            )
            r.raise_for_status()
            results.extend(r.json()["results"])
        return results
    except Exception:
        return None


def _scorer_backtest_result(
    style: str, market: str, cfg_label: str, window_start: date, window_end: date,
    replay_results: list[dict] | None, n_signals_seen: int,
) -> BacktestResult:
    """Fold a /decide/score-replay response into the SAME BacktestResult shape every other
    walk-forward function in this module produces, so _passes_promotion_margin()/_result_dict()
    apply unchanged — a candidate that failed the HTTP call at all is scored identically to one
    that returned zero entries below the sample floor (skipped_reason set either way)."""
    result = BacktestResult(
        style=style, market=market, cfg_label=cfg_label,
        window_start=window_start, window_end=window_end,
        n_signals_seen=n_signals_seen, n_entered=0,
    )
    if replay_results is None:
        result.skipped_reason = "decision-engine unreachable for this candidate"
        return result
    entered = [r for r in replay_results if r["entered"]]
    result.n_entered = len(entered)
    result.entered_signal_ids = [r["signal_id"] for r in entered]
    result.returns = [float(r["pct_return"]) for r in entered]
    if result.n_entered < MIN_SAMPLES_PER_SPLIT:
        result.skipped_reason = (
            f"only {result.n_entered} signals passed the gate (need {MIN_SAMPLES_PER_SPLIT})"
        )
        return result
    wins = sum(1 for r in result.returns if r > 0)
    result.win_rate = round(wins / result.n_entered, 4)
    result.avg_return_pct = round(sum(result.returns) / len(result.returns) * 100, 4)
    return result


def walk_forward_scorer_sweep(
    session: Session, style: str, market: str, base_cfg: dict,
    window_start: date, window_end: date,
) -> dict:
    """Walk-forward sweep over decision-engine's compute_score()/min_score_for_regime()
    threshold constants (T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A items #3, #8, #9, #10, #11,
    #12, #14) — same chronological 70/30 split + _passes_promotion_margin() discipline as every
    sibling walk-forward function in this module, applied against the REAL decision-engine
    scoring path via /decide/score-replay rather than a re-implementation.

    Only ever tunes ONE constant at a time per candidate (see _scorer_sweep_candidates()) — the
    winning train-slice candidate across the WHOLE pool is then re-measured on the held-out
    validation slice against the unmodified baseline (base_cfg with no override at all)."""
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

    train_inputs = _fetch_score_replay_inputs(session, style, market, window_start, train_end)
    if len(train_inputs) < MIN_SAMPLES_PER_SPLIT:
        return {
            "style": style, "market": market,
            "skipped_reason": (
                f"only {len(train_inputs)} resolved BUY signals in the train slice "
                f"(need {MIN_SAMPLES_PER_SPLIT})"
            ),
        }

    baseline_train_results = _score_replay_via_http(train_inputs, base_cfg)
    baseline_train = _scorer_backtest_result(
        style, market, "baseline (train)", window_start, train_end,
        baseline_train_results, len(train_inputs),
    )
    if baseline_train.skipped_reason is not None or baseline_train.avg_return_pct is None:
        return {
            "style": style, "market": market,
            "skipped_reason": "baseline itself did not produce a measurable train-slice result",
            "baseline_train": _result_dict(baseline_train),
        }

    best_candidate: dict | None = None
    best_train: BacktestResult | None = None
    for candidate in _scorer_sweep_candidates():
        cfg = {**base_cfg, **candidate}
        cand_results = _score_replay_via_http(train_inputs, cfg)
        cand_train = _scorer_backtest_result(
            style, market, f"candidate {candidate} (train)", window_start, train_end,
            cand_results, len(train_inputs),
        )
        if cand_train.skipped_reason is not None or cand_train.avg_return_pct is None:
            continue
        if cand_train.avg_return_pct <= baseline_train.avg_return_pct:
            continue
        if best_train is None or cand_train.avg_return_pct > best_train.avg_return_pct:
            best_candidate, best_train = candidate, cand_train

    if best_candidate is None:
        return {
            "style": style, "market": market,
            "promoted": False,
            "train_window": [str(window_start), str(train_end)],
            "baseline_train": _result_dict(baseline_train),
            "note": "no candidate beat the baseline on the train slice — nothing to validate.",
        }

    val_inputs = _fetch_score_replay_inputs(session, style, market, val_start, resolvable_end)
    baseline_val_results = _score_replay_via_http(val_inputs, base_cfg)
    baseline_val = _scorer_backtest_result(
        style, market, "baseline (validation)", val_start, resolvable_end,
        baseline_val_results, len(val_inputs),
    )
    cand_cfg = {**base_cfg, **best_candidate}
    cand_val_results = _score_replay_via_http(val_inputs, cand_cfg)
    cand_val = _scorer_backtest_result(
        style, market, f"candidate {best_candidate} (validation)", val_start, resolvable_end,
        cand_val_results, len(val_inputs),
    )
    promoted = _passes_promotion_margin(cand_val, baseline_val)

    return {
        "style": style, "market": market,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "best_candidate": best_candidate,
        "baseline_train": _result_dict(baseline_train),
        "best_candidate_train": _result_dict(best_train),
        "baseline_validation": _result_dict(baseline_val),
        "candidate_validation": _result_dict(cand_val),
        "promoted": promoted,
        "note": (
            "promoted=True means best_candidate beat the unmodified baseline on the held-out "
            f"validation slice by at least {_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least "
            f"{_MIN_PROMOTION_LIFT_SD_RATIO}x the validation slice's own return dispersion "
            "(same BUG233-BACKTESTHARNESS-COINFLIP margin every other walk-forward function in "
            "this module enforces). This is a research signal only — promoting a candidate here "
            "does NOT change any live decision-engine config; applying it to real trading "
            "requires a separate, explicit config change. Scope caveats: only the ONE "
            "highest-train-EV candidate across the whole one-at-a-time sweep pool was validated "
            "(not every candidate independently), no multiple-comparisons correction across "
            "that pool, is_pre_choppy/is_pre_risk_off/recent_win_rate/live_regime are never "
            "replayed (same permanent gap as every other function in this module), and Layer 3e "
            "signal freshness is never scored at all (item #4's own as_of-injection fix is a "
            "separate, not-yet-built prerequisite)."
        ),
    }


# ── BT-2: replay fidelity — does the replay reproduce what ACTUALLY happened? ─────────────
#
# See docs/2026-09-06/SCOPE_BACKTEST_GENERATED_TRAINING_DATA.md. The premise of expanding
# paper-trade data by replaying entry gates over the 45k persisted signals is only as good as
# the replay's fidelity — so this measures it directly against ground truth rather than
# assuming it.
#
# The test: over the window where REAL paper trades exist, replay the gates across the same
# candidate signals and ask how often the replay's enter/skip decision matches what the live
# engine actually did. A real PaperTrade row with a signal_id IS the record that the live
# engine decided to enter on that signal.
#
# WHY A PERFECT MATCH IS NOT THE TARGET — and treating a mismatch as a bug would be wrong:
#   1. live_regime is None on every replay call. This is the module's own documented PERMANENT
#      gap (see the module docstring): the canonical regime classifier has no historical
#      persistence anywhere, and sig.reasons["market_regime"] is a DIFFERENT classifier with a
#      different vocabulary, so substituting it would be a worse bug than the gap. Any signal
#      the live engine gated on regime is expected to diverge here.
#   2. Portfolio-level state is not replayed at all — max_positions, per-sector caps, cash on
#      hand, daily-entry caps, circuit breakers. The live engine may have SKIPPED a signal that
#      passed every per-signal gate purely because the book was full that day.
#   3. cfg drift — gates have been retuned since these trades were taken, so the replay uses
#      today's thresholds against decisions made under older ones.
#
# Because of (2) especially, the honest asymmetry is: **a replayed ENTER on a signal the live
# engine skipped is often legitimate** (the book was full), **but a replayed SKIP on a signal
# the live engine really entered is the genuinely suspicious direction** — it means a
# per-signal gate rejects something that actually passed. That asymmetry is what
# `recall_on_real_trades` below measures, and it is the number to judge fidelity on.

@dataclass
class ReplayFidelityResult:
    style: str
    market: str
    window_start: date
    window_end: date
    n_real_trades: int = 0            # real PaperTrade rows with a signal_id in-window
    n_real_matched_in_replay: int = 0  # ...that the replay universe could even see
    n_replay_entered: int = 0          # replay said ENTER on a real-traded signal
    n_replay_skipped: int = 0          # replay said SKIP on a real-traded signal (suspicious)
    skipped_reason: str | None = None
    skipped_signal_ids: list[int] = field(default_factory=list)
    # Why the replay rejected real trades, tallied. A bare recall number isn't actionable —
    # this is what tells you whether a low score is benign cfg drift (gates retuned TIGHTER
    # since these trades were taken, so of course today's thresholds reject them) or a genuine
    # replay defect.
    skip_reason_counts: dict = field(default_factory=dict)

    @property
    def recall_on_real_trades(self) -> float | None:
        """Of the real trades the replay could see, what share did it also enter?

        This is the fidelity number that matters — see the asymmetry note above. None when
        there was nothing to measure, deliberately NOT 0.0, so "no data" can never be read as
        "0% fidelity"."""
        if self.n_real_matched_in_replay == 0:
            return None
        return self.n_replay_entered / self.n_real_matched_in_replay

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "market": self.market,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "n_real_trades": self.n_real_trades,
            "n_real_matched_in_replay": self.n_real_matched_in_replay,
            "n_replay_entered": self.n_replay_entered,
            "n_replay_skipped": self.n_replay_skipped,
            "recall_on_real_trades": (
                round(self.recall_on_real_trades, 4) if self.recall_on_real_trades is not None else None
            ),
            "skipped_signal_ids": self.skipped_signal_ids[:50],
            "skip_reason_counts": dict(
                sorted(self.skip_reason_counts.items(), key=lambda kv: -kv[1])[:10]
            ),
            "skipped_reason": self.skipped_reason,
            "caveats": (
                "Replay is regime-blind (live_regime=None — a permanent gap, see this module's "
                "docstring) and does NOT model portfolio-level state (max_positions, sector "
                "caps, cash, daily-entry caps, circuit breakers). A replayed ENTER on a signal "
                "the live engine skipped is therefore often legitimate; a replayed SKIP on a "
                "signal it really entered is the suspicious direction. Judge fidelity on "
                "recall_on_real_trades, not on exact agreement."
            ),
        }


def verify_replay_fidelity(
    session: Session,
    style: str,
    market: str,
    cfg: dict,
    window_start: date,
    window_end: date,
) -> ReplayFidelityResult:
    """BT-2: measure how well replay_should_enter() reproduces REAL paper-trade decisions.

    Gate BT-3 (wiring replayed data into tuners) on this passing — if the replay can't
    reproduce the trades that actually happened, it must not be trusted on the ones that
    didn't.
    """
    from db import PaperTrade  # local import: keeps this module's import surface unchanged

    style = style.upper()
    result = ReplayFidelityResult(
        style=style, market=market, window_start=window_start, window_end=window_end,
    )

    # Real trades in-window that came from a signal we can join back to.
    real_rows = session.execute(
        select(PaperTrade.signal_id)
        .where(
            PaperTrade.trading_style == style,
            PaperTrade.signal_id.is_not(None),
            PaperTrade.entry_date >= window_start,
            PaperTrade.entry_date <= window_end,
        )
    ).all()
    real_signal_ids = {r[0] for r in real_rows}
    result.n_real_trades = len(real_signal_ids)
    if not real_signal_ids:
        result.skipped_reason = "no real paper trades with a signal_id in this window"
        return result

    # The replay universe: signals this harness can score at all (needs a resolved outcome).
    matched = _fetch_matched_signals(session, style, market, window_start, window_end)
    by_id = {sig.id: (sig, outcome, stock) for sig, outcome, stock in matched}

    visible = real_signal_ids & by_id.keys()
    result.n_real_matched_in_replay = len(visible)
    if not visible:
        result.skipped_reason = (
            f"{len(real_signal_ids)} real trades in window, but none are in the replay universe "
            f"(a signal needs a RESOLVED outcome for the {_HORIZON_BUCKET[style]} bucket to be "
            f"scoreable — recent trades may simply not have matured yet)"
        )
        return result

    for sig_id in sorted(visible):
        sig, outcome, stock = by_id[sig_id]
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
        should, _score, _notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime=None, kscore=None,
            as_of=_entry_as_of(outcome.entry_date or outcome.signal_date, market),
        )
        if should:
            result.n_replay_entered += 1
        else:
            result.n_replay_skipped += 1
            result.skipped_signal_ids.append(sig_id)
            key = str(_notes)[:120] if _notes else "(no reason given)"
            result.skip_reason_counts[key] = result.skip_reason_counts.get(key, 0) + 1

    return result


# ── BT-1: replay the gates across ALL persisted signal history ───────────────────────────
#
# See docs/2026-09-06/SCOPE_BACKTEST_GENERATED_TRAINING_DATA.md. Paper trading only started
# 2026-06-16 and only ever acted on signals arriving after that, under whatever cfg was live at
# the time — 124 real trades against 45,278 persisted signals. This replays the CURRENT gates
# across the whole persisted signal history to produce a much larger set of
# (decision, real forward return) pairs.
#
# Why this is honest and not circular: every gate input is read from sig.reasons, the ~170-field
# snapshot frozen at generation time (verified: present on all 45,278 rows, 0 nulls), and every
# OUTCOME is the real SignalOutcome forward return computed from immutable subsequent price
# bars. Only the DECISION is recomputed. Nothing here reads a present-day value and pretends it
# was historical.
#
# HARD FLOOR: 2026-05-25. Before that no sig.reasons snapshot exists, so replaying would mean
# reading today's news sentiment / K-Score / regime and pretending they were historical. This
# function cannot reach further back and deliberately does not try.
#
# What BT-2 already established about interpreting the output (run it first — that ordering is
# the point): today's gates are materially STRICTER than the ones that produced the existing
# 124 trades (AUD-CHASE-ROC10 shipped 2026-09-05; max_entry_gap_pct and confidence floors were
# retuned since). So expect entry counts well below any naive extrapolation from 124, and read
# the result as "what today's gates would have done", never as "what would have happened".

@dataclass
class FullHistoryReplayResult:
    style: str
    market: str
    window_start: date
    window_end: date
    n_signals_seen: int = 0
    n_entered: int = 0
    n_wins: int = 0
    avg_return_pct: float | None = None
    win_rate: float | None = None
    # Weekly clustering — the number that actually matters for whether this sample can support
    # a promotion decision. This platform has been burned by exactly this: a 9.1% win rate on
    # n=11 where 9 fired in a single 8-day window, giving an effective independent sample
    # "closer to 2 than 11". A raw row count hides that; entries_per_week does not.
    n_distinct_weeks: int = 0
    max_entries_in_one_week: int = 0
    skip_reason_counts: dict = field(default_factory=dict)
    skipped_reason: str | None = None

    @property
    def effective_sample_note(self) -> str:
        if self.n_entered == 0 or self.n_distinct_weeks == 0:
            return "no entries to assess"
        concentration = self.max_entries_in_one_week / self.n_entered
        if concentration >= 0.5:
            return (
                f"HIGHLY CLUSTERED — {self.max_entries_in_one_week} of {self.n_entered} entries "
                f"({concentration:.0%}) fell in a single week. Effective independent sample is "
                f"far below the raw count; do NOT treat this as {self.n_entered} observations."
            )
        if self.n_distinct_weeks < 4:
            return (
                f"only {self.n_distinct_weeks} distinct weeks — spans too little time to have "
                f"seen more than one market phase."
            )
        return (
            f"{self.n_entered} entries across {self.n_distinct_weeks} weeks "
            f"(max {self.max_entries_in_one_week} in any one week)."
        )

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "market": self.market,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "n_signals_seen": self.n_signals_seen,
            "n_entered": self.n_entered,
            "n_wins": self.n_wins,
            "win_rate": round(self.win_rate, 4) if self.win_rate is not None else None,
            "avg_return_pct": round(self.avg_return_pct, 4) if self.avg_return_pct is not None else None,
            "n_distinct_weeks": self.n_distinct_weeks,
            "max_entries_in_one_week": self.max_entries_in_one_week,
            "effective_sample_note": self.effective_sample_note,
            "skip_reason_counts": dict(
                sorted(self.skip_reason_counts.items(), key=lambda kv: -kv[1])[:12]
            ),
            "skipped_reason": self.skipped_reason,
            "caveats": (
                "SYNTHETIC — these are replayed decisions, not real trades. Gate inputs come "
                "from each signal's own frozen sig.reasons snapshot and outcomes from real "
                "forward returns, so this is not circular; but the replay is regime-blind "
                "(live_regime=None, a permanent gap) and models NO portfolio-level state "
                "(max_positions, sector caps, cash, daily-entry caps, circuit breakers), so it "
                "over-counts entries a real book could not all have taken. Today's gates are "
                "also stricter than those in force historically. Read as 'what today's "
                "per-signal gates would have admitted', never as realized performance, and "
                "never promote a parameter on this alone — see the scope doc's guardrails."
            ),
        }


def replay_full_signal_history(
    session: Session,
    style: str,
    market: str,
    cfg: dict,
    window_start: date | None = None,
    window_end: date | None = None,
) -> FullHistoryReplayResult:
    """BT-1: replay current gates across all persisted signal history for (style, market)."""
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    # 2026-05-25 is the first date a sig.reasons snapshot exists — see this section's header.
    floor = date(2026, 5, 25)
    ws = max(window_start or floor, floor)
    we = window_end or date.today()

    result = FullHistoryReplayResult(style=style, market=market, window_start=ws, window_end=we)
    matched = _fetch_matched_signals(session, style, market, ws, we)
    result.n_signals_seen = len(matched)
    if not matched:
        result.skipped_reason = "no resolved BUY signals in the persisted-history window"
        return result

    returns: list[float] = []
    weeks: dict[str, int] = {}
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
        should, _score, _notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime=None, kscore=None,
            as_of=_entry_as_of(outcome.entry_date or outcome.signal_date, market),
        )
        if not should:
            key = str(_notes)[:120] if _notes else "(no reason given)"
            result.skip_reason_counts[key] = result.skip_reason_counts.get(key, 0) + 1
            continue
        pct_return = getattr(outcome, f"return_{bucket}")
        is_correct = getattr(outcome, f"is_correct_{bucket}")
        if pct_return is None:
            continue
        returns.append(float(pct_return))
        if is_correct:
            result.n_wins += 1
        d = outcome.signal_date
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk] = weeks.get(wk, 0) + 1

    result.n_entered = len(returns)
    result.n_distinct_weeks = len(weeks)
    result.max_entries_in_one_week = max(weeks.values()) if weeks else 0
    if returns:
        result.avg_return_pct = sum(returns) / len(returns)
        result.win_rate = result.n_wins / len(returns)
    return result


# ── BT-4: replay the AI SIGNAL ALERT gate (_is_conviction_buy) ───────────────────────────
#
# See docs/2026-09-06/SCOPE_BACKTEST_GENERATED_TRAINING_DATA.md §7. BT-1/BT-2 replay
# _should_enter() — the PAPER-TRADE ENTRY gate. That is a genuinely different gate from the one
# deciding whether an AI Signal EMAIL ALERT goes out, which is _is_conviction_buy()
# (scheduler.py). This closes that gap.
#
# This replay is cleaner than BT-1's in one important respect: _is_conviction_buy() is a PURE
# function whose own docstring states it reads regime from the stored signal's reasons dict
# ("the regime at generation time"). So unlike _should_enter(), there is NO regime-blindness
# gap here — the function natively consumes exactly the frozen snapshot a replay can supply.
#
# kscore: the LIVE caller passes a value from a live rankings fetch, but for replay the
# point-in-time-correct source is reasons["kscore"] (the value as of generation; verified 100%
# coverage on recent signals). Using today's ranking instead would be textbook lookahead.
#
# rankings_api_ok=True is passed deliberately: it only changes the WORDING of a failure message
# when kscore is missing, never the pass/fail outcome (see _is_conviction_buy Layer 2).

@dataclass
class AlertGateReplayResult:
    style: str
    market: str
    window_start: date
    window_end: date
    n_signals_seen: int = 0
    n_alerted: int = 0          # conviction gate passed -> an alert would have fired
    n_wins: int = 0
    win_rate: float | None = None
    avg_return_pct: float | None = None
    # Same comparison the gate's own tiering makes — "near" means one SOFT fail (OBV or ADX).
    tier_counts: dict = field(default_factory=dict)
    failed_layer_counts: dict = field(default_factory=dict)
    n_distinct_weeks: int = 0
    max_alerts_in_one_week: int = 0
    # The comparison that actually answers "is the alert gate doing anything useful": how the
    # ALERTED population performed vs. every resolved BUY signal in the same window. A gate that
    # fires on a population no better than the baseline is not adding value, however good its
    # absolute win rate looks.
    baseline_win_rate: float | None = None
    baseline_avg_return_pct: float | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict:
        lift_wr = (
            self.win_rate - self.baseline_win_rate
            if self.win_rate is not None and self.baseline_win_rate is not None else None
        )
        lift_ret = (
            self.avg_return_pct - self.baseline_avg_return_pct
            if self.avg_return_pct is not None and self.baseline_avg_return_pct is not None else None
        )
        return {
            "style": self.style,
            "market": self.market,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "n_signals_seen": self.n_signals_seen,
            "n_alerted": self.n_alerted,
            "win_rate": round(self.win_rate, 4) if self.win_rate is not None else None,
            "avg_return_pct": round(self.avg_return_pct, 4) if self.avg_return_pct is not None else None,
            "baseline_win_rate": round(self.baseline_win_rate, 4) if self.baseline_win_rate is not None else None,
            "baseline_avg_return_pct": (
                round(self.baseline_avg_return_pct, 4) if self.baseline_avg_return_pct is not None else None
            ),
            "win_rate_lift": round(lift_wr, 4) if lift_wr is not None else None,
            "avg_return_lift": round(lift_ret, 4) if lift_ret is not None else None,
            "tier_counts": self.tier_counts,
            "failed_layer_counts": dict(
                sorted(self.failed_layer_counts.items(), key=lambda kv: -kv[1])[:12]
            ),
            "n_distinct_weeks": self.n_distinct_weeks,
            "max_alerts_in_one_week": self.max_alerts_in_one_week,
            "skipped_reason": self.skipped_reason,
            "caveats": (
                "Replays _is_conviction_buy() — the AI Signal EMAIL ALERT gate, NOT the "
                "paper-trade entry gate (_should_enter(), covered by BT-1/BT-2). Inputs come "
                "from each signal's own frozen reasons snapshot, including regime and kscore, "
                "so this replay has no regime-blindness gap. Judge it on win_rate_lift / "
                "avg_return_lift against the baseline of ALL resolved BUY signals in the same "
                "window: a gate whose alerted population performs no better than baseline is "
                "not adding value regardless of its absolute win rate. Check clustering before "
                "trusting any lift figure."
            ),
        }


def replay_alert_gate(
    session: Session,
    style: str,
    market: str,
    window_start: date | None = None,
    window_end: date | None = None,
) -> AlertGateReplayResult:
    """BT-4: replay the AI Signal ALERT conviction gate over persisted signal history."""
    from ..services.scheduler import _is_conviction_buy

    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    floor = date(2026, 5, 25)  # first date a reasons snapshot exists — see BT-1's header
    ws = max(window_start or floor, floor)
    we = window_end or date.today()

    result = AlertGateReplayResult(style=style, market=market, window_start=ws, window_end=we)
    matched = _fetch_matched_signals(session, style, market, ws, we)
    result.n_signals_seen = len(matched)
    if not matched:
        result.skipped_reason = "no resolved BUY signals in the persisted-history window"
        return result

    alerted_returns: list[float] = []
    all_returns: list[float] = []
    all_wins = 0
    weeks: dict[str, int] = {}

    for sig, outcome, stock in matched:
        pct_return = getattr(outcome, f"return_{bucket}")
        is_correct = getattr(outcome, f"is_correct_{bucket}")
        if pct_return is None:
            continue
        # Baseline population: every resolved BUY signal, gate or no gate.
        all_returns.append(float(pct_return))
        if is_correct:
            all_wins += 1

        reasons = sig.reasons or {}
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": reasons,
            # AUD-BT-ALERTHORIZON: `horizon` was MISSING here, and _is_conviction_buy() reads
            # `style = signal_data.get("horizon", "SWING")`. So every style replayed under
            # SWING's rules, silently. GROWTH lost both of its exemptions:
            #   layer 4a — GROWTH needs only trend_above_sma50; SWING requires
            #              sma50_above_sma200 AND trend_above_sma50
            #   layer 4b — GROWTH's RSI band is 50-85; SWING's is 45-72
            # The live caller does supply it (signals_shared.py's _stored_signal_for_style sets
            # "horizon": style_key), so this was a harness-only omission — the same class as
            # AUD-BT-HKCFGDEFAULT: a required input silently defaulting to a PLAUSIBLE wrong
            # value, so nothing raised and the output looked reasonable.
            #
            # This invalidated BT-4's published GROWTH figures. The tell was already visible in
            # the output: the top rejection reason was "Uptrend structure not aligned
            # (SMA50/SMA200/price)" — that message is the NON-GROWTH branch.
            "horizon": style,
        }
        # Point-in-time kscore from the frozen snapshot, NOT a live rankings read.
        raw_k = reasons.get("kscore")
        try:
            kscore = float(raw_k) if raw_k is not None else None
        except (TypeError, ValueError):
            kscore = None

        all_pass, tier, _passed, failed = _is_conviction_buy(
            signal_data, kscore=kscore, rankings_api_ok=True,
        )
        result.tier_counts[tier] = result.tier_counts.get(tier, 0) + 1
        if not all_pass:
            for f in failed:
                key = str(f)[:110]
                result.failed_layer_counts[key] = result.failed_layer_counts.get(key, 0) + 1
            continue

        alerted_returns.append(float(pct_return))
        if is_correct:
            result.n_wins += 1
        d = outcome.signal_date
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk] = weeks.get(wk, 0) + 1

    result.n_alerted = len(alerted_returns)
    result.n_distinct_weeks = len(weeks)
    result.max_alerts_in_one_week = max(weeks.values()) if weeks else 0
    if alerted_returns:
        result.avg_return_pct = sum(alerted_returns) / len(alerted_returns)
        result.win_rate = result.n_wins / len(alerted_returns)
    if all_returns:
        result.baseline_avg_return_pct = sum(all_returns) / len(all_returns)
        result.baseline_win_rate = all_wins / len(all_returns)
    return result
