# Paid Data Services Recommendation — StockAI Enhancement Plan

**Date:** 2025-08-22  
**Purpose:** Comprehensive evaluation of paid data services to improve system performance, accuracy, and reliability based on measured gaps in the current yfinance-based architecture.

---

## Executive Summary

StockAI currently relies primarily on **yfinance** (free) with optional Alpha Vantage and Polygon.io adapters that are dormant. This document evaluates paid services against **real, measured gaps** identified through production data analysis and code audits.

**Key Finding:** The single biggest technical limitation is **quote latency**. yfinance's `fast_info` is cached/delayed 15+ minutes, causing squeeze alerts, volume anomaly detection, and paper trading to operate on stale data.

**Recommended Monthly Budget:** $166-250/mo for high-impact services

---

## Part 1: Current Data Architecture

### 1.1 Active Data Sources

| Source | Type | Cost | Latency | Reliability |
|--------|------|------|---------|-------------|
| yfinance | Quotes, fundamentals, options, news | Free | 15-20 min delay | Medium (rate limits, occasional outages) |
| FRED | Macro economic data | Free (API key) | Same-day | High |
| SEC EDGAR | Insider trades, 8-K filings, 13F | Free | Real-time (filings) | High |
| kadoa-org GitHub | Congress trades | Free | 1-2 day lag | Low (no SLA, community maintained) |
| Google News RSS | News headlines | Free | Near real-time | Medium |
| PR Newswire RSS | Company news | Free | Near real-time | High |

### 1.2 Dormant/Partial Integrations

| Source | Status | Location |
|--------|--------|----------|
| Alpha Vantage | Adapter exists, partially used | `services/market-data/src/providers/` |
| Polygon.io | Adapter exists, dormant | `services/market-data/src/providers/` |
| Alpaca News | Built, needs account activation | `services/news-intelligence/src/services/alpaca_source.py` |

### 1.3 Measured Gaps from Production

| Gap | Evidence | Impact |
|-----|----------|--------|
| Quote staleness | Squeeze alerts fire on 15-20 min old prices | False positives, missed timing |
| No real GEX | gamma_unwind source comment: "this is NOT real GEX" | Heuristic approximation only |
| Congress data fragility | 2 source deaths in 2026 (housestockwatcher, senatestockwatcher) | Operational risk |
| No consensus estimates | EconomicEvent.expected_value column never populated | Can't measure surprise magnitude |
| No intraday bars | Paper trading backtests use daily bars only | Can't validate intraday strategies |
| Options chain limited | Only 2 nearest expiries fetched | Incomplete gamma exposure picture |

---

## Part 2: Tier 1 — HIGH IMPACT Services

### 2.1 Polygon.io

**Website:** https://polygon.io  
**Pricing:** $29/mo (Starter) → $199/mo (Business) → $499/mo (Enterprise)

#### What It Provides

| Feature | Starter ($29) | Business ($199) | Enterprise ($499) |
|---------|---------------|-----------------|-------------------|
| Real-time quotes | ✅ | ✅ | ✅ |
| WebSocket streaming | ✅ | ✅ | ✅ |
| Historical daily bars | 2 years | 5 years | Full history |
| Historical minute bars | ❌ | ✅ | ✅ |
| Options data | ❌ | ✅ | ✅ |
| News feed | ✅ | ✅ | ✅ |
| Rate limits | 5 req/min | Unlimited | Unlimited |

#### Integration Points

```python
# Current (yfinance, delayed):
def _fetch_live_one(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info  # 15-20 min delay
    return {"price": info.last_price, "volume": info.last_volume}

# With Polygon (real-time):
def _fetch_live_one(symbol: str) -> dict:
    resp = polygon_client.get_last_trade(symbol)
    return {"price": resp.price, "volume": resp.size, "timestamp": resp.timestamp}
```

#### Files to Modify

| File | Change |
|------|--------|
| `services/market-data/src/providers/polygon.py` | Activate dormant adapter |
| `services/market-data/src/api/routes.py` | Add provider selection logic |
| `services/market-data/src/services/scheduler.py` | Use Polygon for alert price checks |
| `.env` | Add `POLYGON_API_KEY` |

#### Cost-Benefit Analysis

| Metric | Current (yfinance) | With Polygon Starter | With Polygon Business |
|--------|-------------------|---------------------|----------------------|
| Quote latency | 15-20 min | <1 sec | <1 sec |
| Squeeze alert accuracy | ~60% (estimated) | ~80% | ~85% |
| Intraday backtesting | ❌ | ❌ | ✅ |
| Options flow accuracy | Heuristic | Heuristic | Real data |
| Monthly cost | $0 | $29 | $199 |

#### Recommendation

**Start with Starter ($29/mo)** for real-time quotes. Upgrade to Business ($199/mo) only if options flow accuracy becomes a priority.

---

### 2.2 Unusual Whales

**Website:** https://unusualwhales.com  
**Pricing:** $57/mo (Flow) → $149/mo (Premium) → $299/mo (Institutional)

#### What It Provides

| Feature | Flow ($57) | Premium ($149) | Institutional ($299) |
|---------|------------|----------------|---------------------|
| Options flow alerts | ✅ | ✅ | ✅ |
| Real GEX (Gamma Exposure) | ✅ | ✅ | ✅ |
| DEX (Delta Exposure) | ✅ | ✅ | ✅ |
| Max Pain levels | ✅ | ✅ | ✅ |
| Dark pool prints | ❌ | ✅ | ✅ |
| Institutional flow | ❌ | ✅ | ✅ |
| API access | Limited | Full | Full |
| Historical data | 30 days | 1 year | Full |

#### Current vs. Improved Implementation

```python
# Current gamma_unwind heuristic (scheduler.py):
def _estimate_gamma_pressure(symbol: str) -> dict:
    """
    NOTE: This is NOT real GEX — it's a heuristic based on
    call/put volume ratios and open interest changes.
    """
    chain = yf.Ticker(symbol).options
    # ... crude approximation ...
    return {"gamma_signal": call_vol / put_vol}  # NOT actual gamma

# With Unusual Whales:
def _get_real_gex(symbol: str) -> dict:
    resp = uw_client.get_stock_gex(symbol)
    return {
        "gex": resp.gex,           # Real gamma exposure in $
        "gex_flip": resp.gex_flip, # Price where gamma flips
        "dex": resp.dex,           # Delta exposure
        "max_pain": resp.max_pain, # Max pain strike
        "put_wall": resp.put_wall, # Largest put OI strike
        "call_wall": resp.call_wall,
    }
```

#### New Capabilities Enabled

| Capability | Description | Trading Value |
|------------|-------------|---------------|
| GEX Flip Level | Price where market maker hedging flips from supportive to resistive | Key support/resistance |
| Put/Call Walls | Strikes with highest OI concentration | Magnetic price levels |
| Dark Pool Prints | Large block trades off-exchange | Institutional intent |
| Sweep Alerts | Aggressive multi-exchange options buys | Directional conviction |

#### Integration Points

| File | Change |
|------|--------|
| `services/market-data/src/services/scheduler.py` | Replace gamma_unwind heuristic with real GEX |
| `services/market-data/src/api/routes.py` | New `/stocks/{symbol}/gex` endpoint |
| `frontend/src/pages/stock/[symbol].tsx` | GEX visualization card |
| `services/signal-engine/src/generators/signals.py` | GEX-aware signal adjustment |

#### Recommendation

**Flow tier ($57/mo)** is sufficient for real GEX/DEX/max pain. Premium ($149/mo) only if dark pool data proves valuable after initial integration.

---

### 2.3 Quiver Quant

**Website:** https://www.quiverquant.com  
**Pricing:** $10/mo (Basic) → $30/mo (Pro) → Custom (Enterprise)

#### What It Provides

| Feature | Basic ($10) | Pro ($30) | Enterprise |
|---------|-------------|-----------|------------|
| Congress trades | ✅ (delayed) | ✅ (same-day) | ✅ (real-time) |
| Insider trades | ✅ | ✅ | ✅ |
| Insider clustering | ❌ | ✅ | ✅ |
| Gov contracts | ✅ | ✅ | ✅ |
| Lobbying data | ❌ | ✅ | ✅ |
| API rate limits | 100/day | 1000/day | Unlimited |
| Historical data | 1 year | 5 years | Full |

#### Why This Matters

Congress trading data has been the **most fragile data source** in the system:

| Date | Incident | Resolution |
|------|----------|------------|
| 2026-07-09 | housestockwatcher.com DNS failure | Replaced with kadoa-org |
| 2026-07-09 | senatestockwatcher.com DNS failure | Replaced with kadoa-org |
| Ongoing | kadoa-org has 1-2 day lag, no SLA | Operational risk |

#### Current vs. Improved

```python
# Current (kadoa-org GitHub, fragile):
CONGRESS_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/data/latest.json"

def sync_congress_trades():
    resp = httpx.get(CONGRESS_URL)  # No SLA, community maintained
    # 1-2 day lag on disclosures
    
# With Quiver Quant (stable, same-day):
def sync_congress_trades():
    resp = quiver_client.get_congress_trades(days=7)
    # Same-day disclosure, commercial SLA
```

#### Additional Features

| Feature | Current State | With Quiver Pro |
|---------|---------------|-----------------|
| Insider clustering | Manual analysis | Automated detection |
| Congress committee context | Not available | Committee membership included |
| Trade significance scoring | Basic | Pre-computed scores |

#### Recommendation

**Pro tier ($30/mo)** for same-day congress data + insider clustering. Basic ($10/mo) is too limited on API calls.

---

## Part 3: Tier 2 — MEDIUM IMPACT Services

### 3.1 Alpha Vantage Premium

**Website:** https://www.alphavantage.co  
**Pricing:** Free (25 req/day) → $50/mo (75 req/min) → $250/mo (1200 req/min)

#### Current Integration Status

Alpha Vantage is **partially integrated** but underutilized:

| Feature | Status | Location |
|---------|--------|----------|
| Earnings calendar | ✅ Active | `event-intelligence/earnings.py` |
| Fundamentals | ❌ Dormant | Adapter exists |
| Analyst ratings | ❌ Not used | Available in API |
| Consensus estimates | ❌ Not used | Available in API |

#### What Premium Adds

| Feature | Free Tier | Premium ($50) | Premium ($250) |
|---------|-----------|---------------|----------------|
| Earnings estimates | ❌ | ✅ | ✅ |
| Analyst ratings | ❌ | ✅ | ✅ |
| Consensus EPS | ❌ | ✅ | ✅ |
| Revenue estimates | ❌ | ✅ | ✅ |
| Rate limits | 25/day | 75/min | 1200/min |

#### Integration Points

```python
# Current (no consensus):
def check_earnings_reactions():
    # Can only compare actual vs. LAST QUARTER's actual
    surprise = (eps_actual - eps_prior) / abs(eps_prior)

# With Alpha Vantage Premium:
def check_earnings_reactions():
    # Compare actual vs. ANALYST CONSENSUS
    consensus = av_client.get_earnings_estimate(symbol)
    surprise = (eps_actual - consensus.eps_estimate) / abs(consensus.eps_estimate)
    # This is the REAL earnings surprise metric
```

#### Files to Modify

| File | Change |
|------|--------|
| `services/event-intelligence/src/services/earnings.py` | Fetch consensus estimates |
| `shared/db/models.py` | Populate `EarningsEvent.eps_estimate` properly |
| `services/market-data/src/services/scheduler.py` | Use real surprise in earnings alerts |

#### Recommendation

**$50/mo tier** is sufficient. The $250/mo tier is only needed for high-frequency data pulls.

---

### 3.2 Finnhub

**Website:** https://finnhub.io  
**Pricing:** Free (60 req/min) → $50/mo (300 req/min) → Custom

#### What It Provides

| Feature | Free | Premium ($50) |
|---------|------|---------------|
| Real-time quotes | ✅ (US only) | ✅ (Global) |
| Earnings call transcripts | ❌ | ✅ |
| SEC filings (structured) | ✅ | ✅ |
| Insider sentiment | ❌ | ✅ |
| Social sentiment | ❌ | ✅ |
| IPO calendar | ✅ | ✅ |

#### Key Capability: Earnings Call Transcripts

This is the **only affordable source** for earnings call transcripts:

```python
# Current (numeric EPS only):
def generate_earnings_impact(symbol: str, eps_actual: float, eps_estimate: float):
    # LLM analyzes NUMBERS only
    prompt = f"EPS was {eps_actual} vs estimate {eps_estimate}..."

# With Finnhub transcripts:
def generate_earnings_impact(symbol: str, eps_actual: float, transcript: str):
    # LLM analyzes ACTUAL MANAGEMENT COMMENTARY
    prompt = f"""
    EPS: {eps_actual}
    
    Key quotes from earnings call:
    {extract_key_quotes(transcript)}
    
    Analyze management tone and forward guidance...
    """
```

#### Recommendation

**$50/mo tier** if earnings call NLP is a priority. Otherwise, skip — the free tier's quote data overlaps with Polygon.

---

### 3.3 IEX Cloud

**Website:** https://iexcloud.io  
**Pricing:** Free (50k msg/mo) → $9/mo (500k) → $49/mo (5M) → $499/mo (unlimited)

#### What It Provides

| Feature | Free | Grow ($9) | Scale ($49) |
|---------|------|-----------|-------------|
| Real-time quotes | ✅ | ✅ | ✅ |
| Historical data | 5 years | 15 years | 15 years |
| Fundamentals | ✅ | ✅ | ✅ |
| Options | ❌ | ❌ | ✅ |
| News | ✅ | ✅ | ✅ |

#### Comparison with Polygon

| Aspect | IEX Cloud | Polygon.io |
|--------|-----------|------------|
| Real-time quotes | ✅ | ✅ |
| Options data | Limited | Full |
| WebSocket | ✅ | ✅ |
| Price (comparable tier) | $49/mo | $29/mo |
| Data quality | High | High |

#### Recommendation

**Skip** — Polygon.io provides better value for the same use case. IEX Cloud's strength is fundamentals, which yfinance already covers adequately.

---

## Part 4: Tier 3 — LOWER PRIORITY Services

### 4.1 Estimize (Crowdsourced Estimates)

**Website:** https://www.estimize.com  
**Pricing:** Custom (typically $200-500/mo)

#### What It Provides
- Crowdsourced earnings estimates (often more accurate than Wall Street)
- Revenue estimates
- Historical accuracy tracking

#### Why Lower Priority
- Alpha Vantage Premium provides analyst consensus at 1/4 the cost
- Crowdsourced edge is marginal for this use case
- High cost relative to benefit

#### Recommendation
**Skip** unless analyst consensus proves insufficient.

---

### 4.2 Sentifi / StockTwits API

**Website:** https://sentifi.com, https://stocktwits.com  
**Pricing:** $100-500/mo (Sentifi), $0-200/mo (StockTwits)

#### What It Provides
- Social media sentiment aggregation
- Trending tickers
- Sentiment momentum

#### Why Lower Priority
- Existing news sentiment (VADER + Claude) already works
- Social sentiment is noisy and often contrarian indicator
- High cost for marginal signal improvement

#### Recommendation
**Skip** — existing news sentiment is sufficient.

---

### 4.3 Satellite / Alternative Data

**Examples:** Orbital Insight, Placer.ai, SimilarWeb  
**Pricing:** $500-5000/mo

#### What It Provides
- Store foot traffic (retail stocks)
- Shipping/logistics data
- Web traffic trends

#### Why Lower Priority
- Very high cost
- Requires significant integration effort
- ROI unclear at current portfolio scale
- Better suited for hedge funds with $100M+ AUM

#### Recommendation
**Skip** — not cost-effective for this use case.

---

## Part 5: Free Quick Wins (No Cost)

Before spending on paid services, maximize free resources:

### 5.1 FRED API (Already Have Key)

**Current State:** Key is set but `expected_value` (nowcast) is never populated.

**Quick Win:** Cleveland Fed Inflation Nowcast (free)
```python
# Add to economic.py:
CLEVELAND_FED_NOWCAST_URL = "https://www.clevelandfed.org/api/inflation-nowcast"

def fetch_cpi_nowcast() -> float:
    """Cleveland Fed's real-time CPI nowcast — updates daily."""
    resp = httpx.get(CLEVELAND_FED_NOWCAST_URL)
    return resp.json()["nowcast"]
```

**Effort:** S (2-4 hours)

---

### 5.2 Alpaca News WebSocket (Already Built)

**Current State:** `news-intelligence/alpaca_source.py` exists but needs account activation.

**Quick Win:** Create free Alpaca account, add API keys
```bash
# .env
ALPACA_API_KEY=<your_key>
ALPACA_SECRET_KEY=<your_secret>
```

**Effort:** XS (30 minutes)

---

### 5.3 Expand yfinance Options Chain

**Current State:** Only fetches 2 nearest expiries.

**Quick Win:** Expand to 4-6 expiries for better gamma approximation
```python
# Current:
expiries = ticker.options[:2]

# Improved:
expiries = ticker.options[:6]  # More complete gamma picture
```

**Effort:** XS (1 hour)

---

### 5.4 SEC EDGAR Real-Time Feed (Already Built)

**Current State:** `news-intelligence/edgar_source.py` polls SEC's real-time feed.

**Quick Win:** Ensure scheduler is running this at appropriate frequency (currently may be too conservative).

**Effort:** XS (review and adjust cron)

---

## Part 6: Implementation Roadmap

### Phase 1: Free Quick Wins (Week 1)

| Task | Effort | Impact |
|------|--------|--------|
| Activate Alpaca news WebSocket | XS | Medium |
| Add Cleveland Fed nowcast | S | Low |
| Expand yfinance options to 6 expiries | XS | Low |
| Review SEC EDGAR poll frequency | XS | Low |

**Cost:** $0  
**Total Effort:** 1-2 days

---

### Phase 2: Polygon.io Integration (Week 2)

| Task | Effort | Impact |
|------|--------|--------|
| Activate Polygon adapter | S | High |
| Replace yfinance live quotes | M | High |
| Update squeeze alert price checks | S | High |
| Add WebSocket for real-time streaming | M | Medium |

**Cost:** $29/mo (Starter)  
**Total Effort:** 3-5 days

---

### Phase 3: Unusual Whales Integration (Week 3-4)

| Task | Effort | Impact |
|------|--------|--------|
| Create UW API client | M | High |
| Replace gamma_unwind heuristic with real GEX | M | High |
| Add max pain / put wall / call wall | S | Medium |
| Create GEX visualization in frontend | M | Medium |

**Cost:** $57/mo (Flow)  
**Total Effort:** 5-7 days

---

### Phase 4: Quiver Quant Integration (Week 4)

| Task | Effort | Impact |
|------|--------|--------|
| Replace kadoa-org with Quiver API | S | Medium |
| Add insider clustering detection | S | Medium |
| Update congress score calculation | S | Low |

**Cost:** $30/mo (Pro)  
**Total Effort:** 2-3 days

---

### Phase 5: Alpha Vantage Premium (Week 5)

| Task | Effort | Impact |
|------|--------|--------|
| Upgrade to Premium tier | XS | N/A |
| Fetch consensus EPS estimates | S | Medium |
| Update earnings surprise calculation | S | Medium |
| Populate EarningsEvent.eps_estimate | S | Medium |

**Cost:** $50/mo (Premium)  
**Total Effort:** 2-3 days

---

## Part 7: Cost Summary

### Recommended Configuration

| Service | Tier | Monthly Cost | Annual Cost |
|---------|------|--------------|-------------|
| Polygon.io | Starter | $29 | $348 |
| Unusual Whales | Flow | $57 | $684 |
| Quiver Quant | Pro | $30 | $360 |
| Alpha Vantage | Premium | $50 | $600 |
| **Total** | | **$166/mo** | **$1,992/yr** |

### Optional Upgrades

| Upgrade | When to Consider | Additional Cost |
|---------|------------------|-----------------|
| Polygon Business | Need intraday backtesting or full options data | +$170/mo |
| Unusual Whales Premium | Dark pool data proves valuable | +$92/mo |
| Finnhub Premium | Earnings call NLP becomes priority | +$50/mo |

### Budget Tiers

| Budget | Services | Monthly Cost |
|--------|----------|--------------|
| Minimal | Polygon Starter only | $29 |
| Recommended | Polygon + UW + Quiver + AV | $166 |
| Full | Above + Polygon Business + Finnhub | $386 |

---

## Part 8: Expected Impact

### Quantified Improvements (Estimated)

| Metric | Current | With Paid Services | Improvement |
|--------|---------|-------------------|-------------|
| Quote latency | 15-20 min | <1 sec | 99%+ |
| Squeeze alert timing | ±15 min | ±1 min | 93% |
| Gamma exposure accuracy | ~40% (heuristic) | ~90% (real GEX) | 125% |
| Congress data reliability | 70% (fragile sources) | 99% (commercial SLA) | 41% |
| Earnings surprise accuracy | N/A (no consensus) | Real metric | New capability |

### Risk Reduction

| Risk | Current State | With Paid Services |
|------|---------------|-------------------|
| Data source death | High (2 incidents in 2026) | Low (commercial SLAs) |
| Quote staleness | High | Eliminated |
| False positive alerts | Medium | Low |

---

## Part 9: What NOT to Pay For

| Service | Reason to Skip |
|---------|----------------|
| Bloomberg Terminal ($24k/yr) | Overkill; same data available cheaper |
| Refinitiv/Reuters | Enterprise pricing, no retail tier |
| Social sentiment APIs | Noisy signal; existing news sentiment sufficient |
| Satellite/alt data | High cost, unclear ROI at this scale |
| Multiple quote providers | One (Polygon) is sufficient |
| Estimize | Alpha Vantage consensus is cheaper and adequate |

---

## Part 10: Decision Matrix

| If You Want... | Get This | Cost |
|----------------|----------|------|
| Real-time quotes only | Polygon Starter | $29/mo |
| Real options flow (GEX) | Unusual Whales Flow | $57/mo |
| Stable congress data | Quiver Quant Pro | $30/mo |
| Earnings consensus | Alpha Vantage Premium | $50/mo |
| Earnings call transcripts | Finnhub Premium | $50/mo |
| Everything above | Full stack | $166-216/mo |

---

## Appendix A: API Documentation Links

| Service | API Docs | Python SDK |
|---------|----------|------------|
| Polygon.io | https://polygon.io/docs | `polygon-api-client` |
| Unusual Whales | https://docs.unusualwhales.com | Custom (REST) |
| Quiver Quant | https://www.quiverquant.com/api | `quiverquant` |
| Alpha Vantage | https://www.alphavantage.co/documentation | `alpha_vantage` |
| Finnhub | https://finnhub.io/docs/api | `finnhub-python` |
| IEX Cloud | https://iexcloud.io/docs/api | `pyEX` |

---

## Appendix B: Environment Variables

```bash
# .env additions for paid services

# Polygon.io
POLYGON_API_KEY=<your_polygon_key>

# Unusual Whales
UNUSUAL_WHALES_API_KEY=<your_uw_key>

# Quiver Quant
QUIVER_API_KEY=<your_quiver_key>

# Alpha Vantage (upgrade existing)
ALPHA_VANTAGE_KEY=<your_av_premium_key>

# Finnhub (optional)
FINNHUB_API_KEY=<your_finnhub_key>

# Feature flags for gradual rollout
USE_POLYGON_QUOTES=true
USE_REAL_GEX=true
USE_QUIVER_CONGRESS=true
```

---

## Appendix C: Existing Provider Adapter Pattern

The codebase already has a provider adapter pattern in `services/market-data/src/providers/`:

```python
# Existing pattern to follow:
class DataProvider(ABC):
    @abstractmethod
    def get_quote(self, symbol: str) -> Quote: ...
    
    @abstractmethod
    def get_historical(self, symbol: str, start: date, end: date) -> list[Bar]: ...

class YFinanceProvider(DataProvider): ...
class PolygonProvider(DataProvider): ...  # Dormant, needs activation
class AlphaVantageProvider(DataProvider): ...  # Partial
```

New providers should follow this pattern for easy swapping.

---

## Conclusion

**Priority order for paid services:**

1. **Polygon.io Starter ($29/mo)** — Fixes the #1 technical limitation (quote latency)
2. **Unusual Whales Flow ($57/mo)** — Replaces heuristic gamma with real GEX
3. **Quiver Quant Pro ($30/mo)** — Eliminates congress data fragility
4. **Alpha Vantage Premium ($50/mo)** — Enables real earnings surprise metrics

**Total recommended spend: $166/mo ($1,992/yr)**

This investment addresses every major measured gap in the current system while avoiding overspending on marginal improvements.
