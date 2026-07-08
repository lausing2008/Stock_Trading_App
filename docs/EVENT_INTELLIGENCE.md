# Event Intelligence Platform — Architecture & Implementation Guide

## Overview

The Event Intelligence Platform adds a new bounded context to the AI Stock Trading Platform: **event-driven intelligence**. Where the existing platform answers "what is the signal right now?", this platform answers "why now, and what's coming?". It surfaces catalysts, institutional activity, and macro events so investors have the full context behind every signal.

### Service Boundary

New service: `event-intelligence` (port 8010, container `stockai-event-intelligence-1`)

The service is a standalone FastAPI app that:
- Ingests data from free public APIs on a scheduled basis
- Scores each tracked stock across 5 dimensions
- Exposes REST endpoints consumed by the api-gateway proxy and the signal-engine
- Does not own stock price data (reads stocks table from shared DB)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                       │
│  /intelligence  ·  stock detail catalyst panel  ·  admin page   │
└─────────────────────────┬───────────────────────────────────────┘
                          │ HTTP
┌─────────────────────────▼───────────────────────────────────────┐
│                   api-gateway (port 8000)                        │
│  /events/* → event-intelligence:8010                            │
└──────────────┬────────────────────────────────┬─────────────────┘
               │                                │ catalyst score
┌──────────────▼──────────────┐   ┌─────────────▼───────────────┐
│  event-intelligence:8010     │   │   signal-engine:8005         │
│                              │   │  adds catalyst_score         │
│  ┌────────────────────────┐  │   │  to signal reasons dict      │
│  │  Economic Calendar     │  │   └─────────────────────────────┘
│  │  FRED API / hardcoded  │  │
│  ├────────────────────────┤  │
│  │  Earnings Intelligence │  │
│  │  yfinance              │  │
│  ├────────────────────────┤  │
│  │  Insider Trading       │  │
│  │  SEC EDGAR Form 4      │  │
│  ├────────────────────────┤  │
│  │  Congress Trading      │  │
│  │  House/Senate Watcher  │  │
│  ├────────────────────────┤  │
│  │  Institutional (13F)   │  │
│  │  SEC EDGAR 13F-HR      │  │
│  ├────────────────────────┤  │
│  │  Political Intel       │  │
│  │  USASpending.gov       │  │
│  ├────────────────────────┤  │
│  │  Catalyst + Risk +     │  │
│  │  Composite Scoring     │  │
│  └────────────────────────┘  │
└──────────────────────────────┘
          │
┌─────────▼──────────┐
│  PostgreSQL (shared)│
│  economic_events    │
│  earnings_events    │
│  insider_transactions│
│  congress_trades    │
│  institutional_holdings│
│  institutional_transactions│
│  political_events   │
│  catalyst_scores    │
└─────────────────────┘
```

---

## Database Schema

### `economic_events`
Macro events from FRED API, Federal Reserve calendar, and HK monetary authority.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| event_type | VARCHAR(64) | `fomc_meeting`, `cpi`, `gdp`, `nfp`, `ppi`, `retail_sales`, `hk_gdp`, etc. |
| title | VARCHAR(255) | Display name |
| country | VARCHAR(8) | `US`, `HK` |
| event_date | TIMESTAMP | Scheduled release time (UTC) |
| actual_value | FLOAT | Populated after release |
| expected_value | FLOAT | Consensus estimate |
| previous_value | FLOAT | Prior period reading |
| importance | VARCHAR(16) | `high`, `medium`, `low` |
| source | VARCHAR(64) | `fred`, `fed_calendar`, `manual` |
| created_at | TIMESTAMP | |

### `earnings_events`
Historical and upcoming earnings per stock.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| stock_id | INT FK stocks | |
| report_date | DATE | |
| period | VARCHAR(16) | `Q1 2025` |
| fiscal_year | INT | |
| fiscal_quarter | INT | 1-4 |
| eps_estimate | FLOAT | Analyst consensus |
| eps_actual | FLOAT | Reported EPS |
| revenue_estimate | FLOAT | |
| revenue_actual | FLOAT | |
| surprise_pct | FLOAT | `(actual-estimate)/|estimate|` × 100 |
| revenue_surprise_pct | FLOAT | |
| earnings_strength_score | FLOAT | 0-100 derived score |
| post_earnings_return_1d | FLOAT | % price change day after |
| post_earnings_return_5d | FLOAT | |
| fetched_at | TIMESTAMP | |

UNIQUE INDEX: `uq_earnings_stock_period` on `(stock_id, fiscal_year, fiscal_quarter)`

### `insider_transactions`
SEC Form 4 filings — purchases and sales by corporate insiders.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| stock_id | INT FK stocks | |
| insider_name | VARCHAR(255) | |
| insider_role | VARCHAR(128) | `CEO`, `CFO`, `Director`, `10% Owner`, etc. |
| transaction_type | VARCHAR(32) | `purchase`, `sale`, `gift`, `exercise` |
| shares | BIGINT | |
| price_per_share | FLOAT | |
| total_value | FLOAT | shares × price |
| transaction_date | DATE | |
| filing_date | DATE | |
| accession_number | VARCHAR(32) | SEC filing ID (unique) |
| created_at | TIMESTAMP | |

UNIQUE INDEX: `uq_insider_accession` on `accession_number`

### `congress_trades`
STOCK Act disclosures from House and Senate members.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| politician_name | VARCHAR(255) | |
| party | VARCHAR(32) | `Republican`, `Democrat`, `Independent` |
| chamber | VARCHAR(16) | `House`, `Senate` |
| state | VARCHAR(8) | State abbreviation |
| ticker | VARCHAR(16) | Stock ticker |
| stock_id | INT FK stocks | NULL if ticker not in our DB |
| transaction_type | VARCHAR(32) | `purchase`, `sale`, `exchange` |
| amount_range | VARCHAR(64) | `$1,001 - $15,000` (reported as range) |
| amount_min | FLOAT | Lower bound parsed from range |
| amount_max | FLOAT | Upper bound parsed from range |
| trade_date | DATE | |
| disclosure_date | DATE | |
| source | VARCHAR(32) | `house_clerk`, `senate` |
| created_at | TIMESTAMP | |

UNIQUE INDEX: `uq_congress_trade` on `(politician_name, ticker, trade_date, transaction_type)`

### `institutional_holdings`
Quarterly 13F snapshot — what major funds hold.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| fund_name | VARCHAR(255) | `Berkshire Hathaway`, `ARK Invest`, etc. |
| fund_cik | VARCHAR(32) | SEC CIK number |
| stock_id | INT FK stocks | |
| period_date | DATE | Quarter end date |
| shares | BIGINT | Shares held |
| value_usd | FLOAT | Market value at period end |
| created_at | TIMESTAMP | |

UNIQUE INDEX: `uq_inst_holding` on `(fund_cik, stock_id, period_date)`

### `institutional_transactions`
Changes between quarters (derived from institutional_holdings diffs).

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| fund_name | VARCHAR(255) | |
| fund_cik | VARCHAR(32) | |
| stock_id | INT FK stocks | |
| period_date | DATE | Quarter of change |
| change_type | VARCHAR(32) | `new`, `increased`, `reduced`, `closed` |
| shares_change | BIGINT | Positive = bought, negative = sold |
| value_change_usd | FLOAT | |
| created_at | TIMESTAMP | |

### `political_events`
Government contract awards and regulatory announcements.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| stock_id | INT FK stocks | NULL if not stock-specific |
| event_type | VARCHAR(64) | `contract_award`, `regulatory`, `executive_order` |
| title | VARCHAR(512) | |
| description | TEXT | |
| amount_usd | FLOAT | Contract value (if applicable) |
| agency | VARCHAR(255) | Awarding agency |
| event_date | DATE | |
| impact | VARCHAR(16) | `positive`, `negative`, `neutral` |
| source | VARCHAR(64) | `usaspending`, `sec`, `manual` |
| source_url | TEXT | |
| created_at | TIMESTAMP | |

### `catalyst_scores`
Pre-computed per-stock scores (refreshed every 6 hours).

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| stock_id | INT FK stocks | |
| catalyst_score | FLOAT | 0-100 weighted composite |
| earnings_score | FLOAT | 0-100 |
| insider_score | FLOAT | -100 to 100 (negative = net selling) |
| congress_score | FLOAT | 0-100 |
| institutional_score | FLOAT | 0-100 |
| economic_score | FLOAT | 0-100 |
| risk_score | FLOAT | 0-100 (higher = more risk) |
| composite_score | FLOAT | AI Composite Score 0-100 |
| earnings_days_out | INT | Days until next earnings (NULL if unknown) |
| last_insider_days | INT | Days since last insider purchase |
| last_congress_days | INT | Days since last congress purchase |
| computed_at | TIMESTAMP | |

UNIQUE INDEX: `uq_catalyst_stock` on `stock_id`

---

## Scoring Algorithms

### Earnings Score (0-100)
```
base = 0
if earnings_days_out <= 3:  base += 50
elif earnings_days_out <= 7:  base += 35
elif earnings_days_out <= 14: base += 20
elif earnings_days_out <= 30: base += 10

if beat_rate_90d > 0.80:  base += 20
elif beat_rate_90d > 0.65: base += 10

if avg_post_return_1d > 0.05: base += 15
elif avg_post_return_1d > 0.02: base += 7

if surprise_pct_last > 10: base += 15
```

### Insider Score (-100 to 100)
```
score = 0
for txn in last_90_days:
    weight = {'CEO': 30, 'CFO': 20, 'President': 20, 'Director': 10, '10% Owner': 15}.get(role, 8)
    if type == 'purchase': score += weight
    elif type == 'sale':   score -= weight * 0.5  # sells are less bearish than buys are bullish

if purchase_count > 3: score *= 1.25  # cluster bonus
score = clamp(score, -100, 100)
```

### Congress Score (0-100)
```
score = 0
for trade in last_90_days:
    if type == 'purchase': score += 12
    elif type == 'sale':   score -= 5

if net_purchases > 5: score += 20  # strong institutional interest
score = clamp(score, 0, 100)
```

### Catalyst Score (0-100) — composite
```
catalyst_score = (
  0.35 × max(insider_score, 0) +   # only positive insider activity
  0.30 × earnings_score +
  0.25 × congress_score +
  0.10 × economic_score
)
```

### Risk Score (0-100) — higher = more risky
```
risk_score = (
  0.30 × earnings_proximity_risk +  # high if earnings < 3 days out
  0.25 × volatility_risk +          # ATR/price vs 52wk avg ATR
  0.20 × insider_sell_risk +        # heavy insider selling
  0.15 × congress_sell_risk +
  0.10 × economic_event_proximity   # FOMC/CPI within 2 days
)
```

### AI Composite Score (0-100)
```
composite = (
  0.25 × technical_score +          # signal confidence (from signal-engine)
  0.20 × catalyst_score +
  0.20 × earnings_score +
  0.15 × max(insider_score, 0) +    # only count positive insider activity
  0.10 × congress_score +
  0.10 × institutional_score
) × (1 - 0.05 × risk_penalty)       # risk dampens composite
```
Where `technical_score` = signal bullish_probability × 100 (from signal-engine), and `risk_penalty` = risk_score / 100.

---

## API Endpoints

### Event Intelligence Service (port 8010, proxied via /events/)

```
GET  /health                           # Service health
GET  /events/economic?days=14&market=US # Upcoming economic events
GET  /events/earnings?symbol=AAPL      # Earnings history + upcoming for symbol
GET  /events/earnings/calendar?days=14 # All upcoming earnings in watchlist
GET  /events/insider/{symbol}?days=90  # Insider transactions for symbol
GET  /events/insider/leaderboard?days=30 # Top stocks by insider buying
GET  /events/congress/{symbol}?days=90 # Congress trades for symbol
GET  /events/congress/leaderboard?days=90 # Top stocks by congress buying
GET  /events/institutional/{symbol}    # Latest 13F positions for symbol
GET  /events/institutional/leaderboard # Top stocks by smart money accumulation
GET  /events/political?days=30        # Recent political events
GET  /catalyst/{symbol}               # Catalyst + risk + composite scores
GET  /catalyst/leaderboard?limit=20   # Top stocks by catalyst score
GET  /catalyst/risk-leaderboard?limit=20 # Top stocks by risk score

POST /events/sync/economic            # Trigger economic calendar sync (auth required)
POST /events/sync/earnings            # Trigger earnings sync for all stocks
POST /events/sync/insider             # Trigger insider sync for all stocks
POST /events/sync/congress            # Trigger congress trades sync
POST /events/sync/institutional       # Trigger 13F sync
POST /catalyst/recompute              # Recompute all catalyst scores (auth required)
```

---

## Data Sources

| Feature | Source | Cost | Rate Limit | Update Frequency |
|---------|--------|------|-----------|-----------------|
| Economic Calendar | FRED API | Free | 120/min | Daily |
| FOMC Dates | Fed public calendar (hardcoded) | Free | N/A | Annual |
| HK Economic Events | Manual / HKMA | Free | N/A | Quarterly |
| Earnings Dates | yfinance | Free | Soft limit | Daily |
| EPS Data | yfinance | Free | Soft limit | After earnings |
| Insider Transactions | SEC EDGAR Form 4 | Free | 10/sec | Daily |
| Congress Trades | House/Senate Stock Watcher | Free | S3 public | Daily |
| Institutional 13F | SEC EDGAR 13F-HR | Free | 10/sec | Quarterly |
| Political / Contracts | USASpending.gov API | Free | 1000/hr | Daily |
| (Future) Financial Data | Financial Modeling Prep | $15/mo | 250/day free | Real-time |
| (Future) Economic Data | Trading Economics | Paid | — | Real-time |

**Environment variables required:**
```
FRED_API_KEY=         # https://fred.stlouisfed.org/docs/api/api_key.html (free)
FMP_API_KEY=          # https://site.financialmodelingprep.com (free tier)
EVENT_INTELLIGENCE_URL=http://event-intelligence:8010
```

---

## Scheduler Jobs

All jobs run inside the event-intelligence service scheduler:

| Job | Frequency | Description |
|-----|-----------|-------------|
| `sync_economic` | Daily 06:00 UTC | Sync FRED economic releases |
| `sync_earnings` | Daily 06:30 UTC | yfinance earnings for all stocks |
| `sync_insider` | Daily 07:00 UTC | SEC Form 4 for all tracked tickers |
| `sync_congress` | Daily 07:30 UTC | House/Senate stock watcher JSON |
| `sync_institutional` | Weekly Sunday 08:00 UTC | SEC 13F filings |
| `sync_political` | Daily 08:00 UTC | USASpending.gov contract awards |
| `recompute_catalyst` | Every 6h | Recompute all catalyst_scores |

---

## Phased Implementation

### Phase 1 — Free Data Sources (Implemented 2026-06-21)
- [x] Service skeleton (FastAPI, Dockerfile, requirements.txt)
- [x] DB tables: economic_events, earnings_events, insider_transactions, congress_trades, catalyst_scores
- [x] Economic Calendar: FRED API + hardcoded FOMC/HKMA dates
- [x] Earnings Intelligence: yfinance earnings history + upcoming calendar
- [x] Insider Trading: SEC EDGAR Form 4 full-text search API
- [x] Congress Trading: House/Senate Stock Watcher public S3 JSON
- [x] Catalyst Scoring Engine: weighted formula above
- [x] API endpoints (all GET + admin POST sync)
- [x] Scheduler (daily sync jobs)
- [x] Signal engine reads catalyst_score via HTTP, adds to signal reasons
- [x] Frontend `/intelligence` page: economic calendar, earnings calendar, congress/insider leaderboards, catalyst leaderboard

### Phase 2 — Institutional + Risk + Composite (Implemented 2026-06-21)
- [x] DB tables: institutional_holdings, institutional_transactions
- [x] Institutional Intelligence: SEC EDGAR 13F ingestion (quarterly)
- [x] Risk Engine: ATR volatility + earnings proximity + insider selling pressure
- [x] AI Composite Score: technical + catalyst + institutional + risk
- [x] Catalyst leaderboard with composite scores
- [x] Signal alert engine: new alert types for high catalyst score, congress purchases

### Phase 3 — Political Intelligence (Implemented 2026-06-21)
- [x] DB table: political_events
- [x] USASpending.gov contract awards (free API, filtered to tracked tickers)
- [x] Political Impact Score per sector
- [x] Political events tab on /intelligence page

### Deferred (Requires Paid APIs)
- [ ] Trading Economics integration ($299/mo) — real-time economic data
- [ ] Financial Modeling Prep premium ($50/mo) — real-time institutional data
- [ ] AI Copilot Q&A ("Why is NVDA ranked #1?") — needs RAG over all intelligence data

---

## Integration with Existing Services

### Signal Engine Integration
`_bulk_persist()` in signal-engine routes.py calls `GET /catalyst/{symbol}` (with service token) and appends to signal reasons:
```json
{
  "catalyst_score": 72,
  "earnings_days_out": 8,
  "last_congress_buy_days": 12,
  "last_insider_buy_days": 34,
  "risk_score": 41
}
```

### Research Engine Integration
Research reports already include catalyst context via the existing AI prompt. After Phase 1, the research engine can call `GET /catalyst/{symbol}` and include insider/congress data in the AI prompt.

### Frontend Integration
- Stock detail page (`/stock/[symbol]`): Catalyst panel below signal tabs
- Signal Filter page: new "Catalyst" column in the stock grid
- Admin Health page: event-intelligence service shown in connectivity grid
- New page: `/intelligence` — full Investment Intelligence Dashboard

---

## Docker Compose

Service added to `docker/docker-compose.yml`:
```yaml
event-intelligence:
  build:
    context: ..
    dockerfile: services/event-intelligence/Dockerfile
  container_name: stockai-event-intelligence-1
  restart: unless-stopped
  env_file: ../.env
  depends_on: [postgres, redis]
  networks: [stockai-net]
  ports: ["8010:8010"]
```

API Gateway proxy route: `"events": event_intelligence_url` and `"catalyst": event_intelligence_url`

---

## Security Notes

- All sync endpoints require `Depends(get_current_username)` — scheduler uses service token (same pattern as signal-engine)
- FRED API key stored in `.env` only, never committed
- SEC EDGAR has no auth requirement but rate limit of 10 req/sec; use `asyncio.sleep(0.1)` between requests
- House/Senate watcher data is public S3 — no auth needed
