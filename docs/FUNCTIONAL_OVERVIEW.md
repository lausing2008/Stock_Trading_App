# StockAI — Functional Overview

*Regenerated 2026-07-27 — every value below was verified directly against source code
(a prior 2026-07-23 version of this doc was found to have significant errors: wrong buy
thresholds, wrong decision-engine layer count, 3 missing pages, and several other wrong
numbers — see the "What changed from the last version" note at the end).*

---

## 1. Page Inventory (46 real pages)

| Page | URL | Core Function |
|------|-----|---------------|
| Login | `/login` | JWT auth |
| Dashboard | `/` | Stock list with sector coloring, K-Score-derived badges, market indices/breadth |
| Watchlist | `/watchlist` | Curated per-user list with notes, alerts, relative performance |
| Stock Detail | `/stock/[symbol]` | Full analysis: chart, indicators, AI Game Plan generation, AI chat sidebar |
| Opportunities | `/opportunities` | Strategy screener: Top Picks / Swing / Short-Term / Long-Term / Growth / AI Signal tabs, optional AI 2-5 day outlook |
| Rankings | `/rankings` | K-Score leaderboard (US/HK), watchlist filter, peer-compare drawer |
| Screener | `/screener` | Multi-factor filter with many sortable columns (RVOL, technical/momentum/value/growth sub-scores) |
| Trade Board | `/board` | Kanban: Radar → Planning → Active → Closed |
| Paper Portfolio | `/paper-portfolio` | Live autonomous paper trading: positions, trades, equity curve, decision log, config, divergence vs. real trading |
| Positions | `/positions` | Manual P&L tracker, allocation donut, cash by market |
| Portfolio | `/portfolio` | Mean-Variance / Risk Parity / HRP / AI Allocation optimizer against watchlist holdings |
| Strategies | `/strategies` | Rule-DSL backtest builder + preset strategies |
| Alerts | `/alerts` | Price/signal alert condition management (EMA cross, golden cross, 52wk high, etc.) |
| Alerts Guide | `/alerts-guide` | Static reference explaining every alert job's schedule/scope/cooldown (hand-maintained companion to `scheduler.py`) |
| Research | `/research` | Symbol-entry page routing to a full report |
| Research Report | `/research/[symbol]` | Full AI research report: technical, fundamental, company, industry, economic tabs |
| Signal Accuracy | `/signal-accuracy` | Historical directional-accuracy tracker, win rate/profit factor |
| Insider | `/insider` | SEC Form 4 transactions + featured-trader/cluster detection |
| Congress | `/congress` | STOCK Act disclosures + conviction screener |
| Earnings | `/earnings` | Combined economic/earnings/dividend calendar with urgency coloring |
| Intelligence | `/intelligence` | Multi-tab event-intelligence hub: Overview/Economic/Earnings/Insider/Congress/Catalyst/Risk/Political/CAPE |
| Regime | `/regime` | Market regime state + min-score-gating explanation |
| Forecast | `/forecast` | AI swing-trade screener: "My Stocks" or "Broad Screen" AI-suggested tickers |
| Horizon Compare | `/horizon-compare` | Static side-by-side of the 4 style profiles, sourced from `_STYLE_PROFILES` |
| Signal Filters | `/signal-filters` | Which signals were suppressed and why (maps to real compression conditions) |
| Signal Quality | `/signal-quality` | Confidence-calibration reliability diagram (expected vs. actual win rate per band) |
| Signal Tuning | `/signal-tuning` | Admin view of the self-tuning system: win rates, watchdog state, effective vs. default thresholds |
| Trade Performance | `/trade-performance` | Signal-based synthetic trade performance (win rate, profit factor, avg win/loss) |
| Watchlist Performance | `/watchlist-performance` | Watchlist-level backtested performance + rotation history |
| Watchlist Rotation Explainer | `/watchlist-rotation-explainer` | Static doc explaining the auto-rotation logic per watchlist style/market mix |
| Sector Rotation | `/sector-rotation` | Sector ETF momentum / relative-strength view |
| Short Selling | `/short-selling` | Short interest screener |
| Short Squeeze | `/short-squeeze` | High short interest + rising price candidates |
| Heatmap | `/heatmap` | Sector/stock performance heatmap |
| Compare | `/compare` | Multi-symbol normalized price-performance chart |
| Analyst | `/analyst` | Analyst upgrade/downgrade/initiation feed |
| Journal | `/journal` | Manual + auto-generated trade journal with exit-reason badges |
| Learn | `/learn` | In-app user guide |
| Reports | `/reports` | Per-market report tabs: Trend/Assets/Top Stocks/Money Flow/News & Macro/CAPE/Self-Tuning |
| Improvements | `/improvements` | This tracker (all feature/bug items with status) |
| Admin Health | `/admin-health` | Scheduler job status, ML model metrics, service health |
| Admin Signals | `/admin-signals` | Every AI signal ever emitted, filterable audit log |
| Paper Gates | `/paper-gates` | Static reference explaining the full paper-trading gate pipeline stages |
| Decide | `/decide` | Interactive single-symbol decision-engine tester — live verdict for a typed-in symbol |
| Gate | `/gate` | Trivial app-wide password wall (unrelated to trading "gates" — a Next.js API password check before `/login`) |
| Settings | `/settings` | AI provider keys, broker connections, push notifications, password, admin impersonation |

---

## 2. Signal Lifecycle

```
WAIT  ── fused_prob between sell_threshold and buy_threshold, no clear lean
HOLD  ── fused_prob approaching buy_threshold (bullish lean, not yet BUY)
BUY   ── fused_prob ≥ buy_threshold (regime- AND style-tiered — see Section 3 below;
          there is no single "the" buy threshold in this system)
          → Conviction gate must pass (or "near," 1 soft miss) for an email alert
          → decision-engine must agree (fail-open) for a paper trade entry
SELL  ── fused_prob ≤ 0.35 (FLAT for every style and every regime — no regime tiers
          exist for SELL at all, unlike BUY; a known, deliberate asymmetry, not a bug)

Signal transitions that trigger email alerts:
  Bullish: SELL→HOLD, SELL→WAIT, SELL→BUY, WAIT→HOLD, WAIT→BUY, HOLD→BUY
  Bearish: BUY→HOLD, BUY→WAIT, BUY→SELL, HOLD→WAIT, HOLD→SELL, WAIT→SELL
  Exit alerts bypass the conviction gate — always sent.
```

---

## 3. Trading Horizons — Real `_STYLE_PROFILES` Values

Buy threshold is a per-style, per-REGIME dict — not a single number per style. Full table:

| Style | bull | high_vol | bear | unknown | ml_weight_cap | adx_min | min_pillars_for_buy |
|-------|------|----------|------|---------|---------------|---------|----------------------|
| SHORT | 0.63 | 0.65 | 0.68 | 0.62 | 0.30 | 27 | 2 (default, not overridden) |
| SWING | **0.72** | 0.74 | 0.76 | 0.72 | 0.65 | 15 | 3 (explicit override) |
| LONG | 0.60 | 0.65 | 0.70 | 0.62 | 0.45 | none | 3 (explicit override) |
| GROWTH | 0.60 | 0.65 | 0.68 | 0.60 | 0.60 | 12 | 2 (default, not overridden) |

SWING's bull threshold (0.72) is notably the tightest of all four styles in the bull
regime — the opposite of what a "standard swing trade, easier to trigger" intuition might
suggest. This is the result of several successive audits (SA-28 through SA-32 in the
`signals.py` changelog) finding SWING's BUY signals had the worst historical win rate at
moderate confidence and tightening the bar in response — not an oversight.

Game-plan geometry (stop/target percentages, separate from the buy_threshold above):

| Style | Stop | Default Target | ATR Stop Mult | Typical Hold |
|-------|------|-----------------|---------------|--------------|
| SHORT | -3.0% | +5% | 2.0x | short (days) |
| SWING | -5.5% | +12% | 2.0x | days-weeks |
| LONG | -10.0% | +25% | 2.0x | weeks-months |
| GROWTH | -12.0% | +35% | 3.0x | weeks-months |

GROWTH style differences from SWING (per the file's own documented rationale):
- No SMA50 > SMA200 (golden cross) requirement — growth stocks can consolidate below
  their 200-day average for months
- RSI 38-80 valid (vs. SWING's 45-72) — momentum names run "overbought" by traditional
  standards without it being a real warning sign
- ADX minimum 12 (vs. SWING's 15)
- No relative-strength compression (`rs_compression: None`)
- Weekly BUY gate disabled entirely (`skip_weekly_gate: True`)

---

## 4. Conviction Gate (BUY Email Alert Filter)

Real function: `_is_conviction_buy()` in `services/market-data/src/services/scheduler.py`.
Not a strict "all layers must pass" gate — it has a "near" tier allowing exactly ONE soft
failure.

```
Layer 2 — K-Score           K-Score ≥ 55
Layer 4a — Uptrend structure  GROWTH: price > SMA50 only.
                               All other styles: SMA50 > SMA200 AND price > SMA50.
                               (A double-bottom neckline break auto-passes this layer
                               regardless of style.)
Layer 4b — Entry timing RSI   GROWTH: 50.0-85.0.  All other styles: 45.0-72.0.
Layer 4c — MACD momentum      Passes on a zero-line cross-up, positive histogram, OR a
                               rising histogram — fails only if negative AND falling.
Layer 4d — Volume confirms    OBV trend bullish (boolean)
Layer 4e — Trend strength     ADX > 25 (adx_trending boolean)
Layer 5 — ML confirms TA      Regime-adaptive threshold:
                                 bull: 0.65   neutral/unknown: 0.70
                                 high_vol: 0.78   bear: 0.78
                               Soft-passes ("TA-only") if ml_prob is None OR the model's
                               AUC < 0.50 (ml_weight == 0) — an untrained/near-random
                               model never blocks the gate on its own.

Disqualifiers (hard, no soft tier, block regardless of everything else):
  - Bearish RSI divergence
  - Stochastic RSI overbought (> 0.80)

Soft-failure keywords ("near" tier): any failure message containing "OBV", "ADX",
"ML probability", or "MACD" counts as SOFT. K-Score, uptrend structure, RSI entry
timing, and both disqualifiers are HARD failures.

Verdict:
  0 failures                        → "full"
  0 hard failures, exactly 1 soft   → "near"   (both "full" and "near" pass the gate)
  anything else                     → "failed" (no alert)
```

(Layers 1 and 3 — fundamental analyst-consensus check and the BUY-signal-direction check —
are verified by the CALLER before this function is ever invoked, not inside it.)

---

## 5. Decision Engine Scoring (Paper Trade Gate)

Real function: `services/decision-engine/src/api/core/scorer.py`. The code's own docstring
calls this a "7-layer" model, but it actually implements **13 distinct scoring branches**:

| Branch | Condition | Points |
|--------|-----------|--------|
| Price zone | Below entry2, or between entry2 and breakout | +2 |
| Price zone | Within 3% above breakout | +1 |
| Price zone | Extended > 3% above breakout | −3 |
| R:R quality | R:R ≥ 3.5 | +2 |
| R:R quality | R:R ≥ 2.5 | +1 |
| Volume | Volume z-score > 1.0 | +1 |
| Volume | Volume z-score < −0.5 | −1 |
| Earnings proximity | 6-10 days to earnings | −1 |
| Fused ML+TA probability | ≥ 0.70 | +1 |
| Fused ML+TA probability | < 0.58 | −1 |
| Confidence trajectory | Accelerating > 8pts (currently dead code — the input field is never populated) | +1 |
| Confidence trajectory | Decelerating < −8pts (same dead-code caveat) | −1 |
| Signal freshness | < 4h old | +1 |
| Signal freshness | > 18h old | −1 |
| Catalyst — insider | Insider score ≥ 60 | +1 |
| Catalyst — insider | Insider score < −30 | −1 |
| Catalyst — congress | Congress score > 50 | +1 |
| Pre-regime early warning | Pre-risk-off or pre-choppy | −1 |
| Research alignment | STRONG BUY +2, BUY +1, WATCH 0, AVOID −1, SELL −2 |
| Market regime | bull +1, neutral 0, choppy −1, risk_off −2, bear −99 (hard block) |
| K-Score conviction | ≥ 55 → +1, else −1 |
| Cross-horizon consensus | ≥2 other horizons also BUY → +1; 0 others in bear/choppy → −1 |

Min score thresholds (`min_score_for_regime()`):
```
base = cfg.get("min_entry_score", 4)
bear     → 999 (always blocked)
risk_off → max(base, 5)
choppy   → max(base, 4)
Then: if recent_win_rate < 30% → +1 to whatever was computed above
       (a floor bump when the portfolio has been losing lately)
```

---

## 6. ML Ensemble

```
Per symbol, per horizon — trained weekly:

  XGBoost      (0.30 nominal weight) — primary model, Optuna-tuned hyperparams
  LightGBM     (0.45 nominal weight) — largest nominal weight, not XGBoost
  RandomForest (0.25 nominal weight)
  (weights renormalize over whichever models are actually available / not
   OOS-suppressed for this symbol — these are starting weights, not a fixed split)

  Each model individually calibrated: LogisticRegression (Platt) for small calibration
  sets, IsotonicRegression otherwise.
  Recency weighting: most recent bar weighted 5x the oldest (compute_sample_weight
  handles class imbalance separately).

  Meta-model (0.15 blend into ensemble, when live):
    prob = model_prob * 0.85 + meta_prob * 0.15
    Cross-symbol XGBoost trained on all signal_outcomes, retrained monthly, AUC-gated
    (only replaces the deployed bundle if new AUC is not strictly worse).

  ml_weight (how much the ML ensemble's probability influences the final signal,
  distinct from the per-model ensemble weights above) — a PIECEWISE ramp, not linear:
    AUC < 0.50           → 0.0   (near-random model, TA-only signal)
    0.50 ≤ AUC < 0.55     → ramps 0.0 → 0.20
    AUC ≥ 0.55            → clip(0.20 + (AUC-0.55)/0.15 * 0.55, 0.20, 0.75)
  Then capped by a per-style ml_weight_cap (0.30-0.65, see Section 3 table), floored by
  an AUC-scaled ml_weight_floor, and discounted a further 25-50% if the ML and TA scores
  disagree by more than 0.25-0.35 (logged as ml_ta_conflict).
```

---

## 7. Alert System

### Alert Types (all real, verified against `scheduler.py`)

| Type | Trigger | Delivery |
|------|---------|----------|
| Price alert | above/below threshold | Email + webhook + push |
| Signal alert | BUY/SELL transition | Email + webhook + push (conviction-gated for BUY) |
| Technical alert | EMA cross, 52wk high/low, golden/death cross, MACD cross, RSI bounce, double bottom, breakout, volume spike | Email + webhook |
| Compound alert | Base condition AND (volume_ratio / RSI / signal) | Email + webhook |
| Earnings reminder | Days-to-earnings hits a threshold | Digest email (batched per user) |
| Earnings reaction | eps_actual lands post-print | Email |
| Macro reaction | FRED/FOMC release + LLM reaction generated | Email |
| Pre-market brief | Daily before open (US 08:00 ET, HK 08:00 HKT) | Email (futures + gappers + macro + earnings) |
| Volume anomaly | RVOL > 2.5× session-scaled threshold | Email (10/day cap per user) |
| Value area breakdown | Price closes below VAL or above VAH | Email |
| Top-3 conviction | Measured win rate ≥ 70% with ≥ 30 samples | Email (only re-sends on composition change) |
| Position drawdown | Paper trade down ≥ 5% | Email |

### Delivery Channels
- Email (Gmail SMTP)
- Webhook (HMAC-SHA256 signed POST)
- Web Push (VAPID, service worker)
- In-app notifications (AppNotification table, NotificationBell component)

---

## 8. Research Engine (9 Tabs)

Triggered automatically on a new BUY signal (confidence ≥ 65%) or manually. 6-hour cooldown
prevents repeated expensive AI calls.

| Tab | Content |
|-----|---------|
| Technical | TA score breakdown, pattern detection, support/resistance |
| Fundamental | DCF fair value, sector-relative PE/PB/EV, Piotroski score |
| Company | Business model, competitive moat, management quality |
| Industry | Sector dynamics, competitive landscape, tailwinds/headwinds |
| Economic | Macro sensitivity, interest rate exposure, FX risk |
| Checklist | 10-point investment checklist (quality gates) |
| Trading Plan | Entry zones, stop, target, position sizing, catalysts |
| Position Sizing | ATR-based sizing, risk/reward, account-size-aware |
| AI Verdict | Claude/DeepSeek synthesis: STRONG BUY / BUY / WATCH / AVOID / SELL |

Report quality flags: `full` (24h TTL) / `partial` (30min TTL) / `fallback` (5min TTL)

---

## 9. Paper Trading Engine — Configuration Reality

There is no single fixed config block that applies system-wide — `_DEFAULT_CONFIG` provides
base values, which are then overridden per style (`_STYLE_OVERRIDES`) and, for HK
portfolios, again by `_HK_MARKET_OVERRIDES`. A portfolio's own saved config can override any
of these further. The base defaults, verified directly from `paper_trading_engine.py`:

```
initial_capital           50,000.0   (per-portfolio, set at creation — NOT a system-wide
                                       100,000 constant)
max_positions              6
max_sector_pct              0.25    (though one call site's own fallback literal reads
                                      0.30 instead of referencing this constant — a real,
                                      small inconsistency in the code itself worth knowing
                                      about, not a bug in this doc)
min_confidence              45.0    (SHORT/GROWTH default; SWING=50.0, LONG=40.0;
                                      ALL styles become 65.0 in the HK market — there is
                                      no single "min_confidence" number for the system)
min_kscore                  48.0    (SHORT/GROWTH; SWING=52.0, LONG=50.0)
min_entry_score              4      (US default; HK=6 — HK requires stronger
                                      multi-factor conviction)
min_rr_ratio                 2.0    (or a calibrated value once min_rr_calibration.json
                                      exists, via _default_min_rr_ratio())
decision_engine_mode        "primary"
```

Style overrides (`_STYLE_OVERRIDES`) additionally set per-style `max_hold_days`,
`trail_atr_mult`, `trail_trigger_pct`, `breakeven_trigger_pct`, `partial_tp_pct`,
`max_entry_gap_pct`, and more — see Section 3's table above for the buy-threshold/game-plan
side of this, and `paper_trading_engine.py` directly for the complete config surface (it is
too large to fully enumerate accurately in a fixed-size table).

---

## 10. Portfolio Optimizer

| Method | Algorithm | Description |
|--------|-----------|-------------|
| Mean-Variance (MVO) | SLSQP | Sharpe-maximizing, Ledoit-Wolf covariance shrinkage |
| Risk Parity | SLSQP | Equal risk contribution per asset |
| HRP | Ward clustering + inverse-variance | Hierarchical Risk Parity |
| AI Allocation | Claude/DeepSeek | LLM-driven allocation with fundamental context |

All methods: long-only, weight bounds [0, 0.4], sum-to-1 constraint. Falls back to flat
equal-weight (with a `fallback_reason` field surfaced to the caller) if a constraint makes
the real optimization mathematically infeasible.

---

## 11. Data Sources

| Source | Data | Cost | Key |
|--------|------|------|-----|
| yfinance | OHLCV, fundamentals, options chain, analyst ratings, news | Free | None |
| FRED | Macro economic releases (CPI, NFP, GDP, PCE) | Free | Free API key |
| Federal Reserve RSS | FOMC press releases | Free | None |
| SEC EDGAR | 8-K filings, Form 4 insider transactions | Free | None |
| HKEX | Stock Connect southbound flows | Free (scrape) | None |
| multpl.com | Shiller CAPE ratio | Free (scrape) | None |
| kadoa-org GitHub feed | Congress trading data (STOCK Act) | Free | None |
| Claude API | AI chat, research reports, macro reactions, risk agent | Paid | Settings (admin-configured, stored in Redis) |
| DeepSeek API | AI chat alternative | Paid | Settings |

---

## 12. Key Metrics Tracked

| Metric | Where | Purpose |
|--------|-------|---------|
| Signal win rate | signal_outcomes | Calibration, conviction gate |
| Calibrated win rate | confidence-calibration buckets | Per-signal quality annotation |
| Information Coefficient (IC) | /signals/information_coefficient | Rank-correlation signal quality |
| Alpha decay curve | /signals/alpha_decay | Optimal hold duration per horizon |
| Factor attribution edge | /signals/factor_attribution | Which indicators predict wins |
| Rolling 30d accuracy | /signals/rolling_accuracy | Model drift detection |
| Paper portfolio Sharpe | paper_equity_curve | Strategy quality |
| Paper portfolio alpha vs SPY | summary API | Excess return |
| Tune history EV lift | tune_history.validation_ev_pct | Did calibration help on the validation slice? |
| Realized EV after promotion | tune_history.realized_ev_pct_after | Did it actually help live, weeks later? |
| Position scaling hit rate | ps:shadow:resolved | Shadow-mode accuracy (not yet live-traded) |
| Meta-model AUC | meta_model.joblib bundle | Cross-symbol model quality |

---

## What changed from the last version of this doc (2026-07-23)

A review pass found the previous version had: 3 real pages missing from the inventory
(`/settings`, `/alerts-guide`, `/watchlist-rotation-explainer`); SWING's bull buy_threshold
claimed as 0.62 (real value: 0.72 — the tightest of all 4 styles, not a middling one); the
decision-engine scorer described as "7-layer" when it has 13 distinct branches (the code's
own docstring undersells itself the same way — this doc now lists all 13); the conviction
gate described as a strict "all 5 must pass" when it actually has a "near" tier allowing one
soft failure, plus an ML soft-bypass for untrained models; a linear `ml_weight` formula that
doesn't match the real piecewise ramp; ensemble weights claimed 40/35/25 when the code uses
30/45/25; and a paper-trading config block presented as one fixed system-wide JSON object
when in reality `min_confidence`/`min_kscore`/`min_entry_score` all vary by style and market
(HK is meaningfully stricter across the board, not just a currency/regime detail). This
version was rewritten with every value pulled directly from the executing code path.
