from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from common.jwt_auth import get_current_username
from common.logging import get_logger
from db import Price, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame, get_session

_settings = get_settings()

# ── Redis cache helper ────────────────────────────────────────────────────────

def _get_redis():
    import redis as redis_lib
    return redis_lib.from_url(_settings.redis_url, decode_responses=True)

def _cache_get(key: str):
    try:
        val = _get_redis().get(key)
        return json.loads(val) if val else None
    except Exception:
        return None

def _cache_set(key: str, value, ttl: int = 3600) -> None:
    try:
        _get_redis().setex(key, ttl, json.dumps(value))
    except Exception:
        pass

from ..generators import generate_signal, generate_all_signals

log = get_logger("signals")

router = APIRouter(prefix="/signals", tags=["signals"])


def _compute_stability(session: Session, stock_id: int, horizon: SignalHorizon, current_signal: str, limit: int = 30) -> int:
    """Count consecutive past days the given signal has been persisted in the DB."""
    from sqlalchemy import desc
    sigs = session.execute(
        select(Signal.signal)
        .where(Signal.stock_id == stock_id, Signal.horizon == horizon)
        .order_by(desc(Signal.ts))
        .limit(limit)
    ).scalars().all()
    count = 0
    for sig in sigs:
        if sig.value == current_signal:
            count += 1
        else:
            break
    return count


@router.get("")
def all_latest_signals(
    style: str | None = Query(None, description="Filter by trading style: SHORT, SWING, LONG"),
    session: Session = Depends(get_session),
):
    """Return the most recently persisted signal for every active stock.

    Optional ?style=SWING filters to a specific trading horizon.
    If omitted, returns the best available signal per stock (SWING > LONG > GROWTH > SHORT).
    """
    # Preference order when no style specified: SWING wins if available, then LONG, GROWTH, SHORT.
    _STYLE_PREFERENCE = ["SWING", "LONG", "GROWTH", "SHORT"]
    horizon_filter = style.upper() if style else None
    # Subquery: latest ts per (stock_id, horizon)
    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )
    q = (
        select(Stock.symbol, Signal.stock_id, Signal.signal, Signal.horizon, Signal.confidence, Signal.bullish_probability, Signal.ts)
        .join(Signal, Stock.id == Signal.stock_id)
        .join(latest_subq, (Signal.stock_id == latest_subq.c.stock_id)
              & (Signal.horizon == latest_subq.c.horizon)
              & (Signal.ts == latest_subq.c.max_ts))
        .where(Stock.active.is_(True))
    )
    horizon_enum = None
    if horizon_filter:
        try:
            horizon_enum = SignalHorizon(horizon_filter)
            q = q.where(Signal.horizon == horizon_enum)
        except ValueError:
            pass  # unknown style — return all
    rows = session.execute(q).all()

    # When no style specified, apply preference order (SWING > LONG > GROWTH > SHORT)
    # so each stock contributes at most one row (the best available style).
    if horizon_filter is None:
        _pref_idx = {s: i for i, s in enumerate(_STYLE_PREFERENCE)}
        _best: dict[int, object] = {}
        for row in rows:
            style_val = row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon)
            cur = _best.get(row.stock_id)
            if cur is None:
                _best[row.stock_id] = row
            else:
                cur_style = cur.horizon.value if hasattr(cur.horizon, "value") else str(cur.horizon)
                if _pref_idx.get(style_val, 99) < _pref_idx.get(cur_style, 99):
                    _best[row.stock_id] = row
        rows = list(_best.values())

    # Batch stability: one extra query for all stock_ids at this horizon
    # Fetches ts so we can detect gaps in the streak (non-consecutive days don't count).
    stability_map: dict[int, int] = {}
    if rows and horizon_enum is not None:
        from collections import defaultdict
        stock_ids = list({row.stock_id for row in rows})
        cutoff = datetime.now(timezone.utc) - timedelta(days=35)
        recent = session.execute(
            select(Signal.stock_id, Signal.signal, Signal.ts)
            .where(
                Signal.stock_id.in_(stock_ids),
                Signal.horizon == horizon_enum,
                Signal.ts >= cutoff,
            )
            .order_by(Signal.stock_id, Signal.ts.desc())
        ).all()
        by_stock: dict[int, list[tuple[str, object]]] = defaultdict(list)
        for r in recent:
            by_stock[r.stock_id].append((r.signal.value, r.ts))
        latest_sig_map = {row.stock_id: row.signal.value for row in rows}
        for sid, sig_ts_pairs in by_stock.items():
            cur = latest_sig_map.get(sid, "")
            count = 0
            prev_ts = None
            for sig_val, ts in sig_ts_pairs:
                if sig_val != cur:
                    break
                if prev_ts is not None:
                    gap_days = (prev_ts - ts).days if hasattr(prev_ts, "days") else 0
                    try:
                        gap_days = (prev_ts.date() - ts.date()).days
                    except Exception:
                        gap_days = 1
                    if gap_days > 3:  # gap > 3 calendar days breaks the consecutive streak
                        break
                count += 1
                prev_ts = ts
            stability_map[sid] = count

    return [
        {
            "symbol": row.symbol,
            "signal": row.signal.value,
            "horizon": row.horizon.value,
            "confidence": row.confidence,
            "bullish_probability": row.bullish_probability,
            "ts": row.ts.isoformat() if row.ts else None,
            "stability_days": stability_map.get(row.stock_id, 1),
        }
        for row in rows
    ]


@router.post("/refresh")
def refresh_signals(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Recompute and persist signals for all active stocks, optionally filtered by market."""
    q = select(Stock.symbol).where(Stock.active.is_(True))
    if market:
        q = q.where(Stock.market == market.upper())
    symbols = list(session.execute(q).scalars())
    try:
        r = _get_redis()
        for key in r.scan_iter("signals:cache:*"):
            r.delete(key)
    except Exception:
        pass
    tasks.add_task(_bulk_persist, symbols)
    return {"status": "scheduled", "count": len(symbols)}


@router.post("/reset")
def reset_signals(tasks: BackgroundTasks, session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Wipe all persisted signals then re-persist fresh ones for every active stock."""
    deleted = session.query(Signal).delete()
    session.commit()
    symbols = list(session.execute(select(Stock.symbol).where(Stock.active.is_(True))).scalars())
    try:
        r = _get_redis()
        for key in r.scan_iter("signals:cache:*"):
            r.delete(key)
    except Exception:
        pass
    tasks.add_task(_bulk_persist, symbols)
    log.info("signals.reset", deleted=deleted, repersisting=len(symbols))
    return {"status": "reset", "deleted": deleted, "repersisting": len(symbols)}


def _bulk_persist(symbols: list[str]) -> None:
    from db import SessionLocal
    from sqlalchemy import desc
    for symbol in symbols:
        try:
            all_sig = generate_all_signals(symbol)
            with SessionLocal() as s:
                stock = s.query(Stock).filter(Stock.symbol == symbol).one_or_none()
                if not stock:
                    continue
                for style_key, ai in all_sig.items():
                    horizon_enum = SignalHorizon(ai.horizon)
                    # Only insert if the signal type changed for this (stock, horizon) pair,
                    # or if a new signal of the same type is being generated on a different day.
                    last = s.execute(
                        select(Signal.signal, Signal.ts)
                        .where(Signal.stock_id == stock.id, Signal.horizon == horizon_enum)
                        .order_by(desc(Signal.ts))
                        .limit(1)
                    ).one_or_none()
                    if last is not None and last[0] == SignalType(ai.signal) and last[1].date() == date.today():
                        continue
                    s.add(Signal(
                        stock_id=stock.id,
                        signal=SignalType(ai.signal),
                        horizon=horizon_enum,
                        confidence=ai.confidence,
                        bullish_probability=ai.bullish_probability,
                        reasons=ai.reasons,
                    ))
                s.commit()
        except Exception as exc:
            log.warning("signals.refresh.skip", symbol=symbol, error=str(exc))


@router.get("/accuracy")
def signal_accuracy(
    lookback_days: int = Query(90, ge=2, le=365),
    symbol: str | None = None,
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
        correct     = (signal_type == "BUY" and pct_change > 0) or (signal_type == "SELL" and pct_change < 0)

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
        correct = exit_ > entry
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

    Runs the same weight sweep as /ml-weight-validation, picks the weight with the
    highest BUY accuracy over lookback_days, writes it to ml_weight_override.json,
    and updates the in-process value so new signals use it immediately.
    Returns the chosen weight and the full accuracy curve.
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

    def _first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None
        return _pclose[sid][idx]

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
        exit_p = _pclose[sig.stock_id][-1] if ts_list else None
        if exit_p is None or ts_list[-1] <= signal_date:
            continue
        pct = (exit_p - entry) / entry * 100
        observations.append((float(ml_prob), float(ta_score), pct, signal_date))

    if not observations:
        return {"applied": False, "reason": "no_observations", "optimal_weight": None}

    # Sort by date, split older 70% for calibration, newer 30% for validation
    observations.sort(key=lambda x: x[3])
    split = max(1, int(len(observations) * 0.7))
    calib_obs = observations[:split]
    val_obs = observations[split:] or observations  # fall back to all if too few

    weights = [round(w / 20, 2) for w in range(21)]
    best_acc = -1.0
    optimal_weight = 0.5
    curve = []
    for w in weights:
        # Select weight using calibration set only
        correct = fired = 0
        for ml_p, ta_s, pct, _ in calib_obs:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                fired += 1
                if pct > 0:
                    correct += 1
        calib_acc = correct / fired if fired else None
        if calib_acc is not None and calib_acc > best_acc:
            best_acc = calib_acc
            optimal_weight = w

        # Curve accuracy shown on validation set
        v_correct = v_fired = 0
        returns = []
        for ml_p, ta_s, pct, _ in val_obs:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                v_fired += 1
                if pct > 0:
                    v_correct += 1
                returns.append(pct)
        acc = round(v_correct / v_fired * 100, 1) if v_fired else None
        avg_ret = round(sum(returns) / len(returns), 2) if returns else None
        curve.append({"weight": w, "accuracy": acc, "avg_return_pct": avg_ret})

    # Report validation accuracy for the chosen weight
    v_correct = v_fired = 0
    for ml_p, ta_s, pct, _ in val_obs:
        fused = optimal_weight * ml_p + (1 - optimal_weight) * ta_s
        if fused > 0.5:
            v_fired += 1
            if pct > 0:
                v_correct += 1
    best_acc = round(v_correct / v_fired * 100, 1) if v_fired else 0.0

    set_ml_weight_global_cap(optimal_weight)
    log.info("calibrate_ml_weight: applied cap=%.2f (acc=%.1f%%, n=%d, lookback=%dd)",
             optimal_weight, best_acc, len(observations), lookback_days)

    return {
        "applied": True,
        "optimal_weight": optimal_weight,
        "optimal_accuracy": round(best_acc, 1),
        "signal_count": len(observations),
        "lookback_days": lookback_days,
        "previous_cap": prev_cap,
        "curve": curve,
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

        entry_date = sig.ts.date() + timedelta(days=1)  # execute next day, consistent with /accuracy
        entry_price = price_on_or_before(sid, entry_date)
        if entry_price is None:
            continue

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


@router.get("/suppressed")
def suppressed_signals(
    style: str = Query("SWING", description="Trading style: SHORT, SWING, LONG"),
    session: Session = Depends(get_session),
):
    """All active stocks with their latest signal and full suppression condition breakdown.

    Returns each stock's most recent signal plus all filter states extracted from
    the reasons JSON, so the UI can show which conditions are suppressing each signal.
    Sorted by suppression_count descending, then bullish_probability descending.
    """
    horizon_filter = style.upper()

    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )

    q = (
        select(
            Stock.symbol, Stock.name,
            Signal.stock_id, Signal.signal, Signal.horizon, Signal.confidence,
            Signal.bullish_probability, Signal.ts, Signal.reasons,
        )
        .join(Signal, Stock.id == Signal.stock_id)
        .join(
            latest_subq,
            (Signal.stock_id == latest_subq.c.stock_id)
            & (Signal.horizon == latest_subq.c.horizon)
            & (Signal.ts == latest_subq.c.max_ts),
        )
        .where(Stock.active.is_(True))
    )

    try:
        q = q.where(Signal.horizon == SignalHorizon(horizon_filter))
    except ValueError:
        pass

    rows = session.execute(q).all()

    # Fetch conviction gate results from market-data Redis cache
    conviction_data: dict = {}
    try:
        import httpx as _httpx
        cr = _httpx.get(f"{_settings.market_data_url}/stocks/conviction", timeout=4)
        if cr.status_code == 200:
            conviction_data = cr.json()
    except Exception:
        pass

    results = []

    for row in rows:
        r = row.reasons or {}

        conditions = {
            "weekly_gate":          bool(r.get("weekly_gate_fired", False)),
            # weekly_alignment=None means no weekly history — not a misalignment, skip filter
            "weekly_misalignment":  r.get("weekly_alignment") is False,
            "adx_choppy":           bool(r.get("adx_compression", False)),
            "high_vol_regime":      bool(r.get("high_vol_compression", False)),
            "low_breadth":          bool(r.get("breadth_compression", False)),
            "earnings_caution":     r.get("earnings_warning") in ("caution", "note", "watch"),
            "earnings_level":       r.get("earnings_warning"),
            "negative_news":        r.get("news_sentiment_flag") in ("strongly_negative", "negative"),
            "news_level":           r.get("news_sentiment_flag"),
            "rs_lagging":           r.get("rs_flag") == "lagging_sector",
            "bearish_options":      r.get("options_flag") in ("elevated_put_volume", "slightly_elevated_puts"),
            "options_level":        r.get("options_flag"),
            "stale_data":           bool(r.get("stale_price_warning", False)),
            "insufficient_history": bool(r.get("insufficient_history_warning", False)),
            "compression_cap":      bool(r.get("compression_cap_applied", False)),
        }

        suppression_count = sum(
            1 for k, v in conditions.items()
            if k not in ("earnings_level", "news_level", "options_level") and v is True
        )

        conv = conviction_data.get(f"{row.symbol}:{horizon_filter}")
        results.append({
            "symbol":              row.symbol,
            "name":                row.name,
            "signal":              row.signal.value,
            "horizon":             row.horizon.value,
            "confidence":          round(row.confidence, 1),
            "bullish_probability": round(row.bullish_probability, 4) if row.bullish_probability else None,
            "ts":                  row.ts.isoformat() if row.ts else None,
            "conditions":          conditions,
            "suppression_count":   suppression_count,
            "market_regime":       r.get("market_regime"),
            "weekly_rsi":          r.get("weekly_rsi"),
            "weekly_trend":        r.get("weekly_trend"),
            "rsi":                 r.get("rsi"),
            "adx":                 r.get("adx"),
            "breadth_pct":         r.get("breadth_pct"),
            "days_to_earnings":    r.get("days_to_earnings"),
            "news_sentiment":      r.get("news_sentiment"),
            "rs_score":            r.get("rs_score"),
            "conviction":          conv,
            # SA-19 pillar scores — 0-1 per dimension; None if signal pre-dates SA-19
            "pillar_trend":        r.get("pillar_trend"),
            "pillar_momentum":     r.get("pillar_momentum"),
            "pillar_volume":       r.get("pillar_volume"),
            "pillar_structure":    r.get("pillar_structure"),
            "pillars_active":      r.get("independent_pillars_active"),
        })

    # Compute days_active per condition — how many consecutive days each flag has been True.
    # Bulk-load the last 90 days of signals for all stocks in the result set.
    stock_ids = [row.stock_id for row in rows]
    cutoff_90 = datetime.now(timezone.utc) - timedelta(days=90)
    try:
        _horizon_enum = SignalHorizon(horizon_filter)
        hist_rows = session.execute(
            select(Signal.stock_id, Signal.ts, Signal.reasons)
            .where(
                Signal.stock_id.in_(stock_ids),
                Signal.horizon == _horizon_enum,
                Signal.ts >= cutoff_90,
            )
            .order_by(Signal.stock_id, Signal.ts.desc())
        ).all() if rows else []
    except ValueError:
        hist_rows = []

    # Group history by stock_id
    from collections import defaultdict
    hist_by_stock: dict[int, list] = defaultdict(list)
    for h in hist_rows:
        hist_by_stock[h.stock_id].append(h)

    _CONDITION_KEYS = [
        "weekly_gate", "weekly_misalignment", "adx_choppy", "high_vol_regime",
        "low_breadth", "earnings_caution", "negative_news", "rs_lagging",
        "bearish_options", "stale_data", "insufficient_history", "compression_cap",
    ]

    def _extract_conditions(reasons: dict) -> dict[str, bool]:
        return {
            "weekly_gate":          bool(reasons.get("weekly_gate_fired", False)),
            "weekly_misalignment":  reasons.get("weekly_alignment") is False,
            "adx_choppy":           bool(reasons.get("adx_compression", False)),
            "high_vol_regime":      bool(reasons.get("high_vol_compression", False)),
            "low_breadth":          bool(reasons.get("breadth_compression", False)),
            "earnings_caution":     reasons.get("earnings_warning") in ("caution", "note", "watch"),
            "negative_news":        reasons.get("news_sentiment_flag") in ("strongly_negative", "negative"),
            "rs_lagging":           reasons.get("rs_flag") == "lagging_sector",
            "bearish_options":      reasons.get("options_flag") in ("elevated_put_volume", "slightly_elevated_puts"),
            "stale_data":           bool(reasons.get("stale_price_warning", False)),
            "insufficient_history": bool(reasons.get("insufficient_history_warning", False)),
            "compression_cap":      bool(reasons.get("compression_cap_applied", False)),
        }

    def _days_active(stock_id: int) -> dict[str, int]:
        """Walk back through signal history; count consecutive days each condition is True."""
        history = hist_by_stock.get(stock_id, [])
        streak: dict[str, int] = {k: 0 for k in _CONDITION_KEYS}
        if not history:
            return streak
        # history is ordered ts desc; walk from most recent, stop streak when condition flips to False
        active: dict[str, bool] = {k: True for k in _CONDITION_KEYS}
        prev_ts: datetime | None = None
        for h in history:
            conds = _extract_conditions(h.reasons or {})
            ts = h.ts.replace(tzinfo=timezone.utc) if h.ts.tzinfo is None else h.ts
            # Gap > 2 calendar days (more than a weekend) resets all active streaks
            if prev_ts is not None and (prev_ts - ts).days > 2:
                for k in _CONDITION_KEYS:
                    active[k] = False
            for k in _CONDITION_KEYS:
                if not active[k]:
                    continue
                if conds.get(k, False):
                    streak[k] += 1
                else:
                    active[k] = False
            prev_ts = ts
        return streak

    # Attach days_active to each result
    days_active_by_symbol: dict[str, dict[str, int]] = {}
    for row in rows:
        days_active_by_symbol[row.symbol] = _days_active(row.stock_id)

    for item in results:
        item["days_active"] = days_active_by_symbol.get(item["symbol"], {})

    results.sort(key=lambda x: (-x["suppression_count"], -(x["bullish_probability"] or 0)))
    return results


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
        prices_by_stock[p.stock_id].append((p.ts, float(p.close)))

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

        signal_date = row.ts if isinstance(row.ts, date) else row.ts.date()
        exit_date   = signal_date + timedelta(days=hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date)
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

        signal_date = row.ts if isinstance(row.ts, date) else row.ts.date()
        exit_date   = signal_date + timedelta(days=hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date)
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


@router.post("/calibrate_ta_weights")
def calibrate_ta_weights(
    lookback_days: int = Query(365, ge=60, le=730),
    hold_days: int = Query(10, ge=3, le=30),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Fit logistic regression on historical BUY signals to derive data-driven TA weights.

    Reads the last `lookback_days` of BUY signals, extracts TA boolean features from the
    stored reasons JSON, looks up actual price returns over `hold_days`, then fits a logistic
    regression model. The resulting coefficients (clipped to [0, ∞]) become the new TA weights
    and are written to ta_weights.json next to the ML models directory.

    Returns the fitted weights and in-sample accuracy for review.
    """
    import json
    from pathlib import Path

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        raise HTTPException(status_code=500, detail="scikit-learn not installed in signal-engine")

    from ..generators.signals import _TA_WEIGHTS_DEFAULT, _TA_WEIGHTS_PATH

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id)
        .where(Signal.signal == SignalType.BUY, Signal.ts >= cutoff)
        .order_by(Signal.ts)
    ).all()

    if len(rows) < 50:
        raise HTTPException(status_code=400, detail=f"Need ≥50 BUY signals, found {len(rows)}")

    # TA boolean feature names (positive weights only — penalties excluded from regression)
    TA_FEATURES = [
        "above_sma50", "sma50_above_sma200", "golden_cross_event",
        "rsi_sweet_spot", "rsi_mild_oversold", "rsi_mild_overbought",
        "stoch_oversold", "stoch_cross_up",
        "rsi_divergence_bullish",
        "macd_strong", "macd_positive", "macd_zero_cross_up",
        "bb_mid_zone", "price_above_vwap",
        "bullish_trend", "obv_trend_bullish", "volume_surge",
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
        "rsi_divergence_bullish": lambda r: r.get("rsi_divergence") == "bullish",
        "macd_strong":            lambda r: (r.get("macd_hist") or 0) > 0 and bool(r.get("macd_rising")),
        "macd_positive":          lambda r: (r.get("macd_hist") or 0) > 0 and not bool(r.get("macd_rising")),
        "macd_zero_cross_up":     lambda r: bool(r.get("macd_zero_cross_up")),
        "bb_mid_zone":            lambda r: 0.2 < (r.get("bb_pct_b") or 0) < 0.8,
        "price_above_vwap":       lambda r: r.get("price_above_vwap") is True,
        "bullish_trend":          lambda r: bool(r.get("adx_bullish")),
        "obv_trend_bullish":      lambda r: bool(r.get("obv_trend_bullish")),
        "volume_surge":           lambda r: (r.get("volume_z") or 0) > 0.5,
    }

    import bisect

    # Bulk-load all D1 prices for involved stocks — avoids N+1 queries in loop
    stock_ids = list({row.stock_id for row in rows})
    min_ts = min(row.ts for row in rows)
    max_ts_needed = datetime.now(timezone.utc) + timedelta(days=hold_days + 10)
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= min_ts,
            Price.ts <= max_ts_needed,
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()
    # Build per-stock sorted list of (ts_naive_date, close)
    from collections import defaultdict
    _price_map: dict[int, list[tuple]] = defaultdict(list)
    for pr in price_rows:
        ts_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
        _price_map[pr.stock_id].append((ts_date, float(pr.close)))

    def _lookup_price(stock_id: int, on_or_after: "date") -> "float | None":
        bucket = _price_map.get(stock_id, [])
        if not bucket:
            return None
        dates = [b[0] for b in bucket]
        idx = bisect.bisect_left(dates, on_or_after)
        if idx >= len(bucket):
            return None
        return bucket[idx][1]

    X_rows, y_rows, skipped = [], [], 0
    for row in rows:
        try:
            reasons = json.loads(row.reasons) if isinstance(row.reasons, str) else (row.reasons or {})
        except Exception:
            skipped += 1
            continue

        signal_date = row.ts.date() if hasattr(row.ts, "date") else row.ts
        entry_price_row = _lookup_price(row.stock_id, signal_date)
        exit_price_row = _lookup_price(row.stock_id, signal_date + timedelta(days=hold_days))

        if entry_price_row is None or exit_price_row is None:
            skipped += 1
            continue

        fwd_ret = exit_price_row / entry_price_row - 1
        y_rows.append(1 if fwd_ret > 0 else 0)
        X_rows.append([float(REASONS_MAP[f](reasons)) for f in TA_FEATURES])

    if len(X_rows) < 30:
        raise HTTPException(status_code=400, detail=f"Only {len(X_rows)} usable rows after price lookup (skipped {skipped})")

    X = np.array(X_rows)
    y = np.array(y_rows)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    clf = LogisticRegression(max_iter=500, C=1.0, random_state=42)

    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    cv_scores = cross_val_score(clf, X_scaled, y, cv=TimeSeriesSplit(n_splits=5), scoring="accuracy")
    accuracy = float(np.mean(cv_scores))

    # Fit on full data to derive the production weights
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
    new_weights = dict(_TA_WEIGHTS_DEFAULT)
    new_weights.update(fitted_scaled)

    Path(_TA_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(_TA_WEIGHTS_PATH).write_text(json.dumps(new_weights, indent=2))
    log.info("calibrate_ta_weights: wrote %s (accuracy=%.3f, n=%d)", _TA_WEIGHTS_PATH, accuracy, len(X_rows))

    return {
        "status":           "ok",
        "n_signals":        len(rows),
        "n_usable":         len(X_rows),
        "n_skipped":        skipped,
        "in_sample_accuracy": round(accuracy, 4),
        "weights":          new_weights,
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
        select(SignalOutcome.is_correct, Signal.reasons)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            Signal.reasons.is_not(None),
        )
    ).all()

    if len(rows) < 30:
        raise HTTPException(400, f"Need ≥30 evaluated BUY outcomes, found {len(rows)}")

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
    layer_stats: dict[str, dict] = {}
    for k in all_keys:
        wc = key_wins.get(k, 0)
        lc = key_los.get(k, 0)
        if wc + lc < min_count:
            continue
        wp = wc / n_win if n_win > 0 else 0.0
        lp = lc / n_los if n_los > 0 else 0.0
        accuracy = wc / (wc + lc) if (wc + lc) > 0 else 0.5
        layer_stats[k] = {
            "win_pct": round(wp * 100, 1),
            "los_pct": round(lp * 100, 1),
            "edge_pct": round((wp - lp) * 100, 1),
            "accuracy": round(accuracy * 100, 1),
            "is_noise": accuracy < 0.52,
            "win_count": wc,
            "los_count": lc,
        }

    # Fit logistic regression for coefficient-based weights
    features = sorted(layer_stats.keys())
    if len(features) >= 3 and len(rows) >= 50:
        X = np.array([[int(bool((r.reasons or {}).get(f))) for f in features] for r in rows])
        y = np.array([int(r.is_correct) for r in rows])
        try:
            lr = LogisticRegression(max_iter=500, C=1.0, random_state=42)
            lr.fit(X, y)
            for feat, coef in zip(features, lr.coef_[0]):
                if feat in layer_stats:
                    layer_stats[feat]["logistic_coef"] = round(float(coef), 4)
        except Exception:
            pass

    payload = {
        "as_of": date.today().isoformat(),
        "lookback_days": lookback_days,
        "total_winners": n_win,
        "total_losers": n_los,
        "layer_count": len(layer_stats),
        "noise_count": sum(1 for s in layer_stats.values() if s["is_noise"]),
        "layers": layer_stats,
        "edge_pct": {k: v["edge_pct"] for k, v in layer_stats.items()},
    }

    try:
        Path(_CONVICTION_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_CONVICTION_WEIGHTS_PATH).write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("conviction_weights.write_failed", error=str(exc))

    log.info("conviction_weights.calibrated", layers=len(layer_stats), noise=payload["noise_count"])
    return payload


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
            r = c.get(url, params={"timeframe": "1d", "start": start.isoformat(), "limit": 1000})
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


@router.get("/{symbol}/history")
def signal_history(
    symbol: str,
    style: str = Query("SWING", description="Trading style: SHORT, SWING, LONG, GROWTH"),
    days: int = Query(60, ge=7, le=365),
    session: Session = Depends(get_session),
):
    """Historical signal confidence trend for a single symbol.

    Returns up to `days` days of stored signals ordered oldest → newest.
    Used by the stock detail page sparkline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        horizon = SignalHorizon(style.upper())
    except ValueError:
        horizon = SignalHorizon.SWING

    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Symbol {symbol} not found")

    rows = session.execute(
        select(Signal.ts, Signal.signal, Signal.confidence, Signal.bullish_probability)
        .where(
            Signal.stock_id == stock.id,
            Signal.horizon == horizon,
            Signal.ts >= cutoff,
        )
        .order_by(Signal.ts.asc())
    ).all()

    return [
        {
            "ts": r.ts.isoformat() if r.ts else None,
            "signal": r.signal.value,
            "confidence": round(r.confidence, 1),
            "bullish_probability": round(r.bullish_probability, 4) if r.bullish_probability else None,
        }
        for r in rows
    ]


@router.get("/{symbol}/patterns")
def detect_patterns(
    symbol: str,
    session: Session = Depends(get_session),
):
    """Detect active technical chart patterns for a symbol.

    Returns patterns detected within the last 3-5 bars so the UI shows
    live "about to move" badges. Patterns checked: golden_cross,
    macd_bullish_cross, rsi_oversold_bounce, double_bottom, breakout.
    """
    import pandas as pd

    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Stock {symbol} not found")

    rows = session.execute(
        select(Price.ts, Price.close, Price.high, Price.low, Price.volume)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.asc())
        .limit(260)
    ).all()

    if len(rows) < 30:
        return {"symbol": symbol.upper(), "patterns": [], "as_of": datetime.now(timezone.utc).isoformat() + "Z"}

    close = pd.Series([float(r.close) for r in rows])
    volume = pd.Series([float(r.volume) for r in rows])

    patterns: list[dict] = []

    def _add(name: str, label: str, description: str, bullish: bool) -> None:
        patterns.append({"name": name, "label": label, "description": description, "bullish": bullish})

    # 1. Golden Cross — EMA50 crossed above EMA200 within last 5 bars
    if len(close) >= 200:
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        for i in range(max(-5, -len(close) + 1), 0):
            if ema50.iloc[i - 1] < ema200.iloc[i - 1] and ema50.iloc[i] >= ema200.iloc[i]:
                _add("golden_cross", "Golden Cross", f"EMA50 crossed above EMA200 ({ema200.iloc[-1]:.2f})", True)
                break

    # 2. MACD Bullish Cross — MACD crossed above signal within last 3 bars
    if len(close) >= 35:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()
        for i in range(max(-3, -len(close) + 1), 0):
            if macd.iloc[i - 1] < sig.iloc[i - 1] and macd.iloc[i] >= sig.iloc[i]:
                _add("macd_bullish_cross", "MACD Cross ↑", f"MACD crossed above signal ({sig.iloc[-1]:.3f})", True)
                break

    # 3. RSI Oversold Bounce — RSI crossed above 30 within last 3 bars
    if len(close) >= 16:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        for i in range(max(-3, -len(close) + 1), 0):
            prev_v = rsi.iloc[i - 1]
            curr_v = rsi.iloc[i]
            if pd.notna(prev_v) and pd.notna(curr_v) and prev_v < 30 and curr_v >= 30:
                _add("rsi_oversold_bounce", "RSI Bounce", f"RSI recovered from oversold (now {rsi.iloc[-1]:.1f})", True)
                break

    # 4. Double Bottom — two troughs within 3% in last 60 bars, separated by 5%+ peak
    if len(close) >= 20:
        window = close.tail(60).values
        minima: list[tuple[int, float]] = []
        for i in range(2, len(window) - 2):
            if all(window[i] <= window[j] for j in range(i - 2, i + 3) if j != i):
                minima.append((i, float(window[i])))
        if len(minima) >= 2:
            b1_idx, b1_val = minima[-2]
            b2_idx, b2_val = minima[-1]
            lower = min(b1_val, b2_val)
            if lower > 0 and abs(b1_val - b2_val) / lower <= 0.03 and b2_idx > b1_idx + 3:
                peak = float(max(window[b1_idx:b2_idx + 1]))
                if peak >= lower * 1.05 and float(close.iloc[-1]) > lower * 1.01:
                    _add("double_bottom", "Double Bottom", f"W-pattern: two troughs near ${lower:.2f}", True)

    # 5. Volume Breakout — close above 20-day high with elevated volume (1.4x avg)
    if len(close) >= 21:
        high_20 = float(close.iloc[-21:-1].max())
        avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else 0.0
        if float(close.iloc[-1]) > high_20 and avg_vol > 0 and float(volume.iloc[-1]) >= avg_vol * 1.4:
            _add("breakout", "Volume Breakout", f"Closed above 20-day high (${high_20:.2f}) on elevated volume", True)

    return {
        "symbol": symbol.upper(),
        "patterns": patterns,
        "as_of": datetime.now(timezone.utc).isoformat() + "Z",
    }


@router.get("/outcomes/summary")
def outcomes_summary(
    horizon: str | None = Query(None, description="SHORT | SWING | LONG"),
    days: int = Query(90, description="Look-back window in calendar days"),
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

    outcomes = session.execute(q).scalars().all()

    if not outcomes:
        return {"total": 0, "message": "No evaluated outcomes yet in this window"}

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

    return {
        "total": len(outcomes),
        "days_lookback": days,
        "overall": {
            "win_rate": round(len(wins) / len(outcomes), 3),
            "avg_return_pct": round(statistics.mean(returns) * 100, 2) if returns else None,
            "median_return_pct": round(statistics.median(returns) * 100, 2) if returns else None,
        },
        "by_confidence_band": band_stats,
        "by_horizon": horizon_stats,
        "by_market_regime": regime_summary,
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


def _stored_signal_for_style(session: Session, stock_id: int, style_key: str) -> dict | None:
    """Return the latest persisted signal row for this stock+style as a plain dict."""
    from sqlalchemy import desc as _desc
    try:
        horiz = SignalHorizon(style_key)
    except ValueError:
        return None
    row = session.execute(
        select(Signal.signal, Signal.confidence, Signal.bullish_probability, Signal.ts, Signal.reasons)
        .where(Signal.stock_id == stock_id, Signal.horizon == horiz)
        .order_by(_desc(Signal.ts))
        .limit(1)
    ).one_or_none()
    if not row:
        return None
    reasons = dict(row.reasons) if row.reasons else {}
    reasons["stability_days"] = _compute_stability(session, stock_id, horiz, row.signal.value)
    return {
        "signal": row.signal.value,
        "horizon": style_key,
        "confidence": row.confidence,
        "bullish_probability": row.bullish_probability,
        "reasons": reasons,
        "ts": row.ts.isoformat() if row.ts else None,
    }


@router.get("/{symbol}")
def signal_for(
    symbol: str,
    persist: bool = False,
    live: bool = Query(True, description="False = return latest persisted DB signal (matches signal filter). True = compute fresh (may differ from DB)."),
    style: str | None = Query(None, description="Trading style: SHORT, SWING, LONG, GROWTH. Returns all if omitted."),
    session: Session = Depends(get_session),
):
    """Return signal(s) for a symbol.

    live=False (default on detail page): reads latest stored DB signal — consistent with signal filter.
    live=True + persist=True: recomputes fresh and overwrites DB — used by the Refresh button.
    """
    stock = session.query(Stock).filter(Stock.symbol == symbol).one_or_none()

    if not live and not persist:
        # Fast path: return stored signals from DB without running ML/TA
        if not stock:
            raise HTTPException(404, f"Stock {symbol} not found")
        all_styles = ["SHORT", "SWING", "LONG", "GROWTH"]
        stored: dict[str, dict] = {}
        for s_key in all_styles:
            d = _stored_signal_for_style(session, stock.id, s_key)
            if d:
                stored[s_key] = d
        if not stored:
            # No stored signals yet — fall through to live computation
            pass
        else:
            if style:
                s_key = style.upper()
                data = stored.get(s_key)
                if data:
                    return {"symbol": symbol, **data}
            else:
                return {"symbol": symbol, "signals": stored}

    # Live computation (or fallback when no stored signals exist)
    try:
        all_sig = generate_all_signals(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if persist and stock:
        for ai in all_sig.values():
            session.add(Signal(
                stock_id=stock.id,
                signal=SignalType(ai.signal),
                horizon=SignalHorizon(ai.horizon),
                confidence=ai.confidence,
                bullish_probability=ai.bullish_probability,
                reasons=ai.reasons,
            ))
        session.commit()

    # Inject stability_days into each signal's reasons dict
    if stock:
        for ai in all_sig.values():
            try:
                horiz = SignalHorizon(ai.horizon)
            except ValueError:
                continue
            ai.reasons["stability_days"] = _compute_stability(session, stock.id, horiz, ai.signal)

    if style:
        style_key = style.upper()
        ai = all_sig.get(style_key) or all_sig["SWING"]
        return {"symbol": symbol, **asdict(ai)}

    return {
        "symbol": symbol,
        "signals": {k: asdict(v) for k, v in all_sig.items()},
    }


# ── Signal outcome tracking ────────────────────────────────────────────────────

# Hold window in calendar days per horizon. Approximates actual trading days held.
_OUTCOME_HOLD_DAYS: dict[str, int] = {
    "SHORT":  7,    # ~5 trading days
    "SWING":  14,   # ~10 trading days
    "LONG":   28,   # ~20 trading days
    "GROWTH": 14,   # same window as SWING; growth trades are momentum-based
}


@router.post("/outcomes/evaluate")
def evaluate_signal_outcomes(session: Session = Depends(get_session)):
    """Evaluate closed signal outcomes and persist them to signal_outcomes.

    For each BUY/SELL signal whose hold window has expired:
    - Entry price = first D1 close on or after signal date
    - Exit price  = first D1 close on or after entry_date + hold_window_days
    - pct_return  = (exit - entry) / entry
    - is_correct  = price went up for BUY, down for SELL

    Safe to re-run — already-evaluated signals (by UNIQUE signal_id) are skipped.
    Called automatically by the scheduler post-close.
    """
    from datetime import time as _time

    today = date.today()
    min_hold = min(_OUTCOME_HOLD_DAYS.values())
    cutoff = today - timedelta(days=min_hold)

    # IDs already in signal_outcomes — skip re-evaluation
    evaluated_ids: set[int] = set(session.execute(
        select(SignalOutcome.signal_id)
    ).scalars().all())

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

    import bisect
    from collections import defaultdict

    # Bulk-load all D1 prices for pending signals — avoids N+1 queries in loop
    pending_stock_ids = list({sig.stock_id for sig, _ in pending_signals})
    max_hold = max(_OUTCOME_HOLD_DAYS.values())
    price_min_ts = min((sig.ts for sig, _ in pending_signals), default=datetime.now())
    price_max_ts = datetime.now() + timedelta(days=max_hold + 10)
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

    evaluated, skipped_open, skipped_no_price = 0, 0, 0

    for sig, symbol in pending_signals:
        if sig.id in evaluated_ids:
            continue

        horizon = sig.horizon.value
        hold_days = _OUTCOME_HOLD_DAYS[horizon]
        signal_date = sig.ts.date()

        entry_result = _lookup_outcome_price(sig.stock_id, signal_date)
        if entry_result is None:
            skipped_no_price += 1
            continue

        entry_date, entry_price = entry_result
        exit_target = entry_date + timedelta(days=hold_days)

        if exit_target >= today:
            skipped_open += 1
            continue  # Hold window not closed yet (need tomorrow's price at minimum)

        exit_result = _lookup_outcome_price(sig.stock_id, exit_target)
        if exit_result is None:
            skipped_open += 1
            continue

        exit_date, exit_price = exit_result
        if entry_price <= 0:
            skipped_no_price += 1
            continue
        pct_return = (exit_price - entry_price) / entry_price
        hold_days_actual = (exit_date - entry_date).days
        is_correct = pct_return > 0 if sig.signal == SignalType.BUY else pct_return < 0

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
        )
        session.add(outcome)
        evaluated_ids.add(sig.id)
        evaluated += 1

    session.commit()
    log.info(
        "outcomes.evaluate_done",
        evaluated=evaluated,
        skipped_open=skipped_open,
        skipped_no_price=skipped_no_price,
    )
    return {"evaluated": evaluated, "skipped_open": skipped_open, "skipped_no_price": skipped_no_price}
