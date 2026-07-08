# WF-2: Autonomous Paper Trading Engine — Implementation Plan

**Reference:** `additional_features.txt`  
**Updated:** 2026-06-08  
**Status:** Planning — approved, ready to implement

---

## How It Behaves (The Goal)

The system acts like a disciplined human trader sitting at a screen all day:

1. **Signal arrives** → System asks: *"Is now actually a good time to buy?"* (not just blindly executing every BUY signal)
2. **Conditions are right** → System simulates buying at the current live price
3. **Every 5-10 minutes during hours** → System checks all open positions: *"Should I hold, raise my stop, or get out?"*
4. **Exit decision** → Based on live price vs stop/target, signal degradation, trailing stop, time

This is **not** a batch backtest. It uses **live prices during market hours** and makes decisions in real time, the same way the existing price alert system does.

---

## Critical Architecture Insight

The scheduler already runs `_refresh_market()` **every 5-10 minutes during trading hours**. That function already:
1. Ingests latest bars
2. Refreshes rankings (K-Scores)
3. Refreshes signals (BUY/SELL/HOLD/WAIT) for all stocks
4. Calls `check_signal_alerts()` which fetches live prices via `yfinance.fast_info`

All we add is one more function call at the end: `paper_trading_step()`. It runs on the same cadence — **every 5-10 minutes during market hours** — and uses the same live price infrastructure that already exists.

**No new service. No new scheduler. No new data feed.** Just a new module inside `market-data`.

---

## What Already Exists (Do NOT rebuild)

### Live Price Feed
| Component | Already Built | Used By |
|---|---|---|
| `_fetch_live_one(symbol)` — yfinance fast_info per symbol | ✅ | Price alert check |
| `_fetch_live_bulk(stocks)` — batch live quotes | ✅ | `/latest_prices` endpoint |
| 5-min intraday bar ingest | ✅ | Intraday charts |
| Redis-cached latest prices (60s TTL) | ✅ | Frontend live price display |

### Signal Infrastructure (already intraday)
| Component | Already Built | Notes |
|---|---|---|
| `_refresh_market()` — runs every 5-10 min | ✅ | Scheduler hook — just append our call here |
| `Signal` table — all BUY/SELL/HOLD/WAIT signals stored | ✅ | Query latest per symbol |
| `Signal.reasons` JSON — full TA snapshot | ✅ | RSI, MACD, ADX, sector, regime, ATR, etc. |
| `Signal.confidence`, `bullish_probability` | ✅ | Entry filter inputs |
| `_BULLISH_TRANSITIONS` / `_BEARISH_TRANSITIONS` | ✅ | Signal change tracking already done |
| `_is_conviction_buy()` — 5-layer conviction gate | ✅ | Reuse directly for entry qualifier |

### Entry/Exit Calculations
| Component | Already Built | Where |
|---|---|---|
| `_build_game_plan()` — entry1, entry2, stop, take_profit | ✅ | `scheduler.py` |
| ATR computation (for trailing stop) | ✅ | `routes.py` `GET /stocks/{symbol}/atr` + signals.py |
| Market regime | ✅ | `reasons['market_regime']` |
| Sector ETF trend | ✅ | `reasons['sector_etf_above_sma50']`, `reasons['sector_headwind']` |
| Earnings risk | ✅ | `reasons['days_to_earnings']` |
| RSI, MACD, OBV, ADX | ✅ | `reasons['rsi']`, `reasons['macd_rising']`, etc. |

### Risk & Position Sizing
| Component | Already Built | Where |
|---|---|---|
| ATR-based stop distance | ✅ | `_build_game_plan()` → `stop` field |
| Risk $ per trade (1% of capital) | ✅ | Position sizing logic |
| Sector from DB | ✅ | `Stock.sector` |
| R:R check | computable | `(take_profit - entry) / (entry - stop)` |

### Outcome Tracking
| Component | Already Built | Where |
|---|---|---|
| `signal_outcomes` table | ✅ | ML feedback loop — paper trade exits write here |
| Win rate by confidence band | ✅ | `/signals/outcomes/summary` |

---

## What's New (What We Build)

### 1. Three New DB Tables

```sql
-- Portfolio configuration and running cash balance
CREATE TABLE paper_portfolios (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(128) DEFAULT 'Paper Portfolio 1',
    initial_capital FLOAT NOT NULL,        -- e.g. 50000
    current_cash    FLOAT NOT NULL,        -- decreases on entry, increases on exit
    config          JSONB NOT NULL,        -- rules (see below)
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);
-- config shape:
-- {
--   "max_positions": 10,          -- max concurrent open trades
--   "max_sector_pct": 0.25,       -- max 25% in one sector
--   "risk_per_trade_pct": 0.01,   -- risk 1% of capital per trade
--   "min_confidence": 65.0,       -- min Signal.confidence to consider entry
--   "min_kscore": 52.0,           -- min K-Score ranking threshold
--   "min_rr_ratio": 2.0,          -- minimum risk/reward at entry
--   "min_entry_score": 3,         -- min score from _should_enter() to proceed
--   "max_hold_days_swing": 20,    -- time-stop for SWING
--   "max_hold_days_long": 60,     -- time-stop for LONG
--   "styles": ["SWING", "LONG"],  -- which horizons to trade
--   "enabled": true               -- master on/off switch
-- }

-- One row per paper trade (open or closed)
CREATE TABLE paper_trades (
    id                      SERIAL PRIMARY KEY,
    portfolio_id            INT REFERENCES paper_portfolios(id),
    symbol                  VARCHAR(32) NOT NULL,
    signal_id               BIGINT REFERENCES signals(id),   -- triggering signal
    -- Entry
    entry_date              DATE NOT NULL,
    entry_time              TIMESTAMP NOT NULL,              -- actual entry timestamp (intraday)
    entry_price             FLOAT NOT NULL,                  -- live price at entry (not close)
    shares                  FLOAT NOT NULL,
    stop_loss               FLOAT NOT NULL,                  -- initial stop from game plan
    take_profit             FLOAT,
    horizon                 VARCHAR(16),                     -- SHORT / SWING / LONG / GROWTH
    -- Decision quality at entry
    entry_score             INT,                             -- score from _should_enter()
    entry_decision_notes    JSONB,                          -- list of reasoning strings
    confidence_at_entry     FLOAT,
    kscore_at_entry         FLOAT,
    rr_ratio_at_entry       FLOAT,
    market_regime_at_entry  VARCHAR(16),
    entry_reasons           JSONB,                          -- Signal.reasons snapshot
    -- Live tracking
    current_stop            FLOAT,                          -- trails up as price rises
    current_price           FLOAT,                          -- updated each monitor loop
    highest_price           FLOAT,                          -- peak since entry (for trailing)
    stage                   VARCHAR(20) DEFAULT 'open',     -- open / closed
    hold_days               INT DEFAULT 0,
    -- Exit (null until closed)
    exit_time               TIMESTAMP,
    exit_price              FLOAT,
    exit_reason             VARCHAR(64),
    -- stop_hit / target_reached / signal_exit / momentum_exit / time_stop / regime_exit
    exit_reasons            JSONB,                          -- Signal.reasons at exit
    pnl                     FLOAT,                          -- dollar P&L
    pct_return              FLOAT,                          -- % return
    created_at              TIMESTAMP DEFAULT NOW()
);

-- Daily equity snapshots for the equity curve chart
CREATE TABLE paper_equity_curve (
    id                   SERIAL PRIMARY KEY,
    portfolio_id         INT REFERENCES paper_portfolios(id),
    date                 DATE NOT NULL,
    equity               FLOAT NOT NULL,         -- cash + open position market value
    cash                 FLOAT NOT NULL,
    open_positions_value FLOAT DEFAULT 0,
    open_positions_count INT DEFAULT 0,
    spy_close            FLOAT,
    qqq_close            FLOAT,
    hsi_close            FLOAT,
    UNIQUE(portfolio_id, date)
);
```

---

### 2. The "Should I Buy?" Entry Qualifier

This is the human-like decision layer. A BUY signal alone is not enough — conditions must be right at this specific moment.

**Location:** `paper_trading_engine.py` → `_should_enter()`

```
Inputs: symbol, signal_data, live_price, game_plan, config

PRICE ZONE CHECK:
  If live_price in [entry2, breakout]:        score += 3   "in optimal entry zone"
  If live_price in [entry1, entry2):          score += 4   "deep pullback — better R:R"
  If live_price < entry1:                     score -= 1   "extended drop — wait for stabilisation"
  If live_price > breakout × 1.03:            score -= 3   "chasing — already 3%+ above breakout"

RISK/REWARD AT CURRENT PRICE:
  rr = (take_profit - live_price) / (live_price - stop)
  If rr >= 3.0:  score += 2   "excellent R:R"
  If rr >= 2.0:  score += 1   "acceptable R:R"
  If rr < 2.0:   score -= 3   "R:R below minimum — skip"   → hard reject regardless of total score

MOMENTUM:
  If RSI > 75:   score -= 3   "overbought — wait for cooldown"
  If RSI < 70:   score += 1   "momentum not extended"
  If MACD rising AND zero-cross-up: score += 1   "MACD confirming"
  If OBV bullish: score += 1  "volume confirming"

MARKET CONTEXT:
  If regime == 'bull':   score += 1   "broad tailwind"
  If regime == 'bear':   score -= 2   "bear regime — higher false-signal rate"
  If sector_etf_above_sma50:  score += 1   "sector tailwind"
  If sector_headwind:         score -= 1   "sector below SMA50"
  If breadth_pct >= 55:  score += 1   "broad market healthy"
  If breadth_pct < 40:   score -= 1   "market breadth weak"

EARNINGS RISK:
  If days_to_earnings <= 5:   score -= 3   "binary event — skip"   → hard reject
  If days_to_earnings <= 10:  score -= 1   "earnings near — size down"

CONFIDENCE:
  If bullish_probability >= 0.70:  score += 2   "high conviction signal"
  If bullish_probability >= 0.60:  score += 1   "moderate conviction"
  If confidence >= 75:             score += 1   "high confidence"

DECISION:
  score >= config.min_entry_score (default 3) → ENTER at live_price
  score 1–2                                   → WAIT (conditions not ideal yet)
  score <= 0 or any hard reject               → SKIP (don't enter)
```

**Hard rejects always skip** — R:R < 2:1 and earnings ≤ 5 days override any positive score.

---

### 3. The "Should I Exit?" Position Monitor

Runs every 5-10 minutes for all open paper trades.

**Location:** `paper_trading_engine.py` → `_monitor_positions()`

```
For each open paper_trade:

  live_price = _fetch_live_one(symbol)      -- yfinance fast_info (same as price alerts)
  current_signal = get_latest_signal(symbol) -- query Signal table

  UPDATE: paper_trade.current_price = live_price
  UPDATE: paper_trade.highest_price = max(highest_price, live_price)
  UPDATE: paper_trade.hold_days (count trading days since entry)

  --- HARD EXITS (no score, immediate) ---

  1. STOP HIT:
     If live_price <= paper_trade.current_stop:
       EXIT("stop_hit", live_price)
       reason: f"Stop ${current_stop:.2f} breached at ${live_price:.2f}"

  2. TARGET REACHED:
     If live_price >= paper_trade.take_profit:
       EXIT("target_reached", live_price)
       reason: f"Target ${take_profit:.2f} hit at ${live_price:.2f} — {pnl_pct:+.1f}%"

  3. SIGNAL TURNS SELL:
     If current_signal.signal == 'SELL':
       EXIT("signal_exit", live_price)
       reason: f"Signal downgraded to SELL — {Signal.reasons summary}"

  --- STOP MANAGEMENT (trailing) ---

  pnl_pct = (live_price - entry_price) / entry_price

  If pnl_pct >= 0.03 (3% profit):
    → Move stop to breakeven (entry_price)
    → Log: "Stop raised to breakeven after +3% gain"

  If pnl_pct >= 0.05 (5% profit):
    → Trail stop = highest_price - ATR × 1.5
    → Only update if new trail_stop > current_stop
    → Log: f"Trailing stop raised to ${trail_stop:.2f}"

  --- SOFT EXITS (signal degradation) ---

  4. MOMENTUM FADING:
     If current_signal.signal == 'WAIT' AND hold_days >= 3:
       → Move stop to breakeven (protect capital)
       → Log: "Signal in WAIT for 3+ days — stop moved to breakeven"
     If current_signal.signal == 'WAIT' AND hold_days >= 7:
       EXIT("momentum_exit", live_price)
       reason: "Signal stalled in WAIT for 7+ days — redeploying capital"

  5. TIME STOP:
     If horizon == 'SWING' AND hold_days > config.max_hold_days_swing (default 20):
       EXIT("time_stop", live_price)
       reason: f"No resolution in {hold_days} days — time stop triggered"

  6. REGIME SHIFT:
     If market_regime_at_entry != 'bear' AND current_regime == 'bear':
       → Tighten stop to entry_price (if not already above)
       → Log: "Market shifted to bear regime — stop tightened to breakeven"
```

---

### 4. The Entry Engine

Runs alongside position monitoring (same cadence).

**Location:** `paper_trading_engine.py` → `_scan_for_entries()`

```
1. Fetch today's latest signals WHERE signal='BUY'
   AND confidence >= config.min_confidence
   AND ts >= (now - 30 minutes)    ← only fresh/recently confirmed signals
   ORDER BY confidence DESC

2. JOIN rankings WHERE score >= config.min_kscore

3. Apply portfolio-level filters:
   - Skip if already have open paper_trade for this symbol
   - Skip if open_positions >= config.max_positions
   - Skip if sector would exceed config.max_sector_pct

4. For each candidate:
   a. Fetch live_price = _fetch_live_one(symbol)
   b. Build game_plan = _build_game_plan(symbol, signal_data)
   c. Run _should_enter() → get score, notes, should_enter
   d. If NOT should_enter: log decision as WAIT/SKIP with score, continue
   e. Compute shares:
        risk_dollar = portfolio.current_cash × config.risk_per_trade_pct
        shares = risk_dollar / (entry_price - stop_loss)
        position_value = shares × entry_price
        Reject if position_value > portfolio_equity × 0.10
   f. Check R:R: (take_profit - live_price) / (live_price - stop) >= min_rr_ratio
   g. ENTER: deduct position_value from portfolio.current_cash
             INSERT paper_trade with all data + entry_decision_notes
             Log: "ENTER {symbol} at ${live_price:.2f}, score={score}/10, R:R={rr:.1f}"
```

---

### 5. Scheduler Integration

In `scheduler.py`, add one call to `_refresh_market()`:

```python
# After check_technical_alerts() — runs every 5-10 min during hours
from .paper_trading_engine import paper_trading_step
paper_trading_step()     # monitor positions + scan for entries

# Post-close only — after ML retrain
from .paper_trading_engine import snapshot_equity_curve
snapshot_equity_curve()  # record EOD equity vs benchmarks
```

The paper trading engine runs **intraday automatically** because `_refresh_market()` already runs every 5-10 minutes. No new scheduler jobs needed.

---

### 6. API Endpoints (`paper_portfolio.py`)

```
GET  /paper-portfolio/summary
     → total_return_pct, sharpe, sortino, max_drawdown, win_rate,
       profit_factor, avg_winner, avg_loser, vs_spy_pct, days_running,
       open_positions, total_trades

GET  /paper-portfolio/positions
     → open trades with: symbol, entry_price, current_price, pnl_pct,
       pnl_dollar, hold_days, current_stop, take_profit, confidence,
       entry_score, entry_decision_notes (why it entered)

GET  /paper-portfolio/trades?page=1&limit=50
     → closed trades with: symbol, entry, exit, hold_days, pct_return,
       exit_reason badge, pnl, entry_score, entry_decision_notes, exit_reasons

GET  /paper-portfolio/equity-curve
     → [{date, equity, cash, spy_rebased, qqq_rebased, hsi_rebased}]

GET  /paper-portfolio/decisions?limit=100
     → recent WAIT/SKIP decisions with reasoning (why it did NOT enter)
       — crucial for understanding the system's selectivity

POST /paper-portfolio/configure   (admin only)
     → update config JSON (capital, thresholds, enable/disable)

POST /paper-portfolio/reset       (admin only)
     → close all open positions at current price, restore initial_capital
```

---

### 7. Frontend Page (`/paper-portfolio`)

**Section A — Metrics strip** (6 stat cards):
- Total Return % (vs initial capital)
- Sharpe Ratio
- Max Drawdown
- Win Rate (% of closed trades profitable)
- vs SPY (outperformance)
- Days Running

**Section B — Equity curve** (Plotly line chart):
- Portfolio equity (rebased to 100 at start)
- SPY rebased to 100 (same start date)
- QQQ rebased to 100
- HSI rebased to 100 (toggle for HK view)

**Section C — Open Positions** (live, auto-refreshes every 60s):
- Symbol | Entry Date + Time | Entry Price | Live Price | P&L $ | P&L % | Days | Stop | Target | Entry Score | Why entered (expandable notes)

**Section D — Decision Log** (last 50 ENTER/WAIT/SKIP decisions):
- Shows **why the system passed on signals** — just as important as entries
- Colour-coded: green=ENTER, amber=WAIT, grey=SKIP
- Shows entry score breakdown for each decision

**Section E — Closed Trades**:
- Exit reason badges: 🎯 Target | 🛑 Stop | 📉 Signal | ⏱ Time
- P&L coloured green/red
- "Why entered / why exited" expandable for each row

**Section F — Config panel** (admin only, collapsible):
- Initial capital, thresholds, enable/disable toggle

---

## Timeline & Phases

### Phase 1 — Foundation (3–4 days)
- [ ] Add 3 DB tables to `shared/db/models.py` (PaperPortfolio, PaperTrade, PaperEquityCurve)
- [ ] Run DB migration on EC2
- [ ] `paper_trading_engine.py` — `_should_enter()` + `_monitor_positions()` + `_scan_for_entries()` + `paper_trading_step()`
- [ ] Hook into `scheduler.py` (one line)
- [ ] Seed one portfolio via a startup call or admin endpoint

### Phase 2 — API Layer (2 days)
- [ ] `paper_portfolio.py` — all 7 endpoints
- [ ] Wire into `main.py`
- [ ] Sharpe, drawdown, profit factor computation from equity curve

### Phase 3 — Frontend (3–4 days)
- [ ] `paper-portfolio.tsx` — all 6 sections
- [ ] Plotly equity curve with benchmark comparison
- [ ] Add to nav (all users — it's a showcase, not admin-only)
- [ ] Auto-refresh on open positions (60s SWR)

### Phase 4 — ML Feedback (1 day)
- [ ] On paper trade exit → write to `signal_outcomes` (link via `signal_id`)
- [ ] This feeds the existing Optuna tuning loop with real paper trade outcomes

**Total: ~10–11 days**

---

## Risk Rules

| Rule | How Enforced |
|---|---|
| Risk per trade ≤ 1% | `shares = (cash × 0.01) / (entry_price − stop)` |
| Max position ≤ 10% portfolio | `shares × entry_price ≤ equity × 0.10` — hard reject |
| Max sector ≤ 25% | Sum open trades by Stock.sector — hard reject if exceeded |
| R:R ≥ 2:1 at entry | Hard reject in `_should_enter()` |
| Earnings ≤ 5 days | Hard reject in `_should_enter()` |
| Confidence ≥ threshold | Filter in `_scan_for_entries()` |

---

## Exit Conditions

| Trigger | Urgency | Action |
|---|---|---|
| Stop loss breached (live price) | Immediate | EXIT — stop_hit |
| Target hit (live price) | Immediate | EXIT — target_reached |
| Signal = SELL | Immediate | EXIT — signal_exit |
| +3% profit reached | Proactive | Move stop to breakeven |
| +5% profit reached | Proactive | Trail stop at highest − ATR×1.5 |
| Signal = WAIT for 3 days | Defensive | Move stop to breakeven |
| Signal = WAIT for 7 days | Exit | EXIT — momentum_exit |
| Time stop exceeded | Forced | EXIT — time_stop |
| Market shifts to bear | Defensive | Move stop to breakeven |

---

## Performance Thresholds (for Live Trading Gate)

Before the broker integration layer unlocks (future):
- Sharpe ratio > 1.0 (measured over ≥ 90 trading days)
- Win rate > 50% (over ≥ 30 closed trades)
- Max drawdown < 20%
- Return > SPY over same period
- All conditions must hold for ≥ 30 consecutive days

---

## Files Created / Modified

| File | Action |
|---|---|
| `shared/db/models.py` | Add PaperPortfolio, PaperTrade, PaperEquityCurve |
| `services/market-data/src/services/paper_trading_engine.py` | **NEW** — core engine |
| `services/market-data/src/services/scheduler.py` | +2 lines: hook paper_trading_step() + snapshot_equity_curve() |
| `services/market-data/src/api/paper_portfolio.py` | **NEW** — 7 endpoints |
| `services/market-data/src/main.py` | Register paper_portfolio router |
| `frontend/src/lib/api.ts` | Add paper portfolio types + API calls |
| `frontend/src/pages/paper-portfolio.tsx` | **NEW** — full dashboard |
| `frontend/src/pages/_app.tsx` | Add nav link |

---

## The Key Difference From Backtesting

| Backtesting | This System (WF-2) |
|---|---|
| Uses historical data, knows the future | Uses only what's known right now |
| Runs overnight on years of data | Runs every 5-10 minutes during market hours |
| Fixed entry at daily close price | Enters at live price when conditions are right |
| No entry decision logic | Scores current conditions before entering |
| Can't react intraday to stop breach | Detects stop breach in real time (every 5 min) |
| Results look good, often overfit | Results reflect what a real trader would experience |

The system uses its own live signals as the research layer and adds a real-time judgement layer on top — exactly like a human trader who has done their analysis and then waits for the right moment to pull the trigger.
