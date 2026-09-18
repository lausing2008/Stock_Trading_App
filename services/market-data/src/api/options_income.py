"""T398-OPTIONS-INCOME-ENGINE — screening + autonomous-portfolio API.

GET /options-income/candidates is a research tool (Advanced-tier+): the same ranked
covered-call/CSP list the autonomous engine itself trades from, for a user who wants to act
manually instead. The portfolio endpoints below manage the autonomous side, mirroring
paper_portfolio.py's own list/create/positions shape.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_session
from db.models import OptionsIncomeEquityCurve, OptionsIncomePortfolio, OptionsIncomePosition, User
from .auth import get_admin_user, get_advanced_user
from common.logging import get_logger

log = get_logger("options_income_api")

router = APIRouter(prefix="/options-income", tags=["options-income"])


@router.get("/candidates")
def get_income_candidates(
    strategy: str | None = Query(None, description="COVERED_CALL | CASH_SECURED_PUT (both if omitted)"),
    min_annualized_yield_pct: float = Query(0.0, ge=0.0),
    _: User = Depends(get_advanced_user),
    session: Session = Depends(get_session),
) -> dict:
    """Ranked covered-call/CSP candidates across the income universe, from each symbol's
    latest archived EOD chain — the exact list the autonomous engine itself trades from."""
    from ..services.options_income_engine import rank_income_candidates

    strategies = None
    if strategy:
        strategy = strategy.upper()
        if strategy not in ("COVERED_CALL", "CASH_SECURED_PUT"):
            raise HTTPException(status_code=400, detail="strategy must be COVERED_CALL or CASH_SECURED_PUT")
        strategies = [strategy]

    candidates = rank_income_candidates(session, strategies=strategies)
    candidates = [c for c in candidates if c["annualized_yield_pct"] >= min_annualized_yield_pct]

    # Staleness is surfaced as first-class response metadata, not buried per-row: the option
    # chain going stale is the single failure mode that most silently degrades every candidate
    # at once (a scheduler misfire during an outage left it 5 days behind once already), and
    # nothing on the page could previously tell a fresh candidate from an old one.
    days_stale = max((c.get("days_stale", 0) for c in candidates), default=None)
    data_as_of = max((c["as_of"] for c in candidates), default=None)
    return {
        "candidates": candidates,
        "count": len(candidates),
        "data_as_of": data_as_of,
        "days_stale": days_stale,
        "is_stale": bool(days_stale is not None and days_stale >= 2),
    }


@router.get("/portfolios")
def list_income_portfolios(
    _: User = Depends(get_advanced_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    portfolios = session.execute(
        select(OptionsIncomePortfolio).order_by(OptionsIncomePortfolio.id)
    ).scalars().all()

    result = []
    for p in portfolios:
        open_positions = session.execute(
            select(OptionsIncomePosition).where(
                OptionsIncomePosition.portfolio_id == p.id, OptionsIncomePosition.stage == "open",
            )
        ).scalars().all()
        closed_positions = session.execute(
            select(OptionsIncomePosition).where(
                OptionsIncomePosition.portfolio_id == p.id, OptionsIncomePosition.stage == "closed",
            )
        ).scalars().all()
        latest_curve = session.execute(
            select(OptionsIncomeEquityCurve)
            .where(OptionsIncomeEquityCurve.portfolio_id == p.id)
            .order_by(OptionsIncomeEquityCurve.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        equity = latest_curve.equity if latest_curve else float(p.current_cash)
        wins = [pos for pos in closed_positions if (pos.pnl or 0) > 0]
        assigned = [pos for pos in closed_positions if pos.assigned]

        result.append({
            "id": p.id,
            "name": p.name,
            "initial_capital": p.initial_capital,
            "current_cash": round(float(p.current_cash), 2),
            "current_equity": round(equity, 2),
            "total_return_pct": round((equity / p.initial_capital - 1) * 100, 2),
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "win_rate_pct": round(len(wins) / max(len(closed_positions), 1) * 100, 1),
            "assignment_rate_pct": round(len(assigned) / max(len(closed_positions), 1) * 100, 1),
            "total_premium_collected": round(sum(float(pos.total_premium_collected) for pos in (open_positions + closed_positions)), 2),
            "is_active": p.is_active,
            "config": p.config,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return result


@router.post("/portfolios/create")
def create_income_portfolio(
    body: dict,
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    from ..services.options_income_engine import _DEFAULT_INCOME_CONFIG

    name = str(body.get("name", "Options Income Portfolio")).strip() or "Options Income Portfolio"
    initial_capital = float(body.get("initial_capital", 50_000))
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be > 0")

    cfg = {**_DEFAULT_INCOME_CONFIG, **(body.get("config") or {})}
    p = OptionsIncomePortfolio(
        name=name, initial_capital=initial_capital, current_cash=initial_capital,
        config=cfg, is_active=True,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    log.info("options_income.portfolio_created", portfolio_id=p.id, name=name, capital=initial_capital)
    return {"ok": True, "portfolio_id": p.id, "name": p.name, "config": p.config}


@router.get("/portfolios/{portfolio_id}/positions")
def get_income_positions(
    portfolio_id: int,
    stage: str | None = Query(None, description="open | closed"),
    _: User = Depends(get_advanced_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    portfolio = session.get(OptionsIncomePortfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="portfolio not found")

    query = select(OptionsIncomePosition).where(OptionsIncomePosition.portfolio_id == portfolio_id)
    if stage:
        query = query.where(OptionsIncomePosition.stage == stage)
    positions = session.execute(query.order_by(OptionsIncomePosition.entry_date.desc())).scalars().all()

    # AUD-T398-ASSIGNMENTRISK: an OPEN position going in-the-money is the one thing a holder
    # most needs to know before expiry, and nothing surfaced it — the page showed entry-time
    # figures only, so a put sitting well below its strike looked identical to a safe one.
    # Priced against the CURRENT underlying, not the entry price.
    live_prices: dict[str, float] = {}
    open_syms = sorted({p.symbol for p in positions if p.stage == "open"})
    if open_syms:
        try:
            from ..services.paper_trading_engine import _fetch_live_prices
            live_prices = _fetch_live_prices(open_syms) or {}
        except Exception:
            live_prices = {}  # fail open — never break the position list over a quote fetch

    def _risk(pos) -> dict:
        px = live_prices.get(pos.symbol)
        if pos.stage != "open" or not px:
            return {"live_price": None, "is_itm": None, "cushion_pct": None, "days_to_expiry": None}
        strike = float(pos.strike)
        itm = px > strike if pos.strategy == "COVERED_CALL" else px < strike
        cushion = ((strike - px) / px * 100) if pos.strategy == "COVERED_CALL" else ((px - strike) / px * 100)
        return {
            "live_price": round(px, 2),
            "is_itm": bool(itm),
            "cushion_pct": round(cushion, 2),
            "days_to_expiry": (pos.expiry - date.today()).days,
        }

    return [{
        **_risk(pos),
        "id": pos.id,
        "symbol": pos.symbol,
        "strategy": pos.strategy,
        "option_symbol": pos.option_symbol,
        "strike": pos.strike,
        "expiry": pos.expiry.isoformat(),
        "contracts": pos.contracts,
        "entry_date": pos.entry_date.isoformat(),
        "underlying_entry_price": pos.underlying_entry_price,
        "delta_at_entry": pos.delta_at_entry,
        "premium_per_contract": pos.premium_per_contract,
        "total_premium_collected": pos.total_premium_collected,
        "collateral_reserved": pos.collateral_reserved,
        "stage": pos.stage,
        "close_date": pos.close_date.isoformat() if pos.close_date else None,
        "underlying_close_price": pos.underlying_close_price,
        "assigned": pos.assigned,
        "pnl": pos.pnl,
        "pct_return_on_collateral": pos.pct_return_on_collateral,
        "close_reason": pos.close_reason,
    } for pos in positions]


@router.get("/portfolios/{portfolio_id}/equity-curve")
def get_income_equity_curve(
    portfolio_id: int,
    _: User = Depends(get_advanced_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    portfolio = session.get(OptionsIncomePortfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="portfolio not found")
    curve = session.execute(
        select(OptionsIncomeEquityCurve)
        .where(OptionsIncomeEquityCurve.portfolio_id == portfolio_id)
        .order_by(OptionsIncomeEquityCurve.date)
    ).scalars().all()
    return [{
        "date": c.date.isoformat(), "equity": c.equity, "cash": c.cash,
        "open_positions_count": c.open_positions_count, "collateral_committed": c.collateral_committed,
        # AUD-A19-EQUITYMIXEDBASIS: WHICH definition of equity this point used. Points with
        # different values here are NOT comparable — the difference between them includes a
        # change of definition, not only a change in the market. NULL means a row predating the
        # column whose basis could not be derived from its own arithmetic.
        "equity_basis": c.equity_basis,
    } for c in curve]


@router.post("/run-step")
def run_income_step_now(
    _: User = Depends(get_admin_user),
) -> dict:
    """Admin-only manual trigger — mirrors paper-portfolio's own /run-step escape hatch for
    testing without waiting for the scheduled cadence."""
    from ..services.options_income_engine import run_options_income_step
    # AUD-A07: returns the engine's own verdict rather than an unconditional {"ok": True}. If
    # the scheduled run is already in flight this call is SKIPPED, and reporting that as success
    # would tell an admin their manual trigger did something it did not.
    return run_options_income_step()
