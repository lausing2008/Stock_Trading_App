# SA-33 — Early-Recovery Entry Timing Fix
**Date**: 2026-07-25 (corrected 2026-07-27 — see Finding 1)
**File changed**: `services/signal-engine/src/generators/signals.py`

---

## Correction (2026-07-27)

An adversarial review found Finding 1's original fix (0.25 partial credit) was mathematically
incapable of achieving its own stated goal. During genuine early recovery, `sma50_above_sma200`
and `golden_cross_event` are BOTH structurally `False` (they require `sma50 > sma200`, which by
definition hasn't happened yet) — so with the original 0.25 credit, the best achievable
`p_trend` was `0.25*0.30 + 1.0*0.20 + 1.0*0.10 = 0.375`, even with EVERY other confirming signal
present. It could never reach the claimed 0.5 active threshold. The credit has been corrected
to 0.70 (verified reachable — see below), and the section below is updated to describe the
corrected behavior. The original "either a supertrend cross-up OR an ADX bullish signal alone"
framing was mathematically unreachable at any credit ≤ 1.0 (the pillar's own scale) — the
corrected fix requires BOTH together instead, a stricter but genuinely achievable bar.

---

## Problem

BUY signals consistently fired at the top of moves rather than at the bottom. A stock that had already run 20% and was mid-rally would score highly across all 4 pillars. A stock that had pulled back 12% and was starting to recover would score near zero — the same as a stock still in freefall. Three structural biases in the pipeline caused this.

---

## Finding 1 — TREND pillar zero-scored early recoveries

### What was wrong

The TREND pillar scored `above_sma50=False` as `0.0 × 0.30 = 0` contribution — identical to a stock in freefall. A stock in early recovery (price reclaimed SMA20 but SMA50 hadn't caught up yet) scored the same on the trend pillar as a stock still declining. The pillar only lit up once the stock had already run far enough for the lagging SMA50 to confirm — i.e., after the move was already underway.

```python
# Before SA-33 — early recovery and freefall both scored 0.0:
p_trend = (
    (1.0 if above_sma50 else 0.0) * 0.30 +  # 0.0 whether recovering or falling
    _sma_golden_score              * 0.25 +
    (1.0 if bullish_trend else 0.0) * 0.20 +
    _gc_score                       * 0.15 +
    _st_score                       * 0.10
)
```

### Fix (corrected 2026-07-27)

Added `early_recovery_trend` detection: `price > SMA20 AND price < SMA50`. When true, the `above_sma50` contribution is **0.70** (not the originally-shipped 0.25) instead of 0.0.

**Why 0.70, not 0.25**: with the original 0.25 credit, the trend pillar's best achievable score during genuine early recovery was `0.25*0.30 + 1.0*0.20 + 1.0*0.10 = 0.375` — even with a supertrend cross-up AND an ADX bullish trend BOTH confirming. `_sma_golden_score` and `_gc_score` are structurally locked to 0.0 during early recovery (both require `sma50 > sma200`, which hasn't happened yet by definition), so there was no path to 0.5 with a 0.25 credit. The claim "reaches 0.5 when combined with a supertrend cross-up OR an ADX bullish signal" was checked arithmetically: the weaker single signal (supertrend cross-up, 0.10 weight) would need a credit of `(0.5-0.10)/0.30 = 1.33` — impossible, since the credit itself is a 0-1 score. The stronger single signal (ADX bullish, 0.20 weight) would need `(0.5-0.20)/0.30 = 1.0` — exactly at the pillar's own ceiling, leaving zero margin and no real "OR" case.

0.70 reaches `0.70*0.30 + 1.0*0.20 + 1.0*0.10 = 0.51` when BOTH a supertrend cross-up AND an ADX bullish trend confirm together. This is a stricter bar than the original "either one" framing (which was never actually achievable), but it's real and verified: alone the credit contributes only `0.70*0.30=0.21` (well below 0.5), and with only ONE of the two confirming signals it's still below 0.5 (`0.41` with ADX alone, `0.31` with supertrend alone) — the pillar genuinely requires both signals to activate, preserving the "cannot push the pillar above 0.5 on its own" invariant.

```python
# After SA-33 (corrected):
sma20_val = close.rolling(20).mean().iloc[-1]
_above_sma20 = bool(not pd.isna(sma20_val) and close.iloc[-1] > sma20_val)
_early_recovery_trend = _above_sma20 and not above_sma50

_above_sma50_score = 1.0 if above_sma50 else (0.70 if _early_recovery_trend else 0.0)
p_trend = (
    _above_sma50_score             * 0.30 +
    _sma_golden_score              * 0.25 +
    (1.0 if bullish_trend else 0.0) * 0.20 +
    _gc_score                       * 0.15 +
    _st_score                       * 0.10
)
```

### New `reasons` fields

| Field | Type | Value |
|-------|------|-------|
| `above_sma20` | bool | True when price > 20-day SMA |
| `early_recovery_trend` | bool | True when price > SMA20 but < SMA50 |

---

## Finding 2 — RS compression penalised recoveries

### What was wrong

The relative-strength compression (`rs_rank < 0.70` → ×0.85 compress) fired on every genuine recovery because the 20-day RS window captures the decline, not the turn. A stock that dropped 15% then started recovering always has poor 20d RS — the compression was punishing the exact setup it should reward. The existing `rs_absolute_floor` (skip compression if stock is up >5% in 20d) didn't help because a stock just starting to recover hasn't yet posted a positive 20d return.

```python
# Before SA-33 — no exemption for turning stocks:
if rs_comp is not None and rs_rank is not None and rs_rank < 0.70 and not rs_absolute_floor:
    fused = 0.5 + (fused - 0.5) * rs_comp   # always fired on recoveries
    reasons["rs_flag"] = "lagging_sector"
```

### Fix

Added `rs_recovery_floor`: RS compression is skipped when `RSI 28–45 AND stoch_rsi_cross_up=True`. The stoch cross-up guard ensures this only fires when momentum is actually turning, not just any oversold reading. A stock with RSI=35 and stoch still falling is not a recovery — it still gets compressed.

```python
# After SA-33:
_rsi_for_rs = base_reasons.get("rsi")
_stoch_cross_up_for_rs = base_reasons.get("stoch_rsi_cross_up", False)
rs_recovery_floor = (
    _rsi_for_rs is not None and 28 <= _rsi_for_rs <= 45
    and _stoch_cross_up_for_rs
)
if rs_comp is not None and rs_rank is not None and rs_rank < 0.70 \
        and not rs_absolute_floor and not rs_recovery_floor:
    fused = 0.5 + (fused - 0.5) * rs_comp
    reasons["rs_flag"] = "lagging_sector"
elif (rs_absolute_floor or rs_recovery_floor) and rs_rank is not None and rs_rank < 0.70:
    reasons["rs_flag"] = "lagging_sector_floor_applied"
```

### Updated `reasons` field

`rs_flag = "lagging_sector_floor_applied"` now covers two cases:
- Stock is up >5% in 20d (existing absolute-return floor)
- RSI 28–45 + stoch cross-up (new SA-33 recovery floor)

---

## Finding 3 — Weekly gate fired hardest at the bottom

### What was wrong

The weekly BUY gate (`weekly_rsi <= 38 AND weekly_trend == "down"` → up to 0.40× compression) was designed to block entries into confirmed structural downtrends. But `weekly_rsi <= 38` and `weekly_trend == "down"` is also the exact description of a stock at the bottom of a move. The gate fired with maximum compression (0.40×) precisely when a genuine recovery entry was most valuable — after 20+ consecutive weeks of low RSI.

```python
# Before SA-33 — no exception for turning stocks:
if (style_key in ("SWING", "LONG")
        and weekly_rsi is not None
        and weekly_rsi <= 38
        and weekly_trend == "down"):
    # fired at 0.40× on stocks that were bottoming, not just broken
    fused = 0.5 + (fused - 0.5) * _mult
```

### Fix

Added `_weekly_gate_recovery_exception`: the gate is bypassed when `stoch_rsi_cross_up=True AND pullback_recovery_delta >= 0.07`. Both conditions together mean the daily chart has real evidence of a turn — momentum crossing up from oversold AND volume-confirmed pullback recovery (the strongest tier of `_pullback_recovery()`). Without both, the gate still fires normally. A stock with stoch crossing up but no volume confirmation, or volume confirmation but stoch still falling, still gets the full weekly gate compression.

```python
# After SA-33:
_weekly_gate_recovery_exception = (
    base_reasons.get("stoch_rsi_cross_up", False)
    and (base_reasons.get("pullback_recovery_delta") or 0.0) >= 0.07
)
if (style_key in ("SWING", "LONG")
        and not p.get("skip_weekly_gate")
        and weekly_rsi is not None
        and weekly_trend is not None
        and weekly_rsi <= 38
        and weekly_trend == "down"
        and not _weekly_gate_recovery_exception):   # SA-33: skip if daily turn confirmed
    ...  # gate fires as before
else:
    reasons["weekly_gate_fired"] = False
    reasons["weekly_gate_bars"] = 0
    if _weekly_gate_recovery_exception:
        reasons["weekly_gate_recovery_exception"] = True
```

### New `reasons` field

| Field | Type | Value |
|-------|------|-------|
| `weekly_gate_recovery_exception` | bool | True when weekly gate bypassed by SA-33 |

---

## Combined effect

Before SA-33, a stock that had pulled back 12% with RSI at 38, stoch crossing up from oversold, and volume expanding on the recovery would:
- Score 0.0 on the trend pillar (below SMA50) → pillar inactive
- Get RS-compressed ×0.85 (poor 20d RS from the decline)
- Get weekly-gate-compressed up to ×0.40 (low weekly RSI + down weekly trend)
- Net effect: a fused probability of 0.75 would be pushed to ~0.51 — below the SWING buy threshold of 0.72

After SA-33 (corrected), the same setup:
- Scores 0.21 on the trend pillar alone (early recovery partial credit, `0.70*0.30`) — reaches
  0.51 (active) if BOTH a supertrend cross-up AND an ADX bullish trend also confirm; below 0.5
  with only one or neither
- Skips RS compression (stoch cross-up + RSI 28–45 exemption)
- Skips the weekly gate (stoch cross-up + volume-confirmed recovery exception)
- Net effect: the fused probability is preserved, allowing a genuine recovery to clear the buy
  threshold — PROVIDED the trend pillar also gets both confirming signals; a recovery with
  neither supertrend cross-up nor ADX bullish trend still has an inactive trend pillar, exactly
  as intended (the fix targets recoveries with real corroborating technical evidence, not any
  RSI dip)

---

## What did NOT change

- Buy thresholds — unchanged
- ML weight caps — unchanged
- Pillar gate `min_pillars_for_buy` requirements — unchanged
- All other compression filters (ADX, high-vol, breadth, earnings, news, sector ETF, options, S/R) — unchanged

SA-33 only removes three specific suppressions that were firing at the wrong time. It does not lower the bar for what constitutes a BUY signal.

---

## How to verify in production

```sql
SELECT
    symbol,
    ts,
    signal,
    confidence,
    reasons->>'rsi'                            AS rsi,
    reasons->>'stoch_rsi_cross_up'             AS stoch_cross_up,
    reasons->>'early_recovery_trend'           AS early_recovery,
    reasons->>'rs_flag'                        AS rs_flag,
    reasons->>'weekly_gate_fired'              AS weekly_gate,
    reasons->>'weekly_gate_recovery_exception' AS gate_exception,
    reasons->>'pullback_recovery_delta'        AS pr_delta
FROM signals
WHERE signal = 'BUY'
  AND ts >= now() - interval '7 days'
ORDER BY ts DESC
LIMIT 20;
```

Expected for a healthy early-recovery BUY signal:
- `rsi` between 28–45
- `stoch_cross_up = true`
- `early_recovery_trend = true`
- `rs_flag = lagging_sector_floor_applied` (not `lagging_sector`)
- `weekly_gate_fired = false` and `gate_exception = true`
- `pr_delta >= 0.07`
