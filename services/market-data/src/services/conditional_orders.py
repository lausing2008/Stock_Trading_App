"""T286-CONDITIONAL-ORDER: single-hop "if TRIGGER then ACTION" orders on a portfolio's symbol.

Deliberately scoped down from the original "conditional order CHAINS" ask
(docs/FEATURE_ROADMAP_PYRAMID_GOALS_2026-08-16.md) to the safer, buildable core, per an
explicit design conversation before any code was written: same-symbol only (no
"if X breaks $140, buy Y" cross-symbol triggers), single-hop only (no multi-step chains — a
"chain" a user wants is just several ConditionalOrder rows created individually), and every
BUY action routed through the SAME real entry gate every organic entry already goes through
(_call_decision_engine / _should_enter fallback) — a conditional order only ever decides WHEN
to act on an already-real, already-eligible setup, never WHETHER the setup itself is valid.

Trigger vocabulary (ConditionalOrder.conditions, same JSON-list-of-condition-dicts shape as
PriceAlert.compound_conditions): price, rsi, volume_ratio, signal, position_pnl_pct, time.
trigger_logic ("AND"/"OR") controls how the list combines — AND is the PriceAlert default,
OR is new here per the explicit request for compound AND/OR support.

Action vocabulary (ConditionalOrder.action_type): buy, sell_partial, sell_all, tighten_stop,
close_position, alert_only.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.config import get_settings
from common.logging import get_logger
from db import (
    ConditionalOrder, PaperPortfolio, PaperTrade, Signal, SignalType, Stock, Ranking,
)

log = get_logger("conditional-orders")
_settings = get_settings()

_CONDITIONAL_ORDER_LOCK_KEY = "stockai:lock:check_conditional_orders"
_CONDITIONAL_ORDER_LOCK_TTL = 55  # seconds — job runs every 60s; 55s prevents overlap


def _get_redis():
    from common.redis_client import get_redis
    return get_redis()


# ── Trigger evaluation ──────────────────────────────────────────────────────────────────────

def _fetch_stored_signal(symbol: str, style: str = "SWING") -> dict | None:
    """Same source of truth check_signal_alerts()/_evaluate_compound_conditions() already use
    — a persisted (live=False) DB signal, so a conditional order's "signal = BUY" condition
    reads the same signal a user sees on-screen, not a live recompute."""
    try:
        import httpx
        r = httpx.get(
            f"{_settings.signal_engine_url}/signals/{symbol}",
            params={"style": style, "live": "false"}, timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _fetch_rvol(symbol: str, session: Session) -> float | None:
    try:
        from ..api.routes import get_rvol
        return get_rvol(symbol, session=session).get("rvol")
    except Exception:
        return None


def _position_pnl_pct(session: Session, portfolio_id: int, symbol: str, live_price: float | None) -> float | None:
    """Real-time unrealized P&L% on the portfolio's OPEN position in this symbol, or None if
    there is no open position (a position_pnl_pct condition on a symbol with no open trade
    always fails closed — there is nothing to compute a P&L% against)."""
    if live_price is None:
        return None
    trade = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio_id,
            PaperTrade.symbol == symbol,
            PaperTrade.stage == "open",
        )
    ).scalar_one_or_none()
    if trade is None or not trade.entry_price:
        return None
    return round((live_price - trade.entry_price) / trade.entry_price * 100, 4)


def _evaluate_one_condition(
    cond: dict, symbol: str, portfolio_id: int, live_price: float | None,
    session: Session, caches: dict,
) -> bool:
    """One condition dict: {"metric": ..., "op": "gte"|"lte"|"eq", "value": ...}.

    Mirrors scheduler.py's _evaluate_compound_conditions() metric-by-metric structure and
    extends it with two conditional-order-specific metrics (price, position_pnl_pct, time) —
    rsi/volume_ratio/signal are the SAME metrics/lookups that function already implements,
    reused here rather than re-derived, so a conditional order's "if RSI < 30" reads the exact
    same RSI a PriceAlert compound condition would.

    Fails closed (returns False) on any missing/unavailable data — a conditional order that
    can't evaluate its own trigger must never fire on incomplete information.
    """
    metric = cond.get("metric")
    op = cond.get("op")
    value = cond.get("value")

    if metric == "price":
        actual = live_price
    elif metric == "position_pnl_pct":
        if "pnl_pct" not in caches:
            caches["pnl_pct"] = _position_pnl_pct(session, portfolio_id, symbol, live_price)
        actual = caches["pnl_pct"]
    elif metric == "time":
        # value is an "HH:MM" string in the exchange's own local time — evaluated once per
        # scan cycle as "has the current time reached this time today", i.e. op is always
        # effectively "gte" semantically; op is still honored for consistency with every
        # other metric's own comparison shape.
        now_str = caches.get("now_hhmm")
        if now_str is None:
            now_str = datetime.now(timezone.utc).strftime("%H:%M")
            caches["now_hhmm"] = now_str
        actual = now_str
    elif metric == "volume_ratio":
        if "rvol" not in caches:
            caches["rvol"] = _fetch_rvol(symbol, session)
        actual = caches["rvol"]
    elif metric == "rsi":
        if "signal_payload" not in caches:
            caches["signal_payload"] = _fetch_stored_signal(symbol)
        payload = caches["signal_payload"]
        actual = (payload or {}).get("reasons", {}).get("rsi")
        if actual is not None:
            actual = float(actual)
    elif metric == "signal":
        if "signal_payload" not in caches:
            caches["signal_payload"] = _fetch_stored_signal(symbol)
        payload = caches["signal_payload"]
        actual = (payload or {}).get("signal")
    else:
        return False  # unknown metric — fail closed

    if actual is None:
        return False

    if op == "gte":
        return actual >= value
    if op == "lte":
        return actual <= value
    if op == "eq":
        return actual == value
    return False  # unknown op — fail closed


def evaluate_conditions(
    order: ConditionalOrder, live_price: float | None, session: Session,
) -> bool:
    """True if `order`'s own trigger has been met right now.

    trigger_logic="AND" (default, matches PriceAlert.compound_conditions' own convention):
    every condition must pass. "OR": any single condition passing is enough. An order with
    an empty conditions list never fires (there's nothing to trigger it) — matches the
    fail-closed convention throughout this module.
    """
    conditions = order.conditions or []
    if not conditions:
        return False
    caches: dict = {}
    results = [
        _evaluate_one_condition(cond, order.symbol, order.portfolio_id, live_price, session, caches)
        for cond in conditions
    ]
    if order.trigger_logic == "OR":
        return any(results)
    return all(results)


# ── Action execution ─────────────────────────────────────────────────────────────────────────

def _execute_buy(order: ConditionalOrder, portfolio: PaperPortfolio, live_price: float, session: Session) -> tuple[bool, str, int | None]:
    """A conditional BUY only ever fires on top of a REAL, already-eligible BUY signal — it
    never fabricates one. Routes through the SAME gate real entries use
    (_call_decision_engine, falling back to _should_enter — the exact dual-scorer pattern
    _scan_for_entries() itself uses), so a conditional order can only ever decide WHEN to
    enter, never bypass whether the setup itself clears the real bar.

    Returns (fired, reason, resulting_trade_id).
    """
    from .paper_trading_engine import (
        _DEFAULT_CONFIG, _STYLE_OVERRIDES, _build_game_plan_for_style, _call_decision_engine,
        _compute_equity, _compute_portfolio_drawdown, _consec_loss_streak,
        _entry_gates_override_active, _recent_win_rate, _should_enter,
    )

    style = (portfolio.config or {}).get("trading_style", "SWING")
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), **(portfolio.config or {})}

    # AUD-CONDORDER-CIRCUITBREAKER-BYPASS: a conditional order must respect the SAME
    # portfolio-wide pause switch _scan_for_entries()'s own caller (paper_trading_step())
    # already checks before ever scanning for organic entries — otherwise pausing a portfolio
    # via the admin UI silently does nothing to a pending conditional order on that portfolio.
    if (portfolio.config or {}).get("paused", False):
        return False, "Portfolio is paused — no new entries, including conditional orders", None

    stock = session.execute(select(Stock).where(Stock.symbol == order.symbol)).scalar_one_or_none()
    if stock is None:
        return False, f"Unknown symbol {order.symbol}", None

    sig = session.execute(
        select(Signal)
        .where(Signal.stock_id == stock.id, Signal.horizon == style)
        .order_by(Signal.ts.desc()).limit(1)
    ).scalar_one_or_none()
    if sig is None or sig.signal != SignalType.BUY:
        return False, "No current BUY-eligible signal for this symbol — conditional BUY only ever acts on a real, already-existing setup", None

    already_open = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.symbol == order.symbol,
            PaperTrade.stage == "open",
        )
    ).scalar_one_or_none()
    if already_open is not None:
        return False, "Already have an open position in this symbol on this portfolio", None

    ranking = session.execute(
        select(Ranking).where(Ranking.stock_id == stock.id).order_by(Ranking.as_of.desc()).limit(1)
    ).scalar_one_or_none()
    kscore_f = float(ranking.score) if ranking and ranking.score is not None else None
    ta_score_raw = (sig.reasons or {}).get("ta_score")
    ta_score_f = float(ta_score_raw) if ta_score_raw is not None else None

    game_plan = _build_game_plan_for_style(order.symbol, style, live_price, sig.reasons or {}, atr=None)
    signal_data = {
        "signal": sig.signal.value, "confidence": sig.confidence,
        "bullish_probability": sig.bullish_probability, "reasons": sig.reasons or {}, "ts": sig.ts,
    }

    open_count = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == portfolio.id, PaperTrade.stage == "open")
    ).scalars().all()
    if len(open_count) >= cfg.get("max_positions", 6):
        return False, f"Portfolio already at max_positions ({cfg.get('max_positions', 6)})", None

    live_prices = {order.symbol: live_price}
    equity = _compute_equity(session, portfolio, live_prices)
    recent_wr = _recent_win_rate(session, portfolio.id)
    consec_losses = _consec_loss_streak(session, portfolio.id)
    regime_state = (portfolio.config or {}).get("regime_state", "neutral")

    # AUD-CONDORDER-CIRCUITBREAKER-BYPASS: mirror _scan_for_entries()'s own portfolio-wide
    # circuit breakers (drawdown, daily-loss, weekly-loss/gain-lock) — previously entirely
    # absent from this path. A conditional order calling _call_decision_engine() with the
    # default daily_pnl_pct=0.0 made hard_rejects.py's daily-loss gate structurally
    # unreachable (0.0 can never satisfy `<= -abs(max_daily_loss)`), and the fallback
    # _should_enter() has no equivalent parameter at all — so on EITHER path a conditional
    # BUY could silently bypass every one of these breakers regardless of the portfolio's
    # real current state. Same _gates_override admin escape hatch as _scan_for_entries().
    _gates_override = _entry_gates_override_active(cfg)

    max_dd_cfg = cfg.get("max_portfolio_drawdown_pct", 0.20)
    if max_dd_cfg and max_dd_cfg > 0 and not _gates_override:
        current_dd = _compute_portfolio_drawdown(session, portfolio.id, equity)
        if current_dd is not None and current_dd > max_dd_cfg:
            return False, f"Portfolio drawdown {current_dd*100:.1f}% exceeds {max_dd_cfg*100:.0f}% limit — no new entries until equity recovers", None

    _daily_pnl_pct = 0.0
    max_daily_loss = cfg.get("max_daily_loss_pct", 0.04)
    if max_daily_loss and max_daily_loss > 0 and equity > 0:
        today_open = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())
        daily_net_pnl = session.execute(
            select(func.sum(PaperTrade.pnl))
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.stage == "closed",
                PaperTrade.exit_time >= today_open,
            )
        ).scalar() or 0.0
        _daily_pnl_pct = round(daily_net_pnl / equity, 4)  # a fraction, matching _call_decision_engine()'s own convention
        if daily_net_pnl < 0 and abs(daily_net_pnl) / equity > max_daily_loss and not _gates_override:
            return False, f"Daily loss limit hit ({_daily_pnl_pct*100:.1f}% <= -{max_daily_loss*100:.0f}%) — no new entries today", None

    _weekly_net_pnl_pct: float | None = None
    max_weekly_loss = cfg.get("max_weekly_loss_pct", 0.08)
    max_weekly_gain = cfg.get("max_weekly_gain_pct", 0.06)
    if ((max_weekly_loss and max_weekly_loss > 0) or (max_weekly_gain and max_weekly_gain > 0)) and equity > 0:
        from zoneinfo import ZoneInfo
        week_start = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=7)
        weekly_net_pnl = session.execute(
            select(func.sum(PaperTrade.pnl))
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.stage == "closed",
                PaperTrade.exit_time >= week_start,
            )
        ).scalar() or 0.0
        _weekly_net_pnl_pct = weekly_net_pnl / equity * 100
        if max_weekly_loss and weekly_net_pnl < 0 and abs(weekly_net_pnl) / equity > max_weekly_loss and not _gates_override:
            return False, f"Weekly loss {abs(weekly_net_pnl)/equity*100:.1f}% exceeds {max_weekly_loss*100:.0f}% limit — no entries until next week", None
        if max_weekly_gain and weekly_net_pnl > 0 and weekly_net_pnl / equity > max_weekly_gain and not _gates_override:
            return False, f"Weekly gain lock — up {weekly_net_pnl/equity*100:.1f}% this week; protecting profits until Monday", None

    max_consec_losses = cfg.get("max_consecutive_losses", 3)
    if max_consec_losses and max_consec_losses > 0 and consec_losses >= max_consec_losses and not _gates_override:
        return False, f"Consecutive-loss limit hit ({consec_losses} >= {max_consec_losses}) — no new entries", None

    de_result = _call_decision_engine(
        symbol=order.symbol, live_price=live_price, game_plan=game_plan,
        equity=equity, open_count=len(open_count), cfg=cfg,
        initial_capital=portfolio.initial_capital, daily_pnl_pct=_daily_pnl_pct,
        recent_win_rate=recent_wr, consec_losses=consec_losses, kscore=kscore_f,
        ta_score=ta_score_f, regime_state=regime_state,
        weekly_net_pnl_pct=_weekly_net_pnl_pct,
    )
    notes: list[str] = []
    gate_source = "de"
    if de_result is not None:
        should_enter, verdict, score, blocked_reason = de_result
        if not should_enter:
            return False, blocked_reason or f"Decision engine verdict: {verdict}", None
        notes = [f"DE: {verdict}"]
    else:
        gate_source = "fallback"
        should_enter, score, se_notes = _should_enter(
            order.symbol, signal_data, live_price, game_plan, cfg, kscore=kscore_f,
        )
        if not should_enter:
            return False, (se_notes[0] if se_notes else "Entry gate rejected"), None
        notes = se_notes

    prefetched_open = [
        (t, session.execute(select(Stock).where(Stock.id == t.stock_id)).scalar_one_or_none())
        for t in open_count
    ]
    regime_size_mult = {
        "bull": 1.0, "neutral": 1.0, "choppy": 0.75, "risk_off": 0.50, "bear": 0.0,
    }.get(regime_state, 1.0)
    live_regime = {"state": regime_state} if regime_state else None
    atr = None  # T286: not recomputed for a conditional-order trigger; matches game_plan's own atr=None above

    from .paper_trading_engine import _open_paper_trade
    trade, skip_reason = _open_paper_trade(
        session, portfolio, stock, sig, ranking, live_price, game_plan, score, notes,
        gate_source, cfg, style, equity, regime_size_mult, live_regime, live_prices,
        prefetched_open, atr,
    )
    if trade is None:
        return False, f"Position sizing rejected the trade: {skip_reason}", None
    return True, "Entered via conditional order", trade.id


def _execute_sell_partial(order: ConditionalOrder, portfolio: PaperPortfolio, live_price: float, session: Session) -> tuple[bool, str, int | None]:
    trade = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id, PaperTrade.symbol == order.symbol, PaperTrade.stage == "open",
        )
    ).scalar_one_or_none()
    if trade is None:
        return False, "No open position to sell", None

    fraction = order.action_value if order.action_value is not None else 0.5
    fraction = max(0.01, min(1.0, fraction))
    cfg = portfolio.config or {}
    partial_shares = round(trade.shares * fraction, 4)
    if partial_shares <= 0:
        return False, "Nothing left to sell", None
    # AUD-CONDORDER-SLIPPAGE-CONSISTENCY: match the organic scale-out path's own IF-06
    # size-aware slippage model (_monitor_positions()'s two partial-scale-out blocks) instead
    # of always using the flat base rate regardless of position size.
    from .paper_trading_engine import _avg_daily_volume_for, _size_aware_slippage_pct
    _base_slippage = cfg.get("entry_slippage_pct", 0.001)
    slippage = (
        _size_aware_slippage_pct(partial_shares, _avg_daily_volume_for(order.symbol), _base_slippage)
        if cfg.get("size_aware_slippage_enabled", True) else _base_slippage
    )
    partial_price = round(live_price * (1 - slippage), 4)
    partial_value = round(partial_shares * partial_price, 2)
    partial_pnl = round((partial_price - trade.entry_price) * partial_shares, 2)
    partial_commission = round(cfg.get("commission_per_share", 0.0) * partial_shares, 4)

    trade.shares = round(trade.shares - partial_shares, 4)
    trade.realized_pnl = round((trade.realized_pnl or 0.0) + partial_pnl - partial_commission, 2)
    portfolio.current_cash = round(portfolio.current_cash + partial_value - partial_commission, 2)
    notes_list = list(trade.entry_decision_notes or [])
    notes_list.append(f"Conditional order: sold {partial_shares:.4f}sh @ ${partial_price:.2f}, remaining {trade.shares:.4f}sh")
    trade.entry_decision_notes = notes_list
    return True, f"Sold {fraction*100:.0f}% of position", trade.id


def _execute_tighten_stop(order: ConditionalOrder, portfolio: PaperPortfolio, live_price: float, session: Session) -> tuple[bool, str, int | None]:
    trade = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id, PaperTrade.symbol == order.symbol, PaperTrade.stage == "open",
        )
    ).scalar_one_or_none()
    if trade is None:
        return False, "No open position to tighten a stop on", None
    if order.action_value is None:
        return False, "tighten_stop requires action_value (the new stop price)", None
    new_stop = round(float(order.action_value), 4)
    # Monotonic — never loosens an existing stop, matching every other stop-tightening
    # mechanism in this codebase (scale-out, trailing stop, RSI-overbought trail).
    if new_stop <= (trade.current_stop or 0):
        return False, f"New stop ${new_stop} is not tighter than the current stop ${trade.current_stop}", None
    trade.current_stop = new_stop
    return True, f"Stop tightened to ${new_stop}", trade.id


def _execute_close_position(order: ConditionalOrder, portfolio: PaperPortfolio, live_price: float, session: Session) -> tuple[bool, str, int | None]:
    """Faithful, minimal reimplementation of _monitor_positions()'s own close-flow (the same 8
    fields, cash credit, broker exit routing, SignalOutcome writeback) — NOT a call into
    _monitor_positions() itself, since that function's per-trade loop is deeply woven into its
    own exit-reason decision tree and not separable into a standalone callable without a
    higher-risk refactor of an already delicate, heavily-audited function. Every field set
    here matches that function's own close block exactly."""
    trade = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id, PaperTrade.symbol == order.symbol, PaperTrade.stage == "open",
        )
    ).scalar_one_or_none()
    if trade is None:
        return False, "No open position to close", None

    cfg = portfolio.config or {}
    slippage = cfg.get("entry_slippage_pct", 0.001)
    exit_price = round(live_price * (1 - slippage), 4)
    exit_commission = round(cfg.get("commission_per_share", 0.0) * trade.shares, 4)
    exit_value = round(exit_price * trade.shares, 2)
    entry = trade.entry_price
    pnl_dollar = round((exit_price - entry) * trade.shares, 2)
    pnl_pct = (exit_price - entry) / entry if entry else 0.0
    total_pnl_dollar = round(
        (trade.realized_pnl or 0.0) + pnl_dollar - exit_commission - (trade.entry_commission or 0.0), 2
    )
    cost_basis = entry * (trade.entry_shares or trade.shares)
    total_pnl_pct = (total_pnl_dollar / cost_basis) if cost_basis else pnl_pct

    now = datetime.now(timezone.utc)
    trade.stage = "closed"
    trade.exit_time = now
    trade.exit_price = exit_price
    trade.exit_reason = "conditional_order"
    trade.exit_reasons = {
        "message": f"Closed via conditional order #{order.id}",
        "pnl_pct": round(pnl_pct * 100, 2),
    }
    trade.pnl = total_pnl_dollar
    trade.pct_return = round(total_pnl_pct * 100, 4)
    portfolio.current_cash = max(0.0, round(portfolio.current_cash + exit_value - exit_commission, 2))

    # IF-12: same append-only decision-audit write every other real exit path makes
    # (_monitor_positions()'s automated exits, manual_exit_trade()'s manual close).
    from .paper_trading_engine import _write_decision_log
    _write_decision_log(
        session, trade, "exit", exit_price, trade.shares, "conditional_order",
        {"pnl_dollar": total_pnl_dollar, "pnl_pct": trade.pct_return, "order_id": order.id},
    )

    if portfolio.broker_connection_id and trade.broker_order_id:
        try:
            from .paper_trading_engine import _place_broker_exit
            _place_broker_exit(session, trade, portfolio)
        except Exception as exc:
            log.error("conditional_order.broker_exit_failed", trade_id=trade.id, error=str(exc))

    if trade.signal_id is not None:
        try:
            from db.models import SignalOutcome
            so = session.execute(
                select(SignalOutcome).where(SignalOutcome.signal_id == trade.signal_id)
            ).scalar_one_or_none()
            if so is not None:
                so.entry_price = entry
                so.entry_date = trade.entry_date
                so.exit_price = exit_price
                so.exit_date = now.date()
                days_held = (now.date() - trade.entry_date).days if trade.entry_date else 0
                bucket = "5d" if days_held <= 7 else ("10d" if days_held <= 14 else "20d")
                setattr(so, f"return_{bucket}", round(total_pnl_pct, 4))
                setattr(so, f"is_correct_{bucket}", total_pnl_dollar > 0)
                session.flush()
        except Exception as exc:
            log.error("conditional_order.signal_outcome_writeback_failed", trade_id=trade.id, error=str(exc))

    return True, f"Closed at ${exit_price}, P&L {total_pnl_pct*100:.1f}%", trade.id


def execute_action(order: ConditionalOrder, portfolio: PaperPortfolio, live_price: float | None, session: Session) -> tuple[bool, str, int | None]:
    """Dispatch to the right action handler. alert_only never touches a position at all —
    fires (returns True) purely so the caller sends a notification email."""
    if order.action_type == "alert_only":
        return True, "Alert condition met", None
    if live_price is None:
        return False, "No live price available", None
    if order.action_type == "buy":
        return _execute_buy(order, portfolio, live_price, session)
    if order.action_type == "sell_partial":
        return _execute_sell_partial(order, portfolio, live_price, session)
    if order.action_type in ("sell_all", "close_position"):
        return _execute_close_position(order, portfolio, live_price, session)
    if order.action_type == "tighten_stop":
        return _execute_tighten_stop(order, portfolio, live_price, session)
    return False, f"Unknown action_type {order.action_type}", None


# ── Scheduled evaluator ───────────────────────────────────────────────────────────────────────

def check_conditional_orders() -> None:
    """1-minute evaluator for every pending ConditionalOrder — fail-CLOSED on a lock-acquire
    failure (unlike check_price_alerts' fail-open), matching this feature's real-money-adjacent
    risk profile: skipping one cycle is always safer than risking a double-fire.

    AUD-DQCHECKS-VISIBILITY: previously made zero _record_job_status() calls at all — the same
    AUD266-FIVE-ALERT-JOBS-RECORD-NO-STATUS gap already fixed for 5 other alert jobs, found
    again while adding new data-quality-check coverage for the minute-cadence job family (this
    session's own AUD-MISFIREGRACE-OPTIONSFLOW investigation). Local import (not module-level)
    to avoid conditional_orders.py importing scheduler.py at module load time, since scheduler.py
    already imports FROM this file (`from .conditional_orders import check_conditional_orders`)
    — a top-level import here would be circular.
    """
    import json as _json
    from db import SessionLocal
    from .scheduler import _record_job_status

    _t0 = time.monotonic()
    try:
        acquired = _get_redis().set(_CONDITIONAL_ORDER_LOCK_KEY, "1", nx=True, ex=_CONDITIONAL_ORDER_LOCK_TTL)
        if not acquired:
            log.info("conditional_order.skipped_locked")
            return
    except Exception:
        log.warning("conditional_order.lock_unavailable_skipping_fail_closed")
        return

    try:
        with SessionLocal() as session:
            orders = session.execute(
                select(ConditionalOrder).where(ConditionalOrder.status == "pending")
            ).scalars().all()
            if not orders:
                _record_job_status("check_conditional_orders", "ok", time.monotonic() - _t0)
                return

            now = datetime.now(timezone.utc)
            live_raw = {}
            try:
                for row in _json.loads(_get_redis().get("stockai:live_prices") or "[]"):
                    live_raw[row.get("symbol")] = row.get("price")
            except Exception:
                pass

            fired = 0
            for order in orders:
                try:
                    if order.expires_at is not None and order.expires_at.replace(tzinfo=timezone.utc) <= now:
                        order.status = "expired"
                        order.status_reason = "Reached its own expiration time before triggering"
                        continue

                    portfolio = session.get(PaperPortfolio, order.portfolio_id)
                    if portfolio is None or not portfolio.is_active:
                        order.status = "cancelled"
                        order.status_reason = "Portfolio no longer exists or is inactive"
                        continue

                    live_price = live_raw.get(order.symbol)
                    if not evaluate_conditions(order, live_price, session):
                        continue

                    fired_ok, reason, trade_id = execute_action(order, portfolio, live_price, session)
                    order.triggered_at = now
                    order.status = "triggered" if fired_ok else "failed"
                    order.status_reason = reason
                    order.resulting_trade_id = trade_id
                    session.commit()
                    fired += 1

                    if order.email:
                        try:
                            from .email_service import send_conditional_order_email
                            send_conditional_order_email(order.email, order, fired_ok, reason)
                        except Exception as exc:
                            log.warning("conditional_order.email_send_error", order_id=order.id, error=str(exc))
                except Exception as exc:
                    log.error("conditional_order.evaluate_failed", order_id=order.id, error=str(exc), exc_info=True)
                    session.rollback()

            session.commit()
            log.info("conditional_order.check_done", checked=len(orders), fired=fired,
                     elapsed_s=round(time.monotonic() - _t0, 2))
            _record_job_status("check_conditional_orders", "ok", time.monotonic() - _t0)
    except Exception as exc:
        log.error("conditional_order.check_failed", error=str(exc), exc_info=True)
        _record_job_status("check_conditional_orders", "error", time.monotonic() - _t0, str(exc))
