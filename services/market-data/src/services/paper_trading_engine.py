"""WF-2: Autonomous paper-trading engine.

Runs every 5–10 minutes during market hours (hooked into scheduler._refresh_market).
Behaves like a disciplined human trader:
  1. Scans fresh BUY signals for the configured style (default: GROWTH).
  2. Asks "Is now actually a good time to enter?" via _should_enter() scoring.
  3. If yes, simulates a buy at the current live price.
  4. Every cycle, monitors all open positions for stop breach, target hit,
     signal deterioration, and trailing-stop updates.
  5. Post-close: snapshots the equity curve vs SPY/QQQ/HSI benchmarks.

Style differences baked in per trading_style config:
  GROWTH  — RSI 38-85 valid, wider stop (-12%), big target (+35%), 60-day time stop,
             ATR trail ×2.0, SMA20>SMA50 sufficient, sector ETF exempt.
  SWING   — RSI 30-65, stop (-5.5%), target (+12%), 20-day time stop, ATR trail ×1.5.
  LONG    — RSI 30-65, stop (-10%), target (+25%), 60-day time stop, ATR trail ×2.0.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from db import (
    PaperEquityCurve, PaperPortfolio, PaperTrade,
    Ranking, SessionLocal, Signal, Stock, Watchlist, WatchlistItem,
)
from sqlalchemy import desc, func, select

log = logging.getLogger("paper_trading_engine")

# ── Default portfolio config ──────────────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "trading_style":        "GROWTH",  # which signal horizon to trade
    "max_positions":        10,
    "max_sector_pct":       0.30,      # max 30% in one sector
    "risk_per_trade_pct":   0.01,      # risk 1% of equity per trade
    "max_position_pct":     0.10,      # cap any single position at 10% of equity
    "min_confidence":       62.0,      # Signal.confidence threshold
    "min_kscore":           48.0,      # Ranking.score threshold
    "min_rr_ratio":         2.0,       # minimum risk:reward at entry
    "min_entry_score":      3,         # _should_enter() score threshold
    "max_hold_days":        60,        # time-stop (GROWTH / LONG)
    "trail_atr_mult":       2.0,       # trailing stop = highest_price - ATR × mult
    "trail_trigger_pct":    0.05,      # start trailing after +5% gain
    "breakeven_trigger_pct":0.03,      # move stop to breakeven after +3% gain
    "wait_exit_days":       5,         # exit if signal stays WAIT this many days
    "enabled":              True,
}

# Mirrors scheduler._STYLE_PARAMS — inlined here to avoid circular import.
_STYLE_PARAMS: dict[str, dict] = {
    "SHORT":  {"entry1_pct": 0.995, "entry2_pct": 0.985, "breakout_pct": 1.010,
               "stop_pct": 0.970, "default_tp_pct": 1.05},
    "SWING":  {"entry1_pct": 0.985, "entry2_pct": 0.965, "breakout_pct": 1.020,
               "stop_pct": 0.945, "default_tp_pct": 1.12},
    "LONG":   {"entry1_pct": 0.980, "entry2_pct": 0.950, "breakout_pct": 1.030,
               "stop_pct": 0.900, "default_tp_pct": 1.25},
    "GROWTH": {"entry1_pct": 0.975, "entry2_pct": 0.940, "breakout_pct": 1.035,
               "stop_pct": 0.880, "default_tp_pct": 1.35},
}


def _round_step(price: float) -> float:
    if price >= 1000: return 5.0
    if price >= 100:  return 0.5
    if price >= 10:   return 0.1
    if price >= 1:    return 0.05
    return 0.01


# Style-specific overrides applied on top of defaults
_STYLE_OVERRIDES: dict[str, dict] = {
    "GROWTH": {
        "max_hold_days": 60, "trail_atr_mult": 2.0,
        "trail_trigger_pct": 0.05, "breakeven_trigger_pct": 0.03,
        "wait_exit_days": 5, "min_confidence": 62.0, "min_kscore": 48.0,
    },
    "SWING": {
        "max_hold_days": 20, "trail_atr_mult": 1.5,
        "trail_trigger_pct": 0.04, "breakeven_trigger_pct": 0.02,
        "wait_exit_days": 3, "min_confidence": 65.0, "min_kscore": 52.0,
    },
    "LONG": {
        "max_hold_days": 90, "trail_atr_mult": 2.0,
        "trail_trigger_pct": 0.06, "breakeven_trigger_pct": 0.04,
        "wait_exit_days": 7, "min_confidence": 60.0, "min_kscore": 50.0,
    },
}


# ── ATR helper ────────────────────────────────────────────────────────────────

def _compute_atr(symbol: str, period: int = 14) -> float | None:
    """Compute ATR(14) from the last 30 daily closes for a symbol."""
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="40d")
        if len(hist) < period + 1:
            return None
        high = hist["High"].astype(float)
        low  = hist["Low"].astype(float)
        close = hist["Close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])
    except Exception:
        return None


# ── Live price fetch ──────────────────────────────────────────────────────────

def _fetch_live_prices(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch live prices via yfinance fast_info (same method as price alerts)."""
    if not symbols:
        return {}
    prices: dict[str, float] = {}
    try:
        import yfinance as yf
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                p = tickers.tickers[sym].fast_info.last_price
                if p and float(p) > 0:
                    prices[sym] = float(p)
            except Exception:
                pass
    except Exception as exc:
        log.warning("paper.live_price_fetch_failed", error=str(exc))
    return prices


# ── Entry qualifier ───────────────────────────────────────────────────────────

def _should_enter(
    symbol: str,
    signal_data: dict,
    live_price: float,
    game_plan: dict,
    cfg: dict,
) -> tuple[bool, int, list[str]]:
    """Score current conditions to decide if NOW is a good time to enter.

    Returns (should_enter, score, notes_list).
    Score >= cfg['min_entry_score'] → ENTER.
    Hard-reject conditions return (False, -99, [reason]) regardless of score.
    """
    style = cfg.get("trading_style", "GROWTH")
    reasons = signal_data.get("reasons") or {}
    notes: list[str] = []
    score = 0

    entry1     = game_plan.get("entry1", live_price * 0.975)
    entry2     = game_plan.get("entry2", live_price * 0.940)
    breakout   = game_plan.get("breakout", live_price * 1.035)
    stop       = game_plan.get("stop", live_price * 0.880)
    take_profit = game_plan.get("take_profit", live_price * 1.35)

    # ── Hard rejects (override any positive score) ────────────────────────────

    # R:R check at current live price
    rr = (take_profit - live_price) / (live_price - stop) if (live_price - stop) > 0.001 else 0
    if rr < cfg.get("min_rr_ratio", 2.0):
        return False, -99, [f"R:R {rr:.1f}:1 below minimum {cfg['min_rr_ratio']:.1f}:1 at ${live_price:.2f}"]

    # Earnings too close — binary event risk
    dte = reasons.get("days_to_earnings")
    if dte is not None and int(dte) <= 5:
        return False, -99, [f"Earnings in {dte} days — binary event risk; skip"]

    # ── Price zone (where is price relative to the game plan?) ───────────────

    if entry2 <= live_price <= breakout:
        score += 3
        notes.append(f"Price ${live_price:.2f} in optimal entry zone (${entry2:.2f}–${breakout:.2f})")
    elif live_price < entry2:
        score += 4
        notes.append(f"Price ${live_price:.2f} below entry2 ${entry2:.2f} — deep pullback, excellent R:R")
    elif breakout < live_price <= breakout * 1.03:
        score += 1
        notes.append(f"Price just above breakout (${breakout:.2f}) — momentum confirmed but chasing slightly")
    else:
        score -= 3
        notes.append(f"Price ${live_price:.2f} extended {((live_price/breakout)-1)*100:.1f}% above breakout — chasing risk")

    # ── R:R quality ──────────────────────────────────────────────────────────

    if rr >= 3.5:
        score += 2
        notes.append(f"Excellent R:R {rr:.1f}:1")
    elif rr >= 2.5:
        score += 1
        notes.append(f"Good R:R {rr:.1f}:1")
    else:
        notes.append(f"Acceptable R:R {rr:.1f}:1")

    # ── Momentum / RSI (style-aware) ─────────────────────────────────────────

    rsi = reasons.get("rsi")
    if rsi is not None:
        rsi = float(rsi)
        if style == "GROWTH":
            # For GROWTH, RSI 55-85 is momentum territory — good, not overbought
            if 55 <= rsi <= 85:
                score += 1
                notes.append(f"RSI {rsi:.0f} — momentum territory (valid for growth)")
            elif 72 <= rsi <= 85:
                score += 2  # extra credit: strong momentum
                notes.append(f"RSI {rsi:.0f} — strong momentum (growth names run hot)")
            elif rsi > 88:
                score -= 2
                notes.append(f"RSI {rsi:.0f} — potential exhaustion above 88")
            elif rsi < 38:
                score -= 1
                notes.append(f"RSI {rsi:.0f} — below growth entry minimum (38)")
        else:
            if rsi > 72:
                score -= 2
                notes.append(f"RSI {rsi:.0f} overbought — wait for cooldown")
            elif 40 <= rsi <= 65:
                score += 1
                notes.append(f"RSI {rsi:.0f} — healthy range")

    # ── MACD / momentum confirmation ─────────────────────────────────────────

    if reasons.get("macd_rising") and reasons.get("macd_zero_cross_up"):
        score += 2
        notes.append("MACD rising + zero-cross — momentum confirmed")
    elif reasons.get("macd_rising"):
        score += 1
        notes.append("MACD rising — building momentum")

    if reasons.get("obv_bullish"):
        score += 1
        notes.append("OBV bullish — volume confirming price direction")

    # ── Trend structure ───────────────────────────────────────────────────────

    if style == "GROWTH":
        # SMA20 > SMA50 sufficient for GROWTH (skip SMA50 > SMA200 requirement)
        sma_above = reasons.get("trend_above_sma50")   # reuse as proxy
        if sma_above:
            score += 1
            notes.append("Price above SMA50 — short-term uptrend intact")
    else:
        if reasons.get("sma50_above_sma200") and reasons.get("trend_above_sma50"):
            score += 2
            notes.append("SMA50>SMA200 golden-cross + price above SMA50")
        elif reasons.get("trend_above_sma50"):
            score += 1
            notes.append("Price above SMA50 — trend intact")

    # ── Market context ────────────────────────────────────────────────────────

    regime = reasons.get("market_regime", "unknown")
    if regime == "bull":
        score += 1
        notes.append("Bull market regime — macro tailwind")
    elif regime == "bear":
        score -= 2
        notes.append("Bear regime — higher false-signal rate; reduced size warranted")
    elif regime == "high_vol":
        score -= 1
        notes.append("High-vol regime — wider stop already accommodates this")

    breadth = reasons.get("breadth_pct")
    if breadth is not None:
        if float(breadth) >= 55:
            score += 1
            notes.append(f"Market breadth {float(breadth):.0f}% — broad participation healthy")
        elif float(breadth) < 40:
            score -= 1
            notes.append(f"Market breadth {float(breadth):.0f}% — broad weakness, be selective")

    # ── Sector context (GROWTH is exempt from sector headwind penalty) ────────

    if style != "GROWTH":
        if reasons.get("sector_headwind"):
            score -= 1
            notes.append("Sector ETF below SMA50 — sector headwind")
        elif reasons.get("sector_etf_above_sma50"):
            score += 1
            notes.append("Sector ETF above SMA50 — sector tailwind")

    # ── Earnings window ───────────────────────────────────────────────────────

    if dte is not None and int(dte) <= 10:
        score -= 1
        notes.append(f"Earnings in {dte} days — size conservatively")

    # ── Signal conviction ─────────────────────────────────────────────────────

    bull_prob = signal_data.get("bullish_probability") or 0.0
    confidence = signal_data.get("confidence") or 0.0
    if float(bull_prob) >= 0.72:
        score += 2
        notes.append(f"High conviction {float(bull_prob)*100:.0f}% fused probability")
    elif float(bull_prob) >= 0.62:
        score += 1
        notes.append(f"Moderate conviction {float(bull_prob)*100:.0f}% fused probability")
    if float(confidence) >= 75:
        score += 1
        notes.append(f"Confidence {float(confidence):.0f}% above high-conviction threshold")

    # ── Decision ─────────────────────────────────────────────────────────────

    should = score >= cfg.get("min_entry_score", 3)
    return should, score, notes


# ── Game plan builder ─────────────────────────────────────────────────────────

def _build_game_plan_for_style(
    symbol: str,
    style: str,
    current_price: float,
    signal_reasons: dict,
    atr: float | None,
) -> dict:
    """Derive entry/stop/target for paper trading from signal reasons + price.

    Falls back to style % defaults if ATR is unavailable.
    """
    params = _STYLE_PARAMS.get(style.upper(), _STYLE_PARAMS["SWING"])
    step = _round_step(current_price)

    entry1   = round(current_price * params["entry1_pct"]   / step) * step
    entry2   = round(current_price * params["entry2_pct"]   / step) * step
    breakout = round(current_price * params["breakout_pct"] / step) * step

    # Stop: ATR-based is more adaptive than fixed %
    if atr and atr > 0:
        stop = round((current_price - atr * (2.5 if style == "GROWTH" else 2.0)) / step) * step
        stop = max(stop, round(current_price * params["stop_pct"] / step) * step)
    else:
        stop = round(current_price * params["stop_pct"] / step) * step

    take_profit = round(current_price * params["default_tp_pct"] / step) * step

    return {
        "entry1": entry1,
        "entry2": entry2,
        "breakout": breakout,
        "stop": stop,
        "take_profit": take_profit,
        "current_price": current_price,
        "style": style,
    }


# ── Position monitor ──────────────────────────────────────────────────────────

def _monitor_positions(session, portfolio: PaperPortfolio, live_prices: dict[str, float]) -> None:
    """Check every open trade: stop breach, target hit, trailing stop, signal decay."""
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(portfolio.config.get("trading_style", "GROWTH"), {}), **portfolio.config}
    style = cfg["trading_style"]

    open_trades = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.stage == "open",
        )
    ).scalars().all()

    if not open_trades:
        return

    # Fetch latest signal for each open symbol in one query
    symbols = [t.symbol for t in open_trades]
    latest_signals: dict[str, Signal] = {}
    for sym in symbols:
        sig = session.execute(
            select(Signal)
            .join(Stock, Signal.stock_id == Stock.id)
            .where(Stock.symbol == sym, Signal.horizon == style)
            .order_by(desc(Signal.ts))
            .limit(1)
        ).scalar_one_or_none()
        if sig:
            latest_signals[sym] = sig

    now = datetime.utcnow()

    for trade in open_trades:
        live_price = live_prices.get(trade.symbol)
        if not live_price:
            continue

        trade.current_price = live_price
        if trade.highest_price is None or live_price > trade.highest_price:
            trade.highest_price = live_price

        entry  = trade.entry_price
        stop   = trade.current_stop
        target = trade.take_profit
        pnl_pct = (live_price - entry) / entry

        current_sig = latest_signals.get(trade.symbol)
        sig_type = current_sig.signal.value if current_sig else "UNKNOWN"

        exit_reason = None
        exit_notes: dict = {}

        # ── Hard exits ────────────────────────────────────────────────────────

        if live_price <= stop:
            exit_reason = "stop_hit"
            exit_notes = {
                "message": f"Stop ${stop:.2f} breached at live price ${live_price:.2f}",
                "loss_pct": round(pnl_pct * 100, 2),
            }

        elif target and live_price >= target:
            exit_reason = "target_reached"
            exit_notes = {
                "message": f"Target ${target:.2f} reached at ${live_price:.2f}",
                "gain_pct": round(pnl_pct * 100, 2),
            }

        elif sig_type == "SELL":
            exit_reason = "signal_exit"
            exit_notes = {
                "message": f"Signal downgraded to SELL at ${live_price:.2f}",
                "reasons": (current_sig.reasons or {}) if current_sig else {},
            }

        # ── Time stop ────────────────────────────────────────────────────────

        elif trade.hold_days >= cfg.get("max_hold_days", 60):
            exit_reason = "time_stop"
            exit_notes = {
                "message": f"Time stop: {trade.hold_days} days without resolution",
                "pnl_pct": round(pnl_pct * 100, 2),
            }

        # ── WAIT decay exit ───────────────────────────────────────────────────

        elif sig_type == "WAIT":
            # Count consecutive WAIT signals
            wait_count = session.execute(
                select(func.count())
                .select_from(Signal)
                .join(Stock, Signal.stock_id == Stock.id)
                .where(
                    Stock.symbol == trade.symbol,
                    Signal.horizon == style,
                    Signal.signal == "WAIT",
                    Signal.ts >= now - timedelta(days=cfg.get("wait_exit_days", 5) + 1),
                )
            ).scalar() or 0

            if wait_count >= cfg.get("wait_exit_days", 5):
                exit_reason = "momentum_exit"
                exit_notes = {
                    "message": f"Signal stuck in WAIT for {wait_count} days — momentum lost",
                    "pnl_pct": round(pnl_pct * 100, 2),
                }
            else:
                # Tighten stop to breakeven while waiting
                if live_price > entry and trade.current_stop < entry:
                    trade.current_stop = entry
                    log.info("paper.stop_to_breakeven_wait",
                             symbol=trade.symbol, stop=entry, reason="WAIT signal")

        # ── Execute exit ──────────────────────────────────────────────────────

        if exit_reason:
            pnl_dollar = (live_price - entry) * trade.shares
            trade.stage      = "closed"
            trade.exit_time  = now
            trade.exit_price = live_price
            trade.exit_reason = exit_reason
            trade.exit_reasons = exit_notes
            trade.pnl        = round(pnl_dollar, 2)
            trade.pct_return = round(pnl_pct * 100, 4)
            portfolio.current_cash += live_price * trade.shares
            log.info("paper.exit",
                     symbol=trade.symbol, reason=exit_reason,
                     pnl=round(pnl_dollar, 2), pct=round(pnl_pct * 100, 2))
            continue

        # ── Trailing stop management (still open) ─────────────────────────────

        trail_trigger = cfg.get("trail_trigger_pct", 0.05)
        be_trigger    = cfg.get("breakeven_trigger_pct", 0.03)

        if pnl_pct >= trail_trigger:
            atr = _compute_atr(trade.symbol)
            if atr:
                mult = cfg.get("trail_atr_mult", 2.0)
                new_trail = (trade.highest_price or live_price) - atr * mult
                if new_trail > trade.current_stop:
                    old = trade.current_stop
                    trade.current_stop = round(new_trail, 4)
                    log.info("paper.trail_stop_raised",
                             symbol=trade.symbol, old=round(old, 2), new=round(new_trail, 2),
                             profit_pct=round(pnl_pct * 100, 1))
        elif pnl_pct >= be_trigger and trade.current_stop < entry:
            trade.current_stop = entry
            log.info("paper.stop_to_breakeven",
                     symbol=trade.symbol, entry=entry, pct=round(pnl_pct * 100, 1))

        # Update hold_days
        days_held = (date.today() - trade.entry_date).days
        trade.hold_days = days_held


# ── Entry scanner ─────────────────────────────────────────────────────────────

def _scan_for_entries(session, portfolio: PaperPortfolio, live_prices: dict[str, float]) -> None:
    """Find fresh BUY signals and evaluate them for entry."""
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(portfolio.config.get("trading_style", "GROWTH"), {}), **portfolio.config}
    style   = cfg["trading_style"]
    now     = datetime.utcnow()
    cutoff  = now - timedelta(hours=2)   # only signals updated in last 2 hours

    # Current portfolio state
    open_count = session.execute(
        select(func.count()).select_from(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.stage == "open",
        )
    ).scalar() or 0

    if open_count >= cfg["max_positions"]:
        log.info("paper.entry_scan_skip", reason="max_positions_reached", count=open_count)
        return

    equity = _compute_equity(session, portfolio, live_prices)

    # Symbols already in open positions
    open_symbols: set[str] = set(
        r[0] for r in session.execute(
            select(Stock.symbol)
            .join(PaperTrade, PaperTrade.symbol == Stock.symbol)
            .where(PaperTrade.portfolio_id == portfolio.id, PaperTrade.stage == "open")
        ).all()
    )

    # Get GROWTH watchlist stock IDs
    growth_stock_ids: set[int] = set(
        r[0] for r in session.execute(
            select(WatchlistItem.stock_id)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.trading_style == style)
        ).all()
    )

    # Latest BUY signals for the style, updated recently
    buy_signals = session.execute(
        select(Signal, Stock, Ranking)
        .join(Stock, Signal.stock_id == Stock.id)
        .outerjoin(
            Ranking,
            (Ranking.stock_id == Stock.id) &
            (Ranking.as_of == session.execute(
                select(func.max(Ranking.as_of)).where(Ranking.stock_id == Stock.id)
            ).scalar_subquery()),
        )
        .where(
            Signal.signal == "BUY",
            Signal.horizon == style,
            Signal.confidence >= cfg["min_confidence"],
            Signal.ts >= cutoff,
            Stock.active.is_(True),
        )
        .order_by(desc(Signal.confidence))
    ).all()

    entries_made = 0
    for sig, stock, ranking in buy_signals:
        if open_count + entries_made >= cfg["max_positions"]:
            break
        if stock.symbol in open_symbols:
            continue
        # Optionally restrict to the GROWTH watchlist
        if growth_stock_ids and stock.id not in growth_stock_ids:
            continue
        # K-Score filter (if ranking available)
        if ranking and ranking.score < cfg["min_kscore"]:
            log.info("paper.skip_kscore", symbol=stock.symbol,
                     kscore=ranking.score, min=cfg["min_kscore"])
            continue

        live_price = live_prices.get(stock.symbol)
        if not live_price or live_price <= 0:
            continue

        # Sector concentration check
        if stock.sector:
            sector_value = _sector_value(session, portfolio, stock.sector, live_prices)
            if (sector_value + live_price * 100) / max(equity, 1) > cfg["max_sector_pct"]:
                log.info("paper.skip_sector_cap", symbol=stock.symbol, sector=stock.sector)
                continue

        # Build game plan
        atr = _compute_atr(stock.symbol)
        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons or {},
        }
        game_plan = _build_game_plan_for_style(
            stock.symbol, style, live_price, sig.reasons or {}, atr
        )

        # Entry qualifier: "Is now a good time?"
        should_enter, score, notes = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg
        )

        log.info("paper.entry_decision",
                 symbol=stock.symbol, should_enter=should_enter,
                 score=score, notes=notes[:2])

        if not should_enter:
            continue

        # Position sizing: risk_dollar / stop_distance = shares
        stop       = game_plan["stop"]
        take_profit = game_plan["take_profit"]
        rr = (take_profit - live_price) / max(live_price - stop, 0.001)

        risk_dollar    = equity * cfg["risk_per_trade_pct"]
        stop_distance  = live_price - stop
        if stop_distance <= 0:
            continue
        shares         = risk_dollar / stop_distance
        position_value = shares * live_price

        # Cap position at max_position_pct of equity
        max_pos = equity * cfg["max_position_pct"]
        if position_value > max_pos:
            shares = max_pos / live_price
            position_value = max_pos

        # Ensure we have the cash
        if position_value > portfolio.current_cash * 0.98:
            log.info("paper.skip_insufficient_cash",
                     symbol=stock.symbol, need=position_value,
                     have=portfolio.current_cash)
            continue

        # Simulate entry
        portfolio.current_cash -= position_value
        trade = PaperTrade(
            portfolio_id          = portfolio.id,
            symbol                = stock.symbol,
            signal_id             = sig.id,
            trading_style         = style,
            entry_date            = date.today(),
            entry_time            = now,
            entry_price           = live_price,
            shares                = round(shares, 4),
            stop_loss             = stop,
            take_profit           = take_profit,
            current_stop          = stop,
            highest_price         = live_price,
            current_price         = live_price,
            entry_score           = score,
            entry_decision_notes  = notes,
            confidence_at_entry   = sig.confidence,
            kscore_at_entry       = ranking.score if ranking else None,
            rr_ratio_at_entry     = round(rr, 2),
            market_regime_at_entry= (sig.reasons or {}).get("market_regime"),
            entry_reasons         = sig.reasons,
            stage                 = "open",
            hold_days             = 0,
        )
        session.add(trade)
        open_symbols.add(stock.symbol)
        entries_made += 1

        log.info("paper.entry",
                 symbol=stock.symbol, price=live_price,
                 shares=round(shares, 2), stop=stop,
                 target=take_profit, score=score, rr=round(rr, 2),
                 cash_remaining=round(portfolio.current_cash, 2))


# ── Equity computation ────────────────────────────────────────────────────────

def _compute_equity(session, portfolio: PaperPortfolio, live_prices: dict[str, float]) -> float:
    """Cash + market value of all open positions."""
    open_trades = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.stage == "open",
        )
    ).scalars().all()
    positions_value = sum(
        (live_prices.get(t.symbol) or t.entry_price) * t.shares
        for t in open_trades
    )
    return portfolio.current_cash + positions_value


def _sector_value(session, portfolio: PaperPortfolio, sector: str, live_prices: dict[str, float]) -> float:
    """Dollar value of open trades in the given sector."""
    rows = session.execute(
        select(PaperTrade, Stock)
        .join(Stock, PaperTrade.symbol == Stock.symbol)
        .where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.stage == "open",
            Stock.sector == sector,
        )
    ).all()
    return sum(
        (live_prices.get(t.symbol) or t.entry_price) * t.shares
        for t, _ in rows
    )


# ── Equity curve snapshot ─────────────────────────────────────────────────────

def snapshot_equity_curve(portfolio_id: int | None = None) -> None:
    """Record EOD equity + benchmark closes. Called post-close from scheduler."""
    try:
        import yfinance as yf
    except ImportError:
        return

    # Fetch benchmark closes
    bench_prices: dict[str, float | None] = {"SPY": None, "QQQ": None, "^HSI": None}
    try:
        bench_data = yf.download(list(bench_prices.keys()), period="2d",
                                 auto_adjust=True, progress=False)
        closes = bench_data["Close"] if "Close" in bench_data.columns else bench_data
        for ticker in bench_prices:
            try:
                bench_prices[ticker] = float(closes[ticker].dropna().iloc[-1])
            except Exception:
                pass
    except Exception as exc:
        log.warning("paper.benchmark_fetch_failed", error=str(exc))

    with SessionLocal() as session:
        portfolios_q = select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True))
        if portfolio_id:
            portfolios_q = portfolios_q.where(PaperPortfolio.id == portfolio_id)
        portfolios = session.execute(portfolios_q).scalars().all()

        for portfolio in portfolios:
            open_trades = session.execute(
                select(PaperTrade).where(
                    PaperTrade.portfolio_id == portfolio.id,
                    PaperTrade.stage == "open",
                )
            ).scalars().all()

            symbols = [t.symbol for t in open_trades]
            live = _fetch_live_prices(symbols) if symbols else {}

            positions_value = sum(
                (live.get(t.symbol) or t.entry_price) * t.shares
                for t in open_trades
            )
            equity = portfolio.current_cash + positions_value
            today  = date.today()

            existing = session.execute(
                select(PaperEquityCurve).where(
                    PaperEquityCurve.portfolio_id == portfolio.id,
                    PaperEquityCurve.date == today,
                )
            ).scalar_one_or_none()

            if existing:
                existing.equity = equity
                existing.cash   = portfolio.current_cash
                existing.open_positions_value = positions_value
                existing.open_positions_count = len(open_trades)
                existing.spy_close = bench_prices.get("SPY")
                existing.qqq_close = bench_prices.get("QQQ")
                existing.hsi_close = bench_prices.get("^HSI")
            else:
                session.add(PaperEquityCurve(
                    portfolio_id         = portfolio.id,
                    date                 = today,
                    equity               = equity,
                    cash                 = portfolio.current_cash,
                    open_positions_value = positions_value,
                    open_positions_count = len(open_trades),
                    spy_close            = bench_prices.get("SPY"),
                    qqq_close            = bench_prices.get("QQQ"),
                    hsi_close            = bench_prices.get("^HSI"),
                ))

            session.commit()
            log.info("paper.equity_snapshot",
                     portfolio=portfolio.name, equity=round(equity, 2),
                     cash=round(portfolio.current_cash, 2), positions=len(open_trades))


# ── Seed portfolio ─────────────────────────────────────────────────────────────

def ensure_portfolio_exists(
    name: str = "GROWTH Paper Portfolio",
    initial_capital: float = 50_000.0,
    style: str = "GROWTH",
) -> int:
    """Create the GROWTH paper portfolio if it doesn't exist yet. Returns portfolio id."""
    with SessionLocal() as session:
        existing = session.execute(
            select(PaperPortfolio).where(PaperPortfolio.name == name)
        ).scalar_one_or_none()
        if existing:
            return existing.id

        cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), "trading_style": style}
        p = PaperPortfolio(
            name            = name,
            initial_capital = initial_capital,
            current_cash    = initial_capital,
            config          = cfg,
            is_active       = True,
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        log.info("paper.portfolio_created", name=name, capital=initial_capital, style=style)
        return p.id


# ── Main step function (called from scheduler) ────────────────────────────────

def paper_trading_step() -> None:
    """One full monitor + scan cycle. Runs every 5-10 min during market hours."""
    try:
        with SessionLocal() as session:
            portfolios = session.execute(
                select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True))
            ).scalars().all()

            if not portfolios:
                return

            # Collect all symbols we care about (open + potential entries)
            open_symbols: list[str] = list(set(
                r[0] for r in session.execute(
                    select(Stock.symbol)
                    .join(PaperTrade, PaperTrade.symbol == Stock.symbol)
                    .where(PaperTrade.stage == "open")
                ).all()
            ))

            # Also fetch prices for watchlist candidates to avoid N+1 fetches
            candidate_symbols: list[str] = list(set(
                r[0] for r in session.execute(
                    select(Stock.symbol)
                    .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
                    .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
                    .where(Watchlist.trading_style.in_(
                        [p.config.get("trading_style", "GROWTH") for p in portfolios]
                    ), Stock.active.is_(True))
                ).all()
            ))

            all_symbols = list(set(open_symbols + candidate_symbols))
            live_prices = _fetch_live_prices(all_symbols) if all_symbols else {}

            for portfolio in portfolios:
                if not portfolio.config.get("enabled", True):
                    continue
                _monitor_positions(session, portfolio, live_prices)
                _scan_for_entries(session, portfolio, live_prices)
                session.commit()

    except Exception as exc:
        log.error("paper.step_failed", error=str(exc), exc_info=True)
