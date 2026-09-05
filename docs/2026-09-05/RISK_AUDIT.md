# PHASE D — RISK, SIZING & PORTFOLIO (RISK AUDIT)

**Date:** 2026-09-05
**Prompt:** `docs/recomm_or_audit/AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED 2026-09-04).md`, Phase D
**Scope:** D.1 sizing, D.2 portfolio-level, D.3 gating, D.4 profitability gates.

---

## Headline

**Risk limits are well-chosen and broadly enforced. One real enforcement gap found. And the
system fails the live-automation gate outright — not marginally.**

---

## D.1 Position sizing — limits are justified, with one gap

### The configured values (identical across all 5 portfolios)

| Limit | Value | Assessment |
|---|---|---|
| `max_position_pct` | 10% | **Justified.** Conventional; 10 positions = fully invested. |
| `max_open_risk_pct` | 12% | **Justified.** Total risk across open positions. |
| `max_daily_loss_pct` | 4% | **Justified.** |
| `max_portfolio_drawdown_pct` | 20% | **Justified**, arguably generous — see below. |
| `risk_per_trade_pct` | 1% | **Justified.** The textbook value. |

Per the prompt's instruction, these were audited on whether the **values** make sense, not
whether the mechanism exists. They do: 1% risk per trade with a 10% position cap is standard,
internally consistent, and conservative.

### Enforcement, measured against 124 real trades

| Portfolio | Max position observed | Avg risk/trade | Max risk/trade |
|---|---|---|---|
| ETrade Sandbox SWING | 10.0% | 0.49% | 0.62% |
| **GROWTH Paper** | **13.0%** | 0.92% | **1.59%** |
| **HK GROWTH** | **12.7%** | 0.80% | 1.10% |
| HK SWING | 10.0% | 0.54% | 0.56% |
| US SWING | 10.4% | 0.41% | 0.59% |

Average risk per trade is **well under** the 1% budget everywhere (0.41–0.92%) — sizing is
genuinely conservative in practice.

### FINDING: scale-in bypasses `max_position_pct`

Two portfolios exceeded the 10% cap, peaking at **13.0%** (JPM, GROWTH). Traced to source:

`_size_position()` enforces the cap correctly (`paper_trading_engine.py:4357-4359`,
`max_pos = equity * cfg["max_position_pct"] * earnings_size_mult`). But the **scale-in path does
not re-check it** — it gates only on cash:

```
if portfolio.current_cash >= _si_add_value * 1.1:
```

So a position already at the cap can scale beyond it. JPM's own decision notes show the stack
that produced 13.0%:

> `Size 1.25× (confidence 63% — high conviction)`, `Size 1.15× (multi-timeframe consensus)`,
> `SCALE_IN`, `Scale-in: +3.7469sh @ $360.80 (+5.2%, conf 87%)`

**Severity: MEDIUM.** Every component is individually intentional, the overshoot is bounded
(~30% over cap, 6 of 124 trades), and scale-ins only trigger on *already-profitable* positions —
so this adds size to winners, not losers. But a hard cap that a code path can walk past is not a
hard cap. **Recommend:** re-check `max_position_pct` inside the scale-in branch and truncate the
add rather than skip it.

### Recommended limits (current values are already appropriate)

No changes recommended to `max_position_pct`, `max_open_risk_pct`, `max_daily_loss_pct`, or
`risk_per_trade_pct`. Two notes:

- **`max_portfolio_drawdown_pct: 20%` is generous for an unproven system.** With expectancy
  currently negative, a 20% drawdown allowance permits substantial capital destruction before
  the halt engages. Consider 10–12% until expectancy turns positive.
- **Kelly is not implemented and should not be** (see §F.4). Current fixed-fractional sizing is
  the right choice for a system without stable, positive expectancy — Kelly on a negative edge
  sizes *up* into losses.

---

## D.2 Portfolio-level analysis

**Scope constraint applied per the prompt: 2.5 months, single regime. Every figure below is
indicative only, and no annualised CAGR/Sharpe is reported, because annualising 2.5 months of
one regime would be misleading regardless of caveat.**

### Return vs benchmarks (2026-06-20 → 2026-09-05, the actual trading window)

| | Return |
|---|---|
| **Paper portfolios (aggregate)** | **−1.07%** |
| SPY | **+2.69%** |
| QQQ | −3.26% |

Underperforms SPY by ~3.8 points; beats QQQ by ~2.2. Consistent with the −0.76% alpha measured
independently in `EDGE_SUBSET_ANALYSIS.md`.

### Cash utilisation — the notable finding

| Portfolio | Cash % | Open positions |
|---|---|---|
| ETrade Sandbox SWING | 97.0% | 0 |
| GROWTH Paper | 56.9% | 5 |
| **HK GROWTH** | **100.8%** | **0** |
| **HK SWING** | **97.8%** | **0** |
| US SWING | 74.3% | 3 |

**Three of five portfolios are ~100% cash with zero open positions.** Capital is almost entirely
idle. Given current negative expectancy this is *protective* rather than harmful — but it means
the HK portfolios in particular are effectively dormant, which matches the P2 HK-SWING dormancy
already flagged in a prior audit. Worth confirming this is gating, not a defect, once expectancy
improves.

Concentration, correlation, sector and factor exposure are **not computed** with only 8 open
positions across 5 portfolios — a meaningful concentration analysis needs more simultaneous
holdings. Deferring rather than reporting a number built on n=8.

---

## D.3 Trade gating — the chain exists and enforces

The gate chain is real and layered: restricted-symbol check, stop-cooldown, cross-portfolio
symbol cap, per-portfolio open check, conviction gate (5 layers + hard disqualifiers), DE
verdict gate, equity/cash checks, min-position floor, `max_position_pct` cap.

Evidence it enforces: the DE gate blocked **20 of 20** candidates on a recent day (correctly —
market closed), and `_skip_tally` categorises every rejection reason. This is a genuinely
well-built gate chain.

**One gap, already fixed today:** the cross-portfolio symbol cap (`AUD-GLOBALSYMCAP-STALE`) went
stale mid-scan, allowing 3 concurrent positions in 2382.HK. Fixed and deployed.

---

## D.4 Profitability gates — FAILS, and not marginally

Measured against the prompt's required conditions for live autonomy:

| Condition | Required | Actual | Pass? |
|---|---|---|---|
| Statistically meaningful sample | Yes | 116 closed trades | ⚠️ Marginal |
| **Positive expectancy** | > 0 | **−0.387%** | ❌ **FAIL** |
| **Positive profit factor** | > 1.0 | **0.653** | ❌ **FAIL** |
| Robust out-of-sample | Yes | Not established | ❌ FAIL |
| Walk-forward stability | Yes | Not implemented (§F.3) | ❌ FAIL |
| Acceptable max drawdown | Yes | Within 20% limit | ✅ Pass |
| Positive Sharpe/Sortino | > 0 | Negative (returns negative) | ❌ FAIL |
| Realistic transaction costs | Yes | Commission-free assumed (real) | ✅ Pass |
| Realistic slippage | Yes | **Flat 10bps assumption** | ❌ FAIL (C.0a) |
| Paper-trading confirmation | Yes | Confirms *negative* expectancy | ❌ FAIL |
| No major data leakage | Yes | Live-bar defect found & fixed | ✅ Pass (now) |
| **No single-regime dependence** | Yes | **97.3% bull** | ❌ **HARD BLOCKER** |

### The regime blocker, stated plainly

| Regime | n | % of sample |
|---|---|---|
| **bull** | 16,277 | **97.3%** |
| choppy | 310 | 1.9% |
| unknown | 144 | 0.9% |
| **bear** | **1** | **0.0%** |

**One single bear-regime observation across 16,732 outcomes.** The system has never been
observed operating in a declining market. Nothing about its behaviour in a bear regime is known
— not its win rate, not its drawdown, not whether its stops hold.

**This is a hard blocker to live automation, not a caveat.** Profit factor 0.653 means the system
currently loses $1 for every $0.65 it makes; combined with zero bear-regime evidence, there is no
defensible basis for autonomous capital deployment.

---

## Conclusions

1. **Risk limits are correctly chosen** — 1% risk/trade, 10% position cap, 4% daily loss. Audited
   as values, not mechanisms, per the prompt. No changes recommended except tightening
   `max_portfolio_drawdown_pct` from 20% to 10–12% while expectancy is negative.
2. **Sizing is conservative in practice** (0.41–0.92% average risk/trade vs a 1% budget).
3. **One real enforcement gap:** scale-in bypasses `max_position_pct`. MEDIUM severity —
   bounded, and it only adds to winners, but a hard cap should be hard.
4. **The gate chain is well-built** and demonstrably enforces.
5. **Live automation is blocked** on three independent grounds: negative expectancy (−0.387%),
   profit factor below 1.0 (0.653), and 97.3% single-regime dependence with exactly one bear
   observation.

## What this phase does NOT establish

- Concentration/correlation/factor exposure — n=8 open positions is too few. Deferred, not
  estimated.
- Any annualised figure — deliberately omitted per the prompt's scope constraint.
- Slippage realism — blocked on C.0a instrumentation.
