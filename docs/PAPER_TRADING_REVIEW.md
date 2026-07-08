# Paper Trading System Review — 2026-06-13

Deep review covering: paper trading trustworthiness, AI signal reliability, email alert
independence, regime engine quality, and hold/sell decision logic.

---

## 1. Why Paper Trading Never Bought

### Root cause #1 — Weekend timing (most recent deployment)

Today is Saturday, June 13, 2026. The APScheduler `CronTrigger` jobs are correctly
configured to fire `day_of_week="mon-fri"` only. The test showing next fire time of
`2026-06-15 10:00:00-04:00` is **correct** behavior — the scheduler is working.
Market-refresh jobs (`_refresh_market`, `_refresh_5m`) will fire normally starting
Monday June 16 at 9:30 AM ET.

### Root cause #2 — Old code silently killed `paper_trading_step`

Before the AUD-H5 fix (4-stage isolation in `scheduler.py`), `_refresh_market()` was
a single `try/except` block. Any ingest failure — including routine yfinance errors on
tickers like BRK.A — would raise an exception before reaching Stage 4 (paper trading).
The `paper_trading_step()` call never ran, silently, on every refresh.

Evidence: the portfolio config stored `regime_state: "bull"` — meaning `paper_trading_step()`
DID execute at some point (regime is fetched before entry scanning). But zero trades ever
resulted. This is consistent with the function running but crashing on the entry scan step
when yfinance failed to fetch live prices for the candidate symbols.

### Root cause #3 — Portfolio config scale mismatch in DB

The stored portfolio config contains wrong-scale values entered via the UI:

| Parameter | Stored value | Correct value | Effect |
|---|---|---|---|
| `risk_per_trade_pct` | `1` (100%) | `0.01` (1%) | Position sizing uses 100% of equity as risk base, then cap kicks in — massively over-allocates |
| `max_position_pct` | `5` (500%) | `0.10` (10%) | Same — no practical cap on position size |
| `max_hold_days` | `20` | `60` | GROWTH positions time-stopped 3× too early |

**How it happened**: the UI likely accepted percentage inputs (e.g. "1" meaning 1%) but the
engine expects decimal fractions (0.01). The fields need validation or clear labels.

**Fix** (run on EC2 after confirming values):
```sql
UPDATE paper_portfolios
SET config = config || jsonb_build_object(
    'risk_per_trade_pct', 0.01,
    'max_position_pct',   0.10,
    'max_hold_days',      60,
    'max_loss_per_trade_pct', 0.02,
    'hold_stall_days',    30,
    'hold_stall_max_gain', 0.05
)
WHERE name = 'GROWTH Paper Portfolio';
```

### Root cause #4 — `trade.stock_id` not populated on entry (latent bug)

`_scan_for_entries()` creates `PaperTrade` rows but does not set `stock_id`. The monitor
loop (`_monitor_positions`) queries `trade.stock_id` for double-top detection:
```python
{"sid": trade.stock_id, "h": trade.style ...}
```
This passes `None` as `:sid`, returning no rows (silently). The double-top logic is
effectively always disabled. **Low priority** — it's a missed feature, not a crash.

---

## 2. AI Signal Trustworthiness

### Architecture summary

The AI Signal is a **weighted fused score**, not a vote:

```
fused_score = TA_composite × (1 − ml_weight) + ML_ensemble × ml_weight
ml_weight ramps 0 → 0.45 over 180 training days per style
```

Each of the 4 pillars (Trend, Momentum, Volume, Structure) takes `max()` of its
sub-indicators, preventing double-penalizing a stock for correlated signals.

### Signal → Classification thresholds

| Fused score | Signal | Meaning |
|---|---|---|
| ≥ 60 | BUY | Strong multi-factor alignment |
| 45–59 | WAIT | Positive lean, not enough confirmation |
| 35–44 | HOLD | Neutral |
| 20–34 | CAUTION | Deteriorating conditions |
| < 20 | SELL | Strong negative |

These thresholds are **style-independent at the classification layer** — GROWTH and SWING
apply the same cutoffs to different fused scores because they use different TA weights.

### Conflict handling

When ML and TA diverge by > 35 percentage points, `ml_ta_conflict: true` is set and ML
weight is reduced by up to 25% (proportional to gap size). This makes the signal more
TA-conservative when the ML model is fighting the price structure.

At-Resistance filter compresses the fused score 15% toward neutral — it does **not** block
entry. A stock at resistance with an otherwise strong signal will show a slightly lower
confidence but can still be BUY.

### Why not-all-green still shows BUY

See `docs/AI_SIGNAL.md § Reading the signal — common questions` for the worked ARMK example
and full explanation. Short version: each pillar takes `max()` of its sub-scores. A stock
with strong Trend + Momentum scores can easily clear the BUY threshold even if Volume or
Structure are neutral/negative. The individual indicator checks (RSI, MACD, OBV etc.) are
not independent gates.

### Trustworthiness concerns (open items)

- **AUD-C1 (ML label imbalance)**: BUY labels may be overrepresented in training data during
  bull periods, causing the ML ensemble to fire too easily in flat markets. Mitigation: the
  ml_ta_conflict flag and TA composite provide an independent check.
- **AUD-C2 (calibrator leakage)**: The Platt/isotonic calibrator is fit on the full training
  set rather than out-of-fold — the calibrated probabilities may be overfit. Impact: `bullish_probability`
  may be slightly inflated but the ranking ordering is still valid.
- **Signal freshness**: Signals persist with their `ts` from the last state-change. A BUY
  signal set at Friday 9:30 AM may be 26 hours old by Monday open. The 26h cutoff in the
  entry scanner handles this — but if a stock deteriorated Friday afternoon after the
  signal was locked in, the paper engine won't know. **Mitigation**: monitor position
  closely for first 2 days.

---

## 3. Email Alert System — Independence from Paper Trading

### Email alert types

| Type | Trigger | Handled by |
|---|---|---|
| Signal change alert | Signal changes state (BUY→HOLD etc.) | `email_service.send_signal_alert_email()` |
| Price alert | Price crosses a user-set threshold | `email_service.send_price_alert_email()` |
| Trade exit notification | Paper trade is closed (any reason) | `email_service.send_trade_exit_email()` |

### Paper trading is NOT dependent on email alerts

Paper trading buys are driven entirely by:
1. Signal state in DB (`Signal.signal == "BUY"`)
2. Portfolio config (enabled, paused, regime filter)
3. `_should_enter()` scoring (price zone, R:R, volume, conviction)

Email is **output-only** from paper trading: after a position closes, `_send_exit_emails()`
fires to notify users subscribed to that symbol via `SignalAlert`. The buy scan runs
completely independently — email subscription status has no effect on entry decisions.

The price alert checker (`check_price_alerts`) runs every 1 minute via interval trigger.
This is the only job confirmed always executing — it's the reference that proves the
scheduler thread is alive and working.

---

## 4. Regime Engine Quality

### Classification logic

```
bear:     SPY < 50EMA AND VIX > 30  OR  SPY < 200EMA AND 20d return < -8%
risk_off: SPY < 50EMA AND VIX > 25  (both legs required — single VIX spike does NOT trigger)
choppy:   SPY < 20EMA  OR  VIX > 20
bull:     SPY > 20EMA AND SPY > 50EMA AND VIX < 20
neutral:  everything else
```

### Per-regime trading adjustments

| Regime | Entry allowed | Position size | Min entry score |
|---|---|---|---|
| bull | Yes | 100% | 3 |
| neutral | Yes | 100% | 3 |
| choppy | Yes | 75% | 4 |
| risk_off | Yes | 50% | 5 |
| bear | **No** | — | — |

### Early warning system (RE-9)

`is_pre_choppy`: fires when SPY is only within 1.5% of EMA20 AND VIX has risen > 8%
in the past 5 sessions. Paper trading applies choppy thresholds NOW, before the official
flip to choppy — avoiding being caught buying into a deteriorating tape.

`is_pre_risk_off`: fires when SPY is within 2% of the 50EMA AND VIX is already > 22.
Positions get tighter trails immediately.

### Trail tightening in adverse regimes

The trail ATR multiplier (default 2.0×) scales down in adverse regimes:
- `regime_trail_adj = 0.8` in choppy/risk_off/bear  
- Effectively tightens trail to 1.6× ATR, locking in gains faster when the market is weak

### Weakness: single-source regime data

The regime engine downloads SPY, QQQ, and VIX from yfinance at runtime. If yfinance
fails, the engine falls back to `state: "neutral"` to avoid blocking all trading on a
data error. This is the correct default — but it means a sustained yfinance outage would
let entries happen in a potentially bear market.

---

## 5. Hold and Sell Decision Logic

### Full exit hierarchy (evaluated in order each cycle)

1. **Hard stop-loss** — price drops below `current_stop` → immediate exit. Stop set at
   entry as `max(game_plan.stop, entry * (1 - max_loss_per_trade_pct))`.

2. **Target reached** — price reaches `game_plan.take_profit` → exit 100%.

3. **Signal decay exit** — signal changes to WAIT AND signal has been WAIT for >
   `wait_exit_days` (default 7) days → exit. Prevents holding a fading position.

4. **Hold stall exit** — signal is HOLD, held for > `hold_stall_days` (default 30),
   AND unrealized gain is < `hold_stall_max_gain` (default 5%) → exit. Clears dead money.

5. **Time stop** — held for > `max_hold_days` → exit. For GROWTH: 60 days (but currently
   stored as 20 in DB — needs fix).

6. **Partial profit taking** — at +7% gain, sell 50% of shares. Remaining half gets
   stop raised to breakeven. Fires only once per trade.

7. **Breakeven stop** — at +3% gain, `current_stop` raised to entry price. Prevents
   a winner from turning into a loser.

8. **ATR trailing stop** — armed after +5% gain. `new_trail = highest_price - ATR × 2.0`.
   Stop ratchets up continuously with new price highs. Never falls below initial stop.

9. **Double-top tightening** — if `signal.reasons.double_top_breakdown` is true while
   holding, trail multiplier tightens to 1.2× ATR (from 2.0×). Faster exit if reversal confirmed.

### What the engine does NOT yet use for sell decisions

The following signals are visible in the data but not yet wired into exit logic:

| Signal | Why it matters | Priority |
|---|---|---|
| K-Score deterioration | K-score drops 15+ pts → institutional flow reversing | High |
| OBV divergence | Price rising but OBV falling → distribution, not accumulation | High |
| Relative strength vs sector | Stock lagging sector by > 10% → momentum loss | Medium |
| Peer deterioration | Multiple sector peers break down while stock holds | Medium |
| Earnings 2d proximity hold | Don't trail-stop out before binary event (can gap both ways) | Medium |
| Market breadth cascade | Multiple stocks hitting circuit breakers same day → systemic | Low |

---

## 6. Improvement Items (add to tracker)

See the improvements tracker for the full list. Key new items from this review:

### Critical (fix before Monday open)
- **PT-C1**: Fix portfolio config scale values in DB (`risk_per_trade_pct=0.01`, `max_position_pct=0.10`, `max_hold_days=60`)

### High priority
- **PT-H1**: Add portfolio config validation on save — reject values where `risk_per_trade_pct > 0.05` (users entering 1 instead of 0.01)
- **PT-H2**: Populate `stock_id` on PaperTrade entry row so double-top mid-trade detection actually works
- **PT-H3**: Add K-score deterioration exit — if K-score drops 15+ pts from entry value, begin exit
- **PT-H4**: Add OBV divergence exit signal — OBV declining > 5% over 10 bars while price holds → tighten trail
- **PT-H5**: Add admin endpoint `POST /paper-portfolio/run-step` to trigger `paper_trading_step()` immediately for testing

### Medium priority
- **PT-M1**: Add relative strength vs sector exit — stock lagging sector ETF by > 10% over 5 days → initiate exit
- **PT-M2**: Add earnings proximity protection — if earnings within 2 trading days, freeze trailing stop to prevent stop-out before binary event
- **PT-M3**: Add paper trading dashboard — entry/exit log, equity curve chart, open positions table in UI
- **PT-M4**: Regime engine: add VIX term-structure check (VIX9D vs VIX as proxy for near-term panic vs complacency)
- **PT-M5**: Regime engine: use market breadth (% of S&P500 stocks above 200EMA) as a confirmatory signal

### Documentation
- See `docs/AI_SIGNAL.md` for FAQ on signal interpretation
- See `docs/AUDIT_2026-06-12.md` for all AUD-* items (C1, C2, M7, M8, M10, M13, M19, M23 still pending)
