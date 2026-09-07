# SCOPE: Options Simulator

**Date:** 2026-09-06/07
**Ask:** "Option simulator."

**Verdict:** two of four plausible variants are genuinely feasible and worth building together;
one is the largest build and should follow later; **one is hard-blocked and should not be
attempted.**

---

## 0. Two findings that shape everything

### This codebase contains ZERO option-pricing math

Verified by exhaustive grep: no Black-Scholes, no greeks computation, no implied-volatility
solving anywhere in `services/` or `shared/`. Every mention of it is a *disclaimer explaining its
absence*:

- `services/market-data/src/api/routes.py:3609` — "no implied volatility, no Black-Scholes, no
  dealer-positioning assumption"
- `services/market-data/src/services/scheduler.py:4214` — `check_gamma_unwind_alerts()`'s own
  docstring: a true GEX model "needs each contract's actual gamma (from a Black-Scholes calc)…
  neither is computed anywhere in this app"

Every options value on the platform today is **vendor-read**: IV from yfinance's
`impliedVolatility` (`routes.py:3596`, displayed only, never fed to a model), greeks from
Unusual Whales' `/greeks` (`unusual_whales.py:1012` — fetch and parse, no math).
`compute_max_pain()` (`routes.py:3601`) is the only options math in-repo, and it is deliberately
IV-free (intrinsic value only).

**Practical note:** `scipy` is **not** in `services/market-data/requirements.txt` (only
pandas/numpy) — it's in ml-prediction only. numpy has neither `norm.cdf` nor `erf`. Python's
stdlib `math.erf` is sufficient and is the cleanest **zero-new-dependency** route for a
normal CDF. `frontend/src/pages/alerts-guide.tsx:645` already observes that BS gamma is "a
self-contained quant task… closed-form formula, no new data source" — which is correct.

### There is NO historical options data — at all

69 tables; only 4 are options-related and **none stores a chain**:

| Table | What it holds | Why it can't back a backtest |
|---|---|---|
| `options_flow_snapshots` (`models.py:1792`) | EOD aggregates (cp_ratio, sentiment) per symbol/date | no strikes |
| `gex_snapshots` (`models.py:1836`) | 4 scalars/day (call_wall, put_wall, gamma_flip, gamma_magnet) | no chain |
| `options_game_plan_snapshots` (`models.py:1875`) | **exactly 2 contracts/day** (one put, one call) + greeks, `iv_rank_1y`, `expected_move_pct` | 2 contracts ≠ a chain |
| `options_flow_alert_outcomes` (`models.py:2287`) | per-alert contract, scored on **underlying** returns | not option prices |

`grep "strike"` across `models.py` returns only these. **No historical per-strike chain, no IV
time series, no historical option prices.** The live chain is Redis-cached only
(`routes.py:3820`).

### Paper trading is equity-only, definitively

`PaperTrade` (`models.py:896-980`) has `symbol/entry_price/shares/stop_loss/current_stop/pnl` —
**no strike, expiry, option_type, contract, or multiplier field.** No options position model
exists anywhere. **Options positions cannot be tracked today.**

---

## 1. Feasibility by variant

| Variant | Verdict | Blocker |
|---|---|---|
| **(a) Payoff diagram / what-if calculator** | ✅ **Feasible** | Expiry payoff needs only strike + mid price from `/options-chain` — pure intrinsic math, zero new data. **Intermediate-date** curves need Black-Scholes built from scratch; the chain already carries per-contract `iv`, so the *inputs* exist and only the pricing function is missing. |
| **(b) Multi-leg strategy builder** | ✅ **Most feasible** | Net debit/credit, breakevens, max profit/loss are arithmetic over legs. `compute_options_game_plan()` (`routes.py:4385`) already does 2-leg selection and computes `put_effective_floor_price` / `call_effective_cap_price` — the same shape, generalized. Nothing blocks expiry-payoff. |
| **(c) Historical backtest of an options strategy** | ❌ **HARD-BLOCKED — do not attempt** | No historical chains, no historical IV, none. Would need months of new chain persistence — a real rate-limit risk (`docs/incidents/yfinance-rate-limit-amplification.md`; the game-plan snapshot job was *specifically* designed to avoid per-row chain fetches) — or a paid historical options vendor. |
| **(d) Forward paper-trading of options positions** | ⚠️ **Feasible, largest build, defer** | Needs a new `PaperOptionsTrade` model + migration, a daily marking job (re-fetch precedent exists in `compute_options_game_plan_snapshots_eod()`), and contract-multiplier / assignment / expiry-handling logic with **no precedent here**. Also: `PaperPortfolio` is **cash-only by design** — no margin concept exists anywhere on the platform (CLAUDE.md), so naked-short legs have **no accounting model**. |

---

## 2. Recommendation — build (a)+(b) as one feature

A **live-chain-driven multi-leg payoff calculator**:

- Pick legs from the real live chain (real strikes, real bid/ask/mid, real per-contract IV)
- **Expiry P&L: exact** — pure intrinsic arithmetic, no model, no assumptions
- **Intermediate-date P&L: modeled** — a hand-rolled `math.erf`-based Black-Scholes, no new
  dependency
- Show net debit/credit, breakeven(s), max profit, max loss, and payoff curve
- Preset structures reusing the existing primitives: covered call, protective put, vertical
  spreads, straddle/strangle, PMCC (which pairs naturally with the QQQ LEAPS playbook already
  shipped)

**Honesty requirement, consistent with everything else shipped this session:** label the
intermediate-date curve as a **model output**, and the expiry curve as exact. This repo has a
strong established convention of disclosing exactly this distinction (the gamma-unwind proxy
docstring being the clearest precedent) — a simulator that silently presents modeled mid-life
P&L as fact would break it.

**Effort:** M. The BS function plus leg math is contained; the payoff chart is a `<polyline>`.

---

## 3. Frontend conventions (mandatory, not optional)

- **Hand-rolled SVG, not lightweight-charts.** A payoff curve is a continuous
  *price-vs-P&L* axis — even less suited to a time-series library than strike bars. See
  `OptionsTabCharts.tsx:11-22`, `OptionsChainChart.tsx:1-18`, tracker T270.
- **Pure logic into `lib/`.** `frontend/src/lib/optionsTabCharts.ts:1-6` states the reason
  plainly: this repo has **no DOM/component test harness** for page-level React, so all data
  shaping must live in `lib/` to be testable at all (precedents: `optionsChainChart.ts`,
  `volumeProfile.ts`, `swingPivots.ts`, `riskReward.ts`). Put leg-combination + curve generation
  in `lib/optionsPayoff.ts`; keep the SVG dumb.
- **Falsy-zero discipline.** Use `!= null`, never truthiness — payoff math is exactly where a
  genuine `0` premium or `0` strike must stay distinguishable from absent. This codebase has
  fixed that bug class repeatedly.

---

## 4. Where it should live

The Options tab shipped this session (`frontend/src/pages/stock/[symbol].tsx`, `?tab=options`) is
the natural home — it already has the live chain fetched and the Game Plan legs on screen, so a
simulator seeded from either is a short step rather than a new surface. Deep-linking already
works, so `?tab=options` can carry simulator state later if wanted.
