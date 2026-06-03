from dataclasses import asdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import Price, Signal, SignalHorizon, SignalType, Stock, TimeFrame, get_session

from ..generators import generate_signal, generate_all_signals

log = get_logger("signals")

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def all_latest_signals(
    style: str | None = Query(None, description="Filter by trading style: SHORT, SWING, LONG"),
    session: Session = Depends(get_session),
):
    """Return the most recently persisted signal for every active stock.

    Optional ?style=SWING (default) filters to a specific trading horizon.
    If omitted, returns the most recent signal regardless of style.
    """
    horizon_filter = style.upper() if style else "SWING"
    # Subquery: latest ts per (stock_id, horizon)
    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )
    q = (
        select(Stock.symbol, Signal.signal, Signal.horizon, Signal.confidence, Signal.bullish_probability, Signal.ts)
        .join(Signal, Stock.id == Signal.stock_id)
        .join(latest_subq, (Signal.stock_id == latest_subq.c.stock_id)
              & (Signal.horizon == latest_subq.c.horizon)
              & (Signal.ts == latest_subq.c.max_ts))
        .where(Stock.active.is_(True))
    )
    try:
        q = q.where(Signal.horizon == SignalHorizon(horizon_filter))
    except ValueError:
        pass  # unknown style — return all
    rows = session.execute(q).all()
    return [
        {
            "symbol": row.symbol,
            "signal": row.signal.value,
            "horizon": row.horizon.value,
            "confidence": row.confidence,
            "bullish_probability": row.bullish_probability,
            "ts": row.ts.isoformat() if row.ts else None,
        }
        for row in rows
    ]


@router.post("/refresh")
def refresh_signals(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
):
    """Recompute and persist signals for all active stocks, optionally filtered by market."""
    q = select(Stock.symbol).where(Stock.active.is_(True))
    if market:
        q = q.where(Stock.market == market.upper())
    symbols = list(session.execute(q).scalars())
    tasks.add_task(_bulk_persist, symbols)
    return {"status": "scheduled", "count": len(symbols)}


@router.post("/reset")
def reset_signals(tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """Wipe all persisted signals then re-persist fresh ones for every active stock."""
    deleted = session.query(Signal).delete()
    session.commit()
    symbols = list(session.execute(select(Stock.symbol).where(Stock.active.is_(True))).scalars())
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
                    # Only insert if the signal type changed for this (stock, horizon) pair.
                    last = s.execute(
                        select(Signal.signal)
                        .where(Signal.stock_id == stock.id, Signal.horizon == horizon_enum)
                        .order_by(desc(Signal.ts))
                        .limit(1)
                    ).scalar_one_or_none()
                    if last is not None and last == SignalType(ai.signal):
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
    session: Session = Depends(get_session),
):
    """Historical accuracy of BUY/SELL signals vs actual price outcomes.

    For each persisted BUY or SELL signal within the lookback window, compares
    the close price on the signal date to the most recent available close price.
    A BUY is 'correct' if price rose; a SELL is 'correct' if it fell.
    Signals need at least 1 day of price history after the signal date to be evaluated.
    Uses bulk price queries + bisect matching instead of per-signal queries.
    """
    import bisect

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    outcome_cutoff = datetime.utcnow() - timedelta(days=1)

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
        "signals": results,
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

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    outcome_cutoff = datetime.utcnow() - timedelta(days=1)

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

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    outcome_cutoff = datetime.utcnow() - timedelta(days=1)

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
        entry = price_on_or_before(sig.stock_id, signal_date + timedelta(days=1))
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

    return {"lookback_days": lookback_days, "signal_count": total, "factors": factors}


@router.get("/trade_performance")
def trade_performance(
    lookback_days: int = Query(180, ge=7, le=730),
    symbol: str | None = None,
    horizon: str = Query("SWING", regex="^(SHORT|SWING|LONG)$"),
    session: Session = Depends(get_session),
):
    """BUY → SELL trade-pair performance over a lookback window.

    Filters by horizon (SHORT/SWING/LONG) so SHORT BUY signals are only closed
    by SHORT SELL signals — not cross-contaminated by signals from other styles
    running in the same scheduler batch.

    Only SELL signals close a trade. WAIT is a "hold off new entries" signal and
    does NOT represent an exit — pairing WAIT as an exit created phantom 0-day
    trades with ~0% return whenever SHORT=BUY and SWING=WAIT fired together.

    Open trades (no exit SELL yet) use the latest available price.
    """
    import bisect
    from collections import defaultdict

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    horizon_enum = SignalHorizon(horizon)

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
    buy_rows = session.execute(q).all()

    if not buy_rows:
        return {"lookback_days": lookback_days, "closed_trades": 0, "open_trades": 0,
                "win_rate": None, "avg_return_pct": None, "avg_win_pct": None,
                "avg_loss_pct": None, "profit_factor": None, "avg_hold_days": None,
                "by_symbol": [], "trades": []}

    stock_ids = list({sig.stock_id for sig, _, _ in buy_rows})

    # 2. All SELL signals for those stocks, same horizon (no date filter — exits may be outside window)
    # WAIT is excluded: it means "don't add new entries" not "exit position."
    # Mixing horizons here was the source of phantom 0-day trades (SHORT=BUY + SWING=WAIT same batch).
    exit_rows = session.execute(
        select(Signal.stock_id, Signal.ts, Signal.signal)
        .where(Signal.stock_id.in_(stock_ids))
        .where(Signal.signal == SignalType.SELL)
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
        if exit_ts is not None:
            exit_date  = exit_ts.date() + timedelta(days=1)  # execute next day, symmetric with entry
            exit_price = price_on_or_before(sid, exit_date)
            status     = "closed"
            last_exit_ts[sid] = exit_ts  # block duplicate BUYs that fall before this exit
        else:
            exit_price, exit_ts_raw = latest_price(sid)
            if exit_price is None:
                continue
            # Normalise to date — _price_ts stores date objects after the fix above
            exit_date       = exit_ts_raw.date() if isinstance(exit_ts_raw, datetime) else exit_ts_raw
            exit_signal_val = "OPEN"
            status          = "open"
            in_open_trade.add(sid)  # block any later BUYs — position is already open

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


@router.get("/{symbol}")
def signal_for(
    symbol: str,
    persist: bool = False,
    style: str | None = Query(None, description="Trading style: SHORT, SWING, LONG. Returns all 3 if omitted."),
    session: Session = Depends(get_session),
):
    """Generate (and optionally persist) fresh signals for the given symbol.

    Returns all 3 style signals by default, or just the requested style.
    """
    try:
        all_sig = generate_all_signals(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if persist:
        from sqlalchemy import desc
        stock = session.query(Stock).filter(Stock.symbol == symbol).one_or_none()
        if stock:
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

    if style:
        style_key = style.upper()
        ai = all_sig.get(style_key) or all_sig["SWING"]
        return {"symbol": symbol, **asdict(ai)}

    # Return all 3 styles
    return {
        "symbol": symbol,
        "signals": {k: asdict(v) for k, v in all_sig.items()},
    }
