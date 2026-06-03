"""Market-aware scheduler — refreshes prices, rankings, signals, and ML models
around trading hours for US (NYSE/NASDAQ) and HK (HKEX) markets.

Schedule overview
─────────────────
Three phases per market day, plus a weekly deep-clean:

  Open burst   — every 5 min for 20 min around the open.  Catches gap opens,
                 early momentum, and first-bar signal updates.

  Regular hrs  — every 10 min through the session.  Prices, rankings,22
                 momentum, and signals are all pure local math (TA + XGBoost,
                 no external API cost), so 10-min cadence is safe and free.

  Close burst  — every 5 min for 45 min around the close.  Ensures the final
                 bar is captured as it settles, and signals reflect end-of-day
                 momentum.

  Post-close   — one shot after the final bar is confirmed; also triggers the
                 nightly ML retrain so tomorrow's signals use fresh weights.

  Weekly full refresh (Sunday 14:00 PST) — force re-ingests 3 years of daily
                 bars for all active stocks before the HK Monday open.  Clears
                 any yfinance data drift that accumulates across the week.
                 After ingestion + signals, triggers one Optuna tune_all run
                 (60 trials per symbol, ~2–4 h) so Monday's signals use the
                 best per-symbol hyperparams.

Detailed times (all times local to the market timezone; DST handled automatically)
──────────────────────────────────────────────────────────────────────────────────

  US (America/New_York):
    09:25 09:30 09:35 09:40 09:45           open burst   (every 5 min)
    10:00 10:10 10:20 10:30 10:40 10:50
    11:00 11:10 11:20 11:30 11:40 11:50
    12:00 12:10 12:20 12:30 12:40 12:50
    13:00 13:10 13:20 13:30 13:40 13:50
    14:00 14:10 14:20 14:30 14:40 14:50
    15:00                                   regular hrs  (every 10 min)
    15:30 15:35 15:40 15:45 15:50 15:55
    16:00 16:05 16:10 16:15                 close burst  (every 5 min)
    16:30                                   post-close   (+ ML retrain)

  HK (Asia/Hong_Kong, UTC+8, no DST):
    09:25 09:30 09:35 09:40 09:45           open burst   (every 5 min)
    10:00 10:10 10:20 10:30 10:40 10:50
    11:00 11:10 11:20 11:30 11:40 11:50
    12:00 12:10 12:20 12:30 12:40 12:50
    13:00 13:10 13:20 13:30 13:40 13:50
    14:00 14:10 14:20 14:30 14:40 14:50
    15:00                                   regular hrs  (every 10 min)
    15:30 15:35 15:40 15:45 15:50 15:55
    16:00 16:05 16:10 16:15                 close burst  (every 5 min)
    16:30                                   post-close   (+ ML retrain)

  Weekly (America/Los_Angeles):
    Sunday 14:00                            full force re-ingest all stocks
    Sunday ~14:10–14:20 (after ingest)      Optuna tune_all (~2–4 h, background)

yfinance rate-limit notes
─────────────────────────
  • All ingests use yf.download(symbols_list) — one batch call regardless of
    stock count, so the effective call rate stays well under 500/day.
  • The weekly full refresh is the only job that passes force=True (deletes all
    rows then re-fetches 3 years).  Daily jobs fetch only the latest bars.
"""
from __future__ import annotations

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone

from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import AlertCondition, Price, PriceAlert, SignalAlert, SessionLocal, Stock, Watchlist, WatchlistItem

from .ingestion import ingest_universe
from .email_service import send_price_alert_email, send_signal_alert_email

log = get_logger("scheduler")
_settings = get_settings()
_scheduler: BackgroundScheduler | None = None


def _symbols_for(market: str) -> list[str]:
    """Return all active stock symbols for the given market ('US' or 'HK')."""
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.market == market)
            ).scalars()
        )


def _post(url: str, **kwargs) -> None:
    """Fire-and-forget POST to an internal service.  Logs but never raises on failure."""
    try:
        with httpx.Client(timeout=15) as client:
            client.post(url, **kwargs)
    except Exception as exc:
        log.warning("scheduler.http_error", url=url, error=str(exc))


def _refresh_market(market: str, *, post_close: bool = False) -> None:
    """Run one full refresh cycle for the given market.

    Steps (in order):
      1. ingest_universe  — fetch latest daily OHLCV bars from yfinance → DB
      2. /rankings/refresh — ranking-engine recalculates K-Scores for the market
      3. /signals/refresh  — signal-engine regenerates buy/sell signals
      4. /ml/train_all     — (post_close only) retrain ML models on the day's data
      5. check_signal_alerts / check_technical_alerts — fire any triggered alerts

    Called by every scheduled job (open burst, regular, close burst, post-close).
    post_close=True is only set by the 16:30 job after the final bar has settled.
    """
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

# Conviction thresholds — ALL five layers must clear for a BUY email to fire.
# Bearish/exit alerts bypass these — you always want the exit warning.
_MIN_CONFIDENCE  = 60.0   # AI signal confidence %
_MIN_CONFLUENCE  = 75     # combined 5-factor confluence score


def _confluence_score_full(
    signal: str,
    confidence: float,
    kscore: float,
    technical: float,
    momentum: float,
    rec_mean: float | None,
) -> int:
    """Mirror of frontend confluenceScoreFull() — 5-factor weighted score 0-100.

    Weights: AI signal×conf 30%, K-Score 25%, Analyst 20%, Technical 15%, Momentum 10%.
    rec_mean is the yfinance recommendationMean: 1.0 = Strong Buy, 5.0 = Sell.
    """
    ai_dir = 100 if signal == "BUY" else 50 if signal == "HOLD" else 25 if signal == "WAIT" else 0
    ai = ai_dir * confidence / 100
    analyst = max(0.0, min(100.0, (5.0 - rec_mean) / 4.0 * 100.0)) if rec_mean is not None else 50.0
    return round(
        ai         * 0.30 +
        kscore     * 0.25 +
        analyst    * 0.20 +
        technical  * 0.15 +
        momentum   * 0.10,
    )


def _is_conviction_buy(signal_data: dict, kscore: float | None = None) -> tuple[bool, list[str], list[str]]:
    """Check all conviction layers for a BUY signal across all 4 framework layers.

    Returns (all_passed, passed_layers, failed_layers).

    Layer 1 — Fundamental filter   : Analyst bullish (checked separately before call)
    Layer 2 — Conviction score     : K-Score ≥ 55
    Layer 3 — Timing trigger       : AI Signal = BUY (checked separately)
    Layer 4 — Technical confirmation:
        4a. Uptrend structure       — SMA50 > SMA200 AND price > SMA50
        4b. Entry timing            — RSI 45-65 AND Stoch RSI recovering from oversold
        4c. MACD momentum           — histogram positive+rising OR zero-line crossover
        4d. Volume confirms         — OBV bullish
        4e. Trend has real strength — ADX > 25 (signals reliable only in trending market)
    Layer 5 — ML confirms TA       : ML probability > 70%

    Disqualifiers (false-BUY flags from FEATURES.md — block even if all layers pass):
        • Bearish RSI divergence (price rising but momentum fading)
        • Stoch RSI overbought (RSI itself overextended)
    """
    reasons = signal_data.get("reasons") or {}
    passed: list[str] = []
    failed: list[str] = []

    # Layer 2 — K-Score conviction (≥ 55 = positive territory)
    if kscore is not None:
        if kscore >= 55:
            passed.append(f"K-Score: {kscore:.0f} — conviction positive")
        else:
            failed.append(f"K-Score {kscore:.0f} below 55 — weak fundamental/momentum case")

    # Layer 4a — Clean uptrend structure
    if reasons.get("sma50_above_sma200") and reasons.get("trend_above_sma50"):
        passed.append("Uptrend: SMA50 > SMA200, price > SMA50")
    else:
        failed.append("Uptrend structure not aligned (SMA50/SMA200/price)")

    # Layer 4b — Entry timing: dip bought, not overextended
    rsi = reasons.get("rsi")
    stoch_k = float(reasons.get("stoch_rsi_k") or 50)
    stoch_cross_up = bool(reasons.get("stoch_rsi_cross_up"))
    stoch_oversold = bool(reasons.get("stoch_rsi_oversold"))
    rsi_ok = rsi is not None and 45.0 <= float(rsi) <= 65.0
    stoch_ok = stoch_cross_up or (stoch_oversold and stoch_k < 60)
    if rsi_ok and stoch_ok:
        passed.append(f"Entry timing: RSI {float(rsi):.0f}, Stoch RSI recovering from oversold")
    else:
        parts = []
        if not rsi_ok:
            parts.append(f"RSI {float(rsi):.0f} outside 45-65" if rsi is not None else "RSI unavailable")
        if not stoch_ok:
            parts.append("Stoch RSI not recovering from oversold")
        failed.append("Entry timing: " + "; ".join(parts))

    # Layer 4c — MACD momentum confirmed
    macd_hist = float(reasons.get("macd_hist") or 0)
    macd_rising = bool(reasons.get("macd_rising"))
    macd_zero_cross = bool(reasons.get("macd_zero_cross_up"))
    if (macd_hist > 0 and macd_rising) or macd_zero_cross:
        passed.append("MACD: histogram positive+rising" if not macd_zero_cross else "MACD: zero-line crossover")
    else:
        failed.append("MACD: momentum not confirmed (histogram negative or falling)")

    # Layer 4d — Volume confirms direction
    if reasons.get("obv_bullish"):
        passed.append("OBV: volume confirming price direction")
    else:
        failed.append("OBV: volume not confirming direction")

    # Layer 4e — ADX: trend has real strength (signals unreliable in choppy market)
    if reasons.get("adx_trending"):
        adx = reasons.get("adx", 0)
        passed.append(f"ADX {float(adx):.0f}: trend confirmed, signals reliable")
    else:
        adx = reasons.get("adx", 0)
        failed.append(f"ADX {float(adx):.0f} < 25: market choppy, signals unreliable")

    # Layer 5 — ML model agrees with TA
    ml_prob = reasons.get("ml_probability")
    if ml_prob is not None and float(ml_prob) > 0.70:
        passed.append(f"ML: {float(ml_prob) * 100:.0f}% bullish probability")
    else:
        prob_str = f"{float(ml_prob) * 100:.0f}%" if ml_prob is not None else "unavailable"
        failed.append(f"ML probability {prob_str} (need >70%)")

    # Disqualifiers — false-BUY flags that block regardless of layer scores
    if reasons.get("rsi_divergence") == "bearish":
        failed.append("Bearish RSI divergence: price rising but momentum fading — high false-BUY risk")
    if bool(reasons.get("stoch_rsi_overbought")):
        failed.append("Stoch RSI overbought: RSI itself overextended — pullback risk elevated")

    return len(failed) == 0, passed, failed


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
    """Fire conviction BUY alerts when all 5 layers align; fire exit warnings unconditionally.

    Five-layer conviction gate (BUY transitions only):
      1. Signal transitions to BUY
      2. AI confidence >= 60%
      3. Analyst consensus = buy / strong_buy / outperform
      4+5. K-Score + Technical + Momentum sub-scores via confluence >= 75

    Bearish/exit transitions (BUY→HOLD/WAIT/SELL) bypass the gate — exit
    warnings are always sent regardless of scores.
    """
    try:
        with SessionLocal() as session:
            alerts = session.execute(select(SignalAlert)).scalars().all()
            if not alerts:
                return

            symbols = list({a.symbol for a in alerts})

            # Build (user_id, symbol) → trading_style map from watchlists with a style override
            symbol_user_style: dict[tuple[int, str], str] = {}
            try:
                rows = session.execute(
                    select(Stock.symbol, Watchlist.user_id, Watchlist.trading_style)
                    .join(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
                    .join(Stock, WatchlistItem.stock_id == Stock.id)
                    .where(Watchlist.trading_style.isnot(None))
                ).all()
                for sym, uid, style in rows:
                    symbol_user_style[(uid, sym)] = style
            except Exception as exc:
                log.warning("signal_alert.style_lookup_failed", error=str(exc))

            # Fetch current signals per unique (symbol, style) pair
            signals: dict[tuple[str, str], str] = {}
            signal_details: dict[tuple[str, str], dict] = {}
            style_sym_pairs = {
                (a.symbol, symbol_user_style.get((a.user_id, a.symbol), "SWING"))
                for a in alerts
            }
            for sym, style in style_sym_pairs:
                try:
                    r = httpx.get(
                        f"{_settings.signal_engine_url}/signals/{sym}",
                        params={"style": style}, timeout=10,
                    )
                    if r.status_code == 200:
                        payload = r.json()
                        signals[(sym, style)] = payload.get("signal", "")
                        signal_details[(sym, style)] = payload
                except Exception:
                    pass

            # Fetch analyst ratings + fundamentals (rec_mean, earnings, insider data)
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

            # Fetch K-Scores in one bulk call for Layer 2 conviction check
            kscores: dict[str, float] = {}
            try:
                r = httpx.get(f"{_settings.ranking_engine_url}/rankings", timeout=15)
                if r.status_code == 200:
                    for row in r.json().get("rankings", []):
                        if row.get("score") is not None:
                            kscores[row["symbol"]] = float(row["score"])
            except Exception:
                pass

            fired = 0
            for alert in alerts:
                style = symbol_user_style.get((alert.user_id, alert.symbol), "SWING")
                key = (alert.symbol, style)
                current = signals.get(key)
                if not current:
                    continue

                prev = alert.last_signal

                if prev == current:
                    continue

                is_bullish = (prev, current) in _BULLISH_TRANSITIONS
                is_bearish = (prev, current) in _BEARISH_TRANSITIONS

                alert.last_signal = current  # update regardless of whether we fire

                if not is_bullish and not is_bearish:
                    continue

                conviction_passed: list[str] | None = None
                if is_bullish:
                    sig_data = signal_details.get(key) or {}
                    confidence = float(sig_data.get("confidence") or 0)

                    if current == "BUY":
                        # Full 4-layer conviction gate
                        all_pass, passed, failed = _is_conviction_buy(
                            sig_data, kscore=kscores.get(alert.symbol)
                        )
                        if not all_pass:
                            log.info(
                                "signal_alert.skipped", symbol=alert.symbol,
                                reason="conviction_layers_failed", failed=failed,
                            )
                            continue
                        conviction_passed = passed
                        log.info(
                            "signal_alert.conviction_met", symbol=alert.symbol,
                            passed=passed,
                        )
                    else:
                        # Non-BUY bullish improvement (e.g. WAIT→HOLD) — lighter gate:
                        # analyst bullish + minimum confidence
                        analyst_ok = analyst_ratings.get(alert.symbol, "") in _BULLISH_ANALYST
                        if not analyst_ok or confidence < _MIN_CONFIDENCE:
                            log.info(
                                "signal_alert.skipped", symbol=alert.symbol,
                                reason="analyst_or_confidence",
                                analyst=analyst_ratings.get(alert.symbol, ""),
                                confidence=confidence,
                            )
                            continue

                # Build game plan for BUY transitions
                game_plan = None
                if current == "BUY":
                    game_plan = _build_game_plan(
                        alert.symbol,
                        signal_details.get(key, {}),
                        fundamentals_cache.get(alert.symbol),
                    )

                email_ok = send_signal_alert_email(
                    to=alert.email or "",
                    symbol=alert.symbol,
                    prev_signal=prev,
                    new_signal=current,
                    analyst=analyst_ratings.get(alert.symbol, "buy"),
                    signal_data=signal_details.get(key, {}),
                    fundamentals=fundamentals_cache.get(alert.symbol),
                    game_plan=game_plan,
                    conviction_layers=conviction_passed,
                    horizon=style,
                )
                if email_ok:
                    fired += 1
                    log.info("signal_alert.fired", symbol=alert.symbol, prev=prev, current=current, style=style)

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
                alert.triggered_at = datetime.now(timezone.utc)
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
                        high_52 = float(close.iloc[:-1].tail(252).max())
                        if float(close.iloc[-1]) <= high_52:
                            continue
                        cond_label = f"hit a new 52-week high (prev high {high_52:.2f})"
                        threshold_val = high_52

                    elif cond == AlertCondition.NEW_52WK_LOW:
                        if len(close) < 2:
                            continue
                        low_52 = float(close.iloc[:-1].tail(252).min())
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


def _weekly_full_refresh() -> None:
    """Force re-ingest 3 years of daily bars for every active stock.

    Runs Sunday 14:00 PST — roughly 19 hours before HK Monday open — so both
    markets start the week with clean, gap-free price history.  Triggers a
    full rankings + signals refresh once ingestion completes, then kicks off
    the Optuna tune_all job so Monday's signals use freshly tuned hyperparams.
    tune_all runs in the background inside the ml-prediction container (~2–4 h).
    """
    all_symbols = _symbols_for("US") + _symbols_for("HK")
    if not all_symbols:
        log.info("scheduler.weekly_refresh.skip", reason="no_symbols")
        return
    log.info("scheduler.weekly_refresh_start", count=len(all_symbols))
    try:
        ingest_universe(all_symbols, "1d", force=True)
        _post(f"{_settings.ranking_engine_url}/rankings/refresh")
        _post(f"{_settings.signal_engine_url}/signals/refresh")
        log.info("scheduler.weekly_refresh_done", count=len(all_symbols))
    except Exception as exc:
        log.error("scheduler.weekly_refresh_failed", error=str(exc))

    # Kick off Optuna hyperparameter tuning for all symbols.
    # Runs as a background task in ml-prediction — returns immediately, tunes for ~2–4 h.
    # Best params are saved per-symbol JSON and used by all subsequent daily retrains.
    log.info("scheduler.tune_all_start")
    _post(f"{_settings.ml_prediction_url}/ml/tune_all")


def _refresh_5m(market: str) -> None:
    """Ingest the latest 5-minute bars for all active stocks in the given market.

    Runs every 5 minutes during regular market hours so the intraday chart on
    the stock detail page always shows up-to-date candles.  Only fetches bars
    since the last stored bar — incremental, not a full re-download.
    Rankings and signals are NOT updated (they use daily bars only).
    """
    symbols = _symbols_for(market)
    if not symbols:
        return
    log.info("scheduler.5m_ingest_start", market=market, count=len(symbols))
    try:
        ingest_universe(symbols, "5m")
        log.info("scheduler.5m_ingest_done", market=market, count=len(symbols))
    except Exception as exc:
        log.error("scheduler.5m_ingest_failed", market=market, error=str(exc))


def start_scheduler() -> None:
    """Register all APScheduler jobs and start the background scheduler.

    Idempotent — safe to call multiple times; only the first call has any effect.
    All jobs are registered with replace_existing=True so a hot-reload
    (docker restart) won't create duplicate jobs.

    Schedule (per market):
      - Open burst  (9:25–9:45):   every 5 min  — prices + rankings + signals
      - Regular hrs (10:00–15:00): every 10 min — prices + rankings + signals
      - Close burst (15:30–16:15): every 5 min  — prices + rankings + signals
      - Post-close  (16:30):       once         — above + ML retrain
      - 5m ingest   (9:30–16:00): every 5 min  — intraday bars only (US + HK)
      - Weekly full refresh (Sun 16:00 PST): force re-ingest 3 years
        → then tune_all (Optuna, 60 trials/symbol, ~2–4 h, background)

    Signal and momentum are pure local math (TA + XGBoost), no external API
    cost, so refreshing every 10 min during regular hours is safe and free.
    ML retrain runs only post-close — retraining on intraday data has no value
    since the model learns from daily bar outcomes.
    Hyperparameter tuning runs once on Sunday so each symbol's best params are
    ready for the week ahead; subsequent daily retrains pick them up automatically.

    Job count: 4 US + 4 HK + 2 5m intraday + 1 weekly full refresh + tune_all
               + 1 price alert checker = 12.
    """
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")

    # ── US Market (America/New_York — DST handled automatically) ────────────

    # Open burst: 9:25–9:45 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=9, minute="25,30,35,40,45", day_of_week="mon-fri", timezone="America/New_York"),
        id="us_open_burst", replace_existing=True,
    )
    # Regular hours: every 10 min 10:00–15:00
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        OrTrigger([
            CronTrigger(hour="10,11,12,13,14", minute="0,10,20,30,40,50", day_of_week="mon-fri", timezone="America/New_York"),
            CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        ]),
        id="us_intra", replace_existing=True,
    )
    # Close burst: 15:30–16:15 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        OrTrigger([
            CronTrigger(hour=15, minute="30,35,40,45,50,55", day_of_week="mon-fri", timezone="America/New_York"),
            CronTrigger(hour=16, minute="0,5,10,15", day_of_week="mon-fri", timezone="America/New_York"),
        ]),
        id="us_close_burst", replace_existing=True,
    )
    # Post-close: final bar confirmed + ML retrain
    _scheduler.add_job(
        lambda: _refresh_market("US", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_post_close", replace_existing=True,
    )

    # ── HK Market (Asia/Hong_Kong — UTC+8, no DST) ──────────────────────────

    # Open burst: 9:25–9:45 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=9, minute="25,30,35,40,45", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_open_burst", replace_existing=True,
    )
    # Regular hours: every 10 min 10:00–15:00
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        OrTrigger([
            CronTrigger(hour="10,11,12,13,14", minute="0,10,20,30,40,50", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
            CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        ]),
        id="hk_intra", replace_existing=True,
    )
    # Close burst: 15:30–16:15 every 5 min (HK market closes 16:00, bar settles by 16:15)
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        OrTrigger([
            CronTrigger(hour=15, minute="30,35,40,45,50,55", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
            CronTrigger(hour=16, minute="0,5,10,15", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        ]),
        id="hk_close_burst", replace_existing=True,
    )
    # Post-close: final bar confirmed + ML retrain
    _scheduler.add_job(
        lambda: _refresh_market("HK", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_post_close", replace_existing=True,
    )

    # ── Weekly full refresh — Sunday 16:00 PST, before HK Monday open ───────
    _scheduler.add_job(
        _weekly_full_refresh,
        CronTrigger(day_of_week="sun", hour=14, minute=0, timezone="America/Los_Angeles"),
        id="weekly_full_refresh", replace_existing=True,
    )

    # ── 5-minute intraday bars — US market hours ────────────────────────────
    _scheduler.add_job(
        lambda: _refresh_5m("US"),
        CronTrigger(
            hour="9,10,11,12,13,14,15",
            minute="30,35,40,45,50,55,0,5,10,15,20,25",
            day_of_week="mon-fri",
            timezone="America/New_York",
        ),
        id="us_5m_intraday", replace_existing=True,
    )

    # ── 5-minute intraday bars — HK market hours ────────────────────────────
    _scheduler.add_job(
        lambda: _refresh_5m("HK"),
        CronTrigger(
            hour="9,10,11,12,13,14,15",
            minute="30,35,40,45,50,55,0,5,10,15,20,25",
            day_of_week="mon-fri",
            timezone="Asia/Hong_Kong",
        ),
        id="hk_5m_intraday", replace_existing=True,
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
    log.info("scheduler.started", jobs=12)
