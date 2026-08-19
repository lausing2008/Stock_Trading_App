"""IF-01: persisted VaR/CVaR + stress-test snapshots for a user's real position book.

portfolio-optimizer has no DB access of its own (a pure HTTP-consumer service — see
portfolio-optimizer/src/api/risk.py's own module docstring), so it can compute real VaR/CVaR/
stress-test figures on demand but can never persist them itself. market-data DOES have real DB
access and already knows about a user's real UserPosition holdings, so this module calls
portfolio-optimizer's risk endpoints over HTTP (the same cross-service pattern already
established for /portfolio/optimize's own _fetch_closes(), and for OptionsFlowSnapshot's/
SectorRotationSnapshot's own compute-elsewhere-then-persist-here architecture) and writes the
result into the two new tables (PortfolioRiskMetric, StressTestResult).

Currently triggered on-demand (a user clicking "Save Risk Snapshot" / "Run Stress Test"), not
yet a scheduled daily job — see this tracker item's own note for why that phase (and the full
frontend dashboard) were deliberately deferred to keep this fix reviewable in one pass.
"""
import json
from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from common.config import get_settings
from db import PortfolioRiskMetric, StressTestResult, User, UserPosition, get_session
from .auth import get_current_user

router = APIRouter(prefix="/risk-snapshots", tags=["risk-snapshots"])
_settings = get_settings()


def _user_symbols_and_weights(session: Session, user_id: int) -> tuple[list[str], list[float]]:
    """Build a (symbols, weights) pair from the user's REAL current UserPosition holdings,
    weighted by market value (shares * avg_cost — the same cost-basis convention
    positions.py's own PositionOut already surfaces; a live-price weighting would need an
    extra price fetch this snapshot doesn't otherwise need)."""
    rows = session.execute(
        select(UserPosition.symbol, UserPosition.shares, UserPosition.avg_cost)
        .where(UserPosition.user_id == user_id)
    ).all()
    symbols, weights = [], []
    for symbol, shares, avg_cost in rows:
        value = float(shares or 0) * float(avg_cost or 0)
        if value > 0:
            symbols.append(symbol)
            weights.append(value)
    return symbols, weights


@router.post("/var")
def save_var_snapshot(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Compute today's VaR/CVaR for the user's real position book and persist it — the
    'never persisted' half of this tracker item's own finding."""
    symbols, weights = _user_symbols_and_weights(session, user.id)
    if len(symbols) < 2:
        raise HTTPException(status_code=400, detail="At least 2 positions with real market value required")

    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{_settings.portfolio_optimizer_url}/portfolio-risk/risk",
                params={"symbols": ",".join(symbols[:10]), "weights": ",".join(str(w) for w in weights[:10])},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"portfolio-optimizer risk call failed: {exc}")

    hv = data.get("historical_var") or {}
    today = date.today()
    row = {
        "user_id": user.id,
        "as_of": today,
        "symbols_json": json.dumps(sorted(data.get("symbols", symbols))),
        "portfolio_beta": data.get("portfolio_beta"),
        "var_95_pct": data.get("var_95_pct"),
        "var_95_1d_pct": hv.get("var_95_1d_pct"),
        "var_99_1d_pct": hv.get("var_99_1d_pct"),
        "var_95_10d_pct": hv.get("var_95_10d_pct"),
        "var_99_10d_pct": hv.get("var_99_10d_pct"),
        "cvar_95_1d_pct": hv.get("cvar_95_1d_pct"),
        "cvar_99_1d_pct": hv.get("cvar_99_1d_pct"),
        "cvar_95_10d_pct": hv.get("cvar_95_10d_pct"),
        "cvar_99_10d_pct": hv.get("cvar_99_10d_pct"),
        "sample_size": hv.get("sample_size"),
    }
    stmt = pg_insert(PortfolioRiskMetric).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "as_of"],
        set_={k: v for k, v in row.items() if k not in ("user_id", "as_of")},
    )
    session.execute(stmt)
    session.commit()
    return {"saved": True, "as_of": today.isoformat(), **row}


@router.get("/var/history")
def var_snapshot_history(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    days: int = 90,
):
    """Real, persisted VaR time series for the current user — what portfolio_risk() alone
    could never answer before this fix (no history, only ever the latest live computation)."""
    rows = session.execute(
        select(PortfolioRiskMetric)
        .where(PortfolioRiskMetric.user_id == user.id)
        .order_by(PortfolioRiskMetric.as_of.desc())
        .limit(days)
    ).scalars().all()
    return [
        {
            "as_of": r.as_of.isoformat(),
            "symbols": json.loads(r.symbols_json),
            "portfolio_beta": r.portfolio_beta,
            "var_95_pct": r.var_95_pct,
            "var_95_1d_pct": r.var_95_1d_pct,
            "var_99_1d_pct": r.var_99_1d_pct,
            "var_95_10d_pct": r.var_95_10d_pct,
            "var_99_10d_pct": r.var_99_10d_pct,
            "cvar_95_1d_pct": r.cvar_95_1d_pct,
            "cvar_99_1d_pct": r.cvar_99_1d_pct,
            "cvar_95_10d_pct": r.cvar_95_10d_pct,
            "cvar_99_10d_pct": r.cvar_99_10d_pct,
            "sample_size": r.sample_size,
        }
        for r in reversed(rows)
    ]


@router.post("/stress-test")
def save_stress_test(
    scenario: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Run one predefined stress scenario against the user's real position book and persist
    the result — closing the 'stress testing is entirely absent' half of this tracker item."""
    symbols, weights = _user_symbols_and_weights(session, user.id)
    if len(symbols) < 2:
        raise HTTPException(status_code=400, detail="At least 2 positions with real market value required")

    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{_settings.portfolio_optimizer_url}/portfolio-risk/stress-test",
                params={
                    "symbols": ",".join(symbols[:10]),
                    "weights": ",".join(str(w) for w in weights[:10]),
                    "scenario": scenario,
                },
            )
            if r.status_code == 400:
                raise HTTPException(status_code=400, detail=r.json().get("detail", "Invalid scenario"))
            r.raise_for_status()
            data = r.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"portfolio-optimizer stress-test call failed: {exc}")

    today = date.today()
    row = {
        "user_id": user.id,
        "as_of": today,
        "scenario": data["scenario"],
        "scenario_label": data["label"],
        "symbols_json": json.dumps(sorted(data.get("symbols", symbols))),
        "benchmark_move_pct": data["benchmark_move_pct"],
        "portfolio_impact_pct": data["portfolio_impact_pct"],
        "per_position_impact_json": json.dumps(data["per_position_impact_pct"]),
    }
    stmt = pg_insert(StressTestResult).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "as_of", "scenario"],
        set_={k: v for k, v in row.items() if k not in ("user_id", "as_of", "scenario")},
    )
    session.execute(stmt)
    session.commit()
    return {"saved": True, "as_of": today.isoformat(), **row}


@router.get("/stress-test/history")
def stress_test_history(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    days: int = 90,
):
    rows = session.execute(
        select(StressTestResult)
        .where(StressTestResult.user_id == user.id)
        .order_by(StressTestResult.as_of.desc())
        .limit(days * 5)  # up to 5 scenarios per day
    ).scalars().all()
    return [
        {
            "as_of": r.as_of.isoformat(),
            "scenario": r.scenario,
            "scenario_label": r.scenario_label,
            "symbols": json.loads(r.symbols_json),
            "benchmark_move_pct": r.benchmark_move_pct,
            "portfolio_impact_pct": r.portfolio_impact_pct,
            "per_position_impact_pct": json.loads(r.per_position_impact_json),
        }
        for r in reversed(rows)
    ]
