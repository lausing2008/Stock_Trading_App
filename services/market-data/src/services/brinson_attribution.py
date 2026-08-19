"""IF-10: Brinson sector allocation-vs-selection attribution.

Genuinely distinct from BOTH pre-existing "attribution" surfaces in this app (per this
tracker item's own verification): GET /paper-portfolio/attribution buckets closed trades by
entry-score/confidence/regime/R:R bands — an entry-CHARACTERISTIC diagnostic, never a
sector-vs-benchmark decomposition. The single-scalar outperformance_vs_spy/qqq/hsi fields
(paper_portfolio.py) compare TOTAL portfolio return to a benchmark index, with no per-sector
breakdown at all. This module answers a genuinely different question: "did I make money from
being overweight the RIGHT sectors (allocation), or from picking the right STOCKS within a
sector (selection), or both?"

Standard single-period Brinson-Fachler 3-term decomposition, computed per sector:
    allocation_effect  = (w_p - w_b) * r_b            # over/underweight timing, ignoring stock-picking
    selection_effect   = w_b * (r_p - r_b)             # stock-picking skill within a sector
    interaction_effect = (w_p - w_b) * (r_p - r_b)      # residual cross-term (the standard 3rd Brinson term)
where w_p/r_p are the PORTFOLIO's sector weight/return and w_b/r_b are the BENCHMARK's.

BENCHMARK HONESTY CAVEAT (deliberate, matching this repo's own established discipline of
stating a proxy explicitly rather than implying an exact decomposition it isn't): real, live
S&P 500 sector WEIGHTS require a paid data feed this app does not have. Rather than fabricate
or guess real S&P weights, this module uses an EQUAL-WEIGHT-ACROSS-11-SPDR-SECTORS proxy
(1/11 each) — an honestly-stated simplification, not a claim to replicate the actual S&P
sector composition. Benchmark sector RETURNS are real, though: fetched live from the same
SPDR sector-ETF tickers this app's own RES-4 sector-rotation feature already uses
(XLK/XLV/XLF/... — see routes.py's _SECTOR_ETFS), over the exact date range the portfolio's
own closed trades span.

Portfolio sector weight is a capital-days proxy (dollar-cost-basis * hold-days, summed per
sector, normalized to sum to 1.0) since a paper portfolio has no continuously-marked sector
NAV series to compute a true time-weighted average exposure from — this is a documented
approximation, not the exact Brinson prescription (which technically wants average NAV
weight over the period), but is the best available signal from what this app actually
persists (PaperTrade.sector, entry_date, exit_date, pnl, cost basis).
"""
from __future__ import annotations

from datetime import date

import structlog

log = structlog.get_logger("brinson_attribution")

# Same sector-name normalization gap already present in production data (yfinance's own
# inconsistent sector-name field, e.g. "Financial Services" vs "Financial" for the same real
# sector) — normalized here so both raw strings map to the same benchmark ETF/sector bucket.
_SECTOR_NAME_ALIASES: dict[str, str] = {
    "Financial": "Financial Services",
    "Health Care": "Healthcare",
    "Telecommunications": "Communication Services",
}

# The 11 real SPDR sector-ETF tickers (matches routes.py's own RES-4 _SECTOR_ETFS mapping —
# duplicated here rather than cross-imported, since market-data's api/ and services/ layers
# don't import from each other for this kind of small shared constant elsewhere in this repo).
SECTOR_ETF_TICKERS: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Materials": "XLB",
}

_MIN_TRADES_FOR_ATTRIBUTION = 5


def normalize_sector(raw: str | None) -> str | None:
    """Map a raw PaperTrade.sector string onto the canonical 11-SPDR-sector vocabulary.

    Returns None for missing/empty/unrecognized sectors (folded into an explicit
    'unclassified' bucket by the caller, never silently dropped)."""
    if not raw:
        return None
    canonical = _SECTOR_NAME_ALIASES.get(raw, raw)
    return canonical if canonical in SECTOR_ETF_TICKERS else None


def compute_brinson_attribution(
    closed_trades: list[dict],
    benchmark_sector_returns: dict[str, float],
) -> dict:
    """Compute the 3-term Brinson decomposition from already-fetched inputs.

    closed_trades: list of {"sector": str | None, "entry_date": date, "exit_date": date,
    "entry_price": float, "shares": float, "pct_return": float | None} dicts — pre-extracted
    from real PaperTrade rows by the caller so this function stays pure/DB-independent and
    directly unit-testable.
    benchmark_sector_returns: {canonical_sector_name: pct_return} for the SAME date range the
    trades span, already fetched (real SPDR ETF closes) by the caller.

    Equal-weight-across-11-sectors is applied here (not fetched) — see module docstring for
    why a real S&P weight feed isn't available. Sectors with zero portfolio trades are excluded
    from BOTH sides for this v1 (a sector with zero benchmark weight participating on the
    portfolio side would need weight renormalization semantics beyond this module's stated
    scope; flagged as a known simplification, not silently glossed over).
    """
    scoreable = [t for t in closed_trades if t.get("pct_return") is not None]
    if len(scoreable) < _MIN_TRADES_FOR_ATTRIBUTION:
        return {
            "insufficient_data": True,
            "n_trades": len(scoreable),
            "min_trades_required": _MIN_TRADES_FOR_ATTRIBUTION,
            "sectors": [],
            "total_allocation_effect_pct": None,
            "total_selection_effect_pct": None,
            "total_interaction_effect_pct": None,
        }

    # Portfolio sector weight: capital-days proxy (cost basis * hold-days), normalized.
    sector_capital_days: dict[str, float] = {}
    sector_returns_weighted_sum: dict[str, float] = {}
    sector_weight_sum: dict[str, float] = {}
    for t in scoreable:
        sector = normalize_sector(t.get("sector")) or "unclassified"
        entry_d = t.get("entry_date")
        exit_d = t.get("exit_date")
        hold_days = max((exit_d - entry_d).days, 1) if entry_d and exit_d else 1
        cost_basis = (t.get("entry_price") or 0.0) * (t.get("shares") or 0.0)
        capital_days = cost_basis * hold_days
        sector_capital_days[sector] = sector_capital_days.get(sector, 0.0) + capital_days
        # Weight each trade's own return contribution by its own capital-days share within
        # the sector — a capital-weighted average return, not a naive count-based mean.
        sector_returns_weighted_sum[sector] = sector_returns_weighted_sum.get(sector, 0.0) + t["pct_return"] * capital_days
        sector_weight_sum[sector] = sector_weight_sum.get(sector, 0.0) + capital_days

    total_capital_days = sum(sector_capital_days.values())
    if total_capital_days <= 0:
        return {
            "insufficient_data": True,
            "n_trades": len(scoreable),
            "min_trades_required": _MIN_TRADES_FOR_ATTRIBUTION,
            "sectors": [],
            "total_allocation_effect_pct": None,
            "total_selection_effect_pct": None,
            "total_interaction_effect_pct": None,
        }

    n_benchmark_sectors = len(SECTOR_ETF_TICKERS)
    benchmark_weight = 1.0 / n_benchmark_sectors  # equal-weight proxy — see module docstring

    sectors_out = []
    total_alloc = 0.0
    total_select = 0.0
    total_interact = 0.0
    for sector, cap_days in sorted(sector_capital_days.items(), key=lambda kv: -kv[1]):
        w_p = cap_days / total_capital_days
        r_p = sector_returns_weighted_sum[sector] / sector_weight_sum[sector] if sector_weight_sum[sector] > 0 else 0.0

        if sector == "unclassified":
            # No benchmark counterpart for an unclassified sector — reported for
            # transparency (real portfolio weight/return) but excluded from the effect sums,
            # since there's no meaningful w_b/r_b to compare against.
            sectors_out.append({
                "sector": sector, "portfolio_weight_pct": round(w_p * 100, 2),
                "portfolio_return_pct": round(r_p, 2), "benchmark_weight_pct": None,
                "benchmark_return_pct": None, "allocation_effect_pct": None,
                "selection_effect_pct": None, "interaction_effect_pct": None,
            })
            continue

        w_b = benchmark_weight
        r_b = benchmark_sector_returns.get(sector)
        if r_b is None:
            # A real, resolvable sector with no fetched benchmark return (a fetch failure for
            # that one ETF) — report the portfolio side honestly, skip the effect computation
            # rather than silently treating a missing benchmark return as 0%.
            sectors_out.append({
                "sector": sector, "portfolio_weight_pct": round(w_p * 100, 2),
                "portfolio_return_pct": round(r_p, 2), "benchmark_weight_pct": round(w_b * 100, 2),
                "benchmark_return_pct": None, "allocation_effect_pct": None,
                "selection_effect_pct": None, "interaction_effect_pct": None,
            })
            continue

        alloc = (w_p - w_b) * r_b
        select = w_b * (r_p - r_b)
        interact = (w_p - w_b) * (r_p - r_b)
        total_alloc += alloc
        total_select += select
        total_interact += interact

        sectors_out.append({
            "sector": sector,
            "portfolio_weight_pct": round(w_p * 100, 2),
            "portfolio_return_pct": round(r_p, 2),
            "benchmark_weight_pct": round(w_b * 100, 2),
            "benchmark_return_pct": round(r_b, 2),
            "allocation_effect_pct": round(alloc, 3),
            "selection_effect_pct": round(select, 3),
            "interaction_effect_pct": round(interact, 3),
        })

    return {
        "insufficient_data": False,
        "n_trades": len(scoreable),
        "sectors": sectors_out,
        "total_allocation_effect_pct": round(total_alloc, 3),
        "total_selection_effect_pct": round(total_select, 3),
        "total_interaction_effect_pct": round(total_interact, 3),
        "benchmark_weight_method": "equal_weight_11_spdr_sectors",
    }


def fetch_benchmark_sector_returns(start: date, end: date, timeout: int = 30) -> dict[str, float]:
    """Live fetch: real SPDR sector-ETF returns over [start, end], per canonical sector name.

    A thin wrapper kept separate from compute_brinson_attribution() so the pure computation
    stays network-free and directly unit-testable (matching fama_french.py's own established
    split between fetch and compute). Fails open per-ticker — one ETF's fetch failure never
    blocks the others; a sector with no return data is simply absent from the returned dict,
    and compute_brinson_attribution() already handles a missing sector return explicitly
    rather than treating it as 0%."""
    try:
        import yfinance as yf
    except ImportError:
        return {}

    tickers = list(set(SECTOR_ETF_TICKERS.values()))
    try:
        raw = yf.download(
            tickers, start=start, end=end, interval="1d", progress=False, auto_adjust=True,
        )
        closes = raw["Close"] if len(tickers) > 1 else raw[["Close"]].rename(columns={"Close": tickers[0]})
    except Exception as exc:
        log.warning("brinson.benchmark_fetch_failed", error=str(exc))
        return {}

    ticker_returns: dict[str, float] = {}
    for ticker in tickers:
        if ticker not in closes.columns:
            continue
        series = closes[ticker].dropna()
        if len(series) < 2:
            continue
        ticker_returns[ticker] = (float(series.iloc[-1]) / float(series.iloc[0]) - 1) * 100

    return {
        sector: ticker_returns[ticker]
        for sector, ticker in SECTOR_ETF_TICKERS.items()
        if ticker in ticker_returns
    }
