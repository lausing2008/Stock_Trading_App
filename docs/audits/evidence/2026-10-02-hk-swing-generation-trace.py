"""READ-ONLY instrumented trace: why did HK SWING convert zero signals to BUY?

Distinguishes three explanations the follow-up and the review both say must be kept apart:

  no_opportunity      the fused score is genuinely far from the bar; nothing was suppressed
                      into failing, the evidence simply was not there
  suppression_bound   the score cleared the bar BEFORE compressions and fell under it after,
                      so a gate — not the evidence — decided the outcome
  stale_or_missing    a required input was absent, so the score could not have been right
                      whatever the market was doing

SELECT only. No writes, no config changes, nothing enabled.
"""
import json, sys, statistics as st, collections
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/shared")
from sqlalchemy import text
from db import SessionLocal

# The real SWING conversion rule, read from the deployed generator rather than retyped.
sys.path.insert(0, "/app/src")
import re
SRC = open("/app/src/generators/signals.py").read() if False else None
try:
    from src.generators.signals import _STYLE_PROFILES
except Exception:
    from generators.signals import _STYLE_PROFILES
SWING = _STYLE_PROFILES["SWING"]
BUY_T, HOLD_T = SWING["buy_threshold"], SWING["hold_threshold"]

OUT = {"buy_threshold_by_regime": BUY_T, "hold_threshold_by_regime": HOLD_T,
       "max_compress_ratio": SWING["max_compress_ratio"]}

with SessionLocal() as s:
    s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    rows = s.execute(text("""
        SELECT st.symbol, sg.signal, sg.confidence, sg.bullish_probability AS bp,
               sg.reasons, sg.ts
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE st.market = 'HK' AND sg.horizon = 'SWING'
          AND sg.ts > NOW() - INTERVAL '7 days'
    """)).all()

OUT["n_rows"] = len(rows)
OUT["by_signal"] = dict(collections.Counter(r.signal for r in rows))

regimes = collections.Counter((r.reasons or {}).get("market_regime") for r in rows)
OUT["regimes"] = dict(regimes)

bps = [r.bp for r in rows if r.bp is not None]
OUT["fused_score"] = {
    "n": len(bps), "min": round(min(bps), 4), "max": round(max(bps), 4),
    "median": round(st.median(bps), 4), "mean": round(st.mean(bps), 4),
    "p90": round(sorted(bps)[int(len(bps) * 0.90)], 4) if bps else None,
    "n_above_0.5": sum(1 for b in bps if b > 0.5),
}

# How far is the best signal from the bar it must clear?
def bar(r):
    return BUY_T.get((r.reasons or {}).get("market_regime") or "unknown", 0.72)

gaps = sorted(((bar(r) - r.bp), r.symbol, round(r.bp, 4), bar(r))
              for r in rows if r.bp is not None)
OUT["closest_to_buy"] = [{"gap_to_bar": round(g, 4), "symbol": sym, "fused": b, "bar": t}
                         for g, sym, b, t in gaps[:10]]
OUT["n_within_0.02_of_bar"] = sum(1 for g, *_ in gaps if g <= 0.02)
OUT["n_within_0.05_of_bar"] = sum(1 for g, *_ in gaps if g <= 0.05)

# Counterfactual: the SAME scores against other styles' bars. Does the bar explain it?
for name, t in (("SHORT_bar_0.62", 0.62), ("GROWTH_like_0.60", 0.60), ("SWING_pre_SA32_0.67", 0.67)):
    OUT[f"would_buy_at_{name}"] = sum(
        1 for r in rows if r.bp is not None and r.bp >= t)

# Compression evidence: which gates were live on these rows?
comp = collections.Counter()
for r in rows:
    rs = r.reasons or {}
    if rs.get("hk_low_liquidity"): comp["hk_low_liquidity"] += 1
    if rs.get("hot_news"): comp["hot_news_flag_present"] += 1
    bp_ = rs.get("breadth_pct")
    if bp_ is not None and bp_ < 40: comp["breadth_below_40"] += 1
    adx = rs.get("adx")
    if adx is not None and adx < SWING["adx_min"]: comp["adx_below_min"] += 1
    if rs.get("hsi_bear_gate") or rs.get("hsi_regime"): comp["hsi_gate_field_present"] += 1
    for k in ("earnings_compression_applied", "rs_compression_applied",
              "news_compression_applied", "compression_ratio", "compressed"):
        if rs.get(k) is not None: comp[f"field_{k}"] += 1
OUT["compression_evidence"] = dict(comp)

# Input completeness: a score built on absent inputs is not a verdict about the market.
missing = collections.Counter()
for r in rows:
    rs = r.reasons or {}
    for k in ("ta_score", "calibrated_ta_score", "ml_test_auc", "adx", "breadth_pct",
              "market_regime", "fear_greed_score", "rsi", "macd_hist"):
        if rs.get(k) is None: missing[k] += 1
    if r.bp is None: missing["bullish_probability"] += 1
OUT["missing_inputs"] = dict(missing)

# TA vs ML: is the TA side itself bearish, or is ML dragging a bullish TA read down?
ta = [rs.get("ta_score") for rs in (r.reasons or {} for r in rows) if rs.get("ta_score") is not None]
if ta:
    OUT["ta_score"] = {"n": len(ta), "min": round(min(ta), 3), "max": round(max(ta), 3),
                       "median": round(st.median(ta), 3)}
cal = [rs.get("calibrated_ta_score") for rs in (r.reasons or {} for r in rows)
       if rs.get("calibrated_ta_score") is not None]
if cal:
    OUT["calibrated_ta_score"] = {"n": len(cal), "min": round(min(cal), 3),
                                  "max": round(max(cal), 3), "median": round(st.median(cal), 3)}

# Pillars: the structural read, independent of the fusion arithmetic.
pil = collections.Counter()
for r in rows:
    rs = r.reasons or {}
    pil[f"bullish_pillars_{rs.get('independent_pillars_active')}"] += 1
    pil[f"bearish_pillars_{rs.get('bearish_pillars_active')}"] += 1
OUT["pillars"] = dict(pil)

# Per-symbol best, so "no opportunity" can be checked against real names.
best = {}
for r in rows:
    if r.bp is None: continue
    if r.symbol not in best or r.bp > best[r.symbol]["fused"]:
        best[r.symbol] = {"fused": round(r.bp, 4), "signal": r.signal,
                          "regime": (r.reasons or {}).get("market_regime"),
                          "ta": (r.reasons or {}).get("ta_score"),
                          "adx": (r.reasons or {}).get("adx")}
OUT["per_symbol_best"] = dict(sorted(best.items(), key=lambda kv: -kv[1]["fused"])[:15])
OUT["n_symbols"] = len(best)

print(json.dumps(OUT, indent=2, default=str))
