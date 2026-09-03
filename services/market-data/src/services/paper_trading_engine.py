"""WF-2: Autonomous paper-trading engine.

Full reference: docs/PAPER_TRADING_ENGINE.md

Each cycle (paper_trading_step, every 5-10 min during US market hours):
  1. _fetch_market_regime()    — classify SPY/QQQ/VIX into 5 states
  2. _fetch_live_prices()      — batch yfinance fast_info for all open + candidate symbols
  3. _monitor_positions()      — stops / targets / trailing stops / signal decay per open trade
  4. _scan_for_entries()       — evaluate fresh BUY signals against circuit breakers + scoring gate
  5. snapshot_equity_curve()   — post-close EOD snapshot (separate scheduler job)

Regime states (affect sizing, min_entry_score, trail multiplier):
  bull     — SPY > EMA-20 AND EMA-50, VIX < 20                  100% size, score +1
  neutral  — default                                              100% size
  choppy   — SPY < EMA-20 OR VIX > 20                            75% size, min_score = 4
  risk_off — SPY < EMA-50 AND VIX > 25 (M1 FIX: both legs required — NEW ENTRIES BLOCKED (T226-A default)
             see the AND, not OR, at the actual classification site; a stale OR in this
             docstring previously described the exact bug the M1 FIX resolved)
  bear     — SPY < EMA-50 AND VIX > 30  (OR SPY < EMA-200 + 20d return < -8%)
                                                                  NEW ENTRIES BLOCKED, trail ×0.70

Entry circuit breakers (checked in order):
  max_positions | live_prices health | portfolio drawdown (20%) |
  daily loss (4%) | daily entries (3) | bear regime gate

Style overrides (on top of _DEFAULT_CONFIG):
  GROWTH — RSI 38-85, stop -12%, target +35%, 60d time stop, trail ×2.0
  SWING  — RSI 30-65, stop -5.5%, target +12%, 20d time stop, trail ×1.5
  LONG   — RSI 30-65, stop -10%, target +25%, 90d time stop, trail ×2.0
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.logging import get_logger
from common.indicators import atr as _canon_atr
from db import (
    BrokerConnection, Indicator, PaperEquityCurve, PaperPortfolio, PaperTrade, Price, TimeFrame,
    Ranking, SessionLocal, Signal, SignalAlert, Stock, User, UserPosition, Watchlist, WatchlistItem,
    RestrictedSymbol, PaperTradeDecisionLog,
)
from sqlalchemy import desc, func, select
from .email_service import send_trade_exit_email

log = get_logger("paper_trading_engine")

# AL-1: RL agent — loaded once at module level; falls back gracefully if missing
try:
    from .rl_agent import rl_recommend as _rl_recommend
    _RL_AVAILABLE = True
except ImportError:
    _rl_recommend = None  # type: ignore[assignment]
    _RL_AVAILABLE = False
    log.warning("paper_trading_engine.rl_agent_unavailable", reason="import failed")

# ── Service-to-service JWT token (for calling auth-protected internal endpoints) ──

_svc_token_cache: str = ""


def _svc_token() -> str:
    global _svc_token_cache
    if _svc_token_cache:
        return _svc_token_cache
    import time
    from common.config import get_settings as _gs_tok
    from jose import jwt as _jwt
    _s = _gs_tok()
    _svc_token_cache = _jwt.encode(
        {"sub": "paper-engine", "exp": int(time.time()) + 365 * 86400, "jti": str(__import__("uuid").uuid4())},
        _s.jwt_secret, algorithm="HS256",
    )
    return _svc_token_cache


# ── Broker order routing (E*Trade sandbox / live) ─────────────────────────────

def _etrade_symbol(symbol: str) -> str:
    """Strip market suffix for E*Trade API: 'AAPL.US' → 'AAPL'."""
    return symbol.split(".")[0]


def _get_portfolio_broker(session, portfolio: "PaperPortfolio"):
    """Return a broker adapter for the portfolio, or None if not configured/authorized."""
    if not portfolio.broker_connection_id:
        return None
    try:
        from db.models import BrokerConnection
        from src.api.broker import _decrypt_config
        from src.services.broker import get_broker
        conn = session.get(BrokerConnection, portfolio.broker_connection_id)
        if not conn or not conn.is_authorized:
            return None
        return get_broker(conn.broker_type, _decrypt_config(conn.config))
    except Exception as exc:
        log.warning("broker.load_failed", portfolio_id=portfolio.id, error=str(exc))
        return None


def _handle_broker_error_if_token_rejected(session, portfolio: "PaperPortfolio", exc: Exception) -> bool:
    """T257-ETRADE-PROD-SYSTEMATIC: detect an expired/rejected OAuth token from a broker
    call's exception and immediately mark the connection unauthorized + notify the user,
    rather than silently swallowing it and waiting for tomorrow's 08:30 ET health check
    (scheduler.py's _check_broker_auth) to notice. Returns True if this WAS a token
    rejection (caller can skip logging its own generic warning in that case — the notify
    path already logs). Lazy-imports scheduler.py's shared helpers to avoid a module-load
    cycle (scheduler.py imports several names from this module at its own top level).
    """
    try:
        from .scheduler import _is_token_rejected_error, _mark_broker_unauthorized_and_notify
        if not _is_token_rejected_error(exc):
            return False
        from db.models import BrokerConnection
        conn = session.get(BrokerConnection, portfolio.broker_connection_id)
        if conn and conn.is_authorized:
            _mark_broker_unauthorized_and_notify(session, conn)
        return True
    except Exception as _detect_err:
        log.warning("broker.token_rejection_detect_failed", error=str(_detect_err))
        return False


# T270-ETRADE-PROD-REAL-MONEY: buying-power headroom kept clear of the real account's own
# reported buying_power before ANY real order is placed. A pre-flight check that only rejected
# a position sized EXACTLY at the account's own reported limit would still be fragile against
# the real fill price moving between this check and the broker actually executing the market
# order — a flat safety margin (not just "== buying_power") keeps real headroom against that
# gap, and against the account balance itself being marginally stale.
_BROKER_BUYING_POWER_SAFETY_MARGIN = 0.95  # only use up to 95% of reported buying power

# AUD265-BROKERERROR-RAWEXCEPTION-EXPOSURE: PaperTrade.broker_error is now surfaced through
# GET .../positions and .../trades (get_current_user-only, not admin-gated) and rendered
# directly in a browser tooltip. Every broker-library exception embeds the raw HTTP response
# body verbatim (e.g. EtradeBroker raises RuntimeError(f"... {resp.status_code} {resp.text}")),
# so any future response shape from any linked broker's API that happens to echo back
# credential/token-looking content would otherwise reach any authenticated app user's screen
# unfiltered. This is defense-in-depth, not evidence of an actual observed leak — every real
# E*Trade error text captured in this app's own history has been a clean {"Error":{"code":...,
# "message":...}} body with no account-identifying content — but a broker exception's exact
# text is not something this code controls, so any raw exception text written to
# trade.broker_error must go through this first.
import re as _re
_BROKER_ERROR_REDACT_PATTERNS = [
    _re.compile(r'\b[A-Za-z0-9_\-]{24,}\b'),  # long opaque tokens (bearer/session/API keys)
]


def _sanitize_broker_error(raw: str) -> str:
    text = raw
    for pattern in _BROKER_ERROR_REDACT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:512]


def _place_broker_entry(session, trade: "PaperTrade", portfolio: "PaperPortfolio") -> None:
    """Submit a market BUY to the linked broker (US only — HK skipped).

    On success: stores broker_order_id. If filled immediately (sandbox), updates
    entry_price with the actual fill and adjusts portfolio cash for the delta.
    Falls back silently to the simulated entry on any error.

    T270-ETRADE-PROD-REAL-MONEY: order size is computed entirely from the SIMULATED
    PaperPortfolio ledger, upstream of this function — nothing before this point ever checks
    the REAL linked account's actual buying power, so a simulated ledger sized larger than the
    real account (e.g. after a manual withdrawal, or a real position already open from outside
    this app) would size a real order the account can't actually support, relying entirely on
    the broker's own margin rejection as the only backstop. This pre-flight check fetches the
    real account's buying_power and skips placing the REAL order (falling back to the
    simulated-only fill, exactly like every other failure path in this function — the
    simulated trade itself, already added to the session by the caller, is untouched) if the
    position's real dollar value would exceed a safety-margined fraction of it. Fails OPEN
    toward placing the order on any error fetching the account itself (e.g. a transient
    network blip) — the broker's own margin rejection remains the backstop in that specific
    case, matching this function's pre-existing fail-open posture for every other broker call.
    """
    if trade.symbol.upper().endswith(".HK"):
        return
    broker = _get_portfolio_broker(session, portfolio)
    if broker is None:
        return
    try:
        order_value = float(trade.entry_price) * int(trade.shares)
        account = broker.get_account()
        max_order_value = account.buying_power * _BROKER_BUYING_POWER_SAFETY_MARGIN
        if order_value > max_order_value:
            log.warning(
                "broker.entry_skipped_insufficient_buying_power",
                symbol=trade.symbol, order_value=round(order_value, 2),
                buying_power=round(account.buying_power, 2),
                max_order_value=round(max_order_value, 2),
            )
            trade.broker_error = (
                f"Skipped real order: ${order_value:,.2f} exceeds "
                f"{_BROKER_BUYING_POWER_SAFETY_MARGIN:.0%} of real account buying power "
                f"(${account.buying_power:,.2f})"
            )[:512]
            return
    except Exception as _bp_exc:
        if not _handle_broker_error_if_token_rejected(session, portfolio, _bp_exc):
            log.warning("broker.buying_power_check_failed", symbol=trade.symbol, error=str(_bp_exc))
        # fail open — the broker's own margin rejection is the backstop for this specific case.
        # A genuine token rejection is now marked unauthorized + the user notified immediately
        # (matching every other broker call site in this function) rather than silently falling
        # through to attempt a real order placement on a connection already known to be dead.
    try:
        from src.services.broker.interface import OrderSide, OrderType
        order = broker.place_order(
            symbol=_etrade_symbol(trade.symbol),
            qty=int(trade.shares),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )
        trade.broker_order_id = order.order_id
        trade.broker_error = None  # a successful placement clears any prior failure on this leg
        log.info("broker.entry_order_placed",
                 symbol=trade.symbol, order_id=order.order_id, shares=int(trade.shares))
        # Immediate fill check — sandbox fills market orders instantly
        try:
            filled = broker.get_order(order.order_id)
            if filled.status == "filled" and filled.filled_avg_price:
                fill_p = round(float(filled.filled_avg_price), 4)
                delta = round((trade.entry_price - fill_p) * trade.shares, 2)
                portfolio.current_cash = round(portfolio.current_cash + delta, 2)
                trade.entry_price   = fill_p
                trade.current_price = fill_p
                trade.highest_price = fill_p
                log.info("broker.entry_filled", symbol=trade.symbol, fill_price=fill_p, delta=delta)
        except Exception:
            pass  # polling job will update fill later
    except Exception as exc:
        if not _handle_broker_error_if_token_rejected(session, portfolio, exc):
            log.warning("broker.entry_order_failed", symbol=trade.symbol, error=str(exc))
            trade.broker_error = _sanitize_broker_error(str(exc))


def _place_broker_exit(session, trade: "PaperTrade", portfolio: "PaperPortfolio") -> None:
    """Submit a market SELL to the linked broker for a position that was broker-entered.

    If the sandbox fills immediately, updates trade.exit_price with actual fill and
    adjusts portfolio cash for the delta vs the simulated exit. Falls back silently.
    """
    if trade.symbol.upper().endswith(".HK"):
        return
    if not trade.broker_order_id:
        return  # only broker-exit positions that were broker-entered
    broker = _get_portfolio_broker(session, portfolio)
    if broker is None:
        return
    try:
        from src.services.broker.interface import OrderSide, OrderType
        order = broker.place_order(
            symbol=_etrade_symbol(trade.symbol),
            qty=int(trade.shares),
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
        )
        trade.broker_error = None  # a successful exit placement clears any prior failure
        log.info("broker.exit_order_placed",
                 symbol=trade.symbol, order_id=order.order_id, shares=int(trade.shares))
        try:
            filled = broker.get_order(order.order_id)
            if filled.status == "filled" and filled.filled_avg_price:
                fill_p    = round(float(filled.filled_avg_price), 4)
                old_exit  = trade.exit_price or fill_p
                delta     = round((fill_p - old_exit) * trade.shares, 2)
                portfolio.current_cash = round(portfolio.current_cash + delta, 2)
                trade.exit_price  = fill_p
                # T232-PT6: fold in realized_pnl from scale-out partials here too — this path
                # overwrites trade.pnl/pct_return with the actual broker fill and would
                # otherwise silently re-drop the partial P&L folded in at simulated close.
                remaining_pnl_dollar = round((fill_p - trade.entry_price) * trade.shares, 2)
                total_pnl_dollar = round((trade.realized_pnl or 0.0) + remaining_pnl_dollar, 2)
                _cost_basis = trade.entry_price * (trade.entry_shares or trade.shares)
                total_pnl_pct = (total_pnl_dollar / _cost_basis) if _cost_basis else 0.0
                trade.pnl         = total_pnl_dollar
                trade.pct_return  = round(total_pnl_pct * 100, 4)
                log.info("broker.exit_filled", symbol=trade.symbol, fill_price=fill_p,
                         pnl=trade.pnl)
        except Exception as exc:
            # AUD232-SILENT-BROKER-RECONCILE: the ORDER itself already placed successfully
            # (broker.exit_order_placed logged above) — this block only reconciles OUR OWN
            # accounting (exit_price/pnl/portfolio.current_cash) against the broker's real
            # fill. A failure here (bad filled_avg_price, a division error, a DB write
            # issue) previously vanished with zero trace, leaving our records silently
            # desynced from the broker's actual filled state — the trade shows as closed
            # with stale/wrong P&L while the real money moved correctly at the broker.
            log.error("broker.exit_fill_reconciliation_failed", symbol=trade.symbol,
                      order_id=order.order_id, error=str(exc))
            # AUD265-RECONCILE-MISLABEL: self-describing prefix — the order genuinely succeeded
            # (see above), so this must never read as "the order failed" to any UI consumer.
            trade.broker_error = _sanitize_broker_error(
                f"Order placed successfully at the broker, but our own post-fill bookkeeping "
                f"failed to reconcile: {exc}"
            )
    except Exception as exc:
        if not _handle_broker_error_if_token_rejected(session, portfolio, exc):
            log.warning("broker.exit_order_failed", symbol=trade.symbol, error=str(exc))
            trade.broker_error = _sanitize_broker_error(str(exc))


def poll_broker_order_fills(session=None) -> None:
    """Check open trades with pending broker entry orders and update fill prices.

    Called each intraday scheduler cycle. Handles the case where E*Trade didn't
    fill immediately (e.g., order placed just before market close).
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        pending = session.execute(
            select(PaperTrade).where(
                PaperTrade.stage == "open",
                PaperTrade.broker_order_id.isnot(None),
            )
        ).scalars().all()
        if not pending:
            return
        portfolio_ids = list({t.portfolio_id for t in pending})
        portfolios = {
            p.id: p for p in session.execute(
                select(PaperPortfolio).where(PaperPortfolio.id.in_(portfolio_ids))
            ).scalars().all()
        }
        updated = 0
        for trade in pending:
            port = portfolios.get(trade.portfolio_id)
            if not port:
                continue
            broker = _get_portfolio_broker(session, port)
            if broker is None:
                continue
            try:
                filled = broker.get_order(trade.broker_order_id)
                if filled.status == "filled" and filled.filled_avg_price:
                    fill_p = round(float(filled.filled_avg_price), 4)
                    if abs(fill_p - trade.entry_price) > 0.001:
                        delta = round((trade.entry_price - fill_p) * trade.shares, 2)
                        port.current_cash   = round(port.current_cash + delta, 2)
                        trade.entry_price   = fill_p
                        trade.current_price = fill_p
                        updated += 1
                        log.info("broker.poll_fill_updated",
                                 symbol=trade.symbol, fill_price=fill_p)
            except Exception as exc:
                if not _handle_broker_error_if_token_rejected(session, port, exc):
                    log.debug("broker.poll_check_failed",
                              order_id=trade.broker_order_id, error=str(exc))
        if updated:
            session.commit()
            log.info("broker.poll_fills_updated", count=updated)
    except Exception as exc:
        log.warning("broker.poll_error", error=str(exc))
    finally:
        if own_session:
            session.close()


# ── T230-PORTFOLIO-BROKER-SYNC ─────────────────────────────────────────────────
# GET /connections/{id}/account (src/api/broker.py) already round-trips a real broker's live
# positions end-to-end — the whole OAuth + fetch + parse chain already works. The gap was
# purely that nothing ever PERSISTED that into UserPosition (positions.tsx's data source) —
# every broker-linked user still had to hand-copy their real E*Trade holdings into the manual
# positions tracker. This closes that gap by piggybacking on the same already-scheduled cycle
# poll_broker_order_fills() runs on, rather than adding new cron plumbing.

def sync_broker_positions(session=None) -> None:
    """Sync each authorized BrokerConnection's live positions into UserPosition.

    Upserts by (user_id, symbol), matching UserPosition's own unique constraint. Only ever
    touches rows this sync itself owns (broker_connection_id == this connection's id) — a
    manually-entered position (broker_connection_id IS NULL) for the same symbol is left
    untouched and this sync logs a conflict instead of silently overwriting it, since the
    user's manually-tracked cost basis/share count could differ from what the broker reports
    (e.g. a partial manual entry made before ever linking the account).
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        conns = session.execute(
            select(BrokerConnection).where(
                BrokerConnection.is_active.is_(True),
                BrokerConnection.is_authorized.is_(True),
            )
        ).scalars().all()
        if not conns:
            return
        synced, conflicts = 0, 0
        for conn in conns:
            try:
                from src.api.broker import _decrypt_config
                from src.services.broker import get_broker
                broker = get_broker(conn.broker_type, _decrypt_config(conn.config))
                acct = broker.get_account(conn.account_id or None)
            except Exception as exc:
                if not _handle_broker_error_if_token_rejected(session, _FakePortfolioForConn(conn), exc):
                    log.warning("broker.position_sync_fetch_failed", conn_id=conn.id, error=str(exc))
                continue

            existing_by_symbol = {
                p.symbol: p for p in session.execute(
                    select(UserPosition).where(UserPosition.user_id == conn.user_id)
                ).scalars().all()
            }
            live_symbols = {p.symbol.upper() for p in acct.open_positions}

            for bp in acct.open_positions:
                sym = bp.symbol.upper()
                row = existing_by_symbol.get(sym)
                if row is not None and row.broker_connection_id is None:
                    # A manual entry already exists for this symbol — never silently overwrite
                    # a user's own hand-entered cost basis/share count with the broker's numbers.
                    conflicts += 1
                    log.warning("broker.position_sync_conflict_skipped",
                                conn_id=conn.id, symbol=sym,
                                reason="manual position already exists for this symbol")
                    continue
                if row is not None and row.broker_connection_id != conn.id:
                    # Owned by a DIFFERENT broker connection (same user linked two accounts
                    # that both hold the same symbol) — same non-clobber rule applies.
                    conflicts += 1
                    log.warning("broker.position_sync_conflict_skipped",
                                conn_id=conn.id, symbol=sym,
                                reason=f"already synced from connection {row.broker_connection_id}")
                    continue
                if row is None:
                    row = UserPosition(
                        user_id=conn.user_id, symbol=sym, shares=bp.qty, avg_cost=bp.avg_cost,
                        currency="USD", broker_connection_id=conn.id,
                    )
                    session.add(row)
                else:
                    row.shares = bp.qty
                    row.avg_cost = bp.avg_cost
                row.broker_synced_at = datetime.now(timezone.utc)
                synced += 1

            # A position this sync previously created is now gone from the broker (closed
            # externally, e.g. sold directly on E*Trade's own site) — remove the synced row so
            # positions.tsx doesn't keep showing a position the user no longer actually holds.
            # Only rows THIS connection owns are eligible; a manual/other-connection row is
            # never touched here regardless of what symbols the broker currently reports.
            for sym, row in existing_by_symbol.items():
                if row.broker_connection_id == conn.id and sym not in live_symbols:
                    session.delete(row)

        session.commit()
        if synced or conflicts:
            log.info("broker.position_sync_done", synced=synced, conflicts=conflicts)
    except Exception as exc:
        log.warning("broker.position_sync_error", error=str(exc))
    finally:
        if own_session:
            session.close()


class _FakePortfolioForConn:
    """_handle_broker_error_if_token_rejected() expects a PaperPortfolio-shaped object with a
    broker_connection_id attribute — sync_broker_positions() has a BrokerConnection directly
    (no portfolio in the loop at all), so this adapts the shape rather than changing that
    shared helper's signature for every other caller."""
    def __init__(self, conn: "BrokerConnection"):
        self.broker_connection_id = conn.id


# ── PT-3: Entry score calibration — learned weights from closed paper trades ──

_ENTRY_WEIGHTS_FILE = Path("/data/models/entry_weights.json")
_entry_weights_cache: dict | None = None  # None = not loaded yet; {} = no file


def _load_entry_weights() -> dict:
    """Load calibrated entry weights from disk. Cached in memory after first load."""
    global _entry_weights_cache
    if _entry_weights_cache is not None:
        return _entry_weights_cache
    try:
        if _ENTRY_WEIGHTS_FILE.exists():
            _entry_weights_cache = json.loads(_ENTRY_WEIGHTS_FILE.read_text())
            log.info("paper.entry_weights_loaded", n_trades=_entry_weights_cache.get("n_trades"))
        else:
            _entry_weights_cache = {}
    except Exception as exc:
        log.warning("paper.entry_weights_load_failed", error=str(exc))
        _entry_weights_cache = {}
    return _entry_weights_cache


def reload_entry_weights() -> None:
    """Force reload of entry weights on next _should_enter() call (called after calibration)."""
    global _entry_weights_cache
    _entry_weights_cache = None


# ── SELFIMPROVE-NEVER-CALIBRATED-PARAMS: calibrated min_rr_ratio fallback default ──
# min_rr_ratio (2.0) and regime_min_rr_ratio (3.0) were permanently hardcoded module
# defaults with no feedback loop — see the tracker entry's own "what" field. Calibration
# writes a validated replacement DEFAULT here (same file-cache + reload pattern as entry
# weights above), consulted only when a portfolio's own config doesn't explicitly set the
# key — an explicit per-portfolio min_rr_ratio (settable via paper-portfolio.tsx's Settings
# UI) always wins, exactly as it does today against the hardcoded 2.0/3.0 literals.
_MIN_RR_OVERRIDE_FILE = Path("/data/models/min_rr_calibration.json")
_min_rr_override_cache: dict | None = None  # None = not loaded yet; {} = no file


def _load_min_rr_override() -> dict:
    """Load calibrated min_rr_ratio/regime_min_rr_ratio defaults from disk. Cached after first load."""
    global _min_rr_override_cache
    if _min_rr_override_cache is not None:
        return _min_rr_override_cache
    try:
        if _MIN_RR_OVERRIDE_FILE.exists():
            _min_rr_override_cache = json.loads(_MIN_RR_OVERRIDE_FILE.read_text())
            log.info("paper.min_rr_override_loaded", n_trades=_min_rr_override_cache.get("n_trades"))
        else:
            _min_rr_override_cache = {}
    except Exception as exc:
        log.warning("paper.min_rr_override_load_failed", error=str(exc))
        _min_rr_override_cache = {}
    return _min_rr_override_cache


def reload_min_rr_override() -> None:
    """Force reload of the calibrated min_rr default on next _should_enter() call."""
    global _min_rr_override_cache
    _min_rr_override_cache = None


def _default_min_rr_ratio(regime_state: str) -> float:
    """The calibrated default for a portfolio that hasn't explicitly set min_rr_ratio/
    regime_min_rr_ratio in its own config — falls back to the original hardcoded 2.0/3.0
    literals if no calibration has ever been applied yet."""
    override = _load_min_rr_override()
    key = "regime_min_rr_ratio" if regime_state in ("choppy", "risk_off") else "min_rr_ratio"
    return float(override.get(key) or (3.0 if key == "regime_min_rr_ratio" else 2.0))


# ── Default portfolio config ──────────────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "trading_style":        "GROWTH",  # which signal horizon to trade
    "market":               "US",      # "US" or "HK" — determines market hours + stock universe
    "max_positions":        6,         # max concurrent open positions (fewer = higher quality)
    "max_sector_pct":       0.25,      # max 25% in one sector (spec requirement)
    "max_sector_positions": 3,         # max positions per sector (RISK-3)
    "risk_per_trade_pct":   0.01,      # risk 1% of equity per trade
    "max_position_pct":     0.10,      # cap any single position at 10% of equity
    # Signal.confidence = abs(bull_probability - 0.5) × 200
    # e.g. bull_prob=72.5%→conf=45; bull_prob=75%→conf=50; bull_prob=81%→conf=62.
    # Style-specific minimums in _STYLE_OVERRIDES below.
    "min_confidence":       45.0,      # Signal.confidence threshold (bull_prob ≥ 72.5%)
    "min_kscore":           48.0,      # Ranking.score threshold
    "min_rr_ratio":         2.0,       # minimum risk:reward at entry
    "min_entry_score":      4,         # _should_enter() score threshold (raised from 3 → 4)
    "max_hold_days":        60,        # time-stop (GROWTH / LONG)
    "trail_atr_mult":       2.0,       # trailing stop = highest_price - ATR × mult
    "trail_trigger_pct":         0.05,  # arm trailing stop after +5% gain (once armed, trails every cycle)
    "breakeven_trigger_pct":     0.03,  # move stop to breakeven after +3% gain
    "partial_tp_pct":            0.10,  # sell first tranche at +10% gain (styles override this)
    "wait_exit_days":            5,     # exit if signal stays WAIT this many days
    "max_portfolio_drawdown_pct":0.20,  # pause entries if equity drops 20% from peak
    "max_daily_loss_pct":        0.04,  # pause entries if realized losses today > 4% of equity
    "max_entries_per_day":       3,     # cap new positions opened in one trading day (quality > quantity)
    "max_entry_gap_pct":         0.04,  # T171: reject if live_price is >4% above signal's last_price (gap-up filter)
    "require_kscore":            True,  # reject stocks with no ranking row (unknown quality)
    "max_open_risk_pct":         0.12,  # max aggregate open risk across all positions (12%)
    "max_loss_per_trade_pct":    0.02,  # cap dollar loss on any single trade at 2% of equity
    "entry_slippage_pct":        0.001, # 10 bps slippage on entry and exit (simulates spread)
    "commission_per_share":      0.0,   # per-share commission ($0 for most retail brokers)
    "hold_stall_days":           30,    # exit if position gains < hold_stall_max_gain for this many days
    "hold_stall_max_gain":       0.05,  # max unrealized gain threshold for stall detection (5%)
    "enforce_market_hours":      True,  # skip new entries outside 9:30–16:00 ET Mon–Fri
    # T222-C: Signal freshness gate — reject BUY signals older than this many hours.
    # 72h = 3 calendar days. Signals are computed 5×/day; a 3-day-old signal is 15+ refreshes stale.
    "max_signal_age_hours":      72,   # was 96h (4 days) — 3 days is sufficient with 5×/day refresh
    # T221-D: Post-stop cooldown — 5 days prevents re-entering a stock that's in a downtrend.
    "stop_cooldown_hours":       120,   # hours after stop_hit before re-entering same symbol (was 24h)
    # T221-B: Market cluster cap — prevent entering more positions when already at limit for one market.
    # Multiple correlated positions in HK/US all stop out together on big down days.
    "max_market_positions":      4,     # max open positions in any single market (HK or US)
    # T221-E: Portfolio heat brake — if this many stops hit in the window, pause all new entries.
    "heat_brake_max_stops":      3,     # stop count threshold (3 stops in 48h = adverse conditions)
    "heat_brake_window_hours":   48,    # lookback window for heat brake
    # T221-A: Cross-portfolio symbol cap — max total open positions per symbol across ALL portfolios.
    # Prevents SWING + GROWTH both going long the same stock, tripling concentration risk.
    "max_positions_per_symbol_global": 1,
    # T221-INDEX-TREND-GATE: Skip new entries when today's market index is down >threshold%.
    # SPY for US portfolios, ^HSI for HK. Single bad days (FOMC, CPI, HSI circuit break) cause
    # cascade stops. Regime filter catches multi-day bears; this catches single-day shocks.
    "index_trend_gate_enabled":  True,
    "index_trend_gate_pct":      -0.015,  # -1.5%: index down >1.5% today → no new entries
    # Regime engine
    "enable_regime_filter":      True,   # master on/off
    "regime_vix_high":           25.0,   # VIX above this → risk_off
    "regime_vix_fear":           30.0,   # VIX above this (+ SPY < 50EMA) → bear
    "regime_bear_size_mult":     0.0,    # 0 = block entries entirely in bear
    "regime_risk_off_size_mult": 0.50,   # 50 % size in risk_off
    "regime_choppy_size_mult":   0.75,   # 75 % size in choppy
    "regime_bull_size_mult":     1.0,    # full size (can boost to 1.1 for bull)
    "regime_risk_off_min_score": 5,      # stricter entry gate in risk_off
    # T226-A: Block all new entries in risk_off by default. 9/30 closed paper trades entered
    # in risk_off — 0% win rate, avg -5.0% return. Set False per-portfolio to revert to 50% size.
    "regime_risk_off_gate":      True,
    "regime_choppy_min_score":   4,      # slightly stricter in choppy
    "enabled":                   True,
    # Decision Engine mode — authoritative since Tier 73.
    # "primary": DE verdict is the gate (fallback to _should_enter() if DE unreachable).
    # "shadow":  old behavior — _should_enter() decides, DE logged for comparison only.
    "decision_engine_mode":    "primary",
    # T241-P5: position-scaling gate (conviction-based pullback-add) mode.
    # "off":    default. The pullback-add evaluation never runs at all — zero behavior
    #           change from before this phase existed.
    # "shadow": the position-scaling gate + thesis-persistence check run on every
    #           already-open candidate and their verdicts are logged (paper.position_
    #           scaling_shadow) — but this phase NEVER places a real add or touches
    #           portfolio.current_cash, regardless of this setting. There is currently no
    #           "live" mode: real-money-affecting wiring (sizer.py integration, actually
    #           placing the add) is intentionally deferred to a follow-up phase once shadow
    #           data validates the model against real outcomes, matching the design doc's
    #           own Phase 6 shadow-deployment requirement and the T232-DL-DUALSCORER-SHADOW
    #           precedent already used for the decision engine.
    "position_scaling_mode":  "off",
}


def _regime_risk_off_override_active(cfg: dict) -> bool:
    """True if a time-boxed risk_off gate override (T232-HKOVERRIDE) is currently active.

    Set via POST /paper-portfolio/risk-off-override?hours=N; expires on its own — no cron
    job clears it, this check is what makes the expiry take effect.
    """
    until_str = cfg.get("regime_risk_off_override_until")
    if not until_str:
        return False
    try:
        until = datetime.fromisoformat(until_str)
        return datetime.utcnow() < until
    except (ValueError, TypeError):
        return False


def _entry_gates_override_active(cfg: dict) -> bool:
    """T264-ENTRYGATESOVERRIDE: generalizes T232-HKOVERRIDE's proven time-boxed-override
    pattern from risk_off-only to every "market condition / recent performance" gate a user
    might reasonably want to temporarily bypass because they judge the market to actually be
    fine right now — drawdown, daily_loss, weekly_loss, weekly_gain_lock, consecutive_losses,
    regime_bear, regime_suspension.

    Deliberately does NOT cover max_positions, the equity-floor circuit breaker, or the
    live-price-sparsity safety check — those aren't "is the market good" judgment calls, they're
    hard capital-preservation/data-integrity limits nobody should be able to wave away from a UI
    button. A separate, distinct config key from regime_risk_off_override_until (that one still
    works standalone, unaffected by this one) — set via POST
    /paper-portfolio/entry-gates-override?hours=N, expires on its own with no cron job needed,
    exactly like the risk-off override this generalizes.
    """
    until_str = cfg.get("entry_gates_override_until")
    if not until_str:
        return False
    try:
        until = datetime.fromisoformat(until_str)
        return datetime.utcnow() < until
    except (ValueError, TypeError):
        return False


# Mirrors scheduler._STYLE_PARAMS — inlined here to avoid circular import.
# AUD-DUPLOGIC: atr_stop_mult added here as the single source of truth for the ATR-based
# stop-override multiplier — decision-engine's aggregator.py::_default_game_plan() had its own
# independently-hardcoded copy of this exact value (2.5 for GROWTH) that silently disagreed
# with this file's real, authoritative value (3.0 for GROWTH) for years with no comment
# explaining the difference — a genuine, undocumented drift, not an intentional divergence
# (unlike the deliberately-separate Game Plan/FVG/Position-Sizer systems documented elsewhere).
# decision-engine now reads this field via the same GET /stocks/style-params fetch it already
# uses for entry/breakout/stop/target percentages, instead of hardcoding its own copy.
_STYLE_PARAMS: dict[str, dict] = {
    "SHORT":  {"entry1_pct": 0.995, "entry2_pct": 0.985, "breakout_pct": 1.010,
               "stop_pct": 0.970, "default_tp_pct": 1.05, "atr_stop_mult": 2.0},
    "SWING":  {"entry1_pct": 0.985, "entry2_pct": 0.965, "breakout_pct": 1.020,
               "stop_pct": 0.945, "default_tp_pct": 1.12, "atr_stop_mult": 2.0},
    "LONG":   {"entry1_pct": 0.980, "entry2_pct": 0.950, "breakout_pct": 1.030,
               "stop_pct": 0.900, "default_tp_pct": 1.25, "atr_stop_mult": 2.0},
    "GROWTH": {"entry1_pct": 0.975, "entry2_pct": 0.940, "breakout_pct": 1.035,
               "stop_pct": 0.880, "default_tp_pct": 1.35, "atr_stop_mult": 3.0},
}

# AL-4: Load Optuna-tuned params from shared model dir; overlay onto _STYLE_PARAMS.
# trade_params.json is written by POST /paper-portfolio/tune-params.
_TRADE_PARAMS_FILE = Path("/data/models/trade_params.json")


def _load_tuned_params() -> None:
    """Merge Optuna-tuned stop/tp/hold params into _STYLE_PARAMS if the file exists."""
    if not _TRADE_PARAMS_FILE.exists():
        return
    try:
        data = json.loads(_TRADE_PARAMS_FILE.read_text())
        for style, result in data.items():
            if style in _STYLE_PARAMS:
                if "best_stop_pct" in result:
                    _STYLE_PARAMS[style]["stop_pct"] = result["best_stop_pct"]
                if "best_tp_pct" in result:
                    _STYLE_PARAMS[style]["default_tp_pct"] = result["best_tp_pct"]
        log.info("paper_engine.tuned_params_loaded", styles=list(data.keys()))
    except Exception as exc:
        log.warning("paper_engine.tuned_params_load_failed", exc=str(exc))


# Also update _STYLE_OVERRIDES max_hold_days from tuned params when loaded.
def _apply_tuned_hold_days() -> None:
    if not _TRADE_PARAMS_FILE.exists():
        return
    try:
        data = json.loads(_TRADE_PARAMS_FILE.read_text())
        for style, result in data.items():
            if style in _STYLE_OVERRIDES and "best_max_hold_days" in result:
                _STYLE_OVERRIDES[style]["max_hold_days"] = result["best_max_hold_days"]
    except Exception as exc:
        # AUD232-SILENT-EXCEPTION-AUDIT: falls back to the hardcoded _STYLE_OVERRIDES
        # defaults on any failure, which is a safe fallback — but a malformed tuned-params
        # file silently means every style keeps stale/default hold-days config indefinitely
        # with zero indication the tuning file even exists and isn't being applied.
        log.warning("paper.tuned_hold_days_load_failed", error=str(exc))


def _round_step(price: float) -> float:
    if price >= 1000: return 5.0
    if price >= 100:  return 0.5
    if price >= 10:   return 0.1
    if price >= 1:    return 0.05
    return 0.01


def _hk_board_lot_size(price: float) -> int:
    """AUD262-HK-NO-BOARD-LOTS: HKEX equities trade only in fixed board lots (set per-issuer
    at listing, e.g. 100 shares for Tencent/HSBC, 500-2000 for many mid/small caps, up to
    10000+ for low-priced penny stocks) — never fractional, and never an arbitrary share count.
    Real per-symbol lot sizes are NOT available anywhere in this app's data pipeline (checked:
    absent from yfinance's Ticker.info, absent from the Stock model, no HKEX scrape exists) —
    fetching them for real would need a new HKEX data-source integration, out of scope here.

    This is therefore a DELIBERATE, DOCUMENTED APPROXIMATION, not the real per-symbol value —
    a price-tier heuristic mirroring HKEX's own actual practice of assigning smaller lots to
    higher-priced stocks (so a lot's total cash value stays in a broadly similar range across
    price tiers), using the same price-tier-bucket shape already established by _round_step()
    a few lines above for an analogous purpose (tick size). The goal is not to fake precision
    this app cannot have — it's to stop paper HK entries from sizing to a fractional or
    arbitrary share count that could NEVER be filled by a real HK order, at any price tier.
    Treat the resulting quantity as "the right ORDER OF MAGNITUDE of a fillable lot count",
    not as "this specific symbol's real, confirmed board lot size."
    """
    if price >= 100: return 100
    if price >= 20:  return 200
    if price >= 5:   return 500
    if price >= 1:   return 1000
    if price >= 0.20: return 2000
    return 5000


# Style-specific overrides applied on top of defaults
_STYLE_OVERRIDES: dict[str, dict] = {
    "SHORT": {
        "max_hold_days": 10,   # SHORT signals are 3-5 day momentum plays; exit by day 10
        "hold_stall_days": 7,  # exit earlier than default if position stalls (shorter time horizon)
    },
    "GROWTH": {
        "max_hold_days": 60, "trail_atr_mult": 2.0,
        # T227-D: Separate trail trigger from BE trigger. Both were at 0.04 — when BE fired
        # at +4%, the trailing stop was also armed from that same peak, giving no additional
        # upside protection. Now BE fires at +4% (safety net) and trail arms at +7% (once the
        # trade has real momentum). 30-trade audit: open GROWTH trades avg +8% — they need room.
        "breakeven_trigger_pct": 0.04, "trail_trigger_pct": 0.07,
        # T227-E: Raise first partial TP from +12% to +15%. Open GROWTH trades cluster at
        # +11-17%. Partial TP at +12% was trimming positions when they had more room to run.
        # Second TP stays at +22% (no change).
        "partial_tp_pct": 0.15, "partial_tp2_pct": 0.22,
        "wait_exit_days": 5, "min_confidence": 45.0, "min_kscore": 48.0,
        "max_entry_gap_pct": 0.04,  # T171: GROWTH stocks are volatile; allow 4% gap before rejecting
    },
    "SWING": {
        "max_hold_days": 20,
        # T222-E: Wider trailing stop — winners were hitting +12-14% targets in 2-8 days.
        # 1.5× ATR was shaking out valid trades. 2.0× ATR gives winners room to breathe.
        "trail_atr_mult": 2.0,
        # Trail arms at +3%; breakeven at +1.5% for tighter SWING stops.
        "trail_trigger_pct": 0.03, "breakeven_trigger_pct": 0.015,
        # T222-E: Scale at +10% and +18% (was +7%/+10%) — winners averaged +13%, so old
        # partial TP at +7% was selling too soon and reducing average winner size.
        "partial_tp_pct": 0.10, "partial_tp2_pct": 0.18,
        "wait_exit_days": 3, "min_confidence": 50.0, "min_kscore": 52.0,
        "max_entry_gap_pct": 0.03,  # T171: SWING can't tolerate as much gap chasing
        # T225-A/T226-B: TA floor gate. T225 added at 0.50 (ta_lo50 31.4% win rate).
        # T226: raised to 0.65 — US SWING BUY avg_ta=0.622 (38% win), HK SWING BUY avg_ta=0.620 (26% win).
        "min_ta_score": 0.65,
        # T226-C: SWING requires score=5 in all regimes (was 4 default; already 5 in risk_off).
        # SWING entries with score=4 had 0% win rate in the 30-trade audit.
        "min_entry_score": 5,
    },
    "LONG": {
        "max_hold_days": 90, "trail_atr_mult": 2.0,
        "trail_trigger_pct": 0.06, "breakeven_trigger_pct": 0.04,
        "partial_tp_pct": 0.15, "partial_tp2_pct": 0.20,
        "wait_exit_days": 7, "min_confidence": 40.0, "min_kscore": 50.0,
        "max_entry_gap_pct": 0.05,  # T171: LONG can tolerate slightly more gap (wider stops, larger targets)
    },
}

# HK market needs looser circuit breakers — HSI stays risk_off longer than US indices
# during normal consolidation; HK stocks are more volatile (higher false-signal rate).
# Applied in _scan_for_entries when market == "HK" IF not already in portfolio.config.
_HK_MARKET_OVERRIDES: dict = {
    "regime_suspension_days":  7,   # 3d is too tight; HSI can be risk_off for weeks during consolidation
    "max_consecutive_losses":  3,   # Tightened from 5 — stop trading HK after 3 consecutive losses (same as US default)
    # T222-A: Tighter entry gates for HK — 0% win rate on 9 trades (June 2026 audit).
    # HK signals fire on momentum that doesn't sustain; require stronger conviction.
    "min_entry_score":         6,   # default is 4; HK requires 6 (stronger multi-factor conviction)
    "min_confidence":          65.0, # default is 45; HK BUY signals need higher ML confidence
    # T222-F: Reduce HK position sizing — HK ATR is large, a 2× ATR stop equals a huge % move.
    "trail_atr_mult":          1.5,  # tighter trailing stop (SWING default=1.5, GROWTH default=2.0)
    "max_position_pct":        0.07, # max 7% of equity per position (vs 10% US default)
    "risk_per_trade_pct":      0.007, # risk only 0.7% per trade (vs 1% US default)
    # T224-C/T226-B: TA score gate. T224 set 0.60; T226 raised to 0.65 to match SWING gate.
    # HK SWING BUY at 26% win rate with avg_ta=0.620 in June 2026 audit.
    "min_ta_score":            0.65,
}

# T234-CONFIG-DECIDE-DEFAULT-MISMATCH: the keys _scan_for_entries() actually gates real entries
# on (min_confidence/min_kscore/min_entry_score/min_ta_score/min_rr_ratio) — exposed via
# resolve_entry_gate_params() below so decision-engine's standalone /decide/{symbol}/explain
# path (which never runs _scan_for_entries' own merge, since it's not a real portfolio scan)
# can resolve the SAME real values instead of a disconnected hardcoded literal.
_ENTRY_GATE_KEYS = ("min_confidence", "min_kscore", "min_entry_score", "min_ta_score", "min_rr_ratio")


def resolve_entry_gate_params(style: str, market: str = "US") -> dict:
    """The real, live entry-gate thresholds a portfolio with this style/market and NO explicit
    portfolio.config overrides would actually use — same merge order _scan_for_entries() uses
    (_DEFAULT_CONFIG -> _STYLE_OVERRIDES[style] -> HK override if market == 'HK'), restricted to
    the subset of keys that are real entry GATES (not sizing/risk/exit params, which are out of
    scope for this endpoint's purpose). min_rr_ratio is resolved via _default_min_rr_ratio() so
    this stays calibration-aware too, matching how the real engine reads it, not a stale 2.0
    literal frozen at whatever _DEFAULT_CONFIG said before any calibration ever ran.
    """
    style = (style or "SWING").upper()
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
    if (market or "US").upper() == "HK":
        for _k, _v in _HK_MARKET_OVERRIDES.items():
            if _k in _ENTRY_GATE_KEYS:
                cfg[_k] = _v
    result = {k: cfg.get(k) for k in _ENTRY_GATE_KEYS if k in cfg}
    result["min_rr_ratio"] = _default_min_rr_ratio("neutral")
    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #2: regime_min_rr_ratio was never included here
    # at all — decision-engine's hard_rejects.py had its own disconnected bare `3.0` fallback
    # for the choppy/risk_off-tier R:R floor, with no way to pick up a calibrated value the way
    # min_rr_ratio (the neutral-regime tier) already does above. _default_min_rr_ratio() itself
    # already resolves this correctly (see _should_enter()'s own identical read at line ~1906);
    # the gap was purely that this endpoint — the one thing threading a calibration-aware
    # default into decision-engine's standalone /decide callers — never surfaced it.
    result["regime_min_rr_ratio"] = _default_min_rr_ratio("choppy")
    # min_ta_score has no _DEFAULT_CONFIG entry — 0.0 (gate disabled) is the correct default
    # when no style/market override set it, matching every other read site's own fallback.
    result.setdefault("min_ta_score", 0.0)
    return result


# ── ATR helper ────────────────────────────────────────────────────────────────

def _ewm_atr_from_ohlc(high: "pd.Series", low: "pd.Series", close: "pd.Series", period: int = 14) -> float | None:
    """Compute EWM-ATR from pre-fetched OHLC series. Returns None on bad data.

    T233-ARCH-INDICATOR-DEDUP: delegates to shared/common/indicators.py's canonical Wilder's
    ATR (pure refactor, no behavior change — this function always reads only the final row,
    and min_periods only affects EARLIER rows in the series; verified zero numeric difference
    on real data across multiple symbols before deploying).
    """
    if len(close) < period + 1:
        return None
    val = float(_canon_atr(high, low, close, period=period).iloc[-1])
    return val if pd.notna(val) and val > 0 else None


def _compute_atr(symbol: str, period: int = 14) -> float | None:
    """EWM-ATR(14) for a single symbol. Prefer _batch_compute_atr() for multiple symbols."""
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="40d")
        if len(hist) < period + 1:
            return None
        return _ewm_atr_from_ohlc(
            hist["High"].astype(float),
            hist["Low"].astype(float),
            hist["Close"].astype(float),
            period,
        )
    except Exception:
        return None


def _batch_compute_atr(symbols: list[str], period: int = 14) -> dict[str, float | None]:
    """PA-F1: Compute EWM-ATR for multiple symbols in ONE yfinance download.

    Reduces N individual HTTP calls to a single batch request.
    Falls back to per-symbol _compute_atr() if the batch call fails.
    """
    if not symbols:
        return {}
    result: dict[str, float | None] = {sym: None for sym in symbols}
    try:
        import yfinance as yf
        raw = yf.download(list(symbols), period="40d", auto_adjust=True, progress=False)
        if raw.empty:
            raise ValueError("empty response")
        # M4 FIX: yfinance returns a flat Series (not MultiIndex DataFrame) when only one
        # symbol is requested. Detect and reshape to a consistent single-row DataFrame.
        if isinstance(raw.columns, pd.MultiIndex):
            closes = raw["Close"]
            highs  = raw["High"]
            lows   = raw["Low"]
        else:
            # Single-symbol flat frame — wrap each column into a one-column DataFrame
            closes = raw[["Close"]].rename(columns={"Close": symbols[0]})
            highs  = raw[["High"]].rename(columns={"High": symbols[0]})
            lows   = raw[["Low"]].rename(columns={"Low": symbols[0]})
        for sym in symbols:
            try:
                c = closes[sym].dropna().astype(float)
                h = highs[sym].dropna().astype(float)
                l = lows[sym].dropna().astype(float)
                idx = c.index.intersection(h.index).intersection(l.index)
                result[sym] = _ewm_atr_from_ohlc(h.loc[idx], l.loc[idx], c.loc[idx], period)
            except Exception:
                result[sym] = _compute_atr(sym, period)  # per-symbol fallback
    except Exception as exc:
        log.warning("paper.batch_atr_failed", symbols=len(symbols), error=str(exc),
                    note="falling back to per-symbol fetch")
        for sym in symbols:
            result[sym] = _compute_atr(sym, period)
    return result


# ── Sector ETF mapping (PT-M1) ───────────────────────────────────────────────

_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":              "XLK",
    "Health Care":             "XLV",
    "Healthcare":              "XLV",
    "Financials":              "XLF",
    "Financial Services":      "XLF",
    "Energy":                  "XLE",
    "Consumer Discretionary":  "XLY",
    "Consumer Staples":        "XLP",
    "Industrials":             "XLI",
    "Materials":               "XLB",
    "Real Estate":             "XLRE",
    "Utilities":               "XLU",
    "Communication Services":  "XLC",
    "Telecommunications":      "XLC",
}


def _batch_sector_rs_lag(sym_sector_pairs: list[tuple[str, str | None]]) -> dict[str, bool]:
    """PT-M1: Detect stocks lagging their sector ETF by > 10pp over the last 5 trading days.

    Uses a single yfinance download for all stocks + their mapped ETFs.
    Returns {symbol: True} for stocks with confirmed sector relative weakness.
    """
    if not sym_sector_pairs:
        return {}

    stock_syms = [s for s, _ in sym_sector_pairs]
    sector_to_etf = {(sec or ""): _SECTOR_ETF_MAP.get(sec or "", "SPY") for _, sec in sym_sector_pairs}
    etf_syms = list(set(sector_to_etf.values()))
    all_syms = list(set(stock_syms + etf_syms))

    try:
        import yfinance as yf
        raw = yf.download(all_syms, period="15d", auto_adjust=True, progress=False)
        closes = raw["Close"] if "Close" in raw.columns else raw

        # Handle single-ticker edge case (yfinance returns Series, not DataFrame)
        if hasattr(closes, "name"):
            closes = closes.to_frame(name=closes.name)

        returns_5d: dict[str, float] = {}
        for sym in all_syms:
            if sym in closes.columns:
                s = closes[sym].dropna()
                if len(s) >= 6:
                    returns_5d[sym] = float(s.iloc[-1]) / float(s.iloc[-6]) - 1

        result: dict[str, bool] = {}
        for stock_sym, sector in sym_sector_pairs:
            etf = _SECTOR_ETF_MAP.get(sector or "", "SPY")
            stock_ret = returns_5d.get(stock_sym)
            etf_ret   = returns_5d.get(etf)
            if stock_ret is not None and etf_ret is not None:
                if etf_ret - stock_ret > 0.10:  # stock lagging sector ETF by > 10pp
                    result[stock_sym] = True
        return result

    except Exception as exc:
        log.warning("paper.sector_rs_lag_failed", error=str(exc))
        return {}


# ── Market hours check ───────────────────────────────────────────────────────

# AUD-M13: NYSE/NASDAQ market holiday calendar 2024–2027.
# Static list avoids a pandas_market_calendars dependency.
# Observed dates: when the holiday falls on Sat → Fri observed; Sun → Mon observed.
_NYSE_HOLIDAYS: frozenset[date] = frozenset([
    # 2024
    date(2024,  1,  1),  # New Year's Day
    date(2024,  1, 15),  # MLK Day
    date(2024,  2, 19),  # Presidents' Day
    date(2024,  3, 29),  # Good Friday
    date(2024,  5, 27),  # Memorial Day
    date(2024,  6, 19),  # Juneteenth
    date(2024,  7,  4),  # Independence Day
    date(2024,  9,  2),  # Labor Day
    date(2024, 11, 28),  # Thanksgiving
    date(2024, 12, 25),  # Christmas
    # 2025
    date(2025,  1,  1),  # New Year's Day
    date(2025,  1, 20),  # MLK Day
    date(2025,  2, 17),  # Presidents' Day
    date(2025,  4, 18),  # Good Friday
    date(2025,  5, 26),  # Memorial Day
    date(2025,  6, 19),  # Juneteenth
    date(2025,  7,  4),  # Independence Day
    date(2025,  9,  1),  # Labor Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026,  1,  1),  # New Year's Day
    date(2026,  1, 19),  # MLK Day
    date(2026,  2, 16),  # Presidents' Day
    date(2026,  4,  3),  # Good Friday
    date(2026,  5, 25),  # Memorial Day
    date(2026,  6, 19),  # Juneteenth
    date(2026,  7,  3),  # Independence Day (observed, July 4 = Sat)
    date(2026,  9,  7),  # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027
    date(2027,  1,  1),  # New Year's Day
    date(2027,  1, 18),  # MLK Day
    date(2027,  2, 15),  # Presidents' Day
    date(2027,  3, 26),  # Good Friday
    date(2027,  5, 31),  # Memorial Day
    date(2027,  6, 18),  # Juneteenth (observed, June 19 = Sat)
    date(2027,  7,  5),  # Independence Day (observed, July 4 = Sun)
    date(2027,  9,  6),  # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas (observed, Dec 25 = Sat)
])


def _is_market_hours(market: str = "US", as_of: datetime | None = None) -> bool:
    """True if `as_of` (default: the real current time) falls within the regular session for
    the given market.

    US: 9:30–16:00 ET Mon–Fri (NYSE holidays respected).
    HK: 09:30–12:00 and 13:00–16:00 HKT Mon–Fri.

    T233-SELFIMPROVE-PHASE2b: `as_of` exists so gate_harness.py's historical replay can check
    market hours AS OF a signal's own historical date/time, not the replay's real run-time —
    without it, EVERY historical replay of _should_enter() (which calls this unconditionally
    as a hard reject) would return zero entered trades unless the replay happened to run during
    real live market hours, since "now" always means the actual current moment regardless of
    what date is being replayed. Defaults to `None` -> `datetime.now()`, so every existing live
    caller (paper trading's real-time scan) is completely unaffected — this is purely additive.
    """
    from zoneinfo import ZoneInfo
    if market == "HK":
        now_hkt = as_of.astimezone(ZoneInfo("Asia/Hong_Kong")) if as_of else datetime.now(ZoneInfo("Asia/Hong_Kong"))
        if now_hkt.weekday() >= 5:
            return False
        # H-1: HKEX public holiday check (lazy import avoids circular dependency).
        # Was importing via the wrong absolute path (services.scheduler doesn't exist as a
        # top-level module here) — ImportError every call, so HK holidays were never
        # actually excluded from market hours (silently swallowed, no visible symptom).
        try:
            from .scheduler import _HK_HOLIDAYS as _hkh
            if (now_hkt.year, now_hkt.month, now_hkt.day) in _hkh:
                return False
        except ImportError:
            pass
        morning_open  = now_hkt.replace(hour=9,  minute=30, second=0, microsecond=0)
        morning_close = now_hkt.replace(hour=12, minute=0,  second=0, microsecond=0)
        aftnoon_open  = now_hkt.replace(hour=13, minute=0,  second=0, microsecond=0)
        aftnoon_close = now_hkt.replace(hour=16, minute=0,  second=0, microsecond=0)
        return (morning_open <= now_hkt < morning_close) or (aftnoon_open <= now_hkt < aftnoon_close)

    now_et = as_of.astimezone(ZoneInfo("America/New_York")) if as_of else datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if now_et.date() in _NYSE_HOLIDAYS:
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et < market_close


# ── Market regime engine ─────────────────────────────────────────────────────

_regime_cache: dict = {}          # last successful result
_regime_cache_ts: float = 0.0     # epoch seconds of last successful fetch

# T232-DE7: hysteresis state for regime classification. _fetch_market_regime() is called
# fresh (new yfinance download + reclassification, no debounce) on every _refresh_5m tick —
# every 5 minutes during market hours. A candidate sitting right at an EMA boundary can
# genuinely flip choppy<->bull on consecutive ticks with no real change in market conditions,
# swinging score/size_mult/min_rr on the same signal within minutes. A pending new state must
# now be observed on _REGIME_HYSTERESIS_TICKS consecutive classifications before it actually
# takes effect — a single-tick flip is treated as noise and the previously CONFIRMED state is
# returned instead.
_REGIME_HYSTERESIS_TICKS = 2
_regime_confirmed_state: str | None = None   # last state that passed hysteresis and was returned
_regime_pending_state: str | None = None     # raw state seen on the most recent call(s)
_regime_pending_count: int = 0               # consecutive ticks _regime_pending_state has held


def get_last_regime() -> dict:
    """Return the most recently cached market regime dict.

    If the cache is empty (e.g. after a container restart before any paper trading step),
    performs a fresh fetch so callers like the morning digest always get real data.

    AUD264-REGIME-FAILURE-DEFAULTS-DISAGREE: the lazy-fetch failure path used to return a bare
    {} — every consumer resolves an empty/missing "state" key via .get("state", "neutral"),
    the MOST PERMISSIVE regime (full size, no gate). A failure here means this service has lost
    visibility into market conditions, which should loosen nothing — matches _fetch_market_regime's
    own internal policy of defaulting to "choppy" (conservative) on an ambiguous/failed
    classification, not "neutral".
    """
    if _regime_cache:
        return dict(_regime_cache)
    # Lazy fetch on first call (container just started, no paper trading step yet)
    try:
        return _fetch_market_regime(_DEFAULT_CONFIG)
    except Exception:
        return {"state": "choppy", "notes": ["regime fetch failed — defaulting to conservative choppy state"]}


def _fetch_market_regime(cfg: dict) -> dict:
    """Download SPY/QQQ/VIX (300 days) and classify the current market regime.

    Returns a dict with:
      state       : "bull" | "neutral" | "choppy" | "risk_off" | "bear"
      spy_price   : float | None
      spy_ema20   : float | None
      spy_ema50   : float | None
      spy_ema200  : float | None
      spy_20d_ret : float | None   (% change over last 20 sessions)
      vix         : float | None
      qqq_price   : float | None
      qqq_ema50   : float | None
      notes       : list[str]      (human-readable explanation)
    """
    global _regime_cache, _regime_cache_ts
    result: dict = {
        "state": "neutral",
        "spy_price": None, "spy_ema20": None, "spy_ema50": None, "spy_ema200": None,
        "spy_20d_ret": None, "vix": None, "vix9d": None, "qqq_price": None, "qqq_ema50": None,
        "notes": [],
        # RE-9: early warning fields
        "spy_pct_above_ema20": None,  # how far SPY is above/below EMA20 as %
        "vix_5d_trend": None,         # "rising" | "falling" | "flat"
        "is_pre_choppy": False,       # True when neutral but showing deterioration signals
        "is_pre_risk_off": False,     # True when neutral/choppy with VIX trending toward risk_off
        # PT-M4: VIX term structure — VIX9D/VIX > 1.10 means near-term fear > medium-term → panic spike
        "vix_term_inverted": False,   # True when ^VIX9D / ^VIX > 1.10
        # PT-M5: Market breadth via IWM (Russell 2000) + MDY (S&P 400 mid-cap) vs 200EMA
        "breadth_weak": False,        # True when BOTH IWM and MDY below 200EMA
        "breadth_size_mult": 1.0,     # 0.80 if one below 200EMA; 0.60 if both
        "iwm_vs_ema200": None,        # IWM price / 200EMA (for logging)
        "mdy_vs_ema200": None,        # MDY price / 200EMA (for logging)
        # QW-8: HMM regime overlay
        "hmm_bear_pressure": False,   # True when HMM bear_prob > 0.50
        "hmm_bear_prob": None,        # raw bear posterior probability
        "hmm_state": None,            # "bull" | "neutral" | "choppy" | "bear"
    }
    # T232-DL-REGIME5X: this function was previously called fresh (a real yfinance download +
    # full reclassification) on EVERY _refresh_5m tick during market hours, with no debounce
    # of any kind — _regime_cache/_regime_cache_ts existed only as a 4-hour FAILURE fallback
    # (see the except block below), never as a rate limiter for the normal happy path. Mirrors
    # _fetch_hk_market_regime()'s own already-proven 30-minute freshness cache exactly — the
    # SAME TTL, not a new number invented for this fix, since HK's value already has its own
    # bug-fix history (T237-REG1/REG2) confirming it's safe in production. 30 minutes is also
    # comfortably ABOVE this function's own _REGIME_HYSTERESIS_TICKS mechanism (2 consecutive
    # calls, ~10 min at the 5-min refresh cadence) — hysteresis alone already guarantees no
    # state CHANGE takes effect faster than ~10 min, so classifying against data no older than
    # 30 min doesn't sacrifice any real responsiveness the rest of the system could act on.
    import time as _time
    if _regime_cache and (_time.time() - _regime_cache_ts) < 1800:
        return dict(_regime_cache)
    try:
        import yfinance as yf
        # 300 days needed to warm up the 200EMA; group_by default = by column
        # IWM (Russell 2000) + MDY (S&P 400) added for PT-M5 breadth check
        raw = yf.download(["SPY", "QQQ", "^VIX", "^VIX9D", "IWM", "MDY"], period="300d",
                          auto_adjust=True, progress=False)
        closes = raw["Close"] if "Close" in raw.columns else raw

        spy_s   = closes["SPY"].dropna()    if "SPY"    in closes.columns else None
        qqq_s   = closes["QQQ"].dropna()    if "QQQ"    in closes.columns else None
        vix_s   = closes["^VIX"].dropna()   if "^VIX"   in closes.columns else None
        vix9d_s = closes["^VIX9D"].dropna() if "^VIX9D" in closes.columns else None
        iwm_s   = closes["IWM"].dropna()    if "IWM"    in closes.columns else None
        mdy_s   = closes["MDY"].dropna()    if "MDY"    in closes.columns else None

        if spy_s is not None and len(spy_s) >= 20:
            result["spy_price"] = float(spy_s.iloc[-1])
            result["spy_ema20"] = float(spy_s.ewm(span=20, adjust=False).mean().iloc[-1])
            result["spy_ema50"] = float(spy_s.ewm(span=50, adjust=False).mean().iloc[-1])
            if len(spy_s) >= 200:
                result["spy_ema200"] = float(spy_s.ewm(span=200, adjust=False).mean().iloc[-1])
            result["spy_20d_ret"] = round(
                (float(spy_s.iloc[-1]) / float(spy_s.iloc[-20]) - 1) * 100, 2
            )

        if qqq_s is not None and len(qqq_s) >= 50:
            result["qqq_price"] = float(qqq_s.iloc[-1])
            result["qqq_ema50"] = float(qqq_s.ewm(span=50, adjust=False).mean().iloc[-1])

        if vix_s is not None and len(vix_s) >= 1:
            result["vix"] = float(vix_s.iloc[-1])
            # RE-9: VIX 5-day trend — rising VIX is an early deterioration signal
            if len(vix_s) >= 6:
                vix_now = float(vix_s.iloc[-1])
                vix_5d_ago = float(vix_s.iloc[-6])
                if vix_now > vix_5d_ago * 1.08:   # up > 8% in 5 sessions
                    result["vix_5d_trend"] = "rising"
                elif vix_now < vix_5d_ago * 0.92:  # down > 8% in 5 sessions
                    result["vix_5d_trend"] = "falling"
                else:
                    result["vix_5d_trend"] = "flat"

        # PT-M4: VIX9D (9-day VIX) vs VIX (30-day) term structure.
        # Inverted term structure (short-term fear > medium-term) precedes risk-off regimes.
        if vix9d_s is not None and len(vix9d_s) >= 1 and result["vix"]:
            vix9d_val = float(vix9d_s.iloc[-1])
            result["vix9d"] = vix9d_val
            if vix9d_val / result["vix"] > 1.10:
                result["vix_term_inverted"] = True

        # PT-M5: Market breadth via IWM (Russell 2000) + MDY (S&P 400 mid-cap).
        # Both below their 200EMA signals a narrow market — mega-caps masking widespread weakness.
        iwm_below = False
        mdy_below = False
        if iwm_s is not None and len(iwm_s) >= 200:
            iwm_price = float(iwm_s.iloc[-1])
            iwm_e200  = float(iwm_s.ewm(span=200, adjust=False).mean().iloc[-1])
            result["iwm_vs_ema200"] = round(iwm_price / iwm_e200, 4)
            iwm_below = iwm_price < iwm_e200
        if mdy_s is not None and len(mdy_s) >= 200:
            mdy_price = float(mdy_s.iloc[-1])
            mdy_e200  = float(mdy_s.ewm(span=200, adjust=False).mean().iloc[-1])
            result["mdy_vs_ema200"] = round(mdy_price / mdy_e200, 4)
            mdy_below = mdy_price < mdy_e200
        # Two-tier breadth sizing: one below → 80%; both below → 60%
        if iwm_below and mdy_below:
            result["breadth_weak"]      = True
            result["breadth_size_mult"] = 0.60
        elif iwm_below or mdy_below:
            result["breadth_size_mult"] = 0.80

    except Exception as exc:
        import time as _time
        age = _time.time() - _regime_cache_ts
        if _regime_cache and age < 14_400:  # use cache if < 4 hours old
            log.warning("paper.regime_fallback_to_cached", error=str(exc), cache_age_min=round(age / 60, 1))
            return dict(_regime_cache)
        # cache stale or empty — default to choppy (conservative) not neutral (full-size)
        log.warning("paper.regime_fallback_to_choppy", error=str(exc))
        result["state"] = "choppy"
        result["notes"] = ["regime fetch failed — conservative choppy default"]
        return result

    spy   = result["spy_price"]
    e20   = result["spy_ema20"]
    e50   = result["spy_ema50"]
    e200  = result["spy_ema200"]
    vix   = result["vix"]
    ret20 = result["spy_20d_ret"]
    notes = result["notes"]

    vix_fear = cfg.get("regime_vix_fear", 30.0)
    vix_high = cfg.get("regime_vix_high", 25.0)

    # BEAR — most severe: breadth of market breakdown + extreme fear
    if spy and e50 and vix and spy < e50 and vix > vix_fear:
        notes.append(f"SPY ${spy:.0f} below 50EMA ${e50:.0f}; VIX {vix:.1f} > {vix_fear:.0f}")
        result["state"] = "bear"
    elif spy and e200 and ret20 is not None and spy < e200 and ret20 < -8:
        notes.append(f"SPY ${spy:.0f} below 200EMA ${e200:.0f}; 20d return {ret20:.1f}%")
        result["state"] = "bear"

    # RISK_OFF — M1 FIX: require BOTH legs (SPY lost 50EMA AND VIX elevated).
    # Pure OR caused a single elevated-VIX print to drop the whole portfolio to 50% size
    # with no SPY confirmation, triggering risk_off during normal VIX-22 grinds.
    elif spy and e50 and vix and spy < e50 and vix > vix_high:
        notes.append(f"SPY ${spy:.0f} below 50EMA ${e50:.0f}; VIX {vix:.1f} > {vix_high:.0f}")
        result["state"] = "risk_off"

    # CHOPPY — SPY below 20EMA OR VIX elevated (single signal is enough for caution)
    elif (spy and e20 and spy < e20) or (vix and vix > 20):
        if spy and e20 and spy < e20:
            notes.append(f"SPY ${spy:.0f} below 20EMA ${e20:.0f}")
        if vix and vix > 20:
            notes.append(f"VIX {vix:.1f} > 20")
        result["state"] = "choppy"

    # BULL — clean trend + normal volatility (VIX < 20 covers the full healthy bull range)
    elif spy and e20 and e50 and vix and spy > e20 and spy > e50 and vix < 20:
        notes.append(f"SPY ${spy:.0f} above 20/50EMA; VIX {vix:.1f} < 20")
        result["state"] = "bull"

    # NEUTRAL — everything else
    else:
        spy_str = f"${spy:.0f}" if spy else "unknown"
        notes.append(f"SPY {spy_str} — mixed signals")
        result["state"] = "neutral"

    # T232-DE7: hysteresis — require a NEW state to be observed on 2 consecutive ticks before
    # it actually takes effect, so a single boundary-noise flip doesn't change what every
    # gate/sizing/min_rr check sees. Escalation into the two most severe states (bear,
    # risk_off) is NOT delayed — hysteresis exists to filter noise between benign states, not
    # to slow down the system's reaction to a genuine deterioration signal. De-escalating OUT
    # of bear/risk_off still requires confirmation, same as any other transition.
    global _regime_confirmed_state, _regime_pending_state, _regime_pending_count
    _raw_state = result["state"]
    if _regime_confirmed_state is None:
        # First classification since startup — nothing to compare against, accept immediately.
        _regime_confirmed_state = _raw_state
        _regime_pending_state, _regime_pending_count = _raw_state, _REGIME_HYSTERESIS_TICKS
    elif _raw_state == _regime_confirmed_state:
        _regime_pending_state, _regime_pending_count = _raw_state, 0
    elif _raw_state in ("bear", "risk_off"):
        notes.append(f"regime hysteresis bypassed for escalation to {_raw_state}")
        _regime_confirmed_state = _raw_state
        _regime_pending_state, _regime_pending_count = _raw_state, 0
    else:
        if _regime_pending_state == _raw_state:
            _regime_pending_count += 1
        else:
            _regime_pending_state, _regime_pending_count = _raw_state, 1
        if _regime_pending_count >= _REGIME_HYSTERESIS_TICKS:
            _regime_confirmed_state = _raw_state
        else:
            notes.append(
                f"regime hysteresis: raw={_raw_state} pending {_regime_pending_count}/"
                f"{_REGIME_HYSTERESIS_TICKS} ticks — still reporting confirmed={_regime_confirmed_state}"
            )
    result["raw_state"] = _raw_state
    result["state"] = _regime_confirmed_state

    # RE-9: Compute early warning flags AFTER state classification
    if spy and e20:
        result["spy_pct_above_ema20"] = round((spy / e20 - 1) * 100, 2)
        spy_pct = result["spy_pct_above_ema20"]
        vix_trend = result.get("vix_5d_trend")
        vix_cur = result.get("vix")
        state = result["state"]

        # pre_choppy: regime looks fine (bull/neutral) but EMA20 proximity + rising VIX suggests
        # it's about to flip — apply choppy entry thresholds NOW, not after the damage is done
        if state in ("bull", "neutral") and spy_pct < 1.5 and vix_trend == "rising":
            result["is_pre_choppy"] = True
            notes.append(f"RE-9 pre-choppy warning: SPY only {spy_pct:.1f}% above EMA20, VIX rising")

        # pre_risk_off: SPY close to 50EMA AND VIX already elevated (22+) but not yet official risk_off
        if spy and e50 and vix_cur and state in ("neutral", "choppy") and (spy / e50 - 1) * 100 < 2.0 and vix_cur > 22:
            result["is_pre_risk_off"] = True
            notes.append(f"RE-9 pre-risk_off warning: SPY {(spy/e50-1)*100:.1f}% above 50EMA, VIX {vix_cur:.1f}")

        # PT-M4: Inverted VIX term structure (VIX9D/VIX > 1.10) in bull/neutral → elevate to pre_risk_off.
        # Short-term fear spiking above medium-term fear is a reliable early panic signal even before
        # SPY loses its EMAs — gives us a session head-start over the standard RE-9 trigger.
        if result.get("vix_term_inverted") and state in ("bull", "neutral"):
            result["is_pre_risk_off"] = True
            vix9d_val = result.get("vix9d") or 0
            notes.append(f"PT-M4 VIX term structure inverted: VIX9D {vix9d_val:.1f} / VIX {vix_cur:.1f} > 1.10")

        # PT-M5: Breadth weakness (IWM + MDY below 200EMA) in bull/neutral → elevate to pre_risk_off.
        # A bull-regime SPY reading is unreliable when small/mid caps are already in a downtrend.
        if result.get("breadth_weak") and state in ("bull", "neutral"):
            result["is_pre_risk_off"] = True
            notes.append(
                f"PT-M5 breadth weak: IWM/200EMA={result.get('iwm_vs_ema200', 0):.3f}, "
                f"MDY/200EMA={result.get('mdy_vs_ema200', 0):.3f} — narrow market"
            )

    # QW-8: HMM regime overlay — fail-open, in-process (T233-ARCH-HMMREGIME: was an HTTP call
    # to ml-prediction:8003/regime-state on every regime computation; colocated 2026-07-04
    # since this was the only consumer anywhere in the codebase — a real network hop
    # eliminated, not just a cosmetic move).
    # bear_prob > 0.50 triggers a 30% position size reduction on top of rule-based regime sizing.
    # Detects early-phase downturns via volatility clustering before price action confirms.
    try:
        from .hmm_regime import predict_current as _hmm_predict_current
        _hmm_d = _hmm_predict_current()
        if not _hmm_d.get("error"):
            _bear_p = float(_hmm_d.get("hmm_prob", {}).get("bear", 0.0))
            result["hmm_bear_prob"] = round(_bear_p, 4)
            result["hmm_state"] = _hmm_d.get("hmm_state")
            if _bear_p > 0.50:
                result["hmm_bear_pressure"] = True
    except Exception as _hmm_exc:
        log.debug("paper.hmm_regime_fetch_skipped", error=str(_hmm_exc))

    log.info("paper.regime_classified",
             state=result["state"],
             spy=result["spy_price"],
             ema20=result["spy_ema20"] and round(result["spy_ema20"], 2),
             ema50=result["spy_ema50"] and round(result["spy_ema50"], 2),
             vix=result["vix"],
             vix9d=result["vix9d"],
             vix_term_inverted=result["vix_term_inverted"],
             breadth_weak=result["breadth_weak"],
             breadth_size_mult=result["breadth_size_mult"],
             iwm_vs_ema200=result["iwm_vs_ema200"],
             mdy_vs_ema200=result["mdy_vs_ema200"],
             spy_20d_ret=result["spy_20d_ret"],
             spy_pct_above_ema20=result["spy_pct_above_ema20"],
             vix_5d_trend=result["vix_5d_trend"],
             is_pre_choppy=result["is_pre_choppy"],
             is_pre_risk_off=result["is_pre_risk_off"],
             hmm_state=result["hmm_state"],
             hmm_bear_prob=result["hmm_bear_prob"],
             hmm_bear_pressure=result["hmm_bear_pressure"],
             notes=notes)
    import time as _time
    _regime_cache = dict(result)
    _regime_cache_ts = _time.time()
    return result


_hk_regime_cache: dict = {}
_hk_regime_cache_ts: float = 0.0


def _compute_hk_breadth() -> float | None:
    """% of tracked HK stocks trading above their own 200-day SMA.

    Distinguishes a broad HSI decline from a handful of mega-caps dragging the index
    down (e.g. a single Tencent/Alibaba selloff) — the index-only dual-SMA check can't
    tell these apart. Uses the existing tracked HK universe (no HSI constituent list
    needed) — only stocks with >=200 daily bars are counted, mirroring the sample the
    US breadth_pct calculation implicitly relies on via its own universe.
    """
    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(Stock.id, Stock.symbol).where(
                    Stock.market == "HK", Stock.active.is_(True),
                    # BUG-DELISTED-GENERATION-BLIND: a delisted stock frozen at its last real
                    # price shouldn't count toward the market-wide breadth %, which feeds
                    # regime classification.
                    Stock.delisted.is_(False),
                )
            ).all()
            if not rows:
                return None
            stock_ids = [r.id for r in rows]
            price_rows = session.execute(
                select(Price.stock_id, Price.close)
                .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1)
                .order_by(Price.stock_id, Price.ts.desc())
            ).all()
            from collections import defaultdict as _dd
            closes_by_stock: dict[int, list[float]] = _dd(list)
            for sid, close in price_rows:
                if len(closes_by_stock[sid]) < 200:
                    closes_by_stock[sid].append(float(close))
            above = total = 0
            for sid, closes in closes_by_stock.items():
                if len(closes) < 200:
                    continue
                total += 1
                sma200 = sum(closes) / len(closes)
                if closes[0] >= sma200:  # closes[0] is most recent (DESC order)
                    above += 1
            if total < 10:  # too small a sample to be meaningful
                return None
            return round(above / total * 100, 1)
    except Exception as exc:
        log.warning("paper.hk_breadth_calc_failed", error=str(exc))
        return None


# T237-REG3: HK regime had no hysteresis at all, unlike the US side's T232-DE7 mechanism —
# despite facing the same boundary-noise risk (hard SMA-ratio comparisons that can flip on a
# fractional HSI move near a threshold). Mirror the exact same 2-tick-confirmation pattern,
# with its own independent state so US and HK never share/leak classification state.
_hk_regime_confirmed_state: str | None = None
_hk_regime_pending_state: str | None = None
_hk_regime_pending_count: int = 0


def _fetch_hk_market_regime(cfg: dict) -> dict:
    """HK regime detection using dual SMA (50 + 200) + breadth confirmation.

    Returns a simplified regime dict compatible with the US version.
    No VIX equivalent — uses HSI position vs both SMA50 and SMA200:
      bull     : HSI > SMA200 and 20d return > 0
      neutral  : HSI > SMA200 but 20d return ≤ 0 (topping / momentum fade)
      choppy   : HSI below SMA200 but above SMA50 (recovering) — T237-REG2: this docstring
                 previously also claimed "or within ±5% of SMA200", but no such band exists
                 anywhere in the actual if/elif classification chain below; removed the false claim
      risk_off : HSI 8–15% below SMA200 AND below SMA50 (sustained downtrend, 50% size)
      bear     : HSI > 15% below SMA200 AND below SMA50 (extreme crash, hard block)

    Breadth confirmation (T232-HKBREADTH): a risk_off/bear call driven by index-level
    weakness alone is downgraded one tier when breadth (% of tracked HK stocks above their
    own 200d SMA) is NOT also weak (<40%) — i.e. the decline looks concentrated in a few
    heavyweights rather than broad-based. This does not apply in the other direction: real
    broad-based weakness (breadth <40%) does not escalate a milder index reading.
    """
    global _hk_regime_cache, _hk_regime_cache_ts
    import time as _time
    if _hk_regime_cache and (_time.time() - _hk_regime_cache_ts) < 1800:
        return dict(_hk_regime_cache)

    result: dict = {
        "state": "neutral", "vix": None, "spy_price": None,
        "spy_ema20": None, "spy_ema50": None, "spy_ema200": None,
        "spy_20d_ret": None, "qqq_price": None, "qqq_ema50": None,
        "breadth_weak": False, "breadth_size_mult": 1.0, "breadth_pct": None,
        "vix_term_inverted": False, "vix_5d_trend": None,
        "spy_pct_above_ema20": None, "is_pre_choppy": False, "is_pre_risk_off": False,
        "hmm_bear_pressure": False, "hmm_bear_prob": None, "hmm_state": None,
        "notes": [],
    }
    try:
        import yfinance as yf
        raw = yf.download("^HSI", period="300d", auto_adjust=True, progress=False)
        closes = raw["Close"].dropna() if "Close" in raw.columns else raw.dropna()
        if len(closes) < 50:
            # T237-REG1: was defaulting to (and caching for 30min) "neutral" — the most
            # permissive state, full position size — on a data outage. Mirror the US
            # function's fallback: fresh cache if available, else conservative "choppy".
            log.warning("paper.hk_regime_insufficient_data", n_closes=len(closes))
            if _hk_regime_cache and (_time.time() - _hk_regime_cache_ts) < 14_400:
                return dict(_hk_regime_cache)
            result["state"] = "choppy"
            result["notes"].append("HSI data insufficient — conservative choppy default")
            return result

        hsi_price = float(closes.iloc[-1])
        sma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else float(closes.mean())
        sma50  = float(closes.tail(50).mean())  if len(closes) >= 50  else float(closes.mean())
        sma20  = float(closes.tail(20).mean())
        ret20  = (hsi_price / float(closes.iloc[-20]) - 1) if len(closes) >= 20 else 0.0

        pct_above_200 = (hsi_price / sma200 - 1) if sma200 else 0.0
        pct_above_50  = (hsi_price / sma50  - 1) if sma50  else 0.0
        above_sma50   = hsi_price >= sma50
        result["spy_price"]   = hsi_price      # reuse field for UI compat
        result["spy_ema200"]  = sma200
        result["spy_ema50"]   = sma50
        result["spy_ema20"]   = sma20
        result["spy_20d_ret"] = round(ret20 * 100, 2)

        # HK regime — dual-SMA (50 + 200) to distinguish recovery from sustained downtrend.
        # SMA200 alone is too restrictive: a market recovering from a trough is below its SMA200
        # but above its SMA50. Treating that as risk_off blocks trades in a rising market.
        #
        #   bear     : HSI > 15% below SMA200 AND below SMA50 — extreme crash, hard block
        #   risk_off : HSI > 8% below SMA200 AND below SMA50 — sustained downtrend (50% size)
        #   choppy   : HSI below SMA200 but ABOVE SMA50 (recovering)
        #   neutral  : HSI above SMA200, 20d return ≤ 0
        #   bull     : HSI above SMA200, 20d return > 0
        if hsi_price < sma200 * 0.85 and not above_sma50:
            result["state"] = "bear"
            result["notes"].append(f"HSI {pct_above_200*100:.1f}% below SMA200 + below SMA50 → bear")
        elif hsi_price < sma200 * 0.92 and not above_sma50:
            result["state"] = "risk_off"
            result["notes"].append(f"HSI {pct_above_200*100:.1f}% below SMA200 + below SMA50 → risk_off (50% size)")
        elif hsi_price < sma200:
            # Below SMA200 but above SMA50: short-term trend is up — recovering market
            result["state"] = "choppy"
            result["notes"].append(f"HSI {pct_above_200*100:.1f}% below SMA200 but above SMA50 ({pct_above_50*100:.1f}%) → choppy (recovering)")
        elif ret20 <= 0:
            result["state"] = "neutral"
            result["notes"].append(f"HSI above SMA200 but 20d return {ret20*100:.1f}% → neutral")
        else:
            result["state"] = "bull"
            result["notes"].append(f"HSI {pct_above_200*100:.1f}% above SMA200 + positive 20d return → bull")

        # T232-HKBREADTH: confirm index-level bear/risk_off calls with breadth. A decline
        # concentrated in a few heavyweights (breadth NOT weak) is downgraded one tier —
        # broad-based weakness (breadth IS weak, <40%) leaves the call unchanged. Never
        # escalates a milder reading — this only softens bear/risk_off, one direction.
        breadth_pct = _compute_hk_breadth()
        result["breadth_pct"] = breadth_pct
        if breadth_pct is not None:
            result["breadth_weak"] = breadth_pct < 40.0
            if result["state"] == "bear" and breadth_pct >= 40.0:
                result["state"] = "risk_off"
                result["notes"].append(
                    f"Breadth {breadth_pct:.0f}% not broadly weak — downgraded bear→risk_off (decline looks concentrated)")
            elif result["state"] == "risk_off" and breadth_pct >= 40.0:
                result["state"] = "choppy"
                result["notes"].append(
                    f"Breadth {breadth_pct:.0f}% not broadly weak — downgraded risk_off→choppy (decline looks concentrated)")

        # T237-REG3: hysteresis on the final (post-breadth) classification — same 2-tick
        # confirmation pattern as the US side's T232-DE7, with its own independent module
        # state. Escalation into bear/risk_off bypasses the delay; de-escalating out of them
        # still requires confirmation, same rationale as the US mechanism.
        global _hk_regime_confirmed_state, _hk_regime_pending_state, _hk_regime_pending_count
        _hk_raw_state = result["state"]
        if _hk_regime_confirmed_state is None:
            _hk_regime_confirmed_state = _hk_raw_state
            _hk_regime_pending_state, _hk_regime_pending_count = _hk_raw_state, _REGIME_HYSTERESIS_TICKS
        elif _hk_raw_state == _hk_regime_confirmed_state:
            _hk_regime_pending_state, _hk_regime_pending_count = _hk_raw_state, 0
        elif _hk_raw_state in ("bear", "risk_off"):
            result["notes"].append(f"regime hysteresis bypassed for escalation to {_hk_raw_state}")
            _hk_regime_confirmed_state = _hk_raw_state
            _hk_regime_pending_state, _hk_regime_pending_count = _hk_raw_state, 0
        else:
            if _hk_regime_pending_state == _hk_raw_state:
                _hk_regime_pending_count += 1
            else:
                _hk_regime_pending_state, _hk_regime_pending_count = _hk_raw_state, 1
            if _hk_regime_pending_count >= _REGIME_HYSTERESIS_TICKS:
                _hk_regime_confirmed_state = _hk_raw_state
            else:
                result["notes"].append(
                    f"regime hysteresis: raw={_hk_raw_state} pending {_hk_regime_pending_count}/"
                    f"{_REGIME_HYSTERESIS_TICKS} ticks — still reporting confirmed={_hk_regime_confirmed_state}"
                )
        result["raw_state"] = _hk_raw_state
        result["state"] = _hk_regime_confirmed_state

    except Exception as exc:
        # T237-REG1: same fallback fix as the insufficient-data branch above — use fresh
        # cache if available, else conservative "choppy", never the permissive "neutral"
        # default this dict was initialized with.
        log.warning("paper.hk_regime_fetch_failed", error=str(exc))
        if _hk_regime_cache and (_time.time() - _hk_regime_cache_ts) < 14_400:
            log.warning("paper.hk_regime_fallback_to_cached", cache_age_min=round((_time.time() - _hk_regime_cache_ts) / 60, 1))
            return dict(_hk_regime_cache)
        result["state"] = "choppy"
        result["notes"].append(f"HSI fetch failed — conservative choppy default: {exc}")
        _hk_regime_cache = dict(result)
        _hk_regime_cache_ts = _time.time()
        return result

    _hk_regime_cache = dict(result)
    _hk_regime_cache_ts = _time.time()
    return result


def get_last_hk_regime() -> dict:
    """Return the most recently cached HK market regime dict.

    Mirrors get_last_regime() for the US side. T232-DL-REGIME5X: exposed via
    GET /stocks/regime?market=HK so decision-engine and signal-engine can call this single
    implementation over HTTP instead of maintaining their own independent classifiers.

    AUD264-REGIME-FAILURE-DEFAULTS-DISAGREE: mirrors get_last_regime()'s own fix — a bare {}
    resolves to the most permissive "neutral" state via every consumer's own .get("state",
    "neutral") fallback, exactly when this service has lost visibility into HK market
    conditions. Defaults to conservative "choppy" instead, matching the US side.
    """
    if _hk_regime_cache:
        return dict(_hk_regime_cache)
    try:
        return _fetch_hk_market_regime(_DEFAULT_CONFIG)
    except Exception:
        return {"state": "choppy", "notes": ["regime fetch failed — defaulting to conservative choppy state"]}


# ── Live price fetch ──────────────────────────────────────────────────────────

def _fetch_live_prices(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch live prices via ONE yf.download() call.

    BUG-YFCALLVOL (2026-08-07): this used to loop `tickers.tickers[sym].fast_info` per
    symbol — despite the docstring's own "batch" claim, `yf.Tickers(...)` does not actually
    batch `.fast_info` under the hood; each `.fast_info` access is still a separate HTTP
    request. Called every 5 min during market hours across ~100+ symbols (open positions +
    watchlist candidates), this was silently issuing ~100+ individual yfinance requests per
    cycle — a real, avoidable amplifier of yfinance rate-limit pressure. `_fetch_live_bulk()`
    in api/routes.py already solves the identical problem correctly with one `yf.download()`
    call for the dashboard's live prices — mirrored here.
    """
    if not symbols:
        return {}
    prices: dict[str, float] = {}
    try:
        import yfinance as yf
        raw = yf.download(
            symbols, period="2d", interval="1d", auto_adjust=True, progress=False, group_by="ticker",
        )
        if raw is None or raw.empty:
            return {}
        for sym in symbols:
            try:
                closes = raw[sym]["Close"].dropna() if len(symbols) > 1 else raw["Close"].dropna()
                if closes.empty:
                    continue
                p = float(closes.iloc[-1])
                if p >= 0.50:  # reject zero, $0.01 delisted/error prices
                    prices[sym] = p
            except Exception:
                continue
    except Exception as exc:
        log.warning("paper.live_price_fetch_failed", error=str(exc))
    return prices


def _composite_priority(row) -> float:
    """PT-D6: composite candidate sort priority — confidence + K-Score + breakout context.
    Extracted to module level (was a local closure inside _scan_for_entries) so it's
    independently unit-testable.

    T247-MARKETDATA-KSCORE-FALSY: `rank_r.score or 50.0` / `if rank_r and rank_r.score`
    treated a real K-Score of exactly 0.0 (a valid, clipped [0,100] value per
    ranking-engine's kscore.py) as falsy, silently substituting the unranked-neutral default
    of 50 — inflating a genuinely rock-bottom candidate's composite priority by 0.15 (0.3
    weight x 0.5) and letting it out-rank a real, correctly-scored mediocre candidate for one
    of the day's limited entry slots. Use `is not None` so only a MISSING ranking (rank_r is
    None, or rank_r.score itself is None) falls back to 50.
    """
    sig_r, _, rank_r = row
    conf_score = float(sig_r.confidence or 0.0) / 100.0
    kscore_score = float(rank_r.score) / 100.0 if rank_r is not None and rank_r.score is not None else 0.5
    sr = (sig_r.reasons or {}).get("sr_context", "neutral")
    breakout_bonus = 1.0 if sr == "breakout" else 0.5 if sr == "at_support" else 0.0
    return 0.5 * conf_score + 0.3 * kscore_score + 0.2 * breakout_bonus


# ── T258-PORTFOLIO-CORRELATION-PREENTRY: pairwise correlation vs. the open book ────────────
# market-data has direct DB access to Price/Stock (unlike portfolio-optimizer, which fetches
# over HTTP for the same math in its own /portfolio-risk/risk endpoint — see that endpoint's
# own module docstring for why it has to). Reimplementing the same df.corr() math here as a
# local, direct DB query avoids an HTTP round-trip on the hottest, most capital-sensitive
# code path in the system.

_CORR_LOOKBACK_DAYS = 30
_CORR_MIN_OVERLAP_ROWS = 10  # both series need at least this many overlapping days to trust


def _bulk_fetch_daily_closes(session, stock_ids: list[int]) -> "pd.DataFrame":
    """One bulk query for daily closes across all given stock_ids, pivoted into a wide
    DataFrame (columns = stock_id, index = date, values = close). Called ONCE per scan cycle
    for the open book, not once per candidate — see _scan_for_entries' call site."""
    if not stock_ids:
        return pd.DataFrame()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CORR_LOOKBACK_DAYS)
    rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close).where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= cutoff,
        ).order_by(Price.stock_id, Price.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["stock_id", "ts", "close"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.date
    wide = df.pivot_table(index="ts", columns="stock_id", values="close", aggfunc="last")
    return wide


def _max_correlation_with_open_positions(
    session, candidate_stock_id: int, open_stock_ids: list[int], open_closes_cache: "pd.DataFrame",
) -> float | None:
    """Return the highest pairwise daily-return correlation between the candidate and any
    currently-open position, or None if it can't be computed (no open positions, insufficient
    overlapping history, etc.) — None means "don't apply the score layer", not zero
    correlation, since the two have very different implications for _should_enter()'s score.

    open_closes_cache is the ALREADY bulk-fetched wide DataFrame for every open position's
    stock_id (built once per scan cycle by _bulk_fetch_daily_closes — see _scan_for_entries'
    call site), not re-fetched here. Only the candidate's own closes are fetched fresh per call.
    """
    open_stock_ids = [sid for sid in open_stock_ids if sid != candidate_stock_id]
    if not open_stock_ids or open_closes_cache.empty:
        return None
    try:
        cand_wide = _bulk_fetch_daily_closes(session, [candidate_stock_id])
        if cand_wide.empty or candidate_stock_id not in cand_wide.columns:
            return None
        combined = open_closes_cache.join(cand_wide[[candidate_stock_id]], how="outer")
        returns = combined.pct_change().dropna(how="all")
        cand_rets = returns[candidate_stock_id].dropna()
        best: float | None = None
        for open_id in open_stock_ids:
            if open_id not in returns.columns:
                continue
            open_rets = returns[open_id].dropna()
            aligned = pd.concat([cand_rets, open_rets], axis=1, join="inner")
            if len(aligned) < _CORR_MIN_OVERLAP_ROWS:
                continue
            c = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if pd.isna(c):
                continue
            if best is None or abs(c) > abs(best):
                best = c
        return best
    except Exception as exc:
        log.warning("paper.correlation_check_failed", error=str(exc))
        return None


# ── Entry qualifier ───────────────────────────────────────────────────────────

def _should_enter(
    symbol: str,
    signal_data: dict,
    live_price: float,
    game_plan: dict,
    cfg: dict,
    live_regime: dict | None = None,
    kscore: float | None = None,
    max_open_corr: float | None = None,
    as_of: datetime | None = None,
    recent_win_rate: float | None = None,
) -> tuple[bool, int, list[str]]:
    """Score current conditions to decide if NOW is a good time to enter.

    Returns (should_enter, score, notes_list).
    Score >= cfg['min_entry_score'] → ENTER.
    Hard-reject conditions return (False, -99, [reason]) regardless of score.

    max_open_corr (T258-PORTFOLIO-CORRELATION-PREENTRY): the highest pairwise daily-return
    correlation between this candidate and any currently-open position in the SAME portfolio,
    or None if it couldn't be computed (insufficient price history, no open positions, etc.).
    Advisory only — same -1/+0 shape as the other score layers here, never a hard reject. The
    correlation math itself lives in _max_correlation_with_open_positions() below and is
    computed once per scan cycle by the caller (_scan_for_entries), not per candidate here.

    as_of (T233-SELFIMPROVE-PHASE2b): defaults to `None` -> every wall-clock check below
    resolves to the REAL current time, exactly as before this parameter existed — every live
    caller is unaffected. gate_harness.py's historical replay passes the signal's own
    historical date/time instead, so the market-hours/time-of-day/macro-blackout hard rejects
    below are evaluated AS OF the signal's real date, not the replay's actual run-time. Without
    this, a replay of `_should_enter()` would reject essentially every historical signal
    outside of whatever moment the replay happens to actually execute — a real bug caught via
    live verification against production before Phase 2b was considered deployable.

    recent_win_rate (T232-DL-DUALSCORER-DEBT item #30): mirrors decision-engine's
    min_score_for_regime() own win-rate floor bump (+1 to the additive-threshold floor when
    < 30%) — this fallback previously computed _recent_wr at the caller level (_scan_for_entries)
    purely to forward it to DE, with no local equivalent, so a DE outage during a losing streak
    got no extra selectivity here even though DE (when reachable) would already be stricter.
    Only applied on the ADDITIVE-THRESHOLD branch below (calibrated-logistic bypass already has
    its own, statistically-fit threshold and is left untouched, matching DE's own scorer.py,
    which has no calibrated-logistic path at all to apply this bump to differently).
    """
    _now = as_of or datetime.now(timezone.utc)
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

    # AUD232-021: decision-engine's hard_rejects.py has a T193 market-closed guard (weekend/
    # NYSE-holiday/outside-trading-hours) that this fallback lacked entirely — the scan is only
    # ever SCHEDULED mon-fri (scheduler.py's CronTrigger), so weekends already can't reach here,
    # but a US market holiday landing on a weekday (e.g. July 4th) would otherwise slip through
    # with no check. Reuse the existing _is_market_hours() helper (already NYSE/HKEX-holiday-aware)
    # rather than duplicating hard_rejects.py's separate holiday list.
    if not _is_market_hours(cfg.get("market", "US"), as_of=_now):
        return False, -99, ["Market closed — outside regular trading session or a market holiday"]

    # Confidence hard floor — signals between 90%–100% of min_confidence get scored
    # but signals truly below 90% are rejected here (SQL filter lets them through at 90%)
    confidence = float(signal_data.get("confidence") or 0.0)
    min_conf = cfg.get("min_confidence", _DEFAULT_CONFIG["min_confidence"])
    if confidence < min_conf * 0.90:
        return False, -99, [f"Confidence {confidence:.1f}% below floor {min_conf * 0.90:.1f}%"]

    # R:R check — enforce minimum stop distance to prevent infinite/backward R:R
    stop_dist = live_price - stop
    min_stop_dist = max(live_price * 0.005, 0.05)  # at least 0.5% of price or $0.05
    # AUD232-024: decision-engine's hard_rejects.py splits this into two messages — a distinct
    # "stop above price — invalid setup" for stop_dist<=0 vs "too close to price" for a small
    # positive distance. This fallback used one combined branch that showed a confusing negative
    # "distance" figure for the <=0 case. Same decision outcome either way (stop_dist < min_stop_dist
    # covers both since min_stop_dist>0), just clearer diagnostics now.
    if stop_dist <= 0:
        return False, -99, [f"Stop ${stop:.2f} is above price ${live_price:.2f} — invalid setup"]
    if stop_dist < min_stop_dist:
        return False, -99, [
            f"Stop ${stop:.2f} too close to price ${live_price:.2f} "
            f"(distance ${stop_dist:.2f} < min ${min_stop_dist:.2f}) — invalid setup"
        ]
    rr = (take_profit - live_price) / stop_dist
    # AUD232-060: decision-engine's hard_rejects.py (the "primary" gate) requires a stricter
    # R:R in choppy/risk_off regimes (T190) — this fallback had no regime-aware check at all,
    # so it was measurably looser than DE in exactly the regimes where DE is strictest, right
    # when this fallback is most likely to be the only thing standing between a candidate and
    # a real paper entry (DE unreachable).
    regime_state = (live_regime.get("state", "neutral") if live_regime else "neutral")
    # SELFIMPROVE-NEVER-CALIBRATED-PARAMS: cfg.get(..., 2.0)'s literal fallback is now the
    # calibrated default (falls back further to the original 2.0/3.0 literals if calibration
    # has never run) — an explicit portfolio.config value still always wins.
    min_rr = cfg.get("min_rr_ratio", _default_min_rr_ratio("neutral"))
    if regime_state in ("choppy", "risk_off"):
        min_rr = max(min_rr, cfg.get("regime_min_rr_ratio", _default_min_rr_ratio(regime_state)))
    if rr < min_rr:
        return False, -99, [f"R:R {rr:.1f}:1 below minimum {min_rr:.1f}:1 at ${live_price:.2f}"]

    # Earnings too close — binary event risk
    dte = reasons.get("days_to_earnings")
    if dte is not None and int(dte) <= 5:
        return False, -99, [f"Earnings in {dte} days — binary event risk; skip"]

    # T171: Premarket gap filter — reject if stock has already gapped up significantly
    # from its signal price. Signal reasons["last_price"] is the close at signal-compute time.
    # If live price is already >max_entry_gap_pct above that close, we're chasing the move.
    _signal_close = reasons.get("last_price")
    if _signal_close and float(_signal_close) > 0:
        _gap = live_price / float(_signal_close) - 1
        _max_gap = cfg.get("max_entry_gap_pct", 0.04)
        if _gap > _max_gap:
            return False, -99, [
                f"Gap-up {_gap:.1%} above signal close ${_signal_close:.2f} "
                f"exceeds limit {_max_gap:.0%} — entry price degraded"
            ]

    # T220-D: Economic calendar blackout — reject BUY entries within 2h of major macro events.
    # FOMC, CPI, NFP, PCE cause unpredictable 1-3% moves; entries in this window have higher failure rates.
    # Checks reasons["macro_blackout"] first (fast path — set by signal-engine), then queries DB directly.
    _macro_evt = reasons.get("macro_blackout")
    if _macro_evt is None:
        try:
            from db import SessionLocal
            from sqlalchemy import text
            # BUG232-DEADCODE: this redundant local `from datetime import datetime, timezone,
            # timedelta` (datetime/timezone are already imported at module level, line ~34)
            # made Python treat `datetime` as a LOCAL name for the ENTIRE _should_enter()
            # function — meaning the later AUD232-005 time-of-day-gate/extended-move hard
            # reject's `datetime.now(timezone.utc)` call raised UnboundLocalError whenever
            # THIS if-block was skipped (i.e. whenever reasons.get("macro_blackout") is not
            # exactly None — the normal case, since signal-engine's T220-D fast path sets it
            # to an explicit True/False). That exception was silently swallowed by the time-
            # of-day gate's OWN try/except, making both AUD232-005 hard rejects dead code in
            # production despite looking correctly ported. Found while writing regression
            # tests for T232-DL-DUALSCORER-DEBT's already-ported DE-only hard rejects.
            _window_end = _now + timedelta(hours=2)
            with SessionLocal() as _evsess:
                _ev_row = _evsess.execute(text(
                    "SELECT title FROM economic_events "
                    "WHERE event_date >= :now AND event_date <= :end "
                    "AND importance IN ('high', 'critical') "
                    "LIMIT 1"
                ), {"now": _now.isoformat(), "end": _window_end.isoformat()}).fetchone()
                if _ev_row:
                    _macro_evt = _ev_row.title
        except Exception:
            pass  # DB query failure → allow entry (fail-open)
    if _macro_evt:
        return False, -99, [f"Macro blackout: {_macro_evt} within 2h — avoid binary-event risk"]

    # AUD232-005: ported from decision-engine's hard_rejects.py (T185 time-of-day gate +
    # breakout-extension guard). DE had these two checks but this fallback did not — meaning
    # a DE outage made the LIVE system MORE permissive during exactly the outage window extra
    # caution matters most. Same thresholds/messages as hard_rejects.py for parity.
    try:
        from zoneinfo import ZoneInfo as _ZI
        _market = cfg.get("market", "US")
        _tz = _ZI("America/New_York") if _market.upper() != "HK" else _ZI("Asia/Hong_Kong")
        _local = _now.astimezone(_tz)
        _mins = _local.hour * 60 + _local.minute
        if 570 <= _mins < 600:
            return False, -99, [
                f"Time-of-day gate: first 30 min of market open — "
                f"price discovery in progress ({_local.strftime('%H:%M')} local)"
            ]
        if 945 <= _mins < 960:
            return False, -99, [
                f"Time-of-day gate: last 15 min before close — "
                f"avoid closing auction risk ({_local.strftime('%H:%M')} local)"
            ]
    except Exception:
        pass  # tz lookup failure → allow entry (fail-open, matching hard_rejects.py)

    if breakout and float(breakout) > 0:
        _ext_pct = (live_price / float(breakout) - 1) * 100
        _ext_threshold = cfg.get("max_breakout_extension_pct", 6.0)
        if _ext_pct > _ext_threshold:
            return False, -99, [
                f"Stock {_ext_pct:.1f}% above breakout ${breakout:.2f} — "
                f"extended move, wait for pullback (threshold {_ext_threshold:.0f}%)"
            ]

    # ── Price zone (where is price relative to the game plan?) ───────────────
    # CB-2 FIX: old values (+4/+3) equalled the min_entry_score threshold (3–5), making it a
    # single-factor gate. Capped at +2 max so ≥2 additional factors must align for entry.

    if entry2 <= live_price <= breakout:
        score += 2
        notes.append(f"Price ${live_price:.2f} in optimal entry zone (${entry2:.2f}–${breakout:.2f})")
    elif live_price < entry2:
        score += 2
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

    # ── Volume confirmation (CB-5: independent of signal engine — not in fused_prob) ──
    # PA-B2: low-volume signals have higher false-positive rate for breakouts

    volume_z = reasons.get("volume_z")
    if volume_z is not None:
        vz = float(volume_z)
        if vz > 1.0:
            score += 1
            notes.append(f"Above-average volume (z={vz:.1f}) — conviction confirmation")
        elif vz < -0.5:
            score -= 1
            notes.append(f"Below-average volume (z={vz:.1f}) — breakout less reliable")

    # ── Earnings window ───────────────────────────────────────────────────────

    if dte is not None and int(dte) <= 10:
        score -= 1
        notes.append(f"Earnings in {dte} days — size conservatively")

    # ── Signal conviction summary (CB-5: single bonus replaces per-factor double-count) ──
    # RSI, MACD, OBV, trend, regime, breadth, sector are all captured in bull_prob already.
    # Score the fusion result ONCE rather than re-scoring each component individually.

    bull_prob = float(signal_data.get("bullish_probability") or 0.0)
    confidence = float(signal_data.get("confidence") or 0.0)
    if bull_prob >= 0.70:
        score += 1
        notes.append(f"Strong conviction {bull_prob*100:.0f}% fused probability")
    elif bull_prob < 0.58:
        score -= 1
        notes.append(f"Weak conviction {bull_prob*100:.0f}% fused probability")

    # ── SA-26: Confidence trajectory — accelerating signals outperform decelerating ─
    conf_delta = signal_data.get("confidence_delta")
    if conf_delta is not None:
        if conf_delta > 8:
            score += 1
            notes.append(f"Signal accelerating (+{conf_delta:.0f} confidence trend — momentum building)")
        elif conf_delta < -8:
            score -= 1
            notes.append(f"Signal decelerating ({conf_delta:.0f} confidence trend — fading momentum)")

    # ── SA-24: Signal freshness — fresh transitions stronger than persistent BUYs ─
    sig_ts = signal_data.get("ts")
    if sig_ts is not None:
        try:
            ts_aware = sig_ts.replace(tzinfo=timezone.utc) if sig_ts.tzinfo is None else sig_ts
            signal_age_h = (_now - ts_aware).total_seconds() / 3600
            if signal_age_h < 4:
                score += 1
                notes.append(f"Fresh signal ({signal_age_h:.1f}h old) — entry in prime window")
            elif signal_age_h > 18:
                score -= 1
                notes.append(f"Signal is {signal_age_h:.1f}h old — conditions may have shifted")
        except Exception:
            pass  # malformed ts: no freshness adjustment

    # ── AL-1: RL policy score adjustment ─────────────────────────────────────
    # Linear Q-function trained on closed paper trades. Adjusts the additive score
    # (±1) before the calibrated or raw threshold comparison.
    if _RL_AVAILABLE and _rl_recommend is not None:
        try:
            _rl_rec = _rl_recommend(
                rr_ratio=rr,
                confidence=confidence,
                entry_score=float(score),
                kscore=kscore if kscore is not None else 50.0,
                style=cfg.get("trading_style", "SWING"),
                regime=(live_regime.get("state", "neutral") if live_regime else "neutral"),
            )
            if _rl_rec["available"]:
                if _rl_rec["action"] == "BUY":
                    score += 1
                    notes.append(f"RL policy BUY (Q={_rl_rec['q_value']:.3f})")
                else:
                    score -= 1
                    notes.append(f"RL policy WAIT (Q={_rl_rec['q_value']:.3f})")
        except Exception:
            pass  # RL call failure is non-fatal

    # ── Cross-horizon consensus scoring ──────────────────────────────────────
    cross_buys_h = int(reasons.get("cross_style_buys", 0))
    regime_state_h = (live_regime.get("state", "neutral") if live_regime else "neutral")
    if cross_buys_h >= 2:
        score += 1
        notes.append(f"Cross-horizon: {cross_buys_h}+ styles BUY — strong multi-timeframe alignment")
    elif cross_buys_h == 0 and regime_state_h in ("bear", "choppy"):
        score -= 1
        notes.append("No cross-horizon support in bear/choppy regime — conviction penalty")

    # ── T172-B: Catalyst intelligence scoring ─────────────────────────────────
    # insider_score and congress_score are stored in signal.reasons by signal-engine.
    # Real-money conviction (insider buys/sells) gets ±1; congress net buying gets +1.
    _insider_sc  = reasons.get("insider_score")
    _congress_sc = reasons.get("congress_score")
    if _insider_sc is not None:
        _insider_sc = float(_insider_sc)
        if _insider_sc >= 60:
            score += 1
            notes.append(f"Strong insider buying (score {_insider_sc:.0f}) — real-money conviction")
        elif _insider_sc < -30:
            score -= 1
            notes.append(f"Significant insider selling (score {_insider_sc:.0f}) — management caution")
    if _congress_sc is not None:
        _congress_sc = float(_congress_sc)
        if _congress_sc > 50:
            score += 1
            notes.append(f"Congress net buying (score {_congress_sc:.0f}) — informed capital inflow")

    # ── AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK: calibrated win-rate feedback ──
    # calibrated_win_rate/calibrated_win_rate_count are real, measured historical win rates
    # per (horizon, direction, market, confidence-band) — computed by signal-engine's
    # _build_confidence_calibration() and, as of the same fix that added this score layer, now
    # durably persisted into Signal.reasons by _bulk_persist() (previously computed correctly
    # but only ever written into a response dict AFTER the real DB commit had already run,
    # making it invisible to this function no matter how "real" the number was).
    #
    # Gated behind cfg["calibration_feedback_enabled"] (default False — a pure no-op unless
    # explicitly turned on) because this is a NEW score adjustment, not a tuned value of an
    # EXISTING one — it must be validated via a walk-forward train/validation sweep
    # (gate_harness.py's established _passes_promotion_margin() discipline) BEFORE being
    # trusted to affect real entries, matching this repo's own standing rule that any new
    # sizing/scoring parameter needs that same promotion gate before going live. Real
    # production calibration data (2026-08-19) shows genuine, non-monotonic confidence
    # inversions — e.g. SWING|BUY|HK: 30.9% win rate at the 40-55 confidence band vs. 13.4% at
    # 55-70 — so the adjustment intentionally does NOT assume "higher confidence = higher
    # score"; it reads whichever band's OWN measured win rate applies to this exact signal.
    if cfg.get("calibration_feedback_enabled") and reasons.get("calibrated_win_rate") is not None:
        _cal_wr = float(reasons["calibrated_win_rate"])
        _cal_n = reasons.get("calibrated_win_rate_count")
        # _calibrated_win_rate() itself already enforces _CONF_CAL_MIN_COUNT (30) before ever
        # returning a non-None value, so no second sample-floor check is needed here — a
        # present value is already a trustworthy one by that upstream contract.
        if _cal_wr >= 0.55:
            score += 1
            notes.append(
                f"Calibrated win rate {_cal_wr*100:.0f}% (n={_cal_n}) for this exact "
                f"confidence band — measured edge above baseline"
            )
        elif _cal_wr <= 0.35:
            score -= 1
            notes.append(
                f"Calibrated win rate {_cal_wr*100:.0f}% (n={_cal_n}) for this exact "
                f"confidence band — measured underperformance"
            )

    # ── T232-DL-DUALSCORER: pre-regime early-warning score (F11) ─────────────
    # Ported from decision-engine's scorer.py (compute_score() Layer 3g) — this fallback had
    # no equivalent, so during a DE outage in a pre-choppy/pre-risk-off window a candidate got
    # zero score penalty here even though DE (when reachable) would already be subtracting 1.
    # _scan_for_entries already reads live_regime["is_pre_choppy"/"is_pre_risk_off"] one level
    # up (for min_entry_score/regime_size_mult adjustments) — this only adds the missing direct
    # score layer, it does not duplicate those separate threshold/sizing effects.
    if live_regime and live_regime.get("is_pre_risk_off"):
        score -= 1
        notes.append("Pre-risk-off: VIX rising into warning zone — conditions deteriorating")
    elif live_regime and live_regime.get("is_pre_choppy"):
        score -= 1
        notes.append("Pre-choppy: SPY hugging EMA50 — trend weakening, raise bar")

    # ── T232-DL-DUALSCORER-DEBT item #25: research alignment as a direct score layer ──
    # Ported from decision-engine's scorer.py (compute_score() Layer 4, _RESEARCH_SCORE).
    # Research previously affected ONLY position sizing here (research_size_mult, in the
    # caller's _open_paper_trade()) plus a separate hard AVOID/SELL gate — never a direct,
    # verdict-affecting score component the way it is in decision-engine. A candidate right at
    # the threshold could cross ENTER/SKIP differently between the two systems purely because
    # of how research was treated, even with identical underlying research data. Makes its own
    # short-timeout fetch (matching _open_paper_trade()'s own research_size_mult fetch exactly
    # — same endpoint, same 1.5s timeout, same fail-open-on-any-error contract) rather than
    # requiring the caller to pre-fetch and thread it through, since _should_enter() can run
    # earlier in the pipeline than that sizing block and mirrors decision-engine's own
    # self-sufficient design (DE also independently fetches research itself, never relying on
    # being passed it).
    _research_score_map = {"STRONG BUY": 2, "BUY": 1, "WATCH": 0, "AVOID": -1, "SELL": -2}
    try:
        import httpx as _httpx_research
        from common.config import get_settings as _gs_research
        _res_resp = _httpx_research.get(
            f"{_gs_research().research_engine_url}/research/{symbol}/summary",
            timeout=1.5,
            headers={"Authorization": f"Bearer {_svc_token()}"},
        )
        if _res_resp.status_code == 200:
            _res_json = _res_resp.json()
            _research_rec_raw = (_res_json.get("recommendation") or "").upper().replace("_", " ")
            if _research_rec_raw:
                _research_pts = _research_score_map.get(_research_rec_raw, 0)
                if _research_pts != 0:
                    score += _research_pts
                    notes.append(f"Research: {_research_rec_raw}")
    except Exception:
        pass  # research-engine unreachable/timeout → neutral, no score adjustment

    # ── T232-DL-DUALSCORER: market regime as a direct score layer ────────────
    # Ported from decision-engine's scorer.py (compute_score() Layer 5, _REGIME_SCORE). This
    # fallback previously only used regime_state to raise min_entry_score/min_rr (thresholds)
    # and dampen regime_size_mult (sizing, in the caller) — never as a direct additive/
    # subtractive score component. In choppy/risk_off, DE subtracts from the RAW score before
    # comparing to its floor; this fallback's raw score was untouched by regime at all, a real
    # boundary difference for candidates sitting exactly at the threshold.
    _regime_score_map = {"bull": 1, "neutral": 0, "choppy": -1, "risk_off": -2, "bear": -99}
    _regime_pts = _regime_score_map.get(regime_state_h, 0)
    if _regime_pts != 0:
        score += _regime_pts
        notes.append(f"Regime: {regime_state_h}")

    # ── T232-DL-DUALSCORER (AUD232-042 parity): K-Score as a direct ±1 layer ──
    # Ported from decision-engine's scorer.py (compute_score() Layer 6). This fallback already
    # receives `kscore` (threaded into the RL call and the calibrated-logistic branch below),
    # but — unlike DE — never scored it directly. A portfolio with <100 closed trades (still on
    # the plain additive path, no calibration yet) got zero score adjustment for a weak K-Score
    # in this fallback, while DE penalizes the identical candidate -1 for the same input.
    if kscore is not None:
        if kscore >= 55:
            score += 1
            notes.append(f"K-Score {kscore:.0f} — conviction positive")
        else:
            score -= 1
            notes.append(f"K-Score {kscore:.0f} below 55 — weak fundamental/momentum case")

    # ── T258-PORTFOLIO-CORRELATION-PREENTRY: advisory penalty for high correlation
    # with an already-open position ──────────────────────────────────────────
    # Ten individually-good trades that are all highly correlated (e.g. semis + high-beta
    # software) are effectively one position wearing many tickers. This mirrors the same
    # 0.8 "high correlation" warning threshold portfolio-optimizer's own /portfolio-risk/risk
    # endpoint already uses for its warnings list — advisory only (score -1), never a hard
    # reject, since a single correlation snapshot from a short lookback window is too noisy a
    # signal to block a trade outright on its own (matching this repo's established
    # discipline of promoting a soft penalty to a hard gate only after outcome data justifies
    # it — see the DE-parity hard-reject ports elsewhere in this file for the sibling case
    # where that promotion WAS already justified).
    _HIGH_CORR_THRESHOLD = 0.8
    if max_open_corr is not None and max_open_corr > _HIGH_CORR_THRESHOLD:
        score -= 1
        notes.append(
            f"High correlation ({max_open_corr:.2f}) with an open position — "
            f"reduces effective diversification"
        )

    # ── Decision ─────────────────────────────────────────────────────────────

    # PT-3: Use calibrated logistic weights when available (>=100 closed trades).
    # Falls back to raw additive threshold when no calibration data exists.
    weights = _load_entry_weights()
    if weights.get("intercept") is not None and weights.get("n_trades", 0) >= 100:
        import math as _math
        w = weights
        ks = kscore if kscore is not None else 50.0
        logit = (
            w["intercept"]
            + w["w_rr"]        * min(rr, 8.0)
            + w["w_confidence"] * confidence
            + w["w_score"]     * float(score)
            + w["w_kscore"]    * ks
        )
        cal_prob = 1.0 / (1.0 + _math.exp(-logit))
        should = cal_prob >= w.get("threshold", 0.52)
        notes.append(f"Calibrated win-prob {cal_prob*100:.0f}% (threshold {w.get('threshold',0.52)*100:.0f}%)")
    else:
        _min_entry_score = cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"])
        # T232-DL-DUALSCORER-DEBT item #30: ported from decision-engine's scorer.py
        # min_score_for_regime() — a human trader who has lost 7 of the last 10 trades gets
        # more selective, not less. Raises the bar rather than penalizing the score itself,
        # matching DE's own mechanism exactly (a floor bump, not a score subtraction).
        if recent_win_rate is not None and recent_win_rate < 0.30:
            _min_entry_score += 1
            notes.append(f"Recent win rate {recent_win_rate*100:.0f}% below 30% — floor raised to {_min_entry_score}")
        should = score >= _min_entry_score

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
        # GROWTH stocks are high-volatility; wider ATR multiplier avoids premature stops.
        # AUD-DUPLOGIC: now reads from _STYLE_PARAMS (the single source of truth) instead of
        # its own inline literal — see this dict's own comment for why.
        atr_mult = params.get("atr_stop_mult", 2.0)
        stop = round((current_price - atr * atr_mult) / step) * step
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

def _monitor_positions(session, portfolio: PaperPortfolio, live_prices: dict[str, float], live_regime: dict | None = None) -> list[dict]:
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
        return []

    closed_exits: list[dict] = []  # accumulated for caller to send exit emails

    # Batch-fetch latest signal per open symbol in ONE query (avoids N+1)
    symbols = [t.symbol for t in open_trades]

    # BUG-PAPERPOS-DELISTED-FROZEN: Stock.delisted (aud14-survivorship) is populated with a
    # real, conservative signal (2 consecutive ingestion failures) but was never consumed
    # here — a delisted stock's live quote goes stale, and the pre-existing staleness-
    # escalation logic below only LOGS the condition, never exits the position. Left alone,
    # the position freezes open at an increasingly stale price forever, distorting reported
    # equity/P&L indefinitely. This bulk-fetches the flag once per cycle for all open
    # symbols, matching the batch-fetch pattern every other per-symbol lookup in this
    # function already uses (signals/kscores/OBV above).
    delisted_symbols: set[str] = set()
    if symbols:
        try:
            delisted_symbols = {
                sym for sym, is_delisted in session.execute(
                    select(Stock.symbol, Stock.delisted).where(Stock.symbol.in_(symbols))
                ).all()
                if is_delisted
            }
        except Exception as exc:
            log.warning("paper.monitor_delisted_check_failed", error=str(exc))

    latest_signals: dict[str, Signal] = {}
    if symbols:
        # DISTINCT ON (stock_id): get the most-recent signal row per stock
        latest_ts_subq = (
            select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
            .join(Stock, Signal.stock_id == Stock.id)
            .where(Stock.symbol.in_(symbols), Signal.horizon == style)
            .group_by(Signal.stock_id)
            .subquery()
        )
        batch_sigs = session.execute(
            select(Signal, Stock)
            .join(Stock, Signal.stock_id == Stock.id)
            .join(latest_ts_subq,
                  (Signal.stock_id == latest_ts_subq.c.stock_id) &
                  (Signal.ts == latest_ts_subq.c.max_ts))
            .where(Signal.horizon == style)
        ).all()
        for sig, stk in batch_sigs:
            latest_signals[stk.symbol] = sig

    # PT-H3: Batch-fetch latest K-score per open symbol — ONE query for all positions
    latest_kscores: dict[str, float] = {}
    if symbols:
        kscore_date_subq = (
            select(Ranking.stock_id, func.max(Ranking.as_of).label("max_date"))
            .join(Stock, Ranking.stock_id == Stock.id)
            .where(Stock.symbol.in_(symbols))
            .group_by(Ranking.stock_id)
            .subquery()
        )
        for score, sym in session.execute(
            select(Ranking.score, Stock.symbol)
            .join(Stock, Ranking.stock_id == Stock.id)
            .join(kscore_date_subq,
                  (Ranking.stock_id == kscore_date_subq.c.stock_id) &
                  (Ranking.as_of == kscore_date_subq.c.max_date))
        ).all():
            if score is not None:
                latest_kscores[sym] = float(score)

    # PT-H4: Batch-fetch last 15 daily bars per open symbol for OBV divergence detection.
    # OBV divergence (price holding up but OBV declining) indicates quiet distribution.
    _obv_divergence: dict[str, bool] = {}
    if symbols:
        cutoff_15d = datetime.now(timezone.utc) - timedelta(days=22)  # ~15 trading days
        price_rows = session.execute(
            select(Stock.symbol, Price.close, Price.volume)
            .join(Stock, Price.stock_id == Stock.id)
            .where(
                Stock.symbol.in_(symbols),
                Price.timeframe == TimeFrame.D1,
                Price.ts >= cutoff_15d,
            )
            .order_by(Stock.symbol, Price.ts)
        ).all()
        _sym_bars: dict[str, list[tuple[float, float]]] = {}
        for sym, close, volume in price_rows:
            _sym_bars.setdefault(sym, []).append((float(close), float(volume or 0)))
        for sym, bars in _sym_bars.items():
            if len(bars) < 10:
                continue
            bars = bars[-10:]
            closes  = [b[0] for b in bars]
            volumes = [b[1] for b in bars]
            # Cumulative OBV
            obv = 0.0
            obv_series = [0.0]
            for i in range(1, len(closes)):
                obv += volumes[i] if closes[i] > closes[i - 1] else (-volumes[i] if closes[i] < closes[i - 1] else 0)
                obv_series.append(obv)
            n = len(obv_series)
            first_half_mean = sum(obv_series[:n // 2]) / (n // 2)
            second_half_mean = sum(obv_series[n // 2:]) / (n - n // 2)
            price_held = closes[-1] >= closes[0] * 0.98  # price flat or up over window
            obv_declining = second_half_mean < first_half_mean  # OBV trending down
            if price_held and obv_declining:
                _obv_divergence[sym] = True

    # PT-M1: Batch-compute sector relative-strength lag for all open positions.
    # One yfinance download covers all stocks + their sector ETFs.
    _rs_sector_lag: dict[str, bool] = _batch_sector_rs_lag(
        [(t.symbol, t.sector) for t in open_trades]
    )

    # PT-I1: Fundamental deterioration — if research recommendation is now AVOID or SELL
    # for a held position, tighten the trail to exit faster. Fire-and-forget http calls
    # (short timeout), swallowed on any error so monitor is never blocked.
    _research_deteriorated: dict[str, bool] = {}
    try:
        from common.config import get_settings as _gs_mon
        _res_url = _gs_mon().research_engine_url
        import httpx as _hx_mon
        symbols_to_check = list({t.symbol for t in open_trades if t.stage == "open"})
        for _sym in symbols_to_check:
            try:
                _rr = _hx_mon.get(
                    f"{_res_url}/research/{_sym}/summary", timeout=1.0,
                    headers={"Authorization": f"Bearer {_svc_token()}"},
                )
                if _rr.status_code == 200:
                    _rd = _rr.json()
                    if (_rd.get("recommendation") or "").upper() in ("AVOID", "SELL"):
                        _research_deteriorated[_sym] = True
            except Exception:
                pass
    except Exception:
        pass

    # Regime-adjusted trail multiplier — tighten stops in risk-off environments
    # so existing positions are protected even though new entries are paused/sized down
    regime_trail_adj = 1.0
    if live_regime:
        _rs = live_regime.get("state", "neutral")
        if _rs == "bear":
            regime_trail_adj = 0.70   # 30% tighter: highest_price - ATR×1.4 instead of ×2.0
        elif _rs == "risk_off":
            regime_trail_adj = 0.85   # 15% tighter

    # PT-P2 + PA-F1: batch-fetch ATR for all armed symbols in ONE yfinance download
    trail_trigger = cfg.get("trail_trigger_pct", 0.05)
    armed_symbols = [
        t.symbol for t in open_trades
        if (t.highest_price or t.entry_price) >= t.entry_price * (1 + trail_trigger)
    ]
    monitor_atr_cache: dict[str, float | None] = _batch_compute_atr(list(set(armed_symbols)))

    # PT-H5: Batch-fetch latest RSI-14 for all armed positions to detect overbought peaks.
    # Uses the pre-computed indicators table (cheaper than recomputing from prices).
    _rsi_overbought: dict[str, bool] = {}
    if armed_symbols:
        try:
            rsi_rows = session.execute(
                select(Stock.symbol, Indicator.value)
                .join(Indicator, Stock.id == Indicator.stock_id)
                .where(
                    Stock.symbol.in_(armed_symbols),
                    Indicator.name == "rsi_14",
                    Indicator.timeframe == TimeFrame.D1,
                )
                .order_by(Stock.symbol, Indicator.ts.desc())
                .distinct(Stock.symbol)
            ).all()
            for sym, rsi_val in rsi_rows:
                if rsi_val is not None and float(rsi_val) > 75:
                    _rsi_overbought[sym] = True
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo as _ZI
        _et_date = now.astimezone(_ZI("America/New_York")).date()
    except Exception:
        _et_date = now.date()

    for trade in open_trades:
        # PT-B3: hold days in trading days (excludes weekends/holidays)
        # +1 so today counts as day 1 (busday_count is exclusive of end date)
        days_held = int(np.busday_count(trade.entry_date, _et_date + timedelta(days=1)))
        trade.hold_days = days_held

        _price_is_stale_escalated = False
        live_price = live_prices.get(trade.symbol)
        if not live_price:
            # T234-PT-MONITOR-MISSING-PRICE-FALLBACK: a missing live quote used to skip
            # ALL stop/target/trailing-stop checks for this trade for the entire cycle,
            # silently — a position whose stop should have triggered during a quote gap
            # would ride a bad move further than intended. Fall back the same way
            # _best_price() does elsewhere in this file (live -> cached current_price ->
            # entry_price) so exit checks still run against the best price we have.
            live_price = trade.current_price or trade.entry_price

            # BUG-MONITORPOS-STALEPRICE: the fallback above used to fire silently forever —
            # trade.current_price was unconditionally overwritten with the SAME stale value
            # every cycle (this loop runs every 5-10 min, per this module's own docstring),
            # with no tracking of how many consecutive cycles a real quote hasn't arrived.
            # A genuinely bad multi-cycle data outage (feed issue, halt, delisting) could
            # leave a position's stop/target checks running against an increasingly frozen
            # price for an unbounded time with zero visibility or escalation — a single
            # log.warning() per cycle looks identical whether this is cycle 1 or cycle 50.
            # Track consecutive stale cycles in Redis (no schema change needed — this is
            # transient monitoring state, not something that needs to survive a restart)
            # and escalate to log.error() once it crosses a real, actionable threshold, so
            # a genuinely stuck feed is distinguishable in logs/alerts from one normal
            # missed tick.
            _stale_count = 0
            try:
                from common.redis_client import get_redis as _get_pool_redis
                _stale_redis = _get_pool_redis()
                _stale_key = f"stockai:monitor_stale_price:{trade.id}"
                _stale_count = int(_stale_redis.incr(_stale_key))
                _stale_redis.expire(_stale_key, 3600)  # 1h — well past any real multi-cycle gap
            except Exception:
                _stale_count = 0  # fail-open — staleness tracking is diagnostic, never blocks exit checks

            _STALE_ESCALATION_THRESHOLD = 5  # ~25-50 min of missing quotes at 5-10 min cadence
            if _stale_count >= _STALE_ESCALATION_THRESHOLD:
                # AUD262-STALE-PRICE-COUNTER-GATES-NOTHING: this escalation previously only
                # logged — every stop/target/trailing-stop comparison below still ran
                # unconditionally against the same frozen fallback price, so a genuinely stuck
                # feed could hold a position through an entire decline (stop never fires against
                # a frozen too-high price) or fire a phantom exit at a price that never actually
                # traded. Now actually gates: past the threshold, the whole exit-evaluation chain
                # is skipped for this trade this cycle (see _price_is_stale_escalated below) —
                # hold the position rather than act on a price known to be stale, matching this
                # tracker item's own explicit fix description. Does NOT skip the
                # BUG-PAPERPOS-DELISTED-FROZEN check just below (confirmed delisted is a
                # fundamentally different case — no real market exists at all, vs. a market
                # exists but the feed is temporarily blind to it).
                _price_is_stale_escalated = True
                log.error("paper.monitor_price_stale_escalation", symbol=trade.symbol,
                          trade_id=trade.id, fallback_price=live_price, stale_cycles=_stale_count,
                          note="live quote missing for many consecutive cycles — exit evaluation "
                               "skipped this cycle rather than acting on a stale price")
            else:
                log.warning("paper.monitor_price_fallback", symbol=trade.symbol,
                            trade_id=trade.id, fallback_price=live_price, stale_cycles=_stale_count,
                            note="live quote missing this cycle — using cached/entry price for exit checks")
        else:
            # A real quote arrived this cycle — clear any accumulated staleness streak so a
            # single missed tick followed by a healthy cycle doesn't carry a false streak
            # into a LATER, unrelated gap.
            try:
                from common.redis_client import get_redis as _get_pool_redis
                _get_pool_redis().delete(f"stockai:monitor_stale_price:{trade.id}")
            except Exception:
                pass

        trade.current_price = live_price
        if trade.highest_price is None or live_price > trade.highest_price:
            trade.highest_price = live_price

        entry  = trade.entry_price
        stop   = trade.current_stop
        target = trade.take_profit
        pnl_pct = (live_price - entry) / entry

        current_sig = latest_signals.get(trade.symbol)
        sig_type = current_sig.signal.value if current_sig and current_sig.signal else "UNKNOWN"

        exit_reason = None
        exit_notes: dict = {}

        # ── Hard exits ────────────────────────────────────────────────────────

        # PA-G1: all exit_notes use a consistent schema with shared base keys:
        #   message, pnl_pct, price_at_exit, highest_price_reached, hold_days, signal_at_exit
        # Type-specific extras are appended on top.
        _base_notes: dict = {
            "price_at_exit": round(live_price, 4),
            "highest_price_reached": round(trade.highest_price or live_price, 4),
            "hold_days": days_held,
            "signal_at_exit": sig_type,
        }

        # BUG-PAPERPOS-DELISTED-FROZEN: checked FIRST, ahead of stop/target/signal logic —
        # once a stock is confirmed delisted, `live_price` here is whatever stale fallback
        # value _monitor_positions's own missing-quote handling last supplied (there is no
        # real market left to compute a meaningful stop/target breach against), so this must
        # preempt every other exit condition rather than compete with them.
        if trade.symbol in delisted_symbols:
            exit_reason = "delisted"
            exit_notes = {**_base_notes,
                "message": f"{trade.symbol} confirmed delisted — closing position at last known price ${live_price:.2f}",
                "pnl_pct": round(pnl_pct * 100, 2),
            }
            log.warning("paper.delisted_auto_exit", symbol=trade.symbol, trade_id=trade.id,
                        exit_price=live_price, pnl_pct=round(pnl_pct * 100, 2))

        # AUD262-STALE-PRICE-COUNTER-GATES-NOTHING: once the missing-quote streak has
        # escalated (see _price_is_stale_escalated above), skip the ENTIRE stop/target/
        # signal/time-based exit-evaluation chain below rather than cherry-picking which
        # individual branches to gate — every remaining branch below is an `elif`, so this
        # one taking priority correctly short-circuits all of them for this cycle. Matches
        # this tracker item's own explicit fix: "hold, do not exit on a price known to be
        # stale." exit_reason stays None, so "Execute exit" below is a no-op this cycle —
        # the position is simply held until either a real quote returns (clearing the
        # streak) or the position is later confirmed delisted (handled separately above).
        elif _price_is_stale_escalated:
            log.warning("paper.exit_check_skipped_stale_price", symbol=trade.symbol,
                        trade_id=trade.id, fallback_price=live_price,
                        note="holding position — stale-price escalation active, not evaluating exits this cycle")

        elif live_price <= stop:
            # T197: Distinguish break-even stops (stop ≈ entry) from real losses.
            # A break-even exit means the trade ran positive, came back, and exited flat.
            _be_tol = entry * 0.005  # 0.5% tolerance around entry
            # AUD262-BREAKEVEN-COOLDOWN-60X-TOO-SHORT: the ORIGINAL check here only compared
            # the STOP LEVEL to entry (abs(stop - entry) <= _be_tol) — completely independent
            # of the actual FILL (live_price). A gap-down or next-cycle price can fill well
            # BELOW a breakeven-tolerance stop, realizing a real loss while still being
            # labeled "breakeven_stop" — exactly the same label-vs-fill conflation
            # AUD262-EXITREASON-CONFLATION-ROOT already fixed for stop_hit/trailing_stop
            # above. Confirmed in production: 22 of 26 breakeven_stop trades LOST money,
            # worst -5.18%. Now requires the FILL itself to also be within tolerance of
            # entry — a fill that gapped meaningfully below the stop is a real loss-cut and
            # falls through to the stop_hit branch below instead.
            if abs(stop - entry) <= _be_tol and abs(live_price - entry) <= _be_tol:
                exit_reason = "breakeven_stop"
                exit_notes = {**_base_notes,
                    "message": f"Break-even stop hit: stop ${stop:.2f} ≈ entry ${entry:.2f}, live ${live_price:.2f}",
                    "pnl_pct": round(pnl_pct * 100, 2),
                }
            elif stop > entry and live_price >= entry:
                # AUD262-EXITREASON-CONFLATION-ROOT: a stop that has RATCHETED UP above entry
                # (trailing_stop.py's monotonic-raise-only mechanism) and then triggers is a
                # PROFITABLE exit — it must never share the "stop_hit" label with a genuine
                # protective loss-cut (stop <= entry). Two economically opposite events were
                # being labeled identically, which corrupted 6 downstream consumers: the heat
                # brake counted profitable trailing exits as "adverse conditions" and halted all
                # new entries; the 5-day re-entry cooldown banned re-entry into a symbol BECAUSE
                # it made money; and postmortem/Journal exit analytics blended winning and
                # losing exits into one meaningless average. Confirmed in production: 14 of 49
                # stop_hit trades exited PROFITABLY, up to +13.96%.
                #
                # AUD262-BREAKEVEN-COOLDOWN-60X-TOO-SHORT: added `and live_price >= entry` —
                # without it, a stop sitting only marginally above entry (within the breakeven
                # tolerance just above, so it fails the breakeven branch's OWN live_price check)
                # combined with a hard gap-down fill well BELOW entry would still satisfy `stop >
                # entry` alone and be mislabeled a profitable "trailing_stop" exit, when the real
                # fill was a genuine loss. Requiring the fill to have actually realized at or
                # above entry confirms this really is the ratcheted-stop-profitable-exit case,
                # not a gap-through of a stop that merely started out just above entry.
                exit_reason = "trailing_stop"
                exit_notes = {**_base_notes,
                    "message": f"Trailing stop ${stop:.2f} (above entry ${entry:.2f}) hit at live ${live_price:.2f} — a profitable exit, not a loss-cut",
                    "pnl_pct": round(pnl_pct * 100, 2),
                }
            else:
                exit_reason = "stop_hit"
                exit_notes = {**_base_notes,
                    "message": f"Stop ${stop:.2f} breached at live price ${live_price:.2f}",
                    "pnl_pct": round(pnl_pct * 100, 2),
                }

        elif target and live_price >= target:
            exit_reason = "target_reached"
            exit_notes = {**_base_notes,
                "message": f"Target ${target:.2f} reached at ${live_price:.2f}",
                "pnl_pct": round(pnl_pct * 100, 2),
            }

        elif sig_type == "SELL":
            exit_reason = "signal_exit"
            exit_notes = {**_base_notes,
                "message": f"Signal downgraded to SELL at ${live_price:.2f}",
                "pnl_pct": round(pnl_pct * 100, 2),
                "signal_reasons": (current_sig.reasons or {}) if current_sig else {},
            }

        # ── Time stop ────────────────────────────────────────────────────────

        elif trade.hold_days >= cfg.get("max_hold_days", 60):
            exit_reason = "time_stop"
            exit_notes = {**_base_notes,
                "message": f"Time stop: {trade.hold_days} days without resolution",
                "pnl_pct": round(pnl_pct * 100, 2),
            }

        # ── WF-4: HOLD stall exit — zombie positions stuck < threshold gain for too long ──

        elif sig_type == "HOLD":
            stall_days = cfg.get("hold_stall_days", 30)
            stall_max_gain = cfg.get("hold_stall_max_gain", 0.05)
            if days_held >= stall_days and pnl_pct < stall_max_gain:
                exit_reason = "hold_stall_timeout"
                exit_notes = {**_base_notes,
                    "message": f"HOLD stall: {days_held}d with only {pnl_pct*100:.1f}% gain (threshold +{stall_max_gain*100:.0f}%) — freeing capital",
                    "pnl_pct": round(pnl_pct * 100, 2),
                }
                log.info("paper.hold_stall_exit", symbol=trade.symbol,
                         hold_days=days_held, pnl_pct=round(pnl_pct * 100, 1),
                         stall_days=stall_days, stall_max_gain_pct=stall_max_gain * 100)

        # ── T207: Momentum exhaustion exit ───────────────────────────────────────
        # OBV distribution (price holding but volume declining) + RSI rolled from overbought
        # signals the rally is exhausting. Exit profitable positions early rather than ride back.

        elif (
            exit_reason is None
            and pnl_pct > 0
            and cfg.get("momentum_exit_enabled", True)
            and days_held >= cfg.get("momentum_exit_min_days", 3)
            and _obv_divergence.get(trade.symbol)
            and _rsi_overbought.get(trade.symbol)
        ):
            exit_reason = "momentum_fade"
            exit_notes = {**_base_notes,
                "message": (
                    f"Momentum exhaustion: OBV distribution + RSI rolled from overbought "
                    f"at ${live_price:.2f} (+{pnl_pct*100:.1f}%)"
                ),
                "pnl_pct": round(pnl_pct * 100, 2),
                "obv_divergence": True,
                "rsi_overbought_rolled": True,
            }
            log.info("paper.momentum_fade_exit",
                     symbol=trade.symbol, pnl_pct=round(pnl_pct * 100, 1),
                     hold_days=days_held,
                     note="OBV declining + RSI rolled from overbought — protecting gains")

        # ── WAIT decay exit ───────────────────────────────────────────────────

        elif sig_type == "WAIT":
            # Check if last non-WAIT signal is older than wait_exit_days — true consecutive decay
            last_non_wait_ts = session.execute(
                select(func.max(Signal.ts))
                .join(Stock, Signal.stock_id == Stock.id)
                .where(
                    Stock.symbol == trade.symbol,
                    Signal.horizon == style,
                    Signal.signal != "WAIT",
                    Signal.ts >= trade.entry_time,
                )
            ).scalar()

            wait_days = cfg.get("wait_exit_days", 5)
            # BUG-MONITORPOS-NAIVEAWARE: last_non_wait_ts comes back naive (Signal.ts is a
            # plain DateTime column, no timezone=True) while `now` is tz-aware
            # (datetime.now(timezone.utc), needed elsewhere in this function for exit_time
            # writes) — comparing them directly raises TypeError and aborts the WHOLE
            # paper_trading_step() call (a single outer try/except wraps every portfolio in
            # the loop), silently killing entries for every portfolio, not just this trade's.
            # Strip tzinfo from `now` for this one naive-vs-naive comparison instead of
            # changing `now`'s type globally.
            still_waiting = (
                last_non_wait_ts is None or
                last_non_wait_ts < now.replace(tzinfo=None) - timedelta(days=wait_days)
            )

            if still_waiting:
                exit_reason = "momentum_exit"
                exit_notes = {**_base_notes,
                    "message": f"No non-WAIT signal in {wait_days} days — momentum lost",
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
            # PT-B6: apply exit slippage (sells at a slightly lower price than quoted)
            # AUD262-MIN-STOP-FILL-NOOP: stop_hit/breakeven_stop/trailing_stop are ONLY ever
            # reached from the `elif live_price <= stop:` branch above (see the exit-reason
            # decision block preceding this one) — so at this point live_price <= stop always
            # holds, and min(stop, live_price) always equals live_price. The comments this
            # replaced (RISK-2/QW-7) described a stop-limit-vs-market-fill distinction — filling
            # at the stop LEVEL on a clean breach vs. at the (worse) live/market price on a
            # gap-through — that the code never actually implemented; every stop-breach exit
            # fills at live_price regardless of label. Fixed by using live_price directly for
            # every exit_reason, matching what the code already did, rather than leaving a
            # conditional that describes logic that doesn't run.
            _base_slippage = cfg.get("entry_slippage_pct", 0.001)
            slippage = (
                _size_aware_slippage_pct(trade.shares, _avg_daily_volume_for(trade.symbol), _base_slippage)
                if cfg.get("size_aware_slippage_enabled", True) else _base_slippage
            )
            exit_price = round(live_price * (1 - slippage), 4)
            exit_commission = round(cfg.get("commission_per_share", 0.0) * trade.shares, 4)
            exit_value = round(exit_price * trade.shares, 2)
            pnl_dollar = round((exit_price - entry) * trade.shares, 2)
            pnl_pct    = (exit_price - entry) / entry  # recalc with slipped exit; unweighted, kept in exit_notes for reference
            exit_notes["pnl_pct"] = round(pnl_pct * 100, 2)  # overwrite pre-slippage value
            # T232-PT6: fold in realized P&L from any scale-out partials so a trade that
            # took profit on the way up and trailed the remainder to breakeven is scored as
            # the winner it actually was, not a ~$0/negative loser. pct_return is recomputed
            # against the ORIGINAL cost basis (entry_shares), not just the shares remaining
            # at close, since the partials already returned part of that capital.
            # AUD262-ENTRY-EXIT-COMMISSION-EXCLUDED-FROM-PNL: entry_commission was a one-time
            # cost charged on the full original position at entry (not per-tranche, unlike
            # exit/partial commission above) — subtracted exactly once here, at final close,
            # regardless of how many scale-outs happened in between. Scale-out partials already
            # correctly fold their OWN commission into realized_pnl at the time they happen.
            total_pnl_dollar = round(
                (trade.realized_pnl or 0.0) + pnl_dollar - exit_commission - (trade.entry_commission or 0.0), 2
            )
            _cost_basis = entry * (trade.entry_shares or trade.shares)
            total_pnl_pct = (total_pnl_dollar / _cost_basis) if _cost_basis else pnl_pct
            trade.stage               = "closed"
            trade.exit_time           = now
            trade.exit_price          = exit_price
            trade.exit_reason         = exit_reason
            trade.exit_reasons        = exit_notes
            trade.pnl                 = total_pnl_dollar
            trade.pct_return          = round(total_pnl_pct * 100, 4)
            _write_decision_log(
                session, trade, "exit", exit_price, trade.shares, exit_reason,
                {"pnl_dollar": total_pnl_dollar, "pnl_pct": trade.pct_return,
                 "hold_days": days_held, "exit_reasons": exit_notes},
            )
            # PA-G3: record signal state at exit for walk-forward attribution
            trade.signal_at_exit_id   = current_sig.id   if current_sig else None
            trade.signal_at_exit_type = current_sig.signal.value if current_sig and current_sig.signal else None
            portfolio.current_cash = max(0.0, round(portfolio.current_cash + exit_value - exit_commission, 2))
            # Broker exit routing: submit real SELL for broker-entered positions
            if portfolio.broker_connection_id and trade.broker_order_id:
                _place_broker_exit(session, trade, portfolio)
            # PT-J1: write actual trade result back to signal_outcomes for signal accuracy calibration
            if trade.signal_id is not None:
                try:
                    from db.models import SignalOutcome
                    _so = session.execute(
                        select(SignalOutcome).where(SignalOutcome.signal_id == trade.signal_id)
                    ).scalar_one_or_none()
                    if _so is not None:
                        _so.entry_price = entry
                        _so.entry_date  = trade.entry_date
                        _so.exit_price  = exit_price
                        _so.exit_date   = now.date()
                        # AUD19-DB3: cutoffs are calendar-day approximations of trading-day horizons.
                        # 7 calendar days ≈ 5 trading days (SHORT), 14 ≈ 10 (SWING), 15+ ≈ 11–20+ (LONG).
                        _bucket = "5d" if days_held <= 7 else ("10d" if days_held <= 14 else "20d")
                        # AUD262-SIGNALOUTCOME-LASTTRANCHE-WRITEBACK: use the SAME blended
                        # total_pnl_pct/total_pnl_dollar trade.pct_return/trade.pnl already use
                        # (T232-PT6, above) — not the unblended final-tranche pnl_pct/pnl_dollar.
                        # A trade that scaled out profitably on the way up and trailed the
                        # remainder to a small loss is a real winner; writing the unblended
                        # last-tranche values here recorded it as a loser in the ML ground truth
                        # (signal accuracy, confidence calibration, entry-gate tuning) even
                        # though trade.pnl correctly showed a gain.
                        setattr(_so, f"return_{_bucket}", round(total_pnl_pct, 4))
                        setattr(_so, f"is_correct_{_bucket}", total_pnl_dollar > 0)
                        session.flush()
                except Exception as _soe:
                    # AUD232-016: bumped from warning to error — a failed writeback here
                    # silently leaves that SignalOutcome row's entry_price/exit_price/
                    # return_Nd/is_correct_Nd unset for this (stock, horizon, signal_date)
                    # with no retry and no reconciliation path back to
                    # evaluate_signal_outcomes, so it needs to actually surface in
                    # monitoring rather than blend into routine warning-level noise.
                    log.error("paper.signal_outcome_writeback_failed", signal_id=trade.signal_id, error=str(_soe), exc_info=True)
            # PA-G4: rich attribution log — enables "why did this trade exit?" queries in logs
            log.info("paper.exit",
                     symbol=trade.symbol, reason=exit_reason,
                     pnl=pnl_dollar, pct=round(pnl_pct * 100, 2),
                     entry_price=round(entry, 4),
                     exit_price=exit_price,
                     highest=round(trade.highest_price or entry, 4),
                     hold_days=days_held,
                     entry_score=trade.entry_score,
                     confidence=trade.confidence_at_entry,
                     kscore=trade.kscore_at_entry,
                     regime_at_entry=trade.market_regime_at_entry,
                     signal_at_exit=sig_type)
            # Collect for exit email (sent by caller after commit)
            closed_exits.append({
                "symbol": trade.symbol,
                "exit_reason": exit_reason,
                "entry_price": round(entry, 4),
                "exit_price": exit_price,
                "pnl_dollar": pnl_dollar,
                "pnl_pct": round(pnl_pct * 100, 2),
                "hold_days": days_held,
                "shares": trade.shares,
                "style": style,
                "signal_at_exit": sig_type,
                "highest_price": trade.highest_price,
                "entry_notes": trade.entry_decision_notes or [],
                # PT-MONITOR-NO-MARKET-HOURS-GATE: _monitor_positions() intentionally has no
                # market-hours gate (a genuinely breached stop should still close promptly
                # outside regular hours — leaving it open and unprotected until the next open
                # would be worse) — but the resulting email previously read identically to a
                # live intraday trigger even when it reflects a stale end-of-day close computed
                # hours into the overnight. Records whether the market was actually open AT THE
                # MOMENT of this exit so the email can label it honestly rather than the caller
                # re-deriving this after the fact against a DIFFERENT (later, wall-clock) moment.
                "market_hours_open": _is_market_hours(cfg.get("market", "US")),
            })
            continue

        # ── Two-level scale-out ────────────────────────────────────────────────
        # Level 1: +7% → sell 33%, floor stop at breakeven
        # Level 2: +12% → sell 50% of remaining (≈33% of original), move stop to +5%
        # Backward compat: treats legacy "PARTIAL_TAKEN" marker as level-1 done.

        partial_tp_pct  = cfg.get("partial_tp_pct",  0.07)   # level-1 trigger
        partial_tp2_pct = cfg.get("partial_tp2_pct", 0.12)   # level-2 trigger
        _P1 = "PARTIAL1_TAKEN"
        _P2 = "PARTIAL2_TAKEN"
        notes_list = list(trade.entry_decision_notes or [])
        p1_done = _P1 in notes_list or "PARTIAL_TAKEN" in notes_list
        p2_done = _P2 in notes_list
        _base_slippage = cfg.get("entry_slippage_pct", 0.001)

        if not p1_done and partial_tp_pct and pnl_pct >= partial_tp_pct and trade.shares > 0.01:
            partial_shares = round(trade.shares * 0.33, 4)
            slippage = (
                _size_aware_slippage_pct(partial_shares, _avg_daily_volume_for(trade.symbol), _base_slippage)
                if cfg.get("size_aware_slippage_enabled", True) else _base_slippage
            )
            partial_price  = round(live_price * (1 - slippage), 4)
            partial_value  = round(partial_shares * partial_price, 2)
            partial_pnl    = round((partial_price - entry) * partial_shares, 2)
            partial_commission = round(cfg.get("commission_per_share", 0.0) * partial_shares, 4)
            trade.shares = round(trade.shares - partial_shares, 4)
            trade.realized_pnl = round((trade.realized_pnl or 0.0) + partial_pnl - partial_commission, 2)
            portfolio.current_cash = round(portfolio.current_cash + partial_value - partial_commission, 2)
            if trade.current_stop < entry:
                trade.current_stop = entry
            notes_list.append(_P1)
            notes_list.append(
                f"Scale-out-1: sold {partial_shares:.4f}sh @ ${partial_price:.2f} "
                f"(+{pnl_pct*100:.1f}%), remaining {trade.shares:.4f}sh, stop → breakeven"
            )
            trade.entry_decision_notes = notes_list
            log.info("paper.partial1_profit_taken",
                     symbol=trade.symbol, partial_shares=partial_shares,
                     partial_price=partial_price, partial_value=partial_value,
                     partial_pnl=partial_pnl, pnl_pct=round(pnl_pct * 100, 1),
                     remaining_shares=trade.shares)
            p1_done = True

        if p1_done and not p2_done and partial_tp2_pct and pnl_pct >= partial_tp2_pct and trade.shares > 0.01:
            partial_shares = round(trade.shares * 0.50, 4)
            slippage = (
                _size_aware_slippage_pct(partial_shares, _avg_daily_volume_for(trade.symbol), _base_slippage)
                if cfg.get("size_aware_slippage_enabled", True) else _base_slippage
            )
            partial_price  = round(live_price * (1 - slippage), 4)
            partial_value  = round(partial_shares * partial_price, 2)
            partial_pnl    = round((partial_price - entry) * partial_shares, 2)
            partial_commission = round(cfg.get("commission_per_share", 0.0) * partial_shares, 4)
            trade.shares = round(trade.shares - partial_shares, 4)
            trade.realized_pnl = round((trade.realized_pnl or 0.0) + partial_pnl - partial_commission, 2)
            portfolio.current_cash = round(portfolio.current_cash + partial_value - partial_commission, 2)
            lock_stop = round(entry * 1.05, 2)
            if trade.current_stop < lock_stop:
                trade.current_stop = lock_stop
            notes_list2 = list(trade.entry_decision_notes or [])
            notes_list2.append(_P2)
            notes_list2.append(
                f"Scale-out-2: sold {partial_shares:.4f}sh @ ${partial_price:.2f} "
                f"(+{pnl_pct*100:.1f}%), remaining {trade.shares:.4f}sh, stop → +5%"
            )
            trade.entry_decision_notes = notes_list2
            log.info("paper.partial2_profit_taken",
                     symbol=trade.symbol, partial_shares=partial_shares,
                     partial_price=partial_price, partial_value=partial_value,
                     partial_pnl=partial_pnl, pnl_pct=round(pnl_pct * 100, 1),
                     remaining_shares=trade.shares)

        # ── Trailing stop management (still open) ─────────────────────────────

        trail_trigger = cfg.get("trail_trigger_pct", 0.05)
        be_trigger    = cfg.get("breakeven_trigger_pct", 0.03)

        # PT-M2: Earnings proximity — freeze trail updates within 2 trading days of earnings.
        # Binary events gap both ways; stopping out 2 days before a blowout quarter is costly.
        # Hard stop (stop_loss) remains active — only dynamic trail updates are paused.
        _dte = (current_sig.reasons or {}).get("days_to_earnings") if current_sig else None
        earnings_near = _dte is not None and int(_dte) <= 2
        if earnings_near:
            log.info("paper.earnings_proximity_stop_frozen",
                     symbol=trade.symbol, dte=int(_dte),
                     current_stop=round(trade.current_stop, 2),
                     note="trail frozen — earnings binary event within 2 days")

        # Trail is armed once highest_price has ever cleared trail_trigger above entry.
        # After arming, update every cycle so stop ratchets up continuously on new highs.
        trail_armed = (trade.highest_price or entry) >= entry * (1 + trail_trigger)
        if trail_armed and not earnings_near:
            atr = monitor_atr_cache.get(trade.symbol)
            if atr is not None and atr > 0.01:  # guard against None, NaN, or near-zero
                mult = cfg.get("trail_atr_mult", 2.0) * regime_trail_adj
                new_trail = (trade.highest_price or live_price) - atr * mult
                # Never let trail fall below initial stop_loss
                floored_trail = max(new_trail, trade.stop_loss)
                if floored_trail > trade.current_stop:
                    old = trade.current_stop
                    trade.current_stop = round(floored_trail, 4)
                    if new_trail < trade.stop_loss:
                        log.warning("paper.trail_below_initial_stop",
                                    symbol=trade.symbol,
                                    atr_trail=round(new_trail, 2),
                                    floored_to=round(trade.stop_loss, 2))
                    else:
                        log.info("paper.trail_stop_raised",
                                 symbol=trade.symbol, old=round(old, 2), new=round(floored_trail, 2),
                                 profit_pct=round(pnl_pct * 100, 1))
            else:
                log.warning("paper.trail_atr_invalid", symbol=trade.symbol, atr=atr,
                            note="skipping trail update this cycle")

        # All tightening checks respect earnings proximity freeze — never tighten into a binary event.
        if not earnings_near:
            atr = monitor_atr_cache.get(trade.symbol)

        # Double-top neckline break mid-trade — tighten trail multiplier to 1.2× (from 2.0×)
        # A confirmed double-top breakdown while holding means the thesis is reversing
        sig_reasons = {}
        if not earnings_near:
            try:
                from sqlalchemy import text as sa_text
                sig_reasons = session.execute(
                    sa_text("SELECT reasons FROM signals WHERE stock_id = :sid AND horizon = :h ORDER BY ts DESC LIMIT 1"),
                    {"sid": trade.stock_id, "h": trade.trading_style or cfg.get("trading_style", "GROWTH")},
                ).mappings().one_or_none()
                sig_reasons = dict(sig_reasons["reasons"] or {}) if sig_reasons else {}
            except Exception:
                pass
        if sig_reasons.get("double_top_breakdown") and trail_armed and not earnings_near and atr is not None and atr > 0.01:
            tight_mult = 1.2 * regime_trail_adj
            tight_trail = max((trade.highest_price or live_price) - atr * tight_mult, trade.stop_loss)
            if tight_trail > trade.current_stop:
                trade.current_stop = round(tight_trail, 4)
                log.warning("paper.double_top_trail_tightened", symbol=trade.symbol,
                            new_stop=round(tight_trail, 2),
                            note="double-top neckline break detected — tightening trail to 1.2× ATR")

        # PT-H3: K-score deterioration — tighten trail when institutional flow reverses.
        # A 15-pt drop in K-score (out of 100) signals smart-money distribution.
        # Apply only when trail is armed (we have a gain to protect) to avoid premature exits.
        kscore_entry = trade.kscore_at_entry
        if (not earnings_near and kscore_entry is not None and trail_armed and
                atr is not None and atr > 0.01):
            current_kscore = latest_kscores.get(trade.symbol)
            if current_kscore is not None and current_kscore < kscore_entry - 15:
                kdet_mult  = 1.5 * regime_trail_adj
                kdet_trail = max((trade.highest_price or live_price) - atr * kdet_mult, trade.stop_loss)
                if kdet_trail > trade.current_stop:
                    trade.current_stop = round(kdet_trail, 4)
                    log.warning("paper.kscore_deterioration_trail_tightened",
                                symbol=trade.symbol,
                                kscore_at_entry=round(kscore_entry, 1),
                                kscore_now=round(current_kscore, 1),
                                drop=round(kscore_entry - current_kscore, 1),
                                new_stop=round(kdet_trail, 2),
                                note="K-score drop >15 pts — possible institutional distribution")

        # PT-H4: OBV divergence — price holding but volume declining (quiet distribution).
        # Only tighten when we have a gain to protect AND price is above entry.
        if (not earnings_near and trail_armed and atr is not None and atr > 0.01 and
                pnl_pct >= 0.02 and _obv_divergence.get(trade.symbol)):
            obv_mult  = 1.5 * regime_trail_adj
            obv_trail = max((trade.highest_price or live_price) - atr * obv_mult, trade.stop_loss)
            if obv_trail > trade.current_stop:
                trade.current_stop = round(obv_trail, 4)
                log.warning("paper.obv_divergence_trail_tightened",
                            symbol=trade.symbol,
                            pnl_pct=round(pnl_pct * 100, 1),
                            new_stop=round(obv_trail, 2),
                            note="OBV declining while price holds — possible distribution")

        # PT-M1: Relative strength vs sector exit — stock lagging its sector ETF by > 10pp
        # over the last 5 trading days signals idiosyncratic weakness, not market noise.
        # Only tighten when trail is armed (protecting a gain), not earnings, and we have ATR.
        if (not earnings_near and trail_armed and atr is not None and atr > 0.01 and
                pnl_pct >= 0.02 and _rs_sector_lag.get(trade.symbol)):
            rs_mult  = 1.5 * regime_trail_adj
            rs_trail = max((trade.highest_price or live_price) - atr * rs_mult, trade.stop_loss)
            if rs_trail > trade.current_stop:
                trade.current_stop = round(rs_trail, 4)
                etf_sym = _SECTOR_ETF_MAP.get(trade.sector or "", "SPY")
                log.warning("paper.sector_rs_lag_trail_tightened",
                            symbol=trade.symbol,
                            sector=trade.sector,
                            sector_etf=etf_sym,
                            pnl_pct=round(pnl_pct * 100, 1),
                            new_stop=round(rs_trail, 2),
                            note="Stock lagging sector ETF by >10pp over 5d — sector RS weakness")

        # PT-H5: RSI overbought trail tightening — when RSI > 75 AND we have a gain to
        # protect (trail armed), tighten to 1.0× ATR to lock in profits near the peak.
        # Only fires when profitable (pnl ≥ 5%) to avoid over-managing early-stage trades.
        if (not earnings_near and trail_armed and atr is not None and atr > 0.01 and
                pnl_pct >= 0.05 and _rsi_overbought.get(trade.symbol)):
            rsi_mult  = 1.0 * regime_trail_adj
            rsi_trail = max((trade.highest_price or live_price) - atr * rsi_mult, trade.stop_loss)
            if rsi_trail > trade.current_stop:
                trade.current_stop = round(rsi_trail, 4)
                log.info("paper.rsi_overbought_trail_tightened",
                         symbol=trade.symbol,
                         pnl_pct=round(pnl_pct * 100, 1),
                         new_stop=round(rsi_trail, 2),
                         note="RSI > 75 with gain ≥5% — tightening trail to 1.0× ATR to lock in profits")

        # PT-I1: Fundamental deterioration exit — if research now says AVOID/SELL and we
        # have a profit to protect, tighten trail to 1.5× ATR to exit faster without a
        # hard cut. Only fires when trail is armed and pnl ≥ 2%.
        if (not earnings_near and trail_armed and atr is not None and atr > 0.01 and
                pnl_pct >= 0.02 and _research_deteriorated.get(trade.symbol)):
            det_mult  = 1.5 * regime_trail_adj
            det_trail = max((trade.highest_price or live_price) - atr * det_mult, trade.stop_loss)
            if det_trail > trade.current_stop:
                trade.current_stop = round(det_trail, 4)
                log.info("paper.research_deterioration_trail_tightened",
                         symbol=trade.symbol,
                         pnl_pct=round(pnl_pct * 100, 1),
                         new_stop=round(det_trail, 2),
                         note="Research AVOID/SELL with gain ≥2% — tightening trail to 1.5× ATR")

        # PA-A3: Breakeven stop — unconditional (not elif) so it fires even when trail
        # is armed but ATR trail is still below entry (e.g. large ATR, small gain so far)
        if pnl_pct >= be_trigger and trade.current_stop < entry:
            trade.current_stop = entry
            log.info("paper.stop_to_breakeven",
                     symbol=trade.symbol, entry=entry, pct=round(pnl_pct * 100, 1))

    # PA-D1: sector cap monitor — warn if any sector exceeds max_sector_pct on open positions
    max_sector_pct = cfg.get("max_sector_pct", 0.30)
    equity = _compute_equity(session, portfolio, live_prices)
    if equity > 0:
        sector_value: dict[str, float] = {}
        for trade in open_trades:
            if trade.stage != "open":
                continue
            # AUD232-062: use the shared _best_price() helper instead of a hand-rolled copy of
            # its live -> cached -> entry fallback chain, so a future change to that fallback
            # logic (e.g. a stale-price floor or a fallback-used warning) is picked up here too
            # instead of this sector-cap monitor silently keeping the old behavior.
            value = _best_price(trade, live_prices) * (trade.shares or 0)
            sector = (trade.sector or "unknown")
            sector_value[sector] = sector_value.get(sector, 0.0) + value
        for sector, value in sector_value.items():
            pct = value / equity
            if pct > max_sector_pct:
                log.warning("paper.sector_cap_exceeded",
                            sector=sector,
                            sector_pct=round(pct * 100, 1),
                            max_pct=round(max_sector_pct * 100, 1),
                            value=round(value, 0))

    return closed_exits


# ── Entry scanner ─────────────────────────────────────────────────────────────

def _recent_win_rate(session, portfolio_id: int, n: int = 20) -> float | None:
    """Win rate of the last n closed trades for this portfolio. Returns None if < 5 trades."""
    rows = session.execute(
        select(PaperTrade.exit_reason, PaperTrade.pnl)
        .where(
            PaperTrade.portfolio_id == portfolio_id,
            PaperTrade.stage == "closed",
            PaperTrade.pnl.isnot(None),
        )
        .order_by(PaperTrade.exit_time.desc())
        .limit(n)
    ).all()
    if len(rows) < 5:
        return None
    wins = sum(1 for _, pnl in rows if pnl > 0)
    return wins / len(rows)


def _consec_loss_streak(session, portfolio_id: int) -> int:
    """Count of consecutive losing trades from the tail of closed history."""
    rows = session.execute(
        select(PaperTrade.pnl)
        .where(
            PaperTrade.portfolio_id == portfolio_id,
            PaperTrade.stage == "closed",
            PaperTrade.pnl.isnot(None),
        )
        .order_by(PaperTrade.exit_time.desc())
        .limit(10)
    ).scalars().all()
    streak = 0
    for pnl in rows:
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def _call_decision_engine(
    symbol: str,
    live_price: float,
    game_plan: dict,
    equity: float,
    open_count: int,
    cfg: dict,
    initial_capital: float | None = None,
    daily_pnl_pct: float = 0.0,
    recent_win_rate: float | None = None,
    open_sector_counts: dict | None = None,
    candidate_sector: str | None = None,
    consec_losses: int = 0,
    kscore: float | None = None,
    ta_score: float | None = None,
    regime_state: str = "neutral",
    confidence_delta: float | None = None,
    index_return_pct: float | None = None,
    sig_ref_price: float | None = None,
    short_signal: str | None = None,
    recent_stop_count: int | None = None,
    market_open_count: int | None = None,
    open_sector_value: float | None = None,
    open_risk_total: float | None = None,
    weekly_net_pnl_pct: float | None = None,
    open_exposure_pct: float | None = None,
    squeeze_score: float | None = None,
    pressure_score: float | None = None,
) -> tuple[bool, str, int, str | None] | None:
    """Call Decision Engine and return (should_enter, verdict, score, blocked_reason).

    Returns None if the DE is unreachable (caller falls back to _should_enter()).
    Never raises — failures return None so DE unavailability never blocks entries.
    """
    try:
        import httpx as _httpx
        from common.config import get_settings as _gs_de
        de_url = _gs_de().decision_engine_url
        r = _httpx.post(
            f"{de_url}/decide/{symbol}",
            json={
                "style":            cfg.get("trading_style", "SWING"),
                "equity":           equity,
                # T232-DL-DUALSCORER-DEBT / T201: needed alongside equity above so
                # hard_rejects.py can reconstruct _scan_for_entries' own equity-floor circuit
                # breaker (equity/initial_capital < equity_floor_pct). Falls back to `equity`
                # itself (a 100% ratio, i.e. the gate can never fire) when the real caller
                # doesn't have a portfolio to reference — matches decision-engine's own
                # DecisionRequest default (10_000.0) semantics of "no real portfolio context."
                "initial_capital":  initial_capital if initial_capital is not None else equity,
                "open_positions":   open_count,
                "max_positions":    cfg.get("max_positions", 6),
                "live_price":       live_price,
                "game_plan":        game_plan,
                "market":           cfg.get("market", "US"),
                "daily_pnl_pct":    daily_pnl_pct,
                "config_overrides": {
                    "min_entry_score":        cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]),
                    "min_confidence":         cfg.get("min_confidence", _DEFAULT_CONFIG["min_confidence"]),
                    # AUD256: min_rr_ratio's own fallback literal (2.0) bypassed calibration —
                    # _should_enter() resolves this same key via _default_min_rr_ratio("neutral"),
                    # which returns the calibrated value once SELFIMPROVE-NEVER-CALIBRATED-PARAMS'
                    # min_rr_calibration.json has been written, falling back to 2.0 only if
                    # calibration has never run. Route through the same resolver so DE and the
                    # fallback agree on the SAME baseline instead of DE silently using a stale
                    # hardcoded literal forever regardless of calibration.
                    "min_rr_ratio":           cfg.get("min_rr_ratio", _default_min_rr_ratio("neutral")),
                    # AUD256: regime_min_rr_ratio was never sent at all — decision-engine's own
                    # hard_rejects.py has a read-side default of 3.0 for choppy/risk_off regimes
                    # (T190) that DE always fell back to, completely blind to calibration, even
                    # though _should_enter() has been correctly regime-aware here since AUD232-060.
                    # Threaded through unconditionally (matches min_rr_ratio's own always-sent
                    # convention above) so DE's choppy/risk_off floor tracks the SAME calibrated
                    # value _should_enter() already uses, not a permanently-stale literal.
                    "regime_min_rr_ratio":    cfg.get("regime_min_rr_ratio", _default_min_rr_ratio(regime_state)),
                    "risk_per_trade_pct":     cfg.get("risk_per_trade_pct", 0.01),
                    "max_position_pct":       cfg.get("max_position_pct", 0.10),
                    "max_loss_per_trade_pct": cfg.get("max_loss_per_trade_pct", 0.02),
                    **( {"recent_win_rate": recent_win_rate} if recent_win_rate is not None else {} ),
                    **( {"open_sector_counts": open_sector_counts, "candidate_sector": candidate_sector}
                        if open_sector_counts is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: sector_cap (dollar-exposure, not the count-based
                    # cap above) and open_risk_cap gate parity. Both real, already-known
                    # portfolio-wide aggregates from the ALREADY-prefetched open book — the
                    # candidate's OWN not-yet-computed contribution (stop_distance/shares aren't
                    # sized until AFTER this call in the real function) is approximated on the
                    # DE side using max_position_pct/max_loss_per_trade_pct (both already sent
                    # above) as the same worst-case ceilings the real sizing logic itself caps
                    # against — never an under-estimate, so this can only be as-or-more
                    # conservative than the real fallback gate, never less.
                    **( {"open_sector_value": open_sector_value, "max_sector_pct": cfg.get("max_sector_pct", 0.25)}
                        if open_sector_value is not None else {} ),
                    **( {"open_risk_total": open_risk_total, "max_open_risk_pct": cfg.get("max_open_risk_pct", 0.12)}
                        if open_risk_total is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: weekly loss/gain circuit breakers — real,
                    # portfolio-wide gates in _scan_for_entries() with zero decision-engine
                    # equivalent before this. Both derive from the SAME weekly_net_pnl_pct value
                    # (a signed %, negative = loss, positive = gain), matching the fallback
                    # gate's own single-query-serves-both-checks shape. None (not sent at all)
                    # when the caller's own equity/needs-weekly guard never computed it —
                    # matches every other optional gate's conditional-inclusion convention here.
                    **( {"weekly_net_pnl_pct": weekly_net_pnl_pct,
                         "max_weekly_loss_pct": cfg.get("max_weekly_loss_pct", 0.08),
                         "max_weekly_gain_pct": cfg.get("max_weekly_gain_pct", 0.06)}
                        if weekly_net_pnl_pct is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT / T194: open-exposure cap — the one aggregate-
                    # exposure gate of the three siblings (sector-$ cap, open-risk cap, this
                    # one) that was never ported. Reuses the SAME already-prefetched-open-book
                    # aggregate _scan_for_entries() itself sums (via _open_sector_values), never
                    # a second independent computation that could drift from it.
                    **( {"open_exposure_pct": open_exposure_pct,
                         "max_open_exposure_pct": cfg.get("max_open_exposure_pct", 0.40)}
                        if open_exposure_pct is not None else {} ),
                    **( {"consec_losses": consec_losses} if consec_losses > 0 else {} ),
                    # AUD232-042: DE previously had zero K-Score/ranking-engine reference
                    # anywhere in its scoring — a LONG-horizon stock with kscore=25 (below the
                    # LONG conviction gate's 55 floor) could still score highly and enter via
                    # DE's gate purely because compute_score() had no K-Score input to reflect
                    # the fundamental weakness. Threaded through config_overrides, matching the
                    # existing recent_win_rate/consec_losses extension pattern.
                    **( {"kscore": kscore} if kscore is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: min_kscore is _scan_for_entries' own HARD
                    # pre-filter (candidates below this are discarded before DE is ever called
                    # at all on the real production path) — but DE itself had no hard-reject
                    # equivalent, only the soft ±1 kscore scoring layer above (AUD232-042). This
                    # made /decide/{symbol} silently accept a candidate below the real min_kscore
                    # floor whenever called standalone (e.g. decide.tsx), which never runs
                    # _scan_for_entries' pre-filter at all. Threaded through so hard_rejects.py
                    # can enforce the SAME floor _scan_for_entries already enforces upstream.
                    **( {"min_kscore": cfg.get("min_kscore", _DEFAULT_CONFIG["min_kscore"])} if kscore is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: min_ta_score is _scan_for_entries' own HARD
                    # pre-filter (T224-C/T225-A, ~line 4207) — a candidate below it is discarded
                    # before DE is ever called on the real production path. DE had no equivalent
                    # hard reject, only its own soft TA-related scoring layers, so /decide/{symbol}
                    # called standalone (e.g. decide.tsx, which never runs _scan_for_entries' own
                    # pre-filter) could silently accept a candidate below the real min_ta_score
                    # floor. Threaded through so hard_rejects.py can enforce the SAME floor
                    # _scan_for_entries already enforces upstream — same pattern as min_kscore above.
                    **( {"ta_score": ta_score} if ta_score is not None else {} ),
                    # min_ta_score has NO _DEFAULT_CONFIG entry — it's only ever set via
                    # _STYLE_OVERRIDES (SWING=0.50) or _HK_MARKET_OVERRIDES (0.65); the read
                    # side's own fallback (line ~4218) is a bare 0.0, which disables the gate
                    # (0.0 > 0 is False) — matched exactly here, NOT _DEFAULT_CONFIG["min_ta_score"]
                    # (which doesn't exist and would KeyError).
                    **( {"min_ta_score": cfg.get("min_ta_score", 0.0)} if ta_score is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: T202's declining-confidence gate is
                    # _scan_for_entries' own HARD pre-filter (confidence_delta computed at the
                    # SA-26 trajectory query, ~line 4297) — a candidate whose confidence dropped
                    # more than max_confidence_decline points since the prior signal is discarded
                    # before DE is ever called on the real production path. DE only has the SAME
                    # value's SOFT ±1 scoring layer (scorer.py's own SA-26 mirror), never a hard
                    # reject, so /decide/{symbol} called standalone (e.g. decide.tsx, which never
                    # runs _scan_for_entries' own pre-filter) could silently accept a candidate
                    # below the real max_confidence_decline floor. Threaded through so
                    # hard_rejects.py can enforce the SAME floor _scan_for_entries already
                    # enforces upstream — same pattern as min_kscore/min_ta_score above. Unlike
                    # those two (positive floors, value < min blocks), this threshold is a
                    # NEGATIVE number and the gate blocks when the delta falls BELOW it
                    # (confidence_delta < max_confidence_decline) — the sign must NOT be treated
                    # like a positive floor.
                    **( {"confidence_delta": confidence_delta} if confidence_delta is not None else {} ),
                    **( {"max_confidence_decline": cfg.get("max_confidence_decline", -8.0)}
                        if confidence_delta is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: T221's index-trend gate is _scan_for_entries'
                    # own HARD pre-filter (index_return_pct < index_trend_gate_pct, default
                    # -1.5% — a single-day macro-shock catch, distinct from the regime filter's
                    # sustained bear/risk_off classification) — a candidate is discarded before
                    # DE is ever called on the real production path. DE had no equivalent at
                    # all, so /decide/{symbol} called standalone (e.g. decide.tsx) could
                    # silently approve an entry right after a sharp index selloff the fallback
                    # would reject outright. UNLIKE min_kscore/min_ta_score/HK-flow/low-volume,
                    # this value was never already flowing to DE anywhere (not in sig.reasons,
                    # not in /stocks/regime) — a genuine write-side change, not a free port.
                    # index_return_pct is already computed once per scan cycle above (before
                    # the candidate loop), reused here rather than re-fetched per-candidate.
                    **( {"index_return_pct": index_return_pct} if index_return_pct is not None else {} ),
                    **( {"index_trend_gate_pct": cfg.get("index_trend_gate_pct", -0.015)}
                        if index_return_pct is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT: T196's price-drift gate is _scan_for_entries'
                    # own HARD pre-filter (live_price / sig_ref_price - 1 > max_price_drift_pct,
                    # default 3%) — don't chase a stock that has already rallied hard since the
                    # signal's own reference close. A candidate is discarded before DE is ever
                    # called on the real production path. DE had no equivalent at all, so
                    # /decide/{symbol} called standalone (e.g. decide.tsx) could silently
                    # approve an entry already extended well past the signal's own reference
                    # price. Deliberately NOT reused from sig.reasons["last_price"] — that
                    # value is a frozen snapshot captured at signal-GENERATION time (signal-
                    # engine's own daily-close read), whereas T196's own query is re-derived
                    # fresh, as-of-the-signal's-date, every time the gate runs; the two only
                    # coincide when the gate evaluates a signal from the SAME refresh cycle
                    # that generated it (true today, but not a safe assumption for a candidate
                    # carried over from an earlier cycle/day) — verified this divergence risk
                    # directly before choosing to thread a genuine fresh value instead of
                    # reusing the reasons field. sig_ref_price is looked up fresh from
                    # _sig_ref_prices at the real call site, matching this file's other
                    # per-candidate values (kscore, ta_score) rather than a once-per-scan value
                    # like index_return_pct.
                    **( {"sig_ref_price": sig_ref_price} if sig_ref_price is not None else {} ),
                    **( {"max_price_drift_pct": cfg.get("max_price_drift_pct", 3.0)}
                        if sig_ref_price is not None else {} ),
                    # MPE-05: Market Pressure composite scores (MPE-01 short-squeeze, MPE-02
                    # options-pressure) — pure corroborating soft-score layers (Layer 9,
                    # scorer.py), never a hard gate. Both are symbol-level, universe-scoped
                    # values computed by market-data's own compute_short_squeeze_score()/
                    # compute_options_pressure_score(), not something signal-engine's own
                    # reasons dict already carries — unlike calibrated_win_rate above, there is
                    # no free port here; the real call site below fetches them fresh per
                    # candidate right before this call.
                    **( {"squeeze_score": squeeze_score} if squeeze_score is not None else {} ),
                    **( {"pressure_score": pressure_score} if pressure_score is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT / T226-A: _scan_for_entries' risk_off hard block
                    # (T226-A — data-backed: 9/30 real closed paper trades entered in risk_off
                    # had a 0% win rate) has no decision-engine equivalent — DE only has the
                    # SOFT R:R-stiffening check (T190). Threaded through unconditionally (like
                    # regime_min_rr_ratio above) rather than gated on regime_state being set,
                    # since regime_state is ALWAYS sent on the real call site regardless — this
                    # is a genuinely free port, no new value needed, just the config that
                    # controls whether/how the ALREADY-sent regime_state should hard-block.
                    "regime_risk_off_gate": cfg.get("regime_risk_off_gate", True),
                    **( {"regime_risk_off_override_until": cfg["regime_risk_off_override_until"]}
                        if cfg.get("regime_risk_off_override_until") else {} ),
                    # T203-LLMWIRE: llm_scoring_enabled existed in decision-engine's
                    # llm_scorer.py since T203 but was never threaded from portfolio config
                    # into this request — a built-but-dormant feature with no way to turn it
                    # on for any real portfolio. Opt-in per portfolio via the Config Panel;
                    # requires a Claude/DeepSeek key configured (personal or shared server key).
                    **( {"llm_scoring_enabled": True, "llm_score_weight": cfg.get("llm_score_weight", 1),
                         **( {"llm_model": cfg["llm_model"]} if cfg.get("llm_model") else {} )}
                        if cfg.get("llm_scoring_enabled") else {} ),
                    # CLAUDE-API-COST-AUDIT: risk_check_enabled had the EXACT same T203-LLMWIRE
                    # gap as llm_scoring_enabled above — decision-engine's risk_agent.py reads
                    # cfg.get("risk_check_enabled", False) (routes.py:283) but nothing ever
                    # threaded the portfolio's own setting into this request, so it was a
                    # built-but-dormant opt-in with no way to turn it on for any real portfolio.
                    **( {"risk_check_enabled": True} if cfg.get("risk_check_enabled") else {} ),
                    # T232-DL-DUALSCORER-DEBT / T215+T222-B: multi-timeframe confluence is
                    # _scan_for_entries' own HARD pre-filter — a GROWTH/LONG/SWING BUY whose
                    # SHORT-horizon signal has already flipped to SELL (near-term momentum
                    # working against the entry) is discarded before DE is ever called on the
                    # real production path. DE had no equivalent, so /decide/{symbol} called
                    # standalone (e.g. decide.tsx, which never runs _scan_for_entries' own
                    # pre-filter) could silently approve an entry its own near-term signal
                    # already contradicts. short_signal is looked up fresh from the SAME
                    # _short_signals batch query _scan_for_entries already builds once per scan
                    # cycle (not re-fetched per-candidate) — threaded through here rather than
                    # reused from anywhere in sig.reasons, since no such field exists there.
                    **( {"short_signal": short_signal, "confluence_check_enabled": cfg.get("confluence_check_enabled", True)}
                        if short_signal is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT / T221-E: portfolio heat brake — too many stops
                    # hit recently means adverse market conditions, and _scan_for_entries pauses
                    # ALL new entries portfolio-wide rather than scoring individual candidates.
                    # This is genuinely portfolio-level state (a count of THIS portfolio's own
                    # recent stop_hit exits), but the count itself is already computed once per
                    # scan cycle before the per-candidate loop begins — the same shape as
                    # recent_win_rate/consec_losses above, not a structural blocker requiring
                    # cross-portfolio architecture. Threaded through so /decide/{symbol} called
                    # standalone (e.g. decide.tsx, which never runs _scan_for_entries' own
                    # pre-filter) can't silently approve an entry into a portfolio the fallback
                    # engine itself would have paused entirely.
                    **( {"recent_stop_count": recent_stop_count,
                         "heat_brake_max_stops": cfg.get("heat_brake_max_stops", _DEFAULT_CONFIG["heat_brake_max_stops"])}
                        if recent_stop_count is not None else {} ),
                    # T232-DL-DUALSCORER-DEBT / T221-B: market cluster cap — _scan_for_entries'
                    # own portfolio-wide, per-market position count (HK stocks are highly
                    # correlated; a market-wide down day stops out all positions simultaneously),
                    # blocking ALL new entries once at the cap rather than scoring individual
                    # candidates. Same shape as recent_stop_count/recent_win_rate/consec_losses
                    # above — a single-portfolio count computed once per scan cycle before the
                    # candidate loop begins, not cross-portfolio state DE structurally can't
                    # replicate. DE had no equivalent at all, so /decide/{symbol} called
                    # standalone (e.g. decide.tsx, which never runs _scan_for_entries' own
                    # pre-filter) could silently approve an entry into a market the fallback
                    # engine itself would have refused to add to.
                    **( {"market_open_count": market_open_count,
                         "max_market_positions": cfg.get("max_market_positions", _DEFAULT_CONFIG["max_market_positions"])}
                        if market_open_count is not None else {} ),
                },
            },
            headers={"Authorization": f"Bearer {_svc_token()}"},
            timeout=2.5,
        )
        if r.status_code != 200:
            log.warning("decision_engine.bad_status", symbol=symbol, status=r.status_code)
            return None
        result = r.json()
        verdict       = result.get("verdict", "SKIP")
        should_enter  = verdict == "BUY"
        score         = result.get("score", 0)
        blocked       = result.get("blocked_reason")
        # AUD-DECIDE2-SHADOWMINSCORE: decision-engine's own response always carries its real,
        # regime-adjusted min_score (DecisionResponse.min_score, computed by
        # min_score_for_regime() — see scorer.py) — this was being silently discarded, with
        # every caller instead recording its OWN pre-call cfg["min_entry_score"] as if it were
        # DE's verdict. See the caller's own _record_de_shadow_comparison() docstring for the
        # concrete divergence this produced live (a single symbol logged 2 different
        # de_min_score values 12 seconds apart, neither matching what DE actually used).
        de_min_score  = result.get("min_score")
        return should_enter, verdict, score, blocked, de_min_score
    except Exception as exc:
        log.debug("decision_engine.call_error", symbol=symbol, error=str(exc))
        return None


# T232-DL-DUALSCORER-SHADOW: max list length for de:divergences/de:agreements — bounds Redis
# memory while keeping enough history for the /paper-portfolio/de-divergences UI (which reads
# up to 500) to show a meaningful sample rather than just the last few scan cycles.
_DE_SHADOW_LIST_MAXLEN = 2000


def _record_de_shadow_comparison(
    symbol: str,
    paper_enter: bool,
    paper_score: int,
    de_verdict: str,
    de_score: int,
    de_min_score: int,
    de_blocked_reason: str | None,
) -> None:
    """Record whether _should_enter() and decision-engine agreed on this candidate.

    T232-DL-DUALSCORER-SHADOW: the /paper-portfolio/de-divergences endpoint and its "DE Audit"
    UI tab have existed since before this fix with zero writer anywhere in the codebase — the
    Redis lists it reads (de:divergences, de:agreements) were always empty, so the endpoint
    silently always returned total_divergences=0/total_agreements=0 and the UI always showed
    "No shadow data yet" regardless of how long the system had been running. This is the first
    writer: called once per candidate regardless of which scorer is currently authoritative
    (decision_engine_mode="primary" or "shadow"), so real agreement-rate data accumulates on
    both settings rather than only ever comparing against whichever mode happens to be active.
    Fail-silent — shadow logging must never affect the real entry decision or block trading.
    """
    import json as _json
    # AUD266-STYLE-DEAD-STRING: decision-engine's real verdict vocabulary is exactly
    # BUY/HOLD/SKIP/BLOCKED (confirmed via grep — "SCALE" never appears anywhere in
    # services/decision-engine/src/) — the same dead-string pattern already found and fixed
    # once this session in the alert-fired gate (AUD266-TWO-GATES-CONTRADICTORY-BARS). Harmless
    # here (an OR-branch that's always False changes nothing), but left correct rather than
    # left stale, matching that same fix's own reasoning.
    de_agrees = paper_enter == (de_verdict == "BUY")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "paper_enter": paper_enter,
        "paper_score": paper_score,
        "de_verdict": de_verdict,
        "de_score": de_score,
        "de_min_score": de_min_score,
        "de_blocked_reason": de_blocked_reason,
    }
    if not de_agrees:
        key = "de:divergences"
    else:
        key = "de:agreements"
        payload = {
            "ts": payload["ts"], "symbol": symbol, "verdict": de_verdict,
            "paper_enter": paper_enter, "de_score": de_score, "paper_score": paper_score,
        }
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        _r.lpush(key, _json.dumps(payload))
        _r.ltrim(key, 0, _DE_SHADOW_LIST_MAXLEN - 1)
    except Exception as exc:
        # AUD232-SILENT-EXCEPTION-AUDIT: still fail-silent (this function's own contract
        # above — shadow logging must never affect the real entry decision), but a
        # persistent failure here would silently repeat the exact "endpoint always
        # returns zero data with no clue why" bug this whole mechanism was built to fix
        # in the first place (see the docstring above) — now at least visible in logs.
        log.warning("paper.de_shadow_comparison_write_failed", symbol=symbol, error=str(exc))


# T241-P6: position-scaling shadow verdicts, same bounded-Redis-list pattern as
# de:divergences/de:agreements above. Verdicts start in ps:shadow:pending (unresolved — we
# don't yet know if the "would-act" prediction was right) and move to ps:shadow:resolved once
# _resolve_position_scaling_shadow_verdicts() (scheduler.py) checks the real price outcome
# after enough time has passed. Kept as two separate lists (not one list with a "resolved"
# flag) so /paper-portfolio/position-scaling-shadow can cheaply report "N pending, M resolved,
# X% hit rate" without scanning and filtering a single combined list on every request.
_PS_SHADOW_LIST_MAXLEN = 2000


def _record_position_scaling_shadow_verdict(
    symbol: str,
    portfolio_id: int,
    act_probability: float,
    suggested_size_multiplier: float,
    would_act: bool,
    thesis_recommendation: str,
    thesis_broken_reasons: list[str],
    price_at_verdict: float,
    entry_price: float,
    max_holding_days: int,
) -> None:
    """Record one position-scaling shadow verdict for later outcome resolution.

    Fail-silent — shadow logging must never affect the real trading decision or block the
    scan loop. resolve_after is the timestamp _resolve_position_scaling_shadow_verdicts()
    uses to know when enough price history exists to judge whether would_act was correct
    (matching BarrierConfig.max_holding_days, the same horizon the model was trained on).

    T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): _scan_for_entries() runs
    roughly every 5 minutes during market hours, so a single position sitting below its cost
    basis with an active BUY signal would previously log a near-identical verdict ~60-80
    times in one trading day. Since all of those resolve ~20 days later against essentially
    the same real outcome, the eventual "hit rate" the comparison report shows would be
    dominated by whichever few symbols happened to sit in a long pullback rather than a
    representative independent sample, and a handful of such symbols could evict everything
    else from the bounded ps:shadow:pending list. Deduped here via a per symbol+portfolio+day
    Redis marker (SETNX, 25h TTL — slightly over a day so a marker set just before midnight
    UTC still blocks a duplicate shortly after) so at most one verdict is recorded per
    position per calendar day, regardless of how many scan ticks evaluate it that day.
    """
    import json as _json

    today_str = datetime.now(timezone.utc).date().isoformat()
    dedup_key = f"ps:shadow:seen:{portfolio_id}:{symbol}:{today_str}"

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "resolve_after": (datetime.now(timezone.utc) + timedelta(days=max_holding_days)).isoformat(),
        "symbol": symbol,
        "portfolio_id": portfolio_id,
        "act_probability": round(act_probability, 4),
        "suggested_size_multiplier": suggested_size_multiplier,
        "would_act": would_act,
        "thesis_recommendation": thesis_recommendation,
        "thesis_broken_reasons": thesis_broken_reasons,
        "price_at_verdict": round(price_at_verdict, 4),
        "entry_price": round(entry_price, 4),
    }
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        if not _r.set(dedup_key, "1", nx=True, ex=25 * 3600):
            return  # already recorded this symbol+portfolio+day — skip the duplicate
        _r.lpush("ps:shadow:pending", _json.dumps(payload))
        _r.ltrim("ps:shadow:pending", 0, _PS_SHADOW_LIST_MAXLEN - 1)
    except Exception as exc:
        # AUD232-SILENT-EXCEPTION-AUDIT: still fail-silent (this function's own contract
        # above), but now visible — a persistent failure here silently starves the position-
        # scaling gate's own promotion decision of the verdicts it needs to ever accumulate
        # enough resolved outcomes to evaluate going live.
        log.warning("paper.position_scaling_shadow_write_failed", symbol=symbol, error=str(exc))


def resolve_position_scaling_shadow_verdicts(session) -> dict:
    """T241-P6: scan ps:shadow:pending for verdicts whose resolve_after has passed, look up
    the real subsequent return for each, and move them to ps:shadow:resolved with an
    outcome_correct verdict attached. Called by scheduler.py on a recurring job — NOT tied to
    the per-scan _scan_for_entries() loop, since resolution only needs to run as often as new
    verdicts finish their holding window (daily is plenty).

    "Correct" here means the would_act prediction matched the ACTUAL subsequent return sign
    relative to the price at verdict time: would_act=True is scored correct if the position
    was up by more than a small noise threshold (+0.5%, matching triple_barrier_labeling.py's
    own TIME_LIMIT correctness threshold) by resolve_after; would_act=False is scored correct
    if it was NOT (i.e. staying out would have avoided a loss or non-move). This is a real,
    if simpler, comparison than the offline training labels' with/without-add counterfactual —
    shadow mode never actually places an add, so there is no real "with-add" position to
    compare against, only "did the signal that said 'this pullback is worth watching' end up
    being right about the stock's subsequent direction."
    """
    import json as _json

    try:
        from common.redis_client import get_redis as _get_pool_redis
        r = _get_pool_redis()
    except Exception as exc:
        return {"resolved": 0, "still_pending": 0, "error": str(exc)}

    raw_pending = r.lrange("ps:shadow:pending", 0, -1)
    now = datetime.now(timezone.utc)
    to_remove: list[str] = []  # raw entries actually processed — removed via LREM, not a full-list rebuild
    resolved_count = 0
    correct_count = 0
    still_pending_count = 0

    for raw in raw_pending:
        try:
            payload = _json.loads(raw)
            resolve_after = datetime.fromisoformat(payload["resolve_after"])
        except Exception:
            to_remove.append(raw)  # malformed entry — drop rather than get stuck forever
            continue

        if now < resolve_after:
            still_pending_count += 1
            continue

        symbol = payload["symbol"]
        try:
            stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
            if stock is None:
                still_pending_count += 1
                continue
            verdict_ts = datetime.fromisoformat(payload["ts"])
            price_row = session.execute(
                select(Price.close).where(
                    Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1,
                    Price.ts >= verdict_ts.replace(tzinfo=None),
                ).order_by(Price.ts.desc()).limit(1)
            ).scalar()
        except Exception:
            still_pending_count += 1  # DB hiccup — try again on the next run, don't lose it
            continue

        if price_row is None:
            still_pending_count += 1
            continue

        subsequent_return = (float(price_row) - payload["price_at_verdict"]) / payload["price_at_verdict"]
        actually_worked = subsequent_return > 0.005  # matches triple_barrier_labeling.py's own threshold
        outcome_correct = payload["would_act"] == actually_worked

        resolved_payload = {
            **payload,
            "resolved_ts": now.isoformat(),
            "subsequent_return": round(subsequent_return, 4),
            "outcome_correct": outcome_correct,
        }
        try:
            r.lpush("ps:shadow:resolved", _json.dumps(resolved_payload))
            r.ltrim("ps:shadow:resolved", 0, _PS_SHADOW_LIST_MAXLEN - 1)
        except Exception as exc:
            # AUD232-SILENT-SHADOW-DATALOSS: previously this failure was swallowed and the
            # code fell straight through to to_remove.append(raw)/resolved_count += 1 below
            # — permanently dropping this verdict into neither "pending" NOR "resolved" (a
            # real data-loss bug, not just a missing log) while ALSO inflating the reported
            # hit_rate denominator with an outcome that was never actually persisted. This
            # feeds _retrain_position_scaling_gate/_check_position_scaling_gate_drift's own
            # promotion decision for whether to let position-scaling touch real trades — a
            # corrupted hit_rate here has real downstream consequences. Now logged AND left
            # in ps:shadow:pending (skip this raw entry this run) so the next run retries it
            # instead of losing it — matches the DB-hiccup branch's own "try again on the
            # next run, don't lose it" convention a few lines above.
            log.error("paper.position_scaling_shadow_resolve_write_failed",
                      symbol=symbol, error=str(exc))
            still_pending_count += 1
            continue
        to_remove.append(raw)
        resolved_count += 1
        if outcome_correct:
            correct_count += 1

    # T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): previously did
    # lrange -> process -> delete-the-whole-list -> rpush-back-the-still-pending-ones, which
    # silently dropped any verdict lpush'd by a concurrent scan between the lrange and the
    # delete. This job runs at 05:00 UTC = 13:00 HKT, squarely inside the HK trading session
    # when shadow verdicts are actively being recorded, so that window was real. LREM only
    # removes the SPECIFIC raw entries this run actually processed (by exact string match,
    # count=1 each so a byte-identical duplicate verdict pushed concurrently isn't also
    # removed) — anything pushed after this run's lrange snapshot was taken is left alone.
    try:
        for raw in to_remove:
            r.lrem("ps:shadow:pending", 1, raw)
    except Exception:
        pass

    return {
        "resolved": resolved_count,
        "still_pending": still_pending_count,
        "hit_rate": round(correct_count / resolved_count, 4) if resolved_count else None,
    }


def _clear_gate_block(portfolio_id: int) -> None:
    """Delete the Redis gate_block key so the UI no longer shows a block reason."""
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        _r.delete(f"paper:gate_block:{portfolio_id}")
    except Exception:
        pass


def _compute_portfolio_drawdown(session, portfolio_id: int, equity: float) -> float | None:
    """T286-DRAWDOWN-ALERT: single source of truth for current portfolio drawdown-from-peak,
    factored out of _scan_for_entries()'s own circuit-breaker block so the new user-facing
    drawdown alert (check_portfolio_drawdown_alerts() in scheduler.py) reads the EXACT same
    number the silent gate-block badge already shows, rather than a second, independently
    re-derived computation that could drift from it.

    Returns None only if there's no equity-curve history at all yet (peak_equity <= 0) — the
    circuit breaker's own pre-existing guard for this case, unchanged.
    """
    historical_peak = session.execute(
        select(func.max(PaperEquityCurve.equity))
        .where(PaperEquityCurve.portfolio_id == portfolio_id)
    ).scalar() or 0.0
    # PA-D2: include current intraday equity in peak so intraday drops are caught
    # even if today's EOD snapshot hasn't been written yet
    peak_equity = max(historical_peak, equity)
    if not peak_equity or peak_equity <= 0:
        return None
    return (peak_equity - equity) / peak_equity


_VOL_TARGET_MIN_SAMPLE_DAYS = 20  # same annualizing-sample floor as _MIN_SHARPE_DAYS (paper_portfolio.py)
_VOL_TARGET_ANNUAL_PCT = 0.15     # target annualized portfolio volatility (design doc default)
_VOL_TARGET_MULT_MIN = 0.5
_VOL_TARGET_MULT_MAX = 1.5


def _compute_portfolio_vol_targeting_mult(session, portfolio_id: int) -> float:
    """IF-13: portfolio-level realized-volatility-targeting position-size multiplier.

    Genuinely distinct signal from what regime_size_mult already dampens on: VIX (T192) is
    market-wide, ATR-based sizing elsewhere is per-stock — neither reflects THIS portfolio's
    own realized return volatility, which can diverge from both (e.g. a concentrated,
    correlated book can realize much higher volatility than a calm VIX would suggest, or vice
    versa for a well-diversified one).

    vol_mult = target_vol / realized_vol, clamped to [_VOL_TARGET_MULT_MIN, _VOL_TARGET_MULT_MAX]
    — a portfolio realizing MORE volatility than the target gets sized down (mult < 1), one
    realizing LESS gets sized up (mult > 1), matching the design doc's own formula exactly.

    Reuses _portfolio_risk_metrics()'s own annualized-volatility derivation
    (paper_portfolio.py) rather than re-deriving a second, possibly-drifting formula: daily
    returns from the equity curve -> sample variance -> sqrt(252) annualization.

    Fails open to 1.0 (no adjustment) when there isn't yet enough equity-curve history to
    annualize meaningfully (same _VOL_TARGET_MIN_SAMPLE_DAYS floor _portfolio_risk_metrics()
    itself uses for Sharpe/Sortino/Calmar) or when realized volatility is degenerate (zero).
    """
    import math

    rows = session.execute(
        select(PaperEquityCurve.equity)
        .where(PaperEquityCurve.portfolio_id == portfolio_id)
        .order_by(PaperEquityCurve.date)
    ).scalars().all()
    equities = [e for e in rows if e and e > 0]
    if len(equities) < _VOL_TARGET_MIN_SAMPLE_DAYS:
        return 1.0

    daily_returns = [(equities[i] / equities[i - 1]) - 1 for i in range(1, len(equities))]
    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / max(n - 1, 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    realized_vol = std_r * math.sqrt(252)
    if realized_vol <= 0:
        return 1.0

    vol_mult = _VOL_TARGET_ANNUAL_PCT / realized_vol
    return round(min(_VOL_TARGET_MULT_MAX, max(_VOL_TARGET_MULT_MIN, vol_mult)), 3)


def _write_gate_block(portfolio_id: int, gate: str, reason: str) -> None:
    """Record the most recent portfolio-level gate that blocked new entries.

    Stored in Redis as paper:gate_block:{portfolio_id} (JSON, 4h TTL).
    Read by the /paper-portfolio/portfolios list endpoint to surface the reason in the UI.
    Fail-silent — gate block display is informational only.
    """
    import json as _json
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        _r.setex(
            f"paper:gate_block:{portfolio_id}",
            60 * 60 * 4,  # 4 hour TTL
            _json.dumps({"gate": gate, "reason": reason,
                         "ts": datetime.now(timezone.utc).isoformat()}),
        )
    except Exception:
        pass


_SKIP_REASON_LABEL: dict[str, str] = {
    "stop_cooldown": "Recently stopped out",
    "global_symbol_cap": "Already open in another portfolio",
    "no_ranking": "No K-Score ranking available",
    "kscore": "K-Score below minimum",
    "stale_signal": "Signal too old",
    "confluence_fail": "Short-horizon signal disagrees",
    "price_drift": "Price moved too far from signal",
    "low_volume": "Volume below average — entry postponed",
    "hk_flow_gate": "HK Stock Connect flow unfavorable",
    "ta_gate": "TA score below minimum",
    "declining_confidence": "Confidence declining since signal",
    "research_gate": "Research recommendation unfavorable",
    "min_position": "Position size below minimum",
    "open_risk_cap": "Portfolio open-risk cap reached",
    "sector_cap": "Sector exposure cap reached",
    "sector_count_cap": "Sector position-count cap reached",
    "insufficient_cash": "Insufficient cash",
    "conviction_gate": "Alert system's conviction gate already rejected this BUY",
    "entry_score_below_threshold": "Entry score below minimum (DE or fallback scorer)",
    "already_open_scale_in_only": "Already an open position — evaluated for scale-in only",
    "not_on_watchlist": "Signal exists but stock isn't on this style's watchlist",
}


def _write_no_entry_summary(portfolio_id: int, candidates_seen: int, skip_tally: dict[str, int]) -> None:
    """Record why zero entries happened this cycle when no portfolio-level gate fired.

    T232-WHYNOTRADE: complements _write_gate_block — that only covers portfolio-level
    blocks (drawdown, daily loss, regime, ...). When those are all clear but every BUY
    candidate individually fails its own per-symbol check (K-Score, volume, TA score,
    cooldown, ...), nothing was previously surfaced anywhere except raw container logs.
    Stored in Redis as paper:no_entry_summary:{portfolio_id} (JSON, 4h TTL).
    """
    import json as _json
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        top_reasons = sorted(skip_tally.items(), key=lambda kv: kv[1], reverse=True)[:5]
        _r.setex(
            f"paper:no_entry_summary:{portfolio_id}",
            60 * 60 * 4,  # 4 hour TTL
            _json.dumps({
                "candidates_seen": candidates_seen,
                "top_reasons": [
                    {"reason": k, "label": _SKIP_REASON_LABEL.get(k, k), "count": v}
                    for k, v in top_reasons
                ],
                "ts": datetime.now(timezone.utc).isoformat(),
            }),
        )
    except Exception:
        pass


def _clear_no_entry_summary(portfolio_id: int) -> None:
    """Clear the no-entry summary once the portfolio actually enters a position."""
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _r = _get_pool_redis()
        _r.delete(f"paper:no_entry_summary:{portfolio_id}")
    except Exception:
        pass


def _avg_daily_volume_for(symbol: str) -> float | None:
    """IF-06: look up the symbol's own cached 20-day average daily volume (shares) from the
    same stockai:avg_volume Redis cache _fetch_live_bulk()/check_volume_anomalies() already
    read — no new data source, reusing this app's established average-volume cache. Fails open
    to None (never raises) on any Redis/parse error, matching every other lookup in this file
    that reads this same key.
    """
    try:
        import json as _json_lib
        from common.redis_client import get_redis as _get_pool_redis
        _cache = _json_lib.loads(_get_pool_redis().get("stockai:avg_volume") or "{}")
        _v = _cache.get(symbol)
        return float(_v) if _v else None
    except Exception:
        return None


_SIZE_AWARE_SLIPPAGE_IMPACT_K = 2.0  # scales the sqrt(participation) impact term — see docstring


def _size_aware_slippage_pct(shares: float, avg_daily_volume: float | None, base_slippage_pct: float) -> float:
    """IF-06: scale the flat entry/exit slippage assumption with position size relative to the
    stock's own liquidity, instead of a single constant applied to every trade regardless of
    size or how thin the name is.

    Uses the standard simplified market-impact approximation: impact grows with the SQUARE
    ROOT of participation rate (shares / avg_daily_volume), not linearly — a well-established
    approximation (see e.g. the square-root law of market impact) reflecting that impact cost
    grows sub-linearly with size for any single trade. Returns
    base_slippage_pct * (1 + _SIZE_AWARE_SLIPPAGE_IMPACT_K * sqrt(participation_rate)).

    Fails open to the unmodified base_slippage_pct — never LOWER than it — whenever
    avg_daily_volume is missing/non-positive (no average-volume data yet for this symbol) or
    shares is non-positive (a degenerate order size). This means a size-aware model can only
    ever be as-or-more conservative than the flat constant it replaces, never less.
    """
    if not avg_daily_volume or avg_daily_volume <= 0 or shares <= 0:
        return base_slippage_pct
    participation = shares / avg_daily_volume
    return round(base_slippage_pct * (1 + _SIZE_AWARE_SLIPPAGE_IMPACT_K * (participation ** 0.5)), 6)


def _slipped_position_value(shares: float, live_price: float, entry_slippage_pct: float) -> float:
    """T247-MARKETDATA-CASHGATE-PRESLIPPAGE: the cash-sufficiency gate previously compared
    pre-slippage position_value (at live_price) against current_cash, but the actual cash
    deduction recomputes position_value at the higher slipped price — the check and the charge
    used two different values, letting a candidate pass the gate and still overdraw cash
    (silently floored to 0, no error surfaced). Extracted to a pure, module-level function
    (unchanged behavior) so both the gate and the deduction always agree on the same value, and
    so the arithmetic is independently unit-testable without the surrounding DB/session machinery.
    """
    slipped_entry = round(live_price * (1 + entry_slippage_pct), 4)
    return round(shares * slipped_entry, 2)


def _write_decision_log(
    session, trade: "PaperTrade", action: str, price: float, shares: float,
    reason: str | None, details: dict,
) -> None:
    """IF-12: append-only decision-audit row — a genuine INSERT, never an UPDATE to a prior
    row, unlike paper_trades itself (mutated in place throughout a trade's lifecycle). Called
    once at entry and once at exit; never elsewhere. Fails open (log + swallow) on any error —
    a logging failure must never abort a real trade entry/exit."""
    try:
        session.add(PaperTradeDecisionLog(
            trade_id=trade.id,
            portfolio_id=trade.portfolio_id,
            symbol=trade.symbol,
            action=action,
            price=price,
            shares=shares,
            reason=reason,
            details_json=json.dumps(details, default=str),
        ))
    except Exception as exc:
        log.warning("paper.decision_log_write_failed", symbol=trade.symbol, action=action, error=str(exc))


def _open_paper_trade(
    session, portfolio: PaperPortfolio, stock: Stock, sig: Signal, ranking: "Ranking | None",
    live_price: float, game_plan: dict, score: int, notes: list[str], gate_source: str,
    cfg: dict, style: str, equity: float, regime_size_mult: float, live_regime: dict | None,
    live_prices: dict[str, float], prefetched_open: list, atr: float | None,
) -> tuple["PaperTrade | None", str | None]:
    """T286-CONDITIONAL-ORDER: position-sizing + PaperTrade-opening logic, extracted VERBATIM
    (same variable names, same order of checks, same audit-comment history) out of
    _scan_for_entries()'s own candidate loop so a conditional order's "buy" action can open a
    real position through the EXACT same sizing/cap logic as an organic entry, rather than a
    second, independently-maintained (and inevitably drifting) copy of ~250 lines of real
    risk-sizing math.

    _scan_for_entries() is the ONLY other caller — its own loop body was replaced with a call
    to this function, with its `continue` statements becoming this function's `return None,
    reason` (same skip semantics, now returned to the caller instead of implicit loop-continue).

    Returns (trade_or_None, skip_reason_or_None). `trade_or_None` is None whenever a skip
    condition fires below (mirrors the original inline `continue` points exactly) — the caller
    is responsible for updating its own cycle-level state (entries_made, open_symbols, equity,
    _skip_tally) based on which of the two return values it gets, exactly as it did before this
    extraction when those updates were inline in the same loop body.
    """
    # Position sizing: risk_dollar / stop_distance = shares
    stop        = game_plan["stop"]
    take_profit = game_plan["take_profit"]
    stop_distance = live_price - stop
    if stop_distance <= 0:
        return None, "invalid_stop_distance"
    rr = (take_profit - live_price) / max(stop_distance, live_price * 0.005)

    # PT-B10: Earnings-graduated sizing — reduce size as earnings approach
    dte = (sig.reasons or {}).get("days_to_earnings")
    earnings_size_mult = 1.0
    if dte is not None:
        dte_int = int(dte)
        if 6 <= dte_int <= 10:
            earnings_size_mult = 0.50   # 50% size within 10 days of earnings
            notes = notes + [f"Size reduced 50% — earnings in {dte_int}d"]
        elif 11 <= dte_int <= 20:
            earnings_size_mult = 0.75   # 75% size within 11-20 days
            notes = notes + [f"Size reduced 75% — earnings in {dte_int}d"]

    # PT-D2: Confidence-band sizing — scale position proportional to signal conviction
    sig_conf = float(sig.confidence or 0.0)
    if sig_conf >= 50:
        confidence_size_mult = 1.25
        notes = notes + [f"Size 1.25× (confidence {sig_conf:.0f}% — high conviction)"]
    elif sig_conf >= 30:
        confidence_size_mult = 1.0
    else:
        confidence_size_mult = 0.75
        notes = notes + [f"Size 0.75× (confidence {sig_conf:.0f}% — marginal signal)"]

    # INT-3: Research-gated position sizing — reduce size when research disagrees
    _research_rec = ""  # captured outside try for hard gate below
    research_size_mult = 1.0
    if cfg.get("research_gating_enabled", True):
        try:
            import httpx as _httpx
            from common.config import get_settings as _gs
            _res = _httpx.get(
                f"{_gs().research_engine_url}/research/{stock.symbol}/summary",
                timeout=1.5,
                headers={"Authorization": f"Bearer {_svc_token()}"},
            )
            if _res.status_code == 200:
                _rs = _res.json()
                _research_rec = _rs.get("recommendation", "")
                _score = float(_rs.get("overall_score") or 0)
                if _research_rec == "STRONG BUY" and _score >= 75:
                    research_size_mult = 1.2
                    notes = notes + [f"Size 1.2× (Research: {_research_rec} {_score:.0f})"]
                elif _research_rec == "BUY" and _score >= 65:
                    research_size_mult = 1.0
                elif _research_rec == "WATCH" and _score >= 60:
                    research_size_mult = 0.8
                    notes = notes + [f"Size 0.8× (Research: {_research_rec} {_score:.0f})"]
                elif _research_rec in ("WATCH", "AVOID", "SELL"):
                    research_size_mult = 0.6
                    notes = notes + [f"Size 0.6× (Research: {_research_rec} {_score:.0f})"]
        except Exception:
            pass  # no research data → neutral 1.0×

    # Hard gate: AVOID/SELL research blocks entry entirely — mirrors DE hard_rejects logic
    if cfg.get("research_gating_enabled", True) and _research_rec in ("AVOID", "SELL"):
        log.info("paper.skip_research_gate", symbol=stock.symbol, research_rec=_research_rec)
        return None, "research_gate"

    # 40-B: Cross-horizon consensus boost — when ≥2 other styles also fired BUY
    # for this stock in the same signal batch, we have rare multi-timeframe alignment.
    consensus_size_mult = 1.0
    cross_buys = int((sig.reasons or {}).get("cross_style_buys", 0))
    if cross_buys >= 2:
        consensus_size_mult = 1.15
        notes = notes + [f"Size 1.15× (multi-timeframe consensus: {cross_buys} other styles BUY)"]
    elif cross_buys == 1:
        consensus_size_mult = 1.07
        notes = notes + [f"Size 1.07× (partial consensus: 1 other style BUY)"]

    # T188: Score-to-size multiplier — high-conviction scores get more capital, marginal scores less.
    # Score just at min threshold (excess=0): 0.75×. Score +2 above (normal): 1.0×. Score +4+: 1.25×.
    # AUD262-FALLBACK-SIZES-LARGER-THAN-DE: this used to only apply when gate_source=="de"
    # (score_size_mult pinned to 1.0 on fallback/legacy) — but `score` on the fallback path
    # is _should_enter()'s own returned score, compared against the SAME min_entry_score
    # threshold DE uses (confirmed: _record_de_shadow_comparison already passes this exact
    # same cfg value for both paths), so the input needed was already available on every
    # path — nothing about this multiplier is actually DE-specific. Pinning it to 1.0 meant
    # a marginal candidate (exactly at min_entry_score) got FULL size on the fallback path
    # (1.0x) but REDUCED size under DE (0.75x) — 33% MORE capital on the weakest-conviction
    # trades specifically during a decision-engine outage, when caution matters most.
    _min_score_cfg = cfg.get("min_entry_score", 4)
    _score_excess = score - _min_score_cfg
    score_size_mult = round(max(0.75, min(1.25, 0.75 + _score_excess * 0.125)), 3)
    if score_size_mult != 1.0:
        notes = notes + [f"Size {score_size_mult:.2f}× ({gate_source.upper()} score {score}, excess {_score_excess:+d} from min {_min_score_cfg})"]
    _risk_base     = equity * cfg["risk_per_trade_pct"]
    risk_dollar    = _risk_base * earnings_size_mult * regime_size_mult * confidence_size_mult * research_size_mult * consensus_size_mult * score_size_mult
    # T234-PT-SIZING-MULT-STACK: the 6 categories above are independent per-trade signals
    # (each already min()-composed internally where it overlaps with another, e.g.
    # regime_size_mult folds in VIX/breadth/HMM via min() rather than multiplying them) —
    # multiplying independent judgments together is intentional, but with no combined floor
    # the worst realistic stack sizes a trade down to a token position, where commission/
    # slippage drag can exceed the position's own expected profit. Floor the composed result
    # at 25% of the unadjusted base so a trade that clears every other gate is never sized
    # down below that — multipliers above this floor are unaffected.
    #
    # AUD232-011 (corrected by AUD262-FALLBACK-SIZES-LARGER-THAN-DE): score_size_mult used
    # to only derive from _score_excess when gate_source=="de" — pinned to 1.0 on
    # fallback/legacy. That meant the fallback path's real floor-triggering minimum was
    # 0.1125 (11.25%) vs. DE's 0.084 (8.4%) — a LOOSER effective floor, and correspondingly
    # MORE capital on marginal-conviction trades, specifically during a Decision Engine
    # outage, when extra caution matters most. score_size_mult is now derived identically
    # on every gate_source, so the worst-case figure is the same regardless of path:
    #   earnings 0.50 x regime 0.50 x confidence 0.75 x research 0.6 x score 0.75 = 0.084 (8.4%)
    risk_dollar    = max(risk_dollar, _risk_base * 0.25)
    shares         = risk_dollar / stop_distance

    # PA-C1: Max dollar loss per trade — prevents wide ATR stops from risking > 2% equity
    max_loss_pct = cfg.get("max_loss_per_trade_pct", 0.02)
    if max_loss_pct and equity > 0:
        max_loss_dollar = equity * max_loss_pct
        if stop_distance * shares > max_loss_dollar:
            shares = max_loss_dollar / stop_distance
            notes = notes + [f"Shares capped to max loss ${max_loss_dollar:.0f} ({max_loss_pct*100:.0f}% equity)"]

    # PT-C2: round shares first, then compute position_value from rounded shares
    # so entry cash delta matches exit cash delta exactly
    shares         = round(shares, 4)
    position_value = round(shares * live_price, 2)

    # AUD262-HK-NO-BOARD-LOTS: HKEX has no fractional/arbitrary-quantity orders — round
    # down to a whole number of board lots (see _hk_board_lot_size()'s own docstring for
    # why this is a documented approximation, not this symbol's real, confirmed lot size).
    # Rounding DOWN (never up) means this can only ever reduce risk relative to the
    # risk-budget math above, never exceed it. A candidate whose risk budget doesn't cover
    # even one whole lot rounds to shares=0, which the FIN-07 check just below already
    # skips via its existing `shares < 0.01` branch — no new skip logic needed.
    if cfg.get("market") == "HK":
        _lot = _hk_board_lot_size(live_price)
        shares         = float((int(shares) // _lot) * _lot)
        position_value = round(shares * live_price, 2)

    # FIN-07: skip near-zero share positions that would pollute the journal.
    # Also serves as an implicit ATR-volatility filter: extreme ATR → wide stop →
    # tiny shares (via max_loss_per_trade_pct cap) → position_value < min_position_value → skip.
    min_pos_val = cfg.get("min_position_value", 200.0)
    if shares < 0.01 or position_value <= 0 or position_value < min_pos_val:
        atr_pct = round(atr / live_price * 100, 1) if (atr and live_price > 0) else None
        log.info("paper.skip_min_position", symbol=stock.symbol,
                 shares=shares, position_value=position_value, min_required=min_pos_val,
                 atr_pct=atr_pct, stop_dist=round(stop_distance, 2))
        return None, "min_position"

    # Cap position at max_position_pct of equity
    max_pos = equity * cfg["max_position_pct"] * earnings_size_mult
    if position_value > max_pos:
        shares         = round(max_pos / live_price, 4)
        # AUD262-HK-NO-BOARD-LOTS: this branch recomputes a fresh, fractional `shares`
        # from max_pos/live_price — the lot-rounding applied right after PT-C2 above must
        # be re-applied here too, or a HK trade that hits the max-position cap would exit
        # this block with a fractional share count again.
        if cfg.get("market") == "HK":
            shares = float((int(shares) // _hk_board_lot_size(live_price)) * _hk_board_lot_size(live_price))
        position_value = round(shares * live_price, 2)

    # PT-B5: Aggregate open-risk check — sum (price - stop) * shares for all open trades
    # AUD19-PERF2: uses prefetched_open (pre-fetched before the loop) — no DB query.
    max_open_risk = cfg.get("max_open_risk_pct", 0.12)
    if max_open_risk and equity > 0:
        open_risk = sum(
            abs(live_prices.get(t.symbol, t.entry_price) - t.current_stop) * t.shares
            for t, _ in prefetched_open
        )
        new_trade_risk = stop_distance * shares
        if (open_risk + new_trade_risk) / equity > max_open_risk:
            log.info("paper.skip_open_risk_cap", symbol=stock.symbol,
                     open_risk_pct=round((open_risk + new_trade_risk) / equity * 100, 1),
                     limit_pct=round(max_open_risk * 100, 1))
            return None, "open_risk_cap"

    # Sector concentration check — AUD19-PERF2: computed in Python from pre-fetched open trades.
    _sector = stock.sector  # may be None (unclassified stocks count against a shared bucket)
    sector_value = sum(
        _best_price(t, live_prices) * t.shares
        for t, st in prefetched_open
        if (st.sector is None) == (_sector is None) and (st.sector == _sector or _sector is None and st.sector is None)
    )
    if (sector_value + position_value) / max(equity, 1) > cfg["max_sector_pct"]:
        log.info("paper.skip_sector_cap", symbol=stock.symbol,
                 sector=_sector or "unclassified",
                 sector_pct=round((sector_value + position_value) / equity * 100, 1))
        return None, "sector_cap"
    max_sector_pos = int(cfg.get("max_sector_positions", 3))
    sector_count = sum(
        1 for _, st in prefetched_open
        if (st.sector is None) == (_sector is None) and (st.sector == _sector or _sector is None and st.sector is None)
    )
    if sector_count >= max_sector_pos:
        log.info("paper.skip_sector_count_cap", symbol=stock.symbol,
                 sector=_sector or "unclassified", limit=max_sector_pos)
        return None, "sector_count_cap"

    # PT-B6: Apply entry slippage — simulates spread / market impact
    # IF-06: scale the flat base slippage with position size relative to this symbol's own
    # liquidity, instead of applying the same constant to every trade regardless of size.
    _base_slippage = cfg.get("entry_slippage_pct", 0.001)
    slippage = (
        _size_aware_slippage_pct(shares, _avg_daily_volume_for(stock.symbol), _base_slippage)
        if cfg.get("size_aware_slippage_enabled", True) else _base_slippage
    )
    commission = round(cfg.get("commission_per_share", 0.0) * shares, 4)

    # Cash gate and the actual deduction below both use this same slipped value now
    # (see _slipped_position_value's docstring — T247-MARKETDATA-CASHGATE-PRESLIPPAGE).
    position_value = _slipped_position_value(shares, live_price, slippage)
    if position_value > portfolio.current_cash * 0.98:
        log.info("paper.skip_insufficient_cash",
                 symbol=stock.symbol, need=position_value,
                 have=portfolio.current_cash)
        return None, "insufficient_cash"

    slipped_entry = round(live_price * (1 + slippage), 4)
    # Deduct cash at slipped price (not live_price) so cash and cost basis are consistent
    portfolio.current_cash = max(0.0, round(portfolio.current_cash - position_value - commission, 2))
    now = datetime.now(timezone.utc)
    trade = PaperTrade(
        portfolio_id          = portfolio.id,
        symbol                = stock.symbol,
        signal_id             = sig.id,
        trading_style         = style,
        entry_date            = date.today(),
        entry_time            = now,
        entry_price           = slipped_entry,   # slippage-adjusted entry
        sector                = stock.sector,    # H-SECTOR FIX: PA-D1 monitor reads trade.sector
        stock_id              = stock.id,        # PT-H2: needed for double-top mid-trade detection
        shares                = shares,
        entry_shares          = shares,          # T232-PT6: snapshot before scale-outs shrink `shares`
        entry_commission      = commission,      # AUD262: stored so exit-time pnl can reconcile to it
        stop_loss             = stop,
        take_profit           = take_profit,
        current_stop          = stop,
        highest_price         = slipped_entry,
        current_price         = slipped_entry,
        entry_score           = score,
        entry_decision_notes  = notes,
        confidence_at_entry   = sig.confidence,
        kscore_at_entry       = ranking.score if ranking else None,
        rr_ratio_at_entry     = round(rr, 2),
        market_regime_at_entry= (live_regime or {}).get("state") or (sig.reasons or {}).get("market_regime"),
        entry_reasons         = sig.reasons,
        stage                 = "open",
        hold_days             = 0,
    )
    session.add(trade)
    session.flush()  # IF-12: assign trade.id before the decision-log FK reference below
    _write_decision_log(
        session, trade, "entry", slipped_entry, shares, "; ".join(notes) if notes else None,
        {"entry_score": score, "rr_ratio": round(rr, 2), "gate_source": gate_source,
         "confidence": sig.confidence, "kscore": ranking.score if ranking else None,
         "market_regime": trade.market_regime_at_entry},
    )
    # Broker routing: submit real BUY order to linked broker (US only; falls back on error)
    if portfolio.broker_connection_id:
        _place_broker_entry(session, trade, portfolio)

    log.info("paper.entry",
             symbol=stock.symbol, price=live_price,
             shares=round(shares, 2), stop=stop,
             target=take_profit, score=score, rr=round(rr, 2),
             cash_remaining=round(portfolio.current_cash, 2))
    return trade, None


def _squeeze_score_for(symbol: str, momentum_score: float | None) -> float | None:
    """MPE-05: cheap, fail-open squeeze-score lookup for a real candidate at the moment
    _scan_for_entries() considers it — reads the SAME already-cached fundamentals blob
    routes.py's own short_squeeze() endpoint reads (stockai:fundamentals:v2:{symbol}), never a
    fresh yfinance fetch inside this hot entry-scan loop. change_pct is deliberately omitted
    here (None) — the richer per-symbol {price, change_pct} cache (stockai:live_prices) isn't
    otherwise read by this function today, and change_pct is only 10 of squeeze_score's 100
    points; compute_short_squeeze_score() already degrades this component to 0 gracefully when
    the input is absent, matching every other optional-input degradation in that function.

    Lazily imports from routes.py (a sibling module that itself already imports FROM this file
    lazily inside function bodies — e.g. get_last_regime, resolve_entry_gate_params) rather
    than at module level, to avoid a circular import.
    """
    try:
        from ..api.routes import compute_short_squeeze_score, _get_redis
        r = _get_redis()
        cached = r.get(f"stockai:fundamentals:v2:{symbol}")
        if not cached:
            return None
        data = json.loads(cached)
        spf = data.get("short_percent_of_float")
        if spf is None:
            return None
        result = compute_short_squeeze_score(
            short_percent_of_float=round(spf * 100, 2),
            days_to_cover=data.get("short_ratio"),
            momentum_score=momentum_score,
            change_pct=None,
        )
        return result["score"] if result else None
    except Exception:
        return None


def _scan_for_entries(session, portfolio: PaperPortfolio, live_prices: dict[str, float], live_regime: dict | None = None) -> None:
    """Find fresh BUY signals and evaluate them for entry."""
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(portfolio.config.get("trading_style", "GROWTH"), {}), **portfolio.config}
    # T264-ENTRYGATESOVERRIDE: computed once, reused at every "market condition / recent
    # performance" gate below (drawdown, daily_loss, weekly_loss, weekly_gain_lock,
    # consecutive_losses, regime_bear, regime_suspension) — never at max_positions/equity-floor/
    # live-price-sparsity, which stay hard regardless of this flag.
    _gates_override = _entry_gates_override_active(cfg)
    # Apply HK-specific circuit breaker overrides when not explicitly set in the portfolio config.
    if cfg.get("market") == "HK":
        for _k, _v in _HK_MARKET_OVERRIDES.items():
            if _k not in (portfolio.config or {}):
                cfg[_k] = _v
    style   = cfg["trading_style"]
    now     = datetime.now(timezone.utc)
    # CB-3 FIX + CB-W1 FIX: signals use dedup-on-change persistence — a persistent BUY never
    # writes a new row while unchanged. 26h was fine Mon–Thu but broke on Mondays: Friday's
    # valid signals are 72h+ old and excluded entirely. 5 days (120h) covers any weekend or
    # long-weekend gap. The signal engine's own 3-day price-staleness guard handles truly stale data.
    cutoff  = now - timedelta(days=5)

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

    # T201: Equity floor circuit breaker — suspend all entries if account has dropped too far
    # below initial capital. A human stops trading a badly damaged account and steps back.
    _equity_floor_pct = float(cfg.get("equity_floor_pct", 0.80))
    if _equity_floor_pct > 0 and portfolio.initial_capital > 0:
        _floor_ratio = equity / portfolio.initial_capital
        if _floor_ratio < _equity_floor_pct:
            log.warning("paper.equity_floor_triggered",
                        portfolio=portfolio.name,
                        equity=round(equity, 0),
                        initial_capital=portfolio.initial_capital,
                        equity_pct=round(_floor_ratio * 100, 1),
                        floor_pct=round(_equity_floor_pct * 100, 1),
                        note="account equity below floor — all new entries suspended")
            return

    # Symbols already in open positions — checked across ALL portfolios to prevent cross-portfolio
    # race condition (e.g. 2382.HK entered 3× when SWING + GROWTH both scanned simultaneously).
    open_symbols: set[str] = set(
        r[0] for r in session.execute(
            select(Stock.symbol)
            .join(PaperTrade, PaperTrade.symbol == Stock.symbol)
            .where(PaperTrade.stage == "open")
        ).all()
    )

    # IF-12: bulk-fetch the user-maintained no-trade list ONCE per scan cycle — never a
    # per-candidate query — so a small, deliberately-avoided symbol list can never itself
    # become a per-symbol N+1 cost inside the candidate loop below.
    _restricted_symbols: set[str] = set(
        r[0] for r in session.execute(select(RestrictedSymbol.symbol)).all()
    )

    # PA-E1: live_prices health check — skip entries if price data is too sparse (yfinance outage)
    expected_prices = len(open_symbols)  # at minimum, we need prices for all open positions
    if expected_prices > 0 and len(live_prices) < expected_prices * 0.5:
        log.error("paper.entry_scan_skip",
                  reason="live_prices_too_sparse",
                  got=len(live_prices), expected_min=expected_prices,
                  note="possible yfinance outage — skipping entries to avoid stale equity")
        return

    # Get GROWTH watchlist stock IDs — abort if watchlist is empty (safety guard)
    growth_stock_ids: set[int] = set(
        r[0] for r in session.execute(
            select(WatchlistItem.stock_id)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.trading_style == style)
        ).all()
    )
    if not growth_stock_ids:
        log.warning("paper.entry_scan_skip", reason="growth_watchlist_empty",
                    style=style, note="Add stocks to the GROWTH watchlist to enable entries")
        return

    # CB-W1 FIX: use MOST RECENT signal per stock for this style. Without this, a stock
    # that went BUY→SELL within the 5-day window would still appear in the candidates list
    # (the old BUY row satisfies Signal.signal=="BUY" even though a newer SELL row exists).
    latest_signal_ts_subq = (
        select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
        .where(Signal.horizon == style)
        .group_by(Signal.stock_id)
        .subquery()
    )

    buy_signals = session.execute(
        select(Signal, Stock, Ranking)
        .join(Stock, Signal.stock_id == Stock.id)
        .join(
            latest_signal_ts_subq,
            (Signal.stock_id == latest_signal_ts_subq.c.stock_id) &
            (Signal.ts == latest_signal_ts_subq.c.max_ts),
        )
        .outerjoin(
            Ranking,
            (Ranking.stock_id == Stock.id) &
            (Ranking.as_of == (
                select(func.max(Ranking.as_of))
                .where(Ranking.stock_id == Stock.id)
                .correlate(Stock)
                .scalar_subquery()
            )),
        )
        .where(
            Signal.signal == "BUY",          # only if LATEST signal is BUY
            Signal.horizon == style,
            Signal.confidence >= cfg["min_confidence"] * 0.90,
            Signal.ts >= cutoff,             # within 5-day window
            Stock.active.is_(True),
            # BUG-DELISTED-GENERATION-BLIND: this is _scan_for_entries()'s own real BUY-
            # candidate query — a confirmed-delisted stock must never be entered as a new
            # paper trade, the highest-stakes site this bug class could exist at.
            Stock.delisted.is_(False),
            Stock.market == cfg.get("market", "US"),
        )
        .order_by(desc(Signal.confidence))
    ).all()

    # ── Drawdown circuit breaker ─────────────────────────────────────────────────
    max_dd_cfg = cfg.get("max_portfolio_drawdown_pct", 0.20)
    if max_dd_cfg and max_dd_cfg > 0 and not _gates_override:
        current_dd = _compute_portfolio_drawdown(session, portfolio.id, equity)
        if current_dd is not None and current_dd > max_dd_cfg:
            log.warning("paper.drawdown_circuit_breaker",
                        portfolio=portfolio.name,
                        current_dd_pct=round(current_dd * 100, 1),
                        limit_pct=round(max_dd_cfg * 100, 1),
                        note="new entries suspended until equity recovers")
            _write_gate_block(portfolio.id, "drawdown",
                              f"Portfolio drawdown {current_dd*100:.1f}% exceeds {max_dd_cfg*100:.0f}% limit — no new entries until equity recovers")
            return

    # ── Daily realized-loss circuit breaker (net P&L — winners offset losers) ──────
    _daily_pnl_pct = 0.0  # captured for DE call below
    _recent_wr = _recent_win_rate(session, portfolio.id)      # T184: passed to DE for drawdown-aware floor
    _consec_losses = _consec_loss_streak(session, portfolio.id)  # T187: passed to DE for consec-loss gate
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
        # Decision-engine's daily_pnl_pct is documented (and consumed by hard_rejects.py) as a
        # FRACTION of equity (e.g. -0.015), not a percentage — this used to be `* 100`, sending
        # e.g. -0.10 (meant as "-0.10%") which hard_rejects.py compared directly against
        # max_daily_loss_pct (a fraction, default 0.04): -0.10 <= -0.04 is True, so the daily-loss
        # circuit breaker tripped after any realized loss >= ~0.04% of equity instead of 4%,
        # blocking all new entries via DE (the authoritative gate when decision_engine_mode="primary")
        # after essentially any trivial loss.
        _daily_pnl_pct = round(daily_net_pnl / equity, 4)
        if daily_net_pnl < 0 and abs(daily_net_pnl) / equity > max_daily_loss and not _gates_override:
            log.warning("paper.daily_loss_limit",
                        portfolio=portfolio.name,
                        daily_net_pnl=round(daily_net_pnl, 2),
                        daily_net_pnl_pct=round(daily_net_pnl / equity * 100, 1),
                        limit_pct=round(max_daily_loss * 100, 1),
                        note="new entries suspended for today")
            _write_gate_block(portfolio.id, "daily_loss",
                              f"Daily loss {abs(daily_net_pnl)/equity*100:.1f}% exceeds {max_daily_loss*100:.0f}% limit — no more entries today")
            return

    # ── Weekly realized P&L checks — loss limit + gain lock ─────────────────────
    # Compute weekly pnl once; used for both the loss circuit breaker and T191 gain lock.
    max_weekly_loss = cfg.get("max_weekly_loss_pct", 0.08)
    max_weekly_gain = cfg.get("max_weekly_gain_pct", 0.06)  # T191: 6% weekly gain → lock (was 1.5% — too tight, locked out profitable weeks)
    _needs_weekly = (
        (max_weekly_loss and max_weekly_loss > 0) or
        (max_weekly_gain and max_weekly_gain > 0)
    )
    # T232-DL-DUALSCORER-DEBT: weekly_net_pnl_pct threaded through to decision-engine so it can
    # reconstruct the SAME two portfolio-wide circuit breakers below (weekly loss limit, T191
    # gain lock) — DE previously had zero visibility into weekly realized P&L at all, so a bad
    # (or a locked-in-good) week could be traded through entry-by-entry on the live DE-primary
    # path as long as each candidate cleared DE's own per-candidate gates. None (not 0.0) when
    # not computed below, matching every other optional gate's conditional-inclusion convention
    # in _call_decision_engine() — a portfolio genuinely at 0% weekly P&L is a real, different
    # value from "never computed."
    _weekly_net_pnl_pct: float | None = None
    if _needs_weekly and equity > 0:
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
            log.warning("paper.weekly_loss_limit",
                        portfolio=portfolio.name,
                        weekly_net_pnl=round(weekly_net_pnl, 2),
                        weekly_net_pnl_pct=round(weekly_net_pnl / equity * 100, 1),
                        limit_pct=round(max_weekly_loss * 100, 1),
                        note="new entries suspended for remainder of week")
            _write_gate_block(portfolio.id, "weekly_loss",
                              f"Weekly loss {abs(weekly_net_pnl)/equity*100:.1f}% exceeds {max_weekly_loss*100:.0f}% limit — no entries until next week")
            return
        # T191: Weekly gain lock — don't give back a good week by overtrading.
        # Once weekly realized PnL crosses the gain lock threshold, no new entries until next week.
        if max_weekly_gain and weekly_net_pnl > 0 and weekly_net_pnl / equity > max_weekly_gain and not _gates_override:
            log.info("paper.weekly_gain_lock",
                     portfolio=portfolio.name,
                     weekly_pnl_pct=round(weekly_net_pnl / equity * 100, 1),
                     lock_pct=round(max_weekly_gain * 100, 1),
                     note="weekly gain target reached — protecting profits, no new entries")
            _write_gate_block(portfolio.id, "weekly_gain_lock",
                              f"Weekly gain lock — up {weekly_net_pnl/equity*100:.1f}% this week; protecting profits until Monday")
            return

    # ── Consecutive-loss circuit breaker ─────────────────────────────────────────
    # Uses precomputed _consec_losses (avoids a second DB query here).
    max_consec_losses = cfg.get("max_consecutive_losses", 3)
    if max_consec_losses and max_consec_losses > 0 and _consec_losses >= max_consec_losses and not _gates_override:
        if open_count > 0:
            # Open trades exist — wait for one to close positive before entering again.
            log.warning("paper.consecutive_loss_limit",
                        portfolio=portfolio.name,
                        consecutive_losses=_consec_losses,
                        note="new entries suspended until a trade closes positive")
            _write_gate_block(portfolio.id, "consecutive_losses",
                              f"{_consec_losses} consecutive losses — no new entries until a winning trade")
            return
        else:
            # Deadlock: no open trades and consecutive loss limit hit — there is no trade
            # that can close positive to reset the counter. Allow one recovery entry and
            # zero out consec_losses for the DE call so hard_rejects doesn't also block.
            log.warning("paper.consecutive_loss_restart",
                        portfolio=portfolio.name,
                        consecutive_losses=_consec_losses,
                        note="no open trades — allowing one recovery entry to break deadlock")
            _consec_losses = 0
            _clear_gate_block(portfolio.id)  # remove stale Redis gate so UI clears

    # ── Max entries per day ───────────────────────────────────────────────────────
    max_entries_day = cfg.get("max_entries_per_day", 3)
    if max_entries_day and max_entries_day > 0:
        today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())
        entries_today = session.execute(
            select(func.count()).select_from(PaperTrade)
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.entry_time >= today_start,
            )
        ).scalar() or 0
        if entries_today >= max_entries_day:
            log.info("paper.daily_entry_cap", entries_today=entries_today, limit=max_entries_day)
            _write_gate_block(portfolio.id, "daily_entry_cap",
                              f"Daily entry cap reached ({entries_today}/{max_entries_day}) — no more entries today")
            return

    # ── Per-symbol post-stop cooldown ─────────────────────────────────────────────
    # T221-D: After a stop_hit, don't re-enter for 5 days (120h). A stopped-out stock
    # is in a downtrend — re-entering the next day means catching a falling knife.
    stop_cooldown_hours = cfg.get("stop_cooldown_hours", 120)
    _recently_stopped: set[str] = set()
    if stop_cooldown_hours > 0:
        _stop_cutoff = datetime.now(timezone.utc) - timedelta(hours=stop_cooldown_hours)
        _recently_stopped = set(session.execute(
            select(PaperTrade.symbol)
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.stage == "closed",
                PaperTrade.exit_reason == "stop_hit",
                PaperTrade.exit_time >= _stop_cutoff,
            )
        ).scalars().all())
        if _recently_stopped:
            log.info("paper.stop_cooldown_active",
                     symbols=sorted(_recently_stopped),
                     cooldown_hours=stop_cooldown_hours)

    # T197: Break-even stop cooldown — shorter than real-loss cooldown (2h default vs 24h).
    # A break-even exit is less severe than a loss; allow re-entry sooner if setup recovers.
    be_cooldown_hours = cfg.get("breakeven_cooldown_hours", 2)
    if be_cooldown_hours > 0:
        _be_cutoff = datetime.now(timezone.utc) - timedelta(hours=be_cooldown_hours)
        _be_stopped = set(session.execute(
            select(PaperTrade.symbol)
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.stage == "closed",
                PaperTrade.exit_reason == "breakeven_stop",
                PaperTrade.exit_time >= _be_cutoff,
            )
        ).scalars().all())
        if _be_stopped:
            _recently_stopped |= _be_stopped
            log.info("paper.breakeven_cooldown_active",
                     symbols=sorted(_be_stopped),
                     cooldown_hours=be_cooldown_hours)

    # ── Regime filter ─────────────────────────────────────────────────────────────
    regime_state = (live_regime or {}).get("state", "neutral")
    regime_size_mult = 1.0
    if cfg.get("enable_regime_filter", True) and live_regime:
        # HK has no VIX equivalent (US-only index) — live_regime["vix"] is always None for
        # HK portfolios. Build the gate message from whichever index this market actually
        # uses (SPY+VIX for US, HSI for HK) instead of a US-shaped template with "VIX N/A"
        # bolted on. HK's own regime notes already state the real condition (e.g. "HSI
        # -11.0% below SMA200 + below SMA50"), so lean on those rather than reconstructing it.
        _is_hk = cfg.get("market") == "HK"
        if _is_hk:
            _idx_note = (live_regime.get("notes") or ["HSI trend deteriorating"])[0]
        else:
            _vix_val = live_regime.get("vix")
            _vix_str = f"{_vix_val:.1f}" if _vix_val is not None else "N/A"
        if regime_state == "bear" and not _gates_override:
            log.info("paper.regime_gate_bear",
                     portfolio=portfolio.name,
                     vix=live_regime.get("vix"),
                     spy=live_regime.get("spy_price"),
                     notes=live_regime.get("notes"),
                     note="all new entries suspended in bear regime")
            _bear_msg = (f"Bear market — {_idx_note}; all new entries suspended" if _is_hk else
                         f"Bear market — SPY below 200EMA + VIX {_vix_str}; all new entries suspended")
            _write_gate_block(portfolio.id, "regime_bear", _bear_msg)
            return
        # T173/T226-A: risk_off gate — blocks all new entries when regime_risk_off_gate=True.
        # T226-A changed default to True: 9/30 closed paper trades in risk_off had 0% win rate.
        # T232-HKOVERRIDE: a deliberate, time-boxed override (set via POST
        # /paper-portfolio/risk-off-override?hours=N) can temporarily disable this gate —
        # self-expiring, checked here on every evaluation rather than needing a cron job
        # to turn it back off. T264-ENTRYGATESOVERRIDE's general override (entry-gates-override)
        # ALSO clears this gate, in addition to the risk-off-specific one above — either
        # mechanism is sufficient on its own.
        if (regime_state == "risk_off" and cfg.get("regime_risk_off_gate", True)
                and not _regime_risk_off_override_active(cfg) and not _gates_override):
            log.info("paper.regime_gate_risk_off",
                     portfolio=portfolio.name,
                     vix=live_regime.get("vix"),
                     spy=live_regime.get("spy_price"),
                     note="all new entries suspended in risk_off regime (strict gate enabled)")
            _risk_off_msg = (f"Risk-off regime — {_idx_note}; no new entries until regime improves" if _is_hk else
                              f"Risk-off regime — SPY below 50EMA + VIX {_vix_str}; no new entries until regime improves")
            _write_gate_block(portfolio.id, "regime_risk_off", _risk_off_msg)
            return

        # T210: Regime suspension circuit breaker — if the market has been risk_off or bear
        # for N consecutive calendar days, suspend all new entries regardless of gate setting.
        # Stores one snapshot per day in Redis; checks the last N unique days.
        _regime_suspend_days = int(cfg.get("regime_suspension_days", 3))
        if _regime_suspend_days > 0:
            try:
                from common.redis_client import get_redis as _get_pool_redis
                _t210_redis = _get_pool_redis()
                _today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                _regime_key = f"paper:regime_daily:{portfolio.id}"
                # Store today's state (date-keyed; only writes once per day via NX)
                _t210_redis.hset(_regime_key, _today_str, regime_state)
                _t210_redis.expire(_regime_key, 60 * 60 * 24 * 10)  # keep 10 days of history
                # Collect last N distinct dates, sorted descending
                _all_days = sorted(_t210_redis.hgetall(_regime_key).items(), reverse=True)
                _bad_states = {"risk_off", "bear"}
                _recent_bad = [s for _, s in _all_days[:_regime_suspend_days] if s in _bad_states]
                if len(_recent_bad) >= _regime_suspend_days and not _gates_override:
                    log.warning("paper.regime_suspension_triggered",
                                portfolio=portfolio.name,
                                days=_regime_suspend_days,
                                recent_states=[s for _, s in _all_days[:_regime_suspend_days]],
                                note="market in sustained stress — all entries suspended until regime improves")
                    _write_gate_block(portfolio.id, "regime_suspension",
                                      f"Market in sustained stress for {_regime_suspend_days}+ days — entries suspended until regime improves")
                    return
            except Exception:
                pass  # Redis unavailable — fail-open; normal gates still apply

        # AUD232-REGIME-BEAR-FALLBACK-FULLSIZE: this dict previously had no "bear" key, so
        # .get(regime_state, 1.0) would have silently defaulted to FULL size (1.0) for a bear
        # regime — the opposite of decision-engine's sizer.py, which explicitly zeroes bear
        # sizing. Currently unreachable in practice (regime_state == "bear" hard-returns above
        # at the regime_bear gate before this line ever runs), but added explicitly as
        # defense-in-depth so a future reordering of the gates above can't silently resurrect
        # full-size bear-regime entries via this fallback.
        regime_size_mult = {
            "bull":     cfg.get("regime_bull_size_mult", 1.0),
            "neutral":  1.0,
            "choppy":   cfg.get("regime_choppy_size_mult", 0.75),
            "risk_off": cfg.get("regime_risk_off_size_mult", 0.50),
            "bear":     cfg.get("regime_bear_size_mult", 0.0),
        }.get(regime_state, 1.0)
        # Tighten entry score threshold for risk-off environments
        if regime_state == "risk_off":
            cfg["min_entry_score"] = max(
                cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]), cfg.get("regime_risk_off_min_score", 5)
            )
        elif regime_state == "choppy":
            cfg["min_entry_score"] = max(
                cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]), cfg.get("regime_choppy_min_score", 4)
            )
        if regime_state not in ("bull", "neutral"):
            log.info("paper.regime_applied",
                     state=regime_state, size_mult=regime_size_mult,
                     min_score=cfg.get("min_entry_score"), vix=live_regime.get("vix"))

        # RE-9: Pre-emptive early warning — apply stricter SIZING before the regime flips.
        # AUD-PREREGIME-DOUBLEPENALTY: this used to ALSO raise min_entry_score here, while
        # _should_enter() (below) independently subtracts -1/-2 from the score for the exact
        # same is_pre_choppy/is_pre_risk_off flags — a candidate got hit twice for one signal
        # (raised floor AND lowered score), a 2-point swing at the boundary with zero backtest
        # coverage (gate_harness.py replays with live_regime=None, so this interaction was
        # never validated). decision-engine's own min_score_for_regime() takes only
        # regime_state, never these pre-regime flags — DE applies the pre-regime effect
        # exactly once, via the score layer. Matching that: sizing still tightens
        # preemptively here (a real, independent effect), but the threshold raise is removed
        # so the score-layer subtraction is the ONLY pre-regime effect, same as DE.
        if live_regime.get("is_pre_choppy"):
            regime_size_mult = min(regime_size_mult, cfg.get("regime_choppy_size_mult", 0.75))
            log.warning("paper.pre_choppy_warning", vix_5d_trend=live_regime.get("vix_5d_trend"),
                        spy_pct_above_ema20=live_regime.get("spy_pct_above_ema20"),
                        note="applying choppy sizing preemptively — regime deteriorating")
        elif live_regime.get("is_pre_risk_off"):
            regime_size_mult = min(regime_size_mult, cfg.get("regime_risk_off_size_mult", 0.50))
            log.warning("paper.pre_risk_off_warning", vix=live_regime.get("vix"),
                        note="applying risk_off sizing preemptively — VIX elevated near 50EMA")

        # PT-M5: Breadth-adjusted position sizing — applied on top of regime sizing.
        # Narrow markets (IWM/MDY below 200EMA) warrant smaller positions regardless of SPY regime.
        breadth_mult = live_regime.get("breadth_size_mult", 1.0)
        if breadth_mult < 1.0:
            prev_mult = regime_size_mult
            regime_size_mult = min(regime_size_mult, breadth_mult)
            log.warning("paper.breadth_weakness_size_reduced",
                        breadth_size_mult=breadth_mult,
                        prev_regime_mult=round(prev_mult, 2),
                        new_regime_mult=round(regime_size_mult, 2),
                        iwm_vs_ema200=live_regime.get("iwm_vs_ema200"),
                        mdy_vs_ema200=live_regime.get("mdy_vs_ema200"),
                        note="IWM/MDY breadth below 200EMA — reducing position size")

        # QW-8: HMM bear pressure — reduces sizing 30% when HMM model sees bear_prob > 0.50.
        # Complements rule-based regime: catches early-phase downturns via volatility clustering
        # before SMA/VIX thresholds trigger. Fail-open: if HMM unavailable, no effect.
        if live_regime.get("hmm_bear_pressure"):
            prev_mult = regime_size_mult
            regime_size_mult = min(regime_size_mult, 0.70)
            log.warning("paper.hmm_bear_pressure_size_reduced",
                        hmm_bear_prob=live_regime.get("hmm_bear_prob"),
                        hmm_state=live_regime.get("hmm_state"),
                        prev_mult=round(prev_mult, 2),
                        new_mult=round(regime_size_mult, 2),
                        note="HMM bear_prob > 0.50 — reducing position size to 70%")

    # T192 / HIGH-4: VIX-adjusted position sizing — continuous gradient replaces binary bands.
    # Matches decision-engine formula: max(0.5, 1 - max(0, (VIX - 20) / 30)).
    # VIX≤20 → 1.00×, VIX=25 → 0.83×, VIX=30 → 0.67×, VIX≥35 → 0.50×.
    if live_regime and cfg.get("vix_size_adjust_enabled", True):
        _vix = live_regime.get("vix")
        if _vix is not None:
            _vix_f = float(_vix)
            _vix_mult = round(max(0.5, 1.0 - max(0.0, (_vix_f - 20.0) / 30.0)), 3)
            if _vix_mult < regime_size_mult:
                regime_size_mult = _vix_mult
                log.info("paper.vix_size_reduced", vix=round(_vix_f, 1),
                         mult=_vix_mult, note="VIX gradient sizing applied")

    # IF-13: portfolio-level realized-volatility-targeting size adjustment — a genuinely
    # distinct signal from VIX (market-wide)/breadth/HMM above (which only ever dampen via
    # min(), never scale up): this portfolio's OWN realized return volatility can diverge from
    # what market-wide VIX suggests (e.g. a concentrated, correlated book realizing more
    # volatility than a calm VIX implies, or a diversified one realizing less). Applied as its
    # own multiply of regime_size_mult (not a further min()) specifically because it must be
    # able to move sizing UP when realized vol is comfortably below target, not just down.
    if cfg.get("vol_targeting_enabled", True):
        _vol_mult = _compute_portfolio_vol_targeting_mult(session, portfolio.id)
        if _vol_mult != 1.0:
            _prev_regime_mult = regime_size_mult
            regime_size_mult = round(regime_size_mult * _vol_mult, 3)
            log.info("paper.vol_targeting_size_adjusted", vol_mult=_vol_mult,
                     prev_regime_mult=round(_prev_regime_mult, 3),
                     new_regime_mult=regime_size_mult,
                     note="portfolio realized-vol-targeting multiplier applied")

    # T189: Regime-aware entry throttle — choppy/risk_off regimes cap new entries at 1/day.
    # Human traders become more selective in difficult markets and don't force setups.
    if cfg.get("regime_entry_throttle", True) and regime_state in ("choppy", "risk_off"):
        _te_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())
        _te_count = session.execute(
            select(func.count()).select_from(PaperTrade).where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.entry_time >= _te_start,
            )
        ).scalar() or 0
        if _te_count >= 1:
            log.info("paper.regime_entry_throttle",
                     portfolio=portfolio.name,
                     regime=regime_state,
                     entries_today=_te_count,
                     note="choppy/risk_off: max 1 new entry per day")
            _write_gate_block(portfolio.id, "entry_throttle",
                              f"Entry throttle — {regime_state} regime limits 1 entry/day; already entered today")
            return

    # T221-E: Portfolio heat brake — too many stops in recent window = adverse market conditions.
    # Entering more positions into a market that is stopping us out compounds losses.
    # T232-DL-DUALSCORER-DEBT: _recent_stops is hoisted to a properly-initialized None default
    # (not left block-scoped inside `if _heat_max > 0:`) so it survives, unconditionally, to the
    # per-candidate _call_decision_engine() call site below — matching the exact same hoisting
    # fix already applied to the index-trend gate's _idx_ret for the identical reason.
    _heat_max = cfg.get("heat_brake_max_stops", 3)
    _recent_stops: int | None = None
    if _heat_max > 0:
        _heat_h = cfg.get("heat_brake_window_hours", 48)
        _heat_cutoff = datetime.now(timezone.utc) - timedelta(hours=_heat_h)
        _recent_stops = session.execute(
            select(func.count()).select_from(PaperTrade)
            .where(
                PaperTrade.portfolio_id == portfolio.id,
                PaperTrade.stage == "closed",
                PaperTrade.exit_reason == "stop_hit",
                PaperTrade.exit_time >= _heat_cutoff,
            )
        ).scalar() or 0
        if _recent_stops >= _heat_max:
            log.warning("paper.heat_brake_triggered",
                        portfolio=portfolio.name,
                        recent_stops=_recent_stops,
                        window_hours=_heat_h,
                        note=f"{_recent_stops} stops hit in {_heat_h}h — adverse conditions, pausing entries")
            _write_gate_block(portfolio.id, "heat_brake",
                              f"Heat brake — {_recent_stops} stops hit in {_heat_h}h; entries paused until market conditions improve")
            return

    # T221-INDEX-TREND-GATE: Skip entries when today's market index is down >threshold%.
    # Regime filter handles sustained bear/risk_off conditions; this catches single-day macro
    # shocks (FOMC surprise, CPI print, HSI circuit-breaker open) where any long entry will
    # immediately fight the tide. Uses yfinance fast_info; fail-open on network errors.
    #
    # T232-DL-DUALSCORER-DEBT: _idx_ret is hoisted to survive past this block (unlike before,
    # where it only existed inside the threshold-tripped branch) specifically so it can be
    # threaded through to decision-engine below — this gate's decision logic is purely a
    # function of (market, index_return), no per-symbol/per-portfolio state at all, making it
    # genuinely portable, UNLIKE the last two ports (HK flow, low-volume) this value was never
    # already flowing to decision-engine anywhere (not in sig.reasons, not in /stocks/regime) —
    # a real write-side change, not a free port. Computed once per scan cycle here (this block
    # already ran before the fix), reused for every candidate in the loop below rather than
    # re-fetched per-candidate.
    _mkt = cfg.get("market", "US")  # defined here — reused in market cluster cap below
    _idx_ret: float | None = None
    if cfg.get("index_trend_gate_enabled", True):
        _idx_sym = "^HSI" if _mkt == "HK" else "SPY"
        _idx_threshold = float(cfg.get("index_trend_gate_pct", -0.015))
        try:
            import yfinance as yf
            _idx_fi = yf.Ticker(_idx_sym).fast_info
            _idx_prev = getattr(_idx_fi, "previous_close", None)
            _idx_last = getattr(_idx_fi, "last_price", None)
            if _idx_prev and _idx_last and float(_idx_prev) > 0:
                _idx_ret = (float(_idx_last) - float(_idx_prev)) / float(_idx_prev)
                if _idx_ret < _idx_threshold:
                    log.info("paper.index_trend_gate",
                             portfolio=portfolio.name, market=_mkt,
                             index=_idx_sym,
                             index_return_pct=round(_idx_ret * 100, 2),
                             threshold_pct=round(_idx_threshold * 100, 1),
                             note=f"index down {abs(_idx_ret)*100:.1f}% today — blocking new entries")
                    _write_gate_block(portfolio.id, "index_trend",
                                      f"{_idx_sym} down {abs(_idx_ret)*100:.1f}% today — no new entries on bad index days")
                    return
        except Exception:
            pass  # fail-open — yfinance unavailable doesn't block trading

    # PT-D6: Re-sort candidates by composite priority — confidence + K-Score + breakout context
    buy_signals = sorted(buy_signals, key=_composite_priority, reverse=True)

    # AUD19-PERF2: Pre-fetch all open trades + their stocks ONCE before the candidate loop.
    # Eliminates N+1 queries for open-risk, sector value, and sector count checks.
    _prefetched_open: list[tuple] = session.execute(
        select(PaperTrade, Stock)
        .join(Stock, PaperTrade.stock_id == Stock.id)
        .where(PaperTrade.portfolio_id == portfolio.id, PaperTrade.stage == "open")
    ).all()

    # T186: Pre-compute open sector counts once; passed to DE per candidate for sector gate.
    from collections import Counter as _Counter
    _open_sector_counts: dict[str, int] = dict(_Counter(
        (st.sector or "unclassified") for _, st in _prefetched_open
    ))

    # T232-DL-DUALSCORER-DEBT: sector_cap/open_risk_cap gate parity — both real, portfolio-
    # wide aggregates over the ALREADY-prefetched open book (no per-candidate DB query),
    # threaded through to decision-engine so it can reconstruct the SAME two hard rejects
    # _scan_for_entries' own fallback path applies below (this exact function, ~line 5140-5150).
    # Deliberately uses live_prices (not each trade's stale entry_price) to match
    # _best_price()'s own established convention — the fallback gate's own sector-value sum
    # already does this identically.
    _open_sector_values: dict[str, float] = {}
    for _t, _st in _prefetched_open:
        _sec_key = _st.sector or "unclassified"
        _open_sector_values[_sec_key] = _open_sector_values.get(_sec_key, 0.0) + _best_price(_t, live_prices) * _t.shares
    # open_risk: sum of (price - stop) * shares across every open trade, portfolio-wide —
    # exactly mirrors the fallback gate's own open_risk sum (line ~5127-5130), computed once
    # here rather than once per candidate since it doesn't depend on the candidate at all.
    _open_risk_total = sum(
        abs(live_prices.get(_t.symbol, _t.entry_price) - _t.current_stop) * _t.shares
        for _t, _ in _prefetched_open
    )
    # T232-DL-DUALSCORER-DEBT / T194: open-exposure cap gate parity — the ONE aggregate-
    # exposure sibling of the sector-$-cap and open-risk-$-cap above that was never ported.
    # AUD-DECIDE-OPENEXPOSURE-BASEMISMATCH (fixed): this value MUST use entry_price*shares
    # (cost basis) — NOT _open_sector_values' own live-price sum — because T194's own
    # pre-existing local hard reject a few dozen lines below (the check this value is
    # supposed to give decision-engine parity with) uses entry_price*shares, not live value.
    # Reusing _open_sector_values here looked like free reuse of an already-computed
    # aggregate, but it silently duplicated the WRONG base — a portfolio with meaningfully
    # appreciated open positions would read a materially higher % here than T194's own local
    # check computes for the identical state, letting decision-engine block an entry the
    # fallback gate (_should_enter()) would itself approve. Computed as its own genuine sum
    # over _prefetched_open, matching T194's own formula (line ~5009) exactly.
    _open_exposure_pct = (
        (sum(float(_t.entry_price) * float(_t.shares) for _t, _ in _prefetched_open) / equity * 100)
        if equity > 0 else None
    )

    # T258-PORTFOLIO-CORRELATION-PREENTRY: bulk-fetch daily closes for the open book ONCE per
    # scan cycle (not once per candidate) — each candidate's own correlation check below reuses
    # this same cache, only adding its own single stock_id's closes on top.
    _open_stock_ids = [st.id for _, st in _prefetched_open]
    _open_closes_cache = _bulk_fetch_daily_closes(session, _open_stock_ids) if _open_stock_ids else pd.DataFrame()

    # T221-B: Market cluster cap — block new entries when at the per-market position limit.
    # HK stocks are highly correlated: a market-wide down day stops out all positions simultaneously.
    _max_mkt_pos = cfg.get("max_market_positions", 4)
    _mkt_open_count = sum(1 for _, st in _prefetched_open if st.market == _mkt)
    if _mkt_open_count >= _max_mkt_pos:
        log.info("paper.market_cluster_cap",
                 portfolio=portfolio.name, market=_mkt,
                 open=_mkt_open_count, max=_max_mkt_pos,
                 note="market position cap reached — prevent single-market cluster loss")
        _write_gate_block(portfolio.id, "market_cluster_cap",
                          f"{_mkt} position cap reached ({_mkt_open_count}/{_max_mkt_pos}) — no new entries until a position closes")
        return

    # T237-GATE1: every portfolio-level gate above has now passed. _write_gate_block()'s Redis
    # key only self-expires after a 4h TTL — nothing previously cleared it the moment a gate
    # condition actually resolved (e.g. regime_risk_off -> choppy), so the Portfolio page could
    # show a stale "Risk-Off Regime" badge for up to 4h after the regime had already recovered.
    # Clear proactively here so the badge disappears as soon as this portfolio next scans clean.
    _clear_gate_block(portfolio.id)

    # T194: Open exposure cap — block new entries if deployed capital exceeds max % of equity.
    # A human never commits more than 40% of their capital to open positions simultaneously.
    _max_exposure_pct = cfg.get("max_open_exposure_pct", 0.40)
    if _max_exposure_pct and _max_exposure_pct > 0 and equity > 0:
        _open_exposure = sum(float(t.entry_price) * float(t.shares) for t, _ in _prefetched_open)
        if _open_exposure / equity > _max_exposure_pct:
            log.info("paper.open_exposure_cap",
                     portfolio=portfolio.name,
                     open_exposure_pct=round(_open_exposure / equity * 100, 1),
                     max_pct=round(_max_exposure_pct * 100, 1),
                     note="deployed capital cap reached — no new entries until positions close")
            return

    # T215: Multi-timeframe confluence — for GROWTH/LONG portfolios, pre-fetch SHORT horizon
    # signals for all candidates. A GROWTH BUY that contradicts the SHORT signal (SELL) means
    # near-term momentum is against the trade. Batch-query once; check in candidate loop.
    _short_signals: dict[int, str] = {}
    # T222-B: Extended to SWING — US SWING data shows 35.7% win rate; filtering contra-SHORT
    # entries should remove the subset where SHORT is already signalling a reversal.
    if cfg.get("confluence_check_enabled", True) and style in ("GROWTH", "LONG", "SWING"):
        _candidate_stock_ids = [stock.id for _, stock, _ in buy_signals]
        if _candidate_stock_ids:
            try:
                _short_ts_subq = (
                    select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
                    .where(Signal.stock_id.in_(_candidate_stock_ids), Signal.horizon == "SHORT")
                    .group_by(Signal.stock_id)
                    .subquery()
                )
                for _s_sig, _s_stk in session.execute(
                    select(Signal, Stock)
                    .join(Stock, Signal.stock_id == Stock.id)
                    .join(_short_ts_subq,
                          (Signal.stock_id == _short_ts_subq.c.stock_id) &
                          (Signal.ts == _short_ts_subq.c.max_ts))
                    .where(Signal.horizon == "SHORT")
                ).all():
                    _short_signals[_s_stk.id] = _s_sig.signal.value
            except Exception:
                pass  # fail-open — confluence check skipped if query fails

    # PT-P2 + PA-F1: batch-fetch ATR for all candidates in ONE yfinance download
    candidate_syms = [stock.symbol for _, stock, _ in buy_signals]
    atr_cache: dict[str, float | None] = _batch_compute_atr(list(set(candidate_syms)))

    # T196: Batch-fetch daily close at signal date — used to detect price-chasing.
    # One query per candidate (small N); fail-open if price row missing.
    _sig_ref_prices: dict[int, float] = {}
    for _sr, _sk, _ in buy_signals:
        if _sr.ts is None or _sk.id in _sig_ref_prices:
            continue
        _sig_date = (_sr.ts.replace(tzinfo=timezone.utc) if _sr.ts.tzinfo is None else _sr.ts).date()
        try:
            _ref_close = session.execute(
                select(Price.close)
                .where(
                    Price.stock_id == _sk.id,
                    Price.timeframe == TimeFrame.D1,
                    func.date(Price.ts) <= _sig_date,
                )
                .order_by(Price.ts.desc())
                .limit(1)
            ).scalar()
            if _ref_close is not None:
                _sig_ref_prices[_sk.id] = float(_ref_close)
        except Exception:
            pass  # fail-open — missing price row doesn't block the candidate

    # T221-A: Cross-portfolio symbol dedup — batch-fetch global open counts for all candidates.
    # Prevents SWING + GROWTH both entering the same stock, tripling concentration risk.
    _max_global_per_sym = cfg.get("max_positions_per_symbol_global", 1)
    _global_sym_open: dict[str, int] = {}
    if _max_global_per_sym > 0 and candidate_syms:
        for _gsym, _gcnt in session.execute(
            select(PaperTrade.symbol, func.count().label("c"))
            .where(PaperTrade.stage == "open", PaperTrade.symbol.in_(candidate_syms))
            .group_by(PaperTrade.symbol)
        ).all():
            _global_sym_open[_gsym] = _gcnt

    entries_made = 0
    # T232-WHYNOTRADE: tally per-candidate skip reasons so "no portfolio-level gate fired,
    # yet zero entries happened" is visible somewhere other than raw container logs — this
    # is exactly the situation from the 2026-07-03 HK GROWTH investigation, where every
    # candidate failed its OWN gate (volume, K-Score, cooldown, ...) rather than a
    # portfolio-level one, and nothing surfaced that anywhere in the UI.
    _skip_tally: dict[str, int] = {}
    for sig, stock, ranking in buy_signals:
        if open_count + entries_made >= cfg["max_positions"]:
            break
        # IF-12: restricted/no-trade symbol check — deliberately the FIRST check in the loop,
        # ahead of every other candidate-specific computation, since a user-banned symbol
        # should never even be considered regardless of any other state.
        if stock.symbol in _restricted_symbols:
            log.info("paper.skip_restricted_symbol", symbol=stock.symbol)
            _skip_tally["restricted_symbol"] = _skip_tally.get("restricted_symbol", 0) + 1
            continue
        if stock.symbol in _recently_stopped:
            log.info("paper.skip_stop_cooldown", symbol=stock.symbol, cooldown_hours=stop_cooldown_hours)
            _skip_tally["stop_cooldown"] = _skip_tally.get("stop_cooldown", 0) + 1
            continue
        # T221-A: Skip if this symbol already has an open position in another portfolio.
        if stock.symbol not in open_symbols and _global_sym_open.get(stock.symbol, 0) >= _max_global_per_sym:
            log.info("paper.skip_global_symbol_cap",
                     symbol=stock.symbol,
                     global_open=_global_sym_open.get(stock.symbol, 0),
                     note="symbol open in another portfolio — cross-portfolio concentration cap")
            _skip_tally["global_symbol_cap"] = _skip_tally.get("global_symbol_cap", 0) + 1
            continue
        if stock.symbol in open_symbols:
            # Scale-in: add to profitable position on fresh high-conviction signal.
            #
            # T286-PYRAMID-TIERS: extended from a single fixed add into a real 2-level pyramid,
            # mirroring the scale-OUT side's own established two-level structure exactly
            # (partial_tp_pct/partial_tp2_pct + PARTIAL1_TAKEN/PARTIAL2_TAKEN note markers,
            # ~2850 lines above) — this is the scale-IN counterpart that was previously capped
            # at one add with no way to configure a second, further-out tier. Level 1 keeps its
            # original trigger/size defaults UNCHANGED (5% gain, 25% add) for full backward
            # compatibility with every existing open trade's own "SCALE_IN" marker; Level 2 is
            # a NEW, separate, higher gain trigger (default +10%) adding a smaller tranche
            # (default 15%, since more capital is already committed by the time Level 2 fires)
            # — gated on Level 1 having already happened, exactly matching the scale-out side's
            # own p1_done-before-p2 ordering. Both levels independently re-check the SAME
            # fresh-high-conviction-signal requirement (confidence >= 60) rather than only
            # gating on price — an add should represent renewed conviction, not just "price
            # went up," matching the ORIGINAL single-tier design's own reasoning exactly.
            if cfg.get("scale_in_enabled", True):
                _si_live = live_prices.get(stock.symbol)
                if _si_live:
                    _si_trade = session.execute(
                        select(PaperTrade).where(
                            PaperTrade.portfolio_id == portfolio.id,
                            PaperTrade.symbol == stock.symbol,
                            PaperTrade.stage == "open",
                        )
                    ).scalar_one_or_none()
                    if _si_trade:
                        _si_pnl_pct = (_si_live - _si_trade.entry_price) / _si_trade.entry_price
                        _si_notes_list = _si_trade.entry_decision_notes or []
                        _si_l1_done = "SCALE_IN" in _si_notes_list
                        _si_l2_done = "SCALE_IN2" in _si_notes_list
                        _si_conf = float(sig.confidence or 0.0)
                        _si_trigger1 = float(cfg.get("scale_in_trigger_pct", 0.05))
                        _si_size1 = float(cfg.get("scale_in_size_pct", 0.25))
                        _si_trigger2 = float(cfg.get("scale_in_trigger2_pct", 0.10))
                        _si_size2 = float(cfg.get("scale_in_size2_pct", 0.15))

                        _si_fire_level: int | None = None
                        _si_size_pct: float | None = None
                        if not _si_l1_done and _si_pnl_pct >= _si_trigger1 and _si_conf >= 60.0:
                            _si_fire_level, _si_size_pct = 1, _si_size1
                        elif _si_l1_done and not _si_l2_done and _si_trigger2 and _si_pnl_pct >= _si_trigger2 and _si_conf >= 60.0:
                            _si_fire_level, _si_size_pct = 2, _si_size2

                        if _si_fire_level is not None and _si_size_pct:
                            _si_add_value = _si_live * _si_trade.shares * _si_size_pct
                            if portfolio.current_cash >= _si_add_value * 1.1:
                                _si_base_slippage = cfg.get("entry_slippage_pct", 0.001)
                                # IF-06: approximate the add-on share count pre-slippage for the
                                # size-aware lookup (a small circularity — exact shares depend on
                                # slippage, slippage depends on shares — negligible at this scale).
                                _si_approx_shares = _si_add_value / _si_live
                                _si_slippage = (
                                    _size_aware_slippage_pct(_si_approx_shares, _avg_daily_volume_for(_si_trade.symbol), _si_base_slippage)
                                    if cfg.get("size_aware_slippage_enabled", True) else _si_base_slippage
                                )
                                _si_add_shares = round(_si_add_value / (_si_live * (1 + _si_slippage)), 4)
                                _si_cost = round(_si_add_shares * _si_live * (1 + _si_slippage), 2)
                                portfolio.current_cash = round(portfolio.current_cash - _si_cost, 2)
                                # T234-PT-SCALEIN-COST-BASIS-BUG: blend entry_price as a weighted
                                # average of the original and added lots (standard cost-basis
                                # accounting), and increment entry_shares by the same amount —
                                # the scale-IN counterpart to T232-PT6's scale-OUT tracking above.
                                # Without this, close-time P&L used the INFLATED post-scale-in
                                # shares against the STALE pre-scale-in entry_price (overstating
                                # the numerator) while cost_basis used the frozen original
                                # entry_shares (understating the denominator) — both biased
                                # total_pnl_pct upward on every trade that scaled in, corrupting
                                # the SignalOutcome calibration writeback downstream. Applies
                                # identically at both levels — the blend math itself doesn't
                                # change, only WHEN it fires and how much it adds.
                                _si_old_shares = _si_trade.shares
                                _si_fill_price = round(_si_live * (1 + _si_slippage), 4)
                                _si_new_shares = round(_si_old_shares + _si_add_shares, 4)
                                _si_trade.entry_price = round(
                                    (_si_old_shares * _si_trade.entry_price + _si_add_shares * _si_fill_price)
                                    / _si_new_shares, 4
                                )
                                _si_trade.entry_shares = round((_si_trade.entry_shares or _si_old_shares) + _si_add_shares, 4)
                                _si_trade.shares = _si_new_shares
                                # AUD232-010: confidence_at_entry/kscore_at_entry/market_regime_at_entry
                                # were frozen at the ORIGINAL entry and never touched on scale-in, so
                                # downstream consumers (rl_agent.py's training features,
                                # paper_portfolio.py's calibration-by-confidence-band report,
                                # thesis_persistence_gate.py's regime baseline) silently attributed a
                                # scaled position's full P&L to stale, pre-scale-in conditions. Blend
                                # confidence/kscore share-weighted (same accounting pattern already
                                # used for entry_price above) and refresh regime to the current state
                                # at scale-in time, so a position that's mostly a fresh high-conviction
                                # add reads as one downstream, not as its original entry. Applies at
                                # BOTH levels identically — a Level 2 add re-blends against whatever
                                # Level 1 already blended in, not against the original entry alone.
                                _si_old_conf = _si_trade.confidence_at_entry or 0.0
                                _si_old_kscore = _si_trade.kscore_at_entry or 0.0
                                _si_new_kscore = float(ranking.score) if ranking and ranking.score is not None else _si_old_kscore
                                _si_trade.confidence_at_entry = round(
                                    (_si_old_shares * _si_old_conf + _si_add_shares * _si_conf) / _si_new_shares, 2
                                )
                                _si_trade.kscore_at_entry = round(
                                    (_si_old_shares * _si_old_kscore + _si_add_shares * _si_new_kscore) / _si_new_shares, 2
                                )
                                if live_regime and live_regime.get("state"):
                                    _si_trade.market_regime_at_entry = live_regime["state"]
                                _si_new_notes = list(_si_notes_list)
                                _si_marker = "SCALE_IN" if _si_fire_level == 1 else "SCALE_IN2"
                                _si_new_notes.append(_si_marker)
                                _si_new_notes.append(
                                    f"Scale-in L{_si_fire_level}: +{_si_add_shares:.4f}sh @ ${_si_live:.2f} "
                                    f"(+{_si_pnl_pct*100:.1f}%, conf {_si_conf:.0f}%)"
                                )
                                _si_trade.entry_decision_notes = _si_new_notes
                                log.info("paper.scale_in",
                                         symbol=stock.symbol, level=_si_fire_level, added_shares=_si_add_shares,
                                         live_price=_si_live, pnl_pct=round(_si_pnl_pct * 100, 1),
                                         confidence=_si_conf)

            # T241-P5-SHADOW: position-scaling gate (conviction-based PULLBACK add) —
            # the opposite direction from the scale-in block above, which only adds to a
            # position already up 5%+. This block NEVER places a real add or touches
            # portfolio.current_cash — position_scaling_mode="off" (default) skips it
            # entirely; "shadow" runs the real gate + thesis-persistence check on real data
            # and logs the verdict for later comparison against what actually happens to
            # the position, but still never acts. See _DEFAULT_CONFIG's position_scaling_mode
            # comment for why real order placement is deliberately deferred to a later phase.
            _ps_mode = cfg.get("position_scaling_mode", "off")
            if _ps_mode == "shadow":
                _ps_live = live_prices.get(stock.symbol)
                if _ps_live:
                    _ps_trade = session.execute(
                        select(PaperTrade).where(
                            PaperTrade.portfolio_id == portfolio.id,
                            PaperTrade.symbol == stock.symbol,
                            PaperTrade.stage == "open",
                        )
                    ).scalar_one_or_none()
                    if _ps_trade and _ps_live < float(_ps_trade.entry_price):
                        # Only a genuine pullback (price below current cost basis) is in scope —
                        # matches the explicit Phase 0 scope decision that this system adds
                        # ONLY on a pullback, leaving the existing 5%-up scale-in untouched.
                        try:
                            from ..backtest.candidate_event_mining import compute_live_features_for_position
                            from ..backtest.multi_tranche_engine import BarrierConfig
                            from ..backtest.position_scaling_gate import PositionScalingGate
                            from ..backtest.thesis_persistence_gate import (
                                RegimeLabel, ThesisPersistenceGate, snapshot_from_paper_trade,
                            )

                            _ps_notes = _ps_trade.entry_decision_notes or []
                            _ps_num_prior_adds = sum(1 for n in _ps_notes if "SCALE_IN" in str(n) or "PS_SHADOW" in str(n))
                            # AUD232-009/012: was dividing by portfolio.current_cash (remaining cash,
                            # not total value) — a 90%-invested portfolio holding a position genuinely
                            # worth 20% of equity computed this as ~200%. Use the same cash+positions
                            # total _compute_equity() already uses everywhere else in this file
                            # (sector-cap checks, etc.) so "pct of portfolio" means what its name says.
                            _ps_equity = _compute_equity(session, portfolio, live_prices)
                            _ps_pct_of_portfolio = round((_ps_trade.shares * _ps_live) / _ps_equity, 4) if _ps_equity > 0 else 0.0
                            _ps_features = compute_live_features_for_position(
                                session=session,
                                stock_id=stock.id,
                                symbol=stock.symbol,
                                sector=stock.sector,
                                current_price=_ps_live,
                                weighted_avg_cost_basis=float(_ps_trade.entry_price),
                                primary_signal_confidence=float(sig.confidence or 0.0),
                                signal_confidence_at_last_entry=float(_ps_trade.confidence_at_entry or 50.0),
                                regime_is_favorable=bool(live_regime and live_regime.get("state") in ("bull", "neutral")),
                                volume_zscore=float((sig.reasons or {}).get("volume_z") or 0.0),
                                support_level=(sig.reasons or {}).get("sr_nearest_support"),
                                days_since_last_entry=(datetime.now(timezone.utc).date() - _ps_trade.entry_date).days,
                                existing_position_pct_of_portfolio=_ps_pct_of_portfolio,
                                num_prior_adds=_ps_num_prior_adds,
                            )
                            if _ps_features is not None:
                                from common.config import get_settings as _gs_ps
                                _ps_model_path = str(Path(_gs_ps().model_dir) / "position_scaling_gate.joblib")
                                _ps_gate = PositionScalingGate.load(_ps_model_path)
                                _ps_pred = _ps_gate.predict(_ps_features)

                                _ps_snapshot = snapshot_from_paper_trade(
                                    symbol=stock.symbol,
                                    market_regime_at_entry=_ps_trade.market_regime_at_entry,
                                    confidence_at_entry=_ps_trade.confidence_at_entry,
                                    entry_price=float(_ps_trade.entry_price),
                                    entry_reasons=_ps_trade.entry_reasons,
                                )
                                _ps_current_regime = RegimeLabel.UNKNOWN
                                if live_regime and live_regime.get("state"):
                                    try:
                                        _ps_current_regime = RegimeLabel(live_regime["state"])
                                    except ValueError:
                                        pass
                                _ps_thesis_gate = ThesisPersistenceGate()
                                _ps_thesis_result = _ps_thesis_gate.check(
                                    _ps_snapshot,
                                    current_regime=_ps_current_regime,
                                    current_signal_confidence=float(sig.confidence or 0.0),
                                    current_rs_score=(sig.reasons or {}).get("rs_score"),
                                    current_price=_ps_live,
                                )
                                log.info(
                                    "paper.position_scaling_shadow",
                                    symbol=stock.symbol, portfolio=portfolio.name,
                                    act_probability=round(_ps_pred.act_probability, 4),
                                    suggested_size_multiplier=_ps_pred.suggested_size_multiplier,
                                    would_act=_ps_pred.should_act,
                                    thesis_recommendation=_ps_thesis_result.recommendation,
                                    thesis_broken_reasons=_ps_thesis_result.broken_reasons,
                                    pnl_pct=round((_ps_live - float(_ps_trade.entry_price)) / float(_ps_trade.entry_price) * 100, 2),
                                )
                                # T241-P6: persist beyond the log line so a rolling comparison
                                # report (was would_act right in hindsight?) can be built —
                                # resolved later by scheduler.py once max_holding_days has passed.
                                _record_position_scaling_shadow_verdict(
                                    symbol=stock.symbol,
                                    portfolio_id=portfolio.id,
                                    act_probability=_ps_pred.act_probability,
                                    suggested_size_multiplier=_ps_pred.suggested_size_multiplier,
                                    would_act=_ps_pred.should_act,
                                    thesis_recommendation=_ps_thesis_result.recommendation,
                                    thesis_broken_reasons=_ps_thesis_result.broken_reasons,
                                    price_at_verdict=_ps_live,
                                    entry_price=float(_ps_trade.entry_price),
                                    max_holding_days=BarrierConfig().max_holding_days,
                                )
                        except FileNotFoundError:
                            # No trained model saved yet (train_and_save_position_scaling_gate()
                            # hasn't run) — shadow mode has nothing to log against, fail silent
                            # rather than spamming logs every scan until a model exists.
                            pass
                        except Exception as _ps_exc:
                            log.warning("paper.position_scaling_shadow_failed",
                                        symbol=stock.symbol, error=str(_ps_exc))

            # TA-WHYNOTRADE2: this candidate already has an open position, so it was only ever
            # eligible for scale-in above (whether or not scale-in actually fired) — not a fresh
            # entry. Previously untallied, so _write_no_entry_summary's top_reasons could show []
            # even when every candidate had this perfectly legitimate explanation.
            _skip_tally["already_open_scale_in_only"] = _skip_tally.get("already_open_scale_in_only", 0) + 1
            continue
        # Optionally restrict to the GROWTH watchlist
        if growth_stock_ids and stock.id not in growth_stock_ids:
            # TA-WHYNOTRADE2: signals are computed per-horizon for every active stock regardless
            # of watchlist membership, so a stock can have e.g. a fresh SWING BUY signal without
            # ever being added to a SWING-style watchlist. Previously untallied — see above.
            _skip_tally["not_on_watchlist"] = _skip_tally.get("not_on_watchlist", 0) + 1
            continue
        # K-Score filter — enforce quality gate; reject unranked stocks if require_kscore
        if ranking is None:
            if cfg.get("require_kscore", True):
                log.info("paper.skip_no_ranking", symbol=stock.symbol,
                         note="no ranking row; skipped (require_kscore=True)")
                _skip_tally["no_ranking"] = _skip_tally.get("no_ranking", 0) + 1
                continue
        elif ranking.score < cfg["min_kscore"]:
            log.info("paper.skip_kscore", symbol=stock.symbol,
                     kscore=ranking.score, min=cfg["min_kscore"])
            _skip_tally["kscore"] = _skip_tally.get("kscore", 0) + 1
            continue

        # T195: Signal staleness gate — configurable max age (default 96h / 4 days).
        # Tighter than the 5-day query cutoff; handles normal 3-day weekends (≤84h).
        # A human discards a thesis that has sat untouched for days.
        if sig.ts is not None:
            _ts_aware = sig.ts.replace(tzinfo=timezone.utc) if sig.ts.tzinfo is None else sig.ts
            _sig_age_h = (datetime.now(timezone.utc) - _ts_aware).total_seconds() / 3600
            _max_age_h = float(cfg.get("max_signal_age_hours", 96))
            if _sig_age_h > _max_age_h:
                log.info("paper.skip_stale_signal", symbol=stock.symbol,
                         age_h=round(_sig_age_h, 1), max_age_h=_max_age_h, ts=str(sig.ts)[:19])
                _skip_tally["stale_signal"] = _skip_tally.get("stale_signal", 0) + 1
                continue

        # T215: Multi-timeframe confluence — GROWTH/LONG BUY that contradicts SHORT SELL
        # means near-term momentum is working against the entry. Skip until SHORT agrees.
        if _short_signals and stock.id in _short_signals:
            if _short_signals[stock.id] == "SELL":
                log.info("paper.skip_confluence_fail",
                         symbol=stock.symbol, style=style,
                         short_signal="SELL",
                         note="BUY contradicts SHORT SELL — near-term momentum against trade")
                _skip_tally["confluence_fail"] = _skip_tally.get("confluence_fail", 0) + 1
                continue

        live_price = live_prices.get(stock.symbol)
        if not live_price or live_price < 1.00:  # reject $0, pennies, and recently-delisted data
            continue

        # T196: Price drift gate — don't chase a stock that has rallied >N% since signal date.
        # Reference close fetched pre-loop; fail-open if missing.
        _max_drift = float(cfg.get("max_price_drift_pct", 3.0)) / 100.0
        if stock.id in _sig_ref_prices and _max_drift > 0:
            _drift = live_price / _sig_ref_prices[stock.id] - 1
            if _drift > _max_drift:
                log.info("paper.skip_price_drift",
                         symbol=stock.symbol,
                         drift_pct=round(_drift * 100, 1),
                         sig_close=round(_sig_ref_prices[stock.id], 2),
                         live_price=round(live_price, 2),
                         max_drift_pct=round(_max_drift * 100, 1),
                         note="price rallied too far from signal reference — chasing blocked")
                _skip_tally["price_drift"] = _skip_tally.get("price_drift", 0) + 1
                continue

        # T200: Volume confirmation gate — skip entries when intraday volume is abnormally low.
        # volume_z is stored in signal reasons by the signal engine (z-score vs 20-day avg).
        # Very low volume = thin market, harder to exit, higher slippage risk.
        # T232-DL5: a missing volume_z must NOT be treated as 0 (exactly average) — that silently
        # passes the gate for a data gap. Fail-open (skip the gate) only when data is genuinely absent.
        _vol_z_raw = (sig.reasons or {}).get("volume_z") if sig.reasons else None
        if _vol_z_raw is not None:
            _vol_z = float(_vol_z_raw)
            _min_vol_z = float(cfg.get("min_volume_z", -1.5))
            if _vol_z < _min_vol_z:
                log.info("paper.skip_low_volume",
                         symbol=stock.symbol,
                         volume_z=round(_vol_z, 2),
                         min_vol_z=_min_vol_z,
                         note="below-average volume — entry postponed, higher slippage risk")
                _skip_tally["low_volume"] = _skip_tally.get("low_volume", 0) + 1
                continue

        # T224-A: Mainland flow gate — HK entries require positive 5-day southbound flow.
        # flow_5d_net_hkd < 0 means mainland money is net-selling the stock (bearish pressure).
        # Fail-open if flow data is absent (not all stocks are Stock Connect eligible).
        # MD-HKCONNECT2 (2026-07-13): real data now flows again — hk_connect.py was rewired to
        # a working Eastmoney Stock Connect holdings-ranking source (see that module's
        # docstring), superseding the dead HKEX endpoint (MD-HKCONNECT1, 2026-07-09) this gate
        # was previously permanently fail-open against. This gate is once more able to actively
        # block an HK entry on confirmed mainland net-selling, not just structurally pass
        # everything through.
        if cfg.get("market") == "HK":
            _flow5d = (sig.reasons or {}).get("flow_5d_net_hkd")
            if _flow5d is not None and float(_flow5d) <= 0:
                log.info("paper.skip_hk_flow_gate",
                         symbol=stock.symbol,
                         flow_5d_net_hkd=round(float(_flow5d), 0),
                         note="mainland outflow — HK BUY entry blocked (T224-A)")
                _skip_tally["hk_flow_gate"] = _skip_tally.get("hk_flow_gate", 0) + 1
                continue

        # T224-C / T225-A: TA score gate — applies to any market/style with min_ta_score set.
        # HK: 0.60 (from _HK_MARKET_OVERRIDES) — ML is US-biased, TA is more reliable for HK.
        # SWING: 0.50 (from _STYLE_OVERRIDES) — ta_lo50 bucket had 31.4% win rate (Jun 2026 audit).
        # Fail-open if ta_score absent from reasons (defaults to 1.0).
        _min_ta = float(cfg.get("min_ta_score", 0.0))
        if _min_ta > 0:
            _ta_raw = (sig.reasons or {}).get("ta_score")
            _ta = float(_ta_raw) if _ta_raw is not None else 1.0
            if _ta < _min_ta:
                log.info("paper.skip_ta_gate",
                         symbol=stock.symbol,
                         market=cfg.get("market"),
                         ta_score=round(_ta, 3),
                         min_ta_score=_min_ta,
                         note="TA score below minimum — entry blocked")
                _skip_tally["ta_gate"] = _skip_tally.get("ta_gate", 0) + 1
                continue

        # Build game plan using cached ATR
        atr = atr_cache.get(stock.symbol)

        # SA-26: Confidence trajectory — query most recent prior confidence for delta scoring
        confidence_delta: float | None = None
        try:
            prior_conf = session.execute(
                select(Signal.confidence)
                .where(
                    Signal.stock_id == stock.id,
                    Signal.horizon == style,
                    Signal.ts < sig.ts,
                )
                .order_by(Signal.ts.desc())
                .limit(1)
            ).scalar()
            if prior_conf is not None and sig.confidence is not None:
                confidence_delta = round(float(sig.confidence) - float(prior_conf), 1)
        except Exception:
            pass  # trajectory optional; don't block entry on query failure

        # T202: Declining confidence gate — don't enter when signal conviction is falling.
        # A setup losing confidence over multiple refreshes is degrading, not improving.
        _conf_decline_threshold = float(cfg.get("max_confidence_decline", -8.0))
        if confidence_delta is not None and confidence_delta < _conf_decline_threshold:
            log.info("paper.skip_declining_confidence",
                     symbol=stock.symbol,
                     confidence_delta=round(confidence_delta, 1),
                     threshold=_conf_decline_threshold,
                     note="signal losing confidence — setup degrading, wait for stabilisation")
            _skip_tally["declining_confidence"] = _skip_tally.get("declining_confidence", 0) + 1
            continue

        signal_data = {
            "signal": sig.signal.value,
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons or {},
            "ts": sig.ts,               # SA-24: freshness scoring
            "confidence_delta": confidence_delta,  # SA-26: trajectory scoring
        }
        game_plan = _build_game_plan_for_style(
            stock.symbol, style, live_price, sig.reasons or {}, atr
        )

        # ── Game plan feasibility check ───────────────────────────────────────
        gp_stop = game_plan["stop"]
        gp_target = game_plan["take_profit"]
        if gp_stop >= live_price * 0.99:
            log.warning("paper.skip_invalid_gameplan", symbol=stock.symbol,
                        reason="stop >= price", stop=round(gp_stop, 2), price=round(live_price, 2))
            continue
        if gp_target <= live_price * 1.01:
            log.warning("paper.skip_invalid_gameplan", symbol=stock.symbol,
                        reason="target <= price", target=round(gp_target, 2), price=round(live_price, 2))
            continue

        # Conviction gate hard-block: if the alert system already evaluated this
        # BUY and the conviction gate failed, skip the entry — paper trading must
        # agree with what the alert system would notify (TIER66-PAPER-GATE).
        # Uses the existing conv_gate:{symbol}:{style} Redis key (1-day TTL).
        # No gate key = gate not yet run (allow entry; first BUY before next alert cycle).
        try:
            from common.redis_client import get_redis as _get_pool_redis
            _gate_redis = _get_pool_redis()
            _style = style
            _cgval = _gate_redis.get(f"conv_gate:{stock.symbol}:{_style}")
            if _cgval:
                _cgdata = json.loads(_cgval)
                # sent=False means the gate explicitly failed for this BUY signal
                if _cgdata.get("signal") == "BUY" and _cgdata.get("sent") is False:
                    _failed_layers = _cgdata.get("failed", [])
                    log.info("paper.entry_gate_blocked",
                             symbol=stock.symbol, style=_style, failed=_failed_layers[:2])
                    _skip_tally["conviction_gate"] = _skip_tally.get("conviction_gate", 0) + 1
                    continue
        except Exception:
            pass  # Redis unavailable or parse error → allow entry (fail-open)

        # Entry qualifier: Decision Engine is authoritative; _should_enter() is the fallback.
        de_mode = cfg.get("decision_engine_mode", "primary")
        kscore_f = float(ranking.score) if ranking and ranking.score is not None else None
        # T232-DL-DUALSCORER-DEBT: same reasons dict the TA-score hard-reject above (line ~4207)
        # already reads from — reused here, not re-fetched.
        _ta_score_raw = (sig.reasons or {}).get("ta_score")
        ta_score_f = float(_ta_score_raw) if _ta_score_raw is not None else None
        # MPE-05: fresh, fail-open squeeze-score lookup — cheap (reuses an already-cached
        # fundamentals blob, no new fetch). pressure_score is deliberately NOT wired here —
        # its real inputs (cp_ratio, whale activity) only exist behind a live options-chain
        # yfinance fetch, and this app's own established rate-limit discipline (see
        # options_flow_snapshot.py's own docstring) explicitly rules out an options-chain call
        # inside a hot per-candidate entry-scan loop.
        _squeeze_score_val = _squeeze_score_for(
            stock.symbol, ranking.momentum if ranking and ranking.momentum is not None else None,
        )
        gate_source = "de"

        # T232-DL-DUALSCORER-SHADOW: run BOTH scorers on every candidate regardless of which one
        # is authoritative, purely to populate the de:divergences/de:agreements comparison data
        # the /paper-portfolio/de-divergences endpoint and "DE Audit" UI tab have been reading
        # from since before this fix — with no writer anywhere, that endpoint always silently
        # returned zero data no matter how long the system ran. Only the AUTHORITATIVE scorer
        # (selected by de_mode below, unchanged from before) drives should_enter/score/notes;
        # the other one's result is used for comparison logging only and can never affect
        # whether a real position gets opened.
        de_result = _call_decision_engine(
            symbol=stock.symbol,
            live_price=live_price,
            game_plan=game_plan,
            equity=equity,
            open_count=open_count,
            cfg=cfg,
            initial_capital=portfolio.initial_capital,  # T232-DL-DUALSCORER-DEBT: T201 gate parity
            # T264-ENTRYGATESOVERRIDE: while the override is active, report these two as
            # "no problem" to decision-engine directly rather than threading the flag through
            # _call_decision_engine()'s own signature — DE independently re-implements both the
            # daily-loss and consecutive-loss hard rejects (hard_rejects.py), so the fallback
            # gate's own override below is not enough on its own when decision_engine_mode is
            # "primary" (the live default) — DE would still see the REAL numbers and block.
            daily_pnl_pct=(0.0 if _gates_override else _daily_pnl_pct),
            recent_win_rate=_recent_wr,
            open_sector_counts=_open_sector_counts,   # T186: sector gate
            candidate_sector=stock.sector,             # T186: sector gate
            consec_losses=(0 if _gates_override else _consec_losses),  # T187: streak gate
            recent_stop_count=_recent_stops,            # T232-DL-DUALSCORER-DEBT / T221-E: heat brake
            market_open_count=_mkt_open_count,          # T232-DL-DUALSCORER-DEBT / T221-B: market cluster cap
            kscore=kscore_f,                           # AUD232-042: K-Score visibility
            ta_score=ta_score_f,                        # T232-DL-DUALSCORER-DEBT: TA-score gate parity
            # AUD256: regime_state needed so the calibrated regime_min_rr_ratio default
            # resolves correctly — _default_min_rr_ratio() only returns that key's calibrated
            # value when regime_state is choppy/risk_off, matching _should_enter()'s own usage.
            regime_state=(live_regime.get("state", "neutral") if live_regime else "neutral"),
            confidence_delta=confidence_delta,          # T232-DL-DUALSCORER-DEBT: T202 gate parity
            index_return_pct=_idx_ret,                  # T232-DL-DUALSCORER-DEBT: T221 gate parity
            sig_ref_price=_sig_ref_prices.get(stock.id), # T232-DL-DUALSCORER-DEBT: T196 gate parity
            short_signal=_short_signals.get(stock.id),  # T232-DL-DUALSCORER-DEBT: T215/T222-B gate parity
            open_sector_value=_open_sector_values.get(stock.sector or "unclassified", 0.0),  # T232-DL-DUALSCORER-DEBT: sector_cap (dollar) gate parity
            open_risk_total=_open_risk_total,           # T232-DL-DUALSCORER-DEBT: open_risk_cap gate parity
            weekly_net_pnl_pct=_weekly_net_pnl_pct,      # T232-DL-DUALSCORER-DEBT: weekly loss/gain gate parity
            open_exposure_pct=_open_exposure_pct,        # T232-DL-DUALSCORER-DEBT / T194: open-exposure cap gate parity
            squeeze_score=_squeeze_score_val,             # MPE-05: Market Pressure Engine, soft corroboration layer
        )
        _max_corr = _max_correlation_with_open_positions(
            session, stock.id, _open_stock_ids, _open_closes_cache,
        )
        se_result = _should_enter(
            stock.symbol, signal_data, live_price, game_plan, cfg, live_regime,
            kscore=kscore_f, max_open_corr=_max_corr, recent_win_rate=_recent_wr,
        )

        if de_mode == "primary":
            if de_result is not None:
                should_enter, de_verdict, score, de_blocked, de_min_score = de_result
                notes = [f"DE: {de_verdict}"] + ([f"blocked: {de_blocked}"] if de_blocked else [])
                log.info("paper.de_verdict", symbol=stock.symbol,
                         verdict=de_verdict, score=score, blocked=de_blocked)
                _record_de_shadow_comparison(
                    stock.symbol, se_result[0], se_result[1],
                    de_verdict, score,
                    de_min_score if de_min_score is not None else cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]),
                    de_blocked,
                )
            else:
                # DE unreachable — fall back to _should_enter() so trading continues
                should_enter, score, notes = se_result
                gate_source = "fallback"
                log.warning("paper.de_fallback", symbol=stock.symbol,
                            note="DE unreachable; using _should_enter()")
        else:
            # Legacy shadow mode: _should_enter() decides; DE result (if reachable) logged only.
            should_enter, score, notes = se_result
            gate_source = "legacy"
            if de_result is not None:
                _, de_verdict, de_score, de_blocked, de_min_score = de_result
                _record_de_shadow_comparison(
                    stock.symbol, should_enter, score,
                    de_verdict, de_score,
                    de_min_score if de_min_score is not None else cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]),
                    de_blocked,
                )

        if not should_enter:
            log.info("paper.entry_skipped",
                     symbol=stock.symbol, score=score, gate=gate_source,
                     min_score=cfg.get("min_entry_score", _DEFAULT_CONFIG["min_entry_score"]),
                     regime=regime_state, reasons=notes[:3])
            # T232-WHYNOTRADE: this is the entry-qualifier rejection (DE score below threshold,
            # or _should_enter()'s own hard rejects on fallback/legacy mode) — previously the
            # one major rejection point NOT tallied, so a candidate failing only here showed as
            # an empty top_reasons list with no explanation of why nothing traded.
            _skip_tally["entry_score_below_threshold"] = _skip_tally.get("entry_score_below_threshold", 0) + 1
            continue

        log.info("paper.entry_decision",
                 symbol=stock.symbol, score=score, gate=gate_source, notes=notes[:2])

        trade, skip_reason = _open_paper_trade(
            session, portfolio, stock, sig, ranking, live_price, game_plan, score, notes,
            gate_source, cfg, style, equity, regime_size_mult, live_regime, live_prices,
            _prefetched_open, atr,
        )
        if trade is None:
            _skip_tally[skip_reason] = _skip_tally.get(skip_reason, 0) + 1
            continue

        open_symbols.add(stock.symbol)
        entries_made += 1
        # Recalculate equity after each entry so successive entries in this cycle
        # use the updated cash/position value rather than the stale snapshot
        equity = _compute_equity(session, portfolio, live_prices)

    # T232-WHYNOTRADE: when the scan reaches this point with zero entries, no
    # portfolio-level gate blocked it (those `return` earlier in this function) — every
    # candidate individually failed its own per-symbol check. Surface the tally so this
    # is visible in the UI (paper-gates.tsx / paper-portfolio.tsx) instead of requiring a
    # container-log dig, same place _write_gate_block's portfolio-level reason shows up.
    if entries_made == 0:
        _write_no_entry_summary(portfolio.id, len(buy_signals), _skip_tally)
    else:
        _clear_no_entry_summary(portfolio.id)


# ── Equity computation ────────────────────────────────────────────────────────

def _best_price(trade: PaperTrade, live_prices: dict[str, float]) -> float:
    """Best available price for a trade: live → DB-cached current_price → entry_price.

    Avoids silently using entry_price (0% gain) when live fetch fails — instead uses
    the last successfully cached price, which is far more accurate.
    """
    return live_prices.get(trade.symbol) or trade.current_price or trade.entry_price


def _compute_equity(session, portfolio: PaperPortfolio, live_prices: dict[str, float]) -> float:
    """Cash + market value of all open positions."""
    open_trades = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == portfolio.id,
            PaperTrade.stage == "open",
        )
    ).scalars().all()
    positions_value = sum(
        _best_price(t, live_prices) * t.shares for t in open_trades
    )
    return portfolio.current_cash + positions_value


def _sector_value(session, portfolio: PaperPortfolio, sector: str | None, live_prices: dict[str, float]) -> float:
    """Dollar value of open trades in the given sector (None = unclassified stocks)."""
    q = (
        select(PaperTrade, Stock)
        .join(Stock, PaperTrade.symbol == Stock.symbol)
        .where(PaperTrade.portfolio_id == portfolio.id, PaperTrade.stage == "open")
    )
    q = q.where(Stock.sector.is_(None)) if not sector else q.where(Stock.sector == sector)
    return sum(_best_price(t, live_prices) * t.shares for t, _ in session.execute(q).all())


def _sector_count(session, portfolio: PaperPortfolio, sector: str | None) -> int:
    """Number of open positions in the given sector (None = unclassified)."""
    q = (
        select(func.count())
        .select_from(PaperTrade)
        .join(Stock, PaperTrade.symbol == Stock.symbol)
        .where(PaperTrade.portfolio_id == portfolio.id, PaperTrade.stage == "open")
    )
    q = q.where(Stock.sector.is_(None)) if not sector else q.where(Stock.sector == sector)
    return session.execute(q).scalar() or 0


# ── Equity curve snapshot ─────────────────────────────────────────────────────

def snapshot_equity_curve(portfolio_id: int | None = None) -> None:
    """Record EOD equity + benchmark closes + market regime. Called post-close from scheduler."""
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

    # PT-A2: fetch current regime for shading overlay
    snapshot_regime: str | None = None
    try:
        regime_data = _fetch_market_regime(_DEFAULT_CONFIG)
        snapshot_regime = regime_data.get("state")
    except Exception:
        pass

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
                _best_price(t, live) * t.shares for t in open_trades
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
                if snapshot_regime:
                    existing.market_regime = snapshot_regime
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
                    market_regime        = snapshot_regime,
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

def _send_exit_emails(session, closed_exits: list[dict]) -> None:
    """Send exit alert emails to all users who have a SignalAlert subscription for the exited symbol."""
    for exit_info in closed_exits:
        symbol = exit_info["symbol"]
        try:
            # Find all users subscribed to this symbol with an email address
            rows = session.execute(
                select(SignalAlert.email)
                .join(User, SignalAlert.user_id == User.id)
                .where(
                    SignalAlert.symbol == symbol,
                    SignalAlert.email.isnot(None),
                    User.is_active.is_(True),
                )
            ).scalars().all()
            for email in rows:
                if not (email or "").strip():
                    continue
                try:
                    send_trade_exit_email(
                        to=email,
                        symbol=symbol,
                        exit_reason=exit_info["exit_reason"],
                        entry_price=exit_info["entry_price"],
                        exit_price=exit_info["exit_price"],
                        pnl_dollar=exit_info["pnl_dollar"],
                        pnl_pct=exit_info["pnl_pct"],
                        hold_days=exit_info["hold_days"],
                        shares=exit_info["shares"],
                        style=exit_info.get("style", "GROWTH"),
                        signal_at_exit=exit_info.get("signal_at_exit"),
                        highest_price=exit_info.get("highest_price"),
                        entry_notes=exit_info.get("entry_notes", []),
                        market_hours_open=exit_info.get("market_hours_open", True),
                    )
                    log.info("paper.exit_email_sent", symbol=symbol, to=email,
                             reason=exit_info["exit_reason"])
                except Exception as _em:
                    log.error("paper.exit_email_failed", symbol=symbol, to=email, error=str(_em))
        except Exception as _qe:
            log.error("paper.exit_email_query_failed", symbol=symbol, error=str(_qe))


def paper_trading_step() -> None:
    """One full monitor + scan cycle. Runs every 5-10 min during market hours."""
    # AL-4: reload tuned params each cycle so they take effect without a restart
    _load_tuned_params()
    _apply_tuned_hold_days()
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
                    .where(
                        Watchlist.trading_style.in_(
                            [p.config.get("trading_style", "GROWTH") for p in portfolios]
                        ),
                        Stock.active.is_(True),
                        # BUG-DELISTED-GENERATION-BLIND: a delisted watchlist symbol never
                        # needs a live price pre-fetch — it can never become a real BUY
                        # candidate (_scan_for_entries()'s own query already excludes it).
                        Stock.delisted.is_(False),
                    )
                ).all()
            ))

            all_symbols = list(set(open_symbols + candidate_symbols))
            live_prices = _fetch_live_prices(all_symbols) if all_symbols else {}

            # Fetch market regime per market — US uses SPY/QQQ/VIX; HK uses HSI.
            # Cache the result per market so portfolios sharing a market reuse the same fetch.
            _regime_by_market: dict[str, dict | None] = {}

            def _get_regime_for(pcfg: dict) -> dict | None:
                mkt = pcfg.get("market", "US")
                if mkt not in _regime_by_market:
                    if not pcfg.get("enable_regime_filter", True):
                        _regime_by_market[mkt] = None
                    elif mkt == "HK":
                        _regime_by_market[mkt] = _fetch_hk_market_regime(pcfg)
                    else:
                        _regime_by_market[mkt] = _fetch_market_regime(pcfg)
                return _regime_by_market[mkt]

            for portfolio in portfolios:
                cfg = {**_DEFAULT_CONFIG, **portfolio.config} if portfolio.config else dict(_DEFAULT_CONFIG)
                if not cfg.get("enabled", True):
                    continue  # fully stopped — do nothing

                live_regime = _get_regime_for(cfg)

                # Persist regime snapshot into portfolio config (for UI + audit trail)
                if live_regime:
                    portfolio.config = {**portfolio.config,
                                        "regime_state": live_regime["state"],
                                        "regime_vix": live_regime.get("vix"),
                                        "regime_spy": live_regime.get("spy_price"),
                                        "regime_notes": live_regime.get("notes", [])}

                # Monitor + commit first so cash mutations are durable before scanning
                closed_exits = _monitor_positions(session, portfolio, live_prices, live_regime)
                session.commit()

                # PT-EA1: Send exit alert emails to users subscribed to the exited symbol
                if closed_exits:
                    _send_exit_emails(session, closed_exits)

                if not cfg.get("paused", False):
                    # Market hours guard: check hours for this portfolio's market
                    mkt = cfg.get("market", "US")
                    if cfg.get("enforce_market_hours", True) and not _is_market_hours(mkt):
                        log.info("paper.entry_scan_skip", reason="outside_market_hours", market=mkt)
                    else:
                        _scan_for_entries(session, portfolio, live_prices, live_regime)
                        session.commit()

    except Exception as exc:
        log.error("paper.step_failed", error=str(exc), exc_info=True)
