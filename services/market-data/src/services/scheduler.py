"""Market-aware scheduler — refreshes prices, rankings, signals, and ML models
around trading hours for US (NYSE/NASDAQ) and HK (HKEX) markets.

Schedule (times are in the local market timezone, DST handled automatically):

  US (America/New_York):
    09:00  pre-open         ingest + rankings + signals
    10:45  intra-day 1      ingest + rankings + signals
    12:45  intra-day 2      ingest + rankings + signals
    14:45  intra-day 3      ingest + rankings + signals
    16:30  post-close       ingest + rankings + signals + ML retrain

  HK (Asia/Hong_Kong, UTC+8, no DST):
    09:00  pre-open         ingest + rankings + signals
    10:30  intra-day 1      ingest + rankings + signals
    14:15  intra-day 2      ingest + rankings + signals  (post-lunch)
    15:30  intra-day 3      ingest + rankings + signals
    16:30  post-close       ingest + rankings + signals + ML retrain
"""
from __future__ import annotations

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone

from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import AlertCondition, Price, PriceAlert, SignalAlert, SessionLocal, Stock

from .ingestion import ingest_universe
from .email_service import send_price_alert_email, send_signal_alert_email

log = get_logger("scheduler")
_settings = get_settings()
_scheduler: BackgroundScheduler | None = None


def _symbols_for(market: str) -> list[str]:
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.market == market)
            ).scalars()
        )


def _post(url: str, **kwargs) -> None:
    try:
        with httpx.Client(timeout=15) as client:
            client.post(url, **kwargs)
    except Exception as exc:
        log.warning("scheduler.http_error", url=url, error=str(exc))


def _refresh_market(market: str, *, post_close: bool = False) -> None:
    symbols = _symbols_for(market)
    if not symbols:
        log.info("scheduler.skip", market=market, reason="no_symbols")
        return

    log.info("scheduler.refresh_start", market=market, count=len(symbols), post_close=post_close)

    ingest_universe(symbols, "1d")

    _post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": market})
    _post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": market})

    if post_close:
        _post(f"{_settings.ml_prediction_url}/ml/train_all")

    check_signal_alerts()
    check_technical_alerts()
    log.info("scheduler.refresh_done", market=market, post_close=post_close)


_BULLISH_TRANSITIONS = {
    ("SELL", "HOLD"), ("SELL", "WAIT"), ("SELL", "BUY"),
    ("WAIT", "HOLD"), ("WAIT", "BUY"),
    ("HOLD", "BUY"),
}
# Fired regardless of analyst rating — these are exit warnings
_BEARISH_TRANSITIONS = {
    ("BUY", "HOLD"), ("BUY", "WAIT"), ("BUY", "SELL"),
}
_BULLISH_ANALYST = {"buy", "strong_buy", "strongbuy", "outperform"}


def _build_game_plan(symbol: str, signal_data: dict, fundamentals: dict | None) -> dict | None:
    """Build a rule-based game plan from technical data when signal transitions to BUY."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="60d")
        if hist.empty:
            return None
        current_price = float(hist["Close"].iloc[-1])

        reasons = signal_data.get("reasons", {})

        # Derive entry levels from technical structure
        above_sma50 = reasons.get("trend_above_sma50", False)
        sma50_above_sma200 = reasons.get("sma50_above_sma200", False)
        rsi = reasons.get("rsi")
        bb_pct_b = reasons.get("bb_pct_b")

        # Entry 1: 1.5–2% below current (near support), rounder number
        raw_entry1 = current_price * 0.985
        entry1 = round(raw_entry1 / _round_step(current_price)) * _round_step(current_price)

        # Entry 2: deeper pullback (3.5–4%), ideally near a fibonacci/sma zone
        raw_entry2 = current_price * 0.965
        entry2 = round(raw_entry2 / _round_step(current_price)) * _round_step(current_price)

        # Breakout: 2% above current
        breakout = round(current_price * 1.02 / _round_step(current_price)) * _round_step(current_price)

        # Stop: below entry2 by ~2% — close below = invalidated
        stop = round(current_price * 0.945 / _round_step(current_price)) * _round_step(current_price)

        # Take profit: analyst target or +12%
        target_price = (fundamentals or {}).get("target_price")
        if target_price and float(target_price) > current_price * 1.03:
            take_profit = float(target_price)
            tp_note = "analyst mean price target"
        else:
            take_profit = round(current_price * 1.12 / _round_step(current_price)) * _round_step(current_price)
            tp_note = "+12% from current, near next resistance"

        # Entry rationale hints from technicals
        if rsi is not None and float(rsi) < 45:
            e1_note = f"RSI {float(rsi):.0f} — oversold recovery zone"
            e2_note = "oversold extension — scale in on deeper dip"
        elif bb_pct_b is not None and float(bb_pct_b) < 0.4:
            e1_note = "lower Bollinger band support region"
            e2_note = "near lower band — strong mean-reversion level"
        elif above_sma50:
            e1_note = "pullback to SMA50 support zone"
            e2_note = "deeper pullback — maintain SMA50 as key level"
        else:
            e1_note = "near-term support — scale in on weakness"
            e2_note = "secondary support — averaging down level"

        breakout_note = "breakout above resistance on volume — momentum confirmed"
        if sma50_above_sma200:
            stop_note = "daily close below signals golden-cross breakdown"
        else:
            stop_note = "daily close below invalidates bullish setup"

        # Earnings catalyst / risk
        next_earnings = (fundamentals or {}).get("next_earnings_date")
        days_to_earnings = (fundamentals or {}).get("days_to_earnings")
        earnings_line = ""
        if next_earnings:
            d = days_to_earnings or "?"
            earnings_line = f"No earnings until {next_earnings} ({d}d) — clean runway" if (days_to_earnings or 99) > 10 else f"⚠ Earnings {next_earnings} ({d}d) — position size accordingly"

        catalysts = [c for c in [
            earnings_line or None,
            "Analyst consensus bullish — upgrade potential if momentum holds" if (fundamentals or {}).get("recommendation", "").lower() in ("buy", "strong_buy") else None,
            "SMA50 > SMA200 golden-cross structure intact" if sma50_above_sma200 else None,
            f"RSI {float(rsi):.0f} — recovering from oversold territory" if rsi is not None and float(rsi) < 50 else None,
            "MACD histogram rising — short-term momentum confirming" if reasons.get("macd_rising") else None,
            "OBV bullish — volume confirming price direction" if reasons.get("obv_bullish") else None,
        ] if c is not None][:3]
        if not catalysts:
            catalysts = ["AI signal + analyst consensus aligned", "Technical structure improving", "Volume trend supporting move"]

        regime = reasons.get("market_regime", "unknown")
        risk = (
            "Broad market bear regime active — higher false-signal rate; reduce size"
            if regime == "bear"
            else f"Earnings in {days_to_earnings}d — binary event risk; consider waiting for print" if days_to_earnings and int(days_to_earnings) <= 10
            else "Broader market sell-off would override stock-specific signal regardless of fundamentals"
        )

        return {
            "entry1": entry1, "entry1_note": e1_note,
            "entry2": entry2, "entry2_note": e2_note,
            "breakout": breakout, "breakout_note": breakout_note,
            "stop": stop, "stop_note": stop_note,
            "take_profit": take_profit, "take_profit_note": tp_note,
            "catalysts": catalysts,
            "risk": risk,
            "current_price": current_price,
        }
    except Exception as exc:
        log.warning("game_plan.build_failed", symbol=symbol, error=str(exc))
        return None


def _round_step(price: float) -> float:
    """Return a sensible rounding step for a given price."""
    if price >= 1000: return 5.0
    if price >= 100:  return 0.5
    if price >= 10:   return 0.1
    if price >= 1:    return 0.05
    return 0.01


def check_signal_alerts() -> None:
    """Fire signal-change notifications when AI Signal improves AND analyst is BUY/STRONG BUY."""
    try:
        with SessionLocal() as session:
            alerts = session.execute(select(SignalAlert)).scalars().all()
            if not alerts:
                return

            symbols = list({a.symbol for a in alerts})

            # Fetch current signals (keep full payload for reasons)
            signals: dict[str, str] = {}
            signal_details: dict[str, dict] = {}
            for sym in symbols:
                try:
                    r = httpx.get(f"{_settings.signal_engine_url}/signals/{sym}", timeout=10)
                    if r.status_code == 200:
                        payload = r.json()
                        signals[sym] = payload.get("signal", "")
                        signal_details[sym] = payload
                except Exception:
                    pass

            # Fetch analyst ratings + fundamentals (earnings, insider data)
            analyst_ratings: dict[str, str] = {}
            fundamentals_cache: dict[str, dict] = {}
            for sym in symbols:
                try:
                    r = httpx.get(f"{_settings.market_data_url}/stocks/{sym}/fundamentals", timeout=10)
                    if r.status_code == 200:
                        payload = r.json()
                        analyst_ratings[sym] = (payload.get("recommendation") or "").lower()
                        fundamentals_cache[sym] = payload
                except Exception:
                    pass

            fired = 0
            for alert in alerts:
                current = signals.get(alert.symbol)
                if not current:
                    continue

                prev = alert.last_signal

                # Always update tracked signal, even if we don't fire
                if prev == current:
                    continue

                is_bullish = (prev, current) in _BULLISH_TRANSITIONS
                is_bearish = (prev, current) in _BEARISH_TRANSITIONS
                analyst_ok = analyst_ratings.get(alert.symbol, "") in _BULLISH_ANALYST

                alert.last_signal = current  # update regardless

                # Bullish transitions require bullish analyst consensus.
                # Bearish transitions (exit warnings) fire regardless of analyst.
                if not is_bullish and not is_bearish:
                    continue
                if is_bullish and not analyst_ok:
                    continue

                # Build game plan only for BUY transitions
                game_plan = None
                if current == "BUY":
                    game_plan = _build_game_plan(
                        alert.symbol,
                        signal_details.get(alert.symbol, {}),
                        fundamentals_cache.get(alert.symbol),
                    )

                email_ok = send_signal_alert_email(
                    to=alert.email or "",
                    symbol=alert.symbol,
                    prev_signal=prev,
                    new_signal=current,
                    analyst=analyst_ratings.get(alert.symbol, "buy"),
                    signal_data=signal_details.get(alert.symbol, {}),
                    fundamentals=fundamentals_cache.get(alert.symbol),
                    game_plan=game_plan,
                )
                if email_ok:
                    fired += 1
                    log.info("signal_alert.fired", symbol=alert.symbol, prev=prev, current=current)

            session.commit()
            if fired:
                log.info("signal_alert.check_done", fired=fired)
    except Exception as exc:
        log.error("signal_alert.check_error", error=str(exc))


def check_price_alerts() -> None:
    """Check all untriggered alerts against latest live prices and fire emails."""
    try:
        import yfinance as yf
        with SessionLocal() as session:
            alerts = session.execute(
                select(PriceAlert).where(PriceAlert.triggered.is_(False))
            ).scalars().all()
            if not alerts:
                return

            # Fetch live prices for all unique symbols at once
            symbols = list({a.symbol for a in alerts})
            tickers = yf.Tickers(" ".join(symbols))
            prices: dict[str, float] = {}
            for sym in symbols:
                try:
                    p = tickers.tickers[sym].fast_info.last_price
                    if p:
                        prices[sym] = float(p)
                except Exception:
                    pass

            fired = 0
            for alert in alerts:
                price = prices.get(alert.symbol)
                if price is None:
                    continue
                should_trigger = (
                    (alert.condition.value == "above" and price >= alert.threshold) or
                    (alert.condition.value == "below" and price <= alert.threshold)
                )
                if not should_trigger:
                    continue

                alert.triggered = True
                alert.triggered_at = datetime.utcnow()
                fired += 1
                log.info("alert.triggered", symbol=alert.symbol, price=price, threshold=alert.threshold)

                if alert.email:
                    email_ok = send_price_alert_email(
                        to=alert.email,
                        symbol=alert.symbol,
                        condition=alert.condition.value,
                        threshold=alert.threshold,
                        price=price,
                        note=alert.note,
                    )
                    if not email_ok:
                        log.warning("alert.email_failed", symbol=alert.symbol, email=alert.email)

            session.commit()
            if fired:
                log.info("alert.check_done", fired=fired, checked=len(alerts))
    except Exception as exc:
        log.error("alert.check_error", error=str(exc))


def check_technical_alerts() -> None:
    """Check EMA crossover and 52-week high/low alerts using DB price history.

    Runs after each market refresh (when fresh daily bars are ingested).
    EMA period is stored in the threshold field (20, 50, or 200).
    52-week conditions store 0 in threshold.
    """
    import pandas as pd

    _TECHNICAL = {
        AlertCondition.CROSS_ABOVE_EMA,
        AlertCondition.CROSS_BELOW_EMA,
        AlertCondition.NEW_52WK_HIGH,
        AlertCondition.NEW_52WK_LOW,
        AlertCondition.GOLDEN_CROSS,
        AlertCondition.DEATH_CROSS,
    }

    try:
        with SessionLocal() as session:
            alerts = session.execute(
                select(PriceAlert).where(
                    PriceAlert.triggered.is_(False),
                    PriceAlert.condition.in_(_TECHNICAL),
                )
            ).scalars().all()
            if not alerts:
                return

            # Fetch 260 bars per unique symbol (enough for EMA200 + 52-week)
            symbols = list({a.symbol for a in alerts})
            prices_by_sym: dict[str, pd.Series] = {}
            for sym in symbols:
                try:
                    stock = session.execute(
                        select(Stock).where(Stock.symbol == sym)
                    ).scalar_one_or_none()
                    if not stock:
                        continue
                    rows = session.execute(
                        select(Price.ts, Price.close)
                        .where(Price.stock_id == stock.id)
                        .order_by(Price.ts.asc())
                        .limit(260)
                    ).all()
                    if len(rows) < 3:
                        continue
                    prices_by_sym[sym] = pd.Series(
                        [float(r.close) for r in rows]
                    )
                except Exception as exc:
                    log.warning("tech_alert.price_error", symbol=sym, error=str(exc))

            fired = 0
            for alert in alerts:
                close = prices_by_sym.get(alert.symbol)
                if close is None:
                    continue
                cond = alert.condition

                try:
                    if cond in (AlertCondition.CROSS_ABOVE_EMA, AlertCondition.CROSS_BELOW_EMA):
                        period = int(alert.threshold)  # 20, 50, or 200
                        if len(close) < period:
                            continue
                        ema = close.ewm(span=period, adjust=False).mean()
                        prev_above = close.iloc[-2] > ema.iloc[-2]
                        curr_above = close.iloc[-1] > ema.iloc[-1]
                        crossed = (
                            (cond == AlertCondition.CROSS_ABOVE_EMA and not prev_above and curr_above) or
                            (cond == AlertCondition.CROSS_BELOW_EMA and prev_above and not curr_above)
                        )
                        if not crossed:
                            continue
                        direction = "crossed above" if cond == AlertCondition.CROSS_ABOVE_EMA else "crossed below"
                        cond_label = f"{direction} EMA{period} ({ema.iloc[-1]:.2f})"
                        threshold_val = float(ema.iloc[-1])

                    elif cond == AlertCondition.NEW_52WK_HIGH:
                        if len(close) < 2:
                            continue
                        high_52 = float(close.iloc[:-1].tail(251).max())
                        if float(close.iloc[-1]) <= high_52:
                            continue
                        cond_label = f"hit a new 52-week high (prev high {high_52:.2f})"
                        threshold_val = high_52

                    elif cond == AlertCondition.NEW_52WK_LOW:
                        if len(close) < 2:
                            continue
                        low_52 = float(close.iloc[:-1].tail(251).min())
                        if float(close.iloc[-1]) >= low_52:
                            continue
                        cond_label = f"hit a new 52-week low (prev low {low_52:.2f})"
                        threshold_val = low_52

                    elif cond in (AlertCondition.GOLDEN_CROSS, AlertCondition.DEATH_CROSS):
                        if len(close) < 200:
                            continue
                        ema50 = close.ewm(span=50, adjust=False).mean()
                        ema200 = close.ewm(span=200, adjust=False).mean()
                        prev_above = ema50.iloc[-2] > ema200.iloc[-2]
                        curr_above = ema50.iloc[-1] > ema200.iloc[-1]
                        if cond == AlertCondition.GOLDEN_CROSS:
                            if prev_above or not curr_above:
                                continue
                            cond_label = f"Golden Cross — EMA50 ({ema50.iloc[-1]:.2f}) crossed above EMA200 ({ema200.iloc[-1]:.2f})"
                        else:
                            if not prev_above or curr_above:
                                continue
                            cond_label = f"Death Cross — EMA50 ({ema50.iloc[-1]:.2f}) crossed below EMA200 ({ema200.iloc[-1]:.2f})"
                        threshold_val = float(ema50.iloc[-1])

                    else:
                        continue

                    alert.triggered = True
                    alert.triggered_at = datetime.now(timezone.utc)
                    fired += 1
                    log.info("tech_alert.triggered", symbol=alert.symbol, condition=cond_label)

                    if alert.email:
                        send_price_alert_email(
                            to=alert.email,
                            symbol=alert.symbol,
                            condition=cond_label,
                            threshold=threshold_val,
                            price=float(close.iloc[-1]),
                            note=alert.note,
                        )

                except Exception as exc:
                    log.warning("tech_alert.check_error", symbol=alert.symbol, error=str(exc))

            session.commit()
            if fired:
                log.info("tech_alert.check_done", fired=fired)

    except Exception as exc:
        log.error("tech_alert.error", error=str(exc))


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")

    # ── US Market (America/New_York — DST handled automatically) ────────────
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_pre_open", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=10, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_1", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=12, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_2", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=14, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_3", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_post_close", replace_existing=True,
    )

    # ── HK Market (Asia/Hong_Kong — UTC+8, no DST) ──────────────────────────
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_pre_open", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=10, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_1", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=14, minute=15, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_2", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_3", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_post_close", replace_existing=True,
    )

    # ── Price alert checker — every minute ──────────────────────────────────
    _scheduler.add_job(
        check_price_alerts,
        "interval",
        minutes=1,
        id="price_alert_check",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("scheduler.started", jobs=11)
