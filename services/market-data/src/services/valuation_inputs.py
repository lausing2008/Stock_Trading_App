"""Bounded Yahoo discovery and frozen inputs for valuation reports; no watchlist writes."""
from __future__ import annotations

import json
import hashlib
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from threading import Event

import yfinance as yf

from common.market_calendar import MIN_COVERAGE_YEAR, session_close

SCREENS = ("undervalued_large_caps", "undervalued_growth_stocks", "most_actives")
MAX_SYMBOLS = 60
CACHE_SECONDS = 24 * 3600
STALE_SECONDS = 7 * CACHE_SECONDS
EXCHANGES = {"NYQ", "NMS", "NGM", "NCM", "ASE", "NYSE", "NASDAQ", "AMEX", "US"}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def closed_technical_inputs(cache, symbols, session_day, now):
    """One dated UW batch; a forming-session screener row can never become an EOD RSI."""
    wanted = sorted(set(symbols))
    if not wanted:
        return {}
    digest = hashlib.sha256(",".join(wanted).encode()).hexdigest()[:20]
    key = f"stockai:valuation:technical:v1:{session_day.isoformat()}:{digest}"
    try:
        cached = cache.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    try:
        from . import unusual_whales as U
        if not U.is_available():
            return {}
        rows = U._get("/api/screener/stocks", params={"ticker": ",".join(wanted),
                      "date": session_day.isoformat(), "limit": len(wanted)}, endpoint="/api/screener/stocks")
        result = {}
        for row in rows or []:
            symbol = row.get("ticker")
            if symbol not in wanted or row.get("date") != session_day.isoformat():
                continue
            value = number(row.get("rsi_14"))
            result[symbol] = {"as_of": row["date"], "retrieved_at": now.isoformat(),
                              "rsi_14": value if value is not None and 0 <= value <= 100 else None,
                              "eps_yoy_growth": number(row.get("eps_growth_4q")),
                              "provider": "Unusual Whales daily screener",
                              "endpoint": "/api/screener/stocks"}
        try:
            # Retry partial/missing coverage sooner; immutable complete sessions can be reused.
            cache.setex(key, CACHE_SECONDS if len(result) == len(wanted) else 300,
                        json.dumps(result, allow_nan=False))
        except Exception:
            pass
        return result
    except Exception:
        return {}  # Technical enrichment never takes down fundamentals/valuation coverage.


def completed_session(now: datetime):
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    if day.year > MIN_COVERAGE_YEAR:
        raise ValueError("US exchange calendar needs extending before a report can be generated")
    for _ in range(12):
        close = session_close("US", day)
        if close and close + timedelta(minutes=30) <= now:
            return day
        day -= timedelta(days=1)
    raise ValueError("No verified completed US session")


def select_symbols(quotes, tracked):
    """A declared sample, never an exhaustive US listing universe."""
    discovered = []
    for q in quotes:
        symbol = str(q.get("symbol") or "").upper()
        if (re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol)
                and q.get("exchange") in EXCHANGES
                and q.get("quoteType", "EQUITY") == "EQUITY"
                and symbol not in discovered):
            discovered.append(symbol)
    # Reserve space for discovery beyond the already-covered universe.
    chosen = discovered[:40]
    chosen.extend(s for s in sorted(set(tracked)) if s not in chosen)
    return chosen[:MAX_SYMBOLS], len(set(discovered) | set(tracked))


def _annuals(ticker, errors):
    """Keep same-period annual inputs rather than combining unrelated latest columns."""
    try:
        income, cash, balance = ticker.income_stmt, ticker.cashflow, ticker.balance_sheet
        common = sorted(set(income.columns) & set(cash.columns) & set(balance.columns), reverse=True)
        rows = []
        for period in common[:5]:
            def get(frame, *keys):
                for key in keys:
                    if key in frame.index:
                        value = number(frame.loc[key, period])
                        if value is not None:
                            return value
                return None
            rows.append({
                "period_end": period.date().isoformat(),
                "ebit": get(income, "EBIT"),
                "pretax_income": get(income, "Pretax Income"),
                "tax_provision": get(income, "Tax Provision"),
                "revenue": get(income, "Total Revenue"),
                "net_income": get(income, "Net Income"),
                "depreciation": get(cash, "Depreciation Depletion And Amortization", "Depreciation And Amortization"),
                "capital_expenditure": get(cash, "Capital Expenditure"),
                "change_working_capital": get(cash, "Change In Working Capital"),
                "free_cashflow": get(cash, "Free Cash Flow"),
                "debt": get(balance, "Total Debt"),
                "cash": get(balance, "Cash Cash Equivalents And Short Term Investments",
                            "Cash And Cash Equivalents"),
                "preferred": get(balance, "Preferred Stock Equity", "Preferred Stock"),
                "minority_interest": get(balance, "Minority Interest"),
                "equity": get(balance, "Stockholders Equity"),
            })
        return rows
    except Exception:
        errors.append("annual_statements_unavailable")
        return []


def _fetch(symbol, session_day, now):
    ticker = yf.Ticker(symbol)
    info = ticker.get_info() or {}
    if not info or not info.get("marketCap"):
        raise ValueError("No usable Yahoo company profile")
    errors = []
    bars = []
    try:
        history = ticker.history(period="1y", interval="1d", auto_adjust=False, timeout=20)
        for ts, row in history.iterrows():
            if ts.date() > session_day:
                continue
            bars.append({"date": ts.date().isoformat(), "close": number(row.get("Close")),
                         "adj_close": number(row.get("Adj Close")),
                         "volume": number(row.get("Volume"))})
    except Exception:
        errors.append("price_history_unavailable")
    actions = []
    try:
        frame = ticker.upgrades_downgrades
        if frame is not None:
            for ts, row in frame.sort_index(ascending=False).head(30).iterrows():
                day = ts.date()
                if 0 <= (now.date() - day).days <= 90:
                    actions.append({"date": day.isoformat(), "firm": str(row.get("Firm") or "Unknown"),
                                    "rating": str(row.get("ToGrade") or "Unknown"),
                                    "action": str(row.get("Action") or ""),
                                    "target": number(row.get("currentPriceTarget")),
                                    "prior_target": number(row.get("priorPriceTarget"))})
    except Exception:
        errors.append("analyst_actions_unavailable")
    keys = ("marketCap", "trailingPE", "forwardPE", "trailingEps", "forwardEps", "pegRatio",
            "profitMargins", "revenueGrowth", "earningsGrowth", "freeCashflow", "sharesOutstanding",
            "targetMeanPrice", "targetMedianPrice", "targetLowPrice", "targetHighPrice",
            "numberOfAnalystOpinions", "recommendationMean", "shortPercentOfFloat", "dateShortInterest")
    out = {"symbol": symbol, "name": info.get("longName") or info.get("shortName") or symbol,
           "sector": info.get("sector"), "industry": info.get("industry"),
           "currency": info.get("currency"), "financial_currency": info.get("financialCurrency"),
           "exchange": info.get("exchange"), "quote_type": info.get("quoteType"),
           "country": info.get("country"), "fetched_at": now.isoformat(),
           "info": {k: number(info.get(k)) for k in keys},
           "recommendation": info.get("recommendationKey"),
           "bars": sorted(bars, key=lambda b: b["date"]), "analyst_actions": actions,
           "annuals": _annuals(ticker, errors), "errors": errors,
           "source_url": f"https://finance.yahoo.com/quote/{symbol}/"}
    return out


def collect_inputs(cache, tracked, *, now=None, progress=None):
    """Called by an explicit background refresh, not on a page-view read."""
    now = now or datetime.now(timezone.utc)
    day = completed_session(now)
    quotes, failures = [], []
    for screen in SCREENS:
        try:
            result = yf.screen(screen, count=40)
            quotes.extend(result.get("quotes") or [])
        except Exception:
            failures.append(screen)
    uw_discovery = False
    if failures:
        try:
            from .unusual_whales import get_stock_screener
            uw_quotes = get_stock_screener(limit=60)
            quotes.extend({"symbol": q.get("ticker"), "exchange": "US", "quoteType": "EQUITY"}
                          for q in uw_quotes if not q.get("is_index") and q.get("sector"))
            uw_discovery = bool(uw_quotes)
        except Exception:
            pass
    symbols, discovered = select_symbols(quotes, tracked)
    if not symbols:
        raise ValueError("Discovery returned no US companies and there are no covered US symbols")

    yahoo_failed = Event()
    if len(failures) == len(SCREENS):
        yahoo_failed.set()

    def load(symbol):
        key = f"stockai:valuation:input:v1:{symbol}"
        try:
            raw = cache.get(key)
            old = json.loads(raw) if raw else None
        except Exception:
            old = None
        if old and old.get("bars") and old["bars"][-1]["date"] == day.isoformat():
            stamp = datetime.fromisoformat(old["fetched_at"])
            if (now - stamp).total_seconds() < CACHE_SECONDS:
                return old
        try:
            if not yahoo_failed.is_set():
                try:
                    value = _fetch(symbol, day, now)
                except Exception:
                    yahoo_failed.set()
                    from .valuation_uw import fetch
                    value = fetch(symbol, day, now)
            else:
                from .valuation_uw import fetch
                value = fetch(symbol, day, now)
            try:
                cache.setex(key, STALE_SECONDS, json.dumps(value, allow_nan=False))
            except Exception:
                value["errors"].append("input_cache_write_failed")
            return value
        except Exception:
            if old:
                return {**old, "errors": sorted(set(old.get("errors", []) + ["refresh_failed_using_old_inputs"]))}
            return {"symbol": symbol, "name": symbol, "errors": ["yahoo_inputs_unavailable"],
                    "info": {}, "bars": [], "annuals": [], "analyst_actions": []}

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row in pool.map(load, symbols):
            rows.append(row)
            if progress:
                progress(len(rows), len(symbols))
    # One dated technical batch; short-interest enrichment remains cache-only.
    technical = closed_technical_inputs(cache, symbols, day, now)
    for row in rows:
        row["technical"] = technical.get(row["symbol"])
        try:
            raw = cache.get(f"stockai:uw:short_interest:{row['symbol']}")
            row["uw_short_interest"] = json.loads(raw) if raw else None
        except Exception:
            row["uw_short_interest"] = None
        row["tracked"] = row["symbol"] in tracked
    return {"price_session": day.isoformat(), "rows": rows, "queries": list(SCREENS),
            "queries_failed": failures, "discovered": discovered, "sampled": len(symbols),
            "sample_limit": MAX_SYMBOLS, "uw_discovery": uw_discovery,
            "universe": "Yahoo/UW US stock screens plus covered US symbols; bounded sample, independent of watchlists",
            "uw_note": "Existing UW short-interest cache only; missing cache means enrichment unavailable"}
