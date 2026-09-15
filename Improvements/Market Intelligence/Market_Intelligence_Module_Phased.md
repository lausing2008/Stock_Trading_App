# Market Intelligence Dashboard — Phased Implementation Prompt

**Purpose of this document:** A build-ready prompt for agentic Claude Code sessions.
Replaces the original single-shot "build the whole Bloomberg Terminal" spec with a
3-phase plan that ships something real quickly, then layers in the expensive /
statistically-risky parts once the foundation is proven.

**How to use this:** Paste the "Phase 1" section into a Claude Code session first.
Don't paste all three phases at once — each phase is its own scoped implementation
pass, and Phase 2/3 depend on Phase 1 infrastructure existing.

---

## Role

You are the Lead Software Architect and Principal Quant Engineer extending an
existing AI Stock Trading System. The system already contains:

- AI Signal Engine
- Research Engine
- Decision Engine
- ML Prediction Engine (regime engine → TA/ML signals → meta-labeling gate →
  thesis-persistence check → position sizing)
- Portfolio Optimizer
- Technical Analysis Engine
- Strategy Engine
- Risk Engine

You are adding a new peer module: **Market Intelligence Dashboard**.

## Ground rules (apply to every phase)

1. **No fabricated confidence or probability scores.** Any "confidence," "probability
   of X% move," or "bullish/bearish score" shown to the user must be derived from a
   backtested statistical model (ideally the existing meta-labeling / triple-barrier
   pipeline) — never an LLM asked to output a percentage from vibes. If no calibrated
   model backs a number yet, show a qualitative label ("developing," "early signal")
   instead of a fake precision number.
2. **Every causal claim needs a citation.** "Stock rose because of X" must link to
   the specific article/filing that supports it. If no clear catalyst is found in
   retrieved sources, the system must say so explicitly rather than inventing a
   plausible-sounding reason.
3. **Distinguish free, low-cost, and paid data sources explicitly** in the design —
   don't assume real-time dark pool or 13F freshness that the licensing tier doesn't
   support. 13F filings are ~45 days delayed by regulation; treat them as a lagging
   confirmation signal, not a live one.
4. **Cost-aware LLM usage.** Batch and cache LLM summarization calls; don't
   architect for "re-summarize every ticker every 15 minutes" without a cost model
   attached to it.
5. **Build for one user (you) first.** Ignore "millions of users" scale concerns
   until Phase 1 is proven useful in daily use. Premature scale design wastes budget
   that should go toward signal quality.

---

## PHASE 1 — Foundation (ship in weeks, not quarters)

**Goal:** Get "what happened / why" working well with a small number of real,
already-accessible data sources. This alone should deliver most of the daily value.

### Data sources (Phase 1 only)
- SEC EDGAR (filings, Form 4 insider trades) — free
- One headline feed: Benzinga or Reuters — confirm actual API access/cost first
- Public earnings calendar (Nasdaq or Benzinga) — free
- Your existing market data feed (whatever you're already using for the trading
  platform) for prices/volume

### Modules to build

**1. News Intelligence Engine (scoped down)**
- Ingest from SEC EDGAR + one news feed only (not Reuters+Bloomberg+Yahoo+Reddit+X+
  Discord all at once — add sources incrementally once dedup/clustering works on one).
- Deduplicate articles, cluster similar stories, LLM-summarize, assign sentiment.
- Every summary must retain a source link. No source → no summary shown.

**2. AI Daily Briefing**
- Once per morning, not every 15 minutes.
- Sections: market outlook (from macro data you already track), economic calendar,
  stocks with real catalysts today (earnings/filings — not invented ones), upcoming
  earnings.

**3. Stock Detail Page**
- Overview, price, technical indicators (from your existing TA engine), latest news
  (from the News Intelligence Engine), insider trades (SEC Form 4), earnings, SEC
  filings.
- Bull case / Bear case: generate from retrieved evidence only, each bullet cited.
- Do **not** ship a "probability of move" number in Phase 1 unless it's coming from
  your existing meta-labeling output — if it's not ready, omit this field entirely
  rather than fake it.

**4. Top Gainers/Losers (simplified)**
- Ticker, price, % change, volume/relative volume, sector — plus "reason for
  movement" ONLY if the News Intelligence Engine found a real supporting article.
  Otherwise show "no clear catalyst identified."

### Architecture (Phase 1)
- `market-data` (reuse existing)
- `news-engine` (new)
- `dashboard-api` (new)
- `frontend-dashboard` (new, simple — table + detail page, no heatmaps/timelines yet)

### Explicitly deferred to later phases
Sector heatmap, market movers timeline, options/dark pool flow, institutional 13F
dashboard, portfolio watchlist AI recommendations (Buy/Hold/Sell), predictive
signals, supply-chain intelligence, event detection beyond basic gap/volume flags.

---

## PHASE 2 — Signal layer (build once Phase 1 is in daily use)

**Goal:** Move from "reporting what happened" to "surfacing what deserves attention,"
grounded in your existing quant pipeline rather than fresh LLM guesses.

### New modules
**5. AI Event Detection (subset)**
- Earnings surprises, unusual volume, gap up/down, 52-week breakout, analyst
  upgrades/downgrades, insider buying clusters (multiple insiders within 30 days).
- Each event gets an importance flag based on statistical thresholds you define
  (e.g., relative volume > 3x), not an LLM-assigned "importance score."

**6. Predictive Signals — wired to your existing pipeline**
- This section must call your regime engine → meta-labeling → thesis-persistence
  pipeline, not a standalone LLM forecast.
- Output: ranked watchlist of tickers where your existing conviction-sizing system
  currently shows elevated conviction, with the qualitative reasons (earnings
  revision, insider cluster, technical strength) that fed the score.
- If a ticker's "signal" isn't backed by your model, it doesn't appear here.

**7. Sector Heatmap**
- Sector performance, relative strength, simple rotation view (money flow proxy
  from sector ETF volume). Keep this analytical, not AI-narrated — a heatmap doesn't
  need an LLM to explain each cell; save AI commentary for the daily briefing.

**8. Portfolio Watchlist Intelligence**
- For held/watched tickers only: daily briefing, breaking news, insider activity,
  technical changes. Recommendation labels (Watch/Buy/Hold/Reduce/Sell) must map to
  your Decision Engine and Risk Engine outputs — this dashboard should visualize
  their decisions, not generate independent trading advice via LLM.

### Explicitly deferred to Phase 3
Options flow, dark pool, institutional 13F dashboard, supply-chain intelligence,
market movers timeline, multi-source news (Reddit/X/Discord).

---

## PHASE 3 — Paid/alternative data layer (only once Phase 1–2 prove daily value)

**Goal:** Add the expensive, licensing-dependent signals last, since they're the
costliest and least essential to the core "what happened / why" loop.

### New modules
**9. Institutional Flow Dashboard**
- 13F holdings (WhaleWisdom or similar) — labeled as lagging (45-day-old) data.
- Options flow / unusual activity (Barchart free tier or Unusual Whales if paid).
- Dark pool prints, if a licensed feed is in place — flag data source and latency
  in the UI so it's never mistaken for real-time.

**10. Market Movers Timeline**
- Intraday event log: macro releases, upgrades, sector-moving news, each with
  timestamp, source link, and affected tickers.

**11. Supply Chain Intelligence** (only if trading semis/AI hardware, per your
current focus)
- TrendForce, DigiTimes, SEMI, company data-center announcements — track leading
  indicators (DRAM spot pricing, equipment orders) that tend to move related equity
  names weeks later.

**12. Expanded News Intelligence**
- Add Reddit/X sentiment as an additional *feature* feeding into existing sentiment
  scoring — not a standalone signal. Treat as noisy/crowd-positioning data, same as
  your original sourcing brief noted.

---

## System architecture (target end-state, for reference — do not build all at once)

```
market-data → news-engine → event-engine → sentiment-engine
                                  ↓
                          research-engine → prediction-engine (= your existing
                                              regime/meta-labeling pipeline)
                                  ↓
                          dashboard-api → frontend-dashboard
```

Event-driven communication between services (Kafka/SNS-SQS/whatever your existing
stack already uses on AWS — reuse Airflow for scheduling as originally planned).

## Suggested tech stack (matches your existing tooling)
- Ingestion/orchestration: Airflow (SEC RSS, news API, econ calendar)
- Storage: Snowflake (filings, insider trades, news, event log)
- Transformation: dbt (models for "insider buying clusters," "earnings revisions,"
  event tagging)
- Prediction: your existing Python modules (triple-barrier labeling, meta-labeling,
  thesis-persistence) — the dashboard consumes their output, it does not duplicate
  their logic in the LLM layer
- LLM layer: summarization, clustering, sentiment tagging, citation-grounded
  explanation generation only — never as the source of a probability/confidence
  number

## Output requested from Claude Code (Phase 1 pass only)
1. Architecture for Phase 1 modules only
2. Database schema (news, filings, insider trades tables)
3. Backend API for news-engine + dashboard-api
4. Frontend: gainers/losers table + stock detail page (no heatmap/timeline yet)
5. Data ingestion pipeline (SEC EDGAR + one news feed)
6. Background workers + scheduler (Airflow DAGs)
7. Testing strategy for Phase 1 scope
8. Brief note on what Phase 2/3 will require, without building it yet
