# Decision Engine — Domain Knowledge & Coding Standards

Makes the final BUY/SKIP decision for paper trading by running hard rejects then numerically
scoring candidates via a multi-layer integer-point system (not a fixed dimension count — see
Scoring Layers below). The gatekeeper before capital is deployed. Note: `_should_enter()` in
`paper_trading_engine.py` is a SEPARATE, independently-drifted scorer that this service was
"extracted faithfully from" but no longer matches — see T232-DL-DUALSCORER.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Hard reject gates (cheap, run first) | `api/core/hard_rejects.py` (~151 lines) |
| Market regime — proxies market-data's classifier (fixed 2026-07-04, see T232-DL-REGIME5X) | `api/core/regime.py` (~72 lines, down from ~232 after removing the duplicated classifier) |
| Multi-layer integer-point scoring (not a fixed 9-dimension structure) | `api/core/scorer.py` (~219 lines) |
| Position sizing | `api/core/sizer.py` (~149 lines) |
| Data aggregation (signals + context) | `api/core/aggregator.py` (~177 lines) |
| Decision API endpoints (`/decide/{symbol}`, `/decide/batch`, `/decide/{symbol}/explain`) | `api/routes.py` (~369 lines) |
| Shared data models | `api/core/models.py` (~102 lines) |

---

## Decision Pipeline

```
POST /decide/{symbol}  (symbol is a PATH param, not just body — also: POST /decide/batch,
                         GET /decide/{symbol}/explain)
    ↓
hard_rejects.py  — check each gate in order; first BLOCK returns immediately
    ↓             score = -99 means hard rejected (display as "Hard rejected" not "-99/12")
scorer.py        — compute_score(): a variable-length list of integer-point layers, NOT a
    ↓             fixed 9-dimension 0–1.33-each structure (see Scoring Layers below)
sizer.py         — compute position_size_pct based on score + regime + portfolio config
    ↓
Return: {decision: ENTER|SKIP, score, size_pct, reasons}
```

### Hard reject gates (as of T202)
Order matters — cheaper/more-common gates run first:
1. Open position limit per sector
2. Open exposure cap (`max_open_exposure_pct`)
3. Signal staleness (`max_signal_age_hours`)
4. Price drift from signal date (`max_price_drift_pct`)
5. Volume z-score gate (`min_volume_z`)
6. Confidence decline gate (`max_confidence_decline`)
7. Equity floor circuit breaker
8. Regime gate
9. Stop cooldown (same stock recently stopped out)

### Score = -99 convention
Hard rejected before scoring → `score = -99`. This is a sentinel value, not a real score.
The frontend (`decide.tsx` `ScoreBar`) displays this as "Hard rejected — no score computed"
rather than "-99 / 12".

---

## Regime Detection (`regime.py`)

Computes current market regime from VIX, SPY momentum, and breadth indicators.
Regimes: `bull`, `neutral`, `choppy`, `bear`, `risk_off`.

Used in two ways:
1. As a hard reject: if `regime_risk_off_gate=True` and regime is `risk_off` → BLOCK
2. As a position size multiplier: choppy/bear reduces `position_size_pct`

Regime is also cached in Redis for consumption by other services.

---

## Scoring Layers (`scorer.py::compute_score()`)

Not a fixed 9-dimension × 0–1.33-points structure — actually a variable-length list of integer
point deltas, several of which are conditional (only fire if the relevant data is present).
Total score is NOT bounded to [0, 12]; it can go negative or exceed 12. Actual layers (line
numbers approximate, verify against current `scorer.py` if this drifts again):
1. `price_zone` — ±2/±3 based on entry zone quality
2. `rr_quality` — 0 to +2 based on risk:reward ratio
3. `volume` — ±1, conditional on volume_z being present
4. `earnings` — -1, conditional on upcoming earnings proximity
5. `ml_signal` — ±1 based on ML probability alignment
6. `conf_delta` — ±1, conditional on confidence_delta being present
7. `freshness` — ±1, conditional on signal age
8. `catalyst` — ±1, conditional on event-intelligence catalyst_score being present (NOT in the
   original 9-dimension list — this layer consumes event-intelligence data directly, see
   `services/event-intelligence/skill.md` for the source)
9. `pre_regime` — -1 if regime is pre-choppy/pre-risk_off
10. `entry_drift` — -2 to +1 based on price drift from signal date
11. `research` — -2 to +2 via a `_RESEARCH_SCORE` lookup dict (research recommendation alignment)
12. `regime` — -2 to +1 via a `_REGIME_SCORE` lookup dict

Score interpretation (approximate — these thresholds are not hard invariants since the score
range itself is unbounded):
- ≥ 8: Strong BUY
- 6–8: Moderate BUY
- 4–6: Weak / borderline
- < 4: SKIP (even without hard reject)

**"Sector relative strength" and a standalone "technical setup quality" layer do NOT exist** in
the current scorer — do not assume they're being scored unless re-verified against the live file.

---

## Position Sizing (`sizer.py`)

Base: `portfolio.config.position_size_pct` (default 0.10 = 10% of equity).
Adjusted by:
- Regime multiplier (1.0 bull → 0.5 bear)
- Score multiplier (higher score → slightly larger position)
- Sector concentration cap
- Open exposure remaining

---

## Key API Contract

The paper trading engine calls `POST /decide` with `signal_data` dict. This dict must include:
```python
{
    "symbol": str,
    "style": str,           # SWING, GROWTH, SHORT, LONG
    "signal": str,          # BUY / HOLD / SELL
    "confidence": float,
    "confidence_delta": float,
    "signal_age_hours": float,
    "live_price": float,
    "sig_ref_price": float, # close at signal date (for price drift)
    "volume_z": float,
    "open_exposure": float,
    "equity_ratio": float,  # equity / initial_capital
    "regime": str,
    "portfolio_config": dict,
}
```

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /decide/{symbol}` | Yes (JWT) | Main decision endpoint called by paper trading engine — symbol is a path param |
| `POST /decide/batch` | Yes | Batch decision across multiple symbols |
| `GET /decide/{symbol}/explain` | Yes | Human-readable breakdown of a decision (used by frontend's decide.tsx) |
| `GET /regime` | No | Current market regime — as of 2026-07-04, `api/core/regime.py` calls market-data's `GET /stocks/regime` directly (15-min local cache on top) instead of maintaining its own classifier. Guaranteed to agree with the regime that actually gates real paper trading. See T232-DL-REGIME5X for the fix and the remaining, deliberately-unmerged signal-engine classifier (4th of the original 5, kept separate because its vocabulary is load-bearing for calibrated signal thresholds). |

---

## Known Drift: SCALP / INCOME Styles Don't Exist in the Real Trading Engine

`api/core/models.py` and `aggregator.py` define `SCALP` and `INCOME` as valid style values
(`style: str = Field("SWING", description="SCALP | SWING | GROWTH | INCOME")`). **These do not
exist anywhere in the actual trading engine** (`services/market-data/src/services/
paper_trading_engine.py`), which only implements `SHORT | SWING | LONG | GROWTH`. If you see
`SCALP` or `INCOME` referenced in decision-engine code, treat it as speculative/dead — it has
never been exercised against real trading data. Tracked as `T232-DL-STYLEPARAMS3X` in
`frontend/src/pages/improvements.tsx`. Do not build new features assuming these styles are live.

## Known Drift: Style Game-Plan Parameters Diverge From the Real Trading Engine

`aggregator.py`'s `_STYLE_PARAMS` is a THIRD independent copy of the style stop/target parameters
(the other two are `scheduler.py` — source of truth — and `paper_trading_engine.py`, which
mirrors scheduler.py by comment discipline). Decision-engine's copy has already drifted from the
real engine for GROWTH (stop -16%/target +60% here vs. the real engine's -12%/+35%). Do not trust
this file's GROWTH/SWING/SHORT/LONG stop-target percentages without cross-checking
`scheduler.py::_STYLE_PARAMS` first.
