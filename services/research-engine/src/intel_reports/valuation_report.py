"""Deterministic valuation research from frozen inputs. No network or ORM imports."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from statistics import median
from .candidate_qualification import resolve_review, REQUIRED_GATES
from .sector_valuation import model as sector_model

from intelligence.report_contract import (
    Evidence, EvidenceBook, Field, FieldState, StatementClass, Section,
    fields_fingerprint,
)

REPORT_TYPE = "us_equity_valuation"
SUBJECT = "valuation:US:covered-sample"
POLICY = "valuation-v5-phased-research"


def finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def metric(value=None, *, reason="Source did not provide this metric", state="UNAVAILABLE", unit=None):
    value = finite(value)
    return {"value": value, "state": "OK" if value is not None else state,
            "reason": None if value is not None else reason, "unit": unit}


def age_days(stamp, now):
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        return (now - parsed).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def rsi(closes, period=14):
    if len(closes) < 100 or any(finite(c) is None or c <= 0 for c in closes):
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    if gain == loss == 0:
        return 50.0
    return 100.0 if loss == 0 else round(100 - 100 / (1 + gain / loss), 2)


def dcf(fcff, shares, debt, cash, growth, discount, terminal=0.025, senior_claims=0):
    """FCFF -> enterprise value -> equity value -> per share; no FCFE/WACC mixture."""
    values = (fcff, shares, debt, cash, growth, discount, terminal, senior_claims)
    if (any(finite(v) is None for v in values) or fcff <= 0 or shares <= 0 or discount <= terminal
            or growth <= -1 or terminal <= -1 or min(debt, cash, senior_claims) < 0):
        return None
    cashflow, pv = fcff, 0.0
    for year in range(1, 6):
        cashflow *= 1 + growth
        pv += cashflow / (1 + discount) ** year
    terminal_pv = cashflow * (1 + terminal) / (discount - terminal) / (1 + discount) ** 5
    equity = pv + terminal_pv - debt - senior_claims + cash
    if equity <= 0:
        return None
    return {"fair_value": round(equity / shares, 2),
            "enterprise_value": round(pv + terminal_pv), "equity_value": round(equity),
            "terminal_value_share_pct": round(terminal_pv / (pv + terminal_pv) * 100, 1),
            "growth_pct": round(growth * 100, 2), "discount_rate_pct": discount * 100,
            "terminal_growth_pct": terminal * 100}


def valuation_model(raw, now):
    special = sector_model(raw, now)
    if special is not None:
        return special
    sector = str(raw.get("sector") or "").lower()
    industry = str(raw.get("industry") or "").lower()
    if sector in ("financial services", "financials", "finance", "real estate") or any(
            word in industry for word in ("bank", "insurance", "reit", "capital markets")):
        return {"state": "NOT_APPLICABLE", "reason": "This business needs a sector-specific valuation model"}
    if raw.get("financial_currency") != "USD" or raw.get("currency") != "USD":
        return {"state": "UNAVAILABLE", "reason": "USD share and financial-statement currency alignment is required"}
    rows = sorted(raw.get("annuals") or [], key=lambda r: r["period_end"], reverse=True)
    if len(rows) < 2:
        return {"state": "UNAVAILABLE", "reason": "At least two aligned annual statements are required"}
    latest = rows[0]
    period_age = (now.date() - date.fromisoformat(latest["period_end"])).days
    if not 0 <= period_age <= 450:
        return {"state": "STALE", "reason": "Latest annual period is outside the 450-day model limit"}
    proxy = latest.get("fcff_basis") == "operating_cashflow_plus_after_tax_interest_less_capex_proxy"
    needed = ("ebit", "pretax_income", "tax_provision", "capital_expenditure", "debt", "cash")
    needed += (("operating_cashflow", "interest_expense") if proxy else ("depreciation", "change_working_capital"))
    missing = [k for k in needed if finite(latest.get(k)) is None]
    shares = finite(raw.get("info", {}).get("sharesOutstanding"))
    if missing or not shares or shares <= 0:
        return {"state": "UNAVAILABLE", "reason": "Missing FCFF inputs: " + ", ".join(missing + ([] if shares else ["sharesOutstanding"]))}
    pretax = latest["pretax_income"]
    tax_rate = latest["tax_provision"] / pretax if pretax > 0 else None
    if tax_rate is None or not 0 <= tax_rate <= 0.5 or latest["ebit"] <= 0:
        return {"state": "NOT_APPLICABLE", "reason": "Positive operating profit and a usable effective tax rate are required"}
    # Yahoo's cash-flow statement stores capex and working-capital cash impact with signs.
    if latest["capital_expenditure"] > 0 or (not proxy and latest["depreciation"] < 0):
        return {"state": "CONFLICTING", "reason": "Unexpected cash-flow sign convention"}
    fcff = (latest["operating_cashflow"] + latest["interest_expense"] * (1 - tax_rate)
            + latest["capital_expenditure"]) if proxy else (
                latest["ebit"] * (1 - tax_rate) + latest["depreciation"]
                + latest["capital_expenditure"] + latest["change_working_capital"])
    first = rows[-1]
    years = (date.fromisoformat(latest["period_end"]) - date.fromisoformat(first["period_end"])).days / 365.25
    if not latest.get("revenue") or latest["revenue"] <= 0 or not first.get("revenue") or first["revenue"] <= 0 or years < 0.8:
        return {"state": "UNAVAILABLE", "reason": "Comparable positive annual revenue history is required for growth scenarios"}
    historical_growth = (latest["revenue"] / first["revenue"]) ** (1 / years) - 1
    base_growth = min(0.12, max(-0.05, historical_growth))
    missing_claims = [k for k in ("preferred", "minority_interest") if latest.get(k) is None]
    senior = sum(latest.get(k) or 0 for k in ("preferred", "minority_interest"))
    scenarios = {}
    for label, growth, discount in (("bear", base_growth - 0.03, 0.12),
                                     ("base", base_growth, 0.10),
                                     ("bull", base_growth + 0.03, 0.09)):
        result = dcf(fcff, shares, latest["debt"], latest["cash"], growth, discount, senior_claims=senior)
        if result is None:
            return {"state": "NOT_APPLICABLE", "reason": "FCFF or scenario equity value is nonpositive"}
        scenarios[label] = result
    return {"state": "OK", "method": "Five-year FCFF proxy scenario DCF" if proxy else "Five-year FCFF scenario DCF", "uncertainty": "High",
            "period_end": latest["period_end"], "scenarios": scenarios,
            "inputs": {"fcff": fcff, "shares_outstanding": shares, "debt": latest["debt"],
                       "cash": latest["cash"], "senior_claims": senior,
                       "effective_tax_rate_pct": round(tax_rate * 100, 2),
                       "historical_revenue_cagr_pct": round(historical_growth * 100, 2)},
            "limitations": ["Scenario assumptions are not calibrated forecasts or analyst fair values",
                            "Growth extrapolates historical revenue, capped at -5% to 12% in the base case",
                            "Discount rates 12%/10%/9% and terminal growth 2.5% are explicit scenario assumptions",
                            "Latest annual cash/debt and current basic shares; dilution and newer filings require review",
                            "Statement accounting basis and issuer publication date are not verified"]
                            + (["FCFF proxy uses operating cash flow plus after-tax interest expense less capex; cash-interest classification and tax shields require review"] if proxy else [])
                            + (["Missing senior claims assumed zero for scenarios: " + ", ".join(missing_claims)]
                               if missing_claims else []),
            "eligibility_blockers": ["Diluted share basis and latest material filings have not been verified"]
                                    + (["Senior claims are incomplete"] if missing_claims else [])}


def build_row(raw, session_day, now, quality=None):
    info = raw.get("info") or {}
    bars = sorted(raw.get("bars") or [], key=lambda b: b["date"])
    last = bars[-1] if bars else {}
    price = finite(last.get("close"))
    age = age_days(raw.get("fetched_at"), now)
    fresh = age is not None and 0 <= age <= 1.5 and last.get("date") == session_day
    exclusions = []
    if raw.get("quote_type") != "EQUITY" or raw.get("currency") != "USD":
        exclusions.append("US-listed USD common equity/ADR identity was not established")
    if raw.get("exchange") not in {"NYQ", "NMS", "NGM", "NCM", "ASE", "NYSE", "NASDAQ", "AMEX", "US"}:
        exclusions.append("Supported US listing exchange was not established")
    cap = finite(info.get("marketCap"))
    if cap is None or cap < 1e9:
        exclusions.append("Market cap below $1bn or unavailable")
    if price is None or price < 5:
        exclusions.append("Completed-session price below $5 or unavailable")
    recent = bars[-60:]
    liquid = len(recent) == 60 and all(
        finite(b.get("volume")) is not None and b["volume"] >= 0
        and finite(b.get("close")) is not None and b["close"] > 0 for b in recent)
    dollar_volume = sum(b["volume"] * b["close"] for b in recent) / 60 if liquid else None
    if dollar_volume is None or dollar_volume < 5e6:
        exclusions.append("60-session average dollar volume below $5m or unavailable")
    review, review_blockers, model_raw = resolve_review(raw, now)
    model = valuation_model(model_raw, now)
    if review and not review_blockers and model["state"] == "OK":
        model["eligibility_blockers"] = []
        model["limitations"] = [s for s in model["limitations"] if not (
            s.startswith("Latest annual cash/debt") or s.startswith("Statement accounting basis"))]
        model["limitations"].append("Reconciled issuer inputs and diluted-share basis from the dated qualification review; scenario uncertainty remains")
        model["inputs"]["review_version"] = review["version"]
    if review and review.get("owner_cash_sensitivity"):
        sensitivity = review["owner_cash_sensitivity"]
        owner_cash = sensitivity["operating_cashflow"] - sensitivity["capex"] - sensitivity["stock_compensation"]
        review["sensitivity"] = {"method": "Owner-cash equity sensitivity after stock compensation",
            "period_end": sensitivity["period_end"], "cashflow": owner_cash,
            "diluted_shares": sensitivity["diluted_shares"],
            "scenarios": {label: dcf(owner_cash, sensitivity["diluted_shares"], 0, 0, *assumptions)
                          for label, assumptions in sensitivity["scenarios"].items()},
            "limitations": ["OCF minus capex minus stock compensation is a conservative owner-cash proxy, not a calibrated forecast",
                            "Discount rates 12%/11%/10%, growth 0%/5%/8% and terminal growth 2%/2.5%/2.5% are research assumptions",
                            "Equity cash flow is discounted directly; no enterprise-to-equity debt/cash adjustment or buyback benefit is added",
                            "FY2025 diluted weighted-average shares are a conservative denominator, not current fully diluted shares; acquisitions and financing changes need reconciliation"]}
        # The DCF helper is an FCFF bridge. With zero debt/cash its arithmetic also gives an
        # equity sensitivity, but enterprise-value labels would misrepresent that result.
        for scenario in review["sensitivity"]["scenarios"].values():
            if scenario:
                scenario.pop("enterprise_value", None)
    if model["state"] == "OK" and not fresh:
        model = {**model, "state": "STALE", "reason": "Inputs or price session are stale"}
    state = "insufficient_evidence"
    fair = model.get("scenarios", {}).get("base", {}).get("fair_value")
    ratio = price / fair if price and fair else None
    discount = (1 - ratio) * 100 if ratio is not None else None
    upside = (fair / price - 1) * 100 if price and fair else None
    if model["state"] == "OK" and not exclusions:
        state = "undervalued" if ratio <= .8 else "overvalued" if ratio >= 1.2 else "near_fair_value"
    metrics = {}
    for key, source in (("peg", "pegRatio"), ("market_cap", "marketCap")):
        value = finite(info.get(source))
        metrics[key] = metric(value if value is not None and value > 0 else None,
                              reason="Positive provider value unavailable; PEG horizon/basis is not verified")
    # Yahoo's ready-made multiples may use a forming intraday quote. Reprice the EPS at the
    # frozen session close so the report never silently mixes two price observations.
    for key, source in (("trailing_pe", "trailingEps"), ("forward_pe", "forwardEps")):
        eps = finite(info.get(source))
        metrics[key] = metric(price / eps if price and eps and eps > 0 else None,
                              reason="Positive EPS and a completed-session close are required")
    closes = [b.get("adj_close") for b in bars]
    metrics.update({"price": metric(price, reason="No completed-session close"),
                    "rsi": metric(rsi(closes), reason="At least 100 valid adjusted daily closes required"),
                    "average_dollar_volume": metric(dollar_volume),
                    "fair_value": metric(fair), "price_fair_value": metric(ratio),
                    "discount_pct": metric(discount, unit="%"), "upside_pct": metric(upside, unit="%")})
    if raw.get("provider") == "Unusual Whales":
        metrics["rsi"]["reason"] = "UW price adjustment basis is unverified and no same-session provider RSI was supplied"
    technical = raw.get("technical") or {}
    value = finite(technical.get("rsi_14"))
    same_session = technical.get("as_of") == session_day
    if metrics["rsi"]["value"] is not None:
        metrics["rsi"].update(source="Calculated Wilder RSI", as_of=last.get("date"),
                               basis="14-period RSI on dividend/split-adjusted daily closes")
    elif same_session and value is not None and 0 <= value <= 100:
        metrics["rsi"] = {**metric(value), "source": technical.get("provider"), "as_of": technical["as_of"],
                          "basis": "Provider-reported 14 regular-session close periods; adjustment basis unverified"}
    growth = finite(technical.get("eps_yoy_growth"))
    metrics["eps_yoy_growth_pct"] = {**metric(growth * 100 if same_session and growth is not None else None,
                                            reason="No same-session reported EPS growth observation", unit="%"),
                                      "source": technical.get("provider"), "as_of": technical.get("as_of"),
                                      "basis": "Reported EPS versus four reports earlier, divided by absolute prior EPS; quarterly YoY context, not forward growth or TTM PEG"}
    # Stale numbers can be inspected but never masquerade as current classifications.
    if not fresh:
        for m in metrics.values():
            if m["value"] is not None:
                m.update(state="STALE", reason="Snapshot age or price session does not meet freshness policy")
    model_metrics = ("fair_value", "price_fair_value", "discount_pct", "upside_pct")
    if model["state"] != "OK":
        for k in model_metrics:
            metrics[k].update(state=model["state"], reason=model["reason"])
    actions, risks = [], []
    for a in raw.get("analyst_actions") or []:
        try:
            if not 0 <= (now.date() - date.fromisoformat(a["date"])).days <= 90:
                continue
        except (KeyError, ValueError):
            continue
        actions.append(a)
        rating = str(a.get("rating") or "").lower().replace(" ", "")
        if a.get("action") == "down" or rating in {"sell", "strongsell", "underperform", "underweight", "reduce"}:
            risks.append({"kind": "analyst", "text": f"{a.get('firm')}: {a.get('rating')} ({a.get('action')})", "date": a["date"]})
    if info.get("profitMargins") is not None and info["profitMargins"] < 0:
        risks.append({"kind": "financial", "text": "Negative provider profit margin", "date": raw.get("fetched_at")})
    terminal_share = finite(model.get("scenarios", {}).get("base", {}).get("terminal_value_share_pct"))
    if model.get("state") == "OK" and terminal_share is not None and terminal_share >= 70:
        risks.append({"kind": "model", "text": "At least 70% of modeled value depends on long-run assumptions", "date": model.get("period_end")})
    quality = quality or {"state": "insufficient_evidence", "gates": [],
                          "blocking": ["No stored Quality & Value evidence for this symbol"]}
    blockers = list(exclusions)
    blockers.extend(quality.get("blocking") or [])
    blockers.extend(model.get("eligibility_blockers") or [])
    blockers.extend(review_blockers)
    if state != "undervalued" or discount is None or discount < 30:
        blockers.append("High-uncertainty opportunity screen requires at least 30% model discount")
    if not fresh:
        blockers.append("Current completed-session inputs required")
    gates = quality.get("gates") or []
    by_gate = {g.get("gate"): g.get("status") for g in gates}
    missing_gates = [gate for gate in REQUIRED_GATES if gate not in by_gate]
    blockers.extend(f"Required quality gate missing: {gate}" for gate in missing_gates)
    blockers.extend(f"Required quality gate has not passed: {gate}" for gate in REQUIRED_GATES
                    if gate in by_gate and by_gate[gate] != "pass")
    if len(by_gate) != len(gates):
        blockers.append("Duplicate quality gates require review")
    qualified = all(by_gate.get(gate) == "pass" for gate in REQUIRED_GATES) and not blockers
    target = finite(info.get("targetMeanPrice"))
    targets = {"mean": target, "low": finite(info.get("targetLowPrice")),
               "high": finite(info.get("targetHighPrice")), "median": finite(info.get("targetMedianPrice")),
               "analyst_count": finite(info.get("numberOfAnalystOpinions")),
               "consensus": raw.get("recommendation"), "state": "UNKNOWN",
               "note": raw.get("analyst_note") or "Provider aggregate; publication dates and target horizon not supplied. Not verified as current.",
               "upside_pct": (target / price - 1) * 100 if target and price else None}
    thesis = []
    if state == "undervalued":
        thesis.append(f"Completed-session price is {discount:.1f}% below the base scenario; test the assumptions before treating this as value")
    if finite(info.get("revenueGrowth")) is not None:
        thesis.append(f"{raw.get('provider') or 'Yahoo'} reports revenue growth of {info['revenueGrowth'] * 100:.1f}%; confirm period and sustainability")
    if finite(info.get("profitMargins")) is not None:
        thesis.append(f"Reported profit margin: {info['profitMargins'] * 100:.1f}%")
    return {"symbol": raw["symbol"], "name": raw.get("name") or raw["symbol"],
            "provider": raw.get("provider") or ("Yahoo" if raw.get("fetched_at") else "Unavailable"), "eps_note": raw.get("eps_note"),
            "listing_basis": raw.get("listing_basis"),
            "sector": raw.get("sector") or "Unknown", "industry": raw.get("industry"),
            "currency": raw.get("currency"), "country": raw.get("country"),
            "tracked": bool(raw.get("tracked")), "classification": state,
            "qualified": qualified, "exclusions": exclusions, "blockers": sorted(set(blockers)),
            "metrics": metrics, "valuation": model, "analyst": targets, "analyst_actions": actions,
            "risk_flags": risks, "quality": quality, "thesis": thesis, "candidate_review": review,
            "research_checks": ["Verify latest filings, diluted shares and senior claims",
                                "Test competitive durability and recurring cash-flow growth",
                                "Revisit thesis if cash generation or competitive position deteriorates"],
            "catalysts": [], "catalyst_note": "No company-specific future catalysts verified by this report",
            "uw": raw.get("uw_short_interest"), "errors": raw.get("errors") or [],
            "fetched_at": raw.get("fetched_at"), "price_session": last.get("date"),
            "source_url": raw.get("source_url"), "input_age_days": int(age) if age is not None else None}


def build_report(inputs, *, now, quality=None, previous=None):
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    quality = quality or {}
    rows = [build_row(raw, inputs["price_session"], now, quality.get(raw["symbol"])) for raw in inputs["rows"]]
    # Peer multiples are descriptive context, never a substitute for an intrinsic value model.
    for row in rows:
        peers = [r["metrics"]["trailing_pe"]["value"] for r in rows
                 if r["symbol"] != row["symbol"] and row.get("industry") and r.get("industry") == row["industry"]
                 and r["metrics"]["trailing_pe"]["state"] == "OK"]
        row["peer_pe"] = {"count": len(peers), "median": median(peers) if len(peers) >= 10 else None,
                          "note": "Same-industry covered sample; at least 10 other positive P/E observations required"}
    rows.sort(key=lambda r: (not r["qualified"], r["classification"] != "undervalued",
                             -(r["metrics"]["discount_pct"]["value"] or -1e9), r["symbol"]))
    counts = {key: sum(r["classification"] == key for r in rows)
              for key in ("undervalued", "overvalued", "near_fair_value", "insufficient_evidence")}
    counts.update(reviewed=sum(bool(r.get("candidate_review")) for r in rows),
                  qualified=sum(r["qualified"] for r in rows), analyst_risk=sum(
        any(f["kind"] == "analyst" for f in r["risk_flags"]) for r in rows))
    old = {r["symbol"]: r for r in (previous or {}).get("rows", [])}
    policy_changed = bool(previous and previous.get("policy_version") != POLICY)
    changes = []
    for row in rows:
        prior = old.get(row["symbol"])
        kind = "new_coverage" if prior is None else "classification_changed" if prior["classification"] != row["classification"] else None
        if kind == "classification_changed" and policy_changed:
            kind = "methodology_changed"
        if kind:
            changes.append({"symbol": row["symbol"], "kind": kind,
                            "before": prior["classification"] if prior else None, "after": row["classification"]})
    data = {"rows": rows, "counts": counts, "price_session": inputs["price_session"],
            "universe": inputs["universe"], "discovered": inputs["discovered"],
            "sampled": inputs["sampled"], "sample_limit": inputs["sample_limit"],
            "queries": inputs["queries"], "queries_failed": inputs["queries_failed"],
            "uw_discovery": inputs.get("uw_discovery", False),
            "catalog": inputs.get("catalog"),
            "policy_version": POLICY, "changes": changes,
            "comparison_note": "Since previous generated snapshot; new coverage and methodology changes are not current rating transitions",
            "horizon": "3–5 year business research; five-year cash-flow scenarios",
            "notes": ["Bounded discovery sample, not all US listings; independent of user watchlists",
                      "Model fair values are conditional scenarios, not Morningstar ratings or analyst targets",
                      "RSI uses adjusted daily closes when available; otherwise dated UW daily-screener RSI is labeled as provider-reported with an unverified adjustment basis",
                      "Quarterly EPS YoY growth is historical context; it is not used to fabricate forward EPS or PEG",
                      "PEG is provider-reported; growth horizon and accounting basis are unverified",
                      "Qualified opportunities require all five Quality & Value gates, a sourced issuer review within 30 days, a filing check within seven days, reconciled inputs and a 30% model discount",
                      inputs.get("uw_note", "UW enrichment unavailable")]}
    book = EvidenceBook()
    ids = []
    for raw in inputs["rows"]:
        ids.append(book.add(Evidence(
            evidence_id=f"valuation:{raw['symbol']}", source=raw.get("source_url") or "Company inputs unavailable",
            value=raw, retrieved_at=(datetime.fromisoformat(raw["fetched_at"]) if raw.get("fetched_at") else None),
            observed_period=inputs["price_session"],
            note="Frozen provider inputs and optional cached UW short-interest data; issuer publication dates not verified")))
    for row in rows:
        review = row.get("candidate_review")
        if review:
            for source in review.get("sources", []):
                ids.append(book.add(Evidence(
                    evidence_id=f"issuer-review:{row['symbol']}:{review['version']}:{source['id']}",
                    source=source["url"], value={"source": source, "review": review},
                    observed_period=source.get("period_end"),
                    note="Dated code-reviewed issuer findings; source publication and review date are frozen in the value")))
    fields = {"valuation": Field(value=data, evidence_ids=ids,
                                  statement=StatementClass.CONDITIONAL_SCENARIO, section=Section.METRICS)}
    cov = {"total": len(rows), "ok": len(rows) - counts["insufficient_evidence"],
           "by_state": counts, "sourced": sum(bool(r.get("source_url")) for r in rows),
           "note": "Rows with supported classifications; does not establish investment suitability"}
    # Comparison depends on the previous report, not on the economic inputs. Exclude it from
    # idempotence or an unchanged second generation would manufacture a new snapshot.
    fingerprint_fields = {"valuation": Field(value={k: v for k, v in data.items() if k != "changes"},
                                              evidence_ids=ids, statement=StatementClass.CONDITIONAL_SCENARIO)}
    meta = {"report_type": REPORT_TYPE, "subject_key": SUBJECT, "market": "US",
            "policy_version": POLICY, "cutoff_at": now.replace(tzinfo=None), "status": "partial",
            "fingerprint": fields_fingerprint(fingerprint_fields, policy_version=POLICY, extra={"inputs": inputs, "quality": quality})}
    return fields, book, meta, cov
