# Paper Trading Engine — Reference Guide

**File:** `services/market-data/src/services/paper_trading_engine.py`  
**Last updated:** 2026-06-11 (WF-2 Deep Audit + Regime Engine)  
**Status:** Live — runs every 5–10 min during US market hours via `paper_trading_step()`

---

## Overview

The paper trading engine simulates a disciplined discretionary trader. It is **not** a batch backtest — it runs in real time on live prices, using the same yfinance feed as the price alert system.

Each cycle (`paper_trading_step()`):
1. **Fetch regime** — classify current market state (SPY/QQQ/VIX)
2. **Monitor open positions** — check stops, targets, signal decay, trailing stops
3. **Scan for entries** — evaluate fresh BUY signals against entry quality gate
4. **Snapshot equity** (post-close, scheduled separately)

---

## Architecture

```
scheduler._refresh_market()   ← runs every 5-10 min during hours
    └── paper_trading_step()
            ├── _fetch_market_regime()        fetch SPY/QQQ/VIX → 5-state classifier
            ├── _fetch_live_prices()          batch yfinance fast_info for all symbols
            ├── _monitor_positions()          check stops/targets/trails per open trade
            │       ├── batch signal query    latest signal per symbol in ONE DB query
            │       ├── monitor_atr_cache     one yfinance call per armed symbol (pre-built)
            │       └── regime_trail_adj      tighten trail mult in risk_off / bear
            └── _scan_for_entries()           evaluate BUY signals for new entries
                    ├── circuit breakers      drawdown / daily loss / max entries / regime
                    ├── atr_cache             one yfinance call per candidate (pre-built)
                    ├── _build_game_plan()    derive entry/stop/target from signal + ATR
                    └── _should_enter()       multi-factor scoring gate (score >= min_score)

snapshot_equity_curve()       ← runs post-close (separate scheduler job)
    └── records equity + SPY/QQQ/HSI benchmark closes
```

---

## Regime Engine

### How It Works

Every cycle, `_fetch_market_regime()` downloads **300 days** of SPY, QQQ, and ^VIX via yfinance, then classifies the current market into one of five states:

| State | Conditions | New Entry Sizing | Min Score | Trail Mult |
|-------|-----------|-----------------|-----------|------------|
| `bull` | SPY > EMA-20 AND SPY > EMA-50 AND VIX < 18 | 100% | base (3) | 1.0× |
| `neutral` | Default (all other cases) | 100% | base (3) | 1.0× |
| `choppy` | SPY < EMA-20 OR VIX > 20 | 75% | 4 | 1.0× |
| `risk_off` | SPY < EMA-50 OR VIX > 25 | 50% | 5 | 0.85× |
| `bear` | SPY < EMA-50 AND VIX > 30 | **BLOCKED** | — | 0.70× |

A secondary bear condition also triggers when SPY < EMA-200 AND 20-day return < –8%.

### Classification Logic (Decision Tree)

```
BEAR?   SPY < 50EMA AND VIX > regime_vix_fear (30)
        → Return early from entry scan; tighten trail to 0.70×

RISK_OFF?  SPY < 50EMA  OR  VIX > regime_vix_high (25)
        → Size = 50%; min_entry_score = max(base, 5); trail = 0.85×

CHOPPY?   SPY < 20EMA  OR  VIX > 20
        → Size = 75%; min_entry_score = max(base, 4)

BULL?    SPY > 20EMA AND SPY > 50EMA AND VIX < 18
        → Full size; score +1 bonus in _should_enter()

NEUTRAL   (everything else)
        → Full size; no score adjustment
```

### Where It Flows

```python
# Fetched ONCE per paper_trading_step() cycle
live_regime = _fetch_market_regime(_base_cfg)

# Stored in portfolio.config for UI display
portfolio.config = {**portfolio.config,
    "regime_state": "choppy",   # current state
    "regime_vix": 19.87,
    "regime_spy": 737.05,
    "regime_notes": ["SPY $737 below 20EMA $743"]
}

# Passed to both monitor and scan
_monitor_positions(session, portfolio, live_prices, live_regime)
_scan_for_entries(session, portfolio, live_prices, live_regime)

# Used in _should_enter() scoring
live_regime_state = live_regime.get("state")   # overrides stale signal-stored value
```

### Tuning the Regime

All thresholds are configurable via the portfolio config JSON. Defaults:

```python
"enable_regime_filter":      True,    # master on/off switch
"regime_vix_high":           25.0,    # VIX above this → risk_off
"regime_vix_fear":           30.0,    # VIX above this (+ SPY < 50EMA) → bear
"regime_bear_size_mult":     0.0,     # effectively blocks entries
"regime_risk_off_size_mult": 0.50,
"regime_choppy_size_mult":   0.75,
"regime_bull_size_mult":     1.0,
"regime_risk_off_min_score": 5,
"regime_choppy_min_score":   4,
```

To adjust via API (admin only):
```
PATCH /paper-portfolio/config
Body: {"regime_vix_high": 22.0, "regime_choppy_size_mult": 0.60}
```

To disable the regime filter entirely:
```
PATCH /paper-portfolio/config
Body: {"enable_regime_filter": false}
```

### Key Log Events

| Event | When | Key Fields |
|-------|------|-----------|
| `paper.regime_classified` | Every cycle | `state`, `spy`, `ema20`, `ema50`, `vix`, `spy_20d_ret` |
| `paper.regime_gate_bear` | Bear → block entries | `vix`, `spy`, `notes` |
| `paper.regime_applied` | Non-bull/neutral regime | `state`, `size_mult`, `min_score`, `vix` |

---

## Entry Scoring (`_should_enter()`)

Returns `(should_enter: bool, score: int, notes: list[str])`.

Entry is taken when `score >= min_entry_score` (default 3, raised by regime).

| Factor | Max Score | Condition |
|--------|-----------|-----------|
| Price zone | +4 / –3 | Deep pullback / in zone / extended |
| R:R quality | +2 | R:R ≥ 3.5 (+2), ≥ 2.5 (+1) |
| RSI (GROWTH) | +2 / –2 | 72–85 hot momentum / > 88 exhaustion |
| RSI (SWING/LONG) | +1 / –2 | 40–65 healthy / > 72 overbought |
| MACD | +2 | Rising + zero-cross |
| OBV | +1 | Bullish |
| Volume z-score | –1 | z < –0.5 (below-average volume) |
| Trend / SMA | +2 | SMA50>SMA200 + price above SMA50 |
| Regime (live) | +1 / –2 | Bull / Bear |
| Breadth | +1 / –1 | > 55% / < 40% |
| Conviction | +2 | Bull probability ≥ 72% |
| Confidence | +1 | Signal confidence ≥ 75% |

**Hard rejects** (override any score):
- Confidence < 90% of `min_confidence`
- Stop-to-price distance < 0.5%
- R:R < `min_rr_ratio` (default 2.0)
- Earnings ≤ 5 days away

---

## Circuit Breakers (Entry Scan)

Checked in order before any entry:

1. **Max positions** — `open_count >= max_positions` (default 10)
2. **Live price health** — if `len(live_prices) < len(open_symbols) × 0.5` → skip (yfinance outage guard)
3. **Portfolio drawdown** — `(peak_equity - current_equity) / peak_equity > 20%`
4. **Daily realized loss** — `sum(losing trades today) / equity > 4%`
5. **Daily entries cap** — `entries_today >= 5`
6. **Regime gate** — `regime_state == "bear"` → blocked entirely

---

## Position Sizing

```
shares = (equity × risk_per_trade_pct × earnings_mult × regime_size_mult) / stop_distance

where:
  risk_per_trade_pct = 1% (configurable)
  earnings_mult      = 0.50 if DTE ≤ 10 / 0.75 if DTE 11–20 / 1.0 otherwise
  regime_size_mult   = 0.50 (risk_off) / 0.75 (choppy) / 1.0 (neutral/bull)
  stop_distance      = live_price - game_plan["stop"]
```

Position is capped at `max_position_pct` (default 10%) of equity after sizing.

---

## Exit Conditions

Checked per trade in order:

1. **Stop hit** — `live_price <= current_stop`
2. **Target reached** — `live_price >= take_profit`
3. **SELL signal** — latest signal downgraded to SELL
4. **Time stop** — `hold_days >= max_hold_days` (60 for GROWTH)
5. **WAIT decay** — signal stuck on WAIT for `> wait_exit_days` (5 for GROWTH)

All exits apply 10 bps slippage on the exit price.

---

## Trailing Stop

```
Armed when:  highest_price >= entry × (1 + trail_trigger_pct)   [default: +5%]
Trail level: highest_price - ATR × trail_atr_mult × regime_trail_adj
             floored at: max(atr_trail, initial_stop_loss, entry)   ← breakeven floor

regime_trail_adj:
  bear     → 0.70  (30% tighter — protects through regime-driven selloffs)
  risk_off → 0.85  (15% tighter)
  others   → 1.0
```

---

## Slippage & Commission Model

| Item | Default | Config Key |
|------|---------|-----------|
| Entry slippage | +10 bps | `entry_slippage_pct: 0.001` |
| Exit slippage | −10 bps | same key, applied negatively |
| Commission | $0/share | `commission_per_share: 0.0` |

---

## Key Config Reference

```python
{
    "trading_style":            "GROWTH",  # GROWTH / SWING / LONG
    "max_positions":            10,
    "max_sector_pct":           0.30,
    "risk_per_trade_pct":       0.01,
    "max_position_pct":         0.10,
    "min_confidence":           62.0,
    "min_kscore":               48.0,
    "min_rr_ratio":             2.0,
    "min_entry_score":          3,
    "max_hold_days":            60,
    "trail_atr_mult":           2.0,
    "trail_trigger_pct":        0.05,
    "breakeven_trigger_pct":    0.03,
    "wait_exit_days":           5,
    "max_portfolio_drawdown_pct": 0.20,
    "max_daily_loss_pct":       0.04,
    "max_entries_per_day":      5,
    "require_kscore":           True,
    "max_open_risk_pct":        0.12,
    "entry_slippage_pct":       0.001,
    "enforce_market_hours":     True,
    # Regime engine
    "enable_regime_filter":     True,
    "regime_vix_high":          25.0,
    "regime_vix_fear":          30.0,
    "regime_risk_off_size_mult": 0.50,
    "regime_choppy_size_mult":  0.75,
    "regime_risk_off_min_score": 5,
    "regime_choppy_min_score":  4,
}
```

---

## Useful Log Queries

```bash
# View all regime classifications
docker logs stockai-market-data-1 2>&1 | grep "regime_classified"

# View all entries (what was entered and why)
docker logs stockai-market-data-1 2>&1 | grep "paper.entry\b"

# View all skipped entries with reasons
docker logs stockai-market-data-1 2>&1 | grep "paper.entry_skipped"

# View all exits
docker logs stockai-market-data-1 2>&1 | grep "paper.exit"

# Check circuit breaker activity
docker logs stockai-market-data-1 2>&1 | grep "circuit_breaker\|regime_gate\|daily_loss\|drawdown"

# View trailing stop updates
docker logs stockai-market-data-1 2>&1 | grep "trail_stop_raised\|stop_to_breakeven"
```

---

## UI

The paper portfolio page (`/paper-portfolio`) shows:
- **Regime badge** — color-coded stat card: green (bull) → amber (choppy) → orange (risk_off) → red (bear)
- **VIX level** — shown as sub-text of the regime card
- **vs SPY / vs QQQ** — portfolio excess return vs benchmark since day 1
- **Sharpe** — annualised (shown as `< 20 days data` until sufficient history)
- **Equity chart** — portfolio vs SPY/QQQ/HSI rebased to same starting capital

---

## Pending Improvements (Audit Checklist)

See `/improvements` page → Tier 8 for full list. High-priority pending items:

| ID | Issue |
|----|-------|
| PA-C1 | No max loss per trade (wide stops can exceed intended % of equity) |
| PA-D1 | Sector cap only checked at entry, not on existing open positions |
| PA-D2 | Drawdown circuit breaker uses stale EOD peak, not intraday high-water |
| PA-E3 | ATR uses EWM not SMA — undocumented difference from standard ATR |
| PA-F1 | ATR pre-fetch makes N individual yfinance calls — batch would be 1 call |
| PA-G1 | exit_reasons schema inconsistent across exit types |
| PA-G3 | No signal-to-trade lifecycle tracking for walkforward attribution |
