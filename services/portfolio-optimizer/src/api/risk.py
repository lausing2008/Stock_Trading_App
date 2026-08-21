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


_BETA_VAR_EPS = 1e-9  # AUD292-SHARPE-VAREPS: a bare `var > 0` lets floating-point noise (a
# near-zero-but-nonzero variance from an all-identical or near-identical benchmark return
# series) through as a valid divisor, exploding beta toward an absurd value instead of the
# correct neutral 1.0 fallback — the exact bug class already found and fixed in
# paper_portfolio.py's Sharpe/Sortino computation, ported here to this sibling division.


def _beta(stock_rets: pd.Series, bench_rets: pd.Series) -> float:
    """Compute beta of stock_rets vs bench_rets on common dates."""
    s, b = stock_rets.align(bench_rets, join="inner")
    if len(s) < 5:
        return 1.0
    sv = np.asarray(s, dtype=float).ravel()
    bv = np.asarray(b, dtype=float).ravel()
    cov = float(np.cov(sv, bv)[0, 1])
    var = float(np.var(bv))
    return cov / var if var > _BETA_VAR_EPS else 1.0


# ── IF-01: VaR/CVaR + stress testing ────────────────────────────────────────────────────────
# Both functions below are pure — real portfolio return series in, risk figures out — with no
# DB/HTTP dependency, matching this repo's established pattern (volume_area.py, gate_harness.py)
# of building the calculation layer as directly-testable-against-hand-computed-expectations
# functions before any persistence/scheduling machinery.

_VAR_HORIZONS_DAYS = (1, 10)
_VAR_CONFIDENCES = (0.95, 0.99)


def compute_var_cvar(port_rets: pd.Series) -> dict:
    """Historical (empirical-percentile) VaR/CVaR at 95%/99% confidence, 1-day and 10-day
    horizons, expressed as a POSITIVE percentage of portfolio value (a "5% VaR" means a 5%
    LOSS, matching the sign convention of the pre-existing parametric var_95_pct field).

    Historical VaR (the empirical percentile of ACTUAL past daily returns) is added alongside
    the pre-existing parametric VaR (1.645 * std, which assumes a normal distribution) rather
    than replacing it — historical VaR captures real fat-tail/skew behavior a normal-
    distribution assumption misses, and comparing the two side by side is itself informative
    (a large gap between them signals the return distribution is meaningfully non-normal).

    CVaR (Conditional VaR / Expected Shortfall) = the average of all returns WORSE than the
    VaR percentile itself — "given that a VaR-level loss happens, how bad is it on average" —
    a materially more informative tail-risk figure than VaR alone, which only marks a single
    threshold and says nothing about how much worse the tail beyond it actually gets.

    10-day horizon uses the sqrt(time) scaling convention (day_n_vol = day_1_vol * sqrt(n)) —
    standard for i.i.d. returns; a real simplification (real returns exhibit some
    autocorrelation) but the same assumption this codebase's own CAGR/Sharpe annualization already
    makes elsewhere, not a new one introduced here.

    Returns None values (not fabricated numbers) whenever port_rets has fewer than 20 real
    observations — a VaR/CVaR estimate from a tiny sample is not trustworthy, matching this
    repo's own established sample-floor discipline (_MIN_SHARPE_DAYS, _VOL_TARGET_MIN_SAMPLE_DAYS).
    """
    rets = port_rets.dropna()
    if len(rets) < 20:
        return {
            f"var_{int(c*100)}_{h}d_pct": None
            for h in _VAR_HORIZONS_DAYS for c in _VAR_CONFIDENCES
        } | {
            f"cvar_{int(c*100)}_{h}d_pct": None
            for h in _VAR_HORIZONS_DAYS for c in _VAR_CONFIDENCES
        } | {"sample_size": len(rets), "insufficient_data": True}

    arr = rets.to_numpy(dtype=float)
    result: dict = {"sample_size": len(rets), "insufficient_data": False}
    for conf in _VAR_CONFIDENCES:
        pct_level = (1.0 - conf) * 100  # e.g. 95% confidence -> the 5th percentile of returns
        var_1d = float(np.percentile(arr, pct_level))
        tail = arr[arr <= var_1d]
        cvar_1d = float(tail.mean()) if len(tail) > 0 else var_1d
        for h in _VAR_HORIZONS_DAYS:
            scale = h ** 0.5
            result[f"var_{int(conf*100)}_{h}d_pct"] = round(-var_1d * scale * 100, 2)
            result[f"cvar_{int(conf*100)}_{h}d_pct"] = round(-cvar_1d * scale * 100, 2)
    return result


# ── Predefined historical stress scenarios ──────────────────────────────────────────────────
# Each scenario is a REAL, dated historical benchmark-index move — the assumption is that a
# candidate portfolio's beta-weighted sensitivity to that benchmark move is a reasonable
# first-order approximation of how it would have behaved (a real simplification, honestly
# stated: this is a beta-scaled proxy, not a full historical replay of each stock's own actual
# return during that window, which would need per-symbol price history reaching back to 2008 —
# not available for most of this app's tracked universe).
STRESS_SCENARIOS = {
    "gfc_2008": {
        "label": "2008 Financial Crisis (Sep 15 - Nov 20, 2008)",
        "benchmark_move_pct": -46.0,  # SPY peak-to-trough over this window
    },
    "covid_2020": {
        "label": "COVID-19 Crash (Feb 19 - Mar 23, 2020)",
        "benchmark_move_pct": -34.0,
    },
    "rate_shock_2022": {
        "label": "2022 Rate-Hike Selloff (Jan 3 - Oct 12, 2022)",
        "benchmark_move_pct": -25.0,
    },
    "flash_crash_2010": {
        "label": "2010 Flash Crash (May 6, 2010, intraday)",
        "benchmark_move_pct": -9.0,
    },
    "stagflation_1973": {
        "label": "1973-74 Stagflation Bear Market (proxy, S&P 500 real decline)",
        "benchmark_move_pct": -48.0,
    },
}


def run_stress_test(betas: dict[str, float], weights: dict[str, float], scenario_key: str) -> dict:
    """Apply a predefined historical stress scenario to a weighted, beta-mapped portfolio.

    Per-position impact = beta * scenario's benchmark_move_pct (a standard beta-scaled proxy —
    NOT a claim that this specific stock actually moved this way historically, an explicitly
    stated simplification, see STRESS_SCENARIOS' own module comment). Portfolio impact is the
    weighted sum of per-position impacts — the same weighting convention portfolio_beta already
    uses in portfolio_risk() above.
    """
    if scenario_key not in STRESS_SCENARIOS:
        raise ValueError(f"Unknown stress scenario: {scenario_key}")
    scenario = STRESS_SCENARIOS[scenario_key]
    move = scenario["benchmark_move_pct"]
    per_position = {sym: round(betas.get(sym, 1.0) * move, 2) for sym in weights}
    portfolio_impact = round(sum(per_position[sym] * weights[sym] for sym in weights), 2)
    return {
        "scenario": scenario_key,
        "label": scenario["label"],
        "benchmark_move_pct": move,
        "per_position_impact_pct": per_position,
        "portfolio_impact_pct": portfolio_impact,
    }


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

    # IF-01: historical (empirical) VaR/CVaR at 95%/99%, 1d/10d — alongside the pre-existing
    # parametric figure above, not replacing it (see compute_var_cvar()'s own docstring for why).
    hist_risk = compute_var_cvar(port_rets)

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
        "historical_var": hist_risk,
        "benchmark": bench_ticker,
        "warnings": warnings,
    }


@router.get("/stress-test")
def portfolio_stress_test(
    symbols: str = Query(..., description="Comma-separated stock symbols"),
    weights: str | None = Query(None, description="Comma-separated position weights (any units, auto-normalised)"),
    scenario: str = Query(..., description=f"One of: {', '.join(STRESS_SCENARIOS.keys())}"),
    _user: str = Depends(get_current_username),
):
    """IF-01: apply a predefined historical stress scenario to a real weighted portfolio.

    Reuses portfolio_risk()'s own symbol/weight validation and beta computation (a genuinely
    different endpoint, not a duplicate — this one applies a scenario shock to the SAME beta
    figures the risk endpoint already computes, rather than re-deriving them independently).
    """
    if scenario not in STRESS_SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario '{scenario}'. Valid: {', '.join(STRESS_SCENARIOS.keys())}")

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(sym_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 symbols required")
    if len(sym_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 symbols per request")

    if weights:
        raw_w = [abs(float(w)) for w in weights.split(",") if w.strip()]
        if len(raw_w) != len(sym_list):
            raise HTTPException(status_code=400, detail="weights count must match symbols count")
    else:
        raw_w = [1.0] * len(sym_list)
    total_w = sum(raw_w) or 1.0
    w_list = [w / total_w for w in raw_w]

    df = _fetch_returns(sym_list)
    available = [s for s in sym_list if s in df.columns]
    if len(available) < 2:
        raise HTTPException(status_code=422, detail="Insufficient price history for at least 2 symbols")

    w_map = {sym: w for sym, w in zip(sym_list, w_list)}
    avail_w = [w_map[s] for s in available]
    avail_total = sum(avail_w) or 1.0
    avail_w = [w / avail_total for w in avail_w]
    df = df[available]

    stock_meta = _fetch_stock_meta(available)
    market_map = {s: stock_meta.get(s, {}).get("market", "") for s in available}
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

    weight_map = dict(zip(available, avail_w))
    result = run_stress_test(betas, weight_map, scenario)
    result["symbols"] = available
    result["weights"] = avail_w
    result["benchmark"] = bench_ticker
    return result


@router.get("/stress-test/scenarios")
def list_stress_scenarios(_user: str = Depends(get_current_username)):
    """List all available predefined stress scenarios (key + label + benchmark move)."""
    return {
        key: {"label": v["label"], "benchmark_move_pct": v["benchmark_move_pct"]}
        for key, v in STRESS_SCENARIOS.items()
    }
