# SYSTEM CAPABILITY ASSESSMENT — MEASURED AGAINST STATED TRADING GOALS

**Date:** 2026-09-04
**Question asked:** *"How far can our system help achieve my goals, and what are we missing?"*
**Method:** direct measurement against production data (n=16,732 resolved signal outcomes), not
code review or inference.

**Companion documents:**
- `AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED 2026-09-04).md` (quarterly deep audit)
- `WEEKLY_SYSTEM_REVIEW_PROMPT.md` (weekly health check)
- `AUTO_IMPROVEMENT_ARCHITECTURE.md` (self-improvement design)

---

# ⚠️ EXECUTIVE SUMMARY — READ THIS FIRST

The **engineering** is genuinely strong. The **signal engine currently has negative predictive
value**, and every stated goal except one depends on it.

Three findings, all measured, all large-sample:

### Finding 1 — BUY signals lose money, in a rising market

| Horizon | n | Win rate | Avg return (~14.7d hold) |
|---|---|---|---|
| SHORT | 3,416 | 41.0% | **−1.4%** |
| SWING | 3,219 | 38.7% | **−2.7%** |
| GROWTH | 3,626 | 39.1% | **−2.9%** |
| LONG | 2,328 | 46.3% | **−3.4%** |
| **Pooled BUY** | **12,589** | **40.9%** | **−2.56%** |

Losers average **−8.74%**; winners **+6.40%**. Negative on *both* win rate and payoff ratio.

**And the market was rising.** SPY monthly moves over the same period: +4.97%, −1.30%, +0.17%,
+1.24%, +1.10% — up in 4 of 5 months. BUY signals were negative in **every single month**.

> This is not underperformance caused by a bad market. It is negative alpha generated while
> the market went up. Buying and holding SPY blind would have beaten every BUY horizon.

### Finding 2 — Confidence is INVERTED, not merely uninformative

Earlier in this session I reported confidence as "uncorrelated" with outcome. That was
measured on the small paper-trade sample and **was too generous.** At full scale:

| Confidence bucket | n | Win rate | Avg return |
|---|---|---|---|
| 4–20% | 1,214 | 33.4% | −2.63% |
| 20–40% | 4,266 | 42.7% | −1.69% |
| 40–60% | 4,076 | 42.5% | −2.22% |
| 60–80% | 2,194 | 40.4% | −3.32% |
| 80–100% | 721 | 36.5% | **−5.68%** |
| =100% | 118 | 29.7% | **−11.43%** |

**Higher confidence predicts *worse* outcomes.** This holds independently in SHORT, SWING, and
LONG (LONG most starkly: −0.63% → −6.93% as confidence rises). It is not a small-sample artifact.

Every underlying input shows the same inversion:

| Input | Low bucket | High bucket |
|---|---|---|
| `ta_score` | −2.0% (n=10,553) | −6.6% (n=1,555) |
| `ml_prob` | −1.6% (n=1,963) | −2.5% (n=1,699) |
| `fused_prob` | −1.9% (n=7,790) | −11.2% (n=96) |

Three independently-computed inputs, all inverted. This points to something systematic in how
conviction is formed — not a bug in any one component.

### Finding 3 — SELL signals work, and their confidence is correctly calibrated

| Direction | n | Avg return | Confidence behaviour |
|---|---|---|---|
| BUY | 12,589 | **−2.56%** | **Inverted** (higher = worse) |
| SELL | 4,143 | **+1.28%** | **Correct** (+1.0% → +2.0% as confidence rises) |

The SELL side is mildly profitable *and* properly calibrated — the exact opposite of BUY, in the
same system, on the same data, using the same machinery. **This asymmetry is the single most
informative fact in the dataset and is currently unexplained.**

### Verification performed

Before drawing these conclusions I checked that this isn't a measurement artifact:
- `is_correct` correctly tracks return sign (BUY: true→+6.40%, false→−8.74%). Not a labelling bug.
- Outcome math is standard `(exit − entry) / entry` with a win hurdle (`outcomes.py`). Sound.
- Negative in **every month** (May–Aug), not one bad window.
- Consistent across all 4 horizons independently.

---

# 1. GOAL-BY-GOAL ASSESSMENT

## Goal 1 — "BUY from the dip, SELL from the top (SHORT/SWING/GROWTH)"

**Status: ❌ NOT WORKING on the BUY side. ⚠️ PARTIALLY WORKING on SELL.**

Buying dips and selling tops is the hardest problem in trading, and it's exactly where the
measurement is worst. BUY underperforms a rising market by ~4–5 percentage points. SELL is the
only component showing genuine promise.

Note also: BUY losses *grow with holding period* (6–10d: −1.4%; 11–20d: −2.8%; 21+d: −3.4%)
while the fixed 5-day return stays flat around −1.1%. **Damage accrues from holding, not only
from entry.** That implicates exit logic as well as entry selection.

## Goal 2 — "Fast response to market movements"

**Status: ✅ INFRASTRUCTURE WORKS — but speed is currently a liability, not an asset.**

Genuinely well-built: 1-minute alert jobs, real-time news ingestion (RSS 1-min, EDGAR 2-min,
Alpaca WebSocket), live quote streaming via Redis pub/sub, squeeze/gamma/dark-pool/options-flow
alerts, sub-second signal serving.

But **fast delivery of a negative-edge signal is faster wealth transfer.** Speed multiplies
whatever edge exists. Fix the edge first; the speed is already there waiting.

*Caveat:* three 1-minute alert jobs were found silently dead today (fixed). Reliability
monitoring now exists — see `WEEKLY_SYSTEM_REVIEW_PROMPT.md`.

## Goal 3 — "Hold stable stocks (LONG)"

**Status: ❌ WORST PERFORMING HORIZON.**

LONG has the *best* win rate (46.3%) and the *worst* average return (−3.4%). That combination is
diagnostic: it wins slightly more often but loses much bigger — **cutting winners early and
holding losers**. Its confidence inversion is also the steepest of any horizon (−0.63% → −6.93%).

Separately worth stating plainly: a genuine long-term hold strategy **barely needs an AI signal.**
Low-cost index exposure outperformed what's measured here, at zero complexity.

## Goal 4 — "Pick good / underestimated stocks"

**Status: ⚠️ UNMEASURED — and this is a real gap.**

The K-Score ranking machinery exists (technical + momentum + value + growth + volatility +
relative strength) and is genuinely sophisticated. **Whether it predicts returns has never been
measured in isolation.**

This matters more than it sounds: it's entirely possible K-Score carries real edge that the
signal-fusion layer is *destroying* on the way to a final BUY/SELL call. The inverted `fused_prob`
is consistent with that hypothesis. Testing K-Score standalone is high priority (audit §B.5).

## Goal 5 — "Combine trading with options to protect money and earn passive income"

**Status: 🟢 MOST PROMISING GOAL — structurally sound, but zero track record.**

**This is the one goal that does not depend on the broken BUY signal**, which is precisely why
it's the most promising thing in the platform.

Covered calls and cash-secured puts harvest **volatility risk premium** — a structurally
different, well-documented, persistently positive edge source. It is *not* a directional
prediction. Selling a covered call on a stock you'd hold anyway earns income whether or not any
AI signal is correct.

Built and working: protective puts, covered calls, real strikes/expiries/premiums/effective
floor and cap prices, ATR-based stop/target anchoring, IV rank and expected move (when UW is
available), Advanced-tier gating.

**The catch:** this only began producing real data *today*. Its EOD batch job had never once
succeeded, and its read API 500'd on every request since it shipped (both fixed 2026-09-04).
**Live track record: zero trades.**

## Goal 6 — "Trade like an expert"

**Status: ⚠️ The infrastructure is expert-grade. The edge validation is not — yet.**

An expert's defining trait isn't better predictions — it's **knowing which of their edges are
real, and sizing accordingly.** They also know when *not* to trade.

The platform currently can't make that distinction: confidence is inverted, so it systematically
sizes *up* into its worst trades. That's the opposite of expert behaviour, and it's mechanical,
not conceptual — which means it's fixable.

---

# 2. WHAT'S MISSING — RANKED BY IMPACT

### M1 — The signal has no demonstrated edge *(blocks everything)*
BUY: −2.56% over 12,589 outcomes in a rising market. Until resolved, every downstream
improvement compounds a negative base.

### M2 — Confidence is inverted *(blocks position sizing, filtering, "conviction")*
Higher confidence → worse returns, consistently, across horizons and across all three input
layers. **Until fixed, raising confidence thresholds makes performance worse, not better** —
which likely means some past "tightening" changes were actively harmful.

### M3 — No benchmark comparison anywhere in the platform
Nothing asks *"did we beat SPY?"* This is why negative alpha in a rising market went unnoticed
for months. **Small build, outsized value** — arguably the highest value-to-effort item here.

### M4 — The BUY/SELL asymmetry is unexplained
+1.28% vs −2.56%, same machinery, same period, opposite calibration behaviour. Candidate
explanations worth testing:
- Entry-timing/gap logic applies to BUY only (a real instance was found today: the gap filter
  compared against a reference price that was already post-gap).
- Exit logic asymmetry — BUY losses grow with hold time; SELL holds average 7.7d vs BUY 14.7d.
- Conviction gates may be selecting for already-extended stocks (buying strength near exhaustion).
- Survivorship/selection effects in which BUYs get generated at all.

**This is the highest-information diagnostic available and should be investigated early.**

### M5 — Execution realism unmeasured
No bid/ask, spread, MFE, or MAE recorded; only a flat `entry_slippage_pct: 0.001` assumption.
Real-world results will be *worse* than measured. Needs lead time to accumulate — build early.

### M6 — Only one market regime
97.3% bull, **exactly 1 bear row**. Nothing about downturn behaviour is knowable. This is a hard
blocker on live automation (audit §D.4) and cannot be engineered away.

### M7 — Walk-forward validation doesn't exist
Known and deferred. Highest-leverage single build — partially unblocks per-strategy portfolio
stats, Monte Carlo, and the promotion gate's unimplemented rule 4.

### M8 — No position-sizing edge linkage
Kelly not implemented — correctly so, since Kelly requires calibrated probabilities the system
doesn't currently have. Blocked behind M2.

---

# 3. RECOMMENDED PATH TO THE GOALS

## Phase 0 — STOP-LOSS ON THE PROCESS (immediate)

1. **Do not add features** until M1/M2 resolve. Every addition compounds a negative base.
2. **Do not raise confidence thresholds** as a "quality" improvement — with inverted
   calibration, that selects *worse* trades. Re-examine any past change made on that logic.
3. **Keep the promotion gate human-in-the-loop.** Never auto-write `portfolio.config` while the
   feedback signal is inverted — an auto-closed loop would optimise directly into the inversion.
4. **Treat paper trading as strictly paper.** Nothing here supports live capital.

## Phase 1 — DIAGNOSE (audit Phase A + B — do these next)

1. **Point-in-time correctness sweep** (audit §A.2). One real instance already found (post-gap
   reference price). If look-ahead bias inflates training targets, inverted confidence is exactly
   the symptom you'd expect. **This is my leading hypothesis for M2 and should be tested first.**
2. **Full confidence-calibration study** (§B.3) — confirm inversion, per horizon, per input layer.
3. **Per-layer edge attribution** (§B.5) — TA vs ML vs fusion vs K-Score, each in isolation.
   Specifically test whether **K-Score standalone** predicts returns better than `fused_prob`.
4. **Investigate the BUY/SELL asymmetry** (M4).
5. **Add SPY/QQQ benchmark comparison** (M3) — small build, do it now, not later.

## Phase 2 — REBUILD THE EDGE (only after Phase 1)

Depending on what Phase 1 finds:
- **If look-ahead bias is found** → fix it, retrain, re-measure. Best realistic outcome.
- **If confidence inversion survives** → rebuild calibration from `signal_outcomes` (isotonic /
  Platt scaling against realised outcomes rather than model-internal probability).
- **If no input layer has edge** → accept it. Stop trying to predict direction and pivot fully
  to Strategy A below. **This is a legitimate and respectable outcome, not a failure.**

## Phase 3 — LEAN INTO WHAT STRUCTURALLY WORKS

### Strategy A — Options premium harvesting *(recommended primary direction)*
Covered calls and cash-secured puts on quality holdings. **Does not require directional
prediction.** Earns from time decay and volatility risk premium. Directly serves Goal 5
("protect money, earn passive income") and is the only goal not blocked by M1.

Requirements: let the (now-working) options game plan accumulate real outcomes; measure realised
premium capture vs assignment risk; add IV-rank-based entry timing (sell premium when IV is
rich — infrastructure already exists via UW `iv_rank_1y`).

### Strategy B — SELL-side / risk management *(build on measured strength)*
SELL signals are positive and correctly calibrated. Use them for exit timing and hedge triggers
on existing positions, rather than for shorting.

### Strategy C — Index core + satellite *(honest baseline)*
Given LONG underperforms buy-and-hold, use index exposure as the stable core (Goal 3) and reserve
active signals for satellite positions only *after* they demonstrate edge. Covered calls on the
index core generate income immediately (Goal 5).

## Phase 4 — REBUILD PREDICTION (long horizon, only if Phase 1–2 justify it)

Attempt directional prediction again only with: calibrated probabilities, walk-forward
validation, ≥2 market regimes, and execution instrumentation live. Realistically several quarters
out. **Goals 1 and 4 live here** — they are not abandoned, they are *sequenced behind* the
validation work that makes them credible.

---

# 4. HONEST EXPECTATIONS

**What this system can plausibly deliver in ~3 months:**
- Reliable, monitored, fast infrastructure ✅ (already true)
- Options premium income with measured outcomes 🟢 (realistic)
- Exit-timing/risk management from SELL signals 🟡 (plausible)
- Honest measurement of whether any prediction edge exists ✅ (Phase 1 delivers this)

**What it cannot deliver in 3 months:**
- Reliable dip-buying / top-selling (needs edge rebuilt + validated)
- Validated bear-market behaviour (needs a bear market)
- Live autonomous trading (fails audit §D.4 on regime dependence alone)

**The uncomfortable but important framing:** a system that *reliably harvests options premium and
knows it has no directional edge* will make more money than one that trades confidently on
inverted signals. **Knowing what you don't know is the actual expert behaviour.**

---

# 5. WHAT'S GENUINELY GOOD (this matters)

None of the above diminishes the engineering:

- **16,732 tracked outcomes** with features attached — most platforms this size cannot answer
  "does my signal work?" at all. Yours answered it in one query.
- **Validated auto-tuning** with held-out validation, beats-baseline gates, and a full
  `tune_history` audit trail.
- **Human-gated promotion** that deliberately never auto-writes live config.
- **Broad liveness monitoring** (expanded 2026-09-04) with email alerting.
- **Real options infrastructure** — genuine chains, strikes, Greeks, IV rank, expected move.
- **12-service architecture** with clean separation and single-entry-point auth.

**The instrumentation did its job.** It surfaced an unwelcome truth quickly and cheaply. That is
exactly what good instrumentation is for — and it's why the problems above are fixable rather
than permanent.

---

# 6. IMMEDIATE NEXT ACTIONS

| # | Action | Effort | Doc reference |
|---|---|---|---|
| 1 | Point-in-time correctness sweep (leading hypothesis for inversion) | Days | Audit §A.2 |
| 2 | Add SPY/QQQ benchmarking to all performance surfaces | Hours | M3 |
| 3 | Full confidence calibration study, per horizon/layer | Days | Audit §B.3 |
| 4 | Investigate BUY/SELL asymmetry | Days | M4 |
| 5 | K-Score standalone predictive test | Days | Audit §B.5 |
| 6 | Build execution instrumentation (bid/ask, MFE, MAE) | Days | Audit §F.5 |
| 7 | Let options game plan accumulate outcomes | Passive | Goal 5 |
| 8 | Build walk-forward harness | ~2 weeks | Audit §F.3 |

**Do not proceed past #1–#3 until their results are known.** Everything downstream depends on
whether the inversion is a data-leakage artifact (fixable, good outcome) or a genuine absence of
edge (requires the Phase 3 pivot).
