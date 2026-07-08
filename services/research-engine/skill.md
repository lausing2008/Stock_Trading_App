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

Model used: check `services/research-engine/src/` for the actual model string configured at
call time — this doc previously named a specific model ID that had already drifted from what's
live. Don't trust a hardcoded model name in documentation; grep the source for the current value
(e.g. `grep -rn "claude-" services/research-engine/src/`) since model IDs change more often than
this file gets updated.
Cost estimate: ~$0.01–$0.05 per report depending on context length.
Reports are cached aggressively to minimize API spend.

If the Claude API is unavailable, the engine returns a cached report or a degraded response —
it does NOT block the page render.

---

## Research Divergence Logic

Signal BUY + Research AVOID = divergence. This is logged as `signal.research_divergence`
by signal-engine's `_bulk_persist()` — this service only provides the summary data on request.

**Correction (2026-07-04): AVOID/SELL research IS a hard reject on paper trading entries, not
purely informational.** `paper_trading_engine.py` (`_scan_for_entries`, ~line 3289):
```python
# Hard gate: AVOID/SELL research blocks entry entirely — mirrors DE hard_rejects logic
if cfg.get("research_gating_enabled", True) and _research_rec in ("AVOID", "SELL"):
    continue  # skips this candidate entirely — not just a scoring penalty
```
The identical gate exists in `decision-engine/api/core/hard_rejects.py`. AVOID/SELL research
also tightens the trailing stop on already-open positions when it deteriorates mid-trade
(`paper_trading_engine.py` research-deterioration check) and reduces position sizing on entry.
Do not describe research as "informational only" in any future doc — it materially blocks and
shrinks real trading decisions when `research_gating_enabled` is true (the default).
