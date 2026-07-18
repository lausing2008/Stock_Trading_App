"""Portfolio risk analytics — correlation, VaR, beta, sector concentration.

T233-ARCH-PORTFOLIO-CONSOLIDATE: moved verbatim (same route path, same response shape — zero
frontend changes needed) from services/market-data/src/api/portfolio.py. market-data had direct
DB access to Price/Stock; portfolio-optimizer has none (it's a pure HTTP-consumer service, see
routes.py's own _fetch_closes()), so the two DB queries this endpoint used
(select(Price...).join(Stock...) for closes, select(Stock.symbol, Stock.sector, Stock.market)
for metadata) are replaced with HTTP calls to market-data's already-existing GET /stocks/{symbol}
and GET /stocks/{symbol}/prices — the exact same two endpoints routes.py's own _fetch_closes()
already calls for the /portfolio/optimize path, so this isn't a new integration pattern for this
service, just applying the one it already has to a second endpoint.
"""
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query

from common.config import get_settings
from common.jwt_auth import get_current_username

router = APIRouter(prefix="/portfolio-risk", tags=["portfolio-risk"])
_settings = get_settings()

# Market benchmark tickers
_BENCH = {"US": "SPY", "HK": "^HSI"}


def _fetch_returns(symbols: list[str], days: int = 60) -> pd.DataFrame:
    """Fetch daily closes from market-data's own prices endpoint and return a DataFrame of
    daily % returns — same shape/behavior as the original direct-DB version, just fetched over
    HTTP instead (this service has no DB access of its own)."""
    start = (date.today() - timedelta(days=days)).isoformat()
    series: dict[str, pd.Series] = {}
    with httpx.Client(timeout=30) as c:
        for sym in symbols:
            try:
                r = c.get(f"{_settings.market_data_url}/stocks/{sym}/prices", params={"start": start, "limit": 5000})
                if r.status_code != 200:
                    continue
                data = r.json()
                if not data or len(data) < 5:
                    continue
                df = pd.DataFrame(data)
                df["ts"] = pd.to_datetime(df["ts"])
                closes = df.set_index("ts")["close"].astype(float)
                series[sym] = closes.pct_change().dropna()
            except Exception:
                continue
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna()


def _fetch_stock_meta(symbols: list[str]) -> dict[str, dict]:
    """Fetch sector/market for each symbol via market-data's GET /stocks/{symbol} — the same
    endpoint _fetch_closes-adjacent code elsewhere in this service already relies on."""
    meta: dict[str, dict] = {}
    with httpx.Client(timeout=15) as c:
        for sym in symbols:
            try:
                r = c.get(f"{_settings.market_data_url}/stocks/{sym}")
                if r.status_code == 200:
                    d = r.json()
                    meta[sym] = {"sector": d.get("sector") or "Unknown", "market": d.get("market", "")}
            except Exception:
                continue
    return meta


def _beta(stock_rets: pd.Series, bench_rets: pd.Series) -> float:
    """Compute beta of stock_rets vs bench_rets on common dates."""
    s, b = stock_rets.align(bench_rets, join="inner")
    if len(s) < 5:
        return 1.0
    sv = np.asarray(s, dtype=float).ravel()
    bv = np.asarray(b, dtype=float).ravel()
    cov = float(np.cov(sv, bv)[0, 1])
    var = float(np.var(bv))
    return cov / var if var > 0 else 1.0


@router.get("/risk")
def portfolio_risk(
    symbols: str = Query(..., description="Comma-separated stock symbols"),
    weights: str | None = Query(None, description="Comma-separated position weights (any units, auto-normalised)"),
    _user: str = Depends(get_current_username),
):
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(sym_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 symbols required")
    if len(sym_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 symbols per request")

    # Normalise weights
    if weights:
        raw_w = [abs(float(w)) for w in weights.split(",") if w.strip()]
        if len(raw_w) != len(sym_list):
            raise HTTPException(status_code=400, detail="weights count must match symbols count")
    else:
        raw_w = [1.0] * len(sym_list)
    total_w = sum(raw_w) or 1.0
    w_list = [w / total_w for w in raw_w]

    # Fetch price history
    df = _fetch_returns(sym_list)
    available = [s for s in sym_list if s in df.columns]
    if len(available) < 2:
        raise HTTPException(status_code=422, detail="Insufficient price history for at least 2 symbols")

    # Align weights with available symbols
    w_map = {sym: w for sym, w in zip(sym_list, w_list)}
    avail_w = [w_map[s] for s in available]
    avail_total = sum(avail_w) or 1.0
    avail_w = [w / avail_total for w in avail_w]
    df = df[available]

    # Correlation matrix
    corr = df.corr()

    # Determine benchmark — if any HK stock, use HSI; else SPY
    stock_meta = _fetch_stock_meta(available)
    market_map = {s: stock_meta.get(s, {}).get("market", "") for s in available}
    sector_map = {s: stock_meta.get(s, {}).get("sector", "Unknown") for s in available}
    hk_count = sum(1 for m in market_map.values() if "HK" in m.upper())
    bench_ticker = _BENCH["HK"] if hk_count > len(available) // 2 else _BENCH["US"]

    try:
        bench_raw = yf.download(bench_ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if isinstance(bench_raw.columns, pd.MultiIndex):
            bench_raw = bench_raw["Close"]
        else:
            bench_raw = bench_raw["Close"] if "Close" in bench_raw else bench_raw.iloc[:, 0]
        bench_rets = bench_raw.squeeze().pct_change().dropna()
        bench_rets.index = pd.to_datetime(bench_rets.index).tz_localize(None)
    except Exception:
        bench_rets = pd.Series(dtype=float)

    betas: dict[str, float] = {}
    for sym in available:
        if len(bench_rets) > 0:
            stock_rets = df[sym].copy()
            stock_rets.index = pd.to_datetime(stock_rets.index).tz_localize(None)
            betas[sym] = _beta(stock_rets, bench_rets)
        else:
            betas[sym] = 1.0

    portfolio_beta = float(sum(betas[s] * w for s, w in zip(available, avail_w)))

    # Sector concentration
    sector_weights: dict[str, float] = {}
    for sym, w in zip(available, avail_w):
        sec = sector_map.get(sym, "Unknown")
        sector_weights[sec] = sector_weights.get(sec, 0.0) + w

    # Parametric 1-day VaR at 95% confidence
    port_rets = df.dot(pd.Series(dict(zip(available, avail_w))))
    port_vol = float(port_rets.std())
    var_95_pct = port_vol * 1.645 * 100  # expressed as percentage of portfolio value

    # Warnings
    warnings: list[str] = []
    sorted_pos = sorted(zip(available, avail_w), key=lambda x: -x[1])
    if len(sorted_pos) >= 2 and sorted_pos[0][1] + sorted_pos[1][1] > 0.5:
        warnings.append(
            f"Top 2 holdings ({sorted_pos[0][0]}, {sorted_pos[1][0]}) are {((sorted_pos[0][1]+sorted_pos[1][1])*100):.0f}% of portfolio"
        )
    corr_vals = corr.values
    n = len(available)
    for i in range(n):
        for j in range(i + 1, n):
            c = float(corr_vals[i][j])
            if c > 0.8:
                warnings.append(f"High correlation ({c:.2f}) between {available[i]} and {available[j]}")
    if portfolio_beta > 1.5:
        warnings.append(f"Portfolio beta {portfolio_beta:.2f} — significantly amplifies market moves")
    if var_95_pct > 4.0:
        warnings.append(f"High daily VaR ({var_95_pct:.1f}%) — consider reducing position sizes")
    top_sector_pct = max(sector_weights.values()) * 100
    top_sector = max(sector_weights, key=lambda k: sector_weights[k])
    if top_sector_pct > 60:
        warnings.append(f"{top_sector_pct:.0f}% concentration in {top_sector} — consider diversifying")

    return {
        "symbols": available,
        "weights": avail_w,
        "correlation": corr.values.tolist(),
        "betas": betas,
        "portfolio_beta": round(portfolio_beta, 3),
        "sector_weights": {k: round(v, 4) for k, v in sector_weights.items()},
        "var_95_pct": round(var_95_pct, 2),
        "benchmark": bench_ticker,
        "warnings": warnings,
    }
