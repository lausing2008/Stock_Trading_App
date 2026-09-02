## Review: docs/recomm_or_audit/ — 13 Institutional Features (IF-01..IF-13), Verified 2026-08-18

**Ask**: "Review all the docs under recomm_or_audit folder, see if we have most of them
implemented. If not, document them and update improvement tracker page for action items. Check
the INSTITUTIONAL_FEATURES_OVERVIEW.md first, others are supplementary."

**Verdict: 0 of 13 fully implemented, 5 partial, 8 not built.** Tracked as **Tier 289** in
`improvements.tsx` (14 entries — 1 summary + 13 per-feature). Every feature verified directly
against current code by 3 parallel agents (each required to cite file:line), with every
high-stakes claim independently re-checked by hand.

**Unlike the two prior external audit docs reviewed this month**
(`COMPREHENSIVE_SYSTEM_AUDIT_2026-08-16.md` — 7 of 12 claims stale;
`STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` — 4+ of 8 proposals already built), **this doc set
held up well**: it proposes genuinely new capability rather than misreading existing code as
broken. Documented here mainly for the four name-collision traps below.

### ⚠️ The dominant risk: FOUR name-collision traps

For four features, an endpoint with a near-identical NAME already exists but measures something
materially different. A future session skimming for "is this built?" would very plausibly mark
these done incorrectly:

| Feature | Existing thing with a similar name | What it ACTUALLY measures |
|---|---|---|
| **IF-02** Alpha Decay | `GET /signals/alpha_decay` (`outcomes.py:1818`) | **Holding period** — `entry_date + td` against a FIXED `entry_price` (`outcomes.py:1891-1895`). Entry held constant, exit varies → "how long should I hold?". IF-02 asks the **inverse**: hold exit constant, vary the **entry lag**. |
| **IF-03** Earnings Call NLP | `generate_earnings_impact()` (`earnings.py:64-123`) | A real Claude call, but on **numeric EPS/revenue results** (prompt at `earnings.py:81-91`), never a transcript. |
| **IF-05** Options GEX | `check_gamma_unwind_alerts()` (`scheduler.py:3828`) | An **open-interest concentration ratio** in a ±5% strike band (`scheduler.py:3937-3950`). Its own docstring (`:3839-3848`) already states it is **NOT** real GEX. |
| **IF-07** Factor Exposure | `/signals/factor-exposure` + `/signals/factor_attribution` | Technical-indicator values (RSI/ADX/volume_z) compared across correct-vs-wrong calls, and per-boolean-reason win-rate edge. **Neither is a Fama-French regression.** |

### Verdicts

**PARTIAL (5)** — `IF-01` VaR (~15%: a 3-line parametric VaR at `risk.py:158-161`, never
persisted; zero stress testing) · `IF-10` Attribution (real, but by entry characteristic —
score/confidence/regime/RR — not Brinson; benchmark is a single scalar at
`paper_portfolio.py:255-264`) · `IF-11` Multi-Strategy (isolation genuinely SATISFIED by the
5-portfolio architecture; only dynamic reallocation missing) · `IF-12` Compliance (position/
sector/open-risk limits ARE enforced pre-trade at `paper_trading_engine.py:550,553,572`; missing
the formal rule table, restricted list, and an **immutable** trail — `paper_trades` rows are
mutated in place) · `IF-13` Regime Sizing (**most-built**: real 5-state `regime_size_mult` at
`:4513-4519` — note bear = **0.0**, stricter than the design's 0.4 — plus 4 stacked dampeners
including a continuous VIX gradient at `:4590`; volatility **targeting** genuinely absent).

**NOT BUILT (8)** — IF-02, IF-03, IF-04 (spot VIX is the only true cross-asset instrument; zero
bonds/credit/FX/commodities), IF-05, IF-06 (every real order is a market order; slippage modeled
as a flat 10bps constant, never reduced), IF-07, IF-08, IF-09.

### Three are blocked on something other than engineering effort

- **IF-03** — blocked on a **data source**. The codebase already documents in 3 places that no
  transcript source exists (`scheduler.py:6904`, `email_service.py:1863`, plus an existing
  tracker entry). The design proposes scraping Seeking Alpha / Motley Fool — flagged as a real
  ToS + fragility risk, the same dependency class that killed this repo's congress-data sources.
- **IF-09** — blocked on **data availability**: no depth-of-book source in this stack (yfinance
  has none; Alpaca free tier gives top-of-book only). Also a horizon mismatch — microstructure
  signals decay long before SHORT(≈10d)/SWING(≈20d) holding periods.
- **IF-08** — blocked on **recurring cost**: all five named sources (satellite/app-downloads/
  job-postings/web-traffic/credit-card) are institutional-tier paid feeds. The design does not
  mention pricing at all.

### A measured feasibility constraint worth remembering (IF-02)

Queried production directly rather than assuming — the real `entry_date − signal_date` lag
distribution in `signal_outcomes`:

| lag (days) | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| rows | 7 | **9,217** | 179 | 1,756 | 518 | 1 | 1 | 1 |

The T+1 convention dominates and variance is thin AND **day-grained**, so the design's hourly
decay columns (`return_1h`, `return_4h`) are **not buildable from stored data**. Either scope to
a day-grained curve on the ~2,400 lag≥2 rows (noting the lag is not random — it likely correlates
with *why* entry was delayed, a real confound), or record forward-looking intraday prices at
fixed offsets and accept months of accumulation.

**IF-02 is also the highest-leverage of the 13 for a non-obvious reason**: age thresholds already
govern live entries today (`max_signal_age_hours=72` hard reject at `paper_trading_engine.py:581`;
a hand-set `<4h` "prime window" / `>18h` "conditions may have shifted" rule at `:2077-2083`,
mirrored in decision-engine). Those constants are exactly the never-validated class this repo
catalogs under `T234-CONFIG-UNJUSTIFIED-THRESHOLDS` — IF-02 is the missing measurement that would
justify or refute them.

### Cheapest genuinely-useful next steps (if pursued)

1. **IF-04 yield curve** — FRED already has an API key in production and serves `DGS10`/`DGS2`/
   `T10Y2Y` and HY spreads directly, so this is a near-zero-marginal-cost extension of the
   existing `economic.py` sync, not a new vendor integration.
2. **IF-05 max pain** — needs only strike + open interest (both already fetched), no IV, no
   Black-Scholes, no dealer assumption. A pure, directly-unit-testable function. True GEX is the
   harder half and needs a dealer-positioning **assumption**, not a measurement.
3. **IF-13 volatility targeting** — small, most infrastructure already live. **Caveat**: it
   partially overlaps the existing VIX gradient and ATR-based stop sizing; measure marginal
   effect rather than stacking a fourth correlated vol dampener into over-shrinking positions.
4. **IF-06 size-aware slippage** — not the full TWAP/VWAP design (unjustified at ~$100k book
   sizes), but making the flat 10bps constant scale with position-value/ADV improves the honesty
   of every backtest number this app produces.

### Doc-set gaps found (for whoever maintains these docs)

`INSTITUTIONAL_FEATURES_OVERVIEW.md`'s table references **7 design docs that do not exist**
(`DESIGN_FACTOR_EXPOSURE.md`, `DESIGN_ALTERNATIVE_DATA.md`, `DESIGN_MARKET_MICROSTRUCTURE.md`,
`DESIGN_PORTFOLIO_ATTRIBUTION.md`, `DESIGN_MULTI_STRATEGY.md`, `DESIGN_COMPLIANCE_AUDIT.md`,
`DESIGN_REGIME_AWARE_SIZING.md`). No content is actually missing — IF-07..IF-13 are all covered
inside a single **unreferenced** `DESIGN_ADDITIONAL_FEATURES.md`; only the filename pointers are
wrong.

**How to re-verify any of this**:
```bash
# The four name-collision traps — confirm each still measures what this doc says:
sed -n '1891,1895p' services/signal-engine/src/api/outcomes.py     # IF-02: entry_date + td, fixed entry_price
sed -n '81,91p'     services/event-intelligence/src/services/earnings.py  # IF-03: numeric inputs only
sed -n '3839,3848p' services/market-data/src/services/scheduler.py # IF-05: its own "NOT a real GEX" disclaimer
grep -n 'FACTORS = \[' services/signal-engine/src/api/outcomes.py  # IF-07: rsi/adx/volume_z, not SMB/HML

# Key negatives (all should return nothing):
grep -rn "max_pain\|maxpain" services/ --include="*.py" | grep -v "max pain"
grep -rn "norm.pdf\|black_scholes" services/ --include="*.py"
grep -rn "\^TNX\|HYG\|LQD\|DXY\|T10Y2Y" services/ --include="*.py"
grep -rn "PortfolioRiskMetric\|StressTestResult\|herfindahl" shared/db/models.py
grep -rn "twap\|iceberg\|StrategyAllocation\|ComplianceRule" services/ shared/ --include="*.py"
grep -rn "target_vol\|volatility_target" services/ --include="*.py"

# IF-13 is the most-built — see the real live multipliers:
sed -n '4513,4519p' services/market-data/src/services/paper_trading_engine.py
```

---

