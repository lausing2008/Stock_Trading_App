# Event Intelligence — Domain Knowledge & Coding Standards

Aggregates non-price market intelligence: earnings calendars, insider trades, congressional
trading, institutional flows, economic indicators, political catalysts, and sector events.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Earnings calendar + estimates | `services/earnings.py` (~232 lines) |
| Catalyst identification (earnings, FDA, macro) | `services/catalyst.py` (~293 lines) |
| Congressional trading (Quiver Quant) | `services/congress.py` (~270 lines) |
| Insider trading alerts | `services/insider.py` (~288 lines) |
| Institutional flow tracking | `services/institutional.py` (~212 lines) |
| Economic indicators (FRED) | `services/economic.py` (~230 lines) |
| Political event tracking | `services/political.py` (~120 lines) |
| Event API endpoints | `api/routes.py` (~261 lines) |
| Background event sync scheduler | `scheduler.py` (~100 lines) |

---

## Event Types and Trading Significance

### Earnings (`earnings.py`)
- **Pre-earnings**: upcoming earnings within 7 days = catalyst risk. DE should know about this.
- **Post-earnings**: actual vs estimate. Positive surprise → PEAD (Post-Earnings Announcement Drift)
  — stock tends to drift up for 20+ days. Planned as ML feature T204.
- Data source: AlphaVantage EARNINGS_CALENDAR endpoint (or Quiver Quant)

### Insider Trading (`insider.py`)
- **Cluster buying** (multiple insiders buying within 30 days) = strong bullish signal
- **Single large purchase** (>$1M) = moderately bullish
- **Sales** = less meaningful (can be diversification, tax planning, etc.)
- Data source: SEC Form 4 filings via EDGAR RSS or Quiver Quant

### Congressional Trading (`congress.py`)
- Congress members trade with ~15-45 day lag on legislation they're aware of
- Cluster purchases in a sector by members on relevant committees = sector catalyst
- Data source: Quiver Quant Congressional Trading API
- Also available via `market-data/api/congress.py` — the event-intelligence version is the canonical one

### Institutional Flows (`institutional.py`)
- 13F filings (quarterly) — large fund positions
- 13D/13G filings — activist positions (>5% stake)
- These are lagged (45-day reporting delay) — use for directional bias, not timing

### Economic Indicators (`economic.py`)
- FRED data: CPI, Fed funds rate, unemployment, GDP, ISM PMI
- Fed meeting calendar: rate decision dates = high-volatility event
- Macro context consumed by the research engine for report generation

### Catalyst Classification (`catalyst.py`)
- Aggregates earnings + insider + congress + economic into a single `catalyst_score` (0–10)
- High catalyst_score = elevated event risk; DE can use this as a volatility warning

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /events/{symbol}` | Yes | All events for a symbol (earnings, insider, congress) |
| `GET /events/catalyst/{symbol}` | Yes | Catalyst score + breakdown |
| `GET /events/earnings` | Yes | Upcoming earnings calendar |
| `GET /events/insider?symbol=X` | Yes | Recent insider trades for a symbol |
| `GET /events/congress?symbol=X` | Yes | Congressional trading for a symbol |
| `GET /events/economic` | Yes | Recent FRED macro indicators |
| `GET /events/institutional?symbol=X` | Yes | Institutional holdings |

---

## Data Freshness

| Data type | Update frequency | Lag |
|---|---|---|
| Earnings calendar | Daily | 0 |
| Insider trades (Form 4) | Daily | 2 business days (SEC reporting) |
| Congressional trades | Daily | 15–45 days (House/Senate disclosure lag) |
| Institutional (13F) | Quarterly | 45 days post-quarter |
| Economic (FRED) | On FRED release schedule | Varies (CPI = monthly) |

---

## Integration with Other Services

Corrected 2026-07-04 — the signal-engine and DE rows below were describing planned features that
are actually already shipped:

- **Research engine**: calls `GET /events/catalyst/{symbol}` and `GET /events/earnings` to
  include event context in research reports
- **Signal engine**: **already consumes event data directly, not planned.**
  `services/signal-engine/src/api/routes.py` has two call sites (the scheduled `_bulk_persist()`
  path and the manual-refresh path) that call `GET /catalyst/{symbol}` with a service-token
  bearer header, read `catalyst_score`/`insider_score`/`congress_score`/`composite_score` from
  the response, write them into the signal's `reasons`, and nudge `fused_prob` based on
  insider/congress scores. Separately, T208 (SEC 8-K flags) was implemented via a **direct DB
  read of the shared `sec_filings` table**, explicitly avoiding an HTTP hop to this service —
  so T208 is also done, just not via the HTTP path this doc originally implied.
- **DE scoring**: **already consumes `catalyst_score`, not planned.** `decision-engine/api/core/
  scorer.py` has a live scoring layer (`catalyst`, ±1 point) keyed on this service's
  `catalyst_score` — it's a scoring input, not a hard reject/volatility gate, but it is
  definitely already wired up, not a future item.

If you're deciding whether to build new event-intelligence integration work, check the actual
call sites above first — several "planned" items in past docs/tracker entries for this pipeline
turned out to already be shipped by the time anyone re-read the doc.
