# Market Pressure Engine — Scoping Decision (2026-09-01)

Source proposal: `Improvements/Market Pressure, Options, Short Squeeze & Margin Risk Engine.md`
("the doc" below). This doc records the scoping decision made after mapping the doc's own
§30 checklist against real current code — see the full architecture-review agent report this
session's own conversation history contains for the detailed per-item verification. Every
claim below was personally spot-checked against live source before being trusted, per this
repo's own standing discipline that a background agent's report is a claim to verify, not a
fact to act on.

## Headline finding

Roughly 60-70% of what the doc asks for already exists, under different names, spread across
`market-data`, `decision-engine`, `ranking-engine`, `portfolio-optimizer`, and
`event-intelligence`. The doc's own two central premises are factually wrong for this
codebase, confirmed by direct grep with zero hits either way:

- **"FMP fundamentals integration/planned integration"** — false. `fmp_api_key` is a dead
  config stub (`shared/common/config.py:69`) with zero consumers anywhere. Fundamentals are
  100% yfinance today.
- **"Unusual Whales API integration/planned integration"** — false. Zero code references
  anywhere in the repo (`unusual_whales`/`UnusualWhales`/`uw_api` all return zero hits). This
  repo already investigated Unusual Whales' real pricing once (2026-08-24, documented in
  `.claude/CLAUDE.md`) and found the entry API tier is $125/mo, deliberately deferring
  subscription pending the existing squeeze/gamma alert family accumulating enough resolved
  forward-return outcomes to judge whether the current free-data heuristic is materially
  wrong. That decision stands, unrevisited by this scoping pass.
- **"Portfolio Margin Risk" (§7/§8)** — the doc assumes real leverage/margin exists to model.
  It does not: this is a cash-only paper-trading platform (`_open_paper_trade()`'s hard gate,
  `if position_value > portfolio.current_cash * 0.98: return None, "insufficient_cash"` —
  confirmed directly, a `PaperTrade` position value can never exceed available cash). Zero
  real margin-call/maintenance-margin/leverage concept exists anywhere in the trading engine.

## Disposition table

| # | Item | Disposition | Why |
|---|---|---|---|
| 1 | Composite Short Squeeze Score (0-100) | **Build** | Real short-interest fields (`short_percent_of_float`, `short_ratio`, `shares_short`, `short_interest_date`) already exist as point-in-time-safe columns on `Fundamental`; real squeeze-alert gating logic (short-float floor, price-move floor, session-scaled RVOL floor) already exists in `check_short_squeeze_alerts()`. Nothing here needs a new data source — this is a compositing task over data the app already has. |
| 2 | Composite Options Pressure Score (0-100) | **Build** | Real options-flow (call/put volume, whale-trade detection, cp_ratio, 5-tier sentiment) and max-pain calculation already exist. Same compositing task, no new data source. |
| 3 | Options Expiration tracking view (§6) | **Build** | Already-fetched options-chain data (per-strike OI/volume/expiry) just needs a dedicated per-expiration rollup + a NORMAL/ELEVATED/HIGH/EXTREME classification relative to the symbol's own historical norm — no new fetch. |
| 4 | Feature-ablation harness (§14) | **Build first, before anything else** | The doc assumes this is a reuse of existing walk-forward infrastructure. It is not — every existing sweep (K-Score weights/curve, `tune_strategy`, `walk_forward_scorer_sweep`) perturbs one or two numeric constants at a time, never adds/removes a whole named feature *group*. This is genuinely new work, and it's the cheapest, highest-value piece: it tells us whether the ALREADY-EXISTING short-interest ML features (`short_ratio`, `short_ratio_delta`, `short_percent_of_float` — already in `FUNDAMENTAL_COLUMNS`) are pulling their weight before any new feature is added on top. |
| 5 | Wire a Market Pressure score layer into decision-engine (§16) | **Build, after 1-3 land** | `compute_score()` already has 8 named layers with an established `cfg`-driven-threshold + `ScoreItem` pattern; `hard_rejects.py` already has ~23 gates with the same fail-open, human-readable-reason convention. A 9th layer / a new hard-reject reading the two composite scores from #1/#2 slots in cleanly — but only once those scores exist and #4 has told us whether the squeeze/options signal actually carries predictive value worth scoring on. |
| 6 | True GEX / dealer-hedging engine (§4) | **Deferred, not rejected** | Needs real per-contract Greeks + IV + a dealer-positioning assumption — none of which any current data source in this app provides. Confirmed: zero Black-Scholes/dealer-positioning code exists anywhere. Genuinely requires Unusual Whales (or equivalent) to build honestly rather than fabricate Greeks, which the doc's own §4 explicitly forbids. Re-affirms the existing 2026-08-24 deferral decision — do not re-litigate until the squeeze/gamma alert family's own outcome data (currently only ~107 alerts fired total) is large enough to judge whether the free proxy is actually failing. |
| 7 | Short covering probability / borrow-fee / utilization inputs (§2) | **Deferred, same reason as #6** | Borrow fee/utilization/shares-available are Unusual-Whales-specific data points with no free equivalent found anywhere in this codebase's current provider set. |
| 8 | Portfolio Margin Risk as literally specified (§7/§8) | **Rejected as specified** | No real substrate exists to compute against — this platform has no leverage. Building it would mean fabricating a purely hypothetical margin simulation with nothing real at stake, which produces a number with no grounding rather than a genuine risk signal. If a "what-if I were on margin" educational tool is ever wanted, that's a different, explicitly-labeled feature, not what §7/§8 describes. |
| 9 | "Optimize for Profit Factor as the PRIMARY objective" (§13/§27/§28) | **Deferred as its own separate decision, not bundled here** | Real and legitimate as a critique — every existing promotion gate (`_passes_promotion_margin`, the EV-gate, K-Score sweeps) optimizes EV-lift or precision today, never Profit Factor directly. But re-pointing every existing walk-forward promotion gate at a new primary objective is a large, cross-cutting change to already-proven, already-live infrastructure (drawdown sweep, open-risk sweep, K-Score weight/curve sweeps, scorer sweep, ML ensemble tuning) — it deserves its own dedicated scoping pass with its own before/after validation, not a silent side-effect of building a market-pressure feature. |
| 10 | Group-level feature ablation beyond squeeze/options (§14's full BASELINE+ALL grid) | **Deferred pending #4's own result** | The doc's full 8-cell grid (`BASELINE`, `+OPTIONS`, `+SHORT`, `+MARGIN`, and all pairwise/triple combinations) assumes margin features exist (they don't, per #8) and assumes enough resolved-outcome data exists to trust an 8-way comparison without overfitting. Build the 2-group version (short-interest, options) first per #4; decide whether a 3rd group is ever addable once #6/#7 are unblocked. |

## What this scoping deliberately does NOT do

- It does not commit to subscribing to Unusual Whales. That's a real-money recurring cost
  decision for the user to make when the existing free-data alert family has enough resolved
  outcomes to judge — re-check `SqueezeAlertOutcome`/`PreBreakoutAlertOutcome` row counts
  before ever revisiting #6/#7.
- It does not touch any existing promotion gate's optimization objective. #9 stays a
  documented, separate future decision.
- It does not silently rename or replace the existing `check_gamma_unwind_alerts()` OI-
  concentration proxy, `RestrictedSymbol`, `SqueezeWatch`, or any other already-shipped
  mechanism. The new composite scores in #1/#2 are compositions ON TOP of this data, not
  replacements for the alert mechanisms that already consume it directly.

## Tracker entries

Filed as Tier 320 in `frontend/src/pages/improvements.tsx`, ids `MPE-01` through `MPE-10`
(matching the disposition-table numbering above 1:1). `MPE-01`/`MPE-02`/`MPE-03`/`MPE-04`/
`MPE-05` are `todo` (build). `MPE-06` through `MPE-10` are `todo` too, but each carries an
explicit `implementedNote`-equivalent (in `what`/`fix`) recording WHY it's deferred/rejected
rather than silently absent from the tracker — so a future survey doesn't re-propose them
without first reading why they were set aside.
