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

Also persists iv_rank_1y from the SAME get_iv_rank() call (no extra fetch) — a genuinely
different, complementary signal from expected_move_pct: expected_move_pct says HOW FAR the
market expects this symbol to move; iv_rank_1y says whether that IV reading is CHEAP OR
EXPENSIVE relative to this symbol's own trailing 1-year IV range (0-100 percentile). Captured
independently of expected_move_pct's own `volatility > 0` gate, since iv_rank_1y is still a
real, useful reading even on the rare occasion `volatility` itself comes back null/zero.

AUD-GREEKS: also persists real per-strike put/call delta/gamma/theta/vega/vanna/charm from
Unusual Whales' get_greeks(symbol, expiry) — one extra call per DISTINCT expiry actually in use
(put_exp/call_exp are frequently the same expiry, so usually a single call, never more than 2),
matched in-memory to the exact strike compute_options_game_plan() already selected. Closes a gap
this app's own Options Trading Guide explicitly documents ("no real per-contract Greeks beyond
implied volatility are shown"). Isolated in its own try/except — a failure here only costs these
12 fields, never the rest of the snapshot (the put/call strike/expiry/price legs above already
come from the options chain fetch, independent of whether Unusual Whales is even configured).
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
    iv_rank_1y: float | None = None
    put_delta: float | None = None
    put_gamma: float | None = None
    put_theta: float | None = None
    put_vega: float | None = None
    put_vanna: float | None = None
    put_charm: float | None = None
    call_delta: float | None = None
    call_gamma: float | None = None
    call_theta: float | None = None
    call_vega: float | None = None
    call_vanna: float | None = None
    call_charm: float | None = None


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
        iv_rank_1y: float | None = None
        try:
            from math import sqrt
            from . import unusual_whales as _uw
            _iv_data = _uw.get_iv_rank(symbol)
            if _iv_data is not None:
                iv_rank_1y = _iv_data.iv_rank_1y
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

        # AUD-GREEKS: real per-strike Greeks for the EXACT put/call strike this snapshot
        # already selected above — one get_greeks() call per distinct expiry actually in use
        # (put_exp/call_exp are often the same expiry, so this is usually a single extra call,
        # never more than 2), then a plain in-memory match on strike. Isolated in its own
        # try/except — a failure here only costs the 12 Greek fields, never the rest of the
        # snapshot (which already has real put/call strike/expiry/price from the options chain
        # fetched above regardless of whether Unusual Whales is even configured).
        put_greeks: dict = {}
        call_greeks: dict = {}
        try:
            from . import unusual_whales as _uw

            def _match_strike(rows: list, strike: float | None) -> dict:
                if strike is None:
                    return {}
                for row in rows:
                    if row.strike is not None and abs(row.strike - strike) < 0.005:
                        return row
                return {}

            _greeks_cache: dict[str, list] = {}

            def _rows_for_expiry(expiry: str) -> list:
                # NOTE: dict.setdefault()'s default-value argument is evaluated eagerly
                # regardless of whether the key already exists — using it directly here would
                # call get_greeks() twice even when put_exp == call_exp (the common real case).
                # An explicit if/else avoids that.
                if expiry not in _greeks_cache:
                    _greeks_cache[expiry] = _uw.get_greeks(symbol, expiry)
                return _greeks_cache[expiry]

            if put.get("strike") is not None and put_exp:
                put_greeks = _match_strike(_rows_for_expiry(put_exp), put.get("strike"))
            if call.get("strike") is not None and call_exp:
                call_greeks = _match_strike(_rows_for_expiry(call_exp), call.get("strike"))
        except Exception as exc:
            log.warning("options_game_plan_snapshot.greeks_failed", symbol=symbol, error=str(exc))

        def _greek(row, side: str, name: str):
            return getattr(row, f"{side}_{name}", None) if row else None

        return OptionsGamePlanResult(
            underlying_close=round(current_price, 4),
            stop_loss=stop_loss,
            take_profit=take_profit,
            expected_move_pct=expected_move_pct,
            expected_move_dte=expected_move_dte,
            iv_rank_1y=iv_rank_1y,
            put_delta=_greek(put_greeks, "put", "delta"),
            put_gamma=_greek(put_greeks, "put", "gamma"),
            put_theta=_greek(put_greeks, "put", "theta"),
            put_vega=_greek(put_greeks, "put", "vega"),
            put_vanna=_greek(put_greeks, "put", "vanna"),
            put_charm=_greek(put_greeks, "put", "charm"),
            call_delta=_greek(call_greeks, "call", "delta"),
            call_gamma=_greek(call_greeks, "call", "gamma"),
            call_theta=_greek(call_greeks, "call", "theta"),
            call_vega=_greek(call_greeks, "call", "vega"),
            call_vanna=_greek(call_greeks, "call", "vanna"),
            call_charm=_greek(call_greeks, "call", "charm"),
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
        iv_rank_1y=result.iv_rank_1y,
        put_delta=result.put_delta,
        put_gamma=result.put_gamma,
        put_theta=result.put_theta,
        put_vega=result.put_vega,
        put_vanna=result.put_vanna,
        put_charm=result.put_charm,
        call_delta=result.call_delta,
        call_gamma=result.call_gamma,
        call_theta=result.call_theta,
        call_vega=result.call_vega,
        call_vanna=result.call_vanna,
        call_charm=result.call_charm,
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
