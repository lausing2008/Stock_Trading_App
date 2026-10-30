"""Prospective, frozen-membership cohorts with a publication-safe entry close."""
from datetime import datetime, timedelta, timezone
import math
from statistics import mean
from zoneinfo import ZoneInfo

from common.market_calendar import MIN_COVERAGE_YEAR, session_close

POLICY = "valuation-outcomes-v1"
HORIZONS = (5, 20, 60)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def freeze(report):
    data = (report.payload or {}).get("fields", {}).get("valuation", {}).get("value") or {}
    rows = data.get("rows") or []
    return {"report_id": report.id, "report_version": report.version,
            "published_at": max(utc(report.generated_at), utc(report.cutoff_at)).isoformat(),
            "source_price_session": data.get("price_session"), "source_policy": data.get("policy_version"),
            "groups": {
                "qualified": sorted({r["symbol"] for r in rows if r.get("qualified") and not r.get("exclusions")}),
                "undervalued_research": sorted({r["symbol"] for r in rows if r.get("classification") == "undervalued" and not r.get("exclusions")})}}


def plan(cohort):
    published = utc(cohort["published_at"])
    day = published.astimezone(ZoneInfo("America/New_York")).date()
    sessions = []
    for _ in range(370):
        if day.year > MIN_COVERAGE_YEAR:
            raise ValueError("US calendar coverage must be extended before outcomes can be measured")
        close = session_close("US", day)
        if close and close > published:
            sessions.append((day.isoformat(), close + timedelta(minutes=30)))
        if len(sessions) > max(HORIZONS):
            return {h: {"entry_session": sessions[0][0], "end_session": sessions[h][0],
                        "due_at": sessions[h][1]} for h in HORIZONS}
        day += timedelta(days=1)
    raise ValueError("Could not establish the outcome session calendar")


def _price(prices, symbol, day):
    value = prices.get(symbol, {}).get(day)
    if isinstance(value, bool): return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None


def evaluate(cohort, prices, *, now):
    now = utc(now)
    schedule = plan(cohort)
    results, used = [], {}
    for horizon, item in schedule.items():
        start, end = item["entry_session"], item["end_session"]
        mature = now >= item["due_at"]
        benchmark = None
        if mature:
            a, b = _price(prices, "SPY", start), _price(prices, "SPY", end)
            if a and b:
                benchmark = (b / a - 1) * 100
                used.setdefault("SPY", {}).update({start: a, end: b})
        groups = {}
        for name, members in cohort["groups"].items():
            observations = []
            for symbol in members:
                a, b = _price(prices, symbol, start), _price(prices, symbol, end)
                value = (b / a - 1) * 100 if mature and a and b else None
                if value is not None:
                    used.setdefault(symbol, {}).update({start: a, end: b})
                observations.append({"symbol": symbol, "return_pct": value,
                                     "state": "PENDING" if not mature else "OK" if value is not None else "UNAVAILABLE"})
            values = [o["return_pct"] for o in observations if o["return_pct"] is not None]
            complete = len(values) == len(members) and benchmark is not None
            state = "NO_COHORT" if not members else "PENDING" if not mature else "OK" if complete else "UNAVAILABLE"
            cohort_return = mean(values) if members and complete else None
            groups[name] = {"state": state, "expected": len(members), "observed": len(values),
                            "return_pct": cohort_return,
                            "excess_percentage_points": cohort_return - benchmark if cohort_return is not None else None,
                            "members": observations}
        results.append({"sessions": horizon, "entry_session": start, "end_session": end,
                        "due_at": item["due_at"].isoformat(), "benchmark_return_pct": benchmark, "groups": groups})
    return {"policy": POLICY, "report_id": cohort["report_id"], "evaluated_at": now.isoformat(),
            "published_at": cohort["published_at"], "benchmark": "SPY", "horizons": results,
            "price_observations": used,
            "notes": ["Entry is the first session close strictly after both publication and information cutoff, not the earlier screen price",
                      "Horizons count completed US sessions after the entry close with a 30-minute finalization buffer",
                      "Equal-weight frozen cohorts; no re-selection using later ratings",
                      "Returns use provider adjusted closes as retrieved; adjustment history can be revised",
                      "Any missing member or benchmark prevents an aggregate return; delisted/missing names are not silently dropped",
                      "Excess is the cohort return minus SPY return in percentage points; no costs or execution slippage are assumed",
                      "Research cohorts are descriptive and do not establish qualified recommendations"]}


def next_due(evaluation, *, now):
    if not any(g["expected"] for h in evaluation["horizons"] for g in h["groups"].values()):
        return None
    pending = [utc(h["due_at"]) for h in evaluation["horizons"]
               if any(g["state"] == "PENDING" for g in h["groups"].values())]
    if any(g["state"] == "UNAVAILABLE" for h in evaluation["horizons"] for g in h["groups"].values()):
        pending.append(utc(now) + timedelta(days=1))
    return min(pending) if pending else None
