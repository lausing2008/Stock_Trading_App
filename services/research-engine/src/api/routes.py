"""Planning Stage Research Intelligence Engine.

Aggregates data from all services, computes quantitative scores,
and calls Claude to generate qualitative company/industry/economic analysis.
Returns a full research report JSON.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone

import httpx
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from common.jwt_auth import get_current_username
from pydantic import BaseModel

from common.config import get_settings
from common.logging import get_logger
from common.indicators import sma as _canon_sma, rsi as _canon_rsi, macd as _canon_macd

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
_inflight_research: dict[str, asyncio.Event] = {}  # in-flight events; waiters pause until event fires
CACHE_TTL_SEC = 86_400       # 24 h — full quality reports
CACHE_TTL_PARTIAL_SEC = 1_800  # 30 min — partial (missing services)
CACHE_TTL_FALLBACK_SEC = 300   # 5 min — fallback (AI timeout/error)


# ── Request / Response models ─────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    provider: str = "claude"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    portfolio_size: float = 100_000.0
    max_risk_pct: float = 2.0


# ── Helpers ───────────────────────────────────────────────────────────────────

_REDIS_CLAUDE_KEY   = "stockai:admin:claude_api_key"
_REDIS_DEEPSEEK_KEY = "stockai:admin:deepseek_api_key"


def _get_admin_ai_key(provider: str = "claude") -> str:
    """Return the admin-stored AI API key from Redis, or '' if unavailable."""
    rkey = _REDIS_CLAUDE_KEY if provider == "claude" else _REDIS_DEEPSEEK_KEY
    try:
        import redis as redis_lib
        r = redis_lib.from_url(_s.redis_url, decode_responses=True, socket_connect_timeout=1)
        return r.get(rkey) or ""
    except Exception:
        return ""


import time as _time

_svc_token_cache: str = ""

def _svc_token() -> str:
    """Cached long-lived service JWT for inter-service calls."""
    global _svc_token_cache
    if _svc_token_cache:
        return _svc_token_cache
    from jose import jwt as _jwt
    payload = {
        "sub": "research-engine",
        "jti": str(__import__("uuid").uuid4()),
        "exp": int(_time.time()) + 365 * 86400,
    }
    _svc_token_cache = _jwt.encode(payload, _s.jwt_secret, algorithm="HS256")
    return _svc_token_cache


async def _get(client: httpx.AsyncClient, url: str, auth: str = "") -> dict | list | None:
    # T247-RESEARCHENGINE-GET-SILENT: a non-200 response (e.g. the "python-jose missing from
    # container" 401 pattern already documented multiple times in this repo's CLAUDE.md,
    # affecting signal-engine/ml-prediction/ranking-engine/portfolio-optimizer) previously fell
    # through to `return None` with NO log line — only the exception branch logged. Every
    # research report silently lost that upstream's data (signal/fundamentals/rankings) with
    # nothing in the logs to grep for, unlike the exception path.
    try:
        headers = {"Authorization": auth} if auth else {}
        r = await client.get(url, timeout=20, headers=headers)
        if r.status_code == 200:
            return r.json()
        log.warning("upstream.get.non_200", url=url, status=r.status_code)
    except Exception as exc:
        log.warning("upstream.get.failed", url=url, error=str(exc))
    return None


# T233-ARCH-INSERVICE-SPLITS: pure quant scoring functions extracted to scoring.py (see that
# module's own docstring). Re-exported here so every existing `from src.api.routes import X`
# call site — both route handlers below and every test file in tests/ — keeps working
# unchanged; this is a pure file-layout split, no behavior change.
from ..scoring import (  # noqa: E402,F401
    _atr,
    _last,
    _second_last,
    _institutional_ownership_pct,
    _fmt_cap,
    _score_technical,
    _rsi_interp,
    _macd_interp,
    _hist_interp,
    _sector_bench,
    _score_fundamental,
    _build_checklist,
    _position_sizing_matches,
    _position_size,
    _dcf_fair_value,
)


# ── Claude integration ────────────────────────────────────────────────────────

async def _call_claude(req: ResearchRequest, symbol: str, stock: dict, fund: dict,
                       tech: dict, fund_scores: dict, live_price: float = 0.0,
                       catalyst: dict | None = None) -> dict:
    api_key = req.api_key.strip() or _get_admin_ai_key(req.provider)
    if not api_key:
        return _fallback_ai()
    req = req.model_copy(update={"api_key": api_key})
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

    catalyst_summary = {
        "catalyst_score": (catalyst or {}).get("catalyst_score"),
        "insider_score": (catalyst or {}).get("insider_score"),
        "congress_score": (catalyst or {}).get("congress_score"),
        "institutional_score": (catalyst or {}).get("institutional_score"),
        "earnings_score": (catalyst or {}).get("earnings_score"),
        "composite_score": (catalyst or {}).get("composite_score"),
    }
    catalyst_note = (
        "Scores are 0-100 (positive) or negative (bearish). "
        "insider_score >50 = cluster of executive purchases; congress_score >30 = recent congressional buys; "
        "catalyst_score >60 = strong positive catalyst; <0 = adverse events or net selling."
    )

    user_prompt = f"""Analyze {symbol} ({name}) for investment suitability. Current price: ${price}. Market cap: {market_cap}. Sector: {sector}.

TECHNICAL DATA:
{json.dumps(tech_summary, indent=2)}

FUNDAMENTAL DATA:
{json.dumps(fund_summary, indent=2)}

CATALYST & EVENT INTELLIGENCE ({catalyst_note}):
{json.dumps(catalyst_summary, indent=2)}

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
      "pct": {_institutional_ownership_pct(fund)},
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
        # 90s limit: the gateway allows 240s for research POST requests; keeping AI under 90s
        # leaves buffer for data-gather (25s) and response serialisation.
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
    except Exception as exc:
        log.warning("claude.call.failed", error=str(exc))
        return _fallback_ai()

    if r.status_code == 429:
        log.warning("claude.rate_limited", status=r.status_code, body=r.text[:200])
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
        "_is_fallback": True,
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
            "can_buy_today": "INSUFFICIENT_DATA",
            "why": "AI analysis failed. Please check your API key and retry.",
            "biggest_risks": ["AI analysis unavailable"],
            "must_improve": ["Resolve AI connection"],
            "strong_buy_catalysts": ["Retry with valid API key"],
            "confidence_pct": 0,
            "final_recommendation": "INSUFFICIENT DATA",
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
    """T233-ARCH-INDICATOR-DEDUP (pilot): now uses shared/common/indicators.py — the same
    canonical RSI (Wilder's smoothing)/MACD formulas as technical-analysis — instead of a
    standalone reimplementation. The prior standalone RSI used a simple rolling mean for
    gain/loss instead of Wilder's smoothing, a real formula divergence (mean abs difference
    ~7.4 RSI points, max ~26 points, verified against real AAPL 1y data) that could show a
    different reading here than the same stock's stock/[symbol].tsx or /ta/{symbol} page.
    """
    closes = hist["Close"]
    sma50 = _canon_sma(closes, window=50)
    sma200 = _canon_sma(closes, window=200)
    rsi_series = _canon_rsi(closes, window=14)
    macd_df = _canon_macd(closes, fast=12, slow=26, signal=9)

    def to_list(s):
        return [None if pd.isna(v) else round(float(v), 4) for v in s]

    return {"values": {
        "sma_50": to_list(sma50),
        "sma_200": to_list(sma200),
        "rsi_14": to_list(rsi_series),
        "macd_line": to_list(macd_df["macd"]),
        "signal_line": to_list(macd_df["signal"]),
        "macd_histogram": to_list(macd_df["hist"]),
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


def _yf_fundamentals(sym: str) -> dict:
    """Direct yfinance fundamentals fallback — used when market-data cache is cold."""
    try:
        info = yf.Ticker(sym).info or {}
        if not info:
            return {}
        return {
            "revenue_growth":    info.get("revenueGrowth"),
            "earnings_growth":   info.get("earningsGrowth"),
            "profit_margin":     info.get("profitMargins"),
            "gross_margin":      info.get("grossMargins"),
            "operating_margin":  info.get("operatingMargins"),
            "total_revenue":     info.get("totalRevenue"),
            "total_cash":        info.get("totalCash"),
            "total_debt":        info.get("totalDebt"),
            "free_cashflow":     info.get("freeCashflow"),
            "trailing_pe":       info.get("trailingPE"),
            "ev_to_ebitda":      info.get("enterpriseToEbitda"),
            "ev_to_revenue":     info.get("enterpriseToRevenue"),
        }
    except Exception:
        return {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/batch")
async def get_research_batch(symbols: str, _: str = Depends(get_current_username)):
    """Return lightweight research summaries for multiple symbols (comma-separated).
    INT-10: Used by Opportunities page to show research chips on signal cards.
    Returns only: recommendation, overall_score, confidence, generated_at per symbol.
    Symbols with no cached report are omitted (no 404, just absent from response).
    """
    results = {}
    for raw in symbols.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            sym = _sanitise_symbol(raw)
        except ValueError:
            continue
        entry = _cache.get(sym)
        if not entry:
            continue
        report, ts = entry
        quality = report.get("report_quality", "full")
        ttl = CACHE_TTL_FALLBACK_SEC if quality == "fallback" else CACHE_TTL_PARTIAL_SEC if quality == "partial" else CACHE_TTL_SEC
        if (datetime.now(timezone.utc) - ts).total_seconds() >= ttl:
            _cache.pop(sym, None)
            continue
        results[sym] = {
            "recommendation": report.get("recommendation"),
            "overall_score": report.get("overall_score"),
            "confidence": report.get("confidence"),
            "generated_at": report.get("generated_at"),
        }
    return results


@router.get("/{symbol}/summary")
async def get_research_summary(symbol: str, _: str = Depends(get_current_username)):
    """Return lightweight cached research summary (INT-1: research badge on stock detail page).
    Returns: recommendation, overall_score, confidence, generated_at.
    404 if no cached report exists.
    """
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    entry = _cache.get(sym)
    if entry:
        report, ts = entry
        quality = report.get("report_quality", "full")
        ttl = CACHE_TTL_FALLBACK_SEC if quality == "fallback" else CACHE_TTL_PARTIAL_SEC if quality == "partial" else CACHE_TTL_SEC
        if (datetime.now(timezone.utc) - ts).total_seconds() < ttl:
            return {
                "recommendation": report.get("recommendation"),
                "overall_score": report.get("overall_score"),
                "confidence": report.get("confidence"),
                "generated_at": report.get("generated_at"),
            }
        _cache.pop(sym, None)
    raise HTTPException(404, "No cached research report.")


@router.get("/{symbol}")
async def get_research(symbol: str, _: str = Depends(get_current_username)):
    """Return cached research report (generated within last 24h)."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    entry = _cache.get(sym)
    if entry:
        report, ts = entry
        quality = report.get("report_quality", "full")
        ttl = CACHE_TTL_FALLBACK_SEC if quality == "fallback" else CACHE_TTL_PARTIAL_SEC if quality == "partial" else CACHE_TTL_SEC
        if (datetime.now(timezone.utc) - ts).total_seconds() < ttl:
            return report
        # Cache expired for this quality level — remove stale entry
        _cache.pop(sym, None)
    raise HTTPException(404, "No cached research report. POST to /research/{symbol} to generate.")


@router.delete("/{symbol}")
async def clear_research(symbol: str, _: str = Depends(get_current_username)):
    """Clear cached report to force regeneration."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    _cache.pop(sym, None)
    return {"status": "cleared", "symbol": sym}


@router.post("/{symbol}/trigger", status_code=202)
async def trigger_research(symbol: str, background_tasks: BackgroundTasks):
    """INT-4: Auto-trigger background research if no fresh report exists.
    No auth required — only reachable from internal Docker network.
    Cooldown: skips if a report younger than 6 hours is cached.
    """
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        return {"status": "skipped", "reason": "invalid symbol"}
    entry = _cache.get(sym)
    if entry:
        _, ts = entry
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < 21_600:  # 6 hours
            return {"status": "fresh", "symbol": sym, "age_hours": round(age / 3600, 1)}
    background_tasks.add_task(_generate_with_service_token, sym)
    return {"status": "triggered", "symbol": sym}


async def _generate_with_service_token(sym: str) -> None:
    """Generate research in background using a short-lived service JWT (no user context)."""
    try:
        import uuid as _uuid
        from jose import jwt as _jwt
        from datetime import timedelta
        expire = datetime.now(timezone.utc) + timedelta(hours=1)
        token = _jwt.encode(
            {"sub": "service", "role": "admin", "exp": expire, "jti": str(_uuid.uuid4())},
            _s.jwt_secret, algorithm="HS256",
        )
        async with httpx.AsyncClient(timeout=35) as client:
            await client.post(
                f"{_s.research_engine_url}/research/{sym}",
                json={"provider": "claude", "model": "claude-sonnet-4-6", "api_key": ""},
                headers={"Authorization": f"Bearer {token}"},
            )
        log.info("research.auto_trigger.done", symbol=sym)
    except Exception as exc:
        log.warning("research.auto_trigger.failed", symbol=sym, error=str(exc))


@router.post("/{symbol}")
async def generate_research(symbol: str, req: ResearchRequest, request: Request, _: str = Depends(get_current_username)):
    """Generate a full Planning Stage Research Report for the given symbol."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")

    # Cache check (fast path — no waiting)
    # T247-RESEARCHENGINE-CACHEKEY: also require the cached report's own baked-in
    # portfolio_size/max_risk_pct to match this request's — see _position_sizing_matches().
    entry = _cache.get(sym)
    if entry:
        report, ts = entry
        quality = report.get("report_quality", "full")
        ttl = CACHE_TTL_FALLBACK_SEC if quality == "fallback" else CACHE_TTL_PARTIAL_SEC if quality == "partial" else CACHE_TTL_SEC
        if (datetime.now(timezone.utc) - ts).total_seconds() < ttl and _position_sizing_matches(report, req):
            return report

    # Deduplicate concurrent AI calls for the same symbol using asyncio.Event.
    # If a request is already in-flight, wait for it to finish, then return from cache.
    if sym in _inflight_research:
        try:
            await asyncio.wait_for(_inflight_research[sym].wait(), timeout=60.0)
        except asyncio.TimeoutError:
            # Original caller died without cleaning up — remove stale event and compute ourselves
            _inflight_research.pop(sym, None)
        else:
            entry = _cache.get(sym)
            if entry:
                report, ts = entry
                # Use the same quality-based TTL as the main cache path, not a hardcoded value.
                # A fallback-quality report has TTL=300s; returning it for 6h would be stale.
                _q = report.get("report_quality", "full")
                _waiter_ttl = CACHE_TTL_FALLBACK_SEC if _q == "fallback" else CACHE_TTL_PARTIAL_SEC if _q == "partial" else CACHE_TTL_SEC
                # T247-RESEARCHENGINE-CACHEKEY: same portfolio-params check as the fast path —
                # the in-flight report that just finished was generated for WHOEVER triggered
                # it first, not necessarily this waiter's own portfolio_size/max_risk_pct.
                if (datetime.now(timezone.utc) - ts).total_seconds() < _waiter_ttl and _position_sizing_matches(report, req):
                    return report
            # Fell through (first caller had an error, or portfolio params didn't match) —
            # proceed to compute ourselves. AUD-RE-INFLIGHT-REREGISTER: the first caller
            # already popped this symbol's entry from _inflight_research right before firing
            # its event (see the pop()+set() pair below), so a THIRD concurrent request
            # arriving right now would see `sym not in _inflight_research` and start its own
            # duplicate generation instead of deduping against the one we're about to run.
            # Re-register so any later arrival waits on OUR generation instead.
            _inflight_research[sym] = asyncio.Event()
    else:
        _inflight_research[sym] = asyncio.Event()

    svc_auth = f"Bearer {_svc_token()}"

    # Gather data from all services in parallel
    async with httpx.AsyncClient(timeout=25) as client:
        stock_t, fund_t, prices_t, ind_t, levels_t, signal_t, rank_t, live_t, catalyst_t = await asyncio.gather(
            _get(client, f"{_s.market_data_url}/stocks/{sym}"),
            _get(client, f"{_s.market_data_url}/stocks/{sym}/fundamentals"),
            _get(client, f"{_s.market_data_url}/stocks/{sym}/prices?timeframe=1d&limit=260"),
            _get(client, f"{_s.technical_analysis_url}/ta/{sym}/indicators?days=400"),
            _get(client, f"{_s.technical_analysis_url}/ta/{sym}/levels"),
            # T237-RE1: without style=, GET /signals/{sym} returns {"signals": {SHORT:..., SWING:...}},
            # not a flat {"signal":..., "confidence":..., "horizon":...} shape — this call previously
            # omitted style=, so signal.get("signal")/get("confidence")/get("horizon") below were
            # always None on every single report, silently dead-ending the frontend's Signal badge.
            # style=SWING returns the flat shape directly, matching this app's default-style convention.
            _get(client, f"{_s.signal_engine_url}/signals/{sym}?style=SWING", svc_auth),
            _get(client, f"{_s.ranking_engine_url}/rankings/{sym}"),
            _get(client, f"{_s.market_data_url}/stocks/latest_prices?symbols={sym}"),
            _get(client, f"{_s.event_intelligence_url}/catalyst/{sym}", svc_auth),
        )

    stock = stock_t or {}
    fund = fund_t or {}
    # RES-FIX-1: when market-data fundamentals cache is cold, fall back to direct yfinance fetch
    if not fund:
        loop = asyncio.get_running_loop()
        fund = await loop.run_in_executor(None, _yf_fundamentals, sym)
    prices = prices_t or []
    indicators = ind_t or {"ts": [], "values": {}}
    levels = levels_t or {}
    signal = signal_t or {}
    ranking = rank_t or {}
    live = (live_t or [{}])[0] if isinstance(live_t, list) else {}
    catalyst = catalyst_t or {}

    if not stock:
        # Symbol not in DB — fetch directly from yfinance
        loop = asyncio.get_running_loop()
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
    fund_scores = _score_fundamental(fund, sector=stock.get("sector", "Unknown"), price=price)
    dcf = _dcf_fair_value(fund, price, sector=stock.get("sector", "Unknown"))

    # Call Claude for qualitative analysis
    ai = await _call_claude(req, sym, stock, fund, tech, fund_scores, live_price=price, catalyst=catalyst)

    # Determine report quality
    missing_services = sum([not fund_t, not signal_t, not rank_t, not ind_t])
    if ai.get("_is_fallback"):
        report_quality = "fallback"
    elif missing_services >= 2:
        report_quality = "partial"
    else:
        report_quality = "full"

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

    # Fallback reports must never show a real-looking verdict
    if report_quality == "fallback":
        recommendation = "INSUFFICIENT DATA"

    checklist = _build_checklist(tech, fund_scores, ai, raw_fund=fund)
    position = _position_size(tech, req.portfolio_size, req.max_risk_pct, price)

    report = {
        "symbol": sym,
        "company_name": stock.get("name", sym),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_quality": report_quality,
        "current_price": price,
        "market_cap": fund.get("market_cap"),
        "sector": stock.get("sector"),
        "industry": stock.get("industry") or stock.get("sector"),
        "recommendation": recommendation,
        "overall_score": overall,
        "confidence": 0 if report_quality == "fallback" else min(100, max(0, ai.get("confidence", 65))),
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
            # T237-RE2: "rank" removed — GET /rankings/{symbol} (ranking-engine) never returns a
            # "rank" key (only rs_rank + KScoreComponents: technical/momentum/value/growth/
            # volatility/score/fair_price/relative_strength), so this was always None. Not
            # currently rendered anywhere in the frontend — dropped rather than left as a
            # permanently-null stale contract; re-add for real if a peer-rank display is wanted.
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
        "dcf": dcf,
    }

    _cache[sym] = (report, datetime.now(timezone.utc))
    ttl = CACHE_TTL_FALLBACK_SEC if report_quality == "fallback" else CACHE_TTL_PARTIAL_SEC if report_quality == "partial" else CACHE_TTL_SEC
    log.info("research.generated", symbol=sym, overall=overall, recommendation=recommendation,
             quality=report_quality, cache_ttl_s=ttl)
    # Signal any waiters that the report is now cached, then remove the in-flight marker.
    ev = _inflight_research.pop(sym, None)
    if ev:
        ev.set()
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
async def chat_research(symbol: str, req: ChatRequest, _: str = Depends(get_current_username)):
    """Answer questions about the cached research report using AI."""
    try:
        sym = _sanitise_symbol(symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    entry = _cache.get(sym)
    if not entry:
        raise HTTPException(404, "No research report found. Generate a report first.")

    chat_api_key = req.api_key.strip() or _get_admin_ai_key(req.provider)
    if not chat_api_key:
        raise HTTPException(400, "No AI API key configured. Ask the admin to set a shared key in Settings → AI Assistant, or add your own key in Settings.")

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
        headers = {"Authorization": f"Bearer {chat_api_key}", "content-type": "application/json"}
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
        headers = {"x-api-key": chat_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
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
