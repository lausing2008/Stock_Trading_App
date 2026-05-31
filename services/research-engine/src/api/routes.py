"""Planning Stage Research Intelligence Engine.

Aggregates data from all services, computes quantitative scores,
and calls Claude to generate qualitative company/industry/economic analysis.
Returns a full research report JSON.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime

import httpx
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.config import get_settings
from common.logging import get_logger

log = get_logger("research-engine")
router = APIRouter(prefix="/research", tags=["research"])
_s = get_settings()

import re as _re

def _sanitise_symbol(raw: str) -> str:
    """Allow only A-Z, 0-9, dot, hyphen, colon (covers HK: 0700.HK, indices: ^VIX).
    Raises ValueError for anything else so the route returns 400 before touching prompts.
    """
    clean = _re.sub(r"[^A-Z0-9.\-:]", "", raw.upper())
    if not clean:
        raise ValueError(f"Invalid symbol: {raw!r}")
    return clean

# Simple in-memory cache: symbol → (report_dict, timestamp)
_cache: dict[str, tuple[dict, datetime]] = {}
CACHE_TTL_SEC = 86_400  # 24 hours


# ── Request / Response models ─────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    provider: str = "claude"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    portfolio_size: float = 100_000.0
    max_risk_pct: float = 2.0


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get(client: httpx.AsyncClient, url: str) -> dict | list | None:
    try:
        r = await client.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.warning("upstream.get.failed", url=url, error=str(exc))
    return None


def _last(arr: list, default=None):
    """Last non-None value in an indicator array."""
    for v in reversed(arr):
        if v is not None:
            return v
    return default


def _second_last(arr: list, default=None):
    """Second-to-last non-None value."""
    found = 0
    for v in reversed(arr):
        if v is not None:
            found += 1
            if found == 2:
                return v
    return default


def _atr(prices: list[dict], period: int = 14) -> float | None:
    """Average True Range."""
    if len(prices) < period + 1:
        return None
    trs = []
    for i in range(1, len(prices)):
        h = prices[i].get("high", 0) or 0
        l = prices[i].get("low", 0) or 0
        pc = prices[i - 1].get("close", 0) or 0
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _fmt_cap(cap: float | None) -> str:
    if cap is None:
        return "N/A"
    if cap >= 1e12:
        return f"${cap/1e12:.2f}T"
    if cap >= 1e9:
        return f"${cap/1e9:.1f}B"
    if cap >= 1e6:
        return f"${cap/1e6:.1f}M"
    return f"${cap:,.0f}"


# ── Technical scoring ─────────────────────────────────────────────────────────

def _score_technical(stock: dict, prices: list, indicators: dict, levels: dict, live_price: float = 0.0) -> dict:
    price = live_price or stock.get("price") or stock.get("last_price") or 0.0
    iv = (indicators or {}).get("values", {})

    sma50 = _last(iv.get("sma_50", []))
    sma200 = _last(iv.get("sma_200", []))
    prev_sma50 = _second_last(iv.get("sma_50", []))
    prev_sma200 = _second_last(iv.get("sma_200", []))
    rsi = _last(iv.get("rsi_14", []))
    macd_line = _last(iv.get("macd_line", []))
    signal_line = _last(iv.get("signal_line", []))
    histogram = _last(iv.get("macd_histogram", []))
    prev_macd = _second_last(iv.get("macd_line", []))
    prev_signal = _second_last(iv.get("signal_line", []))
    prev_hist = _second_last(iv.get("macd_histogram", []))

    # Volume
    vols = [p.get("volume") or 0 for p in (prices or [])]
    cur_vol = vols[-1] if vols else 0
    avg20 = sum(vols[-20:]) / len(vols[-20:]) if len(vols) >= 20 else 0
    rvol = round(cur_vol / avg20, 2) if avg20 > 0 else 1.0

    # ATR
    atr_val = _atr(prices or [])
    atr_pct = round(atr_val / price * 100, 2) if atr_val and price else None

    # Support / Resistance
    sr = (levels or {}).get("support_resistance", []) or []
    supports = sorted([l["price"] for l in sr if l.get("kind") == "support" and l.get("price", 0) < price], reverse=True)
    resistances = sorted([l["price"] for l in sr if l.get("kind") == "resistance" and l.get("price", 0) > price])
    # also check "type" key for compatibility
    if not supports:
        supports = sorted([l["price"] for l in sr if l.get("type") == "support" and l.get("price", 0) < price], reverse=True)
    if not resistances:
        resistances = sorted([l["price"] for l in sr if l.get("type") == "resistance" and l.get("price", 0) > price])

    nearest_sup = supports[0] if supports else None
    major_sup = supports[1] if len(supports) > 1 else nearest_sup
    nearest_res = resistances[0] if resistances else None
    major_res = resistances[1] if len(resistances) > 1 else nearest_res

    # ── Scoring (start at 50) ─────────────────────────────────────────────────
    score = 50

    above_200 = (price > sma200) if sma200 else None
    above_50 = (price > sma50) if sma50 else None

    if above_200 is True:
        score += 15
    elif above_200 is False:
        score -= 10

    if above_50 is True:
        score += 10
    elif above_50 is False:
        score -= 7

    # Cross
    cross_status = "none"
    if sma50 and sma200 and prev_sma50 and prev_sma200:
        if prev_sma50 < prev_sma200 and sma50 > sma200:
            cross_status = "golden_cross"
            score += 10
        elif prev_sma50 > prev_sma200 and sma50 < sma200:
            cross_status = "death_cross"
            score -= 10
        elif sma50 > sma200:
            score += 5

    # RSI
    rsi_status = "Unknown"
    if rsi is not None:
        if rsi < 30:
            rsi_status = "Oversold"
        elif rsi < 40:
            rsi_status = "Weak"
            score -= 5
        elif rsi < 60:
            rsi_status = "Healthy"
            score += 5
        elif rsi < 70:
            rsi_status = "Strong"
            score += 8
        else:
            rsi_status = "Overbought"
            score -= 8

    # MACD
    macd_crossover = "none"
    if macd_line is not None and signal_line is not None and prev_macd is not None and prev_signal is not None:
        if prev_macd < prev_signal and macd_line > signal_line:
            macd_crossover = "bullish"
            score += 10
        elif prev_macd > prev_signal and macd_line < signal_line:
            macd_crossover = "bearish"
            score -= 10
        elif macd_line > signal_line:
            score += 3
        else:
            score -= 3

    # Histogram trend
    hist_status = "neutral"
    if histogram is not None:
        if histogram > 0:
            hist_status = "green_growing" if (prev_hist is not None and histogram > prev_hist) else "green_shrinking"
        else:
            hist_status = "red_growing" if (prev_hist is not None and histogram < prev_hist) else "red_shrinking"
        if hist_status == "green_growing":
            score += 2
        elif hist_status == "red_growing":
            score -= 2

    # Volume
    vol_status = "Weak"
    if rvol >= 1.5:
        vol_status = "Strong"
        score += 5
    elif rvol >= 1.0:
        vol_status = "Healthy"
        score += 2
    else:
        score -= 3

    # Near support
    if nearest_sup and price > 0:
        dist = (price - nearest_sup) / price * 100
        if dist < 3:
            score += 3
        elif dist < 8:
            score += 1

    score = max(0, min(100, score))

    # Trend verdict
    if score >= 80:
        trend_verdict = "Strong Bullish"
    elif score >= 65:
        trend_verdict = "Bullish"
    elif score >= 50:
        trend_verdict = "Neutral"
    elif score >= 35:
        trend_verdict = "Bearish"
    else:
        trend_verdict = "Strong Bearish"

    # ── Entry planning ────────────────────────────────────────────────────────
    stop_price = None
    targets = []
    if nearest_sup and atr_val and price > nearest_sup:
        stop_price = round(nearest_sup - atr_val * 0.5, 2)
        risk = round(price - stop_price, 2) if stop_price else None

        # Target 1: nearest resistance
        if nearest_res:
            gain1 = round((nearest_res - price) / price * 100, 1)
            targets.append({"target": 1, "price": round(nearest_res, 2), "gain_pct": gain1,
                            "rationale": "Nearest resistance level"})
        # Target 2: major resistance
        if major_res and major_res != nearest_res:
            gain2 = round((major_res - price) / price * 100, 1)
            targets.append({"target": 2, "price": round(major_res, 2), "gain_pct": gain2,
                            "rationale": "Major resistance level"})
        # Target 3: ATR extension (8x ATR from entry)
        t3 = round(price + atr_val * 8, 2)
        gain3 = round((t3 - price) / price * 100, 1)
        targets.append({"target": len(targets) + 1, "price": t3, "gain_pct": gain3,
                        "rationale": "8× ATR extension target"})

    rr_ratio = None
    rr_assess = "Poor"
    if stop_price and targets and price > stop_price:
        risk = price - stop_price
        reward = targets[0]["price"] - price
        if risk > 0:
            rr_ratio = round(reward / risk, 2)
            if rr_ratio >= 3:
                rr_assess = "Excellent"
            elif rr_ratio >= 2:
                rr_assess = "Good"
            elif rr_ratio >= 1.5:
                rr_assess = "Average"

    agg_zone = f"${nearest_sup:.2f}–${price:.2f}" if nearest_sup else f"~${price:.2f}"
    cons_zone = f"${price:.2f}–${nearest_res:.2f}" if nearest_res else f"~${price:.2f}"

    return {
        "score": round(score),
        "trend_verdict": trend_verdict,
        "price_vs_50_ema": {
            "value": "above" if above_50 else ("below" if above_50 is False else "unknown"),
            "ema": round(sma50, 2) if sma50 else None,
            "pct_diff": round((price - sma50) / sma50 * 100, 2) if sma50 and price else None,
            "interpretation": (
                f"Price is {'above' if above_50 else 'below'} the 50-day SMA at ${sma50:.2f}, "
                f"indicating a {'short-term uptrend' if above_50 else 'short-term downtrend'}."
            ) if above_50 is not None and sma50 else "SMA-50 data unavailable.",
        },
        "price_vs_200_ema": {
            "value": "above" if above_200 else ("below" if above_200 is False else "unknown"),
            "ema": round(sma200, 2) if sma200 else None,
            "pct_diff": round((price - sma200) / sma200 * 100, 2) if sma200 and price else None,
            "interpretation": (
                f"Price is {'above' if above_200 else 'below'} the 200-day SMA at ${sma200:.2f}, "
                f"{'confirming a long-term uptrend' if above_200 else 'indicating a long-term downtrend'}."
            ) if above_200 is not None and sma200 else "SMA-200 data unavailable.",
        },
        "cross_status": cross_status,
        "rsi": {
            "value": round(rsi, 1) if rsi is not None else None,
            "status": rsi_status,
            "interpretation": _rsi_interp(rsi, rsi_status),
        },
        "macd": {
            "line": round(macd_line, 4) if macd_line is not None else None,
            "signal": round(signal_line, 4) if signal_line is not None else None,
            "histogram": round(histogram, 4) if histogram is not None else None,
            "crossover": macd_crossover,
            "interpretation": _macd_interp(macd_crossover, macd_line, signal_line),
        },
        "histogram_analysis": {
            "value": round(histogram, 4) if histogram is not None else None,
            "status": hist_status,
            "interpretation": _hist_interp(hist_status),
        },
        "volume": {
            "current": int(cur_vol),
            "avg_20d": int(avg20),
            "rvol": rvol,
            "status": vol_status,
            "interpretation": f"RVOL {rvol:.2f}x — {'strong' if rvol >= 1.5 else 'healthy' if rvol >= 1.0 else 'weak'} market participation.",
        },
        "support_resistance": {
            "nearest_support": nearest_sup,
            "major_support": major_sup,
            "nearest_resistance": nearest_res,
            "major_resistance": major_res,
        },
        "atr": {
            "value": round(atr_val, 2) if atr_val else None,
            "pct": atr_pct,
            "volatility_rating": (
                "High" if atr_pct and atr_pct > 3
                else "Moderate" if atr_pct and atr_pct > 1.5
                else "Low"
            ),
        },
        "entry_planning": {
            "aggressive_entry": {
                "zone": agg_zone,
                "rationale": "Buy near current support for maximum risk/reward; requires tight stop below support.",
            },
            "conservative_entry": {
                "zone": cons_zone,
                "rationale": "Wait for price to establish above near-term resistance before entering.",
            },
            "stop_loss": {
                "price": stop_price,
                "method": "Support-based + 0.5× ATR buffer",
                "rationale": (
                    f"Below nearest support (${nearest_sup:.2f}) minus 0.5× ATR (${atr_val:.2f}) = ${stop_price:.2f}."
                    if stop_price and nearest_sup and atr_val else "Set below nearest support level."
                ),
            },
            "take_profit": targets,
            "risk_reward": {
                "expected_reward": round(targets[0]["price"] - price, 2) if targets and price else None,
                "expected_risk": round(price - stop_price, 2) if stop_price and price else None,
                "ratio": rr_ratio,
                "assessment": rr_assess,
            },
        },
    }


def _rsi_interp(rsi, status):
    if rsi is None:
        return "RSI data unavailable."
    if status == "Oversold":
        return f"RSI at {rsi:.1f} is oversold — potential mean-reversion bounce, but confirm with volume and support."
    if status == "Weak":
        return f"RSI at {rsi:.1f} is weak — selling pressure dominant; wait for stabilization."
    if status == "Healthy":
        return f"RSI at {rsi:.1f} is in healthy territory — balanced momentum, favorable for trend continuation."
    if status == "Strong":
        return f"RSI at {rsi:.1f} shows strong momentum — bullish but approaching overbought zone."
    return f"RSI at {rsi:.1f} is overbought — elevated risk of pullback or consolidation."


def _macd_interp(crossover, line, signal):
    if line is None:
        return "MACD data unavailable."
    if crossover == "bullish":
        return "MACD bullish crossover detected — momentum shifting positive. Strong buy signal."
    if crossover == "bearish":
        return "MACD bearish crossover detected — momentum turning negative. Caution advised."
    if line and signal and line > signal:
        return f"MACD ({line:.3f}) above signal ({signal:.3f}) — sustained bullish momentum."
    return f"MACD ({line:.3f}) below signal ({signal:.3f}) — bearish momentum present."


def _hist_interp(status):
    m = {
        "green_growing": "Histogram growing green — momentum accelerating to the upside.",
        "green_shrinking": "Histogram green but shrinking — bullish momentum slowing; watch for crossover.",
        "red_growing": "Histogram growing red — selling pressure increasing.",
        "red_shrinking": "Histogram red but shrinking — bearish momentum weakening; potential reversal ahead.",
        "neutral": "Histogram near zero — balanced momentum.",
    }
    return m.get(status, "Histogram data unavailable.")


# ── Fundamental scoring ───────────────────────────────────────────────────────

def _score_fundamental(fund: dict) -> dict:
    if not fund:
        return {"score": 50, "revenue": {}, "eps": {}, "margins": {}, "balance_sheet": {},
                "cash_flow": {}, "valuation": {}, "profitability": {}}

    score = 50

    # Revenue growth
    rev_growth = fund.get("revenue_growth")
    rev_assess = "Unknown"
    if rev_growth is not None:
        rev_pct = rev_growth * 100 if rev_growth < 10 else rev_growth  # handle decimal vs pct
        if rev_pct >= 20:
            rev_assess = "Excellent"; score += 10
        elif rev_pct >= 10:
            rev_assess = "Good"; score += 5
        elif rev_pct >= 0:
            rev_assess = "Average"
        else:
            rev_assess = "Weak"; score -= 5

    # EPS growth
    eps_growth = fund.get("earnings_growth")
    eps_assess = "Unknown"
    if eps_growth is not None:
        eps_pct = eps_growth * 100 if abs(eps_growth) < 10 else eps_growth
        if eps_pct >= 25:
            eps_assess = "Excellent"; score += 10
        elif eps_pct >= 10:
            eps_assess = "Good"; score += 5
        elif eps_pct >= 0:
            eps_assess = "Average"
        else:
            eps_assess = "Weak"; score -= 7

    # Margins
    gross_m = fund.get("gross_margin")
    op_m = fund.get("operating_margin")
    net_m = fund.get("profit_margin")
    if gross_m and gross_m > 0.4:
        score += 5
    elif gross_m and gross_m < 0.2:
        score -= 3
    if op_m and op_m > 0.2:
        score += 5
    elif op_m and op_m < 0.05:
        score -= 3

    def pct(v):
        if v is None:
            return None
        return round(v * 100, 1) if abs(v) <= 1 else round(v, 1)

    # Balance sheet
    cash = fund.get("total_cash") or 0
    debt = fund.get("total_debt") or 0
    de_ratio = round(debt / cash, 2) if cash > 0 else None
    bs_assess = "Strong Balance Sheet"
    if de_ratio is not None:
        if de_ratio < 0.5:
            bs_assess = "Strong Balance Sheet"; score += 5
        elif de_ratio < 2.0:
            bs_assess = "Average Balance Sheet"
        else:
            bs_assess = "Weak Balance Sheet"; score -= 5

    # Cash flow
    ocf = fund.get("operating_cashflow")
    fcf = fund.get("free_cashflow")
    fcf_assess = "Unknown"
    revenue = fund.get("total_revenue") or 1
    if fcf is not None:
        fcf_margin = fcf / revenue * 100 if revenue else None
        if fcf > 0 and fcf_margin and fcf_margin >= 20:
            fcf_assess = "Excellent"; score += 10
        elif fcf and fcf > 0:
            fcf_assess = "Good"; score += 5
        elif fcf is not None and fcf < 0:
            fcf_assess = "Poor"; score -= 5
        else:
            fcf_assess = "Average"
    else:
        fcf_margin = None

    # Valuation
    pe = fund.get("trailing_pe")
    fpe = fund.get("forward_pe")
    ps = fund.get("ev_to_revenue")
    ev_ebitda = fund.get("ev_to_ebitda")
    val_assess = "Fairly Valued"
    if pe is not None and pe > 0:
        if pe < 15:
            val_assess = "Undervalued"; score += 8
        elif pe < 25:
            val_assess = "Fairly Valued"; score += 3
        elif pe < 40:
            val_assess = "Fairly Valued"
        else:
            val_assess = "Overvalued"; score -= 5

    peg = None
    if pe and rev_growth and rev_growth > 0:
        g = rev_growth * 100 if rev_growth < 10 else rev_growth
        peg = round(pe / g, 2) if g > 0 else None

    # Profitability
    roe = fund.get("return_on_equity")
    roa = fund.get("return_on_assets")
    prof_grade = "Unknown"
    if roe is not None:
        roe_pct = roe * 100 if abs(roe) <= 1 else roe
        if roe_pct >= 20:
            prof_grade = "Excellent"; score += 8
        elif roe_pct >= 12:
            prof_grade = "Good"; score += 4
        elif roe_pct >= 6:
            prof_grade = "Average"
        else:
            prof_grade = "Poor"; score -= 4

    score = max(0, min(100, score))

    return {
        "score": round(score),
        "revenue": {
            "yoy_growth": pct(rev_growth),
            "assessment": rev_assess,
        },
        "eps": {
            "yoy_growth": pct(eps_growth),
            "trailing_eps": fund.get("trailing_eps"),
            "forward_eps": fund.get("forward_eps"),
            "assessment": eps_assess,
        },
        "margins": {
            "gross": pct(gross_m),
            "operating": pct(op_m),
            "net": pct(net_m),
            "comparison": "Above typical industry average" if gross_m and gross_m > 0.35 else "Inline with industry",
        },
        "balance_sheet": {
            "cash": cash,
            "debt": debt,
            "de_ratio": de_ratio,
            "assessment": bs_assess,
        },
        "cash_flow": {
            "operating_cf": ocf,
            "fcf": fcf,
            "fcf_margin": round(fcf_margin, 1) if fcf_margin else None,
            "assessment": fcf_assess,
        },
        "valuation": {
            "pe": round(pe, 1) if pe else None,
            "forward_pe": round(fpe, 1) if fpe else None,
            "peg": peg,
            "price_sales": round(ps, 1) if ps else None,
            "ev_ebitda": round(ev_ebitda, 1) if ev_ebitda else None,
            "assessment": val_assess,
        },
        "profitability": {
            "roe": pct(roe),
            "roa": pct(roa),
            "grade": prof_grade,
        },
    }


# ── Checklist ─────────────────────────────────────────────────────────────────

def _build_checklist(tech: dict, fund: dict, ai: dict) -> dict:
    def item(label, status, note=""):
        return {"item": label, "status": status, "note": note}

    f = fund
    t = tech
    sr = t.get("support_resistance", {})
    rsi_val = (t.get("rsi") or {}).get("value")
    macd_cross = (t.get("macd") or {}).get("crossover", "none")

    # Layer 1: Company
    rev_ok = (f.get("revenue") or {}).get("assessment") in ("Excellent", "Good")
    eps_ok = (f.get("eps") or {}).get("assessment") in ("Excellent", "Good")
    fcf_ok = (f.get("cash_flow") or {}).get("assessment") in ("Excellent", "Good")
    de = (f.get("balance_sheet") or {}).get("de_ratio")
    de_ok = de is not None and de < 2.0
    inst_pct = ai.get("institutional_pct") or 0
    moat = ai.get("moat_rating", "")

    layer1 = [
        item("Can explain business in 2 sentences?", "pass"),  # Claude always explains it
        item("Revenue growing YoY?", "pass" if rev_ok else ("warning" if rev_ok is not True else "fail"),
             f"{(f.get('revenue') or {}).get('yoy_growth', '—')}% YoY"),
        item("EPS growing YoY?", "pass" if eps_ok else "warning",
             f"{(f.get('eps') or {}).get('yoy_growth', '—')}% YoY"),
        item("Free cash flow positive & growing?", "pass" if fcf_ok else "warning"),
        item("Debt manageable (D/E < 2)?", "pass" if de_ok else ("warning" if de is not None else "fail"),
             f"D/E = {de:.2f}" if de is not None else "No data"),
        item("Clear competitive moat?", "pass" if moat in ("Very Strong", "Strong") else ("warning" if moat == "Moderate" else "fail"),
             moat or "Unknown"),
        item("Insiders buying or holding?", ai.get("insider_status_checklist", "warning")),
        item("Institutional ownership > 50%?", "pass" if inst_pct >= 50 else "warning",
             f"{inst_pct:.1f}%" if inst_pct else "Unknown"),
    ]

    # Layer 2: Industry
    ind_status = ai.get("industry_status", "")
    ind_verdict = ai.get("industry_verdict", "")
    tam_rating = ai.get("tam_rating", "")
    reg_risk = ai.get("regulatory_risk", "")
    layer2 = [
        item("Industry growing?", "pass" if ind_status == "Growing" else ("warning" if ind_status == "Mature" else "fail"), ind_status),
        item("Large TAM?", "pass" if tam_rating in ("Excellent", "Good") else "warning", tam_rating),
        item("Market share increasing or stable?", ai.get("market_share_checklist", "warning")),
        item("Low regulatory risk?", "pass" if reg_risk == "Low" else ("warning" if reg_risk == "Medium" else "fail"), reg_risk),
        item("Industry tailwind?", "pass" if "Tailwind" in ind_verdict else ("warning" if "Neutral" in ind_verdict else "fail"), ind_verdict),
    ]

    # Layer 3: Economy
    fed = ai.get("fed_status", "")
    inflation = ai.get("inflation_trend", "")
    gdp = ai.get("gdp_status", "")
    rec_risk = ai.get("recession_risk_rating", "")
    layer3 = [
        item("Fed supportive (cutting or holding)?", "pass" if fed in ("Cutting", "Holding") else "warning", fed),
        item("Inflation improving or stable?", "pass" if inflation in ("Improving", "Stable") else "warning", inflation),
        item("GDP expanding?", "pass" if gdp == "Expanding" else ("warning" if gdp == "Flat" else "fail"), gdp),
        item("No major recession signals?", "pass" if rec_risk == "Low" else ("warning" if rec_risk == "Moderate" else "fail"), rec_risk),
        item("Favorable market style?", ai.get("market_style_checklist", "warning")),
    ]

    # Layer 4: Technical
    cross = t.get("cross_status", "none")
    rsi_s = (t.get("rsi") or {}).get("status", "")
    layer4 = [
        item("Price above 200-day SMA?",
             "pass" if t.get("price_vs_200_ema", {}).get("value") == "above" else "fail"),
        item("Price above 50-day SMA?",
             "pass" if t.get("price_vs_50_ema", {}).get("value") == "above" else "warning"),
        item("Golden Cross present?",
             "pass" if cross == "golden_cross" else ("fail" if cross == "death_cross" else "warning"),
             cross.replace("_", " ").title()),
        item("RSI healthy (40-70)?",
             "pass" if rsi_s in ("Healthy", "Strong") else ("warning" if rsi_s == "Oversold" else "fail"),
             f"RSI {rsi_val:.1f} — {rsi_s}" if rsi_val else rsi_s),
        item("MACD bullish or neutral?",
             "pass" if macd_cross == "bullish" else ("warning" if macd_cross == "none" else "fail"),
             macd_cross),
        item("Volume confirming move?",
             "pass" if (t.get("volume") or {}).get("rvol", 0) >= 1.0 else "warning",
             f"RVOL {(t.get('volume') or {}).get('rvol', 0):.2f}x"),
        item("Support level identified?",
             "pass" if sr.get("nearest_support") else "warning",
             f"${sr['nearest_support']:.2f}" if sr.get("nearest_support") else "Not found"),
    ]

    return {
        "layer1_company": layer1,
        "layer2_industry": layer2,
        "layer3_economy": layer3,
        "layer4_technical": layer4,
    }


# ── Position sizing ───────────────────────────────────────────────────────────

def _position_size(tech: dict, portfolio_size: float, max_risk_pct: float, price: float) -> dict:
    stop = (tech.get("entry_planning") or {}).get("stop_loss", {}).get("price")
    if not stop or not price or price <= stop:
        return {"portfolio_size": portfolio_size, "max_risk_pct": max_risk_pct,
                "dollar_risk": None, "stop_distance": None, "share_quantity": None, "position_size": None}
    dollar_risk = portfolio_size * max_risk_pct / 100
    stop_dist = round(price - stop, 2)
    shares = int(dollar_risk / stop_dist) if stop_dist > 0 else 0
    pos_size = round(shares * price, 2)
    return {
        "portfolio_size": portfolio_size,
        "max_risk_pct": max_risk_pct,
        "dollar_risk": round(dollar_risk, 2),
        "stop_distance": stop_dist,
        "share_quantity": shares,
        "position_size": pos_size,
        "pct_of_portfolio": round(pos_size / portfolio_size * 100, 1) if portfolio_size else None,
    }


# ── Claude integration ────────────────────────────────────────────────────────

async def _call_claude(req: ResearchRequest, symbol: str, stock: dict, fund: dict,
                       tech: dict, fund_scores: dict, live_price: float = 0.0) -> dict:
    if not req.api_key.strip():
        return _fallback_ai()
    price = live_price or stock.get("price") or stock.get("last_price") or "N/A"
    name = stock.get("name", symbol)
    sector = stock.get("sector", "Unknown")
    market_cap = _fmt_cap(fund.get("market_cap"))

    # Compact data summary for the prompt
    tech_summary = {
        "trend_verdict": tech.get("trend_verdict"),
        "price_vs_50sma": tech.get("price_vs_50_ema", {}).get("value"),
        "price_vs_200sma": tech.get("price_vs_200_ema", {}).get("value"),
        "cross_status": tech.get("cross_status"),
        "rsi": (tech.get("rsi") or {}).get("value"),
        "rsi_status": (tech.get("rsi") or {}).get("status"),
        "macd_crossover": (tech.get("macd") or {}).get("crossover"),
        "rvol": (tech.get("volume") or {}).get("rvol"),
        "atr_pct": (tech.get("atr") or {}).get("pct"),
        "nearest_support": (tech.get("support_resistance") or {}).get("nearest_support"),
        "nearest_resistance": (tech.get("support_resistance") or {}).get("nearest_resistance"),
        "tech_score": tech.get("score"),
    }

    fund_summary = {
        "revenue_growth_pct": (fund_scores.get("revenue") or {}).get("yoy_growth"),
        "eps_growth_pct": (fund_scores.get("eps") or {}).get("yoy_growth"),
        "gross_margin_pct": (fund_scores.get("margins") or {}).get("gross"),
        "operating_margin_pct": (fund_scores.get("margins") or {}).get("operating"),
        "net_margin_pct": (fund_scores.get("margins") or {}).get("net"),
        "de_ratio": (fund_scores.get("balance_sheet") or {}).get("de_ratio"),
        "fcf_margin_pct": (fund_scores.get("cash_flow") or {}).get("fcf_margin"),
        "pe": (fund_scores.get("valuation") or {}).get("pe"),
        "forward_pe": (fund_scores.get("valuation") or {}).get("forward_pe"),
        "ev_ebitda": (fund_scores.get("valuation") or {}).get("ev_ebitda"),
        "roe_pct": (fund_scores.get("profitability") or {}).get("roe"),
        "roa_pct": (fund_scores.get("profitability") or {}).get("roa"),
        "fund_score": fund_scores.get("score"),
        "beta": fund.get("beta"),
        "short_float_pct": fund.get("short_percent_of_float"),
        "insider_buy_transactions": fund.get("insider_buy_transactions_6m"),
        "insider_net_pct": fund.get("insider_net_pct"),
        "held_pct_institutions": fund.get("held_percent_institutions"),
        "target_price": fund.get("target_price"),
        "recommendation": fund.get("recommendation"),
    }

    system_prompt = (
        "You are a senior equity research analyst with CFA expertise. "
        "You provide rigorous, evidence-based investment analysis. "
        "Always respond with valid JSON only — no markdown, no extra text."
    )

    user_prompt = f"""Analyze {symbol} ({name}) for investment suitability. Current price: ${price}. Market cap: {market_cap}. Sector: {sector}.

TECHNICAL DATA:
{json.dumps(tech_summary, indent=2)}

FUNDAMENTAL DATA:
{json.dumps(fund_summary, indent=2)}

Return a JSON object with EXACTLY this structure (fill in all fields based on your knowledge of {symbol} and the data above):

{{
  "company": {{
    "business_model": "2-3 sentence description of what {symbol} does and how it makes money",
    "competitive_advantage": {{
      "brand_strength": "Strong|Moderate|Weak",
      "network_effects": "Strong|Moderate|Weak|None",
      "patents": "Strong|Moderate|Weak|None",
      "switching_costs": "High|Medium|Low",
      "economies_of_scale": "Strong|Moderate|Weak",
      "distribution_advantage": "Excellent|Good|Average|Weak"
    }},
    "moat": {{
      "rating": "Very Strong|Strong|Moderate|Weak|None",
      "explanation": "2-3 sentence explanation of competitive moat"
    }},
    "insider_activity": {{
      "status": "Bullish|Neutral|Bearish",
      "explanation": "Explain based on insider_buy_transactions and insider_net_pct data"
    }},
    "institutional_ownership": {{
      "pct": {fund.get("held_percent_institutions", 0) or 0},
      "trend": "Increasing|Stable|Decreasing",
      "interpretation": "Brief interpretation"
    }},
    "management": {{
      "rating": "Excellent|Good|Average|Weak",
      "explanation": "Brief assessment of management quality based on capital allocation and track record"
    }}
  }},
  "company_score": 0,
  "industry": {{
    "status": "Growing|Mature|Declining|Disrupted",
    "evidence": "Brief evidence for industry status",
    "tam": {{
      "size": "Large|Medium|Small",
      "growth": "High|Medium|Low",
      "expansion_potential": "Excellent|Good|Average|Weak",
      "rating": "Excellent|Good|Average|Weak"
    }},
    "market_share": {{
      "position": "Dominant|Strong|Average|Weak",
      "trend": "Gaining|Stable|Losing",
      "verdict": "Gaining Share|Stable|Losing Share"
    }},
    "competitors": [
      {{"name": "Competitor 1", "relative_position": "brief comparison"}},
      {{"name": "Competitor 2", "relative_position": "brief comparison"}}
    ],
    "regulatory_risk": "Low|Medium|High",
    "regulatory_explanation": "Brief explanation",
    "verdict": "Strong Tailwind|Moderate Tailwind|Neutral|Headwind|Severe Headwind",
    "verdict_explanation": "Brief explanation of industry verdict"
  }},
  "industry_score": 0,
  "economic": {{
    "fed": {{
      "status": "Hiking|Holding|Cutting",
      "impact": "Brief explanation of Fed policy impact on {symbol}"
    }},
    "inflation": {{
      "cpi_trend": "Improving|Stable|Worsening",
      "impact": "Brief explanation of inflation impact on {symbol}"
    }},
    "gdp": {{
      "status": "Expanding|Flat|Contracting",
      "significance": "Brief explanation of GDP significance for {symbol}"
    }},
    "employment": {{
      "status": "Strong|Neutral|Weak"
    }},
    "recession_risk": {{
      "yield_curve_inverted": false,
      "gdp_negative": false,
      "unemployment_rising": false,
      "consumer_confidence_falling": false,
      "rating": "Low|Moderate|High"
    }},
    "market_environment": {{
      "favored_style": "Growth Stocks|Value Stocks|Dividend Stocks|Defensive Stocks",
      "explanation": "Brief explanation of which equity style is favored and why {symbol} fits or doesn't"
    }}
  }},
  "economic_score": 0,
  "bullish_factors": ["factor 1", "factor 2", "factor 3", "factor 4", "factor 5"],
  "bearish_factors": ["factor 1", "factor 2", "factor 3", "factor 4", "factor 5"],
  "key_risks": ["risk 1", "risk 2", "risk 3"],
  "key_opportunities": ["opportunity 1", "opportunity 2", "opportunity 3"],
  "trade_invalidation": [
    "Condition 1 that would invalidate the trade",
    "Condition 2",
    "Condition 3",
    "Condition 4"
  ],
  "ai_verdict": {{
    "can_buy_today": "YES|NO|WAIT",
    "why": "Detailed 3-4 sentence explanation of the recommendation",
    "biggest_risks": ["risk 1", "risk 2", "risk 3"],
    "must_improve": ["condition 1 that must improve before buying", "condition 2"],
    "strong_buy_catalysts": ["catalyst 1", "catalyst 2", "catalyst 3"],
    "confidence_pct": 0,
    "final_recommendation": "STRONG BUY|BUY|WATCH|AVOID|SELL"
  }},
  "insider_status_checklist": "pass|warning|fail",
  "institutional_pct": 0,
  "moat_rating": "Very Strong|Strong|Moderate|Weak|None",
  "industry_status": "Growing|Mature|Declining|Disrupted",
  "industry_verdict": "Strong Tailwind|Moderate Tailwind|Neutral|Headwind|Severe Headwind",
  "tam_rating": "Excellent|Good|Average|Weak",
  "market_share_checklist": "pass|warning|fail",
  "regulatory_risk": "Low|Medium|High",
  "fed_status": "Hiking|Holding|Cutting",
  "inflation_trend": "Improving|Stable|Worsening",
  "gdp_status": "Expanding|Flat|Contracting",
  "recession_risk_rating": "Low|Moderate|High",
  "market_style_checklist": "pass|warning|fail",
  "confidence": 0
}}

Use your knowledge of {symbol} to fill in qualitative sections accurately. Base scores (company_score, industry_score, economic_score, confidence, ai_verdict.confidence_pct) on actual analysis — use 0-100 integers."""

    # Call Claude
    body = {
        "model": req.model,
        "max_tokens": 4096,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    headers = {
        "x-api-key": req.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
    except Exception as exc:
        log.warning("claude.call.failed", error=str(exc))
        return _fallback_ai()

    if r.status_code != 200:
        log.warning("claude.error", status=r.status_code, body=r.text[:200])
        return _fallback_ai()

    try:
        text = r.json()["content"][0]["text"].strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0]
        return json.loads(text)
    except Exception as exc:
        log.warning("claude.parse.failed", error=str(exc))
        return _fallback_ai()


def _fallback_ai() -> dict:
    return {
        "company": {
            "business_model": "Analysis unavailable — AI provider returned an error.",
            "competitive_advantage": {"brand_strength": "Unknown"},
            "moat": {"rating": "Unknown", "explanation": "Data unavailable."},
            "insider_activity": {"status": "Unknown", "explanation": "Data unavailable."},
            "institutional_ownership": {"pct": 0, "trend": "Unknown", "interpretation": "Data unavailable."},
            "management": {"rating": "Unknown", "explanation": "Data unavailable."},
        },
        "company_score": 50,
        "industry": {
            "status": "Unknown", "evidence": "Data unavailable.",
            "tam": {"size": "Unknown", "growth": "Unknown", "expansion_potential": "Unknown", "rating": "Unknown"},
            "market_share": {"position": "Unknown", "trend": "Unknown", "verdict": "Unknown"},
            "competitors": [],
            "regulatory_risk": "Medium", "regulatory_explanation": "Data unavailable.",
            "verdict": "Neutral", "verdict_explanation": "Data unavailable.",
        },
        "industry_score": 50,
        "economic": {
            "fed": {"status": "Holding", "impact": "Data unavailable."},
            "inflation": {"cpi_trend": "Stable", "impact": "Data unavailable."},
            "gdp": {"status": "Expanding", "significance": "Data unavailable."},
            "employment": {"status": "Neutral"},
            "recession_risk": {"yield_curve_inverted": False, "gdp_negative": False,
                               "unemployment_rising": False, "consumer_confidence_falling": False, "rating": "Low"},
            "market_environment": {"favored_style": "Growth Stocks", "explanation": "Data unavailable."},
        },
        "economic_score": 50,
        "bullish_factors": ["Technical indicators positive", "Market data reviewed"],
        "bearish_factors": ["AI analysis unavailable — please retry with a valid API key"],
        "key_risks": ["AI provider error"],
        "key_opportunities": ["Retry analysis when AI is available"],
        "trade_invalidation": ["Break below key support", "Earnings miss", "Macro deterioration"],
        "ai_verdict": {
            "can_buy_today": "WAIT",
            "why": "AI analysis failed. Please check your API key and retry.",
            "biggest_risks": ["AI analysis unavailable"],
            "must_improve": ["Resolve AI connection"],
            "strong_buy_catalysts": ["Retry with valid API key"],
            "confidence_pct": 0,
            "final_recommendation": "WATCH",
        },
        "insider_status_checklist": "warning",
        "institutional_pct": 0,
        "moat_rating": "Unknown",
        "industry_status": "Unknown",
        "industry_verdict": "Neutral",
        "tam_rating": "Unknown",
        "market_share_checklist": "warning",
        "regulatory_risk": "Medium",
        "fed_status": "Holding",
        "inflation_trend": "Stable",
        "gdp_status": "Expanding",
        "recession_risk_rating": "Low",
        "market_style_checklist": "warning",
        "confidence": 0,
    }


# ── yfinance fallback (for symbols not in the DB) ────────────────────────────

def _compute_yf_indicators(hist: pd.DataFrame) -> dict:
    closes = hist["Close"]
    sma50 = closes.rolling(50).mean()
    sma200 = closes.rolling(200).mean()
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    def to_list(s):
        return [None if pd.isna(v) else round(float(v), 4) for v in s]

    return {"values": {
        "sma_50": to_list(sma50),
        "sma_200": to_list(sma200),
        "rsi_14": to_list(rsi),
        "macd_line": to_list(macd_line),
        "signal_line": to_list(signal_line),
        "macd_histogram": to_list(macd_hist),
    }}


def _yf_sync_fetch(sym: str):
    """Fetch stock info, price history, and TA indicators from yfinance."""
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
        name = info.get("longName") or info.get("shortName") or sym
        sector = info.get("sector") or "Unknown"

        hist = ticker.history(period="1y", interval="1d")
        prices = []
        indicators = {"values": {}}
        if not hist.empty:
            for idx, row in hist.iterrows():
                prices.append({
                    "ts": str(idx.date()),
                    "open": float(row.get("Open") or 0),
                    "high": float(row.get("High") or 0),
                    "low": float(row.get("Low") or 0),
                    "close": float(row.get("Close") or 0),
                    "volume": int(row.get("Volume") or 0),
                })
            indicators = _compute_yf_indicators(hist)

        live_price = 0.0
        try:
            fi = ticker.fast_info
            live_price = float(getattr(fi, "last_price", 0) or 0)
        except Exception:
            live_price = prices[-1]["close"] if prices else 0.0

        if name == sym and not prices:
            return {}, [], {"values": {}}, 0.0

        return {"name": name, "sector": sector}, prices, indicators, live_price
    except Exception as exc:
        log.warning("yf.fallback.failed", symbol=sym, error=str(exc))
        return {}, [], {"values": {}}, 0.0


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{symbol}")
async def get_research(symbol: str):
    """Return cached research report (generated within last 24h)."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    entry = _cache.get(sym)
    if entry:
        report, ts = entry
        if (datetime.utcnow() - ts).total_seconds() < CACHE_TTL_SEC:
            return report
    raise HTTPException(404, "No cached research report. POST to /research/{symbol} to generate.")


@router.delete("/{symbol}")
async def clear_research(symbol: str):
    """Clear cached report to force regeneration."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    _cache.pop(sym, None)
    return {"status": "cleared", "symbol": sym}


@router.post("/{symbol}")
async def generate_research(symbol: str, req: ResearchRequest):
    """Generate a full Planning Stage Research Report for the given symbol."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")

    # Gather data from all services in parallel
    async with httpx.AsyncClient(timeout=25) as client:
        stock_t, fund_t, prices_t, ind_t, levels_t, signal_t, rank_t, live_t = await asyncio.gather(
            _get(client, f"{_s.market_data_url}/stocks/{sym}"),
            _get(client, f"{_s.market_data_url}/stocks/{sym}/fundamentals"),
            _get(client, f"{_s.market_data_url}/stocks/{sym}/prices?timeframe=1d&limit=260"),
            _get(client, f"{_s.technical_analysis_url}/ta/{sym}/indicators?days=400"),
            _get(client, f"{_s.technical_analysis_url}/ta/{sym}/levels"),
            _get(client, f"{_s.signal_engine_url}/signals/{sym}"),
            _get(client, f"{_s.ranking_engine_url}/rankings/{sym}"),
            _get(client, f"{_s.market_data_url}/stocks/latest_prices?symbols={sym}"),
        )

    stock = stock_t or {}
    fund = fund_t or {}
    prices = prices_t or []
    indicators = ind_t or {"ts": [], "values": {}}
    levels = levels_t or {}
    signal = signal_t or {}
    ranking = rank_t or {}
    live = (live_t or [{}])[0] if isinstance(live_t, list) else {}

    if not stock:
        # Symbol not in DB — fetch directly from yfinance
        loop = asyncio.get_event_loop()
        yf_stock, yf_prices, yf_indicators, yf_price = await loop.run_in_executor(
            None, _yf_sync_fetch, sym
        )
        if not yf_stock:
            raise HTTPException(404, f"Symbol {sym} not found")
        stock = yf_stock
        if not prices:
            prices = yf_prices
        if not indicators.get("values"):
            indicators = yf_indicators
        if not live:
            live = {"price": yf_price}

    price = live.get("price") or stock.get("price") or stock.get("last_price") or 0.0

    # Compute scores
    tech = _score_technical(stock, prices, indicators, levels, live_price=price)
    fund_scores = _score_fundamental(fund)

    # Call Claude for qualitative analysis
    ai = await _call_claude(req, sym, stock, fund, tech, fund_scores, live_price=price)

    # Overall weighted score
    scores = {
        "technical": tech["score"],
        "fundamental": fund_scores["score"],
        "company": min(100, max(0, ai.get("company_score", 65))),
        "industry": min(100, max(0, ai.get("industry_score", 65))),
        "economic": min(100, max(0, ai.get("economic_score", 65))),
    }
    overall = round(
        scores["technical"] * 0.25
        + scores["fundamental"] * 0.30
        + scores["company"] * 0.15
        + scores["industry"] * 0.15
        + scores["economic"] * 0.15
    )

    if overall >= 90:
        recommendation = "STRONG BUY"
    elif overall >= 80:
        recommendation = "BUY"
    elif overall >= 65:
        recommendation = "WATCH"
    elif overall >= 50:
        recommendation = "AVOID"
    else:
        recommendation = "SELL"

    # Override with Claude's verdict if available and score is borderline
    claude_rec = (ai.get("ai_verdict") or {}).get("final_recommendation")
    if claude_rec in ("STRONG BUY", "BUY", "WATCH", "AVOID", "SELL") and abs(overall - 65) < 10:
        recommendation = claude_rec

    checklist = _build_checklist(tech, fund_scores, ai)
    position = _position_size(tech, req.portfolio_size, req.max_risk_pct, price)

    report = {
        "symbol": sym,
        "company_name": stock.get("name", sym),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "current_price": price,
        "market_cap": fund.get("market_cap"),
        "sector": stock.get("sector"),
        "industry": stock.get("industry") or stock.get("sector"),
        "recommendation": recommendation,
        "overall_score": overall,
        "confidence": min(100, max(0, ai.get("confidence", 65))),
        "scores": scores,
        "executive_summary": {
            "bullish_factors": ai.get("bullish_factors", []),
            "bearish_factors": ai.get("bearish_factors", []),
            "key_risks": ai.get("key_risks", []),
            "key_opportunities": ai.get("key_opportunities", []),
        },
        "technical": tech,
        "fundamental": fund_scores,
        "company": (ai.get("company") or {}),
        "industry_analysis": (ai.get("industry") or {}),
        "economic": (ai.get("economic") or {}),
        "checklist": checklist,
        "entry_planning": tech.get("entry_planning", {}),
        "position_sizing": position,
        "trade_invalidation": ai.get("trade_invalidation", []),
        "ai_verdict": (ai.get("ai_verdict") or {}),
        "signal": {
            "signal": signal.get("signal"),
            "confidence": signal.get("confidence"),
            "horizon": signal.get("horizon"),
        },
        "ranking": {
            "score": ranking.get("score"),
            "rank": ranking.get("rank"),
            "technical": ranking.get("technical"),
            "momentum": ranking.get("momentum"),
            "value": ranking.get("value"),
            "growth": ranking.get("growth"),
        },
        "analyst": {
            "target_price": fund.get("target_price"),
            "target_high": fund.get("target_high"),
            "target_low": fund.get("target_low"),
            "recommendation": fund.get("recommendation"),
            "num_analysts": fund.get("number_of_analysts"),
        },
        "beta": fund.get("beta"),
        "week_52_high": fund.get("week_52_high"),
        "week_52_low": fund.get("week_52_low"),
        "short_float_pct": fund.get("short_percent_of_float"),
        "next_earnings": fund.get("next_earnings_date"),
        "days_to_earnings": fund.get("days_to_earnings"),
    }

    _cache[sym] = (report, datetime.utcnow())
    log.info("research.generated", symbol=sym, overall=overall, recommendation=recommendation)
    return report


# ── Chat endpoint ─────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    provider: str = "claude"


@router.post("/{symbol}/chat")
async def chat_research(symbol: str, req: ChatRequest):
    """Answer questions about the cached research report using AI."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    entry = _cache.get(sym)
    if not entry:
        raise HTTPException(404, "No research report found. Generate a report first.")

    if not req.api_key.strip():
        raise HTTPException(400, "API key required for chat.")

    report, _ = entry
    sc = report.get("scores", {})
    tech = report.get("technical", {})
    fund = report.get("fundamental", {})
    verdict = report.get("ai_verdict", {})
    ep = report.get("entry_planning", {})

    system_prompt = f"""You are an expert equity research analyst reviewing a freshly generated research report for {sym} ({report.get("company_name", sym)}).

REPORT SNAPSHOT:
  Price: ${report.get("current_price", 0):.2f} | Market Cap: {_fmt_cap(report.get("market_cap"))} | Sector: {report.get("sector", "Unknown")}
  Overall Score: {report.get("overall_score")}/100 | Recommendation: {report.get("recommendation")} | Confidence: {report.get("confidence")}%

SCORES (0-100):
  Technical: {sc.get("technical")} | Fundamental: {sc.get("fundamental")} | Company: {sc.get("company")} | Industry: {sc.get("industry")} | Economic: {sc.get("economic")}

TECHNICAL:
  Trend: {tech.get("trend_verdict")} | Cross: {tech.get("cross_status")}
  vs 50-SMA: {(tech.get("price_vs_50_ema") or {}).get("value")} at ${(tech.get("price_vs_50_ema") or {}).get("ema")}
  vs 200-SMA: {(tech.get("price_vs_200_ema") or {}).get("value")} at ${(tech.get("price_vs_200_ema") or {}).get("ema")}
  RSI: {(tech.get("rsi") or {}).get("value")} ({(tech.get("rsi") or {}).get("status")})
  MACD crossover: {(tech.get("macd") or {}).get("crossover")}
  Volume RVOL: {(tech.get("volume") or {}).get("rvol")}x ({(tech.get("volume") or {}).get("status")})
  Nearest Support: ${(tech.get("support_resistance") or {}).get("nearest_support")} | Resistance: ${(tech.get("support_resistance") or {}).get("nearest_resistance")}
  ATR: ${(tech.get("atr") or {}).get("value")} ({(tech.get("atr") or {}).get("volatility_rating")})

ENTRY PLAN:
  Aggressive entry: {(ep.get("aggressive_entry") or {}).get("zone")}
  Conservative entry: {(ep.get("conservative_entry") or {}).get("zone")}
  Stop loss: ${(ep.get("stop_loss") or {}).get("price")}
  Targets: {[(t.get("price"), t.get("gain_pct")) for t in (ep.get("take_profit") or [])]}
  Risk/Reward: {(ep.get("risk_reward") or {}).get("ratio")}:1 ({(ep.get("risk_reward") or {}).get("assessment")})

FUNDAMENTAL:
  Revenue growth: {(fund.get("revenue") or {}).get("yoy_growth")}%
  EPS growth: {(fund.get("eps") or {}).get("yoy_growth")}%
  Gross margin: {(fund.get("margins") or {}).get("gross")}%
  Free cash flow: {_fmt_cap((fund.get("cash_flow") or {}).get("fcf"))}
  P/E: {(fund.get("valuation") or {}).get("pe")} | Forward P/E: {(fund.get("valuation") or {}).get("forward_pe")}
  Valuation: {(fund.get("valuation") or {}).get("assessment")}
  D/E ratio: {(fund.get("balance_sheet") or {}).get("de_ratio")}

AI VERDICT:
  Can buy today: {verdict.get("can_buy_today")} | Final rec: {verdict.get("final_recommendation")} | Confidence: {verdict.get("confidence_pct")}%
  Why: {verdict.get("why")}
  Key risks: {verdict.get("biggest_risks")}
  Catalysts: {verdict.get("strong_buy_catalysts")}

Answer questions concisely and directly. Use the data above. Be honest about uncertainties. Keep answers under 200 words unless a detailed explanation is requested."""

    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    if req.provider == "deepseek":
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {req.api_key}", "content-type": "application/json"}
        body = {"model": req.model, "max_tokens": 1024, "temperature": 0.3,
                "messages": [{"role": "system", "content": system_prompt}] + messages}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                raise HTTPException(500, f"AI chat failed: {r.status_code}")
            text = r.json()["choices"][0]["message"]["content"]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Chat error: {exc}")
    else:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": req.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": req.model, "max_tokens": 1024, "temperature": 0.3,
                "system": system_prompt, "messages": messages}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                raise HTTPException(500, f"AI chat failed: {r.status_code}")
            text = r.json()["content"][0]["text"]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Chat error: {exc}")

    return {"role": "assistant", "content": text}
