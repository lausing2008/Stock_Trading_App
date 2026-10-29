"""UW fallback shapes verified against live OTIS responses; never infer missing PEG/forward EPS."""
from __future__ import annotations

from datetime import date

from .valuation_inputs import number


def normalize(symbol, profile, financials, candles, opinions, session_day, now):
    def annual(kind):
        return {r["fiscal_date_ending"]: r for r in financials.get(kind, [])
                if r.get("report_type") == "annual" and r.get("fiscal_date_ending")
                and r["fiscal_date_ending"] <= session_day.isoformat()}
    incomes, cashflows, balances = (annual(k) for k in ("income_statements", "cash_flows", "balance_sheets"))
    periods = sorted(set(incomes) & set(cashflows) & set(balances), reverse=True)[:5]
    rows = []
    for period in periods:
        income, cash, balance = incomes[period], cashflows[period], balances[period]
        capex = number(cash.get("capital_expenditures"))
        ocf = number(cash.get("operating_cashflow"))
        rows.append({"period_end": period, "ebit": number(income.get("ebit")),
                     "pretax_income": number(income.get("income_before_tax")),
                     "tax_provision": number(income.get("income_tax_expense")),
                     "revenue": number(income.get("total_revenue")),
                     "net_income": number(income.get("net_income")),
                     "depreciation": number(cash.get("depreciation_depletion_and_amortization")),
                     "capital_expenditure": -abs(capex) if capex is not None else None,
                     "change_working_capital": None,
                     "operating_cashflow": ocf, "interest_expense": number(income.get("interest_expense")),
                     "fcff_basis": "operating_cashflow_plus_after_tax_interest_less_capex_proxy",
                     "free_cashflow": ocf - abs(capex) if ocf is not None and capex is not None else None,
                     "debt": number(balance.get("short_long_term_debt_total")),
                     "cash": number(balance.get("cash_and_short_term_investments")),
                     "equity": number(balance.get("total_shareholder_equity")),
                     "preferred": None, "minority_interest": None,
                     "reported_currency": income.get("reported_currency"),
                     "source_updated_at": income.get("updated_at")})
    bars = []
    for bar in candles:
        day = bar.get("date")
        if day and day <= session_day.isoformat() and bar.get("market_time") == "r":
            bars.append({"date": day, "close": number(bar.get("close")),
                         "volume": number(bar.get("volume")), "adj_close": None})
    bars = sorted({b["date"]: b for b in bars}.values(), key=lambda b: b["date"])[-260:]
    earnings = {r["fiscal_date_ending"]: r for r in financials.get("earnings", [])
                if r.get("report_type") == "quarterly" and r.get("fiscal_date_ending")
                and r.get("report_date") and r["report_date"] <= now.date().isoformat()
                and r["fiscal_date_ending"] <= session_day.isoformat()}
    latest_eps = sorted(earnings, reverse=True)[:4]
    consecutive = len(latest_eps) == 4 and all(65 <= (date.fromisoformat(a) - date.fromisoformat(b)).days <= 120
                                              for a, b in zip(latest_eps, latest_eps[1:]))
    values = [number(earnings[p].get("reported_eps")) for p in latest_eps]
    eps = sum(values) if consecutive and all(v is not None for v in values) else None
    actions = []
    for opinion in opinions:
        if opinion.get("ticker") != symbol:
            continue
        stamp = str(opinion.get("timestamp") or "")[:10]
        try:
            if not 0 <= (now.date() - date.fromisoformat(stamp)).days <= 90:
                continue
        except ValueError:
            continue
        action = str(opinion.get("action") or "")
        actions.append({"date": stamp, "firm": opinion.get("firm") or "Unknown",
                        "rating": opinion.get("recommendation") or "Unknown",
                        "action": "down" if action in ("downgraded", "downgrade") else action,
                        "target": number(opinion.get("target")), "prior_target": None})
    issue = str(profile.get("issue_type") or "").lower()
    currency = incomes[periods[0]].get("reported_currency") if periods else None
    return {"symbol": symbol, "provider": "Unusual Whales", "name": profile.get("full_name") or symbol,
            "sector": profile.get("sector"), "industry": None, "country": None,
            "quote_type": "EQUITY" if issue in ("common stock", "adr") else "UNKNOWN",
            "exchange": "US", "currency": "USD", "financial_currency": currency,
            "listing_basis": "UW US-stock endpoint; exact exchange and quote currency not supplied in profile",
            "fetched_at": now.isoformat(), "source_url": f"https://unusualwhales.com/stock/{symbol}/overview",
            "info": {"marketCap": number(profile.get("marketcap")),
                     "sharesOutstanding": number(profile.get("outstanding")), "trailingEps": eps},
            "bars": bars, "annuals": rows, "analyst_actions": actions, "errors": ["yahoo_unavailable_using_uw"],
            "analyst_note": "UW dated firm actions, not an aggregate consensus. Target horizons are not supplied.",
            "eps_note": "TTM sum of four consecutive UW reported EPS quarters; GAAP/adjusted basis unverified"}


def fetch(symbol, session_day, now):
    from . import unusual_whales as U
    if not U.is_available():
        raise ValueError("UW fallback is not enabled")
    profile = U._get(f"/api/stock/{symbol}/info", endpoint="/api/stock/{symbol}/info")
    if not isinstance(profile, dict) or not profile.get("marketcap"):
        raise ValueError("UW company profile unavailable")
    financials = U._get(f"/api/stock/{symbol}/financials", endpoint="/api/stock/{symbol}/financials")
    candles = U._get(f"/api/stock/{symbol}/ohlc/1d", params={"limit": 500}, endpoint="/api/stock/{symbol}/ohlc/1d")
    opinions = U._get("/api/screener/analysts", params={"ticker": symbol, "limit": 30}, endpoint="/api/screener/analysts")
    return normalize(symbol, profile, financials or {}, candles or [], opinions or [], session_day, now)
