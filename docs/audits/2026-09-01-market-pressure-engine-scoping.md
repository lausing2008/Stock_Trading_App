## Scoping Decision: Market Pressure, Options, Short Squeeze & Margin Risk Engine (2026-09-01)

**User asked to review a large proposal document** (`Improvements/Market Pressure, Options,
Short Squeeze & Margin Risk Engine.md`, 30 sections) asking for a combined Short-Squeeze/
Options-Gamma/Margin-Risk scoring engine feeding the AI decision engine. Per the doc's own
§30, it explicitly requires an "existing architecture summary" response BEFORE any code is
written — dispatched a mapping agent to check every claim against real current code, then
personally spot-checked the most load-bearing claims via direct grep before trusting the
report (the same standing discipline this whole session has repeatedly applied to background
agent output).

**Headline finding: roughly 60–70% of the doc's ask already exists**, under different names,
and its two central premises are factually false for this codebase (confirmed via zero-hit
greps in both directions):

- **"FMP fundamentals integration/planned integration"** — false. `fmp_api_key`
  (`shared/common/config.py:69`) is a dead config stub with zero consumers anywhere in the
  codebase. Fundamentals are 100% yfinance today.
- **"Unusual Whales API integration/planned integration"** — false. Zero code references
  anywhere (`unusual_whales`/`UnusualWhales`/`uw_api` all return zero hits). This repo already
  investigated Unusual Whales' real pricing once (2026-08-24, documented elsewhere in this
  file) and found the entry tier is $125/mo, deliberately deferring subscription — that
  decision stands, unrevisited by this scoping pass.
- **"Portfolio Margin Risk" (§7/§8)** — the doc assumes real leverage/margin exists to model.
  Confirmed directly: `_open_paper_trade()`'s hard cash gate
  (`if position_value > portfolio.current_cash * 0.98: return None, "insufficient_cash"`)
  makes a leveraged position structurally impossible. Zero real margin-call/maintenance-
  margin/leverage concept exists anywhere in the trading engine (the sole "leverage" grep hit
  across the whole `market-data` service is figurative English usage in an unrelated code
  comment, not a trading concept).

**What already exists** (verified directly, not just via the agent's report): real
options-chain/options-flow endpoints + max-pain calculation (yfinance-sourced); an honestly
self-disclaimed OI-concentration gamma proxy (`check_gamma_unwind_alerts()`'s own docstring
explicitly states "this is NOT a real gamma-exposure (GEX) calculation" — confirmed zero
Black-Scholes/dealer-positioning code exists anywhere); real short-interest fields
(`short_percent_of_float`/`short_ratio`/`shares_short`/`short_interest_date`) already on the
point-in-time-safe `Fundamental` table AND already real ML features
(`FUNDAMENTAL_COLUMNS` in `builder.py`); two live squeeze-alert scheduled jobs with real
gating logic; a `RestrictedSymbol` blacklist; real portfolio risk metrics (Sharpe/Sortino/
Calmar/CAGR/max-drawdown/Profit Factor, VaR/CVaR + 5 real historical stress scenarios,
volatility-targeting position sizing); a mature walk-forward promotion-gate framework; and
decision-engine's `compute_score()` already has 8 named layers + `hard_rejects.py`'s ~23
hard-reject gates with an established `cfg`-driven-threshold pattern any new score component
would slot into cleanly.

**What's genuinely missing**: a group-level feature-ablation harness (every existing sweep
perturbs one or two numeric constants at a time, never a whole named feature GROUP); true
GEX/dealer-hedging (needs real per-contract Greeks + a dealer-positioning assumption — no
data source currently provides either); Profit-Factor-as-primary-optimization-objective
(every existing promotion gate optimizes EV-lift or precision today, never Profit Factor
directly, despite Profit Factor already being a computed metric in 3+ places).

**Disposition, filed as Tier 320 / ids `MPE-01` through `MPE-10`** in
`frontend/src/pages/improvements.tsx` — full per-item reasoning in
`docs/SCOPING_MARKET_PRESSURE_ENGINE_2026-09-01.md`:

- **Build now (MPE-01 through MPE-05)**: a composite Short Squeeze Score and a composite
  Options Pressure Score (both pure compositing over already-existing data, no new data
  source); an options-expiration tracking view (rollup over already-fetched options-chain
  data); a genuinely new 4-cell feature-ablation harness (BASELINE/+SHORT/+OPTIONS/
  +SHORT+OPTIONS — deliberately narrower than the doc's own 8-cell grid, matching this
  codebase's own "combinatorial cost + overfitting risk on a thin sample" discipline already
  applied to every existing sweep), built FIRST to test whether the ALREADY-EXISTING short-
  interest ML features carry real predictive value before scoring anything new on top of
  them; and wiring the two composite scores into decision-engine's existing scorer as a 9th
  layer — explicitly gated on the ablation harness's own result, never built speculatively.
- **Deferred pending Unusual Whales (MPE-06/MPE-07)**: true GEX/dealer-hedging; short-covering
  probability/borrow-fee/utilization/shares-available. Re-affirms the existing deferral
  decision — re-check `SqueezeAlertOutcome`/`PreBreakoutAlertOutcome` row counts (currently
  only ~107 alerts fired total across the whole family) before ever revisiting.
- **Rejected as literally specified (MPE-08)**: Portfolio Margin Risk — no real substrate
  exists to compute against on a cash-only platform; building it would mean fabricating a
  purely hypothetical simulation with nothing real at stake.
- **Deferred as their own separate future decisions (MPE-09/MPE-10)**: shifting every
  existing promotion gate to optimize Profit Factor as the primary objective (a real, large,
  cross-cutting change to already-proven infrastructure — deserves its own dedicated scoping
  pass with before/after validation, not a silent side-effect of this build); the full 8-cell
  ablation grid (assumes a margin feature group that does not exist per MPE-08).

**No code was written for this task** — it was a research + scoping + documentation pass
only, per the doc's own explicit "wait for confirmation before making large architectural
changes" instruction.

**What to check if this needs re-verifying**:
```bash
grep -rn "unusual_whales\|UnusualWhales\|uw_api" services/ shared/
grep -rn "financialmodelingprep\|FinancialModelingPrep\|fmp_adapter\|class FMP" services/ shared/
grep -n "insufficient_cash\|current_cash \* 0.98" services/market-data/src/services/paper_trading_engine.py
cat docs/SCOPING_MARKET_PRESSURE_ENGINE_2026-09-01.md   # full disposition table
```
