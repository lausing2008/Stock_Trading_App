# Planning Stage Research Intelligence Engine

Full technical and operational reference for the Research Engine feature.

> **Quick start:** On the Trade Board, move any stock card to **Planning** and click the green **Research** button. Or navigate to `/research` and enter any symbol.

---

## Purpose

The Research Engine answers the ten questions every trader needs to answer before entering a position:

1. Should I buy this stock?
2. Why should I buy it?
3. Why should I avoid it?
4. What are the risks?
5. What conditions need to improve?
6. What is the ideal entry zone?
7. What is the stop loss?
8. What are the profit targets?
9. What would invalidate the trade?
10. How much should I risk?

It combines quantitative scoring (computed in Python from live market data) with qualitative AI analysis (Claude or DeepSeek) into a single, structured research report — comparable to a combination of TradingView, Finviz, Seeking Alpha, Morningstar, and institutional equity research.

---

## Architecture

### Service: `research-engine`

| Property | Value |
|---|---|
| Language | Python 3.11 / FastAPI |
| Port | 8008 (internal) |
| Docker service | `research-engine` |
| Dockerfile | `services/research-engine/Dockerfile` |

The service aggregates data from all other services using parallel `httpx` calls, computes quantitative scores, calls Claude (or DeepSeek) for qualitative analysis, and assembles the final JSON report. Reports are cached in-memory for 24 hours per symbol.

### Data flow

```
Browser
  │
  │  POST /api/research/{symbol}  {provider, model, api_key, ...}
  ▼
Nginx (lausing.com:80)
  │  proxy_read_timeout 180s for /api/research routes
  ▼
Next.js frontend (port 3000)
  │  proxyTimeout: 120000ms   next.config.js rewrite: /api/* → api-gateway:8000
  ▼
API Gateway (port 8000)
  │  proxies /research/* → research-engine:8008
  ▼
Research Engine — all seven data requests run concurrently (asyncio.gather):
  ├─── GET market-data:8001/stocks/{symbol}               (stock name, sector)
  ├─── GET market-data:8001/stocks/{symbol}/fundamentals  (financials, analyst data)
  ├─── GET market-data:8001/stocks/{symbol}/prices        (260 daily bars)
  ├─── GET market-data:8001/stocks/latest_prices?symbols={symbol}  ← live price
  ├─── GET technical-analysis:8002/ta/{symbol}/indicators (SMA, RSI, MACD, ...)
  ├─── GET technical-analysis:8002/ta/{symbol}/levels     (support/resistance)
  ├─── GET signal-engine:8005/signals/{symbol}            (AI signal)
  └─── GET ranking-engine:8004/rankings/{symbol}          (K-Score)
  │
  │  If symbol not in DB → yfinance fallback (see below)
  │
  ├── _score_technical(stock, prices, indicators, levels, live_price)  → 0-100
  ├── _score_fundamental(fund)                                          → 0-100
  │
  └─── POST api.anthropic.com/v1/messages  (or DeepSeek)
         ↑ receives all quantitative data as context
         ↓ returns company + industry + economic analysis + AI verdict JSON
         (skipped if api_key is empty — fallback neutral response used instead)
  │
  ├── _build_checklist()         → 27 PASS/WARNING/FAIL items
  ├── _position_size()           → shares, dollar risk, position size
  ├── assemble_report()          → single JSON object
  └── _cache[symbol] = (report, now())  → 24-hour TTL
  │
  ◄── ResearchReport JSON (200 OK)
```

All data requests run concurrently. Wall-clock time is dominated by the Claude API call (typically 10–30 s). The Nginx proxy timeout is set to 180 s for `/api/research` routes; Next.js proxy timeout is 120 s; the frontend fetch timeout is 90 s.

### yfinance fallback for untracked symbols

When `GET /stocks/{symbol}` returns 404 (symbol not in the database), the research engine falls back to fetching data directly from yfinance:

```
_yf_sync_fetch(sym) → runs in asyncio executor (non-blocking)
  ├── yf.Ticker(sym).info          → name, sector
  ├── yf.Ticker(sym).history(period="1y", interval="1d")  → OHLCV DataFrame
  │   └── _compute_yf_indicators(hist)  → SMA50/200, RSI14, MACD/signal/hist
  └── yf.Ticker(sym).fast_info.last_price  → live price
```

This means the Research Engine works for **any valid stock ticker worldwide**, not just symbols tracked in the database.

### Live price

The report always uses the **real-time price** from `GET /stocks/latest_prices?symbols={symbol}` (yfinance `fast_info`), not the last DB bar. This ensures:
- `current_price` in the report header is accurate
- Technical scoring (price vs SMA50/SMA200 comparisons) uses the actual current price
- Entry zone labels show the real price
- Stop loss and target calculations use the real price

### Caching

Reports are cached in an in-memory Python dict keyed by symbol (uppercase). TTL is 24 hours. The cache lives inside the `research-engine` container — it is **not** shared across container restarts or replicas.

To force regeneration:
- Click **Clear & Regenerate Report** on the report page (calls `DELETE /research/{symbol}`)
- Or restart the `research-engine` container

---

## Report structure

The API returns a single JSON object. Key top-level fields:

```jsonc
{
  "symbol": "AAPL",
  "company_name": "Apple Inc.",
  "generated_at": "2026-05-30T12:34:56Z",
  "current_price": 195.40,        // live price from yfinance fast_info
  "market_cap": 3000000000000,
  "sector": "Technology",
  "recommendation": "BUY",        // STRONG BUY | BUY | WATCH | AVOID | SELL
  "overall_score": 82,            // 0-100, weighted composite
  "confidence": 75,               // 0-100, Claude's confidence

  "scores": {
    "technical":   78,            // Python-computed
    "fundamental": 85,            // Python-computed
    "company":     82,            // Claude-assigned
    "industry":    75,            // Claude-assigned
    "economic":    70             // Claude-assigned
  },

  "executive_summary": { ... },   // bullish_factors, bearish_factors, key_risks, key_opportunities
  "technical":    { ... },        // full technical breakdown + entry_planning
  "fundamental":  { ... },        // full fundamental breakdown
  "company":      { ... },        // Claude-generated company analysis
  "industry_analysis": { ... },   // Claude-generated industry analysis
  "economic":     { ... },        // Claude-generated economic analysis
  "checklist":    { ... },        // 4 layers of PASS/WARNING/FAIL items
  "entry_planning":   { ... },    // entry zones, stop loss, profit targets, risk/reward
  "position_sizing":  { ... },    // dollar risk, shares, position size
  "trade_invalidation": [ ... ],  // list of invalidation conditions
  "ai_verdict":   { ... },        // can_buy_today, why, risks, catalysts, final_recommendation

  "signal":   { "signal": "BUY", "confidence": 72, "horizon": "SWING" },
  "ranking":  { "score": 78.5, ... },
  "analyst":  { "target_price": 220, "recommendation": "buy", "num_analysts": 42 },
  "beta": 1.24,
  "week_52_high": 220.20,
  "week_52_low":  163.70,
  "short_float_pct": 0.6,
  "next_earnings":   "2026-07-28",
  "days_to_earnings": 59
}
```

---

## Scoring details

### Overall score formula

```
overall = technical × 0.25
        + fundamental × 0.30
        + company × 0.15
        + industry × 0.15
        + economic × 0.15
```

### Technical score (Python)

Uses the **live price** (from `latest_prices`) — not the last DB bar — to correctly evaluate price vs SMA relationships.

Starts at 50, adjusted by:

| Signal | Δ Score |
|---|---|
| Price above 200-day SMA | +15 |
| Price below 200-day SMA | −10 |
| Price above 50-day SMA | +10 |
| Price below 50-day SMA | −7 |
| Golden Cross (50 crossed above 200) | +10 |
| Death Cross (50 crossed below 200) | −10 |
| Above golden cross (no recent event) | +5 |
| RSI 40–60 (Healthy) | +5 |
| RSI 60–70 (Strong) | +8 |
| RSI 30–40 (Weak) | −5 |
| RSI > 70 (Overbought) | −8 |
| MACD bullish crossover | +10 |
| MACD bearish crossover | −10 |
| MACD line > signal (sustained) | +3 |
| MACD line < signal (sustained) | −3 |
| Histogram green and growing | +2 |
| Histogram red and growing | −2 |
| RVOL ≥ 1.5x | +5 |
| RVOL 1.0–1.5x | +2 |
| RVOL < 1.0x | −3 |
| Price within 3% of support | +3 |
| Price 3–8% above support | +1 |

Clamped to [0, 100].

**Trend verdict thresholds:**
≥ 80 → Strong Bullish · ≥ 65 → Bullish · ≥ 50 → Neutral · ≥ 35 → Bearish · < 35 → Strong Bearish

### Fundamental score (Python)

Starts at 50, adjusted by:

| Metric | Δ Score |
|---|---|
| Revenue growth ≥ 20% | +10 |
| Revenue growth 10–20% | +5 |
| Revenue growth < 0% | −5 |
| EPS growth ≥ 25% | +10 |
| EPS growth 10–25% | +5 |
| EPS growth < 0% | −7 |
| Gross margin > 40% | +5 |
| Gross margin < 20% | −3 |
| Operating margin > 20% | +5 |
| Operating margin < 5% | −3 |
| D/E ratio < 0.5 | +5 |
| D/E ratio > 2.0 | −5 |
| FCF positive, margin ≥ 20% | +10 |
| FCF positive (any margin) | +5 |
| FCF negative | −5 |
| P/E < 15 | +8 |
| P/E 15–25 | +3 |
| P/E > 40 | −5 |
| ROE ≥ 20% | +8 |
| ROE 12–20% | +4 |
| ROE < 6% | −4 |

### Company / Industry / Economic scores (Claude)

Claude assigns integer scores (0–100) for each of these three dimensions as part of its structured JSON response. The prompt provides all quantitative data and asks Claude to evaluate based on its knowledge of the company.

If no API key is provided or the Claude call fails, scores default to 50 and `_fallback_ai()` provides neutral placeholder text for all narrative sections. The report is still returned and all computed sections (technical, fundamental, entry planning, checklist) are fully populated.

### Recommendation thresholds

| Overall Score | Recommendation |
|---|---|
| 90–100 | STRONG BUY |
| 80–89 | BUY |
| 65–79 | WATCH |
| 50–64 | AVOID |
| 0–49 | SELL |

If the score is within ±10 of the 65 threshold (WATCH/AVOID boundary), Claude's `final_recommendation` is used as a tiebreaker.

---

## Claude prompt

The engine makes **one Claude API call** per report. The request:

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 4096,
  "temperature": 0.2,
  "system": "You are a senior equity research analyst with CFA expertise...",
  "messages": [{ "role": "user", "content": "Analyze {symbol}..." }]
}
```

The user message includes:
- Stock metadata (name, **live price**, market cap, sector)
- Computed technical summary (trend, RSI, MACD, RVOL, support/resistance)
- Computed fundamental summary (growth rates, margins, D/E, FCF, valuation, ROE, beta, short float, insider data, institutional %)

Claude returns a structured JSON object with company, industry, and economic analysis plus the AI verdict. Temperature 0.2 for consistency.

If the key is empty or invalid, the engine skips the API call entirely and uses `_fallback_ai()` — the report is still returned with neutral AI scores.

---

## Entry planning

Stop loss and targets are calculated from live market data:

```
stop_loss = nearest_support − 0.5 × ATR(14)

target_1  = nearest resistance level
target_2  = major resistance level (if different from nearest)
target_3  = entry_price + 8 × ATR(14)

risk/reward = (target_1 − entry_price) / (entry_price − stop_loss)
```

Assessment: R/R ≥ 3 = Excellent · ≥ 2 = Good · ≥ 1.5 = Average · < 1.5 = Poor

---

## Position sizing

```
dollar_risk    = portfolio_size × max_risk_pct / 100
stop_distance  = current_price − stop_loss
share_quantity = floor(dollar_risk / stop_distance)
position_size  = share_quantity × current_price
pct_portfolio  = position_size / portfolio_size × 100
```

---

## Checklist

27 binary PASS/WARNING/FAIL checks across four layers:

### Layer 1 — Company (8 checks)
1. Can explain business in 2 sentences? → always PASS
2. Revenue growing YoY? → PASS if Excellent/Good assessment
3. EPS growing YoY? → PASS if Excellent/Good assessment
4. Free cash flow positive & growing? → PASS if FCF assessment is Excellent/Good
5. Debt manageable (D/E < 2)? → PASS if D/E < 2; FAIL if data unavailable
6. Clear competitive moat? → PASS for Very Strong/Strong; WARNING Moderate; FAIL Weak/None
7. Insiders buying or holding? → from Claude's `insider_status_checklist` field
8. Institutional ownership > 50%? → PASS if ≥ 50%

### Layer 2 — Industry (5 checks)
1. Industry growing? → PASS = Growing; WARNING = Mature; FAIL = Declining/Disrupted
2. Large TAM? → PASS if TAM rating = Excellent/Good
3. Market share increasing or stable? → from Claude's `market_share_checklist` field
4. Low regulatory risk? → PASS = Low; WARNING = Medium; FAIL = High
5. Industry tailwind? → PASS if verdict contains "Tailwind"; WARNING = "Neutral"; FAIL = "Headwind"

### Layer 3 — Economy (5 checks)
1. Fed supportive? → PASS if Cutting/Holding; WARNING if Hiking
2. Inflation improving or stable? → PASS if Improving/Stable; WARNING if Worsening
3. GDP expanding? → PASS if Expanding; WARNING if Flat; FAIL if Contracting
4. No major recession signals? → PASS = Low; WARNING = Moderate; FAIL = High
5. Favorable market style? → from Claude's `market_style_checklist` field

### Layer 4 — Technical (7 checks)
1. Price above 200-day SMA? → PASS/FAIL
2. Price above 50-day SMA? → PASS if above; WARNING if below
3. Golden Cross present? → PASS = golden_cross; FAIL = death_cross; WARNING = none
4. RSI healthy (40–70)? → PASS if Healthy/Strong; WARNING if Oversold; FAIL if Weak/Overbought
5. MACD bullish or neutral? → PASS if bullish crossover; WARNING if none; FAIL if bearish
6. Volume confirming move? → PASS if RVOL ≥ 1.0; WARNING if < 1.0
7. Support level identified? → PASS if nearest support found; WARNING if not

---

## AI Analyst Chatbot

A conversational AI panel appears below every generated research report. It lets you ask follow-up questions about the report in natural language.

### Context

The chatbot system prompt includes a structured snapshot of the report:
- Company name, live price, sector, overall score, recommendation, confidence
- All five dimension scores
- Full technical breakdown (trend verdict, SMA positions, RSI, MACD crossover, RVOL, support/resistance, ATR)
- Entry plan (aggressive/conservative zones, stop loss, profit targets, R/R ratio and assessment)
- Full fundamental summary (revenue/EPS growth, margins, FCF, P/E, D/E, valuation assessment)
- AI verdict (can_buy_today, why, biggest risks, catalysts)

### UX
- Four suggested starter questions appear before the first message
- Press **Enter** or click **Send** to submit
- Full conversation history is sent on each turn so follow-up questions work correctly
- Auto-scrolls to the latest message
- Disabled (greyed out) if no API key is configured in Settings

### API endpoint

```
POST /research/{symbol}/chat
Body: {
  "messages": [
    {"role": "user",      "content": "What is the stop loss?"},
    {"role": "assistant", "content": "The stop loss is at..."},
    {"role": "user",      "content": "And the first target?"}
  ],
  "api_key":  "sk-ant-...",
  "model":    "claude-sonnet-4-6",   // optional
  "provider": "claude"               // "claude" | "deepseek"
}
→ {"role": "assistant", "content": "The first profit target is..."}
```

Requires a cached report to exist (POST to generate first). Returns 404 if no cached report is found. Returns 400 if `api_key` is empty.

---

## Frontend component structure

```
frontend/src/pages/research/
├── index.tsx         # symbol entry landing page
└── [symbol].tsx      # full report page

Key state:
  report: ResearchReport | null    # null until generated or loaded from cache
  tab: Tab                         # active tab (9 options)
  loading: boolean                 # true while POST is in flight
  portfolioSize: number            # for position sizing inputs
  maxRisk: number
  customApiKey: string             # per-session key override
  chatMessages: Message[]          # conversation history
  chatInput: string
  chatLoading: boolean

Key behaviours:
  - useEffect on mount: GET cached report → load instantly if available
  - settings loaded via useEffect (useState + setSettings) to avoid Next.js SSR issues
    with localStorage; ensures the configured API key is always picked up
  - Generate report works without an API key (computed scores only)
  - Research fetch timeout: 90 s (Claude can take 20–60 s)
  - Chat fetch timeout: 60 s
```

---

## Deployment

```bash
# Build and start everything (including research-engine)
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d

# Rebuild only research-engine after Python code changes
docker compose -f docker/docker-compose.yml build research-engine
docker compose -f docker/docker-compose.yml up -d research-engine

# Quick deploy via docker cp (no rebuild needed for Python-only changes)
docker cp services/research-engine/src/api/routes.py stockai-research-engine-1:/app/src/api/routes.py
docker restart stockai-research-engine-1
```

> If `requirements.txt` changes (e.g. adding yfinance), a full `build` is required — `docker cp` cannot install new packages.

### Nginx timeout (EC2 production)

The Nginx config at `/etc/nginx/sites-available/stockai` must have a longer timeout for research routes, since Claude can take 30–60 s:

```nginx
server {
    listen 80;
    server_name lausing.com;

    location /api/research {
        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        proxy_connect_timeout 10s;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 60s;
    }
}
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "No API key" shown in yellow | AI provider not configured in Settings | Settings → AI Assistant → select Claude or DeepSeek and enter key |
| Report generates but AI sections show placeholder text | Key not configured or invalid | Enter key in ⚙ Config panel on the research page, or fix in Settings |
| 504 Gateway Timeout | Nginx `proxy_read_timeout` too short | Ensure `/api/research` location has `proxy_read_timeout 180s` |
| All scores 50, AI verdict = WAIT | Claude API call failed | Check logs: `docker logs stockai-research-engine-1 --tail 30` |
| Technical score seems wrong | Live price not fetched | Check `latest_prices` endpoint; ensure market-data container is healthy |
| Symbol 404 for OSCR / untracked stock | Previously only tracked symbols worked | Fixed: engine now fetches from yfinance directly for any symbol |
| Report is stale | 24-hour cache hit | Click **Clear & Regenerate Report** |
| Chat returns 404 | No cached report | Generate the report first, then chat |
| Chat returns 400 | No API key | Configure key in Settings or enter in ⚙ Config on the page |
| EC2 out of memory during build | t3.medium RAM pressure | Build during off-hours; consider `NODE_OPTIONS=--max-old-space-size=1024` for Next.js |
