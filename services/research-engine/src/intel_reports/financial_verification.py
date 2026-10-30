"""Issuer annual facts with provenance and provider-reconciliation flags, never silent overrides."""
from copy import deepcopy
from datetime import date, datetime
import json
import math
import time

from .issuer_facts import _get, FACTS_URL, extract


def verify(payload, *, cik, now):
    if int(payload.get("cik") or 0) != int(cik):
        raise ValueError("SEC company-facts issuer mismatch")
    # Companyfacts includes later restatements. Cut by filing DAY for a current inventory;
    # this is not sufficient for intraday historical backtesting.
    filtered = deepcopy(payload)
    for taxonomy in (filtered.get("facts") or {}).values():
        for concept in taxonomy.values():
            for unit, entries in (concept.get("units") or {}).items():
                concept["units"][unit] = [e for e in entries if e.get("filed")
                    and date.fromisoformat(e["filed"]) <= now.date()
                    and isinstance(e.get("val"), (float, int)) and not isinstance(e["val"], bool)
                    and math.isfinite(e["val"])]
    facts = extract(filtered, cik=str(cik))
    if not facts.latest_period_end:
        raise ValueError("No usable SEC annual statement anchor")
    return {"state": "OK", **facts.as_dict(), "cik": int(cik), "checked_at": now.isoformat(),
            "source_url": FACTS_URL.format(cik=int(cik)),
            "scope": "US-GAAP annual facts only. Interim changes, issuer extensions and full dilution still require review; values do not replace the screen model."}


def collect(cache, reviews, *, now):
    result = {}
    deadline = time.monotonic() + 45
    for symbol, review in reviews.items():
        cik = int(review["cik"])
        key = f"stockai:valuation:sec-facts:v1:{cik}"
        try:
            try:
                value = json.loads(cache.get(key) or "null")
            except Exception:
                value = None
            if value and value.get("state") == "OK" and int(value.get("cik") or 0) == cik:
                observed = datetime.fromisoformat(value["checked_at"])
                if observed.tzinfo is not None and 0 <= (now - observed).total_seconds() < 86400:
                    result[symbol] = value
                    continue
            time.sleep(.15)
            if time.monotonic() >= deadline:
                raise TimeoutError("SEC enrichment budget exhausted")
            value = verify(_get(FACTS_URL.format(cik=cik), timeout=5), cik=cik, now=now)
            try:
                cache.setex(key, 86400, json.dumps(value))
            except Exception:
                pass
            result[symbol] = value
        except Exception:
            result[symbol] = {"state": "UNAVAILABLE", "cik": cik, "checked_at": now.isoformat(),
                              "reason": "SEC annual facts could not be verified; no provider values were replaced"}
    return result


def reconcile(raw, evidence):
    """Report disagreements without coalescing different periods or share measures."""
    if not evidence or evidence.get("state") != "OK":
        return []
    flags = []
    annuals = sorted(raw.get("annuals") or [], key=lambda row: row["period_end"], reverse=True)
    latest = annuals[0] if annuals else {}
    period = evidence.get("latest_period_end")
    if latest.get("period_end") != period:
        return [f"Provider annual period {latest.get('period_end') or 'unavailable'} differs from SEC {period}; numeric reconciliation requires the same fiscal period"]
    for provider, issuer, sign in (("revenue", "revenue", 1), ("operating_cashflow", "operating_cashflow", 1),
                                   ("capital_expenditure", "capex", -1)):
        sourced = (evidence.get("latest_annual") or {}).get(issuer)
        observed = latest.get(provider)
        if sourced and observed is not None:
            expected = sourced["value"] * sign
            if not math.isclose(observed, expected, rel_tol=.005, abs_tol=1):
                flags.append(f"Provider {provider} differs from same-period SEC fact by more than 0.5%; reconcile accounting scope")
    return flags
