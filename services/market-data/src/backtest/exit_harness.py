"""T390-EXIT-HARNESS: replay the REAL _monitor_positions() over historical prices.

WHY THIS EXISTS — and why an ad-hoc simulation was tried first and thrown away.

The largest single loss bucket in paper trading is SWING stop-outs: 32 trades at -3.90%
average, **-$11,539**, larger than the entire account's net loss. The obvious question is
whether a wider SWING stop would help. Answering it needs a harness, and 2026-09-15 I first
tried the cheap path — a hand-written simulation walking daily bars with stop/target/breakeven
logic.

**IT FAILED ITS OWN CONTROL, and that failure is why this module exists.** Replaying each trade
with its OWN actual stop should reproduce the actual result. It did not:

    SWING   actual -25.1 pp   hand-written sim control  +2.8 pp   (28 pp too optimistic)
    GROWTH  actual -15.0 pp   hand-written sim control +40.7 pp   (56 pp too optimistic)

The cause, confirmed: the real engine has **10 exit paths** (stop_hit, target_reached,
trailing_stop, breakeven_stop, momentum_exit, momentum_fade, signal_exit, hold_stall_timeout,
time_stop, delisted) and takes **partial scale-outs** at partial_tp_pct/partial_tp2_pct rather
than exiting whole. The simulation modelled 4 paths and full exits, so it booked a clean +10%
where the real engine sells a tranche and lets the remainder run — sometimes into a loss.

**The lesson generalises: a counterfactual is only worth its control.** A re-implementation of
exit logic will drift from the real logic, and the drift is invisible without a control that
must reproduce reality.

So this harness does NOT re-implement anything. It drives the REAL, unmodified
`_monitor_positions()` with historical prices fed in as if they were live.

---

FIDELITY — what this harness models faithfully, and what it does not.

FAITHFUL (price-driven, which is what a stop-width question needs):
  * stop_hit, trailing_stop, breakeven_stop, target_reached, partial scale-outs, time_stop

NOT FAITHFUL (signal-driven):
  * signal_exit, momentum_exit, momentum_fade — `_monitor_positions()` queries the LATEST
    Signal row per symbol, which in a replay is TODAY's signal, not the one live at the
    historical moment. These paths will fire on the wrong data.

That is an honest, bounded gap: it is disclosed rather than silently left, matching
gate_harness.py's own precedent of naming its incomplete inputs. A stop-width sweep is still
valid because the exits it moves between are all in the faithful set — but any result that
hinges on a signal-driven exit must not be trusted.

DAILY BARS, NOT INTRADAY. The live engine re-checks every 5 minutes; this feeds one price per
day. Each day is probed LOW-first then CLOSE, so an intraday stop breach is caught, but the
ordering of a same-day stop-vs-target is unknowable and resolved conservatively (stop wins).
That biases AGAINST wider stops, so an improvement it reports is a floor, not a ceiling.

SAFETY. `_monitor_positions()` performs no commit/add/delete (verified: 0 commits, 0 adds, 1
flush, mutating ORM objects in-session only). This harness creates its scratch portfolio and
trade inside a transaction and ALWAYS rolls back in a finally block, so it cannot write to
production even on an exception.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dtime

from sqlalchemy import text

from db import SessionLocal, PaperPortfolio, PaperTrade

# A scratch portfolio must be identifiable if it ever escapes the rollback.
_SCRATCH_PREFIX = "__T390_EXIT_HARNESS_SCRATCH__"

# Bars to replay per trade. 60 covers GROWTH's max_hold_days; SWING's is 20.
_MAX_REPLAY_BARS = 70


@dataclass
class ReplayResult:
    trade_id: int
    symbol: str
    style: str
    entry_price: float
    actual_pct: float | None
    actual_reason: str | None
    replay_pct: float | None
    replay_reason: str | None
    bars_used: int


def _bars(session, stock_id: int, start: date, limit: int = _MAX_REPLAY_BARS):
    return session.execute(text("""
        SELECT ts::date AS d, high, low, close
        FROM prices
        WHERE stock_id = :sid AND timeframe = 'D1' AND ts::date >= :start
        ORDER BY ts LIMIT :lim
    """), {"sid": stock_id, "start": start, "lim": limit}).mappings().all()


def replay_trade(session, trade_row: dict, config_override: dict | None = None) -> ReplayResult:
    """Replay ONE historical trade through the real exit logic. Always rolls back."""
    from ..services.paper_trading_engine import _monitor_positions

    sym = trade_row["symbol"]
    entry = float(trade_row["entry_price"])
    style = trade_row["trading_style"] or "GROWTH"
    bars = _bars(session, trade_row["stock_id"], trade_row["ed"])

    res = ReplayResult(
        trade_id=trade_row["id"], symbol=sym, style=style, entry_price=entry,
        actual_pct=float(trade_row["pct_return"]) if trade_row["pct_return"] is not None else None,
        actual_reason=trade_row["exit_reason"],
        replay_pct=None, replay_reason=None, bars_used=len(bars),
    )
    if not bars:
        res.replay_reason = "nodata"
        return res

    cfg = {"trading_style": style, "market": trade_row.get("market") or "US"}
    if config_override:
        cfg.update(config_override)

    try:
        pf = PaperPortfolio(
            name=f"{_SCRATCH_PREFIX}{trade_row['id']}",
            initial_capital=1_000_000.0, current_cash=1_000_000.0, config=cfg,
            is_active=True,
        )
        session.add(pf)
        session.flush()

        pt = PaperTrade(
            portfolio_id=pf.id, stock_id=trade_row["stock_id"], symbol=sym,
            trading_style=style, entry_date=trade_row["ed"],
            # NOT NULL in the schema — the replay has no real intraday timestamp, so use the
            # entry date's midnight. Only ordering matters to _monitor_positions, not the clock.
            entry_time=datetime.combine(trade_row["ed"], dtime(0, 0)),
            entry_price=entry, shares=float(trade_row["shares"] or 100),
            entry_shares=float(trade_row["shares"] or 100),
            stop_loss=float(trade_row["stop_loss"]) if trade_row["stop_loss"] else None,
            take_profit=float(trade_row["take_profit"]) if trade_row["take_profit"] else None,
            current_stop=float(trade_row["stop_loss"]) if trade_row["stop_loss"] else None,
            current_price=entry, highest_price=entry, stage="open", hold_days=0,
        )
        session.add(pt)
        session.flush()

        for i, b in enumerate(bars):
            pt.hold_days = i
            # Probe LOW first so an intraday stop breach is caught, then CLOSE. Same-day
            # stop-vs-target ordering is unknowable from a daily bar; stop wins (conservative).
            for px in (float(b["low"]), float(b["close"])):
                _monitor_positions(session, pf, {sym: px})
                session.flush()
                if pt.stage != "open":
                    break
            if pt.stage != "open":
                break

        if pt.stage != "open":
            res.replay_pct = float(pt.pct_return) if pt.pct_return is not None else None
            res.replay_reason = pt.exit_reason
        else:
            last = float(bars[-1]["close"])
            res.replay_pct = 100.0 * (last - entry) / entry
            res.replay_reason = "still_open"
        return res
    finally:
        # UNCONDITIONAL. This harness must never write to production, even on an exception.
        session.rollback()


def closed_trades(session, style: str | None = None, limit: int = 500) -> list[dict]:
    sql = """
        SELECT t.id, t.symbol, t.stock_id, t.trading_style, t.entry_price, t.shares,
               t.stop_loss, t.take_profit, t.pct_return, t.exit_reason,
               t.entry_date::date AS ed, p.config->>'market' AS market
        FROM paper_trades t JOIN paper_portfolios p ON p.id = t.portfolio_id
        WHERE t.pnl IS NOT NULL AND t.stock_id IS NOT NULL
          AND t.entry_price > 0 AND t.entry_date IS NOT NULL
    """
    params: dict = {"lim": limit}
    if style:
        sql += " AND t.trading_style = :st"
        params["st"] = style
    sql += " ORDER BY t.id LIMIT :lim"
    return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


def run_control(style: str, limit: int = 500) -> dict:
    """Replay every closed trade with its OWN config. MUST approximate the actual result.

    This is the validity gate. A hand-written simulation failed exactly here (SWING +2.8pp
    against an actual -25.1pp), so no counterfactual from this module should be believed until
    this control passes.
    """
    with SessionLocal() as s:
        rows = closed_trades(s, style, limit)
        out = [replay_trade(s, r) for r in rows]
    actual = sum(r.actual_pct or 0.0 for r in out)
    replay = sum(r.replay_pct or 0.0 for r in out)
    matched = sum(1 for r in out if r.actual_reason == r.replay_reason)
    return {
        "style": style, "n": len(out),
        "actual_total_pp": round(actual, 1),
        "replay_total_pp": round(replay, 1),
        "abs_error_pp": round(abs(replay - actual), 1),
        "exit_reason_match": matched,
        "exit_reason_match_pct": round(100.0 * matched / max(len(out), 1), 1),
        "results": [asdict(r) for r in out],
    }


def sweep_stop_width(style: str, stop_atr_mults: list[float], limit: int = 500) -> dict:
    """What would different stop widths have produced, using the REAL exit logic?"""
    with SessionLocal() as s:
        rows = closed_trades(s, style, limit)
        out = {}
        for m in stop_atr_mults:
            res = [replay_trade(s, r, {"stop_loss_atr_mult": m}) for r in rows]
            tot = sum(x.replay_pct or 0.0 for x in res)
            reasons: dict[str, int] = {}
            for x in res:
                reasons[x.replay_reason or "?"] = reasons.get(x.replay_reason or "?", 0) + 1
            out[str(m)] = {"total_pp": round(tot, 1), "n": len(res), "exits": reasons}
    return {"style": style, "sweep": out}
