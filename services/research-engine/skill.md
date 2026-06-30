# Research Engine — Domain Knowledge & Coding Standards

Aggregates quantitative data from all other services and calls the Claude API to generate
qualitative research reports (company analysis, industry context, economic factors).

---

## What This Service Does

Single large file: `api/routes.py` (~1,795 lines) handles everything:
- Aggregate data collection from market-data, signal-engine, technical-analysis, ml-prediction
- Qualitative analysis via Claude API (claude-sonnet-4-6)
- Research report caching (Redis) to avoid repeated API calls
- Research divergence data for signal-engine (`GET /research/{symbol}/summary`)

---

## Report Architecture

### Data sources aggregated per report
1. **Prices**: 90-day OHLCV, 52-week high/low, ATR
2. **Signals**: current signal per style, confidence, reasons
3. **TA**: RSI, MACD, Bollinger Bands, support/resistance levels
4. **ML**: model probability and confidence
5. **Rankings**: K-score and sector rank
6. **Events**: upcoming earnings, insider trades, sector context
7. **News**: recent headlines and sentiment

### Claude prompt structure
The report prompt is a structured JSON payload sent to Claude. The response is parsed into:
- Overall verdict: STRONG_BUY / BUY / NEUTRAL / AVOID / SELL
- Confidence: 0–100
- Bull/bear thesis
- Key risks
- Price target range

### Caching
Reports are cached in Redis by `{symbol}:{style}:{date}`. Cache TTL: typically 24h.
Stale cache is served if Claude API is unavailable (graceful degradation).

---

## Auth Considerations

### `/research/{symbol}/summary` — used by signal-engine
Called by signal-engine's `_bulk_persist()` to check research divergence.
This endpoint REQUIRES a JWT — signal-engine must pass `_service_token()` in the auth header.
If auth is missing → 401 → research divergence never logged (INT-7 pattern).

### `/research/{symbol}/trigger` — intentionally unauthenticated
Used to kick off a fresh research report from internal services. Reachable only from the Docker
internal network. **Do not add auth to this endpoint** — it is intentionally open by design.
(Documented in CLAUDE.md Connectivity Audit Invariants.)

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /research/{symbol}` | Yes | Full research report (cached or fresh) |
| `GET /research/{symbol}/summary` | Yes | Summary verdict for divergence checks |
| `POST /research/{symbol}/trigger` | No (internal only) | Trigger fresh report generation |
| `GET /research/queue` | Yes | Report generation queue status |

---

## Claude API Integration

Model used: `claude-sonnet-4-6` (current session model — update if migrated).
Cost estimate: ~$0.01–$0.05 per report depending on context length.
Reports are cached aggressively to minimize API spend.

If the Claude API is unavailable, the engine returns a cached report or a degraded response —
it does NOT block the page render.

---

## Research Divergence Logic

Signal BUY + Research AVOID = divergence. This is logged as `signal.research_divergence`.
It does NOT block the trade — it's informational. The paper trading engine and user can
choose to weight research separately.

The divergence check happens in `signal-engine`'s `_bulk_persist()`, not here.
This service only provides the summary data on request.
