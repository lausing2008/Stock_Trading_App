"""AUD-OPTIONS4-GAMEPLANBATCH: end-of-day Options Game Plan snapshot persistence.

compute_options_game_plan() (services/market-data/src/api/routes.py) is pure and already
reusable, but its own FastAPI route does a live, per-request, uncached yfinance options-chain
fetch — safe for one symbol on one page view, not for a scan-list row or a daily BUY-signal
email touching many symbols/recipients at once (the exact rate-limit-amplification shape
docs/incidents/yfinance-rate-limit-amplification.md already warns against). This module computes
the same real game plan once per symbol per day and persists it, mirroring
options_flow_snapshot.py's/gex_snapshot.py's own established EOD-batch pattern exactly (bounded
symbol set, ON CONFLICT upsert on (stock_id, as_of), one commit per batch job).

Deliberately imports compute_options_game_plan() directly from routes.py rather than
re-deriving its own copy of the strike/expiry-selection math — unlike options_flow_snapshot.py
(which duplicates get_options_flow()'s comparatively simple cp_ratio/sentiment math to stay
fully decoupled from the FastAPI-route-shaped file), compute_options_game_plan() is explicitly
documented as pure (no DB/HTTP access) and reusing it directly avoids a second, possibly-drifting
copy of real strike/expiry selection logic. scheduler.py already imports several other pure
helpers from routes.py this same way (refresh_avg_volume_cache, _macro_events_from_db,
get_rvol) — this is an established pattern, not a new one.

Stop-loss/take-profit inputs deliberately differ from the live route's own (nearest-support/
analyst-target, sourced from the requesting frontend page): this batch job instead reuses
_build_game_plan_for_style()'s real ATR-based SWING-style entry/stop/target — the SAME function
the Short Squeeze alert's own _squeeze_game_plan() already calls — since that math needs no live
yfinance call and is already proven safe at a 1-minute alert's own cadence. See
OptionsGamePlanSnapshot's own model docstring for why these two legitimate methods can disagree
without being a bug.

AUD-DECIDE4-EXPECTEDMOVE: also computes expected_move_pct from Unusual Whales'
get_iv_rank(symbol) — a real, market-implied volatility reading, replacing
_build_game_plan_for_style()'s own fixed-percentage take-profit / no-ATR-stop fallback (Domain 2
of the platform audit series found this was the dominant real decision-engine reject reason —
"a fabricated 2.00:1 R:R from a missing-ATR fallback game plan, not a measured setup property").
Standard expected-move formula: price * (iv/100) * sqrt(dte/365). Uses this batch's own fixed
~30-day reference window (the same mid-range DTE _STYLE_PARAMS' SWING style already targets)
rather than any specific listed contract's own expiry, since IV rank is a continuous per-symbol
reading, not tied to one contract. NULL when Unusual Whales is unavailable or has no IV data for
this symbol — the existing fixed-percentage fallback remains the free-tier default, never a
fabricated expected move standing in for a missing real one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from db import OptionsGamePlanSnapshot
from sqlalchemy.dialects.postgresql import insert as pg_insert

import structlog

log = structlog.get_logger()


_EXPECTED_MOVE_REFERENCE_DTE = 30  # a mid-range reference window, matching SWING's own typical
# hold-period order of magnitude — see this module's own docstring for the full rationale.


@dataclass
class OptionsGamePlanResult:
    underlying_close: float
    stop_loss: float | None
    take_profit: float | None
    put_strike: float | None
    put_expiry: str | None
    put_mid_price: float | None
    put_effective_floor_price: float | None
    call_strike: float | None
    call_expiry: str | None
    call_mid_price: float | None
    call_effective_cap_price: float | None
    expected_move_pct: float | None = None
    expected_move_dte: int | None = None


def compute_options_game_plan_snapshot(session, stock_id: int, symbol: str) -> OptionsGamePlanResult | None:
    """Fetch a real options chain for `symbol` and compute its game plan against ATR-based
    SWING-style stop/target (see this module's own docstring for why, not nearest-support/
    analyst-target). Returns None if the symbol has no listed options, no current price, or the
    fetch fails for any reason (fail-open — a single symbol's failure must never abort the whole
    EOD batch, matching options_flow_snapshot.py's own compute_options_flow() contract exactly).
    """
    from ..api.routes import (
        compute_options_game_plan, _options_chain_rows,
        _nearest_expiry_in_dte_window, _goal_current_price,
        _OPTIONS_GAME_PLAN_MIN_PUT_DTE, _OPTIONS_GAME_PLAN_MAX_PUT_DTE,
        _OPTIONS_GAME_PLAN_MIN_CALL_DTE, _OPTIONS_GAME_PLAN_MAX_CALL_DTE,
    )
    from .paper_trading_engine import _build_game_plan_for_style
    from db import Signal, SignalHorizon
    from sqlalchemy import select

    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        expiries = sorted(t.options)
        if not expiries:
            return None

        current_price = _goal_current_price(session, symbol)
        if current_price is None:
            hist = t.history(period="1d")
            current_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        if not current_price:
            return None

        sig = session.execute(
            select(Signal)
            .where(Signal.stock_id == stock_id, Signal.horizon == SignalHorizon.SWING)
            .order_by(Signal.ts.desc())
            .limit(1)
        ).scalars().first()
        reasons = (sig.reasons or {}) if sig else {}
        atr = reasons.get("atr_14")
        game_plan = _build_game_plan_for_style(symbol, "SWING", current_price, reasons, atr)
        if game_plan is None:
            return None
        stop_loss = game_plan.get("stop")
        take_profit = game_plan.get("take_profit")

        today = datetime.now(timezone.utc).date()
        put_exp = _nearest_expiry_in_dte_window(
            expiries, today, _OPTIONS_GAME_PLAN_MIN_PUT_DTE, _OPTIONS_GAME_PLAN_MAX_PUT_DTE
        )
        call_exp = _nearest_expiry_in_dte_window(
            expiries, today, _OPTIONS_GAME_PLAN_MIN_CALL_DTE, _OPTIONS_GAME_PLAN_MAX_CALL_DTE
        )

        put_rows: list[dict] = []
        call_rows: list[dict] = []
        try:
            if put_exp:
                put_rows = _options_chain_rows(t.option_chain(put_exp).puts)
        except Exception as exc:
            log.warning("options_game_plan_snapshot.put_fetch_failed", symbol=symbol, expiry=put_exp, error=str(exc))
        try:
            if call_exp == put_exp:
                call_rows = _options_chain_rows(t.option_chain(call_exp).calls) if call_exp else []
            elif call_exp:
                call_rows = _options_chain_rows(t.option_chain(call_exp).calls)
        except Exception as exc:
            log.warning("options_game_plan_snapshot.call_fetch_failed", symbol=symbol, expiry=call_exp, error=str(exc))

        plan = compute_options_game_plan(
            current_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal=None,
            put_expiries=[put_exp] if put_exp else [],
            put_rows=put_rows,
            call_expiries=[call_exp] if call_exp else [],
            call_rows=call_rows,
            shares=None,
            today=today,
        )
        put = plan.get("protective_put") or {}
        call = plan.get("covered_call") or {}
        if not put and not call:
            return None

        # AUD-DECIDE4-EXPECTEDMOVE: real, per-symbol IV from Unusual Whales — no options-chain
        # fetch needed for this specific piece (unlike the put/call legs above), so a failure or
        # missing UW subscription only costs this one field, never the rest of the snapshot.
        expected_move_pct: float | None = None
        expected_move_dte: int | None = None
        try:
            from math import sqrt
            from . import unusual_whales as _uw
            _iv_data = _uw.get_iv_rank(symbol)
            if _iv_data is not None and _iv_data.volatility is not None and _iv_data.volatility > 0:
                # AUD-DECIDE4-EXPECTEDMOVE-UNITS: UW's own published spec for this endpoint
                # ("The implied volatility value") does not state whether `volatility` is a
                # fraction (0.35) or a percent (35.0) — this app's own OTHER iv field
                # (_options_chain_rows(), yfinance-sourced) is stored as a percent-like number.
                # Normalize defensively: a real annualized IV essentially never exceeds 10.0 as
                # a fraction (1000%), so a value above that threshold is almost certainly
                # already a percent and needs /100 first. Re-verify this assumption against a
                # real live response once a UW subscription is active — see this module's own
                # "what to check if this looks wrong" note in the audit doc.
                _iv_fraction = _iv_data.volatility if _iv_data.volatility <= 10.0 else _iv_data.volatility / 100.0
                expected_move_pct = round(
                    _iv_fraction * sqrt(_EXPECTED_MOVE_REFERENCE_DTE / 365.0) * 100, 4
                )
                expected_move_dte = _EXPECTED_MOVE_REFERENCE_DTE
        except Exception as exc:
            log.warning("options_game_plan_snapshot.expected_move_failed", symbol=symbol, error=str(exc))

        return OptionsGamePlanResult(
            underlying_close=round(current_price, 4),
            stop_loss=stop_loss,
            take_profit=take_profit,
            expected_move_pct=expected_move_pct,
            expected_move_dte=expected_move_dte,
            put_strike=put.get("strike"),
            put_expiry=put.get("expiry"),
            put_mid_price=put.get("mid_price"),
            put_effective_floor_price=put.get("effective_floor_price"),
            call_strike=call.get("strike"),
            call_expiry=call.get("expiry"),
            call_mid_price=call.get("mid_price"),
            call_effective_cap_price=call.get("effective_cap_price"),
        )
    except Exception as exc:
        log.warning("options_game_plan_snapshot.compute_failed", symbol=symbol, error=str(exc))
        return None


def upsert_options_game_plan_snapshot(
    session, stock_id: int, result: OptionsGamePlanResult, as_of: date | None = None
) -> None:
    """Upsert one OptionsGamePlanSnapshot row. Idempotent via ON CONFLICT DO UPDATE on
    (stock_id, as_of) — safe to re-run for the same day without creating duplicate rows.
    Does NOT commit — the caller (the EOD batch job) commits once after the whole batch,
    matching options_flow_snapshot.py's own convention of one commit per batch rather than per-row.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    values = dict(
        stock_id=stock_id,
        as_of=as_of,
        underlying_close=result.underlying_close,
        stop_loss=result.stop_loss,
        take_profit=result.take_profit,
        expected_move_pct=result.expected_move_pct,
        expected_move_dte=result.expected_move_dte,
        put_strike=result.put_strike,
        put_expiry=result.put_expiry,
        put_mid_price=result.put_mid_price,
        put_effective_floor_price=result.put_effective_floor_price,
        call_strike=result.call_strike,
        call_expiry=result.call_expiry,
        call_mid_price=result.call_mid_price,
        call_effective_cap_price=result.call_effective_cap_price,
    )
    stmt = pg_insert(OptionsGamePlanSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "as_of"],
        set_={k: v for k, v in values.items() if k not in ("stock_id", "as_of")},
    )
    session.execute(stmt)


def get_latest_options_game_plan(session, stock_id: int) -> OptionsGamePlanSnapshot | None:
    """Most recent OptionsGamePlanSnapshot row for a stock, or None if never computed."""
    from sqlalchemy import select

    return session.execute(
        select(OptionsGamePlanSnapshot)
        .where(OptionsGamePlanSnapshot.stock_id == stock_id)
        .order_by(OptionsGamePlanSnapshot.as_of.desc())
        .limit(1)
    ).scalars().first()
