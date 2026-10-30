"""Separate equity-model contracts for banks, REITs and loss-making businesses."""
from datetime import date
import math
from statistics import median


def number(value):
    if isinstance(value, bool): return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _missing(method, reason, **extra):
    return {"state": "UNAVAILABLE", "method": method, "reason": reason, "uncertainty": "High", **extra}


def model(raw, now):
    sector = str(raw.get("sector") or "").lower()
    industry = str(raw.get("industry") or "").lower()
    rows = sorted(raw.get("annuals") or [], key=lambda r: r["period_end"], reverse=True)
    latest = rows[0] if rows else {}
    bank = "bank" in industry
    reit = "reit" in industry
    ebit = number(latest.get("ebit"))
    loss = (ebit is not None and ebit <= 0 and sector not in {"financial services", "financials", "finance", "real estate"}
            and not any(word in industry for word in ("insurance", "capital markets")))
    if not (bank or reit or loss): return None
    method = "Bank common-book/ROE sensitivity" if bank else "REIT reported FFO multiple sensitivity" if reit else "Unprofitable-company cash-flow and dilution scenarios"
    if raw.get("currency") != "USD" or raw.get("financial_currency") != "USD":
        return _missing(method, "USD statement and share currency alignment is required")
    if not rows or not 0 <= (now.date() - date.fromisoformat(latest["period_end"])).days <= 450:
        return _missing(method, "A current annual statement within 450 days is required")
    shares = number(raw.get("info", {}).get("sharesOutstanding"))
    if not shares or shares <= 0:
        return _missing(method, "A positive share denominator is required")
    inputs, scenarios, limitations = {}, {}, []
    if bank:
        roes = []
        for current, prior in zip(rows[:3], rows[1:4]):
            a, b, profit = (number(current.get("common_equity")), number(prior.get("common_equity")), number(current.get("net_income_common")))
            span = (date.fromisoformat(current["period_end"]) - date.fromisoformat(prior["period_end"])).days
            if a is not None and b is not None and profit is not None and min(a, b) > 0 and 330 <= span <= 400:
                roes.append(profit / ((a + b) / 2))
        book = number(latest.get("common_equity"))
        if len(roes) < 2 or not book or book <= 0:
            return _missing(method, "Three common-equity annual balances and two attributable common-income periods are required; total equity and total earnings are not substituted")
        roe = median(roes)
        if not .03 < roe <= .30:
            return _missing(method, "Historical common ROE is outside the model's 3%–30% normalization range")
        for label, earning, cost, growth in (("bear", max(.025, roe - .03), .12, .02), ("base", roe, .105, .025), ("bull", min(.30, roe + .02), .095, .03)):
            if earning <= growth:
                return _missing(method, "Sustainable growth must be below the scenario return on common equity")
            value = book / shares * (earning - growth) / (cost - growth)
            scenarios[label] = {"fair_value": round(value, 2), "equity_value": round(value * shares),
                "enterprise_value": None, "growth_pct": growth * 100, "discount_rate_pct": cost * 100,
                "terminal_growth_pct": growth * 100, "terminal_value_share_pct": 100.0}
        inputs = {"common_book_equity": book, "shares_outstanding": shares, "normalized_common_roe_pct": roe * 100}
        limitations = ["Steady-state common-equity model: justified P/B = (ROE − growth)/(cost of equity − growth)",
            "Historical median ROE is not a forecast; credit losses, regulatory capital and deposit durability require review",
            "Growth 2%/2.5%/3% and equity discount rates 12%/10.5%/9.5% are uncalibrated scenario assumptions",
            "All modeled value depends on the steady-state assumption; debt is not subtracted again from common equity"]
    elif reit:
        sourced = (raw.get("sector_inputs") or {}).get("reit") or {}
        ffo = number(sourced.get("ffo_common_per_diluted_share"))
        if not ffo or ffo <= 0 or sourced.get("period_end") != latest["period_end"] or not sourced.get("source_ids"):
            return _missing(method, "Issuer-reported annual FFO per diluted common share, matching fiscal period and reviewed sources are required; total depreciation is not treated as real-estate depreciation")
        for label, multiple in (("bear", 12), ("base", 15), ("bull", 18)):
            value = ffo * multiple
            scenarios[label] = {"fair_value": round(value, 2), "equity_value": round(value * shares),
                "enterprise_value": None, "growth_pct": None, "discount_rate_pct": None,
                "terminal_growth_pct": None, "terminal_value_share_pct": None, "multiple": multiple}
        inputs = {"reported_ffo_per_diluted_common_share": ffo, "shares_outstanding": shares}
        limitations = ["12x/15x/18x are explicit FFO sensitivities, not a calibrated intrinsic value or NAV",
            "FFO is not cash available to owners; maintenance spending, leasing costs, leverage and AFFO reconciliation need review",
            "No FFO is fabricated from total-company depreciation or ordinary EPS"]
    else:
        cash, debt, senior = number(latest.get("cash")), number(latest.get("debt")), None
        claims = [number(latest.get(k)) for k in ("preferred", "minority_interest")]
        if all(v is not None and v >= 0 for v in claims): senior = sum(claims)
        burn = number(latest.get("free_cashflow"))
        diagnostics = {"historical_cash_runway_months": cash / -burn * 12 if cash is not None and cash >= 0 and burn is not None and burn < 0 else None}
        forecasts = (raw.get("sector_inputs") or {}).get("unprofitable") or {}
        if set(forecasts) != {"bear", "base", "bull"} or any(v is None or v < 0 for v in (cash, debt, senior)):
            return _missing(method, "Three sourced five-year FCFF forecasts, explicit senior claims, funding sufficiency and projected diluted shares are required", diagnostics=diagnostics)
        for label, spec in forecasts.items():
            flows = [number(v) for v in spec.get("fcff", [])]
            discount, growth, diluted = (number(spec.get(k)) for k in ("discount", "terminal_growth", "diluted_shares"))
            if (len(flows) != 5 or any(v is None for v in flows) or flows[-1] <= 0
                    or discount is None or growth is None or diluted is None or diluted < shares
                    or not .05 <= discount <= .5 or not 0 <= growth <= .04
                    or discount <= growth or not spec.get("source_ids")):
                return _missing(method, "Forecasts need five finite cash flows, a positive terminal flow, discount above terminal growth and a non-decreasing diluted-share basis", diagnostics=diagnostics)
            funds = cash - debt - senior
            for flow in flows:
                funds += flow
                if funds < 0:
                    return _missing(method, "Forecast needs external financing; a financing and ownership model must be reviewed before valuation", diagnostics=diagnostics)
            terminal = flows[-1] * (1 + growth) / (discount - growth) / (1 + discount) ** 5
            enterprise = sum(flow / (1 + discount) ** (i + 1) for i, flow in enumerate(flows)) + terminal
            equity = enterprise + cash - debt - senior
            if enterprise <= 0 or equity <= 0:
                return _missing(method, "Scenario enterprise or common-equity value is nonpositive", diagnostics=diagnostics)
            scenarios[label] = {"fair_value": round(equity / diluted, 2), "enterprise_value": round(enterprise),
                "equity_value": round(equity), "growth_pct": None, "discount_rate_pct": discount * 100,
                "terminal_growth_pct": growth * 100, "terminal_value_share_pct": round(terminal / enterprise * 100, 1)}
        inputs = {"cash": cash, "debt": debt, "senior_claims": senior, "shares_outstanding": shares}
        limitations = ["Reviewed FCFF scenarios permit early losses but require sufficient existing funds; no financing proceeds are invented",
            "The funding test reserves reported debt and senior claims upfront; debt maturities and new financing are not modeled",
            "Projected diluted share counts and terminal cash generation are explicit assumptions, not forecasts guaranteed by analysts"]
    if not scenarios["bear"]["fair_value"] <= scenarios["base"]["fair_value"] <= scenarios["bull"]["fair_value"]:
        return _missing(method, "Bear/base/bull values are not ordered; review scenario assumptions")
    return {"state": "OK", "method": method, "uncertainty": "High", "period_end": latest["period_end"],
            "inputs": inputs, "scenarios": scenarios, "limitations": limitations,
            "eligibility_blockers": ["Sector assumptions, diluted shares and issuer-specific risks require sourced qualification review"]}
