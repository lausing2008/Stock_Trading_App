"""Uncalibrated daily price setups, independent of company-quality eligibility.

Input bars are ascending, exactly the supplied 21 completed session dates. Range levels
exclude the tested bar. No provider/LLM calls, probabilities, or trading side effects.
"""
from math import isfinite

POLICY = "daily-range-20-v1"


def assess(bars: list[dict], sessions: list[str]) -> dict:
    unknown = {"direction": "unknown", "label": "Unknown", "factors": [],
               "strategy": "Wait for usable completed-session prices.",
               "distance_pct": None, "volume_ratio": None}
    if len(bars) != 21 or [b["date"] for b in bars] != sessions:
        return {**unknown, "factors": ["Missing, stale or duplicate daily sessions"]}
    for b in bars:
        if any(not isinstance(b.get(k), (float, int)) or not isfinite(b[k])
               or b[k] <= 0 for k in ("close", "high", "low", "volume")):
            return {**unknown, "factors": ["Invalid OHLC or missing/zero volume"]}
        if not b["low"] <= b["close"] <= b["high"]:
            return {**unknown, "factors": ["Inconsistent OHLC"]}
    # A changing adjustment factor may indicate a split/dividend. Raw highs/lows cannot
    # be silently combined across it. Missing factors are disclosed, never inferred.
    factors = [b["adj_close"] / b["close"] for b in bars
               if isinstance(b.get("adj_close"), (float, int))
               and isfinite(b["adj_close"]) and b["adj_close"] > 0]
    if factors and max(factors) / min(factors) > 1.001:
        return {**unknown, "factors": ["Adjustment factor changed; reconcile corporate actions"]}
    prior, last = bars[:-1], bars[-1]
    resistance, support = max(b["high"] for b in prior), min(b["low"] for b in prior)
    close = last["close"]
    sma = sum(b["close"] for b in prior) / 20
    volume = last["volume"] / (sum(b["volume"] for b in prior) / 20)
    upper, lower = abs(close / resistance - 1) * 100, abs(close / support - 1) * 100
    if close > resistance:
        direction, label = "breakout", "Up — range break observed"
        strategy = "Watch for a hold/retest above resistance; a close back below invalidates this break."
    elif close < support:
        direction, label = "breakdown", "Down — range break observed"
        strategy = "Review downside exposure; wait for support to be reclaimed before a new long."
    elif upper <= 2 and close > sma:
        direction, label = "breakout_watch", "Upward setup — awaiting break"
        strategy = "Wait for a completed close above resistance and check volume; avoid chasing."
    elif lower <= 2 and close < sma:
        direction, label = "breakdown_watch", "Downward setup — awaiting break"
        strategy = "Watch support; a completed close below it triggers a downside review."
    else:
        direction, label = "range", "Inside range — no directional setup"
        strategy = "Wait for a boundary break; being inside a range does not predict unchanged prices."
    return {"direction": direction, "label": label, "strategy": strategy,
            "close": close, "support": support, "resistance": resistance,
            "session": last["date"], "sma20": sma, "volume_ratio": round(volume, 2),
            "distance_pct": round(upper if direction.startswith("breakout") else lower
                                  if direction.startswith("breakdown") else min(upper, lower), 2),
            "factors": [f"Close {'above' if close > sma else 'at/below'} prior 20-close average",
                        f"Volume {volume:.2f}× prior 20-session average",
                        f"Prior 20-session range {support:.2f}–{resistance:.2f}"],
            "limitations": ["Unadjusted prices; corporate-action verification is incomplete"
                            if len(factors) < 21 else "Unadjusted prices; adjustment factors checked",
                            "Earnings dates, news, spreads and liquidity suitability not checked",
                            "Technical setup only; quality/value gates remain separate"]}


def select_setups(rows: list[dict], market="ALL", direction="all", limit=20, sector=None):
    """Per-market ranking: observed breaks, then watches, then range; volume then proximity.

    Unknowns last. Local-currency turnover is never compared across markets.
    Ranking is a research ordering, not estimated success probability.
    """
    selected = []
    for venue in ("US", "HK"):
        if market not in ("ALL", venue):
            continue
        candidates = [r for r in rows if r["market"] == venue
                      and (direction == "all" or r["setup"]["direction"] == direction)
                      and (not sector or r.get("sector") == sector)]
        def key(r):
            s = r["setup"]
            tier = {"breakout": 0, "breakdown": 0, "breakout_watch": 1,
                    "breakdown_watch": 1, "range": 2}.get(s["direction"], 3)
            return (tier, -(s.get("volume_ratio") or 0),
                    s.get("distance_pct") if s.get("distance_pct") is not None else 999,
                    r["symbol"])
        selected.extend(sorted(candidates, key=key)[:limit])
    return selected
