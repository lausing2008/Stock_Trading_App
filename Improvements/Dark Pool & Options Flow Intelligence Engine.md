# Dark Pool & Options Flow Intelligence Engine

**Created:** 2025-08-22
**Updated:** 2025-08-22 (verified against production)
**Status:** DEFERRED — See "Production Measurement" section
**Priority:** Most features blocked on data or measured below chance

---

## ⚠️ Production Measurement (Critical Context)

**Before building any composite scoring system, we measured the underlying signals:**

| Family | Resolved Outcomes | 1-Day Hit Rate |
|--------|-------------------|----------------|
| Dark Pool | 372 | 44.9% |
| Options Flow | 1,621 | 39.5% |

**Both are below a coin flip on large samples.**

The original design proposed weighting: `smart×0.35 + dp×0.30 + opt×0.20` — i.e., 85% of the weight on families measured at 39-45%.

**Data depth constraints:**
- Dark pool history: 11 days (2026-09-04 → 09-14; 103,339 prints, 69 symbols)
- Options flow outcomes: 14 days
- Feature 5 (Multi-Day Accumulation) wants 5-day patterns → ~2 non-overlapping windows
- Feature 6 (Unusual Activity) needs 30-day baseline → **blocked, only 11 days exist**

**Dark pool directional alerts:** 64 alerts with a side, exactly 1 resolved.

**Recommendation:** Wait ~2-4 weeks for the T377 scoreboard to fill, then build only what the data justifies.

### Status re-verified 2026-09-15

Every figure above still holds, re-measured against production:

| | this doc | re-measured 2026-09-15 |
|---|---|---|
| Dark pool hit rate | 44.9% (n=372) | **44.9% (n=372)** ✅ |
| Options flow hit rate | 39.5% (n=1,621) | **39.5% (n=1,621)** ✅ |
| Alerts with a side / resolved | 64 / 1 | **66 / 1** ✅ |
| Dark pool history span | 11 days | **10 days** (2026-09-04 → 09-14) ✅ |

**The blocker has not moved: still 1 resolved directional outcome.** Revisit early October.

**One correction to a figure published elsewhere:** the dark-pool side distribution was reported
as 55.3% SELL / 34.7% BUY / 10.0% mid from a 611-print sample. On **38,993 prints across 52
symbols**, it is **36.8% / 35.3% / 27.9%** — the undeterminable band is ~2.8× wider than
reported. The classifier is correct (mid-band prints average a 0.074% spread vs 0.143% for
sided ones — tighter spreads genuinely put more prints near the midpoint); the small sample was
not. **More than a quarter of prints are honestly undeterminable**, which strengthens the
"observation, not an edge" caution rather than weakening it.

---

## Overview (Original Design — Now Deferred)

This document outlines enhancements to leverage dark pool and options flow data for improved trading signals. The goal is to transform raw flow data (already fetched via Unusual Whales) into actionable confirmation signals.

**Current State:**
- UW API client fully implemented (`unusual_whales.py`)
- Dark pool direction classification exists (`classify_dark_pool_side()`)
- Options flow data fetched but not aggregated into signals
- No historical tracking or multi-day pattern detection

**Target State (DEFERRED):**
- Real-time flow pressure scores integrated into signal engine
- Smart money filtering to reduce noise
- GEX regime awareness for position sizing
- Multi-day accumulation/distribution detection

---

## Feature 1: Net Premium Flow Score

**Priority:** ~~P0~~ → DEFERRED
**Effort:** Low (2-3 hours)
**Blocker:** Options flow measured at 39.5% hit rate — wiring into signal confidence risks making signals worse

### What's Real

- `total_ask_side_prem` / `total_bid_side_prem` exist and are parsed
- Your existing `compute_options_pressure_score()` deliberately measures conviction, not direction
- A directional score is a real gap — Feature 1 is not a duplicate

### Solution (Buildable, But Deferred)

```python
def options_pressure(symbol: str, hours: int = 4) -> float:
    """
    Aggregate options flow into directional pressure score.
    
    Returns:
        -1.0 (max bearish) to +1.0 (max bullish)
    """
    flow = get_options_flow(symbol, hours)
    if not flow:
        return 0.0
    
    bull_premium = 0.0
    bear_premium = 0.0
    
    for f in flow:
        ask_prem = f.total_ask_side_prem or 0
        bid_prem = f.total_bid_side_prem or 0
        
        if f.option_type == "call":
            bull_premium += ask_prem   # buying calls = bullish
            bear_premium += bid_prem   # selling calls = bearish
        else:  # put
            bear_premium += ask_prem   # buying puts = bearish
            bull_premium += bid_prem   # selling puts = bullish
    
    total = bull_premium + bear_premium
    if total == 0:
        return 0.0
    
    return round((bull_premium - bear_premium) / total, 3)
```

### Interpretation

| Score | Meaning |
|-------|---------|
| > 0.5 | Strong bullish flow |
| 0.2 to 0.5 | Moderate bullish |
| -0.2 to 0.2 | Neutral / mixed |
| -0.5 to -0.2 | Moderate bearish |
| < -0.5 | Strong bearish flow |

### Integration

Add to signal engine as confirmation factor:
- BUY signal + options_pressure > 0.3 → boost confidence +5
- BUY signal + options_pressure < -0.3 → reduce confidence -8

---

## Feature 2: Smart Money Filter

**Priority:** ~~P0~~ → DEFERRED
**Effort:** Low (1-2 hours)
**Blocker:** Parent family (options flow) measured at 39.5% hit rate

### Code Correction

The original `is_smart_money()` won't compile — `FlowAlert` has no `dte` and no `execution_type`. It does have `expiry` (derive DTE) and `has_sweep`.

### Corrected Solution (Buildable, But Deferred)

```python
def is_smart_money(flow: FlowAlert) -> bool:
    """
    Filter for institutional/smart money characteristics.
    
    Criteria:
    - Premium >= $100K (filters retail)
    - DTE >= 30 (not weekly expiry gambling)
    - Sweep execution (urgency, hitting multiple exchanges)
    - OTM preferred (speculative conviction)
    
    NOTE: FlowAlert has `expiry` (not `dte`) and `has_sweep` (not `execution_type`).
    """
    if (flow.premium or 0) < 100_000:
        return False
    # Derive DTE from expiry
    if flow.expiry:
        from datetime import date
        dte = (flow.expiry - date.today()).days
        if dte < 30:
            return False
    # has_sweep indicates urgency
    if flow.has_sweep:
        return True
    # Large block trades also qualify
    if (flow.premium or 0) >= 500_000:
        return True
    return False


def smart_money_flow(symbol: str, hours: int = 4) -> list[FlowAlert]:
    """Return only smart money flow for a symbol."""
    all_flow = get_options_flow(symbol, hours)
    return [f for f in all_flow if is_smart_money(f)]
```

### Filter Criteria Explained

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| Premium | >= $100K | Filters out retail (<$10K typical) |
| DTE | >= 30 days | Excludes weekly lottery tickets |
| Execution | Sweep | Urgency — willing to pay across exchanges |
| Size | >= $500K | Block trade, definitely institutional |

### Enhanced Pressure Score

```python
def smart_money_pressure(symbol: str, hours: int = 4) -> float:
    """Options pressure using only smart money flow."""
    flow = smart_money_flow(symbol, hours)
    # Same aggregation logic as options_pressure()
    ...
```

---

## Feature 3: Dark Pool Pressure Score

**Priority:** ~~P1~~ → DEFERRED
**Effort:** Low (2-3 hours)
**Blocker:** Dark pool measured at 44.9% hit rate; only 64 directional alerts, 1 resolved

### Problem

Dark pool direction is classified per-print but not aggregated into a usable signal.

### Solution

```python
def dark_pool_pressure(symbol: str, hours: int = 4) -> float:
    """
    Aggregate dark pool prints into directional pressure.
    
    Returns:
        -1.0 (heavy selling) to +1.0 (heavy buying)
    """
    prints = get_dark_pool_prints(symbol, hours)
    if not prints:
        return 0.0
    
    buy_volume = 0
    sell_volume = 0
    
    for p in prints:
        side = classify_dark_pool_side(p.price, p.nbbo_bid, p.nbbo_ask)
        if side == "buy":
            buy_volume += p.size
        elif side == "sell":
            sell_volume += p.size
        # None (mid-spread) excluded — honest uncertainty
    
    total = buy_volume + sell_volume
    if total == 0:
        return 0.0
    
    return round((buy_volume - sell_volume) / total, 3)
```

### Size-Weighted Variant

```python
def dark_pool_pressure_weighted(symbol: str, hours: int = 4) -> float:
    """
    Dollar-weighted dark pool pressure.
    Large blocks count more than small prints.
    """
    prints = get_dark_pool_prints(symbol, hours)
    if not prints:
        return 0.0
    
    buy_value = 0.0
    sell_value = 0.0
    
    for p in prints:
        dollar_value = p.size * p.price
        side = classify_dark_pool_side(p.price, p.nbbo_bid, p.nbbo_ask)
        if side == "buy":
            buy_value += dollar_value
        elif side == "sell":
            sell_value += dollar_value
    
    total = buy_value + sell_value
    if total == 0:
        return 0.0
    
    return round((buy_value - sell_value) / total, 3)
```

### Integration

- BUY signal + dark_pool_pressure > 0.3 → flow confirms, boost confidence
- BUY signal + dark_pool_pressure < -0.3 → divergence, reduce confidence or wait

---

## Feature 4: GEX Regime Detection

**Priority:** P2 → **CONSIDER BUILDING** (only feature not built on sub-coin-flip hit rate)
**Effort:** Low (2 hours)

### Why This One Is Different

- GEX gates nothing today (confirmed)
- You're already paying for the data and fetching it 3× while it gates nothing
- It's the only feature not built on a sub-coin-flip hit rate
- **Caveat:** Should be measured against outcomes before it sizes any position

### Background

| Regime | Dealer Behavior | Market Effect |
|--------|-----------------|---------------|
| **Positive Gamma** | Buy dips, sell rips | Dampens moves, mean-reverting |
| **Negative Gamma** | Chase momentum | Amplifies moves, trending |

### Solution

```python
def gex_regime(symbol: str, current_price: float) -> dict:
    """
    Determine gamma regime and key levels.
    
    Returns:
        {
            "regime": "positive_gamma" | "negative_gamma" | "unknown",
            "flip_level": float | None,
            "distance_to_flip_pct": float | None,
            "recommendation": str
        }
    """
    gex = get_gex_levels(symbol)
    if not gex or not gex.neg_gex_flip:
        return {
            "regime": "unknown",
            "flip_level": None,
            "distance_to_flip_pct": None,
            "recommendation": "No GEX data available"
        }
    
    flip = gex.neg_gex_flip
    distance_pct = ((current_price - flip) / flip) * 100
    
    if current_price > flip:
        return {
            "regime": "positive_gamma",
            "flip_level": flip,
            "distance_to_flip_pct": round(distance_pct, 2),
            "recommendation": "Expect mean reversion, wider stops OK"
        }
    else:
        return {
            "regime": "negative_gamma",
            "flip_level": flip,
            "distance_to_flip_pct": round(distance_pct, 2),
            "recommendation": "Expect amplified moves, tighter stops, reduce size"
        }
```

### Position Sizing Adjustment

```python
def adjust_for_gex(base_size: float, symbol: str, price: float) -> float:
    """Reduce position size in negative gamma regime."""
    regime = gex_regime(symbol, price)
    if regime["regime"] == "negative_gamma":
        return base_size * 0.7  # 30% reduction
    return base_size
```

---

## Feature 5: Multi-Day Accumulation/Distribution Tracker

**Priority:** ~~P2~~ → BLOCKED
**Effort:** Medium (4-6 hours)
**Blocker:** Wants 5-day patterns; with 11 days of data, you'd have ~2 non-overlapping windows — not enough to validate anything

### Problem

Single-day flow can be noise. Multi-day patterns reveal institutional positioning.

### Solution

```python
def accumulation_distribution_pattern(
    symbol: str, 
    days: int = 5
) -> dict:
    """
    Detect accumulation or distribution over multiple days.
    
    Returns:
        {
            "pattern": "accumulation" | "distribution" | "neutral" | "unknown",
            "days_analyzed": int,
            "net_dark_pool_pressure": float,
            "price_change_pct": float,
            "divergence": bool,
            "interpretation": str
        }
    """
    daily_pressure = []
    for d in range(days):
        date = (datetime.now() - timedelta(days=d)).date()
        pressure = get_daily_dark_pool_pressure(symbol, date)
        if pressure is not None:
            daily_pressure.append(pressure)
    
    if len(daily_pressure) < 3:
        return {"pattern": "unknown", "interpretation": "Insufficient data"}
    
    avg_pressure = sum(daily_pressure) / len(daily_pressure)
    price_change = get_price_change_pct(symbol, days)
    
    # Detect patterns
    if avg_pressure > 0.2 and price_change < 2:
        return {
            "pattern": "accumulation",
            "net_dark_pool_pressure": avg_pressure,
            "price_change_pct": price_change,
            "divergence": True,
            "interpretation": "Dark pool buying without price rise — accumulation phase, bullish"
        }
    elif avg_pressure < -0.2 and price_change > -2:
        return {
            "pattern": "distribution",
            "net_dark_pool_pressure": avg_pressure,
            "price_change_pct": price_change,
            "divergence": True,
            "interpretation": "Dark pool selling without price drop — distribution phase, bearish"
        }
    elif avg_pressure > 0.2 and price_change > 2:
        return {
            "pattern": "confirmed_uptrend",
            "net_dark_pool_pressure": avg_pressure,
            "price_change_pct": price_change,
            "divergence": False,
            "interpretation": "Dark pool buying + price rising — trend confirmation"
        }
    elif avg_pressure < -0.2 and price_change < -2:
        return {
            "pattern": "confirmed_downtrend",
            "net_dark_pool_pressure": avg_pressure,
            "price_change_pct": price_change,
            "divergence": False,
            "interpretation": "Dark pool selling + price falling — trend confirmation"
        }
    
    return {
        "pattern": "neutral",
        "net_dark_pool_pressure": avg_pressure,
        "price_change_pct": price_change,
        "divergence": False,
        "interpretation": "No clear institutional pattern"
    }
```

### Pattern Interpretation

| Dark Pool | Price | Pattern | Signal |
|-----------|-------|---------|--------|
| Buying | Flat | Accumulation | Bullish (early) |
| Selling | Flat | Distribution | Bearish (early) |
| Buying | Rising | Confirmation | Bullish (trend) |
| Selling | Falling | Confirmation | Bearish (trend) |
| Buying | Falling | Divergence | Caution — could be catching knife |
| Selling | Rising | Divergence | Caution — could be top forming |

---

## Feature 6: Unusual Activity Detection

**Priority:** ~~P2~~ → BLOCKED
**Effort:** Medium (3-4 hours)
**Blocker:** Needs a 30-day baseline; you have 11 days. Cannot be built correctly today.

### Problem

Abnormal volume/premium spikes often precede moves but aren't surfaced as alerts.

### Solution

```python
def detect_unusual_activity(symbol: str) -> dict | None:
    """
    Detect unusual options activity vs historical baseline.
    
    Returns alert dict if unusual, None otherwise.
    """
    today = get_today_options_stats(symbol)
    baseline = get_30day_avg_options_stats(symbol)
    
    if not today or not baseline:
        return None
    
    volume_ratio = today.total_volume / baseline.avg_volume if baseline.avg_volume else 0
    premium_ratio = today.total_premium / baseline.avg_premium if baseline.avg_premium else 0
    
    alerts = []
    
    if volume_ratio >= 2.5:
        alerts.append(f"Volume {volume_ratio:.1f}x normal")
    
    if premium_ratio >= 3.0:
        alerts.append(f"Premium {premium_ratio:.1f}x normal")
    
    # Check for unusual C/P skew
    if today.call_ratio > 0.75 and baseline.avg_call_ratio < 0.55:
        alerts.append(f"Unusual call skew ({today.call_ratio:.0%} vs {baseline.avg_call_ratio:.0%} avg)")
    elif today.call_ratio < 0.25 and baseline.avg_call_ratio > 0.45:
        alerts.append(f"Unusual put skew ({1-today.call_ratio:.0%} puts vs {1-baseline.avg_call_ratio:.0%} avg)")
    
    if not alerts:
        return None
    
    return {
        "symbol": symbol,
        "alerts": alerts,
        "volume_ratio": volume_ratio,
        "premium_ratio": premium_ratio,
        "call_ratio": today.call_ratio,
        "timestamp": datetime.now().isoformat()
    }
```

### Alert Thresholds

| Metric | Threshold | Significance |
|--------|-----------|--------------|
| Volume | >= 2.5x avg | High activity |
| Premium | >= 3.0x avg | Big money moving |
| C/P skew shift | > 20% from avg | Sentiment shift |

---

## Integration: Composite Flow Score

Combine all signals into a single confirmation factor:

```python
def composite_flow_score(symbol: str, price: float) -> dict:
    """
    Unified flow intelligence score for signal confirmation.
    
    Returns:
        {
            "score": float (-100 to +100),
            "components": {
                "options_pressure": float,
                "smart_money_pressure": float,
                "dark_pool_pressure": float,
                "gex_regime": str,
                "accumulation_pattern": str
            },
            "confidence_adjustment": int,
            "size_adjustment": float,
            "interpretation": str
        }
    """
    opt = options_pressure(symbol)
    smart = smart_money_pressure(symbol)
    dp = dark_pool_pressure(symbol)
    gex = gex_regime(symbol, price)
    accum = accumulation_distribution_pattern(symbol)
    
    # Weighted composite (-1 to +1)
    composite = (
        smart * 0.35 +      # Smart money most important
        dp * 0.30 +         # Dark pool second
        opt * 0.20 +        # All options flow
        (0.15 if accum["pattern"] == "accumulation" else
         -0.15 if accum["pattern"] == "distribution" else 0)
    )
    
    # Scale to -100 to +100
    score = round(composite * 100, 1)
    
    # Determine adjustments
    conf_adj = int(score / 10)  # -10 to +10 confidence adjustment
    size_adj = 0.7 if gex["regime"] == "negative_gamma" else 1.0
    
    # Interpretation
    if score > 30:
        interp = "Strong bullish flow confirmation"
    elif score > 10:
        interp = "Moderate bullish flow"
    elif score < -30:
        interp = "Strong bearish flow — caution on longs"
    elif score < -10:
        interp = "Moderate bearish flow"
    else:
        interp = "Neutral/mixed flow"
    
    return {
        "score": score,
        "components": {
            "options_pressure": opt,
            "smart_money_pressure": smart,
            "dark_pool_pressure": dp,
            "gex_regime": gex["regime"],
            "accumulation_pattern": accum["pattern"]
        },
        "confidence_adjustment": conf_adj,
        "size_adjustment": size_adj,
        "interpretation": interp
    }
```

---

## Signal Engine Integration

```python
# In signal generation (signal-engine)
def generate_signal(symbol: str, price: float, ...) -> Signal:
    # Existing signal logic
    base_signal = compute_base_signal(...)  # BUY/HOLD/WAIT/SELL
    base_confidence = compute_confidence(...)
    
    # Flow intelligence confirmation
    flow = composite_flow_score(symbol, price)
    
    # Adjust confidence based on flow alignment
    if base_signal == "BUY" and flow["score"] > 0:
        adjusted_confidence = base_confidence + flow["confidence_adjustment"]
    elif base_signal == "BUY" and flow["score"] < -20:
        adjusted_confidence = base_confidence + flow["confidence_adjustment"]  # negative
    else:
        adjusted_confidence = base_confidence
    
    # Clamp to valid range
    adjusted_confidence = max(0, min(100, adjusted_confidence))
    
    return Signal(
        direction=base_signal,
        confidence=adjusted_confidence,
        flow_score=flow["score"],
        flow_interpretation=flow["interpretation"],
        suggested_size_multiplier=flow["size_adjustment"]
    )
```

---

## API Endpoints

### New Endpoints for Flow Intelligence

```
GET /flow/{symbol}/pressure
    → { options: float, dark_pool: float, smart_money: float }

GET /flow/{symbol}/composite
    → Full composite_flow_score() response

GET /flow/{symbol}/gex
    → GEX regime and levels

GET /flow/{symbol}/accumulation
    → Multi-day accumulation/distribution pattern

GET /flow/unusual
    → List of symbols with unusual activity today
```

---

## UI Integration

### Stock Detail Page

Add "Flow Intelligence" card showing:
- Composite score gauge (-100 to +100)
- Individual component bars (options, dark pool, smart money)
- GEX regime badge (Positive/Negative Gamma)
- Accumulation/Distribution pattern indicator

### Dashboard

- Add flow score column to stock grid (optional toggle)
- Unusual activity alerts in notification bell

### Opportunities Page

- Filter by flow confirmation (show only BUY signals with positive flow)

---

## Data Requirements

| Feature | Data Source | Cost |
|---------|-------------|------|
| Options Flow | Unusual Whales | $57/mo |
| Dark Pool | Unusual Whales | Included |
| GEX Levels | Unusual Whales | Included |
| Historical baseline | PostgreSQL | Free (store daily aggregates) |

**Note:** All features require Unusual Whales subscription. Without UW, only free yfinance C/P ratio available (low accuracy).

---

## Implementation Priority (Revised)

| # | Feature | Original | Revised | Reason |
|---|---------|----------|---------|--------|
| 1 | Net Premium Flow Score | P0 | DEFER | 39.5% hit rate |
| 2 | Smart Money Filter | P0 | DEFER | Parent family below chance |
| 3 | Dark Pool Pressure Score | P1 | DEFER | 44.9% hit rate, 1 resolved alert |
| 4 | GEX Regime Detection | P1 | **P2 — CONSIDER** | Only feature not on sub-coin-flip data |
| 5 | Multi-Day Accumulation | P2 | BLOCKED | Only ~2 windows with 11 days |
| 6 | Unusual Activity | P2 | BLOCKED | Needs 30-day baseline, have 11 |
| 7 | Composite Flow Score | P1 | DEFER | Would weight 85% on 39-45% families |
| 8 | Signal Engine Integration | P1 | DEFER | Risks making signals worse |
| 9 | API Endpoints | P1 | DEFER | No point without validated features |
| 10 | UI Integration | P2 | DEFER | No point without validated features |

**Recommended path:** Wait ~2-4 weeks for T377 scoreboard to fill, then build only what the data justifies.

---

## Success Metrics (Original — Now Questionable)

| Metric | Original Target | Reality Check |
|--------|-----------------|---------------|
| Signal accuracy improvement | +5-10% | Unlikely — weighting 85% on 39-45% families |
| False signal reduction | -15% | Unmeasured — need more resolved outcomes |
| Position sizing effectiveness | +10% Sharpe | GEX is the only candidate; needs validation |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| UW API rate limits | Cache aggressively, batch requests |
| Stale data | TTL-based cache invalidation, show data age in UI |
| Over-reliance on flow | Use as confirmation only, not primary signal |
| Cost ($57/mo) | Start with paper trading validation before live |

---

## Conclusion

This document proposed a 25-35 hour composite scoring system. Production measurement shows:

- Dark pool: 44.9% hit rate (below chance)
- Options flow: 39.5% hit rate (below chance)
- Data depth: 11-14 days (insufficient for multi-day patterns or baselines)

**The cheaper path:** Wait ~2-4 weeks for the T377 scoreboard to fill, then build only what the data justifies.

**One feature to consider now:** GEX Regime Detection (Feature 4) — it's the only one not built on a sub-coin-flip hit rate, and you're already paying for and fetching the data while it gates nothing. But per standing discipline, measure it against outcomes before it sizes any position.

---

## References

- [Unusual Whales API Client](/services/market-data/src/services/unusual_whales.py)
- [Dark Pool Direction Classification](/services/market-data/src/services/unusual_whales.py#L280)
- [Free Options Flow Snapshot](/services/market-data/src/services/options_flow_snapshot.py)
- [Signal Engine](/services/signal-engine/)
