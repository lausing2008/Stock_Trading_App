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

> ## ⚠️ CORRECTION (2026-09-07) — historical options data DOES exist, via Unusual Whales
>
> **The "no historical options data" finding below is about this platform's own DATABASE, and
> that part still stands. But my conclusion that a historical options backtest is hard-blocked
> was WRONG** — the user pointed out UW has options history, and verified against the live key
> it does:
>
> | Endpoint | What it returns |
> |---|---|
> | `/api/stock/{ticker}/option-chains?date=&greeks=true` | **the full chain as it existed on a past date** — strike, expiry, NBBO bid/ask, IV, OI, volume, and delta/gamma/theta/vega/rho |
> | `/api/option-contract/{id}/historic` | per-contract daily OHLC + **IV high/low** + volume split by ask/bid/mid/neutral |
> | `/api/option-contract/{id}/intraday`, `/volume-profile` | finer-grained per-contract history |
>
> **Verified live (AAPL):** 2026-06-02 → **3,598 contracts** with full greeks; 2026-08-14 →
> 3,590; 2026-05-01 → 3,240. 2025-09-15, 2026-04-15, 2026-04-01, 2026-03-24, 2026-03-16 all
> return **HTTP 403**.
>
> **The lookback boundary is between 2026-04-15 (403) and 2026-05-01 (OK)** — a **~4-month
> rolling window** on the current subscription tier, not an archive. Two consequences:
> 1. **Variant (c) is no longer hard-blocked** — it's feasible within ~4 months of history.
> 2. **The window ROLLS.** Data older than the boundary is gone permanently unless persisted.
>    That turns "persist historical chains" from optional into time-sensitive: every day not
>    captured is a day that eventually falls off the back.
>
> Cost note: a full chain is ~3,600 rows/symbol/day. Backfilling even 10 symbols × 90 trading
> days is ~900 calls (fine against the 30k/day budget) but ~3.2M rows — so this needs a
> deliberate scope (which symbols, which strikes, daily vs weekly) rather than a blind sweep.
> See §1 and the revised verdict table for how this changes the recommendation.

> ## ✅ SUPERSEDED (2026-09-07, later same day) — tier upgraded, and the capture is now BUILT
>
> Both numbers in the correction above are out of date; keep the block for its endpoint table,
> but read these figures instead:
>
> | Was (correction above) | Now |
> |---|---|
> | ~4-month rolling window | **~2 years** — verified live back to **2024-09-09** |
> | 30k requests/day | **120,000/day** (API BASIC) |
> | "needs a deliberate scope" | **shipped** — see below |
>
> **OPTHIST-1 is deployed** (commit `b4be064`): `OptionChainHistory` + migration,
> `get_historical_option_chain()`, `capture_option_chain_history()`, and
> `POST /admin/capture-option-chain-history`. Validated end-to-end against production —
> AAPL 2026-06-01..06-05 → **18,190 rows, 0 errors, 11.5s**, all 5 days genuinely distinct
> (volume 634k–1.87M, OI rising 4.98M→5.27M, IV varying), greek fill 43–48% as measured.
>
> **The binding constraint is DB volume, not the request budget** — and now with real measured
> numbers rather than estimates:
>
> | Unit | Requests | Rows | Disk |
> |---|---|---|---|
> | 1 symbol-day | 1 | ~3,640 | **~285 KB** |
> | 10 symbols × 90 trading days | 900 (0.75% of daily budget) | ~3.3M | **~935 MB** |
> | 10 symbols × 2 years (~500 days) | 5,000 (4%) | ~18M | **~5.2 GB** |
>
> So the 120k/day budget is nowhere near binding: even the 2-year sweep is 4% of ONE day's
> quota. Disk is what to scope against — check EC2 headroom before any multi-GB backfill, and
> prefer narrow-symbol/full-history over broad-symbol/shallow, since the rolling window only
> threatens the far end of history.
>
> **Greeks are stored sparse, deliberately.** They're present on exactly the rows with
> `volume > 0` — a 1:1 correlation with zero exceptions in the measured sample (UW computes
> greeks only for contracts that traded). Filtering to `volume > 0` would halve the rows and
> lose no greeks, but would drop 1,064 OI-bearing contracts holding **8.9% of total open
> interest** — and GEX/max-pain reconstruction needs the complete OI distribution, traded or
> not. Every row is kept; the greek columns are simply NULL where UW gives nothing.

### This platform's own DB has NO historical options data

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
| **(c) Historical backtest of an options strategy** | ✅ **Feasible within ~4 months** *(revised 2026-09-07 — was "hard-blocked")* | UW's `/option-chains?date=&greeks=true` returns real historical chains with greeks (verified: 3,598 AAPL contracts on 2026-06-02). **Constraint is the ~4-month ROLLING lookback**, not availability — so the honest framing is "a 3-4 month options backtest", not a multi-year one, and capturing data is time-sensitive because the window moves. Note the same small-sample caution that applies everywhere on this platform: ~4 months spans one market phase, so a strategy that looks good over it has not been tested across regimes. |
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
