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


# =====================================================================================
# Instrument type — because this shortlist is not all operating companies.
#
# The screen visibly returns QQQM and QLD. A reader scanning "Up — range break observed"
# down a list has no way to tell a single company from an index fund, and the two warrant
# different reading: a fund's break is a statement about its basket, and a LEVERAGED fund's
# is a statement about a daily-reset multiple of one.
#
# THERE IS NO AUTHORITATIVE INSTRUMENT CLASSIFICATION ANYWHERE — checked 2026-10-07, `stocks`
# carries only symbol/name/sector/industry/exchange/market/currency/cik/index_membership, and
# `exchange` separates venues (NASDAQ, NYSE, HKEX) rather than instrument types. So every result
# is an INFERENCE, reported with the basis that produced it and a `confidence` of `inferred`
# rather than asserted as fact. Statements and sector support an inference about what a listing
# is; they do not verify its legal type. Measured against the 189 active listings on
# 2026-10-07, which is what fixes the precedence below:
#
#   * 0 symbols have annual statements but no sector/industry. Statements are therefore a
#     SUFFICIENT signal for an operating company, with no observed counterexample.
#   * 7 symbols have NO statements but DO carry sector/industry — AMD, AMGN, ASTS, COHR,
#     COIN, MXL, TXN. All are plainly operating companies, so "no statements" alone would
#     have mislabelled seven of them as funds.
#   * 27 have neither. Those split into real funds (DIA, GDX, GLD, QLD, QQQ, QQQM...) and
#     companies whose identity was never resolved (AAOI, CBRS, HOOD, MUU...). Name matching
#     separates them where it can, and where it cannot the answer is UNVERIFIED — not a
#     guess in either direction.
# =====================================================================================

#: Name fragments that identify a pooled vehicle. Matched case-insensitively on whole words
#: where ambiguity would otherwise bite: "trust" appears in REIT names, so it only counts
#: alongside another fund marker.
_FUND_WORDS = ("etf", "fund", "tracker", "index", "shares", "spdr", "ishares",
               "invesco", "vaneck", "proshares", "direxion", "schwab u.s.", "amplify")
#: A daily-reset leveraged or inverse vehicle. §30 of the intelligence spec is explicit that
#: SOXL must not be treated like an ordinary 1x ETF; the same applies to QLD and TQQQ.
_LEVERAGE_WORDS = ("ultrapro", "ultra ", "2x", "3x", "leveraged", "daily semiconductor",
                   "bull 3", "bear 3", "inverse")


def classify_instrument(*, symbol: str, name: str | None, sector: str | None,
                        industry: str | None, has_annual_statements: bool,
                        declared_type: str | None = None) -> dict:
    """What kind of thing this row probably is, on what basis, and how strongly. Pure.

    EVERY RESULT HERE IS AN INFERENCE UNLESS `declared_type` IS SUPPLIED. Checked 2026-10-07:
    no authoritative instrument classification is stored anywhere — `stocks` carries only
    symbol, name, sector, industry, exchange (NASDAQ / NYSE / HKEX, which separates venues and
    not instrument types), market, currency, cik and index_membership. A provider quote type,
    an exchange security type or an issuer classification would be authoritative; none is
    ingested. `declared_type` is the parameter one would arrive through, and it is always None
    today.

    So `confidence` is `inferred` on every path below, and a caller rendering this must say so.
    Statements and sector metadata support an inference about what a listing *is*; they do not
    verify its legal instrument type, and a fund can perfectly well file financial statements.

    ABSENCE NEVER IMPLIES "FUND". The positive signal for a fund is the NAME; the absence of
    statements and sector only fails to contradict it. A listing with neither and no fund-like
    name is `unverified`, which is a real answer and not a lean in either direction.
    """
    if declared_type:
        return {"type": declared_type, "leveraged": False, "confidence": "declared",
                "basis": f"declared by the source as {declared_type}"}

    n = (name or "").strip()
    nl = n.lower()
    leveraged = any(w in nl for w in _LEVERAGE_WORDS)
    INFER = "inferred"

    if has_annual_statements:
        return {"type": "operating_company", "leveraged": False, "confidence": INFER,
                "basis": "annual financial statements are stored for this issuer. Inferred: "
                         "no authoritative instrument classification is available, and a fund "
                         "may also file statements"}
    if sector or industry:
        return {"type": "operating_company", "leveraged": False, "confidence": INFER,
                "basis": f"classified under sector/industry ({sector or industry}). Inferred "
                         f"from metadata, not from a declared instrument type"}
    if nl and nl != symbol.lower() and any(w in nl for w in _FUND_WORDS):
        return {"type": "fund", "leveraged": leveraged, "confidence": INFER,
                "basis": f"the NAME identifies a pooled vehicle"
                         f"{' with a daily-reset multiple' if leveraged else ''}. Inferred from "
                         f"the name alone — the absence of statements and sector does not "
                         f"establish this and is not what the inference rests on"}
    return {"type": "unverified", "leveraged": leveraged, "confidence": INFER,
            "basis": "no declared instrument type, no sector or industry, no stored statements, "
                     "and the name does not identify a fund — the instrument type is not "
                     "established either way, and absence is not evidence of a fund"}
