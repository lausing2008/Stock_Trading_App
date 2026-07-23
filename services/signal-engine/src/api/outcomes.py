"""Signal-engine analytics/backtest/outcome-evaluation routes.

T233-ARCH-INSERVICE-SPLITS: extracted from routes.py's original 6,289 lines (see routes.py's
own module docstring for the full split rationale). This file holds every read-only analytics
endpoint (accuracy, rolling accuracy, factor exposure, trade performance, filter audit,
walk-forward backtest, alpha decay, information coefficient, factor attribution, gate
backtest) plus the outcome-evaluation job (evaluate_signal_outcomes) and the retro-feedback
realized-EV backfill. Verbatim extraction — no logic changes; a bug found here was already
present before the split.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.jwt_auth import get_current_username
from db import Price, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame, TuneHistory, get_session

from .signals_shared import (
    _CONF_CAL_CACHE_KEY, _CONF_CAL_MIN_COUNT, _OUTCOME_CENSOR_GRACE_DAYS, _OUTCOME_HOLD_DAYS,
    _OUTCOME_WIN_HURDLE_PCT, _SELL_OUTCOME_HOLD_DAYS, _cache_get,
    _cache_set, _get_redis, _service_token, _settings, log,
)

router = APIRouter(prefix="/signals", tags=["signals"])

# ── SELFIMPROVE-NO-RETRO-FEEDBACK-LOOP: close the loop on tune_history ────────
# Every calibration mechanism (calibrate_ta_weights, calibrate_ml_weight, calibrate_
# conviction_weights, tune_style_profiles, promotion_gate, signal_watchdog) writes a
# tune_history row recording what it predicted a change would do (validation_ev_pct) — but
# nothing ever checked whether a promoted change ACTUALLY helped in the real trading that
# followed. This function is that check: it backfills realized_ev_pct_after on already-
# promoted rows once enough real SignalOutcome data has accumulated after the change.

_RETRO_MIN_SAMPLES = 50  # same statistical floor calibrate_ta_weights' walk-forward search uses
_RETRO_MIN_WAIT_MULTIPLIER = 3  # wait at least 3x the style's own hold_days before checking —
# one hold_days' worth only guarantees ONE trade cycle has closed, not enough samples to trust
# a win rate; 3x gives room for a genuinely useful sample size to accumulate across multiple
# signals landing over that period, without waiting so long the check becomes irrelevant.


def _retro_ev_for(session: Session, style: str, market: str, since: "date") -> dict | None:
    """Win-rate/EV for SignalOutcome rows in (style, market) with entry_date >= since,
    using the exact same formula every other calibration mechanism in this file uses
    (win_rate = wins/n, ev_pct = mean(pct_return) * 100 — see calibrate_ta_weights'
    _stats_at() for the canonical version this mirrors). Returns None if fewer than
    _RETRO_MIN_SAMPLES outcomes are available — not enough to trust yet, try again next run.
    market="ALL" is this table's own documented convention for "don't filter by market"
    (see _record_tune_history's docstring) — NOT a literal Stock.market value to match.
    """
    query = (
        select(SignalOutcome)
        .where(
            SignalOutcome.horizon == SignalHorizon(style),
            SignalOutcome.entry_date >= since,
            SignalOutcome.is_correct.isnot(None),
            SignalOutcome.pct_return.isnot(None),
        )
    )
    if market != "ALL":
        query = query.join(Stock, Stock.id == SignalOutcome.stock_id).where(Stock.market == market)
    rows = session.execute(query).scalars().all()
    if len(rows) < _RETRO_MIN_SAMPLES:
        return None
    wins = sum(1 for o in rows if o.is_correct)
    win_rate = wins / len(rows)
    ev_pct = (sum(o.pct_return for o in rows) / len(rows)) * 100
    return {"n": len(rows), "win_rate": round(win_rate, 3), "ev_pct": round(ev_pct, 2)}


@router.post("/backfill_realized_ev")
def backfill_realized_ev(
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Backfill realized_ev_pct_after on promoted tune_history rows old enough that real
    SignalOutcome data has accumulated since the change. Safe to re-run — only considers
    rows where realized_ev_pct_after IS NULL, so an already-checked row is never re-touched
    (each row gets exactly one realized-EV verdict, at whatever point it first clears the
    sample floor, not a constantly-shifting rolling number).
    """
    candidates = session.execute(
        select(TuneHistory).where(
            TuneHistory.promoted.is_(True),
            TuneHistory.realized_ev_pct_after.is_(None),
        )
    ).scalars().all()

    checked = 0
    updated = 0
    skipped_too_soon = 0
    skipped_invalid_style = 0
    now = datetime.now(timezone.utc)

    for row in candidates:
        checked += 1
        # BUY's hold_days (_OUTCOME_HOLD_DAYS) is used even for mechanisms that tune
        # SELL-relevant params — it's the longer, more conservative window of the two, and
        # this retro-check aggregates BOTH directions' outcomes together in _retro_ev_for()
        # regardless (a tune_history row's style has no BUY/SELL split of its own), so a
        # single, deliberately-cautious wait period is simpler and safer than trying to pick
        # per-direction. style="ALL" (ml_fusion_weight, market-pooled mechanisms) has no
        # single style's hold_days to use — fall back to the longest window across all
        # styles as the most conservative wait available.
        hold_days = _OUTCOME_HOLD_DAYS.get(row.style, max(_OUTCOME_HOLD_DAYS.values()))

        row_ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
        min_wait_until = row_ts + timedelta(days=hold_days * _RETRO_MIN_WAIT_MULTIPLIER)
        if now < min_wait_until:
            skipped_too_soon += 1
            continue

        if row.style == "ALL":
            # style="ALL" rows (ml_fusion_weight and any other market/style-pooled mechanism)
            # have no single SignalHorizon to query against — SignalOutcome.horizon only has
            # real SHORT/SWING/LONG/GROWTH values, no pooled "ALL" concept of its own.
            # Properly supporting this would mean aggregating across all 4 styles' outcomes
            # instead of one — a real, larger follow-up (tracked, not silently ignored),
            # not attempted in this pass since ml_fusion_weight is genuinely a single global
            # parameter and the per-style mechanisms (the majority of tune_history rows) are
            # the ones this closes the loop for.
            skipped_invalid_style += 1
            continue

        stats = _retro_ev_for(session, row.style, row.market, row_ts.date())
        if stats is None:
            continue  # not enough samples yet — try again next run, don't mark checked-forever

        row.realized_ev_pct_after = stats["ev_pct"]
        row.realized_n_after = stats["n"]
        row.realized_checked_at = now
        updated += 1

    session.commit()
    log.info("backfill_realized_ev: checked=%d updated=%d skipped_too_soon=%d skipped_all_style=%d",
              checked, updated, skipped_too_soon, skipped_invalid_style)
    return {
        "checked": checked, "updated": updated,
        "skipped_too_soon": skipped_too_soon, "skipped_all_style": skipped_invalid_style,
    }


@router.get("/accuracy")
def signal_accuracy(
    lookback_days: int = Query(90, ge=2, le=365),
    symbol: str | None = None,
    market: str | None = Query(None, regex="^(US|HK)$"),
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=10, le=500),
    session: Session = Depends(get_session),
):
    """Historical accuracy of BUY/SELL signals vs actual price outcomes.

    For each persisted BUY or SELL signal within the lookback window, compares
    the close price on the signal date to the most recent available close price.
    A BUY is 'correct' if price rose; a SELL is 'correct' if it fell.
    Signals need at least 1 day of price history after the signal date to be evaluated.
    Uses bulk price queries + bisect matching instead of per-signal queries.

    Optional from_date / to_date (ISO strings, e.g. "2026-03-01") narrow the
    signal window for walk-forward drill-down without affecting lookback_days.
    """
    import bisect

    if from_date and to_date:
        cutoff = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        outcome_cutoff = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc).replace(hour=23, minute=59, second=59)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    q = (
        select(Signal, Stock.symbol, Stock.name)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal.in_([SignalType.BUY, SignalType.SELL]))
        .order_by(Signal.ts.desc())
    )
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    if market:
        q = q.where(Stock.market == market.upper())

    rows = session.execute(q).all()
    if not rows:
        return {"lookback_days": lookback_days, "total_signals": 0, "buy_count": 0,
                "sell_count": 0, "buy_accuracy": None, "sell_accuracy": None,
                "overall_accuracy": None, "avg_buy_return_pct": None,
                "avg_sell_return_pct": None, "profit_factor": None, "signals": []}

    stock_ids = list({sig.stock_id for sig, _, _ in rows})

    # Bulk-fetch all D1 prices for relevant stocks across the full lookback window
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    # stock_id → (sorted date list, close list)
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

    def first_close_after(sid: int, after_date):
        """First close STRICTLY after after_date, returns (close, date) or (None, None)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None, None
        return _pclose[sid][idx], ts_list[idx]

    def most_recent_close(sid: int):
        """Most recent (last) close in the loaded price window, returns (close, date) or (None, None)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        return _pclose[sid][-1], ts_list[-1]

    # Deduplicate: the scheduler runs every ~10 min and inserts repeated signals on
    # the same day. One evaluation per (stock, signal_type, day) is the right unit —
    # we want "was the model correct that day", not 10 identical copies of the same call.
    seen_keys: set[tuple] = set()

    results = []
    for sig, sym, name in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        dedup_key = (sig.stock_id, sig.signal, signal_date)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        # Entry: first close STRICTLY after signal date — avoids same-day look-ahead
        # (old code used price_on_or_before(signal_date+1) which returned Friday's close
        # for Friday signals since signal_date+1 = Saturday is not a trading day)
        entry_close, entry_date = first_close_after(sig.stock_id, signal_date)
        if entry_close is None:
            continue

        # Exit: most recent available close — shows running P&L from entry to today
        # (old code used first_close_after for exit too, making entry == exit → pct=0%)
        exit_close, exit_date = most_recent_close(sig.stock_id)
        if exit_close is None or exit_date is None or exit_date <= signal_date:
            continue
        if entry_close <= 0:
            continue

        pct_change  = (exit_close - entry_close) / entry_close * 100
        signal_type = sig.signal.value
        # AUD232-047: use the same cost hurdle as evaluate_signal_outcomes' is_correct (T232-OC4)
        # instead of a bare zero line — _OUTCOME_WIN_HURDLE_PCT is a fraction (0.005 = 0.5%);
        # pct_change here is already in percentage points, so compare against the *100 hurdle.
        _hurdle_pp = _OUTCOME_WIN_HURDLE_PCT * 100
        correct     = (signal_type == "BUY" and pct_change > _hurdle_pp) or (signal_type == "SELL" and pct_change < -_hurdle_pp)

        results.append({
            "symbol": sym,
            "name": name,
            "signal": signal_type,
            "confidence": round(sig.confidence, 1),
            "bullish_probability": round(sig.bullish_probability, 4) if sig.bullish_probability else None,
            "signal_date": signal_date.isoformat(),
            "entry_price": round(entry_close, 4),
            "exit_price": round(exit_close, 4),
            "pct_change": round(pct_change, 2),
            "correct": correct,
            "days_held": (exit_date - signal_date).days,
        })

    buy_r  = [r for r in results if r["signal"] == "BUY"]
    sell_r = [r for r in results if r["signal"] == "SELL"]

    def _accuracy(items: list) -> float | None:
        return round(sum(1 for i in items if i["correct"]) / len(items) * 100, 1) if items else None

    def _avg_return(items: list) -> float | None:
        return round(sum(i["pct_change"] for i in items) / len(items), 2) if items else None

    def _profit_factor(items: list) -> float | None:
        # Use abs() so correct SELL signals (negative pct_change) count as gains,
        # not as losses — profit factor measures magnitude of wins vs losses.
        wins   = sum(abs(i["pct_change"]) for i in items if i["correct"])
        losses = sum(abs(i["pct_change"]) for i in items if not i["correct"])
        return round(wins / losses, 2) if losses > 0 else None

    offset = (page - 1) * page_size
    page_signals = results[offset: offset + page_size]

    return {
        "lookback_days": lookback_days,
        "total_signals": len(results),
        "buy_count": len(buy_r),
        "sell_count": len(sell_r),
        "buy_accuracy": _accuracy(buy_r),
        "sell_accuracy": _accuracy(sell_r),
        "overall_accuracy": _accuracy(results),
        "avg_buy_return_pct": _avg_return(buy_r),
        "avg_sell_return_pct": _avg_return(sell_r),
        "profit_factor": _profit_factor(results),
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < len(results),
        "signals": page_signals,
    }


@router.get("/rolling_accuracy")
def rolling_accuracy(
    window: int = Query(30, ge=7, le=90),
    lookback_days: int = Query(180, ge=60, le=730),
    session: Session = Depends(get_session),
):
    """Rolling accuracy of BUY signals over a sliding window.

    Returns a time-series of {date, accuracy_30d, signal_count} for each day in
    the lookback period where at least `window` evaluated BUY signals exist.
    Also returns a drift_warning flag if the latest window accuracy < 55%.
    """
    import bisect

    # Require 7+ calendar days of forward data so the 5-day exit price exists.
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.ts >= cutoff,
            Signal.ts <= outcome_cutoff,
            Signal.signal == SignalType.BUY,
        )
        .order_by(Signal.ts.asc())
    ).all()

    if not rows:
        return {"window": window, "lookback_days": lookback_days, "series": [], "drift_warning": False, "latest_accuracy": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    # Fetch prices from cutoff through today so we can compute 5-day forward exits.
    price_since = (cutoff - timedelta(days=2)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1, Price.ts >= price_since)
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

    def first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        return _pclose[sid][idx] if idx < len(ts_list) else None

    # Build list of evaluated signals using fixed 5-day forward exit (same as main accuracy table).
    # This ensures every signal in the drift series is evaluated over the same holding period.
    evaluated: list[tuple[date, bool]] = []
    seen: set[tuple] = set()
    for sig, sym in rows:
        sig_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, sig_date, sig.horizon)
        if key in seen:
            continue
        seen.add(key)
        entry = first_close_after(sig.stock_id, sig_date)
        exit_target = sig_date + timedelta(days=7)  # 7 calendar days ≈ 5 trading days
        exit_ = first_close_after(sig.stock_id, exit_target)
        if entry is None or exit_ is None or entry <= 0:
            continue
        # AUD232-047: use the same cost hurdle as evaluate_signal_outcomes' is_correct
        # (T232-OC4/_OUTCOME_WIN_HURDLE_PCT) instead of a bare zero line, so this drift
        # series' "win" definition agrees with the canonical calibration-loop definition.
        correct = (exit_ - entry) / entry > _OUTCOME_WIN_HURDLE_PCT
        evaluated.append((sig_date, correct))

    if not evaluated:
        return {"window": window, "lookback_days": lookback_days, "series": [], "drift_warning": False, "latest_accuracy": None}

    # Compute rolling accuracy: for each unique date in the dataset, use the
    # trailing `window` calendar days of evaluated signals ending on that date.
    unique_dates = sorted({d for d, _ in evaluated})
    series = []
    for end_date in unique_dates:
        start_date = end_date - timedelta(days=window - 1)
        window_sigs = [(d, c) for d, c in evaluated if start_date <= d <= end_date]
        if len(window_sigs) < 3:
            continue
        acc = round(sum(1 for _, c in window_sigs if c) / len(window_sigs) * 100, 1)
        series.append({"date": end_date.isoformat(), "accuracy": acc, "signal_count": len(window_sigs)})

    latest_accuracy = series[-1]["accuracy"] if series else None
    drift_warning = latest_accuracy is not None and latest_accuracy < 55.0

    return {
        "window": window,
        "lookback_days": lookback_days,
        "series": series,
        "drift_warning": drift_warning,
        "latest_accuracy": latest_accuracy,
    }


@router.get("/factor-exposure")
def factor_exposure(
    lookback_days: int = Query(90, ge=7, le=365),
    session: Session = Depends(get_session),
):
    """Factor tilt analysis of BUY signals — compares factor values for correct vs wrong calls.

    Extracts numeric factors from the reasons JSON of each BUY signal, pairs with
    price outcome (did price rise after signal?), then returns per-factor averages
    split by correct / wrong so the caller can see which factor dimensions correlate
    with successful signals.
    """
    import bisect

    cache_key = f"signals:cache:factor_exposure:{lookback_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"lookback_days": lookback_days, "signal_count": 0, "factors": []}

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

    def price_on_or_before(sid: int, d):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, d) - 1
        return _pclose[sid][idx] if idx >= 0 else None

    def _first_close_after_fe(sid: int, after_date):
        """Return the first close strictly after after_date (no lookahead)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        return _pclose[sid][idx] if idx < len(ts_list) else None

    def most_recent_close_fe(sid: int):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        return _pclose[sid][-1]

    # factor key → (label, neutral baseline, display scale)
    FACTORS = [
        ("rsi",             "RSI",             50.0,  100.0),
        ("adx",             "ADX",             20.0,  100.0),
        ("volume_z",        "Volume Z",         0.0,    3.0),
        ("ml_probability",  "ML Probability",   0.5,    1.0),
        ("news_sentiment",  "News Sentiment",  50.0,  100.0),
        ("ta_score",        "TA Score",         0.5,    1.0),
    ]

    correct_vals: dict[str, list[float]] = {f[0]: [] for f in FACTORS}
    wrong_vals:   dict[str, list[float]] = {f[0]: [] for f in FACTORS}
    seen: set[tuple] = set()
    total = 0

    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)

        reasons = sig.reasons or {}
        entry = _first_close_after_fe(sig.stock_id, signal_date)
        if entry is None or entry <= 0:
            continue
        exit_p = most_recent_close_fe(sig.stock_id)
        if exit_p is None:
            continue

        total += 1
        correct = exit_p > entry

        for fname, _, _, _ in FACTORS:
            raw = reasons.get(fname)
            if raw is None:
                continue
            try:
                v = float(raw)
                (correct_vals if correct else wrong_vals)[fname].append(v)
            except (TypeError, ValueError):
                pass

    def _avg(lst: list[float]):
        return round(sum(lst) / len(lst), 4) if lst else None

    factors = []
    for fname, label, baseline, scale in FACTORS:
        c_avg = _avg(correct_vals[fname])
        w_avg = _avg(wrong_vals[fname])
        # deviation_pct: how far from neutral baseline as % of the scale range
        def _dev(v):
            if v is None:
                return None
            return round((v - baseline) / scale * 100, 1)
        factors.append({
            "key": fname,
            "label": label,
            "baseline": baseline,
            "scale": scale,
            "correct_avg": c_avg,
            "wrong_avg": w_avg,
            "correct_dev_pct": _dev(c_avg),
            "wrong_dev_pct": _dev(w_avg),
            "correct_count": len(correct_vals[fname]),
            "wrong_count": len(wrong_vals[fname]),
        })

    result = {"lookback_days": lookback_days, "signal_count": total, "factors": factors}
    _cache_set(cache_key, result)
    return result


@router.get("/trade_performance")
def trade_performance(
    lookback_days: int = Query(180, ge=7, le=730),
    symbol: str | None = None,
    horizon: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    market: str | None = Query(None, regex="^(US|HK)$", description="Filter to one market"),
    wait_exits: bool = Query(False, description="Treat same-horizon WAIT as exit (exits when momentum fades)"),
    max_hold_days: int | None = Query(None, ge=1, le=365, description="Force-close after N days. Defaults: SHORT=7, SWING=25, LONG=90"),
    min_confidence: float = Query(0.0, ge=0, le=100, description="Only include BUY signals with confidence >= this value"),
    session: Session = Depends(get_session),
):
    """BUY → SELL/WAIT trade-pair performance over a lookback window.

    Filters by horizon (SHORT/SWING/LONG) so exits are only matched within the
    same trading style — no cross-contamination between horizons.

    Exit rules (applied in priority order):
      1. SELL signal (always an exit)
      2. WAIT signal when wait_exits=True (exits on fading momentum, same horizon)
      3. max_hold_days time-stop (defaults: SHORT=7, SWING=25, LONG=90)
      4. Latest price if no exit found (open trade)

    Open trades (no exit found) use the latest available price.
    """
    import bisect
    from collections import defaultdict

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    horizon_enum = SignalHorizon(horizon)

    # Style-appropriate default max hold periods (prevents SHORT trades drifting for months)
    _default_max_hold = {"SHORT": 7, "SWING": 25, "LONG": 90}
    effective_max_hold: int = max_hold_days if max_hold_days is not None else _default_max_hold[horizon]

    # 1. All BUY signals in the window for the requested horizon
    q = (
        select(Signal, Stock.symbol, Stock.name)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Stock.active.is_(True))
        .where(Signal.ts >= cutoff)
        .where(Signal.signal == SignalType.BUY)
        .where(Signal.horizon == horizon_enum)
        .order_by(Stock.symbol, Signal.ts)
    )
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    if market:
        q = q.where(Stock.market == market.upper())
    if min_confidence > 0:
        q = q.where(Signal.confidence >= min_confidence)
    buy_rows = session.execute(q).all()

    if not buy_rows:
        return {"lookback_days": lookback_days, "closed_trades": 0, "open_trades": 0,
                "win_rate": None, "avg_return_pct": None, "avg_win_pct": None,
                "avg_loss_pct": None, "profit_factor": None, "avg_hold_days": None,
                "by_symbol": [], "trades": []}

    stock_ids = list({sig.stock_id for sig, _, _ in buy_rows})

    # 2. Exit signals — SELL always exits; WAIT exits when wait_exits=True.
    # Both are filtered by the same horizon to prevent cross-style contamination
    # (the old phantom-0-day bug was SHORT=BUY + SWING=WAIT in the same batch).
    exit_signal_filter = (
        Signal.signal.in_([SignalType.SELL, SignalType.WAIT])
        if wait_exits
        else Signal.signal == SignalType.SELL
    )
    exit_rows = session.execute(
        select(Signal.stock_id, Signal.ts, Signal.signal)
        .where(Signal.stock_id.in_(stock_ids))
        .where(exit_signal_filter)
        .where(Signal.horizon == horizon_enum)
        .order_by(Signal.stock_id, Signal.ts)
    ).all()

    # stock_id → (sorted ts list, signal value list)
    _exit_ts: dict[int, list] = defaultdict(list)
    _exit_val: dict[int, list] = defaultdict(list)
    for row in exit_rows:
        _exit_ts[row.stock_id].append(row.ts)
        _exit_val[row.stock_id].append(row.signal.value)

    # 3. All D1 prices for those stocks from just before the lookback window to today
    since_date = (cutoff - timedelta(days=7)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= since_date)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    # stock_id → (sorted date list, close list)
    # Normalise Price.ts to date — driver may return datetime or date depending on schema
    _price_ts: dict[int, list] = defaultdict(list)
    _price_close: dict[int, list] = defaultdict(list)
    for row in price_rows:
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _price_ts[row.stock_id].append(d)
        _price_close[row.stock_id].append(float(row.close))

    def price_on_or_before(sid: int, d) -> float | None:
        ts_list = _price_ts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, d) - 1
        return _price_close[sid][idx] if idx >= 0 else None

    def latest_price(sid: int):
        ts_list = _price_ts.get(sid)
        if not ts_list:
            return None, None
        return _price_close[sid][-1], ts_list[-1]

    def next_exit(sid: int, after_ts):
        ts_list = _exit_ts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_ts)
        if idx >= len(ts_list):
            return None, None
        return ts_list[idx], _exit_val[sid][idx]

    # 4. Pair each BUY with its exit — pure Python, no more per-signal queries.
    # Two dedup guards prevent duplicate trades from intraday scheduler refreshes:
    #   last_exit_ts — blocks BUYs before the previous closed trade's exit timestamp.
    #   in_open_trade — blocks new BUYs while an open (unclosed) position already exists.
    last_exit_ts: dict[int, object] = {}  # stock_id → exit ts of last closed trade
    in_open_trade: set[int] = set()       # stock_ids already represented by an open trade

    trades = []
    for sig, sym, name in buy_rows:
        sid = sig.stock_id
        # Guard 1: BUY arrived before the last closed trade's exit — duplicate refresh.
        if sid in last_exit_ts and sig.ts <= last_exit_ts[sid]:
            continue
        # Guard 2: We're already tracking an open position for this stock.
        if sid in in_open_trade:
            continue

        # Entry on the first trading day with actual price data after the signal date.
        # price_on_or_before(signal_date + 1 calendar day) was wrong for Friday signals:
        # signal_date + 1 = Saturday → price_on_or_before returns Friday's close (lookahead).
        _sid_ts = _price_ts.get(sid, [])
        _entry_idx = bisect.bisect_right(_sid_ts, sig.ts.date())
        if _entry_idx >= len(_sid_ts):
            continue
        entry_date = _sid_ts[_entry_idx]
        entry_price = _price_close[sid][_entry_idx]

        exit_ts, exit_signal_val = next_exit(sid, sig.ts)

        # Apply max-hold time-stop: if no exit or exit is beyond the limit, cut at max_hold_days.
        # This prevents SHORT (1-5d) trades from drifting for weeks with no exit.
        max_exit_date = entry_date + timedelta(days=effective_max_hold)
        if exit_ts is not None:
            signal_exit_date = exit_ts.date() + timedelta(days=1)
            if signal_exit_date <= max_exit_date:
                # Normal signal exit within the hold window
                exit_date  = signal_exit_date
                exit_price = price_on_or_before(sid, exit_date)
                status     = "closed"
                last_exit_ts[sid] = exit_ts
            else:
                # Signal exit is beyond max hold — apply time-stop instead
                exit_date       = max_exit_date
                exit_price      = price_on_or_before(sid, exit_date)
                exit_signal_val = f"TIME({effective_max_hold}d)"
                status          = "closed"
                last_exit_ts[sid] = exit_ts  # still mark so we don't re-enter
        else:
            # No exit signal found — apply time-stop if position has exceeded limit
            today = datetime.now(timezone.utc).date()
            if today >= max_exit_date:
                # Time-stop triggered
                exit_date       = max_exit_date
                exit_price      = price_on_or_before(sid, exit_date)
                exit_signal_val = f"TIME({effective_max_hold}d)"
                status          = "closed"
            else:
                # Still within hold window — open position, use latest price
                exit_price, exit_ts_raw = latest_price(sid)
                if exit_price is None:
                    continue
                exit_date       = exit_ts_raw.date() if isinstance(exit_ts_raw, datetime) else exit_ts_raw
                exit_signal_val = "OPEN"
                status          = "open"
                in_open_trade.add(sid)

        if exit_price is None or entry_price <= 0:
            continue

        pct       = (exit_price - entry_price) / entry_price * 100
        hold_days = (exit_date - entry_date).days

        trades.append({
            "symbol":           sym,
            "name":             name,
            "status":           status,
            "entry_date":       entry_date.isoformat(),
            "exit_date":        exit_date.isoformat(),
            "entry_price":      round(entry_price, 4),
            "exit_price":       round(exit_price, 4),
            "pct_return":       round(pct, 2),
            "hold_days":        hold_days,
            "win":              pct > 0,
            "exit_signal":      exit_signal_val,
            "entry_confidence": round(sig.confidence, 1),
        })

    import math, statistics as _stats

    closed = [t for t in trades if t["status"] == "closed"]
    open_t = [t for t in trades if t["status"] == "open"]
    wins   = [t for t in closed if t["win"]]
    losses = [t for t in closed if not t["win"]]

    gross_wins   = sum(t["pct_return"] for t in wins)
    gross_losses = abs(sum(t["pct_return"] for t in losses))

    by_sym: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "total_return": 0.0, "hold_days": 0})
    for t in closed:
        s = t["symbol"]
        by_sym[s]["trades"]       += 1
        by_sym[s]["wins"]         += int(t["win"])
        by_sym[s]["total_return"] += t["pct_return"]
        by_sym[s]["hold_days"]    += t["hold_days"]
    symbol_summary = [
        {
            "symbol":        s,
            "trades":        v["trades"],
            "win_rate":      round(v["wins"] / v["trades"] * 100, 1),
            "avg_return":    round(v["total_return"] / v["trades"], 2),
            "avg_hold_days": round(v["hold_days"] / v["trades"], 1),
        }
        for s, v in sorted(by_sym.items())
    ]

    # ── Equity curve (closed trades compounded in entry-date order) ──────────
    sorted_closed = sorted(closed, key=lambda t: t["entry_date"])
    equity = 1.0
    equity_curve: list = []
    if sorted_closed:
        equity_curve.append({"date": sorted_closed[0]["entry_date"], "equity": 1.0})
    for t in sorted_closed:
        equity *= 1 + t["pct_return"] / 100
        equity_curve.append({"date": t["exit_date"], "equity": round(equity, 4)})

    total_return = round((equity - 1) * 100, 2) if sorted_closed else None

    # ── Sharpe ratio (per-trade returns, annualised) ─────────────────────────
    sharpe = None
    if len(closed) >= 2:
        returns = [t["pct_return"] for t in closed]
        avg_hd  = max(sum(t["hold_days"] for t in closed) / len(closed), 1)
        mean_r  = _stats.mean(returns)
        std_r   = _stats.stdev(returns)
        if std_r > 0:
            sharpe = round(mean_r / std_r * math.sqrt(252 / avg_hd), 2)

    # ── Max drawdown from equity curve ───────────────────────────────────────
    max_drawdown = None
    if equity_curve:
        peak = 1.0
        worst = 0.0
        for pt in equity_curve:
            if pt["equity"] > peak:
                peak = pt["equity"]
            dd = (pt["equity"] - peak) / peak * 100
            if dd < worst:
                worst = dd
        max_drawdown = round(worst, 2)  # negative e.g. -12.3

    # ── Calmar ratio ─────────────────────────────────────────────────────────
    calmar = None
    if total_return is not None and max_drawdown is not None and max_drawdown < 0 and sorted_closed:
        first_d   = date.fromisoformat(sorted_closed[0]["entry_date"])
        last_d    = date.fromisoformat(sorted_closed[-1]["exit_date"])
        total_days = (last_d - first_d).days
        if total_days > 0:
            ann_ret = total_return / total_days * 252
            calmar  = round(ann_ret / abs(max_drawdown), 2)

    # ── SPY benchmark return over the same date range ────────────────────────
    spy_return = None
    if sorted_closed:
        first_d = date.fromisoformat(sorted_closed[0]["entry_date"])
        last_d  = date.fromisoformat(sorted_closed[-1]["exit_date"])
        spy_stock = session.query(Stock).filter(Stock.symbol == "SPY").one_or_none()
        if spy_stock:
            spy_prices = session.execute(
                select(Price.ts, Price.close)
                .where(Price.stock_id == spy_stock.id)
                .where(Price.timeframe == TimeFrame.D1)
                .where(Price.ts >= first_d)
                .where(Price.ts <= last_d)
                .order_by(Price.ts)
            ).all()
            if len(spy_prices) >= 2:
                s0 = float(spy_prices[0].close)
                s1 = float(spy_prices[-1].close)
                spy_return = round((s1 - s0) / s0 * 100, 2)

    return {
        "lookback_days":  lookback_days,
        "closed_trades":  len(closed),
        "open_trades":    len(open_t),
        "win_rate":       round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_return_pct": round(sum(t["pct_return"] for t in closed) / len(closed), 2) if closed else None,
        "avg_win_pct":    round(gross_wins / len(wins), 2) if wins else None,
        "avg_loss_pct":   round(-gross_losses / len(losses), 2) if losses else None,
        "profit_factor":  round(gross_wins / gross_losses, 2) if gross_losses > 0 else None,
        "avg_hold_days":  round(sum(t["hold_days"] for t in closed) / len(closed), 1) if closed else None,
        "total_return":   total_return,
        "sharpe":         sharpe,
        "max_drawdown":   max_drawdown,
        "calmar":         calmar,
        "spy_return":     spy_return,
        "equity_curve":   equity_curve,
        "by_symbol":      symbol_summary,
        "trades":         trades,
    }


@router.get("/filter_audit")
def filter_audit(
    lookback_days: int = Query(180, ge=30, le=730),
    style: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    hold_days: int = Query(10, ge=1, le=60, description="Days after signal to measure outcome"),
    session: Session = Depends(get_session),
):
    """Correlate active suppression filter count with actual trade win rate.

    For every BUY signal in the lookback window, counts how many suppression
    filters were active at signal time (from reasons JSON), then looks up the
    actual price return hold_days later.  Returns win-rate breakdown by filter
    count so you can see whether heavily-filtered signals genuinely perform worse.
    """
    cache_key = f"signals:cache:filter_audit:{lookback_days}:{style}:{hold_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    since = date.today() - timedelta(days=lookback_days)
    try:
        horizon_enum = SignalHorizon(style.upper())
    except ValueError:
        horizon_enum = SignalHorizon.SWING

    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.signal == SignalType.BUY,
            Signal.horizon == horizon_enum,
            Signal.ts >= since,
            Signal.reasons.isnot(None),
        )
        .order_by(Signal.ts)
    ).all()

    SUPPRESSION_BOOLEAN = [
        "weekly_gate_fired", "adx_compression",
        "high_vol_compression", "breadth_compression",
        "stale_price_warning", "insufficient_history_warning",
    ]
    SUPPRESSION_NAMED = {
        "weekly_alignment":    lambda v: v is False,
        "earnings_warning":    lambda v: v in ("caution", "note", "watch"),
        "news_sentiment_flag": lambda v: v in ("strongly_negative", "negative"),
        "rs_flag":             lambda v: v == "lagging_sector",
        "options_flag":        lambda v: v in ("elevated_put_volume", "slightly_elevated_puts"),
    }

    stock_ids = list({r.stock_id for r in rows})
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= since,
            Price.ts <= date.today(),
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()

    from collections import defaultdict
    prices_by_stock: dict[int, list[tuple]] = defaultdict(list)
    for p in price_rows:
        # Convert datetime → date so _nearest_price can compare against date objects
        _d = p.ts.date() if hasattr(p.ts, "date") else p.ts
        prices_by_stock[p.stock_id].append((_d, float(p.close)))

    def _nearest_price(stock_id: int, target: date) -> float | None:
        candidates = prices_by_stock.get(stock_id, [])
        future = [(abs((d - target).days), c) for d, c in candidates if d >= target]
        return min(future, key=lambda x: x[0])[1] if future else None

    from collections import defaultdict as _dd
    buckets: dict[int, list[float]] = _dd(list)
    per_trade = []

    for row in rows:
        r = row.reasons or {}
        count = sum(1 for k in SUPPRESSION_BOOLEAN if r.get(k))
        count += sum(1 for k, test in SUPPRESSION_NAMED.items() if test(r.get(k)))

        # T237-SIG5: `isinstance(row.ts, date)` is always True for a datetime value too —
        # datetime.datetime is a subclass of datetime.date in Python — so the `else
        # row.ts.date()` branch was dead code and signal_date was always the raw datetime,
        # not a date. This crashed filter_audit() on every real call with "TypeError: can't
        # compare datetime.datetime to datetime.date" inside _nearest_price's `d >= target`
        # comparison (candidates are real date objects; the endpoint was broken outright, not
        # just biased). Check the more specific `datetime` type instead, matching the correct
        # pattern already used a few lines above for prices_by_stock.
        signal_date = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        # T237-SIG4: T+1 entry — use the first close STRICTLY AFTER signal_date, matching the
        # same fix already applied to evaluate_signal_outcomes/calibrate_ta_weights this
        # session. Was signal_date (on-or-after), the same same-day lookahead bias that let a
        # filter-audit "entry price" be the very close the signal was itself generated from.
        exit_date   = signal_date + timedelta(days=1 + hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date + timedelta(days=1))
        exit_price  = _nearest_price(row.stock_id, exit_date)

        if entry_price and exit_price and entry_price > 0:
            ret = (exit_price - entry_price) / entry_price
            buckets[count].append(ret)
            per_trade.append({
                "symbol":       row.symbol,
                "signal_date":  signal_date.isoformat(),
                "filter_count": count,
                "return_pct":   round(ret * 100, 2),
                "win":          ret > 0,
            })

    summary = []
    for fc in sorted(buckets):
        rets = buckets[fc]
        wins = sum(1 for r in rets if r > 0)
        summary.append({
            "filter_count":     fc,
            "trade_count":      len(rets),
            "win_rate_pct":     round(wins / len(rets) * 100, 1) if rets else None,
            "avg_return_pct":   round(sum(rets) / len(rets) * 100, 2) if rets else None,
            "median_return_pct": round(float(sorted(rets)[len(rets) // 2]) * 100, 2) if rets else None,
        })

    # Per-filter win rate: for each flag compare win rate when active vs inactive.
    # edge_pct negative = filter correctly suppresses weaker signals (good).
    # edge_pct positive = filter incorrectly suppresses stronger signals (harmful).
    all_filter_names = list(SUPPRESSION_BOOLEAN) + list(SUPPRESSION_NAMED.keys())
    filter_buckets: dict[str, dict[str, list[float]]] = {f: {"active": [], "inactive": []} for f in all_filter_names}

    for row in rows:
        r = row.reasons or {}
        filter_flags: dict[str, bool] = {}
        for k in SUPPRESSION_BOOLEAN:
            filter_flags[k] = bool(r.get(k))
        for k, test in SUPPRESSION_NAMED.items():
            filter_flags[k] = test(r.get(k))

        # T237-SIG5: `isinstance(row.ts, date)` is always True for a datetime value too —
        # datetime.datetime is a subclass of datetime.date in Python — so the `else
        # row.ts.date()` branch was dead code and signal_date was always the raw datetime,
        # not a date. This crashed filter_audit() on every real call with "TypeError: can't
        # compare datetime.datetime to datetime.date" inside _nearest_price's `d >= target`
        # comparison (candidates are real date objects; the endpoint was broken outright, not
        # just biased). Check the more specific `datetime` type instead, matching the correct
        # pattern already used a few lines above for prices_by_stock.
        signal_date = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        # T237-SIG4: same T+1 entry fix as the filter_count loop above.
        exit_date   = signal_date + timedelta(days=1 + hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date + timedelta(days=1))
        exit_price  = _nearest_price(row.stock_id, exit_date)
        if not (entry_price and exit_price and entry_price > 0):
            continue
        ret = (exit_price - entry_price) / entry_price
        for fname, is_active in filter_flags.items():
            bucket = "active" if is_active else "inactive"
            filter_buckets[fname][bucket].append(ret)

    by_filter = []
    for fname in all_filter_names:
        act = filter_buckets[fname]["active"]
        inact = filter_buckets[fname]["inactive"]
        act_wr   = round(sum(1 for r in act   if r > 0) / len(act)   * 100, 1) if act   else None
        inact_wr = round(sum(1 for r in inact if r > 0) / len(inact) * 100, 1) if inact else None
        act_avg   = round(sum(act)   / len(act)   * 100, 2) if act   else None
        inact_avg = round(sum(inact) / len(inact) * 100, 2) if inact else None
        edge = round((act_wr or 0) - (inact_wr or 0), 1)  # negative = filter correctly suppresses bad trades
        by_filter.append({
            "filter":           fname,
            "n_active":         len(act),
            "n_inactive":       len(inact),
            "win_rate_active":  act_wr,
            "win_rate_inactive": inact_wr,
            "avg_return_active":  act_avg,
            "avg_return_inactive": inact_avg,
            "edge_pct": edge,  # negative means filter correctly blocks worse signals; positive means filter is harmful
            "verdict": "harmful" if edge > 5 else ("weak" if edge > -3 else "predictive"),
        })
    by_filter.sort(key=lambda x: x["edge_pct"])  # most predictive (most negative) first

    n_signals = len(rows)
    n_with_returns = len(per_trade)
    overall_wr = round(sum(1 for t in per_trade if t["win"]) / n_with_returns * 100, 1) if n_with_returns else None
    result = {
        "lookback_days":          lookback_days,
        "style":                  style,
        "hold_days":              hold_days,
        "n_buy_signals_found":    n_signals,
        "n_with_return_data":     n_with_returns,
        "overall_win_rate_pct":   overall_wr,
        "note": "n_with_return_data < n_buy_signals_found when exit date is in the future or price data is missing.",
        "by_filter_count":        summary,
        "by_filter_name":         by_filter,
        "trades":                 per_trade,
    }
    _cache_set(cache_key, result)
    return result


@router.get("/walkforward")
def walkforward_backtest(
    train_days: int = Query(180, ge=30, le=365),
    test_days: int = Query(30, ge=7, le=90),
    lookback_days: int = Query(365, ge=60, le=730),
    hold_days: int = Query(5, ge=1, le=30),
    session: Session = Depends(get_session),
):
    """Walk-forward out-of-sample backtest using persisted signals.

    Divides the lookback period into non-overlapping test windows of test_days each.
    Signals generated during each window are evaluated against prices hold_days
    later — strictly after the signal date, with no look-ahead. Each window
    corresponds to a period where the model was trained on earlier data and tested
    on genuinely unseen future bars.

    Returns per-window accuracy, equity curve, Sharpe, max drawdown, and an optional
    SPY benchmark curve for comparison.
    """
    import bisect
    import math

    import httpx
    import numpy as np

    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=hold_days + 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    rows = session.execute(
        select(Signal, Stock.symbol, Stock.market)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.ts >= cutoff,
            Signal.ts <= outcome_cutoff,
            Signal.signal == SignalType.BUY,
            Stock.active.is_(True),
        )
        .order_by(Signal.ts.asc())
    ).all()

    if not rows:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    stock_ids = list({sig.stock_id for sig, _, _ in rows})
    price_since = (cutoff - timedelta(days=10)).date()

    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= price_since,
        )
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

    def entry_exit(sid: int, sig_date):
        ts_list = _pts.get(sid, [])
        if not ts_list:
            return None, None
        entry_idx = bisect.bisect_right(ts_list, sig_date)
        if entry_idx >= len(ts_list):
            return None, None
        entry_p = _pclose[sid][entry_idx]
        exit_idx = entry_idx + hold_days
        exit_p = _pclose[sid][exit_idx] if exit_idx < len(ts_list) else _pclose[sid][-1]
        if ts_list[-1] <= sig_date:
            return entry_p, None
        return entry_p, exit_p

    seen: set[tuple] = set()
    evaluated: list[tuple] = []  # (sig_date, return_pct)
    market_counts: dict[str, int] = {}

    for sig, sym, market in rows:
        sig_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, sig_date, sig.horizon)
        if key in seen:
            continue
        seen.add(key)
        market_counts[market] = market_counts.get(market, 0) + 1

        entry_p, exit_p = entry_exit(sig.stock_id, sig_date)
        if entry_p is None or exit_p is None or entry_p <= 0:
            continue

        ret_pct = (exit_p - entry_p) / entry_p * 100
        evaluated.append((sig_date, ret_pct))

    if not evaluated:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    # Divide into non-overlapping test windows
    all_dates = [d for d, _ in evaluated]
    window_start = min(all_dates)
    window_end_limit = max(all_dates)

    windows = []
    while window_start <= window_end_limit:
        wend = window_start + timedelta(days=test_days - 1)
        wsigs = [(d, r) for d, r in evaluated if window_start <= d <= wend]
        if len(wsigs) >= 3:
            n = len(wsigs)
            n_correct = sum(1 for _, r in wsigs if r > 0)
            avg_ret = sum(r for _, r in wsigs) / n
            windows.append({
                "start": window_start.isoformat(),
                "end": wend.isoformat(),
                "n_signals": n,
                "n_correct": n_correct,
                "accuracy": round(n_correct / n * 100, 1),
                "avg_return_pct": round(avg_ret, 2),
            })
        window_start = wend + timedelta(days=1)

    if not windows:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    # Equity curve — compound per-window average returns
    equity = 1.0
    for w in windows:
        equity *= (1 + w["avg_return_pct"] / 100)
        w["equity"] = round(equity, 4)

    # Sharpe (annualised from per-window returns)
    rets = np.array([w["avg_return_pct"] for w in windows])
    periods_per_year = 252 / test_days
    sharpe = float(rets.mean() / rets.std() * math.sqrt(periods_per_year)) if rets.std() > 0 else 0.0

    # Max drawdown
    eq_arr = np.array([w["equity"] for w in windows])
    peak = np.maximum.accumulate(eq_arr)
    max_dd = float(abs(((eq_arr - peak) / peak).min())) if len(eq_arr) > 1 else 0.0

    overall_n = sum(w["n_signals"] for w in windows)
    overall_correct = sum(w["n_correct"] for w in windows)
    total_return_pct = round((equity - 1) * 100, 2)
    profitable_windows = sum(1 for w in windows if w["avg_return_pct"] > 0)

    # Benchmark: prefer SPY for US-majority portfolios, else ^HSI
    hk_majority = market_counts.get("HK", 0) > market_counts.get("US", 0)
    bench_sym = "^HSI" if hk_majority else "SPY"
    benchmark = _wf_benchmark(bench_sym, cutoff.date(), windows)

    return {
        "train_days": train_days,
        "test_days": test_days,
        "lookback_days": lookback_days,
        "hold_days": hold_days,
        "windows": windows,
        "total_windows": len(windows),
        "profitable_windows": profitable_windows,
        "signal_count": overall_n,
        "overall_accuracy": round(overall_correct / overall_n * 100, 1) if overall_n else None,
        "avg_return_pct": round(float(rets.mean()), 2),
        "total_return_pct": total_return_pct,
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "benchmark": benchmark,
    }


def _wf_empty(train_days, test_days, lookback_days, hold_days):
    return {
        "train_days": train_days, "test_days": test_days,
        "lookback_days": lookback_days, "hold_days": hold_days,
        "windows": [], "total_windows": 0, "profitable_windows": 0,
        "signal_count": 0, "overall_accuracy": None, "avg_return_pct": None,
        "total_return_pct": None, "sharpe": None, "max_drawdown": None,
        "benchmark": None,
    }


def _wf_benchmark(symbol: str, start: date, windows: list[dict]) -> dict | None:
    import httpx
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/prices"
        with httpx.Client(timeout=10) as c:
            r = c.get(url, params={"timeframe": "1d", "start": start.isoformat(), "limit": 1000},
                      headers={"Authorization": f"Bearer {_service_token()}"})
            if r.status_code != 200:
                return None
        data = r.json()
        if not data:
            return None
        prices_by_date = {row["ts"][:10]: float(row["close"]) for row in data}
        sorted_dates = sorted(prices_by_date)

        start_price = None
        for d in sorted_dates:
            if d >= start.isoformat():
                start_price = prices_by_date[d]
                break
        if start_price is None or start_price <= 0:
            return None

        bench_windows = []
        for w in windows:
            wend = w["end"]
            end_price = None
            for d in sorted_dates:
                if d <= wend:
                    end_price = prices_by_date[d]
            if end_price is not None:
                bench_windows.append({
                    "end": wend,
                    "equity": round(end_price / start_price, 4),
                    "cumulative_return_pct": round((end_price / start_price - 1) * 100, 2),
                })

        if not bench_windows:
            return None
        return {
            "symbol": symbol,
            "windows": bench_windows,
            "total_return_pct": bench_windows[-1]["cumulative_return_pct"],
        }
    except Exception:
        return None


@router.get("/outcomes/summary")
def outcomes_summary(
    horizon: str | None = Query(None, description="SHORT | SWING | LONG"),
    days: int = Query(90, description="Look-back window in calendar days"),
    market: str | None = Query(None, description="US | HK — filter by stock market"),
    symbol: str | None = Query(None, description="Filter to a single symbol (e.g. AAPL)"),
    session: Session = Depends(get_session),
):
    """Return win-rate and return stats from the signal_outcomes table.

    Groups results by confidence band (0-40, 40-55, 55-70, 70-85, 85+) so you
    can verify that higher-confidence signals actually win more often.
    """
    import statistics

    cutoff = date.today() - timedelta(days=days)

    q = select(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.is_correct.is_not(None),
    )
    if horizon:
        try:
            q = q.where(SignalOutcome.horizon == SignalHorizon(horizon.upper()))
        except ValueError:
            raise HTTPException(400, f"Unknown horizon: {horizon}")
    _needs_stock_join = market or symbol
    if _needs_stock_join:
        q = q.join(Stock, Stock.id == SignalOutcome.stock_id)
        if market:
            q = q.where(Stock.market == market.upper())
        if symbol:
            q = q.where(Stock.symbol == symbol.upper())

    outcomes = session.execute(q).scalars().all()

    # T232-OC6: count censored outcomes (hold window closed, price permanently missing —
    # delisting/halt) in the same window/filters, so win rates can be reported alongside
    # the fraction of outcomes that were excluded rather than silently vanishing.
    censored_q = select(func.count()).select_from(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.skip_reason.is_not(None),
    )
    if horizon:
        censored_q = censored_q.where(SignalOutcome.horizon == SignalHorizon(horizon.upper()))
    if _needs_stock_join:
        censored_q = censored_q.join(Stock, Stock.id == SignalOutcome.stock_id)
        if market:
            censored_q = censored_q.where(Stock.market == market.upper())
        if symbol:
            censored_q = censored_q.where(Stock.symbol == symbol.upper())
    censored_count = session.execute(censored_q).scalar_one()

    if not outcomes:
        return {"total": 0, "censored": censored_count, "message": "No evaluated outcomes yet in this window"}

    # Overall stats
    wins = [o for o in outcomes if o.is_correct]
    returns = [o.pct_return for o in outcomes if o.pct_return is not None]

    # By confidence band
    bands = [
        (0, 40, "0-40"),
        (40, 55, "40-55"),
        (55, 70, "55-70"),
        (70, 85, "70-85"),
        (85, 101, "85+"),
    ]
    band_stats = []
    for lo, hi, label in bands:
        bucket = [o for o in outcomes if lo <= o.confidence < hi]
        if not bucket:
            continue
        bucket_wins = sum(1 for o in bucket if o.is_correct)
        bucket_returns = [o.pct_return for o in bucket if o.pct_return is not None]
        band_stats.append({
            "band": label,
            "count": len(bucket),
            "win_rate": round(bucket_wins / len(bucket), 3),
            "avg_return_pct": round(statistics.mean(bucket_returns) * 100, 2) if bucket_returns else None,
        })

    # By horizon (if not filtered)
    horizon_stats = {}
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        hbucket = [o for o in outcomes if o.horizon.value == h]
        if not hbucket:
            continue
        hreturns = [o.pct_return for o in hbucket if o.pct_return is not None]
        horizon_stats[h] = {
            "count": len(hbucket),
            "win_rate": round(sum(1 for o in hbucket if o.is_correct) / len(hbucket), 3),
            "avg_return_pct": round(statistics.mean(hreturns) * 100, 2) if hreturns else None,
        }

    # By market regime
    regime_stats = {}
    for o in outcomes:
        reg = o.market_regime or "unknown"
        if reg not in regime_stats:
            regime_stats[reg] = {"count": 0, "wins": 0, "returns": []}
        regime_stats[reg]["count"] += 1
        if o.is_correct:
            regime_stats[reg]["wins"] += 1
        if o.pct_return is not None:
            regime_stats[reg]["returns"].append(o.pct_return)
    regime_summary = {
        reg: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3),
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for reg, v in regime_stats.items()
    }

    # INT-8: Research alignment breakdown — how does research agreement affect win rate?
    _ALIGNED_RECS   = {"BUY", "STRONG BUY", "STRONG_BUY"}
    _PARTIAL_RECS   = {"WATCH"}
    _DIVERGENT_RECS = {"AVOID", "SELL"}
    research_groups: dict[str, dict] = {
        "aligned": {"count": 0, "wins": 0, "returns": []},
        "partial":  {"count": 0, "wins": 0, "returns": []},
        "divergent": {"count": 0, "wins": 0, "returns": []},
        "no_research": {"count": 0, "wins": 0, "returns": []},
    }
    for o in outcomes:
        rec = (o.research_rec or "").upper().strip()
        if rec in _ALIGNED_RECS:
            grp = "aligned"
        elif rec in _PARTIAL_RECS:
            grp = "partial"
        elif rec in _DIVERGENT_RECS:
            grp = "divergent"
        else:
            grp = "no_research"
        research_groups[grp]["count"] += 1
        if o.is_correct:
            research_groups[grp]["wins"] += 1
        if o.pct_return is not None:
            research_groups[grp]["returns"].append(o.pct_return)

    research_summary = {
        grp: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3) if v["count"] else None,
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for grp, v in research_groups.items()
        if v["count"] > 0
    }

    # Multi-window win rates (INT-8)
    def _window_stats(outcomes, attr_correct, attr_return):
        vals = [(getattr(o, attr_correct), getattr(o, attr_return)) for o in outcomes
                if getattr(o, attr_correct) is not None]
        if not vals:
            return None
        wr = sum(1 for c, _ in vals if c) / len(vals)
        rets = [r for _, r in vals if r is not None]
        return {
            "count": len(vals),
            "win_rate": round(wr, 3),
            "avg_return_pct": round(statistics.mean(rets) * 100, 2) if rets else None,
        }

    multi_window = {
        "5d":  _window_stats(outcomes, "is_correct_5d",  "return_5d"),
        "10d": _window_stats(outcomes, "is_correct_10d", "return_10d"),
        "20d": _window_stats(outcomes, "is_correct_20d", "return_20d"),
    }

    # BUY vs SELL win rate by horizon — reveals directional bias in signal accuracy.
    # AUD232-050: this is a raw diagnostic breakdown (any n, no market split) — a different
    # purpose than _build_confidence_calibration's gated calibrated_win_rate shown on live
    # signal cards (n>=_CONF_CAL_MIN_COUNT, market-first). The two can legitimately report
    # different numbers for the same nominal horizon+direction slice; `reliable` flags when
    # this bucket's n would NOT clear the calibration gate, so a consumer doesn't mistake a
    # tiny-n diagnostic number for the same reliability as the gated one.
    direction_stats: dict = {}
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        for direction in ("BUY", "SELL"):
            bucket = [o for o in outcomes if o.horizon.value == h and o.signal_direction == direction]
            if not bucket:
                continue
            bucket_returns = [o.pct_return for o in bucket if o.pct_return is not None]
            direction_stats[f"{h}/{direction}"] = {
                "count": len(bucket),
                "win_rate": round(sum(1 for o in bucket if o.is_correct) / len(bucket), 3),
                "avg_return_pct": round(statistics.mean(bucket_returns) * 100, 2) if bucket_returns else None,
                "reliable": len(bucket) >= _CONF_CAL_MIN_COUNT,
            }

    # By market (US vs HK) — T223-SIGNAL-WINRATE-API: surfaces cross-market win rate difference
    market_ids = list({o.stock_id for o in outcomes})
    _market_map: dict[int, str] = {}
    if market_ids:
        _mkt_rows = session.execute(
            select(Stock.id, Stock.market).where(Stock.id.in_(market_ids))
        ).all()
        _market_map = {r.id: r.market for r in _mkt_rows}

    market_stats: dict[str, dict] = {}
    for o in outcomes:
        mkt = _market_map.get(o.stock_id, "US")
        if mkt not in market_stats:
            market_stats[mkt] = {"count": 0, "wins": 0, "returns": []}
        market_stats[mkt]["count"] += 1
        if o.is_correct:
            market_stats[mkt]["wins"] += 1
        if o.pct_return is not None:
            market_stats[mkt]["returns"].append(o.pct_return)
    by_market = {
        mkt: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3),
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for mkt, v in market_stats.items()
    }

    signal_dates = [o.signal_date for o in outcomes if o.signal_date is not None]
    date_range = {
        "oldest": min(signal_dates).isoformat() if signal_dates else None,
        "newest": max(signal_dates).isoformat() if signal_dates else None,
    }

    # Per-symbol breakdown — fetch symbol names in one query
    stock_ids = list({o.stock_id for o in outcomes})
    symbol_map: dict[int, str] = {}
    if stock_ids:
        rows = session.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids))).all()
        symbol_map = {r.id: r.symbol for r in rows}

    sym_groups: dict[str, dict] = {}
    for o in outcomes:
        sym = symbol_map.get(o.stock_id, f"id:{o.stock_id}")
        if sym not in sym_groups:
            sym_groups[sym] = {"count": 0, "wins": 0, "returns": []}
        sym_groups[sym]["count"] += 1
        if o.is_correct:
            sym_groups[sym]["wins"] += 1
        if o.pct_return is not None:
            sym_groups[sym]["returns"].append(o.pct_return)

    by_symbol = sorted(
        [
            {
                "symbol": sym,
                "count": v["count"],
                "win_rate": round(v["wins"] / v["count"], 3),
                "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
                "wins": v["wins"],
                "losses": v["count"] - v["wins"],
            }
            for sym, v in sym_groups.items()
            if v["count"] >= 2
        ],
        key=lambda x: -(x["avg_return_pct"] or -999),
    )

    return {
        "total": len(outcomes),
        "censored": censored_count,
        "days_lookback": days,
        "date_range": date_range,
        "overall": {
            "win_rate": round(len(wins) / len(outcomes), 3),
            "avg_return_pct": round(statistics.mean(returns) * 100, 2) if returns else None,
            "median_return_pct": round(statistics.median(returns) * 100, 2) if returns else None,
        },
        "by_confidence_band": band_stats,
        "by_horizon": horizon_stats,
        "by_market": by_market,
        "by_direction": direction_stats,
        "by_market_regime": regime_summary,
        "by_research_alignment": research_summary,
        "by_window": multi_window,
        "by_symbol": by_symbol,
    }


_DECAY_DAYS = [1, 2, 3, 5, 7, 10, 15, 20, 30]


@router.get("/alpha_decay")
def alpha_decay(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=30, le=730),
    regime: str | None = Query(None),
    session: Session = Depends(get_session),
):
    """TM-2: Average cumulative return after BUY signals at each day offset.

    Uses signal_outcomes joined to daily prices to compute returns at 1, 2, 3,
    5, 7, 10, 15, 20, and 30 calendar days after the entry date.  Returns p25/
    p75 bands and the empirically optimal hold day (peak average return).
    """
    from bisect import bisect_left
    from collections import defaultdict

    cutoff = date.today() - timedelta(days=lookback_days)

    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    q = select(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.signal_direction == "BUY",
        SignalOutcome.horizon == hz,
        SignalOutcome.entry_price.is_not(None),
        SignalOutcome.entry_date.is_not(None),
    )
    if regime:
        q = q.where(SignalOutcome.market_regime == regime)

    outcomes = session.execute(q).scalars().all()

    if not outcomes:
        return {
            "horizon": horizon.upper(), "signal_count": 0,
            "lookback_days": lookback_days,
            "optimal_hold_days": None, "optimal_return_pct": None,
            "curve": [],
        }

    stock_ids = {o.stock_id for o in outcomes}
    min_entry = min(o.entry_date for o in outcomes)
    max_entry = max(o.entry_date for o in outcomes)
    price_end = max_entry + timedelta(days=37)

    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close).where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= datetime.combine(min_entry, datetime.min.time()),
            Price.ts <= datetime.combine(price_end, datetime.max.time()),
        ).order_by(Price.stock_id, Price.ts)
    ).all()

    price_map: dict[int, list] = defaultdict(list)
    for stock_id, ts, close in price_rows:
        price_map[stock_id].append((ts.date(), close))

    def price_on_or_after(stock_id: int, target: date) -> float | None:
        bars = price_map.get(stock_id)
        if not bars:
            return None
        dates = [b[0] for b in bars]
        idx = bisect_left(dates, target)
        for i in range(idx, min(idx + 6, len(bars))):
            if (bars[i][0] - target).days <= 5:
                return bars[i][1]
        return None

    day_returns: dict[int, list] = {d: [] for d in _DECAY_DAYS}
    for o in outcomes:
        for td in _DECAY_DAYS:
            p = price_on_or_after(o.stock_id, o.entry_date + timedelta(days=td))
            if p and o.entry_price and o.entry_price > 0:
                day_returns[td].append((p / o.entry_price - 1) * 100)

    curve = []
    for d in _DECAY_DAYS:
        rets = sorted(day_returns[d])
        n = len(rets)
        if n == 0:
            curve.append({"day": d, "avg_return_pct": None, "p25": None, "p75": None, "n": 0})
            continue
        avg = sum(rets) / n
        curve.append({
            "day": d,
            "avg_return_pct": round(avg, 2),
            "p25": round(rets[max(0, int(n * 0.25) - 1)], 2),
            "p75": round(rets[min(n - 1, int(n * 0.75))], 2),
            "n": n,
        })

    best = max((c for c in curve if c["avg_return_pct"] is not None),
               key=lambda c: c["avg_return_pct"], default=None)

    return {
        "horizon": horizon.upper(),
        "signal_count": len(outcomes),
        "lookback_days": lookback_days,
        "optimal_hold_days": best["day"] if best else None,
        "optimal_return_pct": best["avg_return_pct"] if best else None,
        "curve": curve,
    }


@router.get("/information_coefficient")
def information_coefficient(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=90, le=730),
    session: Session = Depends(get_session),
):
    """TM-3: Monthly IC — Spearman rank correlation between fused_prob rank and
    actual return rank.  IC > 0.05 is good; IC_IR (mean/std) > 0.5 is excellent.
    """
    import statistics

    cutoff = date.today() - timedelta(days=lookback_days)
    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.horizon == hz,
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.fused_prob.is_not(None),
            SignalOutcome.pct_return.is_not(None),
        )
    ).scalars().all()

    from collections import defaultdict
    monthly: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for o in outcomes:
        monthly[o.signal_date.strftime("%Y-%m")].append(
            (float(o.fused_prob), float(o.pct_return))
        )

    def _rank(lst: list[float]) -> list[float]:
        order = sorted(range(len(lst)), key=lambda i: lst[i])
        ranks = [0.0] * len(lst)
        for r, i in enumerate(order):
            ranks[i] = float(r + 1)
        return ranks

    series = []
    for month in sorted(monthly):
        pairs = monthly[month]
        if len(pairs) < 5:
            continue
        probs = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        rp = _rank(probs)
        rr = _rank(rets)
        n = len(rp)
        mp, mr = sum(rp) / n, sum(rr) / n
        cov = sum((a - mp) * (b - mr) for a, b in zip(rp, rr)) / n
        sp = (sum((a - mp) ** 2 for a in rp) / n) ** 0.5
        sr = (sum((b - mr) ** 2 for b in rr) / n) ** 0.5
        ic = cov / (sp * sr) if sp > 0 and sr > 0 else 0.0
        series.append({"month": month, "ic": round(ic, 4), "n": n})

    if not series:
        return {
            "horizon": horizon, "lookback_days": lookback_days,
            "monthly_ic": [], "ic_mean": None, "ic_std": None,
            "ic_ir": None, "total_periods": 0,
            "message": "Not enough data — at least 5 BUY outcomes per month required",
        }

    ics = [s["ic"] for s in series]
    ic_mean = statistics.mean(ics)
    ic_std = statistics.stdev(ics) if len(ics) > 1 else 0.0
    ic_ir = round(ic_mean / ic_std, 3) if ic_std > 0 else None

    return {
        "horizon": horizon,
        "lookback_days": lookback_days,
        "monthly_ic": series,
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "ic_ir": ic_ir,
        "total_periods": len(series),
        "quality": "excellent" if ic_mean > 0.05 else "good" if ic_mean > 0.02 else "poor",
    }


@router.get("/factor_attribution")
def factor_attribution(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=90, le=730),
    min_count: int = Query(10),
    session: Session = Depends(get_session),
):
    """TM-4: For each boolean reason flag, compute presence in winners vs losers.
    Edge = win_pct - los_pct.  Positive edge = factor predicts wins; negative = noise.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    rows = session.execute(
        select(SignalOutcome.is_correct, Signal.reasons)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.horizon == hz,
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            Signal.reasons.is_not(None),
        )
    ).all()

    if not rows:
        return {
            "factors": [], "total_winners": 0, "total_losers": 0,
            "message": "No evaluated outcomes with reason data yet",
        }

    n_win = sum(1 for r in rows if r.is_correct)
    n_los = sum(1 for r in rows if not r.is_correct)
    key_wins: dict[str, int] = {}
    key_los: dict[str, int] = {}

    for r in rows:
        reasons = r.reasons or {}
        bucket = key_wins if r.is_correct else key_los
        for k, v in reasons.items():
            if isinstance(v, bool) and v:
                bucket[k] = bucket.get(k, 0) + 1

    all_keys = set(key_wins) | set(key_los)
    factors = []
    for k in all_keys:
        wc = key_wins.get(k, 0)
        lc = key_los.get(k, 0)
        if wc + lc < min_count:
            continue
        wp = wc / n_win if n_win > 0 else 0.0
        lp = lc / n_los if n_los > 0 else 0.0
        factors.append({
            "factor": k,
            "win_pct": round(wp * 100, 1),
            "los_pct": round(lp * 100, 1),
            "edge": round((wp - lp) * 100, 1),
            "win_count": wc,
            "los_count": lc,
        })

    factors.sort(key=lambda x: x["edge"], reverse=True)

    return {
        "horizon": horizon,
        "lookback_days": lookback_days,
        "total_winners": n_win,
        "total_losers": n_los,
        "factors": factors,
    }


@router.post("/outcomes/evaluate")
def evaluate_signal_outcomes(session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Evaluate closed signal outcomes and persist them to signal_outcomes.

    For each BUY/SELL signal whose hold window has expired:
    - Entry price = first D1 close on or after signal date
    - Exit price  = first D1 close on or after entry_date + hold_window_days
    - pct_return  = (exit - entry) / entry
    - is_correct  = price went up for BUY, down for SELL

    INT-8: Also fills multi-window columns (price_5d/10d/20d, return_5d/10d/20d,
    is_correct_5d/10d/20d) and research_rec/research_score at evaluation time.
    Phase 2 of the same run updates existing outcome rows where window columns
    are NULL but sufficient time has now passed.

    Safe to re-run — already-evaluated signals (by UNIQUE signal_id) are skipped.
    Called automatically by the scheduler post-close.
    """
    from datetime import time as _time
    import bisect
    from collections import defaultdict
    import httpx as _httpx
    from sqlalchemy import or_

    today = date.today()
    # T232-SIG10: consider both tables — SELL's shortest window (5d SHORT) is smaller than
    # BUY's shortest (7d SHORT), so the candidate-signal cutoff must use whichever is smaller
    # or SELL signals eligible under their own shorter window would be filtered out too early.
    min_hold = min(min(_OUTCOME_HOLD_DAYS.values()), min(_SELL_OUTCOME_HOLD_DAYS.values()))
    cutoff = today - timedelta(days=min_hold)

    # IDs already in signal_outcomes — skip re-evaluation by signal_id
    evaluated_ids: set[int] = set(session.execute(
        select(SignalOutcome.signal_id)
    ).scalars().all())

    # Also track (stock_id, horizon, signal_date) to prevent duplicates from
    # multiple same-day signal refreshes producing multiple outcome rows.
    evaluated_sighd: set[tuple] = set(
        session.execute(
            select(SignalOutcome.stock_id, SignalOutcome.horizon, SignalOutcome.signal_date)
        ).all()
    )

    # BUY and SELL signals old enough that at least SHORT window could be closed
    pending_signals = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Stock.id == Signal.stock_id)
        .where(
            Signal.signal.in_([SignalType.BUY, SignalType.SELL]),
            Signal.ts <= datetime.combine(cutoff, _time.max),
        )
        .order_by(Signal.ts)
    ).all()

    # Bulk-load D1 prices — always extend window to 20d for INT-8 multi-window
    pending_stock_ids = list({sig.stock_id for sig, _ in pending_signals})
    price_min_ts = min((sig.ts for sig, _ in pending_signals), default=datetime.now())
    price_max_ts = datetime.now() + timedelta(days=30)
    bulk_prices: list = []
    if pending_stock_ids:
        bulk_prices = session.execute(
            select(Price.stock_id, Price.ts, Price.close)
            .where(
                Price.stock_id.in_(pending_stock_ids),
                Price.timeframe == TimeFrame.D1,
                Price.ts >= price_min_ts,
                Price.ts <= price_max_ts,
            )
            .order_by(Price.stock_id, Price.ts)
        ).all()

    _outcome_price_map: dict[int, list[tuple]] = defaultdict(list)
    for pr in bulk_prices:
        pr_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
        _outcome_price_map[pr.stock_id].append((pr_date, float(pr.close)))

    def _lookup_outcome_price(stock_id: int, on_or_after: "date") -> "tuple | None":
        bucket = _outcome_price_map.get(stock_id, [])
        if not bucket:
            return None
        dates = [b[0] for b in bucket]
        idx = bisect.bisect_left(dates, on_or_after)
        if idx >= len(bucket):
            return None
        return bucket[idx]

    def _window_return(stock_id: int, entry_date: "date", entry_price: float, days: int, signal_direction: str = "BUY"):
        """Return (price, return_pct, is_correct) for a +N-day window, or (None, None, None).

        is_correct: BUY wins when ret clears the cost hurdle; SELL wins when ret falls
        below the negative hurdle (T232-OC4 — see _OUTCOME_WIN_HURDLE_PCT above).
        """
        target = entry_date + timedelta(days=days)
        if target > today:
            return None, None, None
        result = _lookup_outcome_price(stock_id, target)
        if result is None or entry_price <= 0:
            return None, None, None
        _, price = result
        ret = (price - entry_price) / entry_price
        is_correct = ret > _OUTCOME_WIN_HURDLE_PCT if signal_direction == "BUY" else ret < -_OUTCOME_WIN_HURDLE_PCT
        return float(price), ret, is_correct

    # Research recommendation cache — one network fetch per symbol per run
    _research_cache: dict[str, tuple] = {}

    def _fetch_research(symbol: str) -> "tuple[str | None, float | None]":
        if symbol in _research_cache:
            return _research_cache[symbol]
        try:
            _tok = _service_token()
            _r = _httpx.get(
                f"{_settings.research_engine_url}/research/{symbol}/summary",
                headers={"Authorization": f"Bearer {_tok}"},
                timeout=2.0,
            )
            if _r.status_code == 200:
                _d = _r.json()
                result = (_d.get("recommendation"), float(_d.get("overall_score") or 0) or None)
            else:
                # AUD232-019: previously swallowed silently into (None, None) — a slow or
                # erroring research-engine permanently blanked research_rec/research_score for
                # this outcome row unless Phase 2's NULL-column backfill happened to retry it
                # later, with no visible symptom until someone noticed a spike in the
                # "no_research" bucket count. Logging this makes a systemic slowdown visible.
                log.warning("outcomes.research_fetch_non200", symbol=symbol, status=_r.status_code)
                result = (None, None)
        except Exception as _rfe:
            log.warning("outcomes.research_fetch_failed", symbol=symbol, error=str(_rfe))
            result = (None, None)
        _research_cache[symbol] = result
        return result

    evaluated, skipped_open, skipped_no_price, censored, failed = 0, 0, 0, 0, 0
    # T243-DQ6: previously one bulk session.commit() at the very end of the whole loop, with
    # no per-signal try/except — a single IntegrityError anywhere (e.g. a duplicate signal_id
    # from an overlapping/retried request; _post() in scheduler.py retries up to 3x on any
    # timeout, including ReadTimeout from a slow run, and this endpoint has no lock against a
    # second overlapping call) silently discarded EVERY new SignalOutcome row accumulated by
    # that entire run, not just the one colliding row — a real, unexplained gap tracked as
    # TUNE-LONG-EVALUATE-BACKLOG matches this exact failure shape. Commit incrementally so a
    # failure only loses the batch since the last checkpoint, and wrap each signal's own work
    # in its own try/except so one bad row can't take down any other row in the same run.
    _COMMIT_EVERY = 25
    _since_commit = 0

    for sig, symbol in pending_signals:
        if sig.id in evaluated_ids:
            continue

        horizon = sig.horizon.value
        # T232-SIG10: SELL uses its own shorter hold window — see _SELL_OUTCOME_HOLD_DAYS above.
        hold_days = (
            _SELL_OUTCOME_HOLD_DAYS[horizon] if sig.signal == SignalType.SELL
            else _OUTCOME_HOLD_DAYS[horizon]
        )
        signal_date = sig.ts.date()

        # Skip if another signal_id for the same (stock, horizon, date) was already evaluated.
        # This prevents 5×/day refreshes from creating duplicate outcome rows for the same
        # logical signal event.
        sighd_key = (sig.stock_id, sig.horizon, signal_date)
        if sighd_key in evaluated_sighd:
            continue

        try:
            # T+1 entry: use the first close STRICTLY AFTER signal_date so we avoid
            # same-day look-ahead bias (signal was generated after close; realistic
            # fill is the next trading day's open/close).
            entry_result = _lookup_outcome_price(sig.stock_id, signal_date + timedelta(days=1))
            if entry_result is None:
                skipped_no_price += 1
                continue

            entry_date, entry_price = entry_result
            exit_target = entry_date + timedelta(days=hold_days)

            if exit_target > today:
                skipped_open += 1
                continue

            exit_result = _lookup_outcome_price(sig.stock_id, exit_target)
            if exit_result is None:
                # T232-OC6: exit_target has passed but no price bar exists on/after it. Give
                # ordinary ingestion lag a grace window (weekends/holidays plus a buffer) before
                # concluding the price is permanently gone — otherwise a stock that's merely a
                # few days behind on ingestion gets wrongly censored as delisted.
                if today - exit_target > timedelta(days=_OUTCOME_CENSOR_GRACE_DAYS):
                    outcome = SignalOutcome(
                        signal_id=sig.id,
                        stock_id=sig.stock_id,
                        symbol=symbol,
                        horizon=sig.horizon,
                        signal_direction=sig.signal.value,
                        signal_date=signal_date,
                        confidence=sig.confidence,
                        fused_prob=sig.bullish_probability,
                        ta_score=(sig.reasons or {}).get("ta_score"),
                        ml_prob=(sig.reasons or {}).get("ml_probability"),
                        ml_auc=(sig.reasons or {}).get("ml_test_auc"),
                        market_regime=(sig.reasons or {}).get("market_regime"),
                        entry_date=entry_date,
                        entry_price=entry_price,
                        skip_reason="no_exit_price",
                    )
                    # AUD250-SIGNALENGINE-ROLLBACK-EXPIRES-IDENTITY-MAP: flush inside a SAVEPOINT
                    # (begin_nested) rather than deferring to the periodic/end-of-loop commit —
                    # any IntegrityError (e.g. a duplicate signal_id from a raced overlapping
                    # request) now surfaces and rolls back immediately, on ONLY this row's
                    # savepoint, without expiring every other Signal object already loaded in
                    # pending_signals (a plain session.rollback() expires the WHOLE identity map
                    # by default, forcing a silent per-attribute re-SELECT on every later
                    # iteration's sig.xxx access — a real N+1 regression, not just this row).
                    with session.begin_nested():
                        session.add(outcome)
                        session.flush()
                    censored += 1
                    evaluated_ids.add(sig.id)
                    evaluated_sighd.add(sighd_key)
                    _since_commit += 1
                else:
                    skipped_open += 1
                    continue
            else:
                exit_date, exit_price = exit_result
                if entry_price <= 0:
                    skipped_no_price += 1
                    continue

                pct_return = (exit_price - entry_price) / entry_price
                hold_days_actual = (exit_date - entry_date).days
                # T232-OC4: require clearing a real cost hurdle, not just a bare zero line — see
                # _OUTCOME_WIN_HURDLE_PCT above for why 0.5% and what's deliberately NOT modeled here.
                is_correct = (
                    pct_return > _OUTCOME_WIN_HURDLE_PCT if sig.signal == SignalType.BUY
                    else pct_return < -_OUTCOME_WIN_HURDLE_PCT
                )

                # INT-8: multi-window forward returns (pass signal direction so SELL wins on negative returns)
                _sig_dir = sig.signal.value  # "BUY" or "SELL"
                p5, r5, c5   = _window_return(sig.stock_id, entry_date, entry_price, 5,  _sig_dir)
                p10, r10, c10 = _window_return(sig.stock_id, entry_date, entry_price, 10, _sig_dir)
                p20, r20, c20 = _window_return(sig.stock_id, entry_date, entry_price, 20, _sig_dir)
                res_rec, res_score = _fetch_research(symbol)

                reasons = sig.reasons or {}
                outcome = SignalOutcome(
                    signal_id=sig.id,
                    stock_id=sig.stock_id,
                    symbol=symbol,
                    horizon=sig.horizon,
                    signal_direction=sig.signal.value,
                    signal_date=signal_date,
                    confidence=sig.confidence,
                    fused_prob=sig.bullish_probability,
                    ta_score=reasons.get("ta_score"),
                    ml_prob=reasons.get("ml_probability"),
                    ml_auc=reasons.get("ml_test_auc"),
                    market_regime=reasons.get("market_regime"),
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=exit_date,
                    exit_price=exit_price,
                    hold_days=hold_days_actual,
                    pct_return=pct_return,
                    is_correct=is_correct,
                    price_5d=p5, return_5d=r5, is_correct_5d=c5,
                    price_10d=p10, return_10d=r10, is_correct_10d=c10,
                    price_20d=p20, return_20d=r20, is_correct_20d=c20,
                    research_rec=res_rec,
                    research_score=res_score,
                )
                # AUD250-SIGNALENGINE-ROLLBACK-EXPIRES-IDENTITY-MAP: see the censored branch
                # above for the full rationale — flush inside its own SAVEPOINT so a failure
                # here rolls back only this row, not the whole session's identity map.
                with session.begin_nested():
                    session.add(outcome)
                    session.flush()
                evaluated_ids.add(sig.id)
                evaluated_sighd.add(sighd_key)
                evaluated += 1
                _since_commit += 1

            if _since_commit >= _COMMIT_EVERY:
                session.commit()
                _since_commit = 0
        except Exception as _eval_exc:
            # A failure here (e.g. IntegrityError from a duplicate signal_id if a retried/
            # overlapping request raced this one) previously rolled back EVERY row accumulated
            # by the entire run's single end-of-loop commit, not just this one signal. Roll
            # back just the uncommitted work since the last checkpoint and move on — at most
            # this signal and up to _COMMIT_EVERY-1 already-processed-but-uncommitted signals
            # are affected, not the whole batch.
            session.rollback()
            failed += 1
            log.warning("outcomes.evaluate_signal_failed", signal_id=sig.id, symbol=symbol,
                        horizon=horizon, error=str(_eval_exc))

    session.commit()

    # ── Phase 2: Fill NULL window columns on existing outcome rows ─────────────
    # Outcomes created before INT-8 (or where a window wasn't closed at create time)
    # may have NULL price_5d/10d/20d. Fill them in as the windows mature.
    needs_update = session.execute(
        select(SignalOutcome)
        .where(
            SignalOutcome.entry_date.is_not(None),
            SignalOutcome.entry_price.is_not(None),
            # Include both BUY and SELL outcomes — SELL wins when return < 0
            or_(
                SignalOutcome.price_5d.is_(None),
                SignalOutcome.price_10d.is_(None),
                SignalOutcome.price_20d.is_(None),
            )
        )
        .limit(500)
    ).scalars().all()

    updated = 0
    if needs_update:
        # Extend price map with any stocks not already loaded
        missing_ids = [o.stock_id for o in needs_update if o.stock_id not in _outcome_price_map]
        if missing_ids:
            upd_prices = session.execute(
                select(Price.stock_id, Price.ts, Price.close)
                .where(
                    Price.stock_id.in_(missing_ids),
                    Price.timeframe == TimeFrame.D1,
                )
                .order_by(Price.stock_id, Price.ts)
            ).all()
            for pr in upd_prices:
                pr_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
                _outcome_price_map[pr.stock_id].append((pr_date, float(pr.close)))

        for out in needs_update:
            changed = False
            ep, ed = out.entry_price, out.entry_date
            _out_dir = out.signal_direction or "BUY"  # SELL wins on negative return
            if out.price_5d is None:
                p5, r5, c5 = _window_return(out.stock_id, ed, ep, 5, _out_dir)
                if p5 is not None:
                    out.price_5d, out.return_5d, out.is_correct_5d = p5, r5, c5
                    changed = True
            if out.price_10d is None:
                p10, r10, c10 = _window_return(out.stock_id, ed, ep, 10, _out_dir)
                if p10 is not None:
                    out.price_10d, out.return_10d, out.is_correct_10d = p10, r10, c10
                    changed = True
            if out.price_20d is None:
                p20, r20, c20 = _window_return(out.stock_id, ed, ep, 20, _out_dir)
                if p20 is not None:
                    out.price_20d, out.return_20d, out.is_correct_20d = p20, r20, c20
                    changed = True
            if out.research_rec is None:
                rr, rs = _fetch_research(out.symbol)
                if rr is not None:
                    out.research_rec, out.research_score = rr, rs
                    changed = True
            if changed:
                updated += 1

        session.commit()

    # AUD232-003: confidence-calibration's Redis cache (1h TTL) previously had no
    # invalidation tied to this endpoint actually writing new/updated rows — it would
    # rebuild every hour from whatever signal_outcomes data existed, self-consistently,
    # with no signal if THIS job silently stopped running (e.g. the jose-missing-library
    # failure pattern already seen multiple times in this repo). Explicitly invalidate
    # whenever real data changed so the next read rebuilds from fresh rows instead of
    # riding out the rest of the TTL on stale ones.
    if evaluated or updated:
        try:
            _get_redis().delete(_CONF_CAL_CACHE_KEY)
        except Exception:
            pass

    log.info(
        "outcomes.evaluate_done",
        evaluated=evaluated,
        skipped_open=skipped_open,
        skipped_no_price=skipped_no_price,
        censored=censored,
        updated_windows=updated,
    )
    return {
        "evaluated": evaluated,
        "skipped_open": skipped_open,
        "skipped_no_price": skipped_no_price,
        "censored": censored,
        "failed": failed,
        "updated_windows": updated,
    }


@router.get("/gate_backtest")
def gate_backtest(
    lookback_days: int = Query(90, ge=30, le=365),
    style: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    hold_days: int = Query(10, ge=1, le=60),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """HISTORICAL RETROSPECTIVE of the T234 conviction-gate migration — NOT a live proposal.

    AUD232-044: this endpoint's "new" parameterization (relaxed MACD OR-condition, MACD
    soft-fail, GROWTH RSI floor=50) is not a pending change to evaluate — it IS the current,
    already-shipped behavior of the real _is_conviction_buy() in
    services/market-data/src/services/scheduler.py (see that function's Layer 4b/4c and
    _SOFT_LAYER_KEYWORDS, confirmed to match this endpoint's "new" flags exactly as of
    2026-07-11). The "old" parameterization is the PRE-T234 gate, which no longer runs in
    production anywhere. Calling this a "new vs old" comparison (as if "new" were still under
    review) was misleading after T234 shipped — both arms were never re-synced against the
    real gate's current parameters, so if the real gate changes again in the future without a
    corresponding update here, this retrospective would silently stop representing either the
    real "before" or the real "after" state. Use this to see the win-rate lift T234 already
    delivered, not to decide whether to ship anything — there is nothing left to decide here.

    Replays _is_conviction_buy with pre-T234 and post-T234 parameters to measure how many
    more signals fired and whether the newly-unblocked signals actually performed well.

    Gate changes evaluated (all already live in production):
      1. MACD condition: pre-T234 = (hist > 0 AND rising) OR crossover
                         post-T234 = hist > 0 OR rising OR crossover
      2. MACD soft tier: pre-T234 = hard failure (blocks alone)
                        post-T234 = soft failure (1 allowed per near-conviction tier)
      3. GROWTH RSI lo:  pre-T234 = 55  →  post-T234 = 50

    Returns per-group win-rate and avg return for this retrospective comparison.
    """
    cache_key = f"signals:cache:gate_backtest:{lookback_days}:{style}:{hold_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    since = date.today() - timedelta(days=lookback_days)
    try:
        horizon_enum = SignalHorizon(style.upper())
    except ValueError:
        horizon_enum = SignalHorizon.SWING

    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id, Stock.symbol, Signal.horizon)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.signal == SignalType.BUY,
            Signal.horizon == horizon_enum,
            Signal.ts >= since,
            Signal.reasons.isnot(None),
        )
        .order_by(Signal.ts)
    ).all()

    # AUD232-066: this is a manually-synced replica of market-data/services/scheduler.py's
    # _REGIME_THRESHOLDS (the real conviction gate — cross-service, can't be imported directly).
    # "unknown" was previously missing here and relied on .get(regime, 0.70) coincidentally
    # matching "neutral"'s value — made explicit so a future change to either side's "unknown"/
    # "neutral" value doesn't silently desync this backtest replica from the real gate.
    _REGIME_ML_THRESH = {"bull": 0.65, "neutral": 0.70, "high_vol": 0.78, "bear": 0.78, "unknown": 0.70}

    def _apply_gate(r: dict, horizon: str, new_macd_cond: bool, new_macd_soft: bool, new_growth_rsi: bool):
        """Inline replay of _is_conviction_buy. Returns (passes, tier, list[failed_keys])."""
        failed: list[str] = []

        # K-Score — may not be stored in reasons (fetched by scheduler); treat None as soft-pass
        kscore = r.get("kscore")
        if kscore is not None and float(kscore) < 55:
            failed.append("KScore")

        # 4a — Uptrend structure
        if horizon == "GROWTH":
            if not r.get("trend_above_sma50"):
                failed.append("Uptrend")
        else:
            if not (r.get("sma50_above_sma200") and r.get("trend_above_sma50")):
                failed.append("Uptrend")

        # 4b — RSI range
        rsi = r.get("rsi")
        if rsi is not None:
            rsi_f = float(rsi)
            if horizon == "GROWTH":
                lo = 50.0 if new_growth_rsi else 55.0
                rsi_ok = lo <= rsi_f <= 85.0
            else:
                rsi_ok = 45.0 <= rsi_f <= 72.0
            if not rsi_ok:
                failed.append("RSI")

        # 4c — MACD momentum
        macd_hist = float(r.get("macd_hist") or 0)
        macd_rising = bool(r.get("macd_rising"))
        macd_cross = bool(r.get("macd_zero_cross_up"))
        if new_macd_cond:
            macd_ok = macd_hist > 0 or macd_rising or macd_cross
        else:
            macd_ok = (macd_hist > 0 and macd_rising) or macd_cross
        if not macd_ok:
            failed.append("MACD")

        # 4d — OBV (always soft)
        if not r.get("obv_trend_bullish"):
            failed.append("OBV")

        # 4e — ADX (always soft)
        if not r.get("adx_trending"):
            failed.append("ADX")

        # 5 — ML probability (always soft)
        # T234-SIG-GATEBACKTEST-DRIFT: the real gate (_is_conviction_buy) soft-passes when
        # ml_weight == 0.0 (model trained but AUC < 0.50, so signal-engine assigned it zero
        # fusion weight — "ML had no say, don't penalize on it"). This replica was missing
        # that carve-out and always failed on a threshold miss regardless of ml_weight,
        # scoring some historically-soft-passed signals as ML-gate failures.
        ml_prob = r.get("ml_probability")
        ml_weight = float(r.get("ml_weight") or 0.0)
        if ml_prob is not None and ml_weight != 0.0:
            regime = r.get("market_regime", "unknown")
            thresh = _REGIME_ML_THRESH.get(regime, 0.70)
            if float(ml_prob) <= thresh:
                failed.append("ML")

        # Disqualifiers — always hard
        if r.get("rsi_divergence") == "bearish":
            failed.append("RSI_DIV")
        if r.get("stoch_rsi_overbought"):
            failed.append("STOCH_OB")

        soft_kw = {"OBV", "ADX", "ML"}
        if new_macd_soft:
            soft_kw.add("MACD")
        soft_failed = [f for f in failed if f in soft_kw]
        hard_failed = [f for f in failed if f not in soft_kw]

        if not failed:
            tier = "full"
        elif not hard_failed and len(soft_failed) == 1:
            tier = "near"
        else:
            tier = "failed"
        return tier in ("full", "near"), tier, failed

    # Build price lookup
    stock_ids = list({r.stock_id for r in rows})
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= since - timedelta(days=10),
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()

    from collections import defaultdict
    prices_by_stock: dict = defaultdict(list)
    for p in price_rows:
        d = p.ts.date() if hasattr(p.ts, "date") else p.ts
        prices_by_stock[p.stock_id].append((d, float(p.close)))

    def _price_at(stock_id: int, target) -> float | None:
        candidates = prices_by_stock.get(stock_id, [])
        future = [(abs((d - target).days), c) for d, c in candidates if d >= target]
        return min(future, key=lambda x: x[0])[1] if future else None

    # Evaluate each signal under old and new gates
    records = []
    for row in rows:
        r = row.reasons or {}
        sig_date = row.ts.date() if hasattr(row.ts, "date") else row.ts
        exit_date = sig_date + timedelta(days=hold_days)
        horizon = row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon)

        entry = _price_at(row.stock_id, sig_date)
        exit_ = _price_at(row.stock_id, exit_date)
        ret = ((exit_ - entry) / entry) if (entry and exit_ and entry > 0) else None

        old_pass, old_tier, old_failed = _apply_gate(r, horizon, new_macd_cond=False, new_macd_soft=False, new_growth_rsi=False)
        new_pass, new_tier, new_failed = _apply_gate(r, horizon, new_macd_cond=True,  new_macd_soft=True,  new_growth_rsi=True)

        # Attribute what change caused the unblock
        change_reasons: list[str] = []
        if not old_pass and new_pass:
            macd_hist = float(r.get("macd_hist") or 0)
            macd_rising = bool(r.get("macd_rising"))
            macd_cross = bool(r.get("macd_zero_cross_up"))
            old_macd_ok = (macd_hist > 0 and macd_rising) or macd_cross
            new_macd_ok = macd_hist > 0 or macd_rising or macd_cross
            if not old_macd_ok and new_macd_ok:
                change_reasons.append("macd_condition_relaxed")
            elif "MACD" in old_failed and "MACD" not in new_failed:
                change_reasons.append("macd_soft_reclassified")
            rsi = r.get("rsi")
            if horizon == "GROWTH" and rsi is not None and 50.0 <= float(rsi) < 55.0:
                change_reasons.append("growth_rsi_50_54")

        records.append({
            "symbol": row.symbol,
            "signal_date": sig_date.isoformat(),
            "old_pass": old_pass, "old_tier": old_tier, "old_failed": old_failed,
            "new_pass": new_pass, "new_tier": new_tier, "new_failed": new_failed,
            "ret_pct": round(ret * 100, 2) if ret is not None else None,
            "win": (ret > 0) if ret is not None else None,
            "change_reasons": change_reasons,
        })

    def _stats(items: list) -> dict:
        with_ret = [x for x in items if x["ret_pct"] is not None]
        wins = [x for x in with_ret if x["win"]]
        return {
            "count": len(items),
            "count_with_returns": len(with_ret),
            "win_rate_pct": round(len(wins) / len(with_ret) * 100, 1) if with_ret else None,
            "avg_return_pct": round(sum(x["ret_pct"] for x in with_ret) / len(with_ret), 2) if with_ret else None,
        }

    old_pass_set = [x for x in records if x["old_pass"]]
    new_pass_set = [x for x in records if x["new_pass"]]
    newly_pass   = [x for x in records if x["new_pass"] and not x["old_pass"]]
    always_fail  = [x for x in records if not x["new_pass"]]

    by_change = {}
    for reason in ("macd_condition_relaxed", "macd_soft_reclassified", "growth_rsi_50_54"):
        grp = [x for x in newly_pass if reason in x["change_reasons"]]
        by_change[reason] = _stats(grp)

    sample = sorted(
        [x for x in newly_pass if x["ret_pct"] is not None],
        key=lambda x: x["ret_pct"], reverse=True,
    )[:20]

    result = {
        "lookback_days": lookback_days,
        "horizon": style,
        "hold_days": hold_days,
        "n_signals_total": len(records),
        "old_gate": _stats(old_pass_set),
        "new_gate": _stats(new_pass_set),
        "newly_unblocked": {
            **_stats(newly_pass),
            "by_change": by_change,
            "note": "win_rate_pct > 50% means newly unblocked signals go up more often than not — change is beneficial",
        },
        "still_blocked": _stats(always_fail),
        "sample_newly_unblocked": sample,
    }
    _cache_set(cache_key, result, ttl=3600)
    return result


# T232-OC5: /{symbol} MUST be registered after every other static-path route in this router.
# FastAPI matches routes in registration order, and a bare /{symbol} catch-all placed earlier
# swallows any later static route with the same prefix depth (e.g. /signals/gate_backtest was
# being treated as symbol="gate_backtest" and 500ing on an invalid stock lookup — completely
# unreachable since it was added). Moved here, after every other GET, so this can never recur
# by accident; if you add a new static GET route to this router, add it ABOVE this line.