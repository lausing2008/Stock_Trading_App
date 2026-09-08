# Real-Time News, Events & Market Intelligence — Capability Assessment & Enhancement Plan

**Date:** 2025-08-22  
**Purpose:** Document current capabilities and enhancement opportunities for leveraging real-time news, events, earnings, macro indicators (CPI, PCE, FOMC), and market sentiment (FOMO, squeeze alerts) to improve market trend analysis and stock alerts.

---

## Executive Summary

StockAI already has **extensive event intelligence infrastructure** built across two dedicated services (event-intelligence, news-intelligence) plus deep integration into signal generation, paper trading gates, and alert delivery. This document catalogs what's ALREADY BUILT vs. what's genuinely still open, to avoid re-implementing existing features.

**Key Finding:** ~80% of the "real-time news/events" capability the user asked about is ALREADY IMPLEMENTED. The remaining gaps are specific, well-scoped enhancements rather than greenfield builds.

---

## Part 1: Current Capabilities (Already Built)

### 1.1 Macro Economic Events (CPI, PCE, FOMC, NFP, GDP)

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| FRED data sync (13 series) | ✅ DONE | `event-intelligence/economic.py` | CPI, PPI, GDP, NFP, PCE, Fed Funds, Retail Sales, Consumer Conf, Housing Starts, Jobless Claims |
| FOMC calendar (2025-2027) | ✅ DONE | `event-intelligence/economic.py:_FOMC_DATES` | 8 meetings/year seeded |
| Release-date calendar from FRED | ✅ DONE | `event-intelligence/economic.py:sync_fred_release_dates()` | Real release dates, not reference periods |
| Macro blackout signal suppression | ✅ DONE | `signal-engine/signals.py`, `paper_trading_engine.py` | Blocks BUY entries 2h pre-FOMC/CPI/NFP |
| Fast poll for CPI/PPI/GDP/NFP releases | ✅ DONE | `event-intelligence/macro_reaction.py:check_release_day_fast_poll()` | Armed 8:30am-12pm ET on release days |
| FOMC statement RSS poll | ✅ DONE | `event-intelligence/macro_reaction.py:check_fomc_statement_poll()` | Armed 2-3pm ET on FOMC days |
| LLM reaction analysis | ✅ DONE | `event-intelligence/macro_reaction.py:generate_reaction()` | Claude Haiku, structured JSON output |
| Macro reaction email alerts | ✅ DONE | `market-data/scheduler.py:check_macro_reaction_alerts()` | 1-minute interval delivery |
| Events Calendar page | ✅ DONE | `frontend/src/pages/earnings.tsx` | Macro + earnings + ex-dividend unified view |
| Premarket Brief email | ✅ DONE | `market-data/scheduler.py:send_premarket_brief()` | 8:00am local, includes today's macro events |

**Latency:** 5-45 minutes after release (FRED mirror lag) — explicitly NOT tick-level.

### 1.2 Earnings Events

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| Earnings calendar sync | ✅ DONE | `event-intelligence/earnings.py` | yfinance + AlphaVantage |
| Earnings proximity signal compression | ✅ DONE | `signal-engine/signals.py:_apply_style_signal()` | Regime-aware: bull+beat≥70% = boost, bear = compress |
| Earnings hard-reject gate | ✅ DONE | `paper_trading_engine.py:_should_enter()` | DTE≤5 = hard reject |
| Earnings-aware position sizing | ✅ DONE | `paper_trading_engine.py` | 50% size if DTE≤10, 75% if 11-20 |
| Earnings reaction email (plain) | ✅ DONE | `market-data/scheduler.py:check_earnings_reactions()` | EPS actual vs estimate |
| Earnings impact LLM analysis | ✅ DONE | `market-data/scheduler.py:check_earnings_impact_alerts()` | Claude-generated impact assessment |
| Earnings surprise chart | ✅ DONE | `frontend/src/pages/stock/[symbol].tsx` | Last 8 quarters, beat rate badge |
| Pre-earnings screener | ✅ DONE | `frontend/src/pages/opportunities.tsx` | "Earnings This Week" panel |

### 1.3 Short Squeeze & FOMO Detection

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| Short squeeze score (0-100) | ✅ DONE | `frontend/src/pages/stock/[symbol].tsx` | float_pts + ratio_pts + options_pts |
| Classic squeeze alert (≥15% float, ≥3% move) | ✅ DONE | `market-data/scheduler.py:check_short_squeeze_alerts()` | + RVOL confirmation |
| Squeeze ignition alert (1-2.9% early warning) | ✅ DONE | `market-data/scheduler.py:check_squeeze_ignition_alerts()` | Lower threshold, same RVOL gate |
| Gamma unwind alerts (calls + puts) | ✅ DONE | `market-data/scheduler.py:check_gamma_unwind_alerts()` | Options flow driven |
| Pre-breakout compression alert | ✅ DONE | `market-data/scheduler.py:check_prebreakout_alerts()` | Price compression + high short interest |
| Squeeze watch list | ✅ DONE | `frontend/src/pages/short-squeeze.tsx` | Track candidates, auto-revert on fade |
| Squeeze alert outcome tracking | ✅ DONE | `market-data/scheduler.py:evaluate_squeeze_alert_outcomes()` | 5d/10d/20d forward returns |
| Squeeze alert backtest | ✅ DONE | `market-data/admin.py:squeeze_alert_backtest()` | Historical win rate analysis |
| Days-to-cover critical alert | ✅ DONE | `scheduler.py:_SQUEEZE_CRITICAL_DAYS_TO_COVER` | ≤2 days = critical warning |

### 1.4 News Intelligence

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| Per-symbol news fetch | ✅ DONE | `market-data/api/news.py` | yfinance + Google News RSS |
| VADER sentiment scoring | ✅ DONE | `market-data/api/news.py` | -1 to +1, mapped to 0-100 |
| News sentiment signal compression | ✅ DONE | `signal-engine/signals.py` | score<25 = 30% compress, <35 = 20% |
| Real-time news service | ✅ DONE | `news-intelligence/` | PR Newswire RSS, SEC EDGAR real-time, Alpaca WebSocket |
| Hot news Redis flag | ✅ DONE | `news-intelligence/storage.py:_mark_hot()` | Material negative = suppress BUY signals |
| LLM headline classification | ✅ DONE | `news-intelligence/classify.py` | sentiment_label, material, macro_category |
| News in stock detail | ✅ DONE | `frontend/src/pages/stock/[symbol].tsx` | Last 10 articles with sentiment |

### 1.5 Insider/Congress/Institutional Intelligence

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| SEC Form 4 insider trades | ✅ DONE | `event-intelligence/insider.py` | Daily sync from EDGAR |
| Congress trading | ✅ DONE | `event-intelligence/congress.py` | kadoa-org feed (replaced dead sources) |
| 13F institutional holdings | ✅ DONE | `event-intelligence/institutional.py` | Quarterly, 7 major funds |
| Institutional transactions (QoQ diff) | ✅ DONE | `event-intelligence/institutional.py:_write_institutional_transactions()` | initiate/exit/add/trim |
| Catalyst score composite | ✅ DONE | `event-intelligence/catalyst.py` | earnings + insider + congress + economic |
| Signal enrichment with catalyst scores | ✅ DONE | `signal-engine/routes.py:_bulk_persist()` | insider_score, congress_score in reasons |
| Intelligence page | ✅ DONE | `frontend/src/pages/intelligence.tsx` | 8 tabs: Overview, Economic, Earnings, Insider, Congress, Catalyst, Risk, Political |

### 1.6 Market Regime & Trend Analysis

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| 5-state regime classifier | ✅ DONE | `market-data/api/routes.py:get_regime()` | bull/neutral/choppy/risk_off/bear |
| HMM regime model | ✅ DONE | `market-data/hmm_regime.py` | Hidden Markov Model, hmm_bear_pressure |
| Regime-aware signal thresholds | ✅ DONE | `signal-engine/signals.py` | Different ML/confluence/confidence floors per regime |
| Regime-aware position sizing | ✅ DONE | `paper_trading_engine.py` | regime_size_mult: bull=1.0, neutral=0.85, choppy=0.70, risk_off=0.0, bear=0.0 |
| Risk-off hard block | ✅ DONE | `paper_trading_engine.py`, `decision-engine/hard_rejects.py` | regime_risk_off_gate=True default |
| Market breadth indicators | ✅ DONE | `market-data/api/routes.py:market_breadth()` | US + HK, advance/decline, new highs/lows |
| Fear & Greed proxy | ✅ DONE | `market-data/api/routes.py:fear_greed()` | VIX-based, 0-100 scale |
| CAPE / Bubble Warning | ✅ DONE | `event-intelligence/valuation.py` | Shiller CAPE ratio tracking |

### 1.7 Cross-Asset Signals (NEW - Built 2026-08-19)

| Feature | Status | Location | Notes |
|---------|--------|----------|-------|
| Bond/Credit/FX/Commodity sync | ✅ DONE | `event-intelligence/economic.py:sync_cross_asset()` | TLT, HYG, LQD, DXY, GLD, USO, ^TNX |
| Cross-asset reading endpoint | ✅ DONE | `event-intelligence/routes.py:GET /events/cross-asset/latest` | Latest readings + regime implications |
| Signal enrichment | ✅ DONE | `signal-engine/routes.py` | cross_asset_regime in reasons |

---

## Part 2: Alert Delivery Infrastructure

All alerts flow through a unified delivery system:

| Alert Type | Trigger | Delivery | Feature Flag |
|------------|---------|----------|--------------|
| Price alerts | Price crosses threshold | Email | Per-subscription |
| Signal alerts | BUY/SELL transition | Email | alert_mode (all/buy_only) |
| Earnings reaction | EPS released | Email | Per-subscription |
| Earnings impact (LLM) | EPS released | Email | `stockai:admin:feature:earnings_llm_reaction_enabled` |
| Macro reaction (LLM) | CPI/FOMC/etc released | Email | `stockai:admin:feature:macro_llm_reaction_enabled` |
| Short squeeze | ≥15% float + ≥3% move | Email | Per-subscription |
| Gamma unwind | Options flow spike | Email | Per-subscription |
| Volume anomaly | RVOL spike | Email | Per-subscription |
| Premarket brief | 8:00am local | Email | Per-user |
| Morning digest | 8:50am local | Email | Per-user |

---

## Part 3: Genuine Gaps (Not Yet Built)

### 3.1 HIGH PRIORITY — Direct User Ask

| Gap | Description | Effort | Recommendation |
|-----|-------------|--------|----------------|
| **Theme/Sector Rally Prediction** | User asked for "which themes (GPU, MLCC, Gold, Space) will rally next few weeks" — NO existing theme-level granularity exists (only broad GICS sectors) | L | Requires hand-curated theme→symbol mappings + new rollup logic |
| **FOMO Sentiment Indicator** | No explicit "FOMO meter" — closest is squeeze score + options flow | S | Could composite: squeeze_score + call_put_ratio + volume_z + social_mentions (if added) |
| **Market Pulse Dashboard** | User wants "real-time alerts for stocks and market" in one view | M | New dashboard compositing: regime, breadth, VIX, top movers, active alerts |

### 3.2 MEDIUM PRIORITY — Enhancement Opportunities

| Gap | Description | Effort | Recommendation |
|-----|-------------|--------|----------------|
| **Social Sentiment** | No Twitter/Reddit/StockTwits integration | M | Paid APIs or scraping (ToS risk); lower priority than structured data |
| **Options GEX/Max Pain** | Gamma unwind alert exists but is NOT real GEX (per its own source comment) | M | Requires options chain depth data not currently available |
| **Sector-Level News Pulse** | News is per-symbol only; no "tech sector news sentiment" rollup | S | Aggregate per-symbol sentiment by sector |
| **Earnings Call Transcript NLP** | generate_earnings_impact() uses numeric EPS, never actual transcript | L | Requires transcript data source (paid) |
| **Expected/Consensus Values** | EconomicEvent.expected_value column exists but never populated | S | Cleveland Fed nowcast for CPI/PCE (free proxy) |

### 3.3 LOW PRIORITY — Nice-to-Have

| Gap | Description | Effort | Recommendation |
|-----|-------------|--------|----------------|
| **Volatility Targeting** | Kelly endpoint exists but is advisory-only (zero consumers) | M | Wire into position sizing |
| **Dynamic Multi-Strategy Reallocation** | 5 portfolios exist with capital isolation; no auto-rebalance between them | L | Complex, needs clear rules |
| **Alternative Data** | No satellite imagery, web traffic, job postings | L | Paid data sources; ROI unclear |

---

## Part 4: Recommended Enhancements

### 4.1 FOMO/Momentum Composite Score (NEW)

**Purpose:** Surface "FOMO conditions" as a single 0-100 score per symbol.

**Components (all already computed):**
- `squeeze_score` (0-100) — short interest + days to cover + options flow
- `call_put_ratio` — from options flow endpoint
- `volume_z` — from signal reasons
- `momentum_score` — from K-Score sub-scores
- `regime_is_favorable` — from regime classifier

**Formula:**
```python
fomo_score = (
    squeeze_score * 0.30 +
    min(call_put_ratio * 20, 30) +  # cap at 30 pts
    min(volume_z * 10, 20) +         # cap at 20 pts
    momentum_score * 0.20
)
# Regime multiplier: bull=1.0, neutral=0.8, choppy=0.6, risk_off=0.3, bear=0.2
fomo_score *= regime_mult
```

**Effort:** S (all inputs exist, just composition + UI)

### 4.2 Theme/Sector Rotation Alert

**Purpose:** Alert when a sector/theme shows coordinated strength.

**Approach:**
1. Define theme→symbol mappings (manual curation):
   ```python
   THEMES = {
       "GPU/AI Chips": ["NVDA", "AMD", "AVGO", "MRVL", "QCOM"],
       "MLCC/Passive": ["AMAT", "LRCX", "KLAC"],
       "Gold Miners": ["GLD", "GDX", "NEM", "GOLD"],
       "Space": ["RKLB", "ASTS", "LUNR"],
       # ...
   }
   ```
2. Compute theme momentum = avg(K-Score momentum) across theme symbols
3. Alert when theme momentum crosses threshold AND ≥60% of symbols are BUY

**Effort:** M (theme mapping is manual; logic is straightforward)

### 4.3 Market Pulse Dashboard

**Purpose:** Single-page view of "what's happening right now."

**Sections:**
1. **Regime Banner** — Current regime + VIX + Fear/Greed
2. **Macro Events Today** — From events calendar
3. **Active Alerts** — Squeeze/gamma/volume alerts fired in last 4h
4. **Top Movers** — Biggest gainers/losers in watchlist
5. **Sector Heat Map** — Sector performance today
6. **News Pulse** — Latest material headlines (from news-intelligence)

**Effort:** M (all data exists, just composition)

### 4.4 Sector News Sentiment Rollup

**Purpose:** "Tech sector sentiment is -15 (bearish)" as a single number.

**Approach:**
1. For each sector, aggregate recent_items() from news-intelligence
2. Average sentiment_label scores weighted by recency
3. Surface on Market Pulse dashboard + sector rotation view

**Effort:** S

---

## Part 5: What NOT to Build

Based on the improvements tracker analysis, these are explicitly **not recommended**:

| Item | Reason |
|------|--------|
| Rebuild confidence calibration | Already done (AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK) |
| Rebuild risk-off blocking | Already done (T226-A, regime_risk_off_gate=True) |
| Rebuild symbol blacklist | Already exists (RestrictedSymbol table + admin CRUD) |
| Rebuild entry-score calibration | Already done (calibrated logistic regression, activates at 100 trades) |
| Rebuild squeeze alert thresholds | No outcome data yet to justify changes |
| Build "alpha decay" tracking | Name collision — existing endpoint measures different axis |
| Build Fama-French factor exposure | Name collision — existing endpoints are not FF regressions |

---

## Part 6: Implementation Priority Matrix

| Priority | Item | Effort | Impact | Dependencies |
|----------|------|--------|--------|--------------|
| P0 | FOMO Composite Score | S | High | None — all inputs exist |
| P0 | Market Pulse Dashboard | M | High | None — all data exists |
| P1 | Theme/Sector Rotation Alert | M | Medium | Manual theme mapping |
| P1 | Sector News Sentiment Rollup | S | Medium | None |
| P2 | Expected/Consensus Values (nowcast) | S | Low | Cleveland Fed API |
| P3 | Social Sentiment | M | Low | Paid API or scraping |

---

## Part 7: Measurement Framework

For any new feature, track:

| Metric | Query |
|--------|-------|
| Alert fire rate | `SELECT alert_type, COUNT(*) FROM squeeze_alert_outcomes GROUP BY 1` |
| Alert win rate (5d) | `SELECT alert_type, AVG(is_correct_5d::int) FROM squeeze_alert_outcomes WHERE is_correct_5d IS NOT NULL GROUP BY 1` |
| Macro reaction delivery | `SELECT COUNT(*) FROM economic_events WHERE reaction_sent_at IS NOT NULL AND event_date > NOW() - INTERVAL '30 days'` |
| News hot-flag impact | Compare win rate of trades where hot_news_gate fired vs. didn't |

---

## Appendix A: Key File Locations

| Capability | Primary File(s) |
|------------|-----------------|
| Macro events | `services/event-intelligence/src/services/economic.py`, `macro_reaction.py` |
| Earnings | `services/event-intelligence/src/services/earnings.py` |
| Insider/Congress | `services/event-intelligence/src/services/insider.py`, `congress.py` |
| Institutional | `services/event-intelligence/src/services/institutional.py` |
| Catalyst scoring | `services/event-intelligence/src/services/catalyst.py` |
| News intelligence | `services/news-intelligence/src/services/` |
| Squeeze alerts | `services/market-data/src/services/scheduler.py` (lines 2639-3200) |
| Signal generation | `services/signal-engine/src/generators/signals.py` |
| Paper trading gates | `services/market-data/src/services/paper_trading_engine.py` |
| Alert delivery | `services/market-data/src/services/email_service.py` |

---

## Appendix B: Improvements Tracker Cross-Reference

These tracker items are directly relevant to this document's scope:

| ID | Title | Status |
|----|-------|--------|
| T249-MARKETMOVER-P0 | FRED release calendar | ✅ Done |
| T249-MARKETMOVER-P1 | Earnings reaction alerts | ✅ Done |
| T249-MARKETMOVER-P2 | Macro fast reaction | ✅ Done |
| T249-MARKETMOVER-P3 | Premarket brief | ✅ Done |
| T260-SHORTSQUEEZE-ALERT-GAMEPLAN | Squeeze watch + game plan | ✅ Done |
| T264-SQUEEZEALERT-PERFORMANCE | Outcome tracking | ✅ Done |
| T264-SHORTSQUEEZE-PREBREAKOUT | Compression alert | ✅ Done |
| T220-ECONOMIC-CALENDAR-SUPPRESSION | Macro blackout gate | ✅ Done |
| T220-SHORT-SQUEEZE-COMPOSITE | Squeeze score | ✅ Done |
| IF-04-CROSS-ASSET-SIGNALS | Bond/FX/Commodity | ✅ Done |
| news-sentiment | News sentiment layer | ✅ Done |
| earnings-surprise | Earnings surprise tracking | ✅ Done |

---

## Conclusion

StockAI's event intelligence infrastructure is **substantially more complete than initially assumed**. The user's ask for "real-time news, events, earnings, CPI, FOMO alerts" is ~80% already built. The genuine gaps are:

1. **FOMO Composite Score** — Easy win, just composition
2. **Theme/Sector Rotation** — Needs manual theme mapping
3. **Market Pulse Dashboard** — Composition of existing data
4. **Sector News Rollup** — Simple aggregation

None of these require new data sources or major architectural changes — they're composition and UI work on top of existing infrastructure.
